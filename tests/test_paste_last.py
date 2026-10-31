"""Paste Last Dictation: a hotkey (Ctrl+Alt+P by default) that types the last
dictation again, for when it went to the wrong window.

It types only once every key of the combo is let go: typing while Ctrl and
Alt are still held would turn each letter into a Ctrl+Alt shortcut.
"""
import pytest
from evdev import ecodes

from gtk_text import tree_text
from conftest import KeyEvent
from tuxtalk import config, history, kwin_hotkeys
from tuxtalk import hotkeys
from tuxtalk import notices
from tuxtalk import recording_state as rs
from tuxtalk import live_settings as live
from tuxtalk import keyboard_grab
from tuxtalk import delivery
from tuxtalk.beeps import CANCEL_BEEP
from tuxtalk import recording
from tuxtalk import pipeline

P = ecodes.KEY_P
CTRL, ALT = ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTALT
COMBO = ({"CTRL", "ALT"}, P)


class Cfg:
    beeps = False
    notify = False
    injection_mode = "type"
    auto_space = True


@pytest.fixture
def keys(monkeypatch):
    """A fake keyboard: grabs, typing and the Recent Dictations list recorded."""
    seen = {"grabbed": 0, "released": 0, "typed": [], "beeps": []}
    monkeypatch.setattr(keyboard_grab, "grab_keyboards_hiding", lambda devs, codes: seen.__setitem__("grabbed", seen["grabbed"] + 1))
    monkeypatch.setattr(keyboard_grab, "release_all", lambda: seen.__setitem__("released", seen["released"] + 1))
    monkeypatch.setattr(delivery, "inject_text", lambda text, mode: seen["typed"].append((text, mode)) or "ok")
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda on, *a: seen["beeps"].append(a))
    monkeypatch.setattr(history, "get_entries", lambda: ["Hello there.\nSecond line.", "older"])
    monkeypatch.setattr(rs.state, "is_recording", False)
    monkeypatch.setattr(rs.state, "paste_last", None, raising=False)
    return seen


def send(code, value, held, combo=COMBO):
    """Deliver one key event the way the main loop does: modifier state first."""
    if code in (CTRL, ALT):
        (held.add if value else held.discard)(code)
    hotkeys._handle_key_event(KeyEvent(code, value), ecodes.KEY_F8, None,
                          None, held, [], Cfg(), 0, paste_last_combo=combo)


def press_combo(held):
    send(CTRL, 1, held)
    send(ALT, 1, held)
    send(P, 1, held)


def test_types_the_last_dictation_once_every_key_is_up(keys):
    held = set()
    press_combo(held)
    assert keys["grabbed"] == 1 and keys["typed"] == []      # P still down
    send(P, 0, held)
    assert keys["typed"] == []                                # Ctrl, Alt still down
    send(ALT, 0, held)
    assert keys["typed"] == []
    send(CTRL, 0, held)
    assert keys["typed"] == [("Hello there.§SHIFT_ENTER§Second line. ", "type")]
    assert keys["released"] >= 1


def test_modifiers_let_go_before_the_key_works_too(keys):
    held = set()
    press_combo(held)
    send(CTRL, 0, held)
    send(ALT, 0, held)
    assert keys["typed"] == []
    send(P, 0, held)
    assert len(keys["typed"]) == 1


def test_it_fires_once_per_press(keys):
    held = set()
    press_combo(held)
    for code in (P, ALT, CTRL):
        send(code, 0, held)
    send(ecodes.KEY_A, 0, held)                               # a later unrelated release
    assert len(keys["typed"]) == 1


def test_nothing_dictated_yet_gives_the_cancel_beep(keys, monkeypatch):
    monkeypatch.setattr(history, "get_entries", lambda: [])
    held = set()
    press_combo(held)
    for code in (P, ALT, CTRL):
        send(code, 0, held)
    assert keys["typed"] == [] and keys["beeps"] == [CANCEL_BEEP]
    assert keys["released"] >= 1


