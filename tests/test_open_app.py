"""Open apps by voice (TuxTalk Labs): a dictation that is only "open Firefox"
opens Firefox, or brings its window forward, and types nothing. The menu's
apps, the focusing and the starting are stand-ins: no test starts a real app
or touches the desktop."""
import logging
import types

import pytest

from conftest import esc, next_recording
from type_at_pause_rig import frames_of, speech, type_chunk
from tuxtalk import (app_finder, app_focus, app_launcher, config, open_app, pipeline,
                     recording, screen_message, service_tick)
from tuxtalk import live_settings as live
from tuxtalk import recording_state as rs
from tuxtalk.app_finder import AppEntry
from tuxtalk.beeps import CANCEL_BEEP, READY_BEEP

FIREFOX = AppEntry("firefox.desktop", "Firefox", "Firefox", "Web Browser", ("Internet",),
                   "firefox", "")
KWRITE = AppEntry("org.kde.kwrite.desktop", "KWrite", "KWrite", "Text Editor", (), "kwrite", "")
KONSOLE = AppEntry("org.kde.konsole.desktop", "Konsole", "Konsole", "Terminal", (), "konsole",
                   "")
KITTY = AppEntry("kitty.desktop", "Kitty", "Kitty", "Terminal", (), "kitty", "")


class Desktop:
    """What open_app sees of the desktop, and what it did there."""

    def __init__(self, chat):
        self.chat = chat
        self.apps = [FIREFOX, KWRITE]
        self.defaults = {"browser": "firefox.desktop"}
        self.running = False        # the app already has a window
        self.starts = True          # Gio says it started
        self.focused, self.launched = [], []
        self.beeps, self.floated, self.notified = [], [], []
        self.while_looking = None   # called while it looks for the app's window

    def focus_running(self, entry, timeout=1.0):
        if self.while_looking:
            self.while_looking()
        if self.running:
            self.focused.append(entry.app_id)
        return self.running

    def launch(self, entry):
        self.launched.append(entry.app_id)
        return self.starts


@pytest.fixture
def desktop(chat, monkeypatch):
    """open_apps on, two apps in the menu, Firefox the default browser."""
    d = Desktop(chat)
    monkeypatch.setattr(live, "open_apps", True)
    monkeypatch.setattr(app_finder, "installed_apps", lambda: list(d.apps))
    monkeypatch.setattr(app_finder, "default_apps", lambda: dict(d.defaults))
    monkeypatch.setattr(app_focus, "focus_running", d.focus_running)
    monkeypatch.setattr(app_launcher, "launch", d.launch)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda on, *tone: on and d.beeps.append(tone))
    monkeypatch.setattr(screen_message, "show_over_active_window",
                        lambda text, *a, **k: d.floated.append(text))
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body="", **k:
                        d.notified.append((title, body)))
    recording.cancel_requested.clear()
    yield d
    recording.cancel_requested.clear()


@pytest.fixture
def say(desktop, let_go):
    """say(words): a whole dictation of *words*, through the real pipeline."""
    def said(words):
        desktop.chat[1].append(words)
        return let_go()
    return said


# --- the setting ---------------------------------------------------------------------------

def test_the_setting_is_off_by_default():
    assert config.Settings().open_apps is False


def test_off_means_it_isnt_a_command(desktop, monkeypatch):
    monkeypatch.setattr(live, "open_apps", False)
    assert open_app.run("open Firefox", True, False) is None
    assert desktop.launched == [] and desktop.focused == []


def test_off_the_words_are_typed(desktop, monkeypatch, say):
    monkeypatch.setattr(live, "open_apps", False)
    say("open Firefox")
    assert [text for text, _ in desktop.chat[2]] == ["Open Firefox."]
    assert desktop.launched == []


def test_the_flatpak_treats_it_as_off(monkeypatch):
    """Its Labs tickbox is hidden there, so a backup from another install
    must not leave it on with no way to turn it off."""
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    monkeypatch.setattr(live, "open_apps", False)
    live.apply(config.Settings(open_apps=True))
    assert live.open_apps is False


def test_it_is_live_applied():
    assert "open_apps" in config.LIVE_APPLIED_KEYS


# --- opening -------------------------------------------------------------------------------

def test_open_starts_an_app_that_isnt_running(desktop):
    assert open_app.run("open Firefox", True, False) is True
    assert desktop.launched == ["firefox.desktop"] and desktop.beeps == [READY_BEEP]
    assert desktop.floated == []


def test_open_brings_a_running_app_forward_instead(desktop):
    desktop.running = True
    assert open_app.run("Open Firefox.", True, False) is True
    assert desktop.focused == ["firefox.desktop"] and desktop.launched == []
    assert desktop.beeps == [READY_BEEP]


