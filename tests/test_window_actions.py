"""Window voice commands (TuxTalk Labs): "close Firefox", "switch to
Firefox", "minimize window" said alone do that and type nothing, on KDE,
GNOME and X11; elsewhere a note says it can't be done. A command naming no
app ("Close the door.") is typed as dictation.

The desktops are stand-ins (desktop_fakes.py, or a recording act()): nothing
here touches the real one, starts an app or closes a window.
"""
import logging
import types

import dbus
import pytest

from conftest import CHAT_WINDOW, esc
from desktop_fakes import FakeShell, set_desktop
from type_at_pause_rig import service_tick_runs
from tuxtalk import (app_finder, app_focus, app_launcher, app_match, command_phrases,
                     kwin_windows, voice_actions, window_actions, x11_windows)
from tuxtalk import recording_state as rs
from tuxtalk.app_commands import OpenRequest
from tuxtalk.app_finder import AppEntry
from tuxtalk.beeps import READY_BEEP
from tuxtalk.command_phrases import WindowRequest

FIREFOX = AppEntry("firefox.desktop", "Firefox", "Firefox", "Web Browser", "firefox", "")
KWRITE = AppEntry("org.kde.kwrite.desktop", "KWrite", "KWrite", "Text Editor", "kwrite", "")
KONSOLE = AppEntry("org.kde.konsole.desktop", "Konsole", "Konsole", "Terminal", "konsole", "")
KITTY = AppEntry("kitty.desktop", "Kitty", "Kitty", "Terminal", "kitty", "")
# Names that start like words of a sentence: "the file", "all".
FILELIGHT = AppEntry("org.kde.filelight.desktop", "Filelight", "Filelight", "Disk Usage Analyzer",
                     "filelight", "")
ALLIGATOR = AppEntry("org.kde.alligator.desktop", "Alligator", "Alligator", "Feed Reader",
                     "alligator", "")
# Named as their menus name them, with programs that aren't the name said.
CHROME = AppEntry("google-chrome.desktop", "Google Chrome", "Google Chrome", "Web Browser",
                  "google-chrome-stable", "")
BRAVE = AppEntry("brave-browser.desktop", "Brave Web Browser", "Brave Web Browser",
                 "Web Browser", "brave-browser-stable", "")
EDGE = AppEntry("microsoft-edge.desktop", "Microsoft Edge", "Microsoft Edge", "Web Browser",
                "microsoft-edge-stable", "")
WRITER = AppEntry("libreoffice-writer.desktop", "LibreOffice Writer", "LibreOffice Writer",
                  "Word Processor", "libreoffice", "")
WORD_NAMED = [FIREFOX, FILELIGHT, ALLIGATOR, CHROME, BRAVE, EDGE, WRITER]
# A menu entry whose id leaves nothing to know its windows by.
NAMELESS = AppEntry(".desktop", "Ghostwriter", "Ghostwriter", "Text Editor", "ghostwriter", "")


class Asked(list):
    """What a stand-in was asked or shown; *answer*: whether it found a window."""
    answer = True


@pytest.fixture
def apps(monkeypatch):
    """Firefox and KWrite in the menu, and no default apps. Returns the
    list, which a test may change."""
    entries = [FIREFOX, KWRITE]
    monkeypatch.setattr(app_finder, "installed_apps", lambda: list(entries))
    monkeypatch.setattr(app_finder, "default_apps", lambda: {})
    return entries


@pytest.fixture
def kwin_calls(monkeypatch):
    """kwin_windows.act's stand-in: (action, desktop file, classes, every)."""
    calls = Asked()

    def act(action, desktop_file, classes, every, timeout):
        calls.append((action, desktop_file, frozenset(classes), every))
        return calls.answer
    monkeypatch.setattr(kwin_windows, "act", act)
    return calls


