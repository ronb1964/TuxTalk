"""Right Ctrl and Right Alt as dictation keys, and Left Ctrl held with Space.

All are modifiers: a key TuxTalk presses while one is down arrives as a
shortcut (Ctrl+W closes the tab), and a lone tap of Alt opens an app's menu
bar. Where the keyboard options make one the Compose key (Pop!_OS's Right
Ctrl), its press begins a compose that would swallow the next keys typed.
The hotkey loop notes its keyboards and dictation keys here, and typing
asks before each key whether it must wait or end a compose first.
"""
import itertools
import threading
import time
from dataclasses import dataclass, field

from evdev import ecodes

from . import ctrl_kept, focused_window, keyboard_layout, virtual_keyboard
from .config import HOTKEY_CHOICES
from .hotkey_keys import MODIFIER_CODES, SHIFT_CODES, keys_down
from .keyboard_devices import is_keyboard_device
from .logger import setup_logger

logger = setup_logger(__name__)

# The keyboards the hotkey loop reads and its dictation keys that are
# modifiers; set by note().
_noted = ([], frozenset())
# The keys a grab hides from the desktop now, and the keyboards it grabbed
# for that; set by note_hidden().
_hidden = (frozenset(), ())

# How long typing waits for the key to come up while the desktop can see it.
# Pressed again while the last dictation is still being typed, it starts the
# next recording, whose grab hides it within moments, and typing carries on
# under it (note_hidden). Longer than nearly any dictation all the same: a
# key that reads as down and unhidden for longer is stuck (a KVM switch, a
# remapper's keyboard that never sent the release). Typing then stops, and
# the dictation stays in Recent Dictations: typing on would turn the rest
# into Ctrl shortcuts wherever the desktop sees the key down too.
HELD_LIMIT_S = 30.0
# Keys found stuck: the next keys give up at once while they stay down,
# rather than each fallback (paste, then typing) waiting all over again.
_stuck = set()
# Right Ctrl or Right Alt when the keyboard options make it Compose, as
# (code, with Shift) pairs (keyboard_layout.compose_keys), read when the
# hotkey loop starts watching and after each dictation (read_compose_keys),
# not on each press: the desktop can take a moment to answer.
_compose_keys = frozenset()
# The press of a Compose dictation key that began a compose in the focused
# app TuxTalk hasn't ended yet (note_compose_key), or None; set on the
# hotkey loop, ended there or by typing. Cleared only once the press that
# ends it has gone, under _compose_lock, so typing that finds it set waits
# for that. Lock order: _compose_lock, then the hotkey helper's
# (HelperKeyboard, waiting for its answer) or virtual_keyboard's.
_compose = None
_compose_lock = threading.Lock()
_compose_numbers = itertools.count(1)
# How long ending a compose waits for the hotkey helper's answer to hiding
# its press, when that hasn't come in yet: it takes milliseconds.
_ANSWER_WAIT_S = 0.25
# How long ending a compose waits for the look at the focused window taken
# at the press, and for each question to the tray in the one taken then
# (_moved): it can run on the hotkey loop with every keyboard grabbed. The
# tray answers from a cache in a millisecond; focused_window.TRAY_TIMEOUT_S
# is for questions that can wait.
_LOOK_WAIT_S = 0.15
# How long the desktop gets to see a key it wasn't hidden from go up before
# Compose is pressed again: a compositor drops a press of a key it holds
# down, and reads a key event within a frame (16 ms at 60 Hz).
_SETTLE_S = 0.05
# Alt, Ctrl and Super: a key TuxTalk presses while one is down is a shortcut
# (Alt+Space opens KRunner). Not Shift: Shift+Space is still a space.
SHORTCUT_MODIFIERS = frozenset(MODIFIER_CODES - SHIFT_CODES)
# Key codes to the names Preferences shows ("Right Ctrl"), for the log, and
# the Left Ctrl that Left Ctrl+Space is held with.
_NAMES = {**{ecodes.ecodes[key]: name for name, key in HOTKEY_CHOICES.items()},
          ecodes.KEY_LEFTCTRL: "Left Ctrl"}


