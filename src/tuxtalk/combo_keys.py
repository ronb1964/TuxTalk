"""Dictation keys that are combos: Left Ctrl+Space, Alt+D, Ctrl+Shift+K.

The combo's last key (Space, D, K) is the code the hotkey loop listens for
and the one the hotkey helper reports and the desktops hold back, with the
modifiers. The modifiers are the condition: the key counts only when it
goes down while they are held. Without the condition every D typed would
start a dictation wherever TuxTalk reads the keyboards itself (the input
group, no hotkey helper). Letting go of one of the modifiers first ends the
combo as the key's release would, once: the key's own release after it is
nothing. Alt in a combo is Left Alt: Right Alt is AltGr on most layouts,
and the hotkey helper never counts it.

The hold hides the key and the modifiers held with it from the desktop
(hotkeys._start_hold_recording), so that what Type at pause types under it
isn't a row of Ctrl or Alt shortcuts. When the hold ends with Left Ctrl
still held, TuxTalk presses it again for the desktop (ctrl_kept), for a
Ctrl+Z after the dictation. Where that didn't happen (any other modifier)
the desktop takes the modifier to be up until it's let go, so the key
pressed again under it reaches the app as itself, and isn't the combo until
the modifier is pressed again (modifier_hidden). The hotkey helper doesn't
report such a key at all: it counts only a modifier the desktop saw go down
on a real keyboard, not TuxTalk's. The toggle hides nothing: typing waits
for its modifiers to come up (modifier_hotkeys.wait_while_held), and a
hidden one would make the second tap of a double tap, the modifier kept
down, the key itself.
"""
from typing import NamedTuple

from evdev import ecodes

from . import ctrl_kept
from .dictation_keys import CTRL_SPACE, canonical
from .hotkey_helper_client import KeyEvent

# The modifiers a combo names, as the keys that count for each.
_GROUPS = {
    "Ctrl": frozenset({ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL}),
    "Alt": frozenset({ecodes.KEY_LEFTALT}),
    "Super": frozenset({ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA}),
    "Shift": frozenset({ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT}),
}
# The letter the hotkey helper's WATCH spec gives each (hotkey_helper).
_HELPER_LETTERS = {"Ctrl": "C", "Alt": "A", "Super": "M", "Shift": "S"}


class ComboKey(NamedTuple):
    """A combo dictation key: its last key's *code*, the modifiers that must
    be held with it (*mods*, each a set of which any one will do), and its
    modifiers as a hotkey helper WATCH spec spells them (*letters*)."""
    code: int
    mods: tuple
    letters: str


LEFT_CTRL_SPACE = ComboKey(ecodes.KEY_SPACE, (frozenset({ecodes.KEY_LEFTCTRL}),), "L")


def parse(name: str) -> ComboKey | None:
    """Dictation key *name* as a ComboKey, or None when it's a key on its
    own (F8) or not a combo TuxTalk knows."""
    name = canonical(name)
    if name == CTRL_SPACE:
        return LEFT_CTRL_SPACE
    *mods, key = name.split("+")
    if not mods or any(mod not in _GROUPS for mod in mods):
        return None
    from .hotkey_keys import keycode_from_name
    code = keycode_from_name(key)
    if code is None:
        return None
    return ComboKey(code, tuple(_GROUPS[mod] for mod in mods),
                    "".join(sorted(_HELPER_LETTERS[mod] for mod in mods)))


def typing_modifiers(level3_keys) -> frozenset:
    """The modifiers a combo may name ("Alt", "Super") whose every key is
    among *level3_keys* (keyboard_layout.level3_keys): with it a letter
    types a character on this layout, so it can't make a dictation key.
    A modifier with another key left (Left Ctrl when Right Ctrl is AltGr)
    still works with that one, as Presses.use counts it."""
    return frozenset(mod for mod, group in _GROUPS.items() if group <= set(level3_keys))


