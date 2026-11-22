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


SHORTCUTS = (("cheat sheet", "Ctrl+Alt+V"), ("paste last dictation", "Ctrl+Alt+P"))


@pytest.mark.parametrize("name, other, voice_editing, typing, refused", [
    ("Alt+D", "F9", False, set(), False),
    ("Alt+D", "Ctrl+D", False, set(), True),        # the same last key
    ("Ctrl+Shift+K", "F9", True, set(), True),      # Shift is voice editing's
    ("Ctrl+Shift+K", "F9", False, set(), False),
    ("Alt+D", "F9", False, {"Alt"}, True),          # Left Alt types on this layout
    ("Super+D", "F9", False, {"Super"}, True),      # so does the Windows key
    ("Ctrl+D", "F9", False, {"Ctrl"}, True),        # and Right Ctrl
    ("Ctrl+D", "F9", False, {"Alt"}, False),
    ("Ctrl+D", "Right Ctrl", False, set(), True),   # Right Ctrl would be the combo's Ctrl
    ("Right Ctrl", "Ctrl+Alt+K", False, set(), True),
    ("Ctrl+Alt+P", "F9", False, set(), True),       # Paste Last's own shortcut
    ("Ctrl+Alt+Shift+P", "F9", False, set(), True),  # which it answers to first
    ("Alt+P", "F9", False, set(), False),
    ("D", "F9", False, set(), True),
])
def test_what_preferences_refuses(name, other, voice_editing, typing, refused):
    why = dictation_keys.refusal(name, other, voice_editing, frozenset(typing), SHORTCUTS)
    assert bool(why) is refused


def test_shift_with_an_f_key_says_shift_is_the_problem():
    assert dictation_keys.problem("Shift+F8").startswith("Shift alone")


def test_where_the_desktop_cant_hold_a_combo_back_the_warning_says_the_app_gets_it():
    assert "gets it as well" in dictation_keys.warning("Alt+D", held_back=False)
    assert dictation_keys.warning("F8", held_back=False) is None


def test_a_combo_warns_that_apps_shortcuts_cant_be_checked():
    assert "menu" in dictation_keys.warning("Alt+D")
    assert "can't check" in dictation_keys.warning("Ctrl+D")
    assert dictation_keys.warning("F8") is None
    assert dictation_keys.warning("Left Ctrl+Space") is None
