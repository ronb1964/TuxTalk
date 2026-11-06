"""Lock the screen and "what can I say" by voice (system_actions.py). A
stand-in for loginctl replaces every program run, and a stand-in
screensaver on the test's own session bus answers for Cinnamon, MATE and
Xfce: no test locks the session."""
import logging
import subprocess
import time
import types

import pytest
from gi.repository import Gio, GLib

from desktop_fakes import FakeXfconf, set_desktop
from tuxtalk import beeps, system_actions, tray_link
from tuxtalk.command_phrases import SystemRequest


class Loginctl:
    """loginctl as the tests need it: records each command, answers
    show-session from *hints* in turn and then *hint*, and refuses
    lock-session when *refuses*."""

    def __init__(self):
        self.commands = []
        self.hints = []
        self.hint = "yes"
        self.refuses = False

    def __call__(self, cmd, **kw):
        self.commands.append(list(cmd))
        if cmd[1] == "lock-session" and self.refuses:
            return subprocess.CompletedProcess(cmd, 1, "", "denied")
        out = ""
        if cmd[1] == "show-session":
            out = (self.hints.pop(0) if self.hints else self.hint) + "\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def looks(self):
        return [c for c in self.commands if c[1] == "show-session"]


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
    assert "the lock wasn't seen" in caplog.text


def test_a_confirmed_lock_is_logged_as_locked(loginctl, kde, caplog):
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        system_actions.lock(False, sleep=no_wait)
    assert "the session says it is locked" in caplog.text


def test_elsewhere_an_unconfirmed_lock_says_so(loginctl, cosmic, actions_on):
    loginctl.hint = "no"
    assert system_actions.lock(False, sleep=no_wait) is True
    assert actions_on == ["Lock not confirmed"]


def test_on_kde_too_an_unconfirmed_lock_says_so(loginctl, kde, actions_on):
    loginctl.hint = "no"
    system_actions.lock(False, sleep=no_wait)
    assert actions_on == ["Lock not confirmed"]


def test_a_lock_logind_refuses_says_so(loginctl, kde, actions_on):
    loginctl.refuses = True
    assert system_actions.lock(False, sleep=no_wait) is False
    assert actions_on == ["Couldn't lock the screen"]
    assert not [c for c in loginctl.commands if c[1] == "show-session"]


def test_the_check_waits_for_the_locker(loginctl, kde):
    order = []
    system_actions.lock(
        False, sleep=lambda s: order.append(("sleep", s, len(loginctl.commands))))
    assert order[0][2] == 1
    assert loginctl.commands[1][1] == "show-session"


def test_a_locker_that_answers_after_a_moment_sounds_done(loginctl, gnome, actions_on, beeped):
    loginctl.hints = ["no", "no"]
    system_actions.lock(True, sleep=no_wait)
    assert beeped == [beeps.READY_BEEP]
    assert actions_on == []
    assert len(loginctl.looks()) == 3


def test_a_locker_that_never_answers_is_given_about_three_seconds(loginctl, kde, actions_on):
    waited = []
    loginctl.hint = "no"
    system_actions.lock(False, sleep=waited.append)
    assert 2.5 <= sum(waited) <= 3.5
    assert actions_on == ["Lock not confirmed"]


def test_a_locked_state_it_cant_read_is_not_asked_again(loginctl, kde, actions_on):
    loginctl.hint = "maybe"
    system_actions.lock(False, sleep=no_wait)
    assert len(loginctl.looks()) == 1
    assert actions_on == ["Lock not confirmed"]


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


@pytest.fixture
def beeped(monkeypatch):
    """The beeps asked for, with sound on, instead of played."""
    heard = []
    monkeypatch.setattr(beeps, "beep", lambda on, *tone: heard.append(tone) if on else None)
    return heard


def test_an_unconfirmed_lock_elsewhere_never_sounds_done(loginctl, cosmic, actions_on, beeped):
    loginctl.hint = "no"
    system_actions.lock(True, sleep=no_wait)
    assert beeps.READY_BEEP not in beeped
    assert actions_on == ["Lock not confirmed"]


