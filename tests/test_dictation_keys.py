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