def is_held(combo: ComboKey, held_modifiers) -> bool:
    """Whether every modifier *combo* names is among *held_modifiers*."""
    return all(group & set(held_modifiers) for group in combo.mods)


class Presses:
    """The key events the dictation keys see when a combo is one of them,
    from those read: a combo's key only as the combo, and one of its
    modifiers let go first as the combo's release too."""

    def __init__(self, combos=()):
        self._down = set()      # codes whose key went down as the combo and isn't up yet
        self._hidden = set()    # modifiers a hold hid from the desktop (modifier_hidden)
        self._combos = {}
        self.level3 = frozenset()   # the AltGr keys the combos were last made for
        self.use(combos)

    def use(self, combos, level3_keys=frozenset()) -> list:
        """Make *combos* (ComboKeys, hotkey_keys.Hotkeys.combos) the combo
        dictation keys. A key among *level3_keys* doesn't count as a
        modifier: with it a letter types a character (keyboard_layout), and
        each one would start a dictation. The combos left with a modifier
        no key can be are returned: they never start one."""
        self.level3 = frozenset(level3_keys)
        stripped = [combo._replace(mods=tuple(group - self.level3 for group in combo.mods))
                    for combo in combos]
        # Space is always Left Ctrl+Space: it never types as a dictation key.
        self._combos = {LEFT_CTRL_SPACE.code: LEFT_CTRL_SPACE,
                        **{combo.code: combo for combo in stripped}}
        return [combo for combo in stripped if not all(combo.mods)]

    def combo_for(self, code) -> ComboKey | None:
        """The combo dictation key whose last key is *code*, or None."""
        return self._combos.get(code)

    def held_with(self, code) -> frozenset:
        """The modifier keys dictation key *code* may be held with: none for
        a key on its own, both Ctrls for Ctrl+D."""
        combo = self.combo_for(code)
        return frozenset().union(*combo.mods) if combo else frozenset()

    def events(self, event, held_modifiers, dictation_keys) -> list:
        """What *event* is to the dictation keys, with *held_modifiers*
        already counting it: nothing, itself, or with a modifier let go
        first, itself and the combo's key coming up."""
        combos = {code: self.combo_for(code) for code in dictation_keys if self.combo_for(code)}
        if not combos:
            return [event]
        if any(event.code in group for combo in combos.values() for group in combo.mods):
            return self._modifier(event, held_modifiers, combos)
        combo = combos.get(event.code)
        if combo is None:
            return [event]
        if event.value == 1:
            # A modifier the hold hid is up to the desktop: the key under it
            # reaches the app, unless TuxTalk pressed Left Ctrl again (ctrl_kept).
            hidden = self._hidden - ({ecodes.KEY_LEFTCTRL} if ctrl_kept.pressed() else set())
            if is_held(combo, set(held_modifiers) - hidden):
                self._down.add(event.code)
                return [event]
            return []
        if event.code not in self._down:
            return []           # typing, or a release the combo already had
        if event.value == 0:
            self._down.discard(event.code)
        return [event]

    def _modifier(self, event, held_modifiers, combos) -> list:
        if event.value == 2:
            return [event]
        self._hidden.discard(event.code)
        out = [event]
        if event.value == 0:
            for code in sorted(self._down):
                if code in combos and not is_held(combos[code], held_modifiers):
                    self._down.discard(code)
                    out.append(KeyEvent(ecodes.EV_KEY, code, 0))
        return out

    def modifier_hidden(self, codes) -> None:
        """TuxTalk's own grab has hidden modifier *codes* from the desktop
        (modifier_hotkeys.hidden_by_own_grab): until each is pressed again,
        by the user or by TuxTalk (ctrl_kept), a key under it reaches the
        app, and isn't the combo."""
        self._hidden |= set(codes)

    def forget(self, still_down=()) -> None:
        """The combos' keys other than *still_down* won't be read coming up
        (their keyboard went away, or the keys changed): a later release of
        them isn't the combo's."""
        self._down &= set(still_down)