def test_open_new_always_starts_another(desktop):
    desktop.running = True
    assert open_app.run("open new Firefox", True, False) is True
    assert desktop.launched == ["firefox.desktop"] and desktop.focused == []


def test_the_browser_opens_the_default_one(desktop):
    assert open_app.run("open the browser", True, False) is True
    assert desktop.launched == ["firefox.desktop"]


def test_launch_and_start_work_like_open(desktop):
    open_app.run("launch KWrite", True, False)
    open_app.run("start firefox", True, False)
    assert desktop.launched == ["org.kde.kwrite.desktop", "firefox.desktop"]


def test_it_works_in_a_terminal(desktop):
    """Unlike select and move, which can't work in one."""
    desktop.chat[0][0] = "org.kde.konsole"
    assert open_app.run("open Firefox", True, False) is True
    assert desktop.launched == ["firefox.desktop"]


def test_the_log_names_the_app_not_the_words(desktop, caplog):
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        open_app.run("open fire fox", True, False)
    assert "Voice command: open firefox.desktop" in caplog.text
    assert "fire fox" not in caplog.text


# --- nothing opened ------------------------------------------------------------------------

def test_no_match_says_what_it_heard_and_opens_nothing(desktop, caplog):
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert open_app.run("open fierfox zz", True, False) is False
    assert desktop.launched == [] and desktop.focused == []
    assert desktop.floated == ["No app called “fierfox zz”"]
    assert desktop.notified == [("No app called “fierfox zz”",
                                 "No app called “fierfox zz”. Nothing was opened.")]
    assert desktop.beeps == [CANCEL_BEEP]
    assert "no app matched" in caplog.text and "fierfox" not in caplog.text


def test_a_tie_names_both_and_opens_neither(desktop):
    desktop.apps = [KONSOLE, KITTY]
    desktop.defaults = {}
    assert open_app.run("open terminal", True, False) is False
    assert desktop.launched == []
    assert desktop.floated == ["Two apps fit: Konsole, Kitty. Say the full name."]
    assert desktop.notified[0][1] == ("Two apps fit: Konsole, Kitty. Say the full name. "
                                      "Nothing was opened.")
    assert desktop.beeps == [CANCEL_BEEP]


def test_a_tie_of_many_names_three(desktop):
    """"open libre" fits every LibreOffice app: the label stays short."""
    desktop.apps = [AppEntry(f"libreoffice-{n.lower()}.desktop", f"LibreOffice {n}",
                             f"LibreOffice {n}", "", (), "libreoffice", "")
                    for n in ("Writer", "Calc", "Impress", "Draw")]
    open_app.run("open libre", True, False)
    assert desktop.floated == ["Several apps fit: LibreOffice Writer, LibreOffice Calc, "
                               "LibreOffice Impress and more. Say the full name."]


def test_an_app_that_doesnt_start_says_so(desktop):
    desktop.starts = False
    assert open_app.run("open Firefox", True, False) is False
    assert desktop.floated == ["Couldn't open Firefox"]
    assert desktop.beeps == [CANCEL_BEEP]


# --- through the dictation pipeline --------------------------------------------------------

def test_an_open_command_types_nothing_and_sends_nothing(desktop, keyboard, say):
    """"Send each dictation automatically" is on in the chat: no Enter follows."""
    say("Open Firefox.")
    assert desktop.launched == ["firefox.desktop"]
    assert desktop.chat[2] == [] and keyboard.typed_keys() == []


def test_a_sentence_about_opening_is_typed(desktop, say):
    say("I opened Firefox yesterday")
    assert desktop.chat[2][0][0].startswith("I opened Firefox yesterday")
    assert desktop.launched == []


def test_literal_types_the_words(desktop, say):
    say("literal open Firefox")
    assert desktop.chat[2][0][0].startswith("Open Firefox")
    assert desktop.launched == [] and desktop.floated == []


def test_literal_on_its_own_sentence_is_kept(desktop, say):
    say("literal translations lose the meaning")
    assert desktop.chat[2][0][0].startswith("Literal translations")


def test_esc_after_letting_go_opens_nothing(desktop, say):
    """Esc while TuxTalk looks for the app's window: it isn't started."""
    desktop.while_looking = esc
    say("open Firefox")
    assert desktop.launched == []


def test_esc_before_the_command_is_found_brings_nothing_forward(desktop, say, monkeypatch):
    def apps_then_esc():
        esc()
        return list(desktop.apps)
    monkeypatch.setattr(app_finder, "installed_apps", apps_then_esc)
    desktop.running = True
    say("open Firefox")
    assert desktop.launched == [] and desktop.focused == []


# --- in a hands-free or Type at pause recording ----------------------------------------------

CFG = types.SimpleNamespace(beeps=True, notify=False)