@pytest.fixture
def x11_calls(monkeypatch):
    """x11_windows.act's stand-in: (action, desktop file, names, every)."""
    calls = Asked()

    def act(action, desktop_file, names, every):
        calls.append((action, desktop_file, frozenset(names), every))
        return calls.answer
    monkeypatch.setattr(x11_windows, "act", act)
    return calls


@pytest.fixture
def beeps_played(chat, monkeypatch):
    """The tones played, after the chat fixture has silenced them."""
    played = []
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda on, *tone, **k: on and played.append(tone))
    return played


@pytest.fixture
def focused(monkeypatch):
    """app_focus.bring_forward's stand-in: the app ids brought forward, and
    starting an app fails the test."""
    brought = Asked()

    def bring_forward(entry, timeout, exact):
        if brought.answer:
            brought.append(entry.app_id)
        return brought.answer

    def launch(entry):
        pytest.fail(f"switch to started {entry.app_id}")
    monkeypatch.setattr(app_focus, "bring_forward", bring_forward)
    monkeypatch.setattr(app_launcher, "launch", launch)
    return brought


def close(app="", every=False):
    return WindowRequest("close", OpenRequest(app) if app else None, every)


def switch(app):
    return WindowRequest("switch", OpenRequest(app))


# --- KDE ------------------------------------------------------------------------------------


def test_close_firefox_on_kde_asks_kwin_with_the_apps_window_names(kde, apps, kwin_calls,
                                                                    actions_on, beeps_played):
    assert window_actions.run(close("firefox"), CHAT_WINDOW, True) is True
    assert kwin_calls == [("close", "firefox", frozenset({"firefox"}), False)]
    assert actions_on == [] and beeps_played == [READY_BEEP]


def test_close_firefox_where_the_menu_names_it_firefox_web_browser(kde, apps, kwin_calls,
                                                                     actions_on):
    """Mint's menu: the program's name is a whole name too."""
    apps[:] = [AppEntry("firefox.desktop", "Firefox Web Browser", "Firefox Web Browser",
                        "Web Browser", "firefox", "")]
    assert window_actions.run(close("firefox"), CHAT_WINDOW, False) is True
    assert kwin_calls == [("close", "firefox", frozenset({"firefox"}), False)]


def test_close_all_firefox_asks_for_every_window(kde, apps, kwin_calls, actions_on):
    assert window_actions.run(close("firefox", every=True), CHAT_WINDOW, False) is True
    assert kwin_calls == [("close", "firefox", frozenset({"firefox"}), True)]


def test_close_window_on_kde_asks_for_the_focused_one(kde, kwin_calls, actions_on):
    assert window_actions.run(close(), CHAT_WINDOW, False) is True
    assert kwin_calls == [("close", "", frozenset(), False)]


def test_close_an_app_with_no_window_says_so(kde, apps, kwin_calls, actions_on):
    """Firefox in the menu but not open: nothing is closed, and a note says why."""
    kwin_calls.answer = False
    assert window_actions.run(close("firefox"), CHAT_WINDOW, False) is False
    assert actions_on == ["Firefox has no open window"]


def test_close_window_with_no_focused_window_says_so(kde, kwin_calls, actions_on):
    kwin_calls.answer = False
    assert window_actions.run(close(), CHAT_WINDOW, False) is False
    assert actions_on == ["Couldn't tell which window"]


def test_a_kwin_error_says_it_cant_and_is_logged(kde, apps, actions_on, monkeypatch, caplog):
    def refused(*_args):
        raise RuntimeError("KWin refused the script")
    monkeypatch.setattr(kwin_windows, "act", refused)
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert window_actions.run(WindowRequest("maximize"), CHAT_WINDOW, False) is False
    assert actions_on == ["Can't minimize or maximize here"]
    assert "maximize" in caplog.text and "KWin refused the script" in caplog.text


@pytest.mark.parametrize("error", [TimeoutError("KWin didn't answer in time"),
                                   dbus.exceptions.DBusException(
                                       "Did not receive a reply",
                                       name="org.freedesktop.DBus.Error.NoReply")])
