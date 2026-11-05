"""Lock the screen and "what can I say" by voice (system_actions.py). A
stand-in for loginctl replaces every program run: no test locks the session."""
import logging
import subprocess

import pytest

from tuxtalk import system_actions, tray_link
from tuxtalk.command_phrases import SystemRequest


class Loginctl:
    """loginctl as the tests need it: records each command, answers
    show-session from *hint*, and refuses lock-session when *refuses*."""

    def __init__(self):
        self.commands = []
        self.hint = "yes"
        self.refuses = False

    def __call__(self, cmd, **kw):
        self.commands.append(list(cmd))
        if cmd[1] == "lock-session" and self.refuses:
            return subprocess.CompletedProcess(cmd, 1, "", "denied")
        out = self.hint + "\n" if cmd[1] == "show-session" else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.fixture
def loginctl(monkeypatch):
    fake = Loginctl()
    monkeypatch.setattr(system_actions.subprocess, "run", fake)
    return fake


def no_wait(_seconds):
    """Stands in for the wait for the locker."""


def test_lock_screen_asks_logind_to_lock_this_session(loginctl, monkeypatch, kde):
    monkeypatch.setenv("XDG_SESSION_ID", "3")
    loginctl.hint = "yes"
    assert system_actions.lock(False, sleep=no_wait) is True
    assert loginctl.commands[0] == ["loginctl", "lock-session", "3"]


def test_a_lock_the_session_never_confirms_is_not_called_locked(loginctl, kde, caplog):
    loginctl.hint = "no"
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        system_actions.lock(False, sleep=no_wait)
    assert "says it is locked" not in caplog.text
    assert "doesn't say it is locked" in caplog.text


def test_a_confirmed_lock_is_logged_as_locked(loginctl, kde, caplog):
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        system_actions.lock(False, sleep=no_wait)
    assert "the session says it is locked" in caplog.text


def test_elsewhere_an_unconfirmed_lock_says_so(loginctl, cosmic, actions_on):
    loginctl.hint = "no"
    assert system_actions.lock(False, sleep=no_wait) is True
    assert actions_on == ["Lock not confirmed"]


def test_on_kde_an_unconfirmed_lock_gives_no_note(loginctl, kde, actions_on):
    loginctl.hint = "no"
    system_actions.lock(False, sleep=no_wait)
    assert actions_on == []


def test_a_lock_logind_refuses_says_so(loginctl, kde, actions_on):
    loginctl.refuses = True
    assert system_actions.lock(False, sleep=no_wait) is False
    assert actions_on == ["Couldn't lock the screen"]
    assert not [c for c in loginctl.commands if c[1] == "show-session"]


def test_the_check_waits_for_the_locker(loginctl, kde):
    order = []
    system_actions.lock(False, sleep=lambda s: order.append(("sleep", s, len(loginctl.commands))))
    assert order == [("sleep", system_actions.LOCK_CHECK_S, 1)]
    assert loginctl.commands[1][1] == "show-session"


def test_without_a_session_id_it_locks_the_callers_session(loginctl, kde, monkeypatch):
    monkeypatch.delenv("XDG_SESSION_ID", raising=False)
    system_actions.lock(False, sleep=no_wait)
    assert loginctl.commands[0] == ["loginctl", "lock-session", "auto"]


def test_a_session_that_cannot_be_asked_is_not_called_locked(kde, monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError("loginctl")
    monkeypatch.setattr(system_actions.subprocess, "run", missing)
    assert system_actions.locked("auto") is None


def test_what_can_i_say_opens_the_voice_commands_window(monkeypatch):
    shown = []
    monkeypatch.setattr(tray_link, "show_voice_commands", lambda: shown.append(True))
    assert system_actions.run(SystemRequest("commands"), False) is True
    assert shown == [True]


def test_lock_screen_said_alone_types_nothing(actions_on, chat, say, loginctl, kde, monkeypatch):
    monkeypatch.setattr(system_actions, "LOCK_CHECK_S", 0)
    say("Lock the screen.")
    assert chat[2] == []
    assert loginctl.commands[0][:2] == ["loginctl", "lock-session"]