def test_a_confirmed_lock_sounds_done_even_elsewhere(loginctl, cosmic, actions_on, beeped):
    loginctl.hint = "yes"
    system_actions.lock(True, sleep=no_wait)
    assert beeped == [beeps.READY_BEEP]


def test_on_kde_an_unconfirmed_lock_never_sounds_done(loginctl, kde, actions_on, beeped):
    loginctl.hint = "no"
    system_actions.lock(True, sleep=no_wait)
    assert beeps.READY_BEEP not in beeped


def test_a_slow_session_query_means_not_confirmed(loginctl, cosmic, actions_on, monkeypatch):
    real = loginctl.__call__

    def slow(cmd, **kw):
        if cmd[1] == "show-session":
            raise subprocess.TimeoutExpired(cmd, 2)
        return real(cmd, **kw)
    monkeypatch.setattr(system_actions.subprocess, "run", slow)
    assert system_actions.lock(False, sleep=no_wait) is True
    assert actions_on == ["Lock not confirmed"]


# The desktops whose screensaver locks the screen and never tells logind:
# (XDG_CURRENT_DESKTOP, the screensaver's bus name).
SCREENSAVERS = [("X-Cinnamon", "org.cinnamon.ScreenSaver"), ("MATE", "org.mate.ScreenSaver"),
                ("XFCE", "org.xfce.ScreenSaver")]
_ON_EACH = pytest.mark.parametrize("desktop, screensaver", SCREENSAVERS,
                                   indirect=["screensaver"])


def _mate_lockdown(monkeypatch, tmp_path, disabled):
    """MATE's lockdown settings with disable-lock-screen *disabled*, from a
    schema of the test's own: the machine's settings are never read."""
    folder = tmp_path / f"schemas-{disabled}"
    folder.mkdir()
    (folder / "org.mate.lockdown.gschema.xml").write_text(f"""<schemalist>
  <schema id="org.mate.lockdown" path="/tuxtalk-tests/mate-lockdown/">
    <key name="disable-lock-screen" type="b"><default>{str(disabled).lower()}</default></key>
  </schema>
</schemalist>""")
    # Not subprocess.run: the loginctl stand-in takes its place.
    subprocess.check_call(["glib-compile-schemas", str(folder)])
    source = Gio.SettingsSchemaSource.new_from_directory(str(folder), None, False)
    monkeypatch.setattr(Gio.SettingsSchemaSource, "get_default", staticmethod(lambda: source))
    memory = Gio.memory_settings_backend_new()
    real = Gio.Settings.new_full
    monkeypatch.setattr(Gio.Settings, "new_full",
                        staticmethod(lambda schema, _backend, path: real(schema, memory, path)))


@pytest.fixture(autouse=True)
def an_ordinary_session(monkeypatch, tmp_path):
    """Where each screensaver locks, whatever the machine running the tests
    is: the user isn't in nopasswdlogin, nor in a live or guest session
    (Cinnamon); the lock screen isn't disabled by MATE's lockdown setting;
    Xfce's /lock/enabled was never changed. Returns the stand-in Xfconf."""
    def no_such_group(name):
        raise KeyError(name)
    monkeypatch.setattr(system_actions.grp, "getgrnam", no_such_group)
    boot = tmp_path / "ordinary-cmdline"
    boot.write_text("BOOT_IMAGE=/vmlinuz root=UUID=1234 ro quiet splash\n")
    monkeypatch.setattr(system_actions, "_KERNEL_COMMAND_LINE", str(boot))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _mate_lockdown(monkeypatch, tmp_path, disabled=False)
    xfconf = FakeXfconf()
    yield xfconf
    xfconf.stop()


