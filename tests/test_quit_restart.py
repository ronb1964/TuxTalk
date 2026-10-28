"""Quitting and restarting: who waits for the dictation service, and where.

The service types what it holds before it quits (test_quit_finishes_typing).
Whoever needs it gone waits for that off GTK's thread, so the menu and the
uninstall window never freeze; the tray outlives it, since from an AppImage
the service's files go with the tray.
"""
import os
import signal
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest
from gi.repository import GLib

from tuxtalk import service_launcher, single_instance, tray_instance
from tuxtalk import tray as tray_mod


class Processes:
    """The services find_service_pids sees, held as end_processes holds
    them, on a clock its waits advance."""

    def __init__(self, monkeypatch, pids, ignores_sigterm=(), unkillable=()):
        self.pids = list(pids)
        self.ignores_sigterm = set(ignores_sigterm)
        self.unkillable = set(unkillable)
        self.sent = []
        self.now = 0.0
        self.appear = {}            # time -> pid started then (Dictation on again)
        procs = self

        class Held:
            def __init__(self, pid):
                self.pid = pid

            def send(self, sig):
                procs.signal(self.pid, sig)

            def running(self):
                procs.find()
                return self.pid in procs.pids

            def close(self):
                pass
        monkeypatch.setattr(service_launcher, "find_service_pids", self.find)
        monkeypatch.setattr(service_launcher, "_Process", Held)
        monkeypatch.setattr(service_launcher, "release_desktop_hotkeys", lambda: None)
        monkeypatch.setattr(service_launcher.time, "monotonic", lambda: self.now)
        monkeypatch.setattr(service_launcher.time, "sleep", self.sleep)

    def find(self):
        for at in [t for t in self.appear if t <= self.now]:
            self.pids.append(self.appear.pop(at))
        return list(self.pids)

    def signal(self, pid, sig):
        self.sent.append((pid, sig))
        if sig == signal.SIGKILL and pid not in self.unkillable:
            self.pids.remove(pid)
        elif sig == signal.SIGTERM and pid not in self.ignores_sigterm:
            self.pids.remove(pid)

    def sleep(self, s):
        self.now += s


def test_a_service_that_quits_is_waited_for_and_never_killed(monkeypatch):
    procs = Processes(monkeypatch, [42])
    assert service_launcher.end_dictation_service() is True
    assert procs.sent == [(42, signal.SIGTERM)]


def test_a_service_started_while_waiting_is_left_alone(monkeypatch):
    """Dictation off, then on again while the old service still types: the
    new one must not be stopped (or killed) along with the old."""
    procs = Processes(monkeypatch, [42], ignores_sigterm=[42])
    procs.appear[1.0] = 43
    assert service_launcher.end_dictation_service(wait_s=5) is True
    assert [pid for pid, _sig in procs.sent] == [42, 42] and procs.pids == [43]


def test_a_service_still_there_after_the_wait_is_killed(monkeypatch):
    """An older service has no deadline of its own."""
    procs = Processes(monkeypatch, [42], ignores_sigterm=[42])
    assert service_launcher.end_dictation_service(wait_s=5) is True
    assert procs.sent == [(42, signal.SIGTERM), (42, signal.SIGKILL)]
    assert procs.now >= 5


def test_a_service_that_survives_sigkill_is_reported(monkeypatch):
    Processes(monkeypatch, [42], ignores_sigterm=[42], unkillable=[42])
    assert service_launcher.end_dictation_service(wait_s=1) is False


