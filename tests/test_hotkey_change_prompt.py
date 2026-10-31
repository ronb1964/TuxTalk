"""After a hotkey change, Preferences offers the hotkey test and says when
KDE already uses the key, as first run does, instead of leaving KDE's refusal
in the log only."""
import pytest

pytest.importorskip("gi")
from tuxtalk.hotkey_labels import hands_free_label, hotkey_label  # noqa: E402
from tuxtalk.prefs_general import hotkey_change_message  # noqa: E402

CFG = {"hotkey": "F7", "toggle_hotkey": "F9", "voice_commands_hotkey": "Ctrl+Alt+V"}


def test_a_new_hold_key_offers_the_test_and_warns_about_laptop_fn_keys():
    _title, text, offer_test, _clash = hotkey_change_message(CFG, {"hotkey"}, [])
    assert offer_test and "hold-to-talk key is now F7" in text and "Fn" in text


def test_both_keys_are_named():
    _title, text, _, _clash = hotkey_change_message(CFG, {"hotkey", "toggle_hotkey"}, None)
    assert "F7" in text and "F9" in text


def test_a_key_kde_already_uses_is_said_on_screen():
    _title, text, offer_test, _clash = hotkey_change_message(CFG, {"hotkey"}, ["F7"])
    assert offer_test and "KDE already uses F7" in text


def test_only_the_voice_commands_key_changed_has_no_test_to_offer():
    assert hotkey_change_message(CFG, {"voice_commands_hotkey"}, []) is None
    _title, text, offer_test, _clash = hotkey_change_message(CFG, {"voice_commands_hotkey"}, ["Ctrl+Alt+V"])
    assert not offer_test and "KDE already uses Ctrl+Alt+V" in text


def test_no_hotkey_change_says_nothing():
    assert hotkey_change_message(CFG, {"model"}, ["F7"]) is None


def test_keys_apps_already_use_are_labelled_in_the_dropdowns():
    assert hotkey_label("F5") == "F5 (reloads the page in browsers)"
    assert hotkey_label("F8") == "F8"


def test_kdes_meta_spelling_maps_back_to_super():
    cfg = dict(CFG, voice_commands_hotkey="Ctrl+Super+V")
    _title, text, _, _clash = hotkey_change_message(cfg, {"voice_commands_hotkey"}, ["Ctrl+Meta+V"])
    assert "KDE already uses Ctrl+Super+V" in text


def test_the_question_says_where_to_test_later():
    _title, text, _, _clash = hotkey_change_message(CFG, {"hotkey"}, [])
    assert "Test Hotkeys, under the hotkeys on the General tab" in text


def test_an_app_kde_opens_with_the_key_is_named_first():
    """F6 opened the Emoji Selector as well as dictating (2026-10-13). The
    clash leads, title and all, for someone who reads only the top."""
    cfg = dict(CFG, hotkey="F6")
    title, text, offer_test, clash = hotkey_change_message(cfg, {"hotkey"}, ["F6"],
                                                           {"F6": "Emoji Selector"})
    assert title == "F6 also opens Emoji Selector" and clash
    assert text.startswith("KDE opens Emoji Selector with F6")
    assert "System Settings" in text and offer_test
    assert "one of its own shortcuts" not in text


def test_a_kde_shortcut_clash_leads_too():
    title, text, _, clash = hotkey_change_message(CFG, {"hotkey"}, ["F7"])
    assert title == "KDE already uses F7" and clash
    assert text.startswith("KDE already uses F7")


def test_several_clashes_get_one_title():
    cfg = dict(CFG, hotkey="F6")
    title, _, _, clash = hotkey_change_message(cfg, {"hotkey"}, ["F6", "F9"], {"F6": "Emoji Selector"})
    assert title == "KDE already uses some of these keys" and clash


def test_no_clash_asks_to_try_the_key():
    title, _, _, clash = hotkey_change_message(CFG, {"hotkey"}, [])
    assert title == "Try your new hotkey?" and not clash


def test_the_hands_free_option_names_the_toggle_key():
    assert hands_free_label("F9") == "Double-tap F9 for hands-free: stop after"
    assert hands_free_label("F10").startswith("Double-tap F10 ")
    assert hands_free_label("") == "Double-tap the toggle key for hands-free: stop after"


def test_right_ctrl_with_an_older_hotkey_helper_leads_with_the_update():
    """Helper 1.4 doesn't report Right Ctrl: the key would do nothing. Under
    "Try your new hotkey?" with Test Now, that was a line in the middle, and
    the test it offered couldn't work."""
    cfg = dict(CFG, hotkey="Right Ctrl")
    title, text, offer_test, _clash = hotkey_change_message(cfg, {"hotkey"}, [],
                                                            unheard=["Right Ctrl"])
    assert title == "Right Ctrl needs a newer hotkey helper" and not offer_test
    assert "Update Helper updates it now" in text
    title, text, offer_test, _clash = hotkey_change_message(cfg, {"hotkey"}, [])
    assert title == "Try your new hotkey?" and offer_test and "hotkey helper" not in text


def test_helpers_before_1_5_dont_hear_right_ctrl_and_alt():
    from tuxtalk import secure_input
    assert not secure_input.hears_key("1.4", "Right Ctrl")
    assert not secure_input.hears_key("1.4", "Right Alt")
    assert secure_input.hears_key("1.4", "F8") and secure_input.hears_key("1.4", "Scroll Lock")
    assert secure_input.hears_key("1.5", "Right Ctrl")
    assert secure_input.hears_key(None, "Right Ctrl")    # no helper: TuxTalk reads the keys