def service_tick_runs():
    """One pass of the dictation service's tick, as its loop runs it."""
    service_tick.tick(CFG, service_tick.ServiceState(CFG))


@pytest.fixture
def recording_on(desktop, monkeypatch):
    """A Type at pause recording under way, its phrases typed as they come."""
    monkeypatch.setattr(recording, "indicator", None)
    monkeypatch.setattr(recording, "stop_stream_safely", lambda rec: None)
    monkeypatch.setattr(recording, "resume_media_players", lambda: None)
    monkeypatch.setattr("tuxtalk.tray_link.notify_recording_state", lambda *a: None)
    monkeypatch.setattr(rs.state, "is_recording", True)
    rec = next_recording()

    def phrase(*heard):
        """A phrase cut at a pause: what the engine hears of it, and of it
        with the phrase before when that held back its final mark."""
        desktop.chat[1].extend(heard)
        type_chunk(frames_of(speech(1.0)), "middle")
    return rec, phrase


def test_opening_mid_recording_ends_it(desktop, recording_on, keyboard):
    """Phrases typed before it stay; later words never land in whichever
    window has the focus while the app starts; no automatic Enter; the
    ready beep and not the cancel beep."""
    rec, phrase = recording_on
    phrase("see you at noon")
    phrase("open Firefox", "See you at noon. Open Firefox.")
    assert desktop.launched == ["firefox.desktop"]
    service_tick_runs()
    assert rs.state.is_recording is False and rec.message.cancelled
    typed = "".join(text for text, _ in desktop.chat[2])
    assert typed == "See you at noon. "      # its mark, held for the next phrase
    phrase("and this goes nowhere")
    assert "".join(text for text, _ in desktop.chat[2]) == typed
    assert keyboard.typed_keys() == []
    assert desktop.beeps == [READY_BEEP]


def test_no_match_mid_recording_keeps_it_going(desktop, recording_on):
    rec, phrase = recording_on
    phrase("open fierfox zz")
    service_tick_runs()
    assert rs.state.is_recording is True and not rec.message.cancelled


def test_a_whole_recording_has_no_recording_left_to_end(desktop):
    open_app.run("open Firefox", True, False)
    assert not recording.cancel_requested.is_set()


def test_a_phrase_of_an_older_recording_leaves_the_new_one_alone(desktop, recording_on):
    """The dictation worker was behind: "open Firefox" from the recording
    before is opened, but the one started since keeps recording."""
    rec, _phrase = recording_on
    older = rec.message
    newer = next_recording()
    desktop.chat[1].append("open Firefox")
    pipeline.transcribe_and_inject(frames_of(speech(1.0)), 16000, True, True, False, None, True,
                                   False, "type", chunk_role="middle", message=older)
    service_tick_runs()
    assert desktop.launched == ["firefox.desktop"]
    assert rs.state.is_recording is True and not newer.message.cancelled


# --- Preferences ---------------------------------------------------------------------------

OPEN_APPS_LABEL = "Open apps: say “open Firefox”"


def _labs_boxes(window):
    """TuxTalk Labs' tickboxes, top to bottom, by label."""
    from gi.repository import Gtk
    from gtk_text import below, find
    heading = find(window.window, Gtk.Label, "\U0001f9ea TuxTalk Labs")
    grid = heading.get_parent()
    rows = [w for w in below(heading)
            if isinstance(w, Gtk.CheckButton) and w.get_parent() is grid]
    rows.sort(key=lambda w: grid.child_get_property(w, "top-attach"))
    return {w.get_label(): w for w in rows}


def test_it_is_a_labs_tickbox_after_select_and_move(prefs_window):
    window = prefs_window()
    boxes = _labs_boxes(window)
    labels = list(boxes)
    nav = next(i for i, label in enumerate(labels) if label.startswith("Select and move:"))
    assert labels[nav + 1] == OPEN_APPS_LABEL
    box = boxes[OPEN_APPS_LABEL]
    assert not box.get_active()
    tip = box.get_tooltip_text()
    for said in ("“launch”", "“open new Firefox”", "“open the browser”",
                 "“literal open Firefox”", "hands-free"):
        assert said in tip
    box.set_active(True)
    assert window.config["open_apps"] is True


def test_the_tooltip_names_the_users_own_key(prefs_window):
    tip = _labs_boxes(prefs_window(hotkey="F6"))[OPEN_APPS_LABEL].get_tooltip_text()
    assert tip.startswith("Hold F6,")


def test_the_flatpak_has_no_tickbox(prefs_window, monkeypatch):
    from gi.repository import Gtk
    from gtk_text import widgets
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    window = prefs_window()
    assert not [w for w in widgets(window.window)
                if isinstance(w, Gtk.CheckButton) and w.get_label() == OPEN_APPS_LABEL]
