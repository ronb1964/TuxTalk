"""Open apps by voice (TuxTalk Labs): a dictation that is only "open Firefox"
opens Firefox, or brings its window forward, and types nothing. The menu's
apps, the focusing and the starting are stand-ins: no test starts a real app
or touches the desktop."""
import logging

import pytest

from conftest import QueuedWorker, esc, next_recording
from type_at_pause_rig import frames_of, service_tick_runs, speech, type_chunk
from tuxtalk import (app_finder, app_focus, app_launcher, config, dictation_worker,
                     pipeline, recording, screen_message, voice_actions)
from tuxtalk import live_settings as live
from tuxtalk import recording_state as rs
from tuxtalk.app_finder import AppEntry
from tuxtalk.beeps import CANCEL_BEEP, READY_BEEP

FIREFOX = AppEntry("firefox.desktop", "Firefox", "Firefox", "Web Browser", "firefox", "")
KWRITE = AppEntry("org.kde.kwrite.desktop", "KWrite", "KWrite", "Text Editor", "kwrite", "")
KONSOLE = AppEntry("org.kde.konsole.desktop", "Konsole", "Konsole", "Terminal", "konsole", "")
KITTY = AppEntry("kitty.desktop", "Kitty", "Kitty", "Terminal", "kitty", "")
# Before the desktop fixture stands in for it.
READ_THE_MENU = app_finder.installed_apps


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
        self.exact = []             # each look's exact= (by the menu entry only)

    def focus_running(self, entry, timeout=1.0, exact=False):
        self.exact.append(exact)
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
    return d


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
    assert voice_actions.run("open Firefox", True) is None
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
    assert voice_actions.run("open Firefox", True) is True
    assert desktop.launched == ["firefox.desktop"] and desktop.beeps == [READY_BEEP]
    assert desktop.floated == []


def test_open_brings_a_running_app_forward_instead(desktop):
    desktop.running = True
    assert voice_actions.run("Open Firefox.", True) is True
    assert desktop.focused == ["firefox.desktop"] and desktop.launched == []
    assert desktop.beeps == [READY_BEEP]


FIREFOX_FLATPAK = AppEntry("org.mozilla.firefox.desktop", "Firefox", "Firefox", "Web Browser",
                           "firefox", "flatpak")


def test_with_two_copies_a_window_counts_only_by_its_menu_entry(desktop):
    """Both copies' windows have the class "firefox": the one asked for is
    brought forward only if its own window can be told apart."""
    voice_actions.run("open Firefox", True)
    desktop.apps.append(FIREFOX_FLATPAK)
    voice_actions.run("open Firefox", True)
    voice_actions.run("open Firefox Flatpak", True)
    assert desktop.exact == [False, True, True]
    assert desktop.launched[-1] == "org.mozilla.firefox.desktop"


def test_open_new_always_starts_another(desktop):
    desktop.running = True
    assert voice_actions.run("open new Firefox", True) is True
    assert desktop.launched == ["firefox.desktop"] and desktop.focused == []


def test_the_browser_opens_the_default_one(desktop):
    assert voice_actions.run("open the browser", True) is True
    assert desktop.launched == ["firefox.desktop"]


def test_launch_and_start_work_like_open(desktop):
    voice_actions.run("launch KWrite", True)
    voice_actions.run("start firefox", True)
    assert desktop.launched == ["org.kde.kwrite.desktop", "firefox.desktop"]


def test_it_works_in_a_terminal(desktop):
    """Unlike select and move, which can't work in one."""
    desktop.chat[0][0] = "org.kde.konsole"
    assert voice_actions.run("open Firefox", True) is True
    assert desktop.launched == ["firefox.desktop"]


def test_the_log_names_the_app_not_the_words(desktop, caplog):
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        voice_actions.run("open fire fox", True)
    assert "Voice command: open firefox.desktop" in caplog.text
    assert "fire fox" not in caplog.text


# --- nothing opened ------------------------------------------------------------------------

def test_no_match_is_typed_and_opens_nothing(desktop, caplog):
    """"Start over." names no app: lost, it would be the worst outcome, and a
    typed "Open fierfox zz." is easy to undo."""
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert voice_actions.run("open fierfox zz", True) is None
    assert desktop.launched == [] and desktop.focused == []
    assert desktop.floated == [] and desktop.notified == [] and desktop.beeps == []
    assert "Open command: no app matched; typed as words" in caplog.text
    assert "fierfox" not in caplog.text


