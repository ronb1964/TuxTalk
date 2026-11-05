"""Desktop voice commands (TuxTalk Labs): "snap left", "centre window",
"show desktop", "switch to desktop two" and the rest, said alone, are done on
KDE, GNOME and X11 and type nothing; elsewhere a note says it can't be done.
A snap or a move acts only on the window that had the focus when it was
heard.

The desktops are stand-ins (desktop_fakes.py, or a recording act()): nothing
here touches the real one.
"""
import logging

import pytest

from conftest import esc
from desktop_fakes import FakeShell
from type_at_pause_rig import service_tick_runs
from tuxtalk import (command_notices, command_phrases, desktop_actions, kwin_desktop, notify,
                     voice_actions)
from tuxtalk import recording_state as rs
from tuxtalk.beeps import READY_BEEP
from tuxtalk.command_phrases import DesktopRequest

KWRITE_LOOK = ("org.kde.kwrite", ("tray", "1"))
KONSOLE_LOOK = ("org.kde.konsole", ("tray", "2"))


@pytest.fixture
def focus_on(chat):
    """focus_on(look): the window that has the focus from now on."""
    def put(look):
        chat[0][:] = list(look)
    return put


def typed(chat):
    return [text for text, _ in chat[2]]


# --- KDE ------------------------------------------------------------------------------------


def test_snap_left_on_kde_invokes_the_shortcut(kde, fake_kde, actions_on, focus_on):
    focus_on(KWRITE_LOOK)
    assert desktop_actions.run(DesktopRequest("snap left"), KWRITE_LOOK, False) is True
    assert fake_kde.calls == [("invokeShortcut", "Window Quick Tile Left")] and actions_on == []


def test_a_done_command_plays_the_ready_beep(kde, fake_kde, actions_on, focus_on, monkeypatch):
    played = []
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda on, *tone, **k: on and played.append(tone))
    focus_on(KWRITE_LOOK)
    assert desktop_actions.run(DesktopRequest("center"), KWRITE_LOOK, True) is True
    assert played == [READY_BEEP]


def test_a_snap_after_the_focus_moved_does_nothing(kde, fake_kde, actions_on, focus_on):
    """The desktop snaps whichever window has the focus when it gets the
    request: one the user clicked into since must not be snapped instead."""
    focus_on(KONSOLE_LOOK)
    assert desktop_actions.run(DesktopRequest("snap left"), KWRITE_LOOK, False) is False
    assert fake_kde.calls == [] and actions_on == ["Window changed, nothing done"]


def test_a_move_to_a_desktop_after_the_focus_moved_does_nothing(gnome, fake_extension,
                                                                 actions_on, focus_on):
    focus_on(KONSOLE_LOOK)
    assert desktop_actions.run(DesktopRequest("to desktop", 2), KWRITE_LOOK, False) is False
    assert fake_extension.desktop_calls == []
    assert actions_on == ["Window changed, nothing done"]


def test_show_desktop_needs_no_focus_check(kde, fake_kde, actions_on, focus_on):
    focus_on(KONSOLE_LOOK)
    assert desktop_actions.run(DesktopRequest("show desktop"), KWRITE_LOOK, False) is True
    assert fake_kde.calls == [("showDesktop", True)]


def test_the_log_names_the_command_and_number(kde, fake_kde, actions_on, caplog):
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert desktop_actions.run(DesktopRequest("desktop", 2), KWRITE_LOOK, False) is True
    assert "Voice command: desktop 2" in caplog.text
    assert fake_kde.calls == [("setCurrentDesktop", 2)]


@pytest.mark.parametrize("error", [TimeoutError("KWin didn't answer in time"),
                                   RuntimeError("KWin's tuxtalk-screens script failed: x")])
def test_a_kwin_that_doesnt_answer_is_never_told_as_one_screen(kde, actions_on, focus_on,
                                                               monkeypatch, caplog, error):
    """A user with two screens must not hear there is one because KWin was slow."""
    def silent(*_args):
        raise error
    monkeypatch.setattr(kwin_desktop, "act", silent)
    focus_on(KWRITE_LOOK)
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert desktop_actions.run(DesktopRequest("to next screen"), KWRITE_LOOK,
                                   False) is False
    assert actions_on == ["The desktop didn't answer"]
    assert "to next screen" in caplog.text and str(error) in caplog.text


# --- GNOME ----------------------------------------------------------------------------------


def test_gnome_asks_the_extension(gnome, fake_extension, actions_on):
    assert desktop_actions.run(DesktopRequest("desktop", 2), KWRITE_LOOK, False) is True
    assert fake_extension.desktop_calls == [("desktop", 2)] and actions_on == []


def test_gnome_sends_no_number_as_zero(gnome, fake_extension, actions_on, focus_on):
    focus_on(KWRITE_LOOK)
    assert desktop_actions.run(DesktopRequest("snap right"), KWRITE_LOOK, False) is True
    assert fake_extension.desktop_calls == [("snap right", 0)]


@pytest.mark.parametrize("answer, label", [
    ("no_desktop", "There's no desktop 5"), ("one_desktop", "There's only one desktop"),
    ("edge", "No desktop that way"), ("one_screen", "There's only one screen"),
    ("no_window", "Couldn't tell which window"), ("unknown", "Can't switch desktops here")])
def test_each_answer_has_its_note(gnome, fake_extension, actions_on, answer, label):
    fake_extension.desktop_answer = answer
    assert desktop_actions.run(DesktopRequest("desktop", 5), KWRITE_LOOK, False) is False
    assert actions_on == [label]