def test_a_slow_kwin_is_told_as_one_and_is_logged(kde, apps, actions_on, monkeypatch, caplog,
                                                  error):
    def slow(*_args):
        raise error
    monkeypatch.setattr(kwin_windows, "act", slow)
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert window_actions.run(close(), CHAT_WINDOW, False) is False
    assert actions_on == ["The desktop didn't answer"]
    assert "Voice command close not done" in caplog.text


def test_a_slow_gnome_shell_is_told_as_one(gnome, apps, actions_on, monkeypatch):
    def slow(*_args):
        raise dbus.exceptions.DBusException("Did not receive a reply",
                                            name="org.freedesktop.DBus.Error.NoReply")
    monkeypatch.setattr(app_focus, "call_extension", slow)
    assert window_actions.run(switch("firefox"), CHAT_WINDOW, False) is False
    assert actions_on == ["The desktop didn't answer"]


def test_an_x11_error_says_it_cant_and_is_logged(x11_session, apps, actions_on, monkeypatch,
                                                 caplog):
    def refused(*_args):
        raise OSError("the X server refused the request (error 3)")
    monkeypatch.setattr(x11_windows, "act", refused)
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert window_actions.run(close("firefox"), CHAT_WINDOW, False) is False
    assert actions_on == ["Can't close windows on this desktop"]
    assert "close" in caplog.text and "error 3" in caplog.text


def test_the_log_names_the_app_not_the_words(kde, apps, kwin_calls, actions_on, caplog):
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        window_actions.run(close("fire fox"), CHAT_WINDOW, False)
    assert "Voice command: close firefox.desktop" in caplog.text
    assert "fire fox" not in caplog.text


def test_the_log_says_the_focused_window(kde, kwin_calls, actions_on, caplog):
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        window_actions.run(WindowRequest("minimize"), CHAT_WINDOW, False)
    assert "Voice command: minimize the focused window" in caplog.text


def test_a_tie_names_both_and_closes_neither(kde, apps, kwin_calls, actions_on):
    apps[:] = [KONSOLE, KITTY]
    assert window_actions.run(close("terminal"), CHAT_WINDOW, False) is False
    assert kwin_calls == []
    assert actions_on == ["Two apps fit: Konsole, Kitty. Say the full name."]


@pytest.mark.parametrize("desktop, session", [("KDE", "wayland"), ("GNOME", "wayland"),
                                              ("XFCE", "x11")])
def test_a_named_app_with_nothing_to_know_it_by_never_closes_the_focused_window(
        apps, actions_on, nobody_is_asked, monkeypatch, desktop, session):
    """An empty identity means "the focused window" to every desktop: an app
    whose entry gives none must not close whatever the user is in."""
    set_desktop(monkeypatch, desktop, session)
    apps.append(NAMELESS)
    assert window_actions.run(close("ghostwriter"), CHAT_WINDOW, False) is False
    assert actions_on == ["Ghostwriter has no open window"]


# --- GNOME ----------------------------------------------------------------------------------


def test_close_window_on_gnome_asks_the_extension_for_the_focused_one(gnome, fake_extension,
                                                                      actions_on):
    assert window_actions.run(close(), CHAT_WINDOW, False) is True
    assert fake_extension.window_calls == [("close", "", False)]


def test_close_all_firefox_on_gnome_names_its_menu_entry(gnome, apps, fake_extension,
                                                         actions_on):
    assert window_actions.run(close("firefox", every=True), CHAT_WINDOW, False) is True
    assert fake_extension.window_calls == [("close", "firefox.desktop", True)]


def test_gnome_with_no_such_window_says_so(gnome, apps, fake_extension, actions_on):
    fake_extension.window_answer = False
    assert window_actions.run(close("firefox"), CHAT_WINDOW, False) is False
    assert actions_on == ["Firefox has no open window"]


@pytest.mark.parametrize("request_, said", [
    (WindowRequest("close"), "Can't close windows on this desktop"),
    (WindowRequest("minimize"), "Can't minimize or maximize here")])