def test_a_tie_names_both_and_opens_neither(desktop):
    desktop.apps = [KONSOLE, KITTY]
    desktop.defaults = {}
    assert voice_actions.run("open terminal", True) is False
    assert desktop.launched == []
    assert desktop.floated == ["Two apps fit: Konsole, Kitty. Say the full name."]
    assert desktop.notified[0][1] == ("Two apps fit: Konsole, Kitty. Say the full name. "
                                      "Nothing was opened.")
    assert desktop.beeps == [CANCEL_BEEP]


def test_a_tie_of_many_names_three(desktop):
    """"open libre" fits every LibreOffice app: the label stays short."""
    desktop.apps = [AppEntry(f"libreoffice-{n.lower()}.desktop", f"LibreOffice {n}",
                             f"LibreOffice {n}", "", "libreoffice", "")
                    for n in ("Writer", "Calc", "Impress", "Draw")]
    voice_actions.run("open libre", True)
    assert desktop.floated == ["Several apps fit: LibreOffice Writer, LibreOffice Calc, "
                               "LibreOffice Impress and more. Say the full name."]


def test_an_app_that_doesnt_start_says_so(desktop):
    desktop.starts = False
    assert voice_actions.run("open Firefox", True) is False
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


@pytest.mark.parametrize("said, typed", [("Start over.", "Start over."),
                                         ("start with the introduction",
                                          "Start with the introduction"),
                                         ("open fierfox zz", "Open fierfox zz")])
def test_a_short_sentence_naming_no_app_is_typed(desktop, say, said, typed):
    desktop.apps.append(AppEntry("overgrive.desktop", "Overgrive", "Overgrive", "",
                                 "overgrive", ""))
    say(said)
    assert desktop.chat[2][0][0].startswith(typed)
    assert desktop.launched == [] and desktop.floated == [] and desktop.beeps == []


def test_spoken_quotation_is_typed_not_opened(desktop, say):
    """"open quote" is the curly quote's own command."""
    say("open quote hello close quote")
    assert desktop.chat[2][0][0].startswith('"Hello')
    assert desktop.launched == [] and desktop.floated == []


def test_a_word_the_menu_entry_only_lists_as_a_keyword_opens_nothing(desktop, say, tmp_path,
                                                                       monkeypatch):
    """Konsole's Keywords include "script": "start the script" is a sentence."""
    from gi.repository import Gio
    path = tmp_path / "org.kde.konsole.desktop"
    path.write_text("[Desktop Entry]\nType=Application\nName=Konsole\nExec=true\n"
                    "GenericName=Terminal\nKeywords=terminal;shell;script;command;\n")
    monkeypatch.setattr(app_finder, "_desktop_apps",
                        lambda: [Gio.DesktopAppInfo.new_from_filename(str(path))])
    monkeypatch.setattr(app_finder, "installed_apps", READ_THE_MENU)
    say("start the script")
    assert desktop.launched == []
    assert desktop.chat[2][0][0].startswith("Start the script")


def test_literal_types_the_words(desktop, say):
    say("literal open Firefox")
    assert desktop.chat[2][0][0].startswith("Open Firefox")
    assert desktop.launched == [] and desktop.floated == []


@pytest.mark.parametrize("said", ["The word open means unlocked",
                                  "The words open source are confusing"])
def test_the_word_before_open_is_typed_whole(desktop, say, said):
    """Only "literal" is the escape: "the word" starts sentences."""
    say(said)
    assert desktop.chat[2][0][0].startswith(said)


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
    assert desktop.beeps == [READY_BEEP] and desktop.notified == []


def test_no_match_mid_recording_is_typed_and_keeps_it_going(desktop, recording_on):
    rec, phrase = recording_on
    phrase("open fierfox zz")
    service_tick_runs()
    assert rs.state.is_recording is True and not rec.message.cancelled
    assert "".join(text for text, _ in desktop.chat[2]).startswith("Open fierfox zz")