def test_not_while_recording(keys, monkeypatch):
    monkeypatch.setattr(rs.state, "is_recording", True)
    held = set()
    press_combo(held)
    assert keys["grabbed"] == 0


def test_without_a_combo_nothing_happens(keys):
    held = set()
    send(CTRL, 1, held, combo=None)
    send(P, 1, held, combo=None)
    send(P, 0, held, combo=None)
    send(CTRL, 0, held, combo=None)
    assert keys["grabbed"] == 0 and keys["typed"] == []


def test_no_trailing_space_without_auto_space(keys, monkeypatch):
    monkeypatch.setattr(Cfg, "auto_space", False)
    held = set()
    press_combo(held)
    for code in (P, ALT, CTRL):
        send(code, 0, held)
    assert keys["typed"][0][0] == "Hello there.§SHIFT_ENTER§Second line."


# --- the setting ----------------------------------------------------------------

def test_on_by_default_and_applied_without_a_restart():
    assert config.Settings().paste_last_hotkey == "Ctrl+Alt+P"
    assert "paste_last_hotkey" in config.LIVE_APPLIED_KEYS


def test_kde_and_gnome_hold_it_back_from_the_focused_app():
    cfg = config.Settings(hotkey="F8", toggle_hotkey="F9")
    names = [name for name, _what, _key in kwin_hotkeys.wanted_shortcuts(cfg)]
    assert any("paste-last" in n for n in names)


def test_preferences_offers_it(prefs_window):
    from gi.repository import Gtk
    from gtk_text import beside, find
    window = prefs_window()
    keys = beside(find(window.window, Gtk.Label, "Paste Last Dictation:"))
    keys.set_active_id("Ctrl+Super+P")
    assert window.config["paste_last_hotkey"] == "Ctrl+Super+P"
    keys.set_active_id("")
    assert window.config["paste_last_hotkey"] == ""


def test_a_hand_edited_key_shows_and_none_turns_it_off(prefs_window):
    """A key typed into config.toml that the list doesn't offer is the one
    in use: shown as itself, and picking None really turns it off."""
    from gi.repository import Gtk
    from gtk_text import beside, find
    window = prefs_window(paste_last_hotkey="Ctrl+Shift+V")
    keys = beside(find(window.window, Gtk.Label, "Paste Last Dictation:"))
    assert keys.get_active_id() == "Ctrl+Shift+V"
    keys.set_active_id("")
    assert window.config["paste_last_hotkey"] == ""


# --- telling people it exists ------------------------------------------------------
# Where they'd need it: the "couldn't type that" notification, the Recent
# Dictations menu, and the tips at the end of first-time setup.


@pytest.mark.parametrize("outcome", ["failed", "partial"])
def test_the_undelivered_notice_offers_the_hotkey(outcome, monkeypatch):
    told = []
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, msg: told.append(msg))
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a: None)
    monkeypatch.setattr(live, "paste_last_hotkey", "Ctrl+Alt+P")
    import tuxtalk.uinput_helper as uh
    monkeypatch.setattr(uh, "typing_blocked_reason", lambda: None)
    notices.report_undelivered(outcome, False)
    assert "Or press Ctrl+Alt+P to type it again where your cursor is." in told[0]


def test_no_hint_when_the_hotkey_is_off(monkeypatch):
    told = []
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, msg: told.append(msg))
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a: None)
    monkeypatch.setattr(live, "paste_last_hotkey", "")
    import tuxtalk.uinput_helper as uh
    monkeypatch.setattr(uh, "typing_blocked_reason", lambda: None)
    notices.report_undelivered("failed", False)
    assert "Ctrl+Alt+P" not in told[0]


def test_the_hint_text():
    assert history.paste_last_hint("Ctrl+Super+P") == "Ctrl+Super+P types the newest one again"
    assert history.paste_last_hint("") is None


