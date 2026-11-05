"""Window voice commands (TuxTalk Labs): "close Firefox", "switch to
Firefox", "minimize window" said alone do that and type nothing, on KDE,
GNOME and X11; elsewhere a note says it can't be done. A command naming no
app ("Close the door.") is typed as dictation.

The desktops are stand-ins (desktop_fakes.py, or a recording act()): nothing
here touches the real one, starts an app or closes a window.
"""
import logging

import pytest

from conftest import esc
from desktop_fakes import FakeShell, set_desktop
from type_at_pause_rig import service_tick_runs
from tuxtalk import (app_finder, app_focus, app_launcher, command_phrases, kwin_windows,
                     notify, screen_message, voice_actions, window_actions, x11_windows)
from tuxtalk import recording_state as rs
from tuxtalk.app_commands import OpenRequest
from tuxtalk.app_finder import AppEntry
from tuxtalk.beeps import READY_BEEP
from tuxtalk.command_phrases import WindowRequest

FIREFOX = AppEntry("firefox.desktop", "Firefox", "Firefox", "Web Browser", "firefox", "")
KWRITE = AppEntry("org.kde.kwrite.desktop", "KWrite", "KWrite", "Text Editor", "kwrite", "")
KONSOLE = AppEntry("org.kde.konsole.desktop", "Konsole", "Konsole", "Terminal", "konsole", "")
KITTY = AppEntry("kitty.desktop", "Kitty", "Kitty", "Terminal", "kitty", "")
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
def x11_session(monkeypatch):
    set_desktop(monkeypatch, "XFCE", "x11")


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
def floated(monkeypatch):
    """The labels floated over the window, with notifications kept off the
    desktop. Its *beeps* are the tones played."""
    shown = Asked()
    shown.beeps = []
    monkeypatch.setattr(screen_message, "show_over_active_window",
                        lambda text, *a, **k: shown.append(text))
    monkeypatch.setattr(notify, "notify", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.beeps.beep",
                        lambda on, *tone, **k: on and shown.beeps.append(tone))
    return shown


@pytest.fixture
def focused(monkeypatch):
    """app_focus.focus_running's stand-in: the app ids brought forward, and
    starting an app fails the test."""
    brought = Asked()

    def focus_running(entry, timeout=1.0, exact=False):
        if brought.answer:
            brought.append(entry.app_id)
        return brought.answer

    def launch(entry):
        pytest.fail(f"switch to started {entry.app_id}")
    monkeypatch.setattr(app_focus, "focus_running", focus_running)
    monkeypatch.setattr(app_launcher, "launch", launch)
    return brought


def close(app="", every=False):
    return WindowRequest("close", OpenRequest(app) if app else None, every)


# --- KDE ------------------------------------------------------------------------------------


def test_close_firefox_on_kde_asks_kwin_with_the_apps_window_names(kde, apps, kwin_calls,
                                                                    floated):
    assert window_actions.run(close("firefox"), True) is True
    assert kwin_calls == [("close", "firefox", frozenset({"firefox"}), False)]
    assert floated == [] and floated.beeps == [READY_BEEP]


def test_close_all_firefox_asks_for_every_window(kde, apps, kwin_calls, floated):
    assert window_actions.run(close("firefox", every=True), False) is True
    assert kwin_calls == [("close", "firefox", frozenset({"firefox"}), True)]


def test_close_window_on_kde_asks_for_the_focused_one(kde, kwin_calls, floated):
    assert window_actions.run(close(), False) is True
    assert kwin_calls == [("close", "", frozenset(), False)]


def test_close_an_app_with_no_window_says_so(kde, apps, kwin_calls, floated):
    """Firefox in the menu but not open: nothing is closed, and a note says why."""
    kwin_calls.answer = False
    assert window_actions.run(close("firefox"), False) is False
    assert floated == ["Firefox has no open window"]


def test_close_window_with_no_focused_window_says_so(kde, kwin_calls, floated):
    kwin_calls.answer = False
    assert window_actions.run(close(), False) is False
    assert floated == ["Couldn't tell which window"]


def test_a_desktop_error_is_said_and_logged(kde, apps, floated, monkeypatch, caplog):
    def refused(*_args):
        raise RuntimeError("KWin refused the script")
    monkeypatch.setattr(kwin_windows, "act", refused)
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert window_actions.run(WindowRequest("maximize"), False) is False
    assert floated == ["Couldn't press the keys"]
    assert "maximize" in caplog.text and "KWin refused the script" in caplog.text


def test_the_log_names_the_app_not_the_words(kde, apps, kwin_calls, floated, caplog):
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        window_actions.run(close("fire fox"), False)
    assert "Voice command: close firefox.desktop" in caplog.text
    assert "fire fox" not in caplog.text


def test_the_log_says_the_focused_window(kde, kwin_calls, floated, caplog):
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        window_actions.run(WindowRequest("minimize"), False)
    assert "Voice command: minimize the focused window" in caplog.text