def note(devices, *keys) -> None:
    """Remember the hotkey loop's *devices* and which of its dictation *keys*
    are modifiers, for wait_while_held: Right Ctrl or Right Alt, and the
    Left Ctrl a Left Ctrl+Space dictation key is held with, which the hotkey
    loop names among *keys* (ctrl_space.held_with)."""
    global _noted
    _noted = (devices, frozenset(k for k in keys if k in MODIFIER_CODES))


def note_hidden(codes, keyboards=()) -> None:
    """Remember the keys a grab of *keyboards* has just hidden from the
    desktop (keyboard_grab.grab_keyboards_hiding), or none once it lets go. The
    desktop has been told they're up, so typing under them is safe: Type at
    pause types each phrase while the hold key is still held.

    The hotkey helper hides them in its own process and answers later, or
    refuses (another program holds the keyboard): until its keyboard says
    the helper did (hotkey_helper_client.HelperKeyboard.hidden), typing
    still waits. A keyboard TuxTalk grabs itself has hidden them once
    grab() returns."""
    global _hidden
    _hidden = (frozenset(codes), tuple(keyboards))


@dataclass(eq=False)
class _ComposePress:
    """One press of a Compose dictation key. *number* counts presses from
    the start, so an answer about an earlier one confirms nothing here.

    *hidden*: hiding the press is known to have worked (note_compose_hidden).
    Only then does the desktop take the key to be up, so a second press
    isn't dropped, and the key didn't come through a key remapper, whose
    keyboard is never held (kanata takes TuxTalk's keyboard too unless told
    not to, and would hand Compose pressed again back as the dictation key:
    a recording that ends its own compose again, without end). A press left
    to the desktop (the hotkey test, a tap that only holds a send, the
    user's own shortcut, a tap already up when it was read) isn't.

    *shown*: the hotkey helper refused to hide it for a reason it gives
    only for a press on no remapper's keyboard (note_compose_shown): the
    desktop saw it as it was, so once it's up there, Compose pressed again
    ends the compose as for a hidden one.

    *where*: the focused window soon after the press (_look_later), None
    until *looked* is set, or when the look failed. *tries*: left for the
    press that ends the compose, when sending it fails."""
    number: int
    code: int
    shift: bool
    hidden: bool = False
    shown: bool = False
    where: tuple | None = None
    looked: threading.Event = field(default_factory=threading.Event)
    tries: int = 2


def compose_press():
    """The number of the press whose compose isn't ended yet, or None, for
    an answer about hiding it (note_compose_hidden) to name."""
    press = _compose
    return None if press is None else press.number


def note_compose_hidden(codes, number) -> None:
    """Hiding press *number* (compose_press) of *codes* worked: a grab
    TuxTalk holds caught them still down on a keyboard it grabbed (never a
    remapper's), or the hotkey helper said OK to hiding them. That press's
    compose is TuxTalk's to end (end_compose). Without the lock: set on the
    press itself, so a late answer can't reach another, and the helper's
    answer can come in on a thread holding virtual_keyboard's lock."""
    press = _compose
    if press is not None and press.number == number and press.code in codes:
        press.hidden = True


def note_compose_shown(codes, number) -> None:
    """The hotkey helper refused to hide press *number* (compose_press) of
    *codes*, which it does only after finding the press on no remapper's
    keyboard (a keyboard that's a mouse too held a modifier with it): that
    press's compose is TuxTalk's to end too, once the key is up. Without
    the lock, as note_compose_hidden."""
    press = _compose
    if press is not None and press.number == number and press.code in codes:
        press.shown = True


def _hidden_now():
    codes, keyboards = _hidden
    return codes if all(getattr(k, "hidden", True) for k in keyboards) else frozenset()


def hidden(code) -> bool:
    """Whether a grab hides *code* from the desktop now (note_hidden)."""
    return code in _hidden_now()


def hidden_by_own_grab(code) -> bool:
    """Whether a grab TuxTalk holds itself (the input group) hides *code*.
    The hotkey helper reports a Space under Left Ctrl only while the desktop
    can see that Ctrl; TuxTalk reading the keys itself sees every Space."""
    _codes, keyboards = _hidden
    return hidden(code) and not any(hasattr(k, "held_now") for k in keyboards)


def note_released(code) -> None:
    """The hotkey loop read *code* come up. A key typing had found stuck
    isn't any more: the next typing waits for it again rather than giving
    up at once, also when it's down again by then (a stop tap held)."""
    _stuck.discard(code)


