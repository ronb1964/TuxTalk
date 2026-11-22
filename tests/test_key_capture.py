"""Preferences' "Other key or combination…" (CODE_HEALTH 105): a dictation key
picked by pressing it, Alt+D or Ctrl+Shift+K, beyond what the lists hold."""
import pytest
from gi.repository import Gdk

from tuxtalk import prefs_key_capture


def _key(name):
    return Gdk.keyval_from_name(name)


CTRL, ALT, SUPER, SHIFT = (Gdk.ModifierType.CONTROL_MASK, Gdk.ModifierType.MOD1_MASK,
                           Gdk.ModifierType.SUPER_MASK, Gdk.ModifierType.SHIFT_MASK)


@pytest.mark.parametrize("keyval, state, name", [
    ("d", ALT, "Alt+D"),
    ("K", CTRL | SHIFT, "Ctrl+Shift+K"),
    ("F13", 0, "F13"),
    ("Scroll_Lock", 0, "Scroll Lock"),
    ("e", SUPER, "Super+E"),
    ("a", 0, "A"),                     # read, then refused by dictation_keys
])
def test_the_keys_pressed_are_named_as_preferences_shows_them(keyval, state, name):
    assert prefs_key_capture.key_name(_key(keyval), state) == name


@pytest.mark.parametrize("keyval", ["Alt_L", "Control_R", "Shift_L", "Tab", "Return", "Escape",
                                    "space"])
def test_a_modifier_alone_or_the_windows_own_keys_arent_picked(keyval):
    """Tab, Enter and Escape still move around the window and press its
    buttons, so it works from the keyboard alone."""
    assert prefs_key_capture.key_name(_key(keyval), 0) is None


def test_a_combination_pressed_becomes_the_hold_key(prefs_window, monkeypatch):
    tab = prefs_window().general_tab
    asked = []
    monkeypatch.setattr(prefs_key_capture, "ask",
                        lambda parent, role, other, editing: asked.append(role) or "Alt+D")
    tab.hotkey_combo.set_active_id("other")
    assert asked == ["hold"]
    assert tab.hotkey_combo.get_active_id() == "Alt+D"
    assert tab.config["hotkey"] == "Alt+D"
    ids = [row[1] for row in tab.hotkey_combo.get_model()]
    assert ids[-2:] == ["Alt+D", "other"]       # kept above "Other…"


def test_cancelling_puts_the_key_back(prefs_window, monkeypatch):
    tab = prefs_window(hotkey="F8").general_tab
    monkeypatch.setattr(prefs_key_capture, "ask", lambda *a: None)
    tab.hotkey_combo.set_active_id("other")
    assert tab.hotkey_combo.get_active_id() == "F8" and tab.config["hotkey"] == "F8"