def test_gnome_without_the_extension_says_it_cant(gnome, actions_on, request_, said):
    """No TuxTalk extension (Ubuntu's GNOME with the tray instead): GNOME
    Shell answers UnknownMethod, as for an old extension, but there is
    nothing to log out and back in for."""
    shell = FakeShell(extension=False)
    try:
        assert window_actions.run(request_, CHAT_WINDOW, False) is False
        assert actions_on == [said]
    finally:
        shell.stop()


def test_gnome_shell_not_on_the_bus_says_it_cant(gnome, actions_on):
    assert window_actions.run(close(), CHAT_WINDOW, False) is False
    assert actions_on == ["Can't close windows on this desktop"]


@pytest.mark.parametrize("request_", [WindowRequest("minimize"), switch("firefox")])
def test_gnome_extension_before_logout_says_to_log_out(gnome, apps, actions_on, request_):
    """GNOME Shell runs the extension it loaded at login until the user logs
    out: 0.21's serves no object at all, but GNOME Shell says it's enabled."""
    shell = FakeShell(extension="old")
    try:
        assert window_actions.run(request_, CHAT_WINDOW, False) is False
        assert actions_on == ["Log out and back in for this"]
    finally:
        shell.stop()


# --- X11 ------------------------------------------------------------------------------------


def test_minimize_on_x11_uses_the_window_manager(x11_session, x11_calls, actions_on):
    assert window_actions.run(WindowRequest("minimize"), CHAT_WINDOW, False) is True
    assert x11_calls == [("minimize", "", frozenset(), False)]


def test_close_firefox_on_x11_reaches_the_window_manager(window_manager, apps, actions_on):
    firefox = window_manager.window("Navigator", "firefox")
    window_manager.stack([window_manager.window("mousepad", "Mousepad"), firefox])
    assert window_actions.run(close("firefox"), CHAT_WINDOW, False) is True
    assert window_manager.requests(b"_NET_CLOSE_WINDOW") == [(firefox, (0, 2, 0, 0, 0))]


# --- everywhere else ------------------------------------------------------------------------


def test_cosmic_closes_nothing_and_says_so(cosmic, nobody_is_asked, actions_on):
    assert window_actions.run(close(), CHAT_WINDOW, False) is False
    assert actions_on == ["Can't close windows on this desktop"]


def test_cosmic_minimizes_nothing_and_says_so(cosmic, nobody_is_asked, actions_on):
    assert window_actions.run(WindowRequest("maximize"), CHAT_WINDOW, False) is False
    assert actions_on == ["Can't minimize or maximize here"]


# --- switch to ------------------------------------------------------------------------------


def test_switch_to_brings_it_forward_and_never_starts_it(kde, apps, focused, actions_on,
                                                         beeps_played):
    assert window_actions.run(switch("firefox"), CHAT_WINDOW, True) is True
    assert focused == ["firefox.desktop"] and beeps_played == [READY_BEEP]


def test_switch_to_an_app_not_running_says_so(kde, apps, focused, actions_on):
    focused.answer = False
    assert window_actions.run(switch("firefox"), CHAT_WINDOW, False) is False
    assert actions_on == ["Firefox isn't open"]


def test_switch_to_on_cosmic_says_it_cant(cosmic, apps, nobody_is_asked, actions_on,
                                          monkeypatch):
    monkeypatch.setattr(app_launcher, "launch", lambda entry: pytest.fail("started"))
    assert window_actions.run(switch("firefox"), CHAT_WINDOW, False) is False
    assert actions_on == ["Can't switch windows on this desktop"]


def test_switch_to_on_gnome_asks_the_extension(gnome, apps, fake_extension, actions_on):
    assert window_actions.run(switch("firefox"), CHAT_WINDOW, False) is True
    assert fake_extension.called == ["firefox.desktop"]


