"""Which keys the first-run hotkey test accepts, reports and saves.

- Change Key refuses keys the service can't use as a hotkey (Pause, space,
  a letter): they would leave the app with no hotkey, and a letter would
  start dictation while typing. It takes what Preferences offers: F1 to F12,
  Right Ctrl, Right Alt and Scroll Lock.
- Closing the test with the window's X on first run saves the keys shown,
  since the settings start with no hotkey and the service would watch no key.
- The evdev reader passes on releases too, since only a release marks a key
  as working; otherwise a key the compositor grabbed never tests as working.
"""
from types import SimpleNamespace

import pytest

from tuxtalk import hotkey_test_keys as htk


@pytest.mark.parametrize("key", ["Pause", "space", "a", "Shift_L", "Control_L", "KP_0", "F13"])
def test_keys_the_service_cannot_use_are_refused(key):
    assert htk.captured_key_problem(key, "F9")


@pytest.mark.parametrize("key", ["a", "Z", "space", "BackSpace", "1", "Return"])
def test_ordinary_keys_pressed_during_the_test_stay_out_of_the_log(key):
    """Without the hotkey helper the test reads every keyboard: a password
    typed in another window while it's open must not end up in the log."""
    assert htk.loggable_key(key) == "another key"


@pytest.mark.parametrize("key", ["F8", "F12", "Escape", "Pause", "Scroll_Lock", "Right Ctrl"])
def test_the_keys_the_test_is_about_are_still_logged(key):
    assert htk.loggable_key(key) == key


@pytest.mark.parametrize("key", ["F10", "Right Ctrl", "Right Alt", "Scroll Lock"])
def test_the_keys_preferences_offers_are_accepted(key):
    assert htk.captured_key_problem(key, "F9") is None


def test_a_key_that_needs_no_fn_survives_the_test_closing():
    """Closing the test must not put a Right Ctrl picked in Preferences
    back to F8."""
    assert htk.hotkeys_to_save(_config("Right Ctrl", "Scroll Lock"), "Right Ctrl",
                               "Scroll Lock", confirmed=False) == ("Right Ctrl", "Scroll Lock")


def test_both_hotkeys_cannot_be_the_same_key():
    assert "other hotkey" in htk.captured_key_problem("F9", "F9")


def _config(hold="", toggle=""):
    return SimpleNamespace(hotkey=hold, toggle_hotkey=toggle)


def test_closing_with_the_x_on_first_run_saves_the_shown_keys():
    assert htk.hotkeys_to_save(_config(), "F8", "F9", confirmed=False) == ("F8", "F9")


def test_closing_with_the_x_keeps_keys_already_saved():
    assert htk.hotkeys_to_save(_config("F6", "F7"), "F10", "F11", confirmed=False) == ("F6", "F7")


def test_continue_saves_the_chosen_keys():
    assert htk.hotkeys_to_save(_config("F6", "F7"), "F10", "F11", confirmed=True) == ("F10", "F11")


def test_an_unusable_saved_key_falls_back_to_the_defaults():
    assert htk.hotkeys_to_save(_config("Pause", "Pause"), "Pause", "Pause",
                               confirmed=False) == ("F8", "F9")


MODS = {56, 100}          # left and right Alt, as evdev numbers them
NAMES = {66: "F8", 67: "F9"}


def _event(code, value):
    return SimpleNamespace(code=code, value=value)


def test_evdev_releases_are_passed_on():
    held = set()
    assert htk.evdev_key_action(_event(66, 1), held, MODS, NAMES.get) == ("F8", "press")
    assert htk.evdev_key_action(_event(66, 0), held, MODS, NAMES.get) == ("F8", "release")


def test_evdev_alt_f8_does_not_count_as_f8():
    held = set()
    htk.evdev_key_action(_event(56, 1), held, MODS, NAMES.get)       # Alt down
    assert htk.evdev_key_action(_event(66, 1), held, MODS, NAMES.get) is None
    assert htk.evdev_key_action(_event(66, 0), held, MODS, NAMES.get) is None
    htk.evdev_key_action(_event(56, 0), held, MODS, NAMES.get)       # Alt up
    assert htk.evdev_key_action(_event(66, 0), held, MODS, NAMES.get) == ("F8", "release")


def test_evdev_auto_repeat_and_unknown_keys_are_ignored():
    assert htk.evdev_key_action(_event(66, 2), set(), MODS, NAMES.get) is None
    assert htk.evdev_key_action(_event(30, 1), set(), MODS, NAMES.get) is None


def test_right_ctrl_alone_tests_as_a_key_but_not_as_part_of_a_shortcut():
    names = {97: "Right Ctrl", 66: "F8"}
    mods = {29, 97}         # left and right Ctrl
    held = set()
    assert htk.evdev_key_action(_event(97, 1), held, mods, names.get) == ("Right Ctrl", "press")
    assert htk.evdev_key_action(_event(97, 0), held, mods, names.get) == ("Right Ctrl", "release")
    htk.evdev_key_action(_event(29, 1), held, mods, names.get)       # Left Ctrl down
    assert htk.evdev_key_action(_event(97, 1), held, mods, names.get) is None
    assert htk.evdev_key_action(_event(66, 1), held, mods, names.get) is None


@pytest.mark.parametrize("gtk_name,key", [("Control_R", "Right Ctrl"), ("Alt_R", "Right Alt"),
                                          ("ISO_Level3_Shift", "Right Alt"),
                                          ("Scroll_Lock", "Scroll Lock"), ("F8", "F8")])
def test_gtk_names_the_keys_as_the_settings_do(gtk_name, key):
    """On a German layout GTK calls Right Alt ISO_Level3_Shift (AltGr): it
    must still test as Right Alt, or the test never says it works."""
    assert htk.gtk_key_name(gtk_name) == key


def test_evdev_names_the_keys_as_the_settings_do():
    ecodes = pytest.importorskip("evdev").ecodes
    assert htk.evdev_key_name(ecodes.KEY_RIGHTCTRL) == "Right Ctrl"
    assert htk.evdev_key_name(ecodes.KEY_SCROLLLOCK) == "Scroll Lock"
    assert htk.evdev_key_name(ecodes.KEY_F8) == "F8"
    assert htk.evdev_key_name(ecodes.KEY_A) == "a"
    assert htk.evdev_key_name(ecodes.KEY_PAUSE) is None