@_ON_EACH
def test_there_the_screensaver_locks_and_says_so(loginctl, screensaver, desktop, monkeypatch,
                                                  actions_on, beeped, caplog):
    """cinnamon-screensaver never sets logind's LockedHint, so a lock asked
    of logind was never confirmed on Mint (and on the Mint VM the screen
    didn't even lock)."""
    set_desktop(monkeypatch, desktop, "x11")
    screensaver.locks_after = 1
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert system_actions.lock(True, sleep=no_wait) is True
    assert len(screensaver.locks) == 1
    assert not [c for c in loginctl.commands if c[1] == "lock-session"]
    assert (beeped, actions_on) == ([beeps.READY_BEEP], [])
    assert "the screensaver came on for it" in caplog.text


@_ON_EACH
def test_a_lock_the_screensaver_never_shows_is_not_called_locked(
        loginctl, screensaver, desktop, monkeypatch, actions_on, beeped):
    """MATE and Xfce don't come on for Lock when locking is turned off."""
    set_desktop(monkeypatch, desktop, "x11")
    screensaver.locks_after = None
    system_actions.lock(True, sleep=no_wait)
    assert beeps.READY_BEEP not in beeped
    assert actions_on == ["Lock not confirmed"]


def _seen_lock(screensaver):
    """Whether *screensaver* got one Lock. It is sent with no answer
    awaited, so the stand-in may read it a moment after lock() returns."""
    end = time.monotonic() + 2
    while not screensaver.locks and time.monotonic() < end:
        time.sleep(0.01)
    return len(screensaver.locks) == 1


@_ON_EACH
def test_a_screensaver_already_on_proves_nothing(loginctl, screensaver, desktop, monkeypatch,
                                                 actions_on, beeped):
    """On before the request, its answer can't show the request locked
    anything: a screen blanked with locking turned off answers the same."""
    set_desktop(monkeypatch, desktop, "x11")
    screensaver.active = True
    system_actions.lock(True, sleep=no_wait)
    assert _seen_lock(screensaver)
    assert beeps.READY_BEEP not in beeped
    assert actions_on == ["Lock not confirmed"]


@pytest.mark.parametrize("screensaver", ["org.mate.ScreenSaver"], indirect=True)
def test_a_lock_request_never_answered_is_still_confirmed_at_once(
        loginctl, screensaver, monkeypatch, actions_on, beeped):
    """mate-screensaver never answers Lock: waiting for the answer held
    every lock up until the call timed out."""
    set_desktop(monkeypatch, "MATE", "x11")
    screensaver.replies_to_lock = False
    screensaver.locks_after = 1
    started = time.monotonic()
    system_actions.lock(True, sleep=no_wait)
    assert time.monotonic() - started < 1
    assert (beeped, actions_on) == ([beeps.READY_BEEP], [])


def _nopasswdlogin_member(monkeypatch, tmp_path):
    user = GLib.get_user_name()
    monkeypatch.setattr(system_actions.grp, "getgrnam",
                        lambda name: types.SimpleNamespace(gr_mem=[user]))


def _live_session(monkeypatch, tmp_path):
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("BOOT_IMAGE=/casper/vmlinuz boot=casper quiet splash\n")
    monkeypatch.setattr(system_actions, "_KERNEL_COMMAND_LINE", str(cmdline))