def test_a_whole_recording_has_no_recording_left_to_end(desktop):
    message = rs.state.current.message
    voice_actions.run("open Firefox", True)
    assert not message.cancelled


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
    assert desktop.launched == ["firefox.desktop"] and older.cancelled
    assert rs.state.is_recording is True and not newer.message.cancelled


def test_the_rest_queued_as_the_key_came_up_is_dropped_too(desktop, recording_on, keyboard,
                                                           monkeypatch):
    """The key came up after "open Firefox" was cut at a pause, while the
    dictation worker was still busy: the rest it queued at the stop isn't
    typed, nor sent with "Send each dictation automatically", into whichever
    window has the focus while Firefox starts."""
    rec, _phrase = recording_on
    worker = QueuedWorker()
    monkeypatch.setattr(dictation_worker, "_instance", worker)
    desktop.chat[1].extend(["open Firefox", "thanks"])
    dictation_worker.submit(lambda: type_chunk(frames_of(speech(1.0)), "middle"))
    rec.chunks_submitted = 1
    rec.frames = frames_of(speech(1.0))
    pipeline.stop_recording(False, False, False)
    service_tick_runs()
    worker.run()
    service_tick_runs()
    assert desktop.launched == ["firefox.desktop"]
    assert desktop.chat[2] == [] and keyboard.typed_keys() == []


def test_a_recording_started_in_the_same_tick_keeps_going(desktop, recording_on, monkeypatch):
    """"open Firefox" was cut at a pause, the key came up, and the next press
    is taken in the same pass of the tick that ends recordings."""
    _rec, phrase = recording_on
    phrase("open Firefox")
    monkeypatch.setattr(rs.state, "is_recording", False)
    started = []

    def start(*args, **kwargs):
        started.append(next_recording())
        rs.state.is_recording = True
    monkeypatch.setattr(recording, "start_recording", start)
    recording.start_requested.set()
    service_tick_runs()
    assert len(started) == 1 and rs.state.is_recording is True
    assert not started[0].message.cancelled


# --- Preferences ---------------------------------------------------------------------------

OPEN_APPS_LABEL = ("Apps, windows, keys and the mouse by voice: “open Firefox”, “new tab”, "
                   "“mouse grid”")


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
                 "“literal open Firefox”", "hands-free", "typed as usual"):
        assert said in tip
    box.set_active(True)
    assert window.config["open_apps"] is True


def test_the_labs_box_names_the_mouse(prefs_window):
    box = _labs_boxes(prefs_window())[OPEN_APPS_LABEL]
    assert "and the mouse by voice" in box.get_label()
    tip = box.get_tooltip_text()
    assert "“mouse grid”, then a number and “click”" in tip
    assert "“left 3”, “up”" in tip and "move the dot" in tip
    assert "go to youtube.com" in tip


def test_the_tooltip_names_the_users_own_key(prefs_window):
    tip = _labs_boxes(prefs_window(hotkey="F6"))[OPEN_APPS_LABEL].get_tooltip_text()
    assert tip.startswith("Hold F6,")


def test_the_tooltip_lists_every_kind_of_command_with_your_own_key(prefs_window):
    tip = _labs_boxes(prefs_window(hotkey="F6"))[OPEN_APPS_LABEL].get_tooltip_text()
    for said in ("open Firefox", "close Firefox", "switch to Firefox", "minimize window",
                 "new tab", "scroll down", "go to youtube.com", "web search", "literal",
                 "copy the selection", "paste it", "save the file", "reload the page",
                 "press escape", "snap left", "center window", "show desktop",
                 "switch to desktop two", "move window to the other screen",
                 "minimize Firefox", "volume up", "pause music", "next track", "lock screen",
                 "take a screenshot", "what can I say"):
        assert said in tip, said
    assert "\u201ccopy\u201d" not in tip and "\u201cpaste\u201d" not in tip
    assert "F6" in tip and "F8" not in tip


def test_the_flatpak_has_no_tickbox(prefs_window, monkeypatch):
    from gi.repository import Gtk
    from gtk_text import widgets
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    window = prefs_window()
    assert not [w for w in widgets(window.window)
                if isinstance(w, Gtk.CheckButton) and w.get_label() == OPEN_APPS_LABEL]