def test_only_a_helper_too_old_for_the_new_key_is_named(monkeypatch):
    """Preferences asks the running helper its version only for Right Ctrl
    or Right Alt, and names just the keys that helper won't report."""
    from tuxtalk import secure_input
    asked = []
    monkeypatch.setattr(secure_input, "running_version", lambda: asked.append(1) or "1.4")
    assert secure_input.unheard_keys(["F7", "F9"]) == [] and not asked
    assert secure_input.unheard_keys(["Right Ctrl", "F9"]) == ["Right Ctrl"]


# --- Update Helper, in the question after OK or Apply ---------------------------

@pytest.fixture
def right_ctrl_question(prefs_window, monkeypatch):
    """Preferences with Right Ctrl just applied and a hotkey helper too old
    for it: (the General tab, the steps taken, the warnings shown)."""
    from gi.repository import Gtk
    from tuxtalk import prefs_general, secure_input
    win = prefs_window(hotkey="Right Ctrl")
    tab, steps, warnings = win.general_tab, [], []
    monkeypatch.setattr(secure_input, "unheard_keys", lambda keys: ["Right Ctrl"])
    monkeypatch.setattr(tab, "_kde_refused", lambda since: None)
    monkeypatch.setattr(tab, "_ask_hotkey_question", lambda *question, update=False:
                        steps.append((question[0], update)) or Gtk.ResponseType.OK)
    monkeypatch.setattr(win, "restart_service",
                        lambda on_failed=None: steps.append("restarted") or True)
    monkeypatch.setattr(prefs_general, "run_hotkey_test", lambda parent, cfg: steps.append("test"))
    monkeypatch.setattr(prefs_general, "show_message",
                        lambda parent, kind, title, text: warnings.append((title, text)))
    return tab, steps, warnings


def test_update_helper_updates_it_and_then_offers_the_test(right_ctrl_question, monkeypatch):
    """No dictation restart: it would throw away a dictation under way."""
    from tuxtalk import secure_input
    tab, steps, warnings = right_ctrl_question
    monkeypatch.setattr(secure_input, "install_with_pkexec",
                        lambda: steps.append("updated") or (True, ""))
    assert tab.after_hotkey_change({"hotkey"}, 0)
    assert steps == [("Right Ctrl needs a newer hotkey helper", True), "updated", "test"]
    assert warnings == []


def test_a_cancelled_update_says_where_to_do_it_later_and_offers_no_test(
        right_ctrl_question, monkeypatch):
    from tuxtalk import secure_input
    tab, steps, warnings = right_ctrl_question
    monkeypatch.setattr(secure_input, "install_with_pkexec",
                        lambda: steps.append("updated") or (False, "Setup cancelled."))
    assert tab.after_hotkey_change({"hotkey"}, 0)
    assert steps == [("Right Ctrl needs a newer hotkey helper", True), "updated"]
    [(title, text)] = warnings
    assert title == "The hotkey helper wasn't updated" and "Show system check" in text


def test_the_question_s_main_button_is_update_helper(prefs_window, monkeypatch):
    from gi.repository import Gtk
    from tuxtalk import prefs_general
    buttons = []

    class Dialog:
        def __getattr__(self, name):
            return lambda *a, **k: None

        def add_button(self, label, response):
            buttons.append(label)

        def run(self):
            return Gtk.ResponseType.CANCEL
    monkeypatch.setattr(prefs_general, "message_dialog", lambda **k: Dialog())
    monkeypatch.setattr("tuxtalk.ui_style.message_dialog", lambda **k: Dialog())
    tab = prefs_window().general_tab
    tab._ask_hotkey_question("Right Ctrl needs a newer hotkey helper", "", False, False,
                             update=True)
    assert buttons == ["Not Now", "Update Helper"]


def test_an_update_that_outlasts_the_wait_says_how_it_ended(right_ctrl_question, monkeypatch):
    """Past pkexec's time-out the script can still run as root: the window
    stopped waiting, and nobody heard that it finished after all."""
    from tuxtalk import update_offers
    tab, steps, warnings = right_ctrl_question
    finish, told = [], []
    monkeypatch.setattr(update_offers, "update_helper", finish.append)
    monkeypatch.setattr(tab, "_while_drawn", lambda finished, seconds: False)
    monkeypatch.setattr(update_offers, "say_helper_updated",
                        lambda ok, message, source: told.append((ok, source)))
    assert tab.after_hotkey_change({"hotkey"}, 0)
    [(title, text)] = warnings
    # No notification is promised: after OK the window's process ends, and
    # with it the wait for one.
    assert title == "The update is taking a while" and "notification" not in text
    finish[0](True, "")                         # the update ends after all
    assert told == [(True, "from the hotkey question")] and "test" not in steps


def test_the_log_says_where_a_helper_update_was_asked_for(monkeypatch, caplog):
    """An update from the hotkey question was logged as the offer at start's."""
    import logging
    from tuxtalk import notify, update_offers
    monkeypatch.setattr(notify, "notify", lambda *a, **k: None)
    with caplog.at_level(logging.INFO):
        update_offers.say_helper_updated(True, "", "from the hotkey question")
        update_offers.say_helper_updated(True, "")
    assert [r.getMessage() for r in caplog.records if "Updated" in r.getMessage()] == [
        "Updated the hotkey helper from the hotkey question",
        "Updated the hotkey helper from the offer at start"]
