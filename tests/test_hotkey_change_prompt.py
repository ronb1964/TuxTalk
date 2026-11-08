"""After a hotkey change, Preferences offers the hotkey test and says when
KDE already uses the key, as first run does, instead of leaving KDE's refusal
in the log only."""
import pytest

from tuxtalk.config import CTRL_SPACE
from tuxtalk.ctrl_space_clashes import GNOME, IBUS, KEYBOARD_OPTIONS
from tuxtalk.hotkey_labels import hands_free_label, hotkey_label
from tuxtalk.prefs_general import hotkey_change_message

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


def test_left_ctrl_space_needs_helper_1_15(monkeypatch):
    """1.14 reports the pair, but leaves a Left Ctrl kept down after a hold
    up for the desktop: Ctrl+Z after the dictation typed "z"."""
    from tuxtalk import secure_input
    assert not secure_input.hears_key("1.13", CTRL_SPACE)
    assert not secure_input.hears_key("1.14", CTRL_SPACE)
    assert secure_input.hears_key("1.15", CTRL_SPACE) and secure_input.hears_key(None, CTRL_SPACE)
    monkeypatch.setattr(secure_input, "running_version", lambda: "1.13")
    assert secure_input.unheard_keys([CTRL_SPACE, "F9"]) == [CTRL_SPACE]


def test_picking_left_ctrl_space_with_helper_1_13_offers_the_update():
    cfg = {**CFG, "hotkey": CTRL_SPACE}
    title, text, offer_test, clash = hotkey_change_message(cfg, {"hotkey"}, [],
                                                           unheard=[CTRL_SPACE])
    assert title == "Left Ctrl+Space needs a newer hotkey helper"
    assert "Left Ctrl+Space" in text and not offer_test and not clash


# --- Left Ctrl+Space: what it costs, and who else uses Ctrl+Space ------------------

def test_the_dropdown_says_what_left_ctrl_space_gives_up():
    assert hotkey_label(CTRL_SPACE) == ("Left Ctrl+Space (Ctrl+Space stops working in apps "
                                        "while dictation is on)")
    assert hotkey_label(CTRL_SPACE, password=True).endswith(
        "while dictation is on; asks for your password once)")


@pytest.mark.parametrize("unheard,asks", [([CTRL_SPACE], True), ([], False)])
def test_the_dropdown_mentions_the_password_only_with_an_older_helper(
        monkeypatch, unheard, asks):
    from tuxtalk import hotkey_labels, secure_input
    monkeypatch.setattr(secure_input, "unheard_keys", lambda keys: unheard)
    labels = dict(hotkey_labels.dropdown_entries())
    assert ("asks for your password once" in labels[CTRL_SPACE]) is asks
    assert labels["F8"] == "F8"
    assert ("asks for your password once" in hotkey_labels.layout_label(CTRL_SPACE)) is asks


def test_picking_left_ctrl_space_where_ibus_uses_it_says_so():
    cfg = {**CFG, "hotkey": CTRL_SPACE}
    title, text, _offer_test, clash = hotkey_change_message(cfg, {"hotkey"}, [],
                                                            ctrl_space=[IBUS])
    assert title == "IBus already uses Ctrl+Space" and clash
    assert text.startswith("IBus already uses Ctrl+Space to switch input methods, so pressing "
                           "Left Ctrl+Space may do that instead of dictating. Pick another key, "
                           "or give IBus another shortcut (IBus Preferences, Next input method).")


def test_the_keyboard_options_clash_says_what_they_do_to_left_ctrl():
    cfg = {**CFG, "hotkey": CTRL_SPACE}
    swapcaps = KEYBOARD_OPTIONS["ctrl:swapcaps"]
    title, text, _, _ = hotkey_change_message(cfg, {"hotkey"}, [], ctrl_space=[swapcaps])
    assert title == "Your keyboard options change Left Ctrl"
    assert text.startswith(
        "Your keyboard options make Left Ctrl act as Caps Lock, so pressing "
        "Left Ctrl+Space may do that instead of dictating. Pick another key, or change "
        "those keyboard options (Settings, Keyboard, Keyboard options).")
    assert swapcaps.log_line() == (
        "Your keyboard options make Left Ctrl act as Caps Lock; "
        "Left Ctrl+Space may do that instead of dictating")
    assert IBUS.log_line() == ("IBus also uses Ctrl+Space to switch input methods; "
                               "Left Ctrl+Space may do that instead of dictating")


def test_with_no_helper_running_the_dropdown_has_no_password_words(monkeypatch):
    from tuxtalk import hotkey_labels, secure_input
    monkeypatch.setattr(secure_input, "running_version", lambda: None)
    assert "password" not in dict(hotkey_labels.dropdown_entries())[CTRL_SPACE]


def test_kde_clash_comes_first_and_the_update_after_it():
    cfg = {**CFG, "hotkey": CTRL_SPACE}
    title, text, _, clash = hotkey_change_message(
        cfg, {"hotkey"}, ["Ctrl+Space"], unheard=[CTRL_SPACE], ctrl_space=[IBUS])
    assert title == "KDE already uses Left Ctrl+Space" and clash
    kde, ibus, update = (text.index(s) for s in (
        "KDE already uses", "IBus already uses", "older than this TuxTalk"))
    assert kde < ibus < update


def test_two_input_methods_are_both_named():
    cfg = {**CFG, "hotkey": CTRL_SPACE}
    title, text, _, _ = hotkey_change_message(cfg, {"hotkey"}, [], ctrl_space=[IBUS, GNOME])
    assert title == "IBus already uses Ctrl+Space"
    assert "IBus already uses" in text and "GNOME already uses" in text


def test_a_lookup_that_fails_is_logged_and_preferences_doesnt_wait_it_out(prefs_window,
                                                                         monkeypatch,
                                                                         caplog):
    import logging
    import time
    from tuxtalk import ctrl_space_clashes

    def broken():
        raise UnicodeDecodeError("utf-8", b"\xe3", 0, 1, "unexpected end of data")
    monkeypatch.setattr(ctrl_space_clashes, "clashes", broken)
    tab = prefs_window(hotkey=CTRL_SPACE).general_tab
    began = time.monotonic()
    with caplog.at_level(logging.INFO):
        assert tab._ctrl_space_clashes({CTRL_SPACE}) == []
    assert time.monotonic() - began < 2
    assert "Couldn't look for who else uses Ctrl+Space" in caplog.text


def test_after_apply_the_clashes_are_looked_for_only_when_left_ctrl_space_was_picked(
        prefs_window, monkeypatch):
    from gi.repository import Gtk
    from tuxtalk import ctrl_space_clashes, secure_input
    asked, looked = [], []
    monkeypatch.setattr(ctrl_space_clashes, "clashes", lambda: looked.append(1) or [IBUS])
    monkeypatch.setattr(secure_input, "unheard_keys", lambda keys: [])
    for hotkey in ("F7", CTRL_SPACE):
        tab = prefs_window(hotkey=hotkey).general_tab
        monkeypatch.setattr(tab, "_kde_refused", lambda since: None)
        monkeypatch.setattr(tab, "_ask_hotkey_question", lambda *question, update=False:
                            asked.append(question[0]) or Gtk.ResponseType.CANCEL)
        assert tab.after_hotkey_change({"hotkey"}, 0)
    assert looked == [1]
    assert asked == ["Try your new hotkey?", "IBus already uses Ctrl+Space"]