def test_switch_to_on_gnome_without_the_extension_says_it_cant(gnome, apps, actions_on,
                                                               monkeypatch):
    """Ubuntu's GNOME with the tray instead: "Firefox isn't open" would be wrong."""
    monkeypatch.setattr(app_launcher, "launch", lambda entry: pytest.fail("started"))
    shell = FakeShell(extension=False)
    try:
        assert window_actions.run(switch("firefox"), CHAT_WINDOW, False) is False
        assert actions_on == ["Can't switch windows on this desktop"]
    finally:
        shell.stop()


# --- naming no app: typed -------------------------------------------------------------------


def test_no_app_returns_none_with_no_note(kde, apps, actions_on, kwin_calls, caplog):
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert window_actions.run(close("the door"), CHAT_WINDOW, False) is None
        assert window_actions.run(switch("decaf"), CHAT_WINDOW, False) is None
    assert actions_on == [] and kwin_calls == []
    assert "Window command: no app matched; typed as words" in caplog.text
    assert "door" not in caplog.text and "decaf" not in caplog.text


def test_minimize_firefox_on_kde_asks_kwin_for_its_window(kde, apps, kwin_calls, actions_on):
    assert window_actions.run(WindowRequest("minimize", OpenRequest("firefox")),
                              CHAT_WINDOW, False) is True
    assert kwin_calls == [("minimize", "firefox", frozenset({"firefox"}), False)]
    assert actions_on == []


@pytest.mark.parametrize("said", ["Minimize the risk.", "Maximize profits.", "Restore the backup."])
def test_a_name_that_fits_no_app_is_typed(actions_on, chat, say, kde, apps, said):
    say(said)
    assert [text for text, _ in chat[2]] == [said] and actions_on == []


# --- through the dictation pipeline ---------------------------------------------------------


@pytest.mark.parametrize("said", ["Close the door.", "Close all the doors.", "Switch to decaf.",
                                  "Close all."])
def test_close_or_switch_naming_no_app_is_typed(actions_on, chat, say, kde, apps,
                                                nobody_is_asked, said):
    say(said)
    assert [text for text, _ in chat[2]] == [said] and actions_on == []


FILE_ROLLER = AppEntry("org.gnome.FileRoller.desktop", "File Roller", "File Roller",
                        "Archive Manager", "file-roller", "")
GIMP = AppEntry("gimp.desktop", "GNU Image Manipulation Program",
                "GNU Image Manipulation Program", "Image Editor", "gimp-2.10", "")
KEYS = AppEntry("org.gnome.seahorse.Application.desktop", "Passwords and Keys",
                "Passwords and Keys", "", "seahorse", "")
MEDIA_WRITER = AppEntry("org.fedoraproject.MediaWriter.desktop", "Fedora Media Writer",
                        "Fedora Media Writer", "", "mediawriter", "")


@pytest.fixture
def open_windows(monkeypatch):
    """kwin_windows.act's stand-in where only the apps whose desktop files are
    in *open* have a window; *done* keeps (action, desktop file) of each
    action asked for, the "find" that only looks left out."""
    state = types.SimpleNamespace(open=set(), done=[])

    def act(action, desktop_file, classes, every, timeout):
        if action != "find":
            state.done.append((action, desktop_file))
        return desktop_file in state.open
    monkeypatch.setattr(kwin_windows, "act", act)
    return state


@pytest.mark.parametrize("said", ["Close the fire.", "Close the file.", "Close all.",
                                  "Close every.", "Switch to fire.", "Close the door.",
                                  "Switch to decaf.", "Close the program.", "Close the processor.",
                                  "Switch to the chrome."])
def test_a_sentence_that_only_starts_like_an_apps_name_is_typed(actions_on, chat, say, kde,
                                                                apps, open_windows, said):
    """With every app open, a sentence isn't a window command: the start of
    a name ("fire") never counts, nor a word of a name after "the", nor a
    word of what the app is ("processor")."""
    apps[:] = WORD_NAMED + [FILE_ROLLER, GIMP, KEYS]
    open_windows.open = {app_focus.desktop_file(e) for e in apps}
    say(said)
    assert [text for text, _ in chat[2]] == [said] and actions_on == []
    assert open_windows.done == []