def test_a_tie_names_both_and_closes_neither(kde, apps, kwin_calls, floated):
    apps[:] = [KONSOLE, KITTY]
    assert window_actions.run(close("terminal"), False) is False
    assert kwin_calls == []
    assert floated == ["Two apps fit: Konsole, Kitty. Say the full name."]


@pytest.mark.parametrize("desktop, session", [("KDE", "wayland"), ("GNOME", "wayland"),
                                              ("XFCE", "x11")])
def test_a_named_app_with_nothing_to_know_it_by_never_closes_the_focused_window(
        apps, floated, nobody_is_asked, monkeypatch, desktop, session):
    """An empty identity means "the focused window" to every desktop: an app
    whose entry gives none must not close whatever the user is in."""
    set_desktop(monkeypatch, desktop, session)
    apps.append(NAMELESS)
    assert window_actions.run(close("ghostwriter"), False) is False
    assert floated == ["Ghostwriter has no open window"]


# --- GNOME ----------------------------------------------------------------------------------


def test_close_window_on_gnome_asks_the_extension_for_the_focused_one(gnome, fake_extension,
                                                                      floated):
    assert window_actions.run(close(), False) is True
    assert fake_extension.window_calls == [("close", "", False)]


def test_close_all_firefox_on_gnome_names_its_menu_entry(gnome, apps, fake_extension,
                                                         floated):
    assert window_actions.run(close("firefox", every=True), False) is True
    assert fake_extension.window_calls == [("close", "firefox.desktop", True)]


def test_gnome_with_no_such_window_says_so(gnome, apps, fake_extension, floated):
    fake_extension.window_answer = False
    assert window_actions.run(close("firefox"), False) is False
    assert floated == ["Firefox has no open window"]


def test_gnome_extension_before_logout_says_to_log_out(gnome, floated):
    """GNOME Shell runs the extension it loaded at login until the user logs
    out: one from before the update has no WindowAction."""
    shell = FakeShell(extension="old")
    try:
        assert window_actions.run(WindowRequest("minimize"), False) is False
        assert floated == ["Log out and back in for this"]
    finally:
        shell.stop()


# --- X11 ------------------------------------------------------------------------------------


def test_minimize_on_x11_uses_the_window_manager(x11_session, x11_calls, floated):
    assert window_actions.run(WindowRequest("minimize"), False) is True
    assert x11_calls == [("minimize", "", frozenset(), False)]


def test_close_firefox_on_x11_reaches_the_window_manager(window_manager, apps, floated):
    firefox = window_manager.window("Navigator", "firefox")
    window_manager.stack([window_manager.window("mousepad", "Mousepad"), firefox])
    assert window_actions.run(close("firefox"), False) is True
    assert window_manager.requests(b"_NET_CLOSE_WINDOW") == [(firefox, (0, 2, 0, 0, 0))]


# --- everywhere else ------------------------------------------------------------------------


def test_cosmic_closes_nothing_and_says_so(cosmic, nobody_is_asked, floated):
    assert window_actions.run(close(), False) is False
    assert floated == ["Can't close windows on this desktop"]


def test_cosmic_minimizes_nothing_and_says_so(cosmic, nobody_is_asked, floated):
    assert window_actions.run(WindowRequest("maximize"), False) is False
    assert floated == ["Can't minimize or maximize here"]


# --- switch to ------------------------------------------------------------------------------


def test_switch_to_brings_it_forward_and_never_starts_it(kde, apps, focused, floated):
    assert window_actions.run(WindowRequest("switch", OpenRequest("firefox")), True) is True
    assert focused == ["firefox.desktop"] and floated.beeps == [READY_BEEP]


def test_switch_to_an_app_not_running_says_so(kde, apps, focused, floated):
    focused.answer = False
    assert window_actions.run(WindowRequest("switch", OpenRequest("firefox")), False) is False
    assert floated == ["Firefox isn't open"]


def test_switch_to_on_cosmic_says_it_cant(cosmic, apps, focused, floated):
    assert window_actions.run(WindowRequest("switch", OpenRequest("firefox")), False) is False
    assert focused == [] and floated == ["Can't switch windows on this desktop"]


# --- naming no app: typed -------------------------------------------------------------------


def test_no_app_returns_none_with_no_note(kde, apps, floated, kwin_calls, caplog):
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert window_actions.run(close("the door"), False) is None
        assert window_actions.run(WindowRequest("switch", OpenRequest("decaf")), False) is None
    assert floated == [] and kwin_calls == []
    assert "Window command: no app matched; typed as words" in caplog.text
    assert "door" not in caplog.text and "decaf" not in caplog.text


# --- through the dictation pipeline ---------------------------------------------------------


@pytest.mark.parametrize("said", ["Close the door.", "Close all the doors.", "Switch to decaf.",
                                  "Close all."])
def test_close_or_switch_naming_no_app_is_typed(actions_on, chat, say, kde, apps,
                                                nobody_is_asked, said):
    say(said)
    assert [text for text, _ in chat[2]] == [said] and actions_on == []


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