def test_a_note_names_what_to_do_in_the_notification(gnome, fake_extension, actions_on,
                                                     monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "notify", lambda title, body, *a, **k: sent.append(body))
    fake_extension.desktop_answer = "no_desktop"
    desktop_actions.run(DesktopRequest("desktop", 5), KWRITE_LOOK, False)
    assert sent == [command_notices.NOTES["no_such_desktop"][1]]


def test_an_unknown_snap_says_it_cant_move_windows(gnome, fake_extension, actions_on,
                                                   focus_on):
    fake_extension.desktop_answer = "unknown"
    focus_on(KWRITE_LOOK)
    assert desktop_actions.run(DesktopRequest("snap left"), KWRITE_LOOK, False) is False
    assert actions_on == ["Can't move windows on this desktop"]


def test_gnome_extension_before_logout_says_to_log_out(gnome, actions_on, focus_on):
    """GNOME Shell runs the extension it loaded at login until the user logs
    out, and that one has no DesktopCommand."""
    focus_on(KWRITE_LOOK)
    shell = FakeShell(extension="old")
    try:
        assert desktop_actions.run(DesktopRequest("snap left"), KWRITE_LOOK, False) is False
        assert actions_on == ["Log out and back in for this"]
    finally:
        shell.stop()


def test_gnome_without_the_extension_says_it_cant(gnome, actions_on):
    shell = FakeShell(extension=False)
    try:
        assert desktop_actions.run(DesktopRequest("next desktop"), KWRITE_LOOK,
                                   False) is False
        assert actions_on == ["Can't switch desktops here"]
    finally:
        shell.stop()


# --- X11 and everywhere else ----------------------------------------------------------------


def test_x11_overview_says_there_is_none(x11_session, actions_on):
    assert desktop_actions.run(DesktopRequest("overview"), KWRITE_LOOK, False) is False
    assert actions_on == ["No window overview here"]


def test_x11_asks_the_window_manager(x11_session, actions_on, focus_on, monkeypatch):
    asked = []
    monkeypatch.setattr("tuxtalk.x11_desktop.act",
                        lambda action, number: asked.append((action, number)) or "done")
    focus_on(KWRITE_LOOK)
    assert desktop_actions.run(DesktopRequest("to desktop", 3), KWRITE_LOOK, False) is True
    assert asked == [("to desktop", 3)]


def test_an_x11_error_says_it_cant_and_is_logged(x11_session, actions_on, monkeypatch, caplog):
    def refused(*_args):
        raise OSError("the X server refused the request (error 3)")
    monkeypatch.setattr("tuxtalk.x11_desktop.act", refused)
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert desktop_actions.run(DesktopRequest("show desktop"), KWRITE_LOOK,
                                   False) is False
    assert actions_on == ["Can't switch desktops here"]
    assert "show desktop" in caplog.text and "error 3" in caplog.text


def test_cosmic_snaps_nothing_and_says_so(cosmic, nobody_is_asked, actions_on, focus_on):
    focus_on(KWRITE_LOOK)
    assert desktop_actions.run(DesktopRequest("snap left"), KWRITE_LOOK, False) is False
    assert actions_on == ["Can't move windows on this desktop"]


# --- through the dictation pipeline ---------------------------------------------------------


def test_snap_left_said_alone_types_nothing(actions_on, chat, say, kde, fake_kde, keyboard):
    say("Snap left.")
    assert chat[2] == [] and keyboard.typed_keys() == []
    assert fake_kde.calls == [("invokeShortcut", "Window Quick Tile Left")]


@pytest.mark.parametrize("said", ["Show me the desktop.", "Move the window later.",
                                  "Switch to desktop 10."])
def test_near_miss_sentences_are_typed_through_the_pipeline(actions_on, chat, say, kde,
                                                            fake_kde, said):
    say(said)
    assert typed(chat) == [said] and fake_kde.calls == [] and actions_on == []


def test_esc_before_a_desktop_command_does_nothing(actions_on, chat, say, kde, fake_kde,
                                                   monkeypatch):
    parse = command_phrases.parse

    def parse_then_esc(*args, **kwargs):
        request = parse(*args, **kwargs)
        esc()
        return request
    monkeypatch.setattr(command_phrases, "parse", parse_then_esc)
    say("Show desktop.")
    assert fake_kde.calls == [] and chat[2] == []


def test_a_desktop_command_mid_recording_ends_it(actions_on, recording_on, kde, fake_kde):
    rec, phrase = recording_on
    phrase("show desktop")
    service_tick_runs()
    assert fake_kde.calls == [("showDesktop", True)]
    assert rec.message.cancelled and rs.state.is_recording is False


def test_a_refused_desktop_command_mid_recording_keeps_it_going(actions_on, recording_on,
                                                                kde, fake_kde):
    rec, phrase = recording_on
    fake_kde.desktops = 1
    phrase("next desktop")
    service_tick_runs()
    assert actions_on == ["There's only one desktop"]
    assert not rec.message.cancelled and rs.state.is_recording is True


def test_voice_actions_hands_a_desktop_request_over(kde, fake_kde, actions_on):
    assert voice_actions.run("switch to desktop two", False) is True
    assert fake_kde.calls == [("setCurrentDesktop", 2)]


# --- a short note for a command that was done -----------------------------------------------


def test_a_done_note_floats_its_label_and_sends_no_notification(actions_on, monkeypatch,
                                                                caplog):
    floated, sent = [], []
    monkeypatch.setattr("tuxtalk.screen_message.show_over_active_window",
                        lambda text, *a, **k: floated.append((text, k.get("warning"))))
    monkeypatch.setattr(notify, "notify", lambda *a, **k: sent.append(a))
    monkeypatch.setitem(command_notices.DONE, "volume_set", "Volume {level}%")
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        command_notices.tell("volume_set", False, level=45)
    assert floated == [("Volume 45%", False)] and sent == []
    assert "Voice command done: volume_set" in caplog.text