def test_a_real_process_is_held_until_it_ends():
    """Signalled through its pidfd, and seen gone once it exits, though as
    our child it stays a zombie until reaped."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert service_launcher.end_processes([child.pid], wait_s=5) is True
        assert child.poll() == -signal.SIGTERM
    finally:
        child.kill()
        child.wait()


# ---- the tray --------------------------------------------------------------

class Indicator:
    def __init__(self):
        self.status = None

    def set_status(self, status):
        self.status = status


def _bare_tray(**attrs):
    """A tray with what Quit and Restart Service touch, and no desktop."""
    t = tray_mod.DictationTray.__new__(tray_mod.DictationTray)
    t.onboarding_in_progress = False
    t._service_gen = 0
    t._restarting = False
    t._restart_pending = False
    t._quitting = False
    t._uninstalling = False
    t._menu_window = None
    t.preferences_process = None
    t.dbus_service = None
    t.indicator = Indicator()
    t._stop_kwin_focus_provider = lambda: None
    t.__dict__.update(attrs)
    return t


def _run_main_loop_until(done, timeout=5):
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not done() and time.monotonic() < deadline:
        ctx.iteration(False)
        time.sleep(0.01)
    return done()


@pytest.fixture
def slow_service(monkeypatch):
    """A service that takes until gone.set() to quit; where it was waited for."""
    gone, waited_on = threading.Event(), []

    def end(wait_s=None):
        waited_on.append(threading.current_thread())
        assert gone.wait(10)
        return True
    monkeypatch.setattr(service_launcher, "end_dictation_service", end)
    monkeypatch.setattr(service_launcher, "stop_dictation_service", lambda force=False: None)
    return types.SimpleNamespace(gone=gone, waited_on=waited_on)


def test_quit_returns_at_once_and_leaves_once_the_service_has_gone(slow_service):
    """The tray waited for the service on GTK's thread: menus, windows and
    D-Bus froze for as long as the last dictation took to type."""
    left = []
    t = _bare_tray(_leave_main_loop=lambda: left.append(True))
    started = time.monotonic()
    t.quit_app(None)
    assert time.monotonic() - started < 1
    assert t.indicator.status == tray_mod.AppIndicator3.IndicatorStatus.PASSIVE
    assert not _run_main_loop_until(lambda: left, timeout=0.3)    # still typing
    slow_service.gone.set()
    assert _run_main_loop_until(lambda: left)
    assert slow_service.waited_on[0] is not threading.main_thread()


def test_a_second_quit_while_quitting_does_nothing(slow_service):
    shut_downs = []
    t = _bare_tray(_leave_main_loop=lambda: None)
    t._hide_for_quit = lambda: shut_downs.append(True) or setattr(t, "_quitting", True)
    t.quit_app(None)
    t.quit_app(None)
    slow_service.gone.set()
    assert shut_downs == [True] and len(slow_service.waited_on) == 1


def test_restart_service_starts_the_new_one_once_the_old_one_has_gone(slow_service, monkeypatch):
    """The old one typed what it held while the new one already ran."""
    launched = []
    t = _bare_tray(_launch_service=lambda: launched.append(True),
                   update_status_and_menu=lambda repeat=True: False)
    t.restart_service(None)
    assert not _run_main_loop_until(lambda: launched, timeout=0.3)
    slow_service.gone.set()
    assert _run_main_loop_until(lambda: launched)
    assert t._restarting is False


# ---- a new tray while the old one quits ------------------------------------

def _hold_tray_lock(tmp_path, seconds):
    """Another process holding the tray's lock for *seconds*."""
    code = ("import fcntl, sys, time\n"
            "f = open(sys.argv[1], 'w')\n"
            "fcntl.flock(f, fcntl.LOCK_EX)\n"
            "print('held', flush=True)\n"
            "time.sleep(float(sys.argv[2]))\n")
    holder = subprocess.Popen([sys.executable, "-c", code,
                               str(tmp_path / tray_instance.TRAY_LOCK), str(seconds)],
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline() == "held\n"
    return holder


@pytest.fixture
def quitting_tray(tmp_path, monkeypatch):
    monkeypatch.setattr(single_instance, "runtime_dir", lambda: str(tmp_path))
    monkeypatch.setattr("tuxtalk.service_launcher.pre_rename_tray_pids", lambda: [])
    monkeypatch.setattr("tuxtalk.control.open_in_running", lambda *a, **k: False)
    told = []
    monkeypatch.setattr(tray_instance, "_show_already_running", lambda **k: told.append(k))
    return told


def test_opened_again_while_quitting_it_starts_once_the_old_one_is_gone(tmp_path, quitting_tray):
    """It said "already running" with no icon anywhere: the old one had
    let go of its D-Bus service and was waiting for its service to finish."""
    holder = _hold_tray_lock(tmp_path, 0.5)
    lockfile = single_instance.open_lock(tray_instance.TRAY_LOCK)
    try:
        tray_instance._give_way_or_exit(lockfile)    # returns: this tray goes on starting
        assert quitting_tray == []
    finally:
        lockfile.close()
        holder.wait()


def test_a_tray_that_doesnt_go_is_still_reported_running(tmp_path, quitting_tray, monkeypatch):
    monkeypatch.setattr(tray_instance, "_TRAY_QUIT_EXTRA_S", 0.0)
    monkeypatch.setattr("tuxtalk.service_launcher.QUIT_WAIT_S", 0.3)
    holder = _hold_tray_lock(tmp_path, 30)
    lockfile = single_instance.open_lock(tray_instance.TRAY_LOCK)
    try:
        with pytest.raises(SystemExit):
            tray_instance._give_way_or_exit(lockfile)
        assert len(quitting_tray) == 1
    finally:
        lockfile.close()
        holder.kill()
        holder.wait()


# ---- the service with no tray ----------------------------------------------

@pytest.mark.parametrize("action", ["stop_service", "quit"])
def test_with_no_tray_the_service_stops_itself(action, monkeypatch):
    """Dictation off from the GNOME extension with no tray running asked
    every *other* service to stop, and this one went on."""
    from tuxtalk import service_dbus
    sent = []
    monkeypatch.setattr(service_dbus.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    instance = service_dbus.AppInstance.__new__(service_dbus.AppInstance)
    getattr(instance, action)()
    assert sent == [(os.getpid(), signal.SIGTERM)]


# ---- uninstall -------------------------------------------------------------

def test_uninstall_waits_for_the_service_while_its_window_draws(slow_service, monkeypatch):
    """It waited on GTK's thread (the "Uninstalling" window couldn't draw)
    and killed the service after 3 s, mid-dictation."""
    from tuxtalk import uninstall
    monkeypatch.setattr(uninstall, "_end_service", lambda: service_launcher.end_dictation_service())
    monkeypatch.setattr(uninstall, "_tuxtalk_pids", lambda engines: [])
    monkeypatch.setattr(uninstall.uinput_helper, "stop_ydotoold_we_started", lambda: None)
    plan = types.SimpleNamespace(home=Path("/nonexistent-home"), ydotoold_unit=None)
    ticks = []

    def tick():
        ticks.append(True)
        if len(ticks) == 3:
            slow_service.gone.set()
        return True
    source = GLib.timeout_add(20, tick)
    try:
        uninstall.stop_tuxtalk(plan)
    finally:
        GLib.source_remove(source)
    assert len(ticks) >= 3 and slow_service.waited_on[0] is not threading.main_thread()


def test_uninstall_stops_the_tray_and_service_before_the_root_step(monkeypatch):
    """A restart queued in the tray started a new service while the package
    was being removed; cancelled at the password prompt, everything is as
    it was, dictation included."""
    from tuxtalk import uninstall
    order = []
    monkeypatch.setattr(uninstall.update_checker, "get_install_type", lambda: "deb")
    monkeypatch.setattr(uninstall, "find", lambda *a, **k: types.SimpleNamespace(needs_root=True))
    monkeypatch.setattr(uninstall, "_ask", lambda plan: (False, False, False))
    monkeypatch.setattr(uninstall, "_working_window",
                        lambda: types.SimpleNamespace(destroy=lambda: None))
    monkeypatch.setattr(uninstall, "_wait_for", lambda work: work())
    monkeypatch.setattr(uninstall, "_end_service", lambda: order.append("service ends") or True)
    monkeypatch.setattr(uninstall, "_remove_as_root",
                        lambda plan, leave_group: order.append("root step") and None)
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: [42])
    monkeypatch.setattr(service_launcher, "launch_dictation_service",
                        lambda: order.append("dictation back on"))
    tray = uninstall.TrayParts(stop=lambda: order.append("tray stops"),
                               resume=lambda: order.append("tray resumes"),
                               shut_down=lambda: order.append("tray shuts down"))
    assert uninstall.run(tray) is False
    assert order == ["tray stops", "service ends", "root step", "tray resumes",
                     "dictation back on"]
