"""Right Ctrl and Right Alt as dictation keys.

Both are modifiers: a key TuxTalk presses while one is down arrives as a
shortcut (Ctrl+W closes the tab), and a lone tap of Alt opens an app's menu
bar. The hotkey loop notes its keyboards and dictation keys here, and typing
asks before each key whether it must wait.
"""
import time

from evdev import ecodes

from .config import HOTKEY_CHOICES
from .hotkey_keys import MODIFIER_CODES, keys_down
from .logger import setup_logger
from .uinput_helper import is_keyboard_device
from .virtual_keyboard import GaveUp

logger = setup_logger(__name__)

# The keyboards the hotkey loop reads and its dictation keys that are
# modifiers; set by note().
_noted = ([], frozenset())
# The keys a grab hides from the desktop now; set by note_hidden().
_hidden = frozenset()

# How long typing waits for the key to come up. Pressed again while the last
# dictation is still being typed, it holds the rest up for as long as it's
# held, as if dictating the next sentence; that sentence isn't recorded,
# though: the hotkey loop is the thread typing, and reads the press only
# once typing is done (CODE_HEALTH.md). So this is longer than nearly any
# dictation; a key that reads as down for longer is stuck (a KVM switch, a
# remapper's keyboard that never sent the release). Typing then stops, and
# the dictation stays in Recent Dictations: typing on would turn the rest
# into Ctrl shortcuts wherever the desktop sees the key down too.
HELD_LIMIT_S = 30.0
# Keys found stuck: the next keys give up at once while they stay down,
# rather than each fallback (paste, then typing) waiting all over again.
_stuck = set()
# Key codes to the names Preferences shows ("Right Ctrl"), for the log.
_NAMES = {ecodes.ecodes[key]: name for name, key in HOTKEY_CHOICES.items()}


def note(devices, *keys) -> None:
    """Remember the hotkey loop's *devices* and which of its dictation *keys*
    are modifiers, for wait_while_held."""
    global _noted
    _noted = (devices, frozenset(k for k in keys if k in MODIFIER_CODES))


def note_hidden(codes) -> None:
    """Remember the keys a grab has just hidden from the desktop
    (app._grab_keyboards_hiding), or none once it lets go. The desktop has
    been told they're up, so typing under them is safe: Type at pause types
    each phrase while the hold key is still held."""
    global _hidden
    _hidden = frozenset(codes)


def wait_while_held(destination=None):
    """A check for virtual_keyboard.send to make before each key: None when
    no Right Ctrl or Right Alt dictation key the desktop can see is down,
    otherwise a function that waits until none is, which send runs with the
    keyboard free for other threads. It gives up after HELD_LIMIT_S and
    raises virtual_keyboard.GaveUp("key_held"). wait_while_held itself
    returns None when neither is a dictation key.

    *destination*, if given, says where the keys go (the focused window, a
    password field or not). It's asked when a wait starts and when it ends,
    and GaveUp("moved") stops the typing if the answers differ: the checks
    made before typing began are out of date by then, and the user may have
    clicked into a password field meanwhile."""
    devices, modifiers = _noted
    if not modifiers:
        return None
    keyboards = [d for d in list(devices) if is_keyboard_device(d)]

    def held():
        return keys_down(keyboards) & (modifiers - _hidden)

    def wait():
        where = destination() if destination else None
        give_up_at = time.monotonic() + HELD_LIMIT_S
        while still := held():
            if still & _stuck or time.monotonic() >= give_up_at:
                _give_up(still)
            time.sleep(0.02)
        _stuck.clear()
        if destination and destination() != where:
            logger.info("Typing stopped: the focus moved while it waited for the key")
            raise GaveUp("moved")

    def before_press():
        if held():
            return wait
        _stuck.clear()
        return None
    return before_press


def wait_until_up() -> bool:
    """Wait as typing would before its next key (wait_while_held); False when
    it gave up."""
    before_press = wait_while_held()
    wait = before_press() if before_press else None
    try:
        if wait:
            wait()
    except GaveUp:
        return False
    return True


def _give_up(held) -> None:
    if not held & _stuck:
        names = " and ".join(sorted(_NAMES.get(code, str(code)) for code in held))
        logger.warning(f"{names} has been down for {HELD_LIMIT_S:g} s, so typing stopped; "
                       "the dictation is in Recent Dictations")
        _stuck.update(held)
    raise GaveUp("key_held")


def right_alt_alone(event, dictation_keys, held_modifiers) -> bool:
    """Whether *event* is a Right Alt dictation key going down with no other
    modifier held, which app._guard_alt_tap keeps from opening a menu. Not
    with another modifier: the key isn't dictating then, and if Left Ctrl is
    down, the guard letting go of it could let it go there too."""
    return (event.value == 1 and event.code == ecodes.KEY_RIGHTALT
            and event.code in dictation_keys
            and not held_modifiers - {ecodes.KEY_RIGHTALT})
