"""Right Ctrl and Right Alt as dictation keys.

Both are modifiers: a key TuxTalk presses while one is down arrives as a
shortcut (Ctrl+W closes the tab), and a lone tap of Alt opens an app's menu
bar. Where the keyboard options make one the Compose key (Pop!_OS's Right
Ctrl), its press begins a compose that would swallow the next keys typed.
The hotkey loop notes its keyboards and dictation keys here, and typing
asks before each key whether it must wait or end a compose first.
"""
import threading
import time

from evdev import ecodes

from . import focused_window, virtual_keyboard
from .config import HOTKEY_CHOICES
from .hotkey_keys import MODIFIER_CODES, keys_down
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
# Right Ctrl or Right Alt when the keyboard options make it Compose, read
# when the hotkey loop starts watching (note_compose_keys), not on each
# press: the desktop can take a moment to answer.
_compose_keys = frozenset()
# The Compose dictation key whose press began a compose in the focused app
# that TuxTalk hasn't ended yet (note_compose_key); set on the hotkey loop,
# ended there or by typing. Cleared only once the press that ends it has
# gone, under _compose_lock, so typing that finds it set waits for that.
# Lock order: _compose_lock, then virtual_keyboard's.
_compose_begun = None
_compose_lock = threading.Lock()
# Hiding the press that began _compose_begun is known to have worked
# (note_compose_hidden): only then does the desktop take the key to be up,
# so a second press isn't dropped, and the key didn't come through a key
# remapper, whose keyboard is never held (kanata takes TuxTalk's keyboard
# too unless told not to, and would hand Compose pressed again back as the
# dictation key: a recording that ends its own compose again, without end).
# A press left to the desktop (the hotkey test, a tap that only holds a
# send, the user's own shortcut, a tap already up when it was read) isn't.
_compose_hidden = False
# Tries left for the press that ends a compose, when sending it fails.
_compose_tries = 0
# How long the desktop gets to see a key it wasn't hidden from go up before
# Compose is pressed again: a compositor drops a press of a key it holds
# down, and reads a key event within a frame (16 ms at 60 Hz).
_SETTLE_S = 0.05
# Alt, Ctrl and Super: a key TuxTalk presses while one is down is a shortcut
# (Alt+Space opens KRunner). Not Shift: Shift+Space is still a space.
SHORTCUT_MODIFIERS = frozenset(MODIFIER_CODES - {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT})
# Key codes to the names Preferences shows ("Right Ctrl"), for the log.
_NAMES = {ecodes.ecodes[key]: name for name, key in HOTKEY_CHOICES.items()}


def note(devices, *keys) -> None:
    """Remember the hotkey loop's *devices* and which of its dictation *keys*
    are modifiers, for wait_while_held."""
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


def note_compose_hidden(codes=None) -> None:
    """Hiding a press of *codes* worked: a grab TuxTalk holds caught them
    still down on a keyboard it grabbed (never a remapper's), or (None) the
    hotkey helper said OK to hiding the press. Its compose is TuxTalk's to
    end (end_compose)."""
    global _compose_hidden
    if _compose_begun is not None and (codes is None or _compose_begun in codes):
        _compose_hidden = True


def _hidden_now():
    codes, keyboards = _hidden
    return codes if all(getattr(k, "hidden", True) for k in keyboards) else frozenset()


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


def wait_while_held(destination=None):
    """A check for virtual_keyboard.send to make before each key: None when
    no Right Ctrl or Right Alt dictation key the desktop can see is down and
    no compose one began is left to end, otherwise a function that waits
    until none is and ends it (end_compose), which send runs with the
    keyboard free for other threads. It gives up after HELD_LIMIT_S and
    raises virtual_keyboard.GaveUp("key_held"). wait_while_held itself
    returns None when neither is a dictation key.

    *destination*, if given, says where the keys go (focused_window.destination: the
    focused window's class, which window it is, and whether the cursor is in
    a password field). It's asked when a wait starts and when it ends, and
    GaveUp("moved") stops the typing if the answers differ in a part both
    know (focused_window.changed): the checks made before typing began are
    out of date by then, and the user may have clicked into a password
    field meanwhile."""
    devices, modifiers = _noted
    if not modifiers:
        return None
    keyboards = [d for d in list(devices) if is_keyboard_device(d)]

    def held():
        return keys_down(keyboards) & (modifiers - _hidden_now())

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
                _give_up(still)
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
        return end_compose if _compose_begun is not None else None
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


