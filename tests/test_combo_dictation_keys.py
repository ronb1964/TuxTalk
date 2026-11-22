"""Combo dictation keys like Alt+D and Ctrl+Shift+K (CODE_HEALTH 105).

The key counts only while its modifiers are held: plain D is typing,
wherever the keys are read. Letting go of either key ends a hold once, the
hold hides the modifiers with the key, Left Alt never opens an app's menu
bar, and the hotkey helper is asked for the combo, not the key alone.
"""
from types import SimpleNamespace

import pytest
from evdev import ecodes

from conftest import KeyEvent
from hotkey_loop_fakes import Cfg
from tuxtalk import (combo_keys, hotkey_keys, hotkeys, keyboard_grab, keyboard_layout,
                     modifier_hotkeys, notices, recording)
from tuxtalk import live_settings as live
from tuxtalk import recording_state as rs
from tuxtalk.hotkey_helper_client import watch_specs
from tuxtalk.hotkey_keys import Hotkeys

D, K, F9 = ecodes.KEY_D, ecodes.KEY_K, ecodes.KEY_F9
LALT, RALT, LCTRL, LSHIFT = (ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT, ecodes.KEY_LEFTCTRL,
                             ecodes.KEY_LEFTSHIFT)
ALT_D = combo_keys.ComboKey(D, (frozenset({LALT}),), "A")
CTRL_SHIFT_K = combo_keys.ComboKey(
    K, (frozenset({LCTRL, ecodes.KEY_RIGHTCTRL}), frozenset({LSHIFT, ecodes.KEY_RIGHTSHIFT})),
    "CS")


@pytest.fixture(autouse=True)
def _combos(monkeypatch):
    """Alt+D holds, Ctrl+Shift+K toggles, nothing down yet."""
    monkeypatch.setattr(hotkeys, "_combo_presses", combo_keys.Presses((ALT_D, CTRL_SHIFT_K)))


@pytest.fixture
def svc():
    return SimpleNamespace(last_activity=0.0, input_device_idx=0)


@pytest.fixture
def guarded(monkeypatch):
    """How often Alt was kept from opening a menu."""
    times = []
    monkeypatch.setattr(modifier_hotkeys, "guard_alt_tap", lambda: times.append(1))
    return times


def ev(code, value, held, svc, hold=D, toggle=K):
    keys = Hotkeys(hold, toggle, "", None, None, combos=(ALT_D, CTRL_SHIFT_K))
    hotkeys._on_key_event(KeyEvent(code, value), keys, held, [], Cfg(), svc, 1.0)


def test_the_names_become_the_combos():
    assert combo_keys.parse("Alt+D") == ALT_D
    assert combo_keys.parse("ctrl+shift+k") == CTRL_SHIFT_K
    assert combo_keys.parse("F8") is None


def test_holding_alt_d_dictates_and_letting_go_types(service, svc, guarded):
    held = set()
    ev(LALT, 1, held, svc)
    ev(D, 1, held, svc)
    assert service == ["grab", "start"] and guarded == [1]
    ev(D, 0, held, svc)
    assert service[-2:] == ["let go", "stop"]


def test_letting_go_of_alt_first_ends_the_dictation_once(service, svc, guarded):
    held = set()
    for code, value in ((LALT, 1), (D, 1), (LALT, 0)):
        ev(code, value, held, svc)
    assert service.count("stop") == 1
    ev(D, 0, held, svc)
    assert service.count("stop") == 1


def test_d_alone_and_altgr_d_are_typing(service, svc, guarded):
    """Right Alt is AltGr on most layouts: AltGr+D types a letter."""
    held = set()
    for code, value in ((D, 1), (D, 0), (RALT, 1), (D, 1), (D, 0), (RALT, 0)):
        ev(code, value, held, svc)
    assert service == [] and svc.last_activity == 0.0 and guarded == []


def test_ctrl_with_alt_d_is_someone_elses_shortcut(service, svc, guarded):
    held = set()
    for code, value in ((LCTRL, 1), (LALT, 1), (D, 1), (D, 0)):
        ev(code, value, held, svc)
    assert "start" not in service
    # The guard's Ctrl tap could have let go of the user's own Ctrl for the app.
    assert guarded == []


def test_the_hold_hides_d_and_alt(service, svc, guarded, monkeypatch):
    asked = []
    grab = keyboard_grab.grab_keyboards_hiding
    monkeypatch.setattr(keyboard_grab, "grab_keyboards_hiding",
                        lambda devices, codes: asked.append(set(codes)) or grab(devices, codes))
    held = set()
    ev(LALT, 1, held, svc)
    ev(D, 1, held, svc)
    assert asked == [{D, LALT}]


def test_tapping_ctrl_shift_k_starts_and_stops(service, svc, guarded):
    held = set()
    for _ in range(2):
        for code, value in ((LCTRL, 1), (LSHIFT, 1), (K, 1), (K, 0), (LSHIFT, 0), (LCTRL, 0)):
            ev(code, value, held, svc)
    assert service.count("start") == 1 and "stop" in service


def test_the_helper_is_asked_for_the_combos():
    assert watch_specs(D, K, 1, (), {D: "A", K: "CS"}) == ["A:32", "CS:37", "1"]


@pytest.mark.parametrize("hold, toggle", [(K, D), (D, K)])
def test_with_voice_editing_on_a_key_with_shift_in_it_still_dictates(
        hold, toggle, service, svc, guarded, monkeypatch):
    """Ctrl+Shift+K picked, voice editing turned on after: its own Shift made
    every press a voice edit of the selection, and plain dictation was gone."""
    monkeypatch.setattr(live, "voice_editing", True)
    edits = []

    def start(*a, **k):
        edits.append(k.get("edit_mode", False))
        rs.state.is_recording = True
        return rs.state.current
    monkeypatch.setattr(recording, "start_recording", start)
    held = set()
    for code, value in ((LCTRL, 1), (LSHIFT, 1), (K, 1)):
        ev(code, value, held, svc, hold=hold, toggle=toggle)
    assert edits == [False]


def test_where_left_alt_types_alt_d_types_too_and_the_user_is_told_once(
        service, svc, guarded, monkeypatch):
    """A keyboard option (lv3:lalt_switch) makes Left Alt+D type a character:
    each one typed would have started a dictation."""
    monkeypatch.setattr(keyboard_layout, "level3_keys", lambda: frozenset({LALT}))
    told = []
    monkeypatch.setattr(notices, "warn_dictation_key_types", lambda key, why: told.append(key))
    monkeypatch.setattr(hotkeys, "_said_typing_keys", ())
    monkeypatch.setattr(hotkeys, "_claim_desktop_hotkeys", lambda cfg: None)
    monkeypatch.setattr(modifier_hotkeys, "read_compose_keys", lambda: None)
    cfg = Cfg()
    cfg.hotkey = "Alt+D"
    keys = Hotkeys(D, K, "", None, None, combos=(ALT_D, CTRL_SHIFT_K))
    for _ in range(2):      # a settings change watches the keys again
        hotkeys._watch(cfg, [], keys)
    assert told == ["Alt+D"]
    held = set()
    for code, value in ((LALT, 1), (D, 1), (D, 0), (LALT, 0)):
        hotkeys._on_key_event(KeyEvent(code, value), keys, held, [], cfg, svc, 1.0)
    assert service == [] and guarded == []


def test_an_f13_key_pressed_in_preferences_is_one_the_loop_listens_for():
    assert hotkey_keys.dictation_keycode("F13") == ecodes.KEY_F13
    assert combo_keys.parse("Ctrl+F24").code == ecodes.KEY_F24