def test_only_small_words_of_a_name_never_name_the_app(actions_on, chat, say, kde, apps,
                                                       open_windows):
    """"and" is a word of "Passwords and Keys", and of many sentences."""
    apps[:] = [KEYS]
    open_windows.open = {app_focus.desktop_file(KEYS)}
    say("Close and.")
    assert [text.strip() for text, _ in chat[2]] == ["Close and"]   # no period after "and"
    assert actions_on == [] and open_windows.done == []


@pytest.mark.parametrize("said, app", [("Close Chrome.", CHROME), ("Close Edge.", EDGE),
                                       ("Close Writer.", WRITER)])
def test_a_word_of_an_open_apps_menu_name_closes_it(actions_on, chat, say, kde, apps,
                                                    open_windows, said, app):
    """"Chrome" is a word of "Google Chrome", as people call it; Fedora Media
    Writer, installed but not open, isn't the Writer meant."""
    apps[:] = WORD_NAMED + [MEDIA_WRITER]
    open_windows.open = {app_focus.desktop_file(app)}
    say(said)
    assert chat[2] == [] and open_windows.done == [("close", app_focus.desktop_file(app))]


@pytest.mark.parametrize("said, done", [("Close all the chrome.", []),
                                        ("Close all Chrome.", [("close", "google-chrome")])])
def test_close_all_keeps_the_article_rule(actions_on, chat, say, kde, apps, open_windows, said,
                                          done):
    """"the" after "close all" is a sentence's too: only "close all Chrome"
    closes every Chrome window."""
    apps[:] = WORD_NAMED
    open_windows.open = {app_focus.desktop_file(CHROME)}
    say(said)
    assert open_windows.done == done
    assert [text for text, _ in chat[2]] == ([] if done else [said])


def test_a_word_of_the_name_of_an_app_not_open_is_typed(actions_on, chat, say, kde, apps,
                                                        open_windows):
    apps[:] = WORD_NAMED
    say("Close Chrome.")
    assert [text for text, _ in chat[2]] == ["Close Chrome."] and actions_on == []
    assert open_windows.done == []


def test_a_word_two_open_apps_share_names_both(actions_on, chat, say, kde, apps, open_windows):
    apps[:] = [WRITER, MEDIA_WRITER]
    open_windows.open = {app_focus.desktop_file(WRITER), app_focus.desktop_file(MEDIA_WRITER)}
    say("Close Writer.")
    assert chat[2] == [] and open_windows.done == []
    assert actions_on == ["Two apps fit: LibreOffice Writer, Fedora Media Writer. "
                          "Say the full name."]


def test_a_word_of_a_name_counts_nowhere_open_windows_cant_be_told(actions_on, chat, say,
                                                                   cosmic, apps,
                                                                   nobody_is_asked):
    apps[:] = WORD_NAMED
    say("Close Chrome.")
    assert [text for text, _ in chat[2]] == ["Close Chrome."] and actions_on == []


def test_gnome_without_the_extension_logs_no_app_a_word_might_name(actions_on, chat, say, gnome,
                                                                   apps, caplog):
    """The apps a word fits hint at the words, which are then typed: the log
    says once that it couldn't tell, naming none of them."""
    apps[:] = [WRITER, MEDIA_WRITER]
    shell = FakeShell(extension=False)
    try:
        with caplog.at_level(logging.INFO, logger="tuxtalk"):
            say("Close Writer.")
    finally:
        shell.stop()
    assert [text for text, _ in chat[2]] == ["Close Writer."]
    assert caplog.text.count("Couldn't tell which apps have a window open") == 1
    assert "libreoffice" not in caplog.text and "MediaWriter" not in caplog.text


