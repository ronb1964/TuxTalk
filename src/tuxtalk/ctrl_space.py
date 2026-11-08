"""Left Ctrl+Space as a dictation key.

Space is the key the hotkey loop listens for (config.HOTKEY_CHOICES), and
Left Ctrl the condition: a Space counts only when it goes down while Left
Ctrl is held. Space is the code because its press and release are the
shortcut's (Ctrl goes down first and comes up last), and it's the key the
hotkey helper reports and the desktops hold back, with Ctrl. Plain Space is
typing: without the condition every space typed would start a dictation
wherever TuxTalk reads the keyboards itself (the input group, no hotkey
helper). Letting go of Left Ctrl first ends the pair as Space's release
would, once: Space's own release after it is nothing.

The hold hides Space and Left Ctrl from the desktop (hotkeys
_start_hold_recording), so that what Type at pause types under it isn't a
row of Ctrl shortcuts. When the hold ends with Left Ctrl still held, TuxTalk
presses it again for the desktop (ctrl_kept). Where that didn't happen the
desktop takes Left Ctrl to be up until it's let go, so a Space pressed again
under it reaches the app as a space, and isn't the pair until Left Ctrl is
pressed again (modifier_hidden). The hotkey helper doesn't report such a
Space at all: it counts only a Ctrl the desktop saw go down on a real
keyboard, not TuxTalk's. The toggle hides nothing: Space isn't a modifier,
typing waits for a Left Ctrl the desktop sees
(modifier_hotkeys.wait_while_held), and a hidden one would make the second
tap of a double tap, Ctrl kept down, a space.
"""
from evdev import ecodes

from . import ctrl_kept
from .hotkey_helper_client import KeyEvent

KEY = ecodes.KEY_SPACE
MODIFIER = ecodes.KEY_LEFTCTRL


def held_with(code) -> frozenset:
    """The modifier dictation key *code* is held with: Left Ctrl for Space."""
    return frozenset({MODIFIER}) if code == KEY else frozenset()


class Presses:
    """The key events the dictation keys see when Space is one of them, from
    those read: a Space only as the pair, and Left Ctrl let go first as the
    pair's release too."""

    def __init__(self):
        self._down = False          # Space went down as the pair and isn't up yet
        self._ctrl_hidden = False   # a hold hid this press of Left Ctrl (modifier_hidden)

    def events(self, event, held_modifiers, dictation_keys) -> list:
        """What *event* is to the dictation keys, with *held_modifiers*
        already counting it: nothing, itself, or with a Left Ctrl let go
        first, itself and the pair's Space coming up."""
        if KEY not in dictation_keys:
            return [event]
        if event.code == MODIFIER:
            return self._modifier(event)
        if event.code != KEY:
            return [event]
        if event.value == 1:
            # Pressed again for the desktop, it sees Ctrl+Space: the pair.
            self._down = MODIFIER in held_modifiers and not (
                self._ctrl_hidden and not ctrl_kept.pressed())
            return [event] if self._down else []
        if not self._down:
            return []           # typing, or a release the pair already had
        if event.value == 0:
            self._down = False
        return [event]

    def _modifier(self, event) -> list:
        if event.value == 2:
            return [event]
        self._ctrl_hidden = False
        if event.value == 0 and self._down:
            self._down = False
            return [event, KeyEvent(ecodes.EV_KEY, KEY, 0)]
        return [event]

    def modifier_hidden(self) -> None:
        """TuxTalk's own grab has hidden Left Ctrl from the desktop
        (modifier_hotkeys.hidden_by_own_grab): until it's pressed again, by
        the user or by TuxTalk (ctrl_kept), a Space under it reaches the
        app, and isn't the pair."""
        self._ctrl_hidden = True

    def forget(self) -> None:
        """The pair's Space won't be read coming up (its keyboard went away,
        or the keys changed): a later Space release isn't the pair's."""
        self._down = False