def test_the_tray_tells_the_extension_the_key(monkeypatch):
    from tuxtalk import dbus_service
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(paste_last_hotkey="Ctrl+Super+P"))
    assert dbus_service.TuxTalkDBusService.GetPasteLastHotkey(object()) == "Ctrl+Super+P"


def test_the_tray_menu_line_really_appears(monkeypatch):
    """Runs the real menu code: a missing import here only fails when a user
    opens Recent Dictations."""
    import types
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from tuxtalk import tray_menu
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(paste_last_hotkey="Ctrl+Alt+P"))
    monkeypatch.setattr(history, "get_entries", lambda: ["Hello there."])
    fake = types.SimpleNamespace(_history_entry_submenu=lambda text, i: Gtk.Menu(),
                                 _clear_history=lambda: None)
    menu = Gtk.Menu()
    tray_menu.TrayMenu.fill_history(fake, menu)
    assert "Ctrl+Alt+P types the newest one again" in tree_text(menu)


def test_the_setup_tip_really_appears(monkeypatch):
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from tuxtalk import setup_complete_dialog
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(paste_last_hotkey="Ctrl+Alt+P"))
    seen = []

    def fake_run(dialog):
        seen.extend(tree_text(dialog))
        return Gtk.ResponseType.OK
    monkeypatch.setattr(Gtk.Dialog, "run", fake_run)
    setup_complete_dialog.show_setup_complete_dialog()
    assert any("Press Ctrl+Alt+P to type it again" in text for text in seen)


def test_a_hold_started_while_the_combo_is_pending_still_stops(keys, monkeypatch):
    """While the combo waits for its keys to be let go, the hold key's release
    must still get through, or an F8 recording started in that moment keeps
    running after F8 is let go."""
    stopped = []
    monkeypatch.setattr(recording, "start_recording",
                        lambda *a, **k: setattr(rs.state, "is_recording", True) or rs.state.current)
    monkeypatch.setattr(pipeline, "stop_recording",
                        lambda *a, **k: stopped.append(True) or setattr(rs.state, "is_recording", False))
    monkeypatch.setattr(Cfg, "smart_quotes", False, raising=False)
    monkeypatch.setattr(Cfg, "language", None, raising=False)
    monkeypatch.setattr(Cfg, "auto_period", True, raising=False)
    held = set()
    press_combo(held)
    send(CTRL, 0, held)
    send(ALT, 0, held)               # P still down: paste pending
    send(ecodes.KEY_F8, 1, held)
    send(ecodes.KEY_F8, 0, held)
    assert stopped, "the hold key's release was swallowed"


def test_a_right_ctrl_hold_key_let_go_while_the_combo_is_pending_still_stops(keys, monkeypatch):
    """Right Ctrl is a modifier, but as the hold key its release is the
    dictation's end, not part of Ctrl+Alt+P."""
    stopped = []
    monkeypatch.setattr(recording, "start_recording",
                        lambda *a, **k: setattr(rs.state, "is_recording", True) or rs.state.current)
    monkeypatch.setattr(pipeline, "stop_recording",
                        lambda *a, **k: stopped.append(True) or setattr(rs.state, "is_recording", False))
    for name, value in (("smart_quotes", False), ("language", None), ("auto_period", True)):
        monkeypatch.setattr(Cfg, name, value, raising=False)
    rctrl = ecodes.KEY_RIGHTCTRL
    held = set()
    press_combo(held)
    send(CTRL, 0, held)
    send(ALT, 0, held)               # P still down: paste pending
    for value in (1, 0):
        (held.add if value else held.discard)(rctrl)
        hotkeys._handle_key_event(KeyEvent(rctrl, value), rctrl, None, None, held, [], Cfg(),
                                  0, paste_last_combo=COMBO)
    assert stopped, "the Right Ctrl hold key's release was swallowed"