def test_switch_to_brave_by_a_word_of_its_menu_name(actions_on, chat, say, kde, apps, focused,
                                                    open_windows):
    apps[:] = WORD_NAMED
    open_windows.open = {"brave-browser"}
    say("Switch to Brave.")
    assert chat[2] == [] and focused == ["brave-browser.desktop"]


def test_close_the_web_closes_the_default_browser(actions_on, chat, say, kde, apps,
                                                  open_windows, monkeypatch):
    """"web", "browser", "mail", "files" and "terminal" name the default app
    (app_finder's default-app words), with or without "the"."""
    apps[:] = WORD_NAMED
    monkeypatch.setattr(app_finder, "default_apps", lambda: {"browser": "brave-browser.desktop"})
    open_windows.open = {"brave-browser"}
    say("Close the web.")
    assert chat[2] == [] and open_windows.done == [("close", "brave-browser")]


def test_open_chrome_is_unchanged(apps):
    apps[:] = WORD_NAMED
    assert app_match.find(OpenRequest("chrome")).entry == CHROME


def test_close_firefox_said_alone_types_nothing(actions_on, chat, say, kde, apps, kwin_calls,
                                                keyboard):
    say("Close Firefox.")
    assert chat[2] == [] and keyboard.typed_keys() == []
    assert kwin_calls == [("close", "firefox", frozenset({"firefox"}), False)]


def test_esc_before_a_window_command_does_nothing(actions_on, chat, say, kde, apps, kwin_calls,
                                                  monkeypatch):
    parse = command_phrases.parse

    def parse_then_esc(*args, **kwargs):
        request = parse(*args, **kwargs)
        esc()
        return request
    monkeypatch.setattr(command_phrases, "parse", parse_then_esc)
    say("Close Firefox.")
    assert kwin_calls == [] and chat[2] == []


def test_close_window_after_the_focus_moved_closes_nothing(kde, kwin_calls, actions_on,
                                                           chat):
    """The desktop closes whichever window has the focus when it gets the
    request: one the user switched to since must not be closed instead."""
    chat[0][:] = ["org.kde.kwrite", ("tray", "{another window}")]
    assert window_actions.run(close(), CHAT_WINDOW, False) is False
    assert kwin_calls == [] and actions_on == ["Window changed, nothing done"]


def test_a_named_app_is_closed_wherever_the_focus_is(kde, apps, kwin_calls, actions_on, chat):
    chat[0][:] = ["org.kde.kwrite", ("tray", "{another window}")]
    assert window_actions.run(close("firefox"), CHAT_WINDOW, False) is True
    assert kwin_calls == [("close", "firefox", frozenset({"firefox"}), False)]


def test_esc_while_the_app_is_found_closes_nothing(actions_on, chat, say, kde, apps, kwin_calls,
                                                   monkeypatch):
    def apps_then_esc():
        esc()
        return [FIREFOX, KWRITE]
    monkeypatch.setattr(app_finder, "installed_apps", apps_then_esc)
    say("Close Firefox.")
    assert kwin_calls == [] and chat[2] == [] and actions_on == []


def test_a_window_command_mid_recording_ends_it(actions_on, recording_on, chat, kde,
                                                kwin_calls):
    rec, phrase = recording_on
    phrase("minimize window")
    service_tick_runs()
    assert kwin_calls == [("minimize", "", frozenset(), False)]
    assert rec.message.cancelled and rs.state.is_recording is False


def test_a_refused_window_command_mid_recording_keeps_it_going(actions_on, recording_on, chat,
                                                               kde, kwin_calls):
    rec, phrase = recording_on
    kwin_calls.answer = False
    phrase("minimize window")
    service_tick_runs()
    assert actions_on == ["Couldn't tell which window"] and chat[2] == []
    assert not rec.message.cancelled and rs.state.is_recording is True


def test_voice_actions_hands_a_window_request_over(kde, apps, kwin_calls, actions_on):
    assert voice_actions.run("close all Firefox", False) is True
    assert kwin_calls == [("close", "firefox", frozenset({"firefox"}), True)]