def stuck() -> bool:
    """Whether typing gave up on a key that's still down (HELD_LIMIT_S)."""
    return bool(_stuck)


def shortcut_keys_down() -> bool:
    """Whether Alt, Ctrl or Super is down on the hotkey loop's keyboards,
    one a grab hides from the desktop aside. Complete only where TuxTalk
    reads the keyboards itself: through the hotkey helper (the default) a
    modifier is known only while held with a dictation key, so Alt pressed
    on its own isn't seen (CODE_HEALTH task 46)."""
    return bool(keys_down(_keyboards()) & (SHORTCUT_MODIFIERS - _hidden_now()))


def shift_shown() -> bool:
    """Whether a Shift the desktop can see is down on the hotkey loop's
    keyboards: one a grab hides aside. A voice edit's Shift stays seen when
    the hotkey helper refused to hide it (a keyboard that's a mouse too, or
    one it may only read, holds it), or held on a keyboard TuxTalk never
    grabs (keyboard_grab.grab_keyboards_hiding)."""
    return bool(keys_down(_keyboards()) & (SHIFT_CODES - _hidden_now()))


def shortcut_keys_maybe_down() -> bool:
    """As shortcut_keys_down, for a check made with TuxTalk's keyboard held,
    right before a key: nothing it asks may wait. The hotkey helper's last
    word is used (HelperKeyboard.held_now), never asked for again; while
    the helper isn't connected the keys can't be known, and they may be
    down."""
    down = set()
    for keyboard in _keyboards():
        if not hasattr(keyboard, "held_now"):
            down |= keys_down([keyboard])       # an ioctl: answers at once
        elif (held := keyboard.held_now()) is None:
            return True
        else:
            down |= held
    return bool(down & (SHORTCUT_MODIFIERS - _hidden_now()))


def _keyboards():
    devices, _ = _noted
    return [d for d in list(devices) if is_keyboard_device(d)]


def wait_while_held(destination=None,
                    gave_up="typing stopped; the dictation is in Recent Dictations"):
    """A check for virtual_keyboard.send to make before each key: None when
    no dictation modifier (note) the desktop can see is down and no compose
    one began is left to end, otherwise a function that waits until none is
    and ends it (end_compose), which send runs with the keyboard free for
    other threads. It gives up after HELD_LIMIT_S and raises
    virtual_keyboard.GaveUp("key_held"). wait_while_held itself returns
    None when no dictation modifier is noted and no compose is left to end
    (one whose ending failed, from before the keys changed).

    *destination*, if given, says where the keys go (focused_window.destination: the
    focused window's class, which window it is, and whether the cursor is in
    a password field). It's asked when a wait starts and when it ends, and
    GaveUp("moved") stops the typing if the answers differ in a part both
    know (focused_window.changed): the checks made before typing began are
    out of date by then, and the user may have clicked into a password
    field meanwhile. *gave_up* says in the log what giving up stopped."""
    devices, modifiers = _noted
    if not modifiers and _compose is None and not ctrl_kept.pressed():
        return None
    keyboards = [d for d in list(devices) if is_keyboard_device(d)]

    def held():
        # With the Left Ctrl TuxTalk holds for the desktop (ctrl_kept),
        # until it's let go: the real one may be up a moment before.
        kept = {ctrl_kept.CODE} if ctrl_kept.pressed() else set()
        return keys_down(keyboards) & (modifiers - _hidden_now()) | kept

    def wait():
        """Until no such key is down, guarding a Right Alt first; GaveUp
        when it stays down or the destination changed meanwhile. The guard
        goes before the destination is asked: that can take a moment, and
        a quick tap let go before the guard opens the menu bar."""
        still = held()
        if ecodes.KEY_RIGHTALT in still and not still & _stuck:
            _guard_if_alone(keyboards)
        where = destination() if destination else None
        give_up_at = time.monotonic() + HELD_LIMIT_S
        while still := held():
            if still & _stuck or time.monotonic() >= give_up_at:
                ctrl_kept.let_go()
                _give_up(still, gave_up)
            ctrl_kept.let_go(keyboards)     # if the real one is up already
            time.sleep(0.02)
        _stuck.clear()
        if destination and focused_window.changed(where, destination()):
            logger.info("Typing stopped: the window changed while it waited for the key")
            raise virtual_keyboard.GaveUp("moved")
        end_compose()

    def before_press():
        if held():
            return wait
        _stuck.clear()
        return end_compose if _compose is not None else None
    return before_press


