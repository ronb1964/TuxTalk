"""Which keys may be a dictation key (dictation_keys.py, CODE_HEALTH 105): a
key that types never, a combo with Ctrl, Alt or the Windows key always."""
import pytest

from tuxtalk import dictation_keys
from tuxtalk.config import Settings, repaired


@pytest.mark.parametrize("name", [
    "", "F8", "F13", "F24", "Right Ctrl", "Right Alt", "Scroll Lock", "Left Ctrl+Space",
    "Alt+D", "Ctrl+D", "Super+K", "Ctrl+Shift+K", "Ctrl+Alt+D", "Alt+F7", "alt+d",
])
def test_keys_that_dont_type_may_be_dictation_keys(name):
    assert dictation_keys.problem(name) is None


@pytest.mark.parametrize("name", [
    "D", "d", "Space", "Tab", "Enter", "Shift", "Shift+D", "Shift+F8",
    "Hyper+D", "Alt+1", "Alt+Space",
])
def test_keys_that_type_are_refused_with_a_reason(name):
    assert dictation_keys.problem(name)


def test_names_are_spelled_as_preferences_shows_them():
    assert dictation_keys.canonical("shift+ctrl+k") == "Ctrl+Shift+K"
    assert dictation_keys.canonical("right ctrl") == "Right Ctrl"
    assert dictation_keys.canonical("left ctrl+space") == "Left Ctrl+Space"


def test_a_letter_written_into_the_settings_by_hand_is_refused():
    """hotkey = "d" started a dictation with every d typed, where TuxTalk
    reads the keyboards itself."""
    s = Settings(hotkey="d", toggle_hotkey="F9")
    assert repaired(s).hotkey == "" and s.toggle_hotkey == "F9"


@pytest.mark.parametrize("hold, toggle", [("Alt+D", "Ctrl+D"), ("F8", "Ctrl+F8")])
def test_the_toggle_key_cant_end_in_the_hold_keys_key(hold, toggle):
    s = Settings(hotkey=hold, toggle_hotkey=toggle)
    assert repaired(s).toggle_hotkey == "" and s.hotkey == hold


def test_different_keys_stay():
    s = Settings(hotkey="Alt+D", toggle_hotkey="Alt+F")
    assert repaired(s).toggle_hotkey == "Alt+F"


@pytest.mark.parametrize("name, other, voice_editing, left_alt_types, refused", [
    ("Alt+D", "F9", False, False, False),
    ("Alt+D", "Ctrl+D", False, False, True),        # the same last key
    ("Ctrl+Shift+K", "F9", True, False, True),      # Shift is voice editing's
    ("Ctrl+Shift+K", "F9", False, False, False),
    ("Alt+D", "F9", False, True, True),             # Left Alt types on this layout
    ("Ctrl+D", "F9", False, True, False),
    ("D", "F9", False, False, True),
])
def test_what_preferences_refuses(name, other, voice_editing, left_alt_types, refused):
    assert bool(dictation_keys.refusal(name, other, voice_editing, left_alt_types)) is refused


def test_a_combo_warns_that_apps_shortcuts_cant_be_checked():
    assert "menu" in dictation_keys.warning("Alt+D")
    assert "can't check" in dictation_keys.warning("Ctrl+D")
    assert dictation_keys.warning("F8") is None
    assert dictation_keys.warning("Left Ctrl+Space") is None