def note_compose_keys(codes) -> None:
    """Remember which of Right Ctrl and Right Alt the keyboard options make
    Compose (keyboard_layout.compose_keys), for note_compose_key. Another key
    a desktop holds back from apps (Scroll Lock) begins no compose."""
    global _compose_keys
    _compose_keys = frozenset(codes) & {ecodes.KEY_RIGHTCTRL, ecodes.KEY_RIGHTALT}


def note_compose_key(event, dictation_keys) -> None:
    """Where the keyboard options make a Right Ctrl or Right Alt dictation
    key the Compose key (Pop!_OS makes Right Ctrl one), its press reaches the
    focused app before TuxTalk can hide it and begins a compose there, which
    swallows the next keys sent: a paste's Ctrl+V, or the first letters
    typed. Compose pressed once more ends it, and is swallowed with it: no
    compose begins with two. That press goes when the key is let go, or
    before the first key typed while it's held (end_compose), once the
    desktop has been told it's up: a compositor drops a second press of a key
    it holds down. One per press: a toggle's start and stop taps each end
    their own. Called before the press is hidden or acted on, so the compose
    is known before anything is typed."""
    global _compose_begun, _compose_hidden, _compose_tries
    if event.value == 0 and event.code == _compose_begun:
        end_compose()       # also once it's no longer a dictation key
    elif event.value == 1 and event.code in dictation_keys and event.code in _compose_keys:
        with _compose_lock:
            _compose_begun, _compose_hidden, _compose_tries = event.code, False, 2


def end_compose() -> None:
    """Press the Compose key once more if a press of the dictation key began
    a compose that nothing has ended yet (note_compose_key). Typing that
    finds the compose begun waits here until it's ended.

    Only for a press whose hiding is known to have worked (_compose_hidden
    says why). A key no longer hidden (a toggle's stop tap lets go of the
    grab as it's pressed) gets a moment to be seen going up first. A press
    that didn't go out is tried once more, before the next key typed."""
    global _compose_begun, _compose_tries
    with _compose_lock:
        code = _compose_begun
        if code is None:
            return
        if not _compose_hidden:
            _compose_begun = None
            return
        if code not in _hidden_now():
            time.sleep(_SETTLE_S)
        _compose_tries -= 1
        try:
            if not virtual_keyboard.send([(code, 1), (code, 0)]):
                logger.error("Couldn't press Compose again to end the compose the dictation "
                             "key began; the first keys typed may be lost")
                if _compose_tries > 0:
                    return
        except virtual_keyboard.StoppedPartway:
            # Not tried again: had it gone out, another would begin a compose.
            logger.warning("Compose pressed again went out only partly; the first keys "
                           "typed may be lost")
        _compose_begun = None


def _guard_if_alone(keyboards) -> None:
    """Typing that waits for a Right Alt dictation key presses the guard
    itself, rather than count on the hotkey loop having read the press
    first (it guards it as it reads it): typing, on the dictation worker,
    can get to the key before the loop does, and the desktop would see a
    lone Alt tap. Not with another modifier down, for right_alt_alone's
    reason."""
    if not keys_down(keyboards) & (MODIFIER_CODES - {ecodes.KEY_RIGHTALT}):
        guard_alt_tap()


def _give_up(held) -> None:
    if not held & _stuck:
        names = " and ".join(sorted(_NAMES.get(code, str(code)) for code in held))
        logger.warning(f"{names} has been down for {HELD_LIMIT_S:g} s, so typing stopped; "
                       "the dictation is in Recent Dictations")
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