def guard_alt_tap() -> None:
    """Press Ctrl while a Right Alt dictation key is down. To the desktop and
    apps a lone tap of Alt opens the menu bar (Firefox, LibreOffice), and what
    TuxTalk types next would go into the menu. Any key between Alt's press and
    release makes it a shortcut instead; Ctrl+Alt alone does nothing. keyd
    does the same for the same reason. Pressed at once: it goes with the key
    that typing waits for."""
    try:
        if not virtual_keyboard.send([(ecodes.KEY_LEFTCTRL, 1), (ecodes.KEY_LEFTCTRL, 0)]):
            logger.error("Couldn't press Ctrl to keep Right Alt from opening a menu")
    except virtual_keyboard.StoppedPartway:
        pass            # logged by send, which let go of the Ctrl


def read_compose_keys() -> None:
    """Read which keys the keyboard options make Compose, for
    note_compose_key: when the hotkey loop starts watching, and after each
    dictation, so options changed meanwhile count from the next press
    rather than the next start. Only when a dictation key is Right Ctrl or
    Right Alt: the desktop can take a moment to answer, and Left Ctrl (held
    with Space) is never asked about."""
    dictation_keys = _noted[1] & {ecodes.KEY_RIGHTCTRL, ecodes.KEY_RIGHTALT}
    note_compose_keys(keyboard_layout.compose_keys() if dictation_keys else ())


def note_compose_keys(bindings) -> None:
    """Remember which of the Right Ctrl and Right Alt dictation keys the
    keyboard options make Compose, as (code, with Shift) pairs
    (keyboard_layout.compose_keys), for note_compose_key. Another key a
    desktop holds back from apps (Scroll Lock) begins no compose, and one
    that isn't dictating is left to the desktop."""
    global _compose_keys
    dictation_keys = _noted[1] & {ecodes.KEY_RIGHTCTRL, ecodes.KEY_RIGHTALT}
    keys = frozenset(b for b in bindings if b[0] in dictation_keys)
    if keys != _compose_keys:
        names = sorted(("Shift+" if shift else "") + _NAMES.get(code, str(code))
                       for code, shift in keys)
        logger.info(f"Compose among the dictation keys: {', '.join(names) or 'none'}")
    _compose_keys = keys


def note_compose_key(event, dictation_keys, held_modifiers=frozenset()) -> None:
    """Where the keyboard options make a Right Ctrl or Right Alt dictation
    key the Compose key (Pop!_OS makes Right Ctrl one), alone or with the
    Shift in *held_modifiers*, its press reaches the focused app before
    TuxTalk can hide it and begins a compose there, which swallows the next
    keys sent: a paste's Ctrl+V, or the first letters typed. Compose pressed
    once more ends it, and is swallowed with it: no compose begins with
    two. That press goes when the key is let go, or before the first key
    typed while it's held (end_compose), once the desktop has been told
    it's up: a compositor drops a second press of a key it holds down. One
    per press: a toggle's start and stop taps each end their own. Called
    before the press is hidden or acted on, so the compose is known before
    anything is typed."""
    global _compose
    press = _compose
    if event.value == 0 and press is not None and event.code == press.code:
        end_compose()       # also once it's no longer a dictation key
    elif event.value == 1 and event.code in dictation_keys:
        shift = bool(held_modifiers & SHIFT_CODES)
        if (event.code, shift) in _compose_keys:
            press = _ComposePress(next(_compose_numbers), event.code, shift)
            with _compose_lock:
                _compose = press
            _look_later(press)


def _await_answer(press) -> None:
    """Give the hotkey helper a moment to answer whether it hid *press*,
    when the key is found up first: by the hotkey loop reading its release,
    or by typing under it (Type at pause). The press was hidden all the
    same, and its compose would be left begun (HelperKeyboard.await_hide)."""
    for keyboard in _keyboards():
        await_hide = getattr(keyboard, "await_hide", None)
        if await_hide is not None:
            await_hide(press.number, _ANSWER_WAIT_S)


def _look_later(press) -> None:
    """Note which window has the focus for *press* (_moved), off the hotkey
    loop: the tray can take a moment to answer, and the key isn't hidden
    yet."""
    threading.Thread(target=_look, args=(press,), name="compose-look", daemon=True).start()