def _guest_session(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", "/tmp/guest-x1y2z3")


@pytest.mark.parametrize("no_lock_here", [_nopasswdlogin_member, _live_session, _guest_session])
@pytest.mark.parametrize("screensaver", ["org.cinnamon.ScreenSaver"], indirect=True)
def test_where_cinnamon_only_blanks_the_screen_it_is_not_called_locked(
        loginctl, screensaver, monkeypatch, tmp_path, actions_on, beeped, no_lock_here):
    """cinnamon-screensaver comes on for every Lock but locks only for a
    user who can lock (its user_can_lock): not one who logs in without a
    password, nor in a live or guest session. The screen there is blank
    and opens with a key press."""
    set_desktop(monkeypatch, "X-Cinnamon", "x11")
    screensaver.locks_after = 0
    no_lock_here(monkeypatch, tmp_path)
    system_actions.lock(True, sleep=no_wait)
    assert _seen_lock(screensaver)
    assert beeps.READY_BEEP not in beeped
    assert actions_on == ["Lock not confirmed"]


@pytest.mark.parametrize("screensaver", ["org.mate.ScreenSaver"], indirect=True)
def test_with_the_lock_screen_disabled_on_mate_it_is_not_called_locked(
        loginctl, screensaver, monkeypatch, tmp_path, actions_on, beeped):
    """mate-screensaver ignores Lock while the lockdown setting
    disable-lock-screen is on, but still comes on when the computer is idle:
    that coming on must not be taken for the lock."""
    set_desktop(monkeypatch, "MATE", "x11")
    _mate_lockdown(monkeypatch, tmp_path, disabled=True)
    screensaver.locks_after = 0
    system_actions.lock(True, sleep=no_wait)
    assert _seen_lock(screensaver)
    assert beeps.READY_BEEP not in beeped
    assert actions_on == ["Lock not confirmed"]


@pytest.mark.parametrize("enabled, confirmed", [(False, False), (True, True)])
@pytest.mark.parametrize("screensaver", ["org.xfce.ScreenSaver"], indirect=True)
def test_on_xfce_the_lock_is_confirmed_only_with_locking_on(
        loginctl, screensaver, an_ordinary_session, monkeypatch, actions_on, beeped,
        enabled, confirmed):
    """xfce4-screensaver ignores Lock while its /lock/enabled is off (Lock
    Screen off in its settings), but still comes on when idle."""
    set_desktop(monkeypatch, "XFCE", "x11")
    an_ordinary_session.values[("xfce4-screensaver", "/lock/enabled")] = GLib.Variant(
        "b", enabled)
    screensaver.locks_after = 0
    system_actions.lock(True, sleep=no_wait)
    assert (beeps.READY_BEEP in beeped) == confirmed
    assert actions_on == ([] if confirmed else ["Lock not confirmed"])


@pytest.mark.parametrize("screensaver", ["org.mate.ScreenSaver"], indirect=True)
def test_mate_settings_it_cant_read_mean_not_confirmed(loginctl, screensaver, monkeypatch,
                                                       actions_on, beeped):
    """No settings schemas to read: whether the lock screen is disabled
    can't be told, so its coming on isn't taken for the lock."""
    set_desktop(monkeypatch, "MATE", "x11")
    monkeypatch.setattr(Gio.SettingsSchemaSource, "get_default", staticmethod(lambda: None))
    screensaver.locks_after = 0
    system_actions.lock(True, sleep=no_wait)
    assert beeps.READY_BEEP not in beeped
    assert actions_on == ["Lock not confirmed"]


@pytest.mark.parametrize("screensaver", ["org.xfce.ScreenSaver"], indirect=True)
def test_xfce_with_no_screensaver_settings_yet_locks_as_by_default(
        loginctl, screensaver, an_ordinary_session, monkeypatch, actions_on, beeped):
    """Xfconf answers ChannelNotFound until a setting of the screensaver is
    changed: locking is on then, xfce4-screensaver's default."""
    set_desktop(monkeypatch, "XFCE", "x11")
    an_ordinary_session.channel_missing = True
    screensaver.locks_after = 0
    system_actions.lock(True, sleep=no_wait)
    assert (beeped, actions_on) == ([beeps.READY_BEEP], [])


@pytest.mark.parametrize("desktop", ["X-Cinnamon", "MATE", "XFCE"])
def test_without_its_screensaver_the_lock_goes_through_logind(loginctl, desktop, monkeypatch,
                                                              actions_on):
    """An Xfce with light-locker, say: no org.xfce.ScreenSaver on the bus."""
    set_desktop(monkeypatch, desktop, "x11")
    loginctl.hint = "yes"
    assert system_actions.lock(False, sleep=no_wait) is True
    assert loginctl.commands[0][:2] == ["loginctl", "lock-session"]
    assert actions_on == []