def _look(press) -> None:
    try:
        press.where = focused_window.look()
    finally:
        press.looked.set()


def _moved(press) -> bool:
    """Whether another window is known to have the focus since *press*
    (held in one window, let go in another after a click): Compose pressed
    again there would begin a compose in it. The one the press began stays
    in the first window, out of reach.

    The look at the press may not have come back yet: it's waited for. It
    starts on its own thread as the key goes down and names the window the
    tray knows when it answers, so a click in its first few milliseconds
    would be missed. A look that doesn't come back in _LOOK_WAIT_S leaves
    the window unknown, which isn't another window, as for typing
    (focused_window.changed): treating it as one would cost the first keys
    of every dictation while the tray is busy, a far more common case than
    a click into another window meanwhile (CODE_HEALTH row 76)."""
    if not press.looked.wait(_LOOK_WAIT_S) or press.where is None:
        logger.info("Compose pressed again without knowing which window the dictation "
                    "key went down in")
        return False
    if not focused_window.changed(press.where, focused_window.look(_LOOK_WAIT_S)):
        return False
    logger.info("Compose not pressed again: another window has the focus since the "
                "dictation key went down")
    return True


def end_compose() -> None:
    """Press the Compose key once more if a press of the dictation key began
    a compose that nothing has ended yet (note_compose_key). Typing that
    finds the compose begun waits here until it's ended.

    Only for a press whose hiding is known to have worked, or was refused
    on no remapper's keyboard (_ComposePress says why), in the window it
    went to. The hotkey helper's answer may still be on its way when the
    key is found up (_await_answer), whether by the hotkey loop or by
    typing. A key no longer hidden (a toggle's stop tap lets go of the grab
    as it's pressed, and a refused one never was) gets a moment to be seen
    going up first. A press that didn't go out is tried once more, before
    the next key typed."""
    global _compose
    with _compose_lock:
        press = _compose
        if press is None:
            return
        if not (press.hidden or press.shown):
            _await_answer(press)
        if not (press.hidden or press.shown) or _moved(press):
            _compose = None
            return
        if press.code not in _hidden_now():
            time.sleep(_SETTLE_S)
        press.tries -= 1
        # Shift too for a key that is Compose only with it: the desktop was
        # told Shift went up with the press.
        shift = [(ecodes.KEY_LEFTSHIFT, 1)] if press.shift else []
        try:
            if not virtual_keyboard.send(shift + [(press.code, 1), (press.code, 0)]
                                         + [(k, 0) for k, _ in shift]):
                logger.error("Couldn't press Compose again to end the compose the dictation "
                             "key began; the first keys typed may be lost")
                if press.tries > 0:
                    return
        except virtual_keyboard.StoppedPartway:
            # Not tried again: had it gone out, another would begin a compose.
            logger.warning("Compose pressed again went out only partly; the first keys "
                           "typed may be lost")
        _compose = None


def _guard_if_alone(keyboards) -> None:
    """Typing that waits for a Right Alt dictation key presses the guard
    itself, rather than count on the hotkey loop having read the press
    first (it guards it as it reads it): typing, on the dictation worker,
    can get to the key before the loop does, and the desktop would see a
    lone Alt tap. Not with another modifier down, for right_alt_alone's
    reason."""
    if not keys_down(keyboards) & (MODIFIER_CODES - {ecodes.KEY_RIGHTALT}):
        guard_alt_tap()


def _give_up(held, what: str) -> None:
    if not held & _stuck:
        names = " and ".join(sorted(_NAMES.get(code, str(code)) for code in held))
        logger.warning(f"{names} has been down for {HELD_LIMIT_S:g} s, so {what}")
        _stuck.update(held)
    raise virtual_keyboard.GaveUp("key_held")


def right_alt_alone(event, dictation_keys, held_modifiers) -> bool:
    """Whether *event* is a Right Alt dictation key going down with no other
    modifier held, which guard_alt_tap keeps from opening a menu. Not
    with another modifier: the key isn't dictating then, and if Left Ctrl is
    down, the guard letting go of it could let it go there too."""
    return (event.value == 1 and event.code == ecodes.KEY_RIGHTALT
            and event.code in dictation_keys
            and not held_modifiers - {ecodes.KEY_RIGHTALT})
