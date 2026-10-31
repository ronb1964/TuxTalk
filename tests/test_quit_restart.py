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
from tuxtalk.control import WaitingStart
from tuxtalk import tray as tray_mod
from tuxtalk import tray_service


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
                self.gone = False

            def send(self, sig):
                if not self.gone:
                    procs.signal(self.pid, sig)

            def running(self):
                procs.find()
                return not self.gone and self.pid in procs.pids

            def close(self):
                pass
        monkeypatch.setattr(service_launcher, "find_service_pids", self.find)
        monkeypatch.setattr(service_launcher, "HeldProcess", Held)
        monkeypatch.setattr(service_launcher, "is_service_pid", lambda pid: pid in self.pids)
        monkeypatch.setattr(service_launcher, "release_desktop_hotkeys", lambda: None)
        monkeypatch.setattr(service_launcher.time, "monotonic", lambda: self.now)
        monkeypatch.setattr(service_launcher.time, "sleep", self.sleep)

    def find(self):
        for at in [t for t in self.appear if t <= self.now]:
            self.pids.append(self.appear.pop(at))
        return list(self.pids)

    def signal(self, pid, sig):
        self.sent.append((pid, sig))
        if ((sig == signal.SIGKILL and pid not in self.unkillable)
                or (sig == signal.SIGTERM and pid not in self.ignores_sigterm)):
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
    t._install_thread = None
    t._uninstalling = False
    t._waiting_start = WaitingStart()
    t._dictation_on = True
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
    """A service (PID 42) that takes until gone.set() to quit; where it was
    waited for."""
    gone, waited_on, running = threading.Event(), [], [42]

    def end(wait_s=None, pids=None):
        waited_on.append(threading.current_thread())
        assert gone.wait(10)
        running.clear()
        return True
    monkeypatch.setattr(service_launcher, "end_dictation_service", end)
    monkeypatch.setattr(service_launcher, "stop_dictation_service", lambda force=False: None)
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: list(running))
    return types.SimpleNamespace(gone=gone, waited_on=waited_on, running=running)


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


def test_the_desktop_gets_its_hotkeys_back_off_gtks_thread(monkeypatch):
    """After every stop the tray hands KWin and COSMIC the hotkeys back. On
    GTK's thread the menu froze while KWin took its time to answer."""
    on = []
    monkeypatch.setattr(service_launcher, "release_desktop_hotkeys",
                        lambda: on.append(threading.current_thread()))
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: [])
    t = _bare_tray(is_service_running=lambda: False)
    t._release_desktop_hotkeys().join(5)
    assert on and on[0] is not threading.main_thread()


def test_a_service_started_before_the_release_keeps_its_hotkeys(monkeypatch):
    """Dictation off, then on again before the release thread ran: handing
    KWin or COSMIC the keys back then undid the new service's claim, and F8
    typed stray characters in terminals until the next start."""
    released = []
    monkeypatch.setattr(service_launcher, "release_desktop_hotkeys",
                        lambda: released.append(True))
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: [43])  # the new one
    t = _bare_tray(is_service_running=lambda: False)     # none, when the tray looked
    t._release_desktop_hotkeys().join(5)
    assert released == []


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
    monkeypatch.setattr(uninstall, "_end_service",
                        lambda: service_launcher.end_dictation_service())
    monkeypatch.setattr(uninstall, "_tuxtalk_pids", lambda engines: [])
    monkeypatch.setattr(uninstall.ydotoold_leftovers, "stop_ydotoold_we_started", lambda: None)
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
    monkeypatch.setattr(uninstall.installation, "get_install_type", lambda: "deb")
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
                               shut_down=lambda: order.append("tray shuts down"),
                               preferences_open=lambda: False)
    monkeypatch.setattr(uninstall, "_close_preferences", lambda: True)
    assert uninstall.run(tray) is False
    assert order == ["tray stops", "service ends", "root step", "tray resumes",
                     "dictation back on"]


def test_a_restart_finishing_after_dictation_off_starts_nothing(slow_service):
    launched = []
    t = _bare_tray(_launch_service=lambda: launched.append(True),
                   update_status_and_menu=lambda repeat=True: False)
    t.restart_service(None)
    t._drop_pending_launches()              # Dictation off while it waited
    slow_service.gone.set()
    assert not _run_main_loop_until(lambda: launched, timeout=0.5)
    assert t._restarting is False


def test_an_automatic_restart_with_dictation_off_leaves_it_off(slow_service):
    """A Performance preset or the Parakeet Ultra download restarts
    dictation; after the user turned Dictation off, it turned it back on,
    hotkeys and all."""
    slow_service.running.clear()            # Dictation off
    launched = []
    t = _bare_tray(_launch_service=lambda: launched.append(True),
                   update_status_and_menu=lambda repeat=True: False)
    t.restart_if_on()
    assert not _run_main_loop_until(lambda: launched, timeout=0.3)
    assert slow_service.waited_on == [] and t._restarting is False


def test_restart_service_with_dictation_off_turns_it_on(slow_service):
    """The user's own Restart Service (the menu, the GNOME extension) is a
    request to have dictation running."""
    slow_service.running.clear()            # Dictation off
    slow_service.gone.set()                 # nothing to wait for
    launched = []
    t = _bare_tray(_launch_service=lambda: launched.append(True),
                   update_status_and_menu=lambda repeat=True: False)
    t.restart_service(None)
    assert _run_main_loop_until(lambda: launched)


def _tray_with_dbus(slow_service):
    """A bare tray and the D-Bus service Preferences reaches it through."""
    from tuxtalk.dbus_service import TuxTalkDBusService
    from tuxtalk.tray_dbus_app import TrayAppInstance
    launched = []
    t = _bare_tray(_launch_service=lambda: launched.append(True),
                   update_status_and_menu=lambda repeat=True: False)
    bus_side = TuxTalkDBusService.__new__(TuxTalkDBusService)
    bus_side.app = TrayAppInstance(t)
    return t, bus_side, launched


def test_an_automatic_restart_while_the_stopped_service_types_leaves_dictation_off(
        slow_service):
    """Dictation off while the old service still typed a long paragraph,
    then a Performance preset or a Preferences change: the restart saw that
    service still running, waited for it and started a new one, so Dictation
    came back on by itself."""
    from tuxtalk.dbus_service import TuxTalkDBusService
    t, bus_side, launched = _tray_with_dbus(slow_service)
    t.stop_service(None)                    # PID 42 is still typing
    t.restart_if_on()                       # a Performance preset
    TuxTalkDBusService.RestartIfOn(bus_side)    # Preferences after a change
    _run_main_loop_until(lambda: False, timeout=0.2)
    slow_service.gone.set()
    assert not _run_main_loop_until(lambda: launched, timeout=0.5)
    assert len(slow_service.waited_on) == 1     # the Stop's wait, no other
    assert t._restarting is False


def test_preferences_restart_restarts_while_dictation_is_on(slow_service):
    from tuxtalk.dbus_service import TuxTalkDBusService
    t, bus_side, launched = _tray_with_dbus(slow_service)
    TuxTalkDBusService.RestartIfOn(bus_side)
    _run_main_loop_until(lambda: slow_service.waited_on, timeout=2)
    slow_service.gone.set()
    assert _run_main_loop_until(lambda: launched)


def test_restart_service_after_dictation_off_turns_it_on_again(slow_service):
    """The user's own Restart Service still means "have dictation running"."""
    t, _bus_side, launched = _tray_with_dbus(slow_service)
    t.stop_service(None)
    slow_service.gone.set()
    _run_main_loop_until(lambda: len(slow_service.waited_on) == 1, timeout=2)
    t.restart_service(None)
    assert _run_main_loop_until(lambda: launched)
    assert t._dictation_on is True


@pytest.mark.parametrize("second, starts", [("restart_service", True),
                                            ("restart_if_on", False)])
def test_a_restart_queued_after_dictation_off(slow_service, second, starts):
    """Restart, Dictation off while it waits, then another restart queued
    behind the first. The user's Restart Service turns dictation on; an
    automatic one (a preset chosen meanwhile) leaves it off."""
    launched = []
    t = _bare_tray(_launch_service=lambda: launched.append(True),
                   update_status_and_menu=lambda repeat=True: False)
    t.restart_service(None)
    t._drop_pending_launches()              # Dictation off
    getattr(t, second)()                    # queued, while the first waits
    slow_service.gone.set()
    assert bool(_run_main_loop_until(lambda: launched, timeout=0.5)) is starts
    assert _run_main_loop_until(lambda: not t._restarting)


def test_an_action_queued_before_quit_does_nothing_after_it(monkeypatch):
    """Quit and Open Preferences together from the GNOME extension:
    Preferences opened over a TuxTalk that was going away."""
    from tuxtalk.tray_dbus_app import TrayAppInstance
    opened = []
    t = _bare_tray(open_preferences=lambda _: opened.append(True))
    TrayAppInstance(t).show_preferences()   # queued
    t._quitting = True                      # Quit runs first
    _run_main_loop_until(lambda: False, timeout=0.2)
    assert opened == []


def test_quitting_refuses_the_menu_and_the_command_line(monkeypatch):
    """TuxTalk opened again while quitting waits for it to go, and
    tuxtalk --toggle says it's quitting instead of starting a service."""
    from tuxtalk.dbus_service import TuxTalkDBusService
    app = types.SimpleNamespace(quitting=True, show_menu=lambda: None)
    service = TuxTalkDBusService.__new__(TuxTalkDBusService)
    service.app = app
    assert TuxTalkDBusService.ShowMenu(service) is False
    t = _bare_tray(_quitting=True)
    assert t.control("toggle") == "quitting"


def test_a_reused_pid_is_never_signalled_without_pidfds(monkeypatch):
    """With no pidfds (an old kernel, a sandbox), the service exited and its
    PID went to another program, which the escalation then killed."""
    sent = []
    starts = {42: "1000"}
    monkeypatch.setattr(service_launcher.os, "pidfd_open",
                        lambda pid: (_ for _ in ()).throw(OSError(38, "no pidfds")))
    monkeypatch.setattr(service_launcher, "_start_time", lambda pid: starts.get(pid))
    monkeypatch.setattr(service_launcher.os, "kill", lambda pid, sig: sent.append(sig))
    proc = service_launcher.HeldProcess(42)
    proc.send(signal.SIGTERM)
    starts[42] = "2000"                     # gone, and the PID taken by another program
    proc.send(signal.SIGKILL)
    assert sent == [signal.SIGTERM] and not proc.running()


def test_stop_ends_only_the_services_running_when_it_was_chosen(monkeypatch):
    """Dictation off, then on before the wait began: the new service was
    found by the wait and stopped with the old one."""
    chosen = []
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: [42])
    monkeypatch.setattr(service_launcher, "end_in_background",
                        lambda then, pids: chosen.append(list(pids)))
    t = _bare_tray()
    t.stop_service(None)
    assert chosen == [[42]]


def test_dictation_off_drops_a_start_waiting_for_the_model(monkeypatch):
    """tuxtalk --start while the model loaded, then Dictation off, then on:
    the new service started recording when it was ready."""
    monkeypatch.setattr(service_launcher, "end_in_background", lambda then, pids: None)
    t = _bare_tray()
    t._waiting_start.apply("start")
    t.stop_service(None)
    assert not t._waiting_start.take()


def test_a_cancelled_uninstall_keeps_preferences_open(monkeypatch):
    """Preferences, with edits not applied yet, closed as the uninstall
    began; the password prompt was cancelled and the edits were gone."""
    closed = []
    t = _bare_tray(preferences_process=types.SimpleNamespace(
        poll=lambda: None, terminate=lambda: closed.append(True)))
    t._stop_taking_actions()                # TrayParts.stop
    assert closed == []
    t._resume_after_uninstall = lambda: None
    t._stop_kwin_focus_provider = lambda: None
    t._shut_down_for_uninstall()            # TrayParts.shut_down, once it goes ahead
    assert closed == [True]


def test_setup_isnt_started_when_the_running_service_wont_stop(monkeypatch):
    shown, quit = [], []
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: [42])
    monkeypatch.setattr(service_launcher, "end_in_background", lambda then, pids: then(False))
    monkeypatch.setattr(tray_service, "show_message", lambda *a, **k: shown.append(a[2]))
    t = _bare_tray(quit_app=lambda _: quit.append(True))
    started = []
    t.onboard_once_service_gone(lambda: started.append(True))
    assert started == [] and quit == [True] and shown == ["TuxTalk couldn't start its setup"]


def test_quit_inside_a_dialog_waits_for_the_first_run_copy(monkeypatch):
    """The fallback exit for a Quit made inside a dialog cut the first-run
    copy into ~/AppImages off."""
    from tuxtalk import appimage_copy
    timers, exits = [], []
    monkeypatch.setattr(tray_service, "GLib", types.SimpleNamespace(
        timeout_add=lambda ms, fn: timers.append(fn)))
    monkeypatch.setattr(tray_service, "Gtk", types.SimpleNamespace(main_quit=lambda: None))
    monkeypatch.setattr(tray_service.os, "_exit", exits.append)
    copying = {"now": True}
    monkeypatch.setattr(appimage_copy, "copying", lambda: copying["now"])
    t = _bare_tray(_install_thread=None)
    t._leave_main_loop()
    assert timers[0]() is True and exits == []      # still copying: looks again
    copying["now"] = False
    timers[0]()
    assert exits == [0]


def test_quit_inside_a_dialog_waits_for_an_update_going_in(monkeypatch):
    """Download & Install, then Quit inside a dialog: the fallback exit cut
    the update the user asked for off halfway."""
    from tuxtalk import appimage_copy
    timers = []
    monkeypatch.setattr(tray_service, "GLib", types.SimpleNamespace(
        timeout_add=lambda ms, fn: timers.append(fn)))
    monkeypatch.setattr(tray_service, "Gtk", types.SimpleNamespace(main_quit=lambda: None))
    monkeypatch.setattr(tray_service.os, "_exit", lambda code: None)
    monkeypatch.setattr(appimage_copy, "copying", lambda: False)
    t = _bare_tray(_install_thread=types.SimpleNamespace(is_alive=lambda: True))
    t._leave_main_loop()
    assert timers[0]() is True


def test_an_update_that_goes_in_after_quit_doesnt_start_tuxtalk_again(monkeypatch):
    from tuxtalk import update_install
    started = []
    monkeypatch.setattr(update_install, "restart_via_launcher",
                        lambda target=None: started.append(target) or (True, ""))
    t = _bare_tray(_quitting=True)
    t.restart_app()
    assert started == []


def test_an_uninstall_without_the_tray_holds_its_lock(tmp_path, monkeypatch):
    """With no tray (the command line, or Preferences with none running),
    Preferences restarting the service on Apply started a new one while the
    package was removed. Both hold the tray's lock while they work."""
    from tuxtalk import uninstall
    monkeypatch.setattr(single_instance, "runtime_dir", lambda: str(tmp_path))
    monkeypatch.setattr(uninstall.installation, "get_install_type", lambda: "deb")
    monkeypatch.setattr(uninstall, "find", lambda *a, **k: types.SimpleNamespace(needs_root=True))
    monkeypatch.setattr(uninstall, "_ask", lambda plan: (False, False, False))
    monkeypatch.setattr(uninstall, "_working_window",
                        lambda: types.SimpleNamespace(destroy=lambda: None))
    held = []
    monkeypatch.setattr(uninstall, "_remove_root_part", lambda plan, leave, tray: held.append(
        single_instance.held_elsewhere(tray_instance.TRAY_LOCK)))
    uninstall.run()
    assert held == [True]       # flock is per open file: this process's other one sees it


def test_preferences_restart_waits_for_an_uninstall(tmp_path, monkeypatch, started):
    """The other half: a restart from Preferences with no tray waits for the
    lock, and starts nothing while an uninstall holds it."""
    from tuxtalk import prefs
    monkeypatch.setattr(single_instance, "runtime_dir", lambda: str(tmp_path))
    monkeypatch.setattr(service_launcher, "QUIT_WAIT_S", 0.2)
    launched = []
    monkeypatch.setattr(service_launcher, "end_dictation_service", lambda: True)
    monkeypatch.setattr(service_launcher, "launch_dictation_service",
                        lambda: launched.append(1) or started["now"])
    with single_instance.open_lock(tray_instance.TRAY_LOCK) as uninstalling:
        assert single_instance.claim(uninstalling)
        assert prefs._restart_without_tray() is False    # owed: a tray keeps the old one
    assert launched == []
    assert prefs._restart_without_tray() is True
    assert launched == [1]


class _Started:
    """A launched service: it gets ready after *checks* looks, or ends
    after that many (ready=False), or neither (checks=None)."""

    pid = 4242

    def __init__(self, ready=True, checks=0):
        self.ready, self.checks, self.returncode = ready, checks, None

    def wait(self, timeout):
        if self.checks is not None and not self.ready:
            if self.checks == 0:
                self.returncode = 1
                return 1
            self.checks -= 1
        raise subprocess.TimeoutExpired("tuxtalk.app", timeout)

    def ready_pid(self):
        if self.ready and self.checks is not None:
            if self.checks == 0:
                return self.pid
            self.checks -= 1
        return None


@pytest.fixture
def started(monkeypatch):
    """What launching the service gives, with the ready file it writes,
    and a Preferences window still open (another test may have closed one)."""
    from tuxtalk import prefs
    monkeypatch.setattr(prefs, "_window_closed", threading.Event())
    service = {"now": _Started()}
    monkeypatch.setattr(service_launcher, "launch_dictation_service", lambda: service["now"])
    monkeypatch.setattr(service_launcher, "read_ready_pid", lambda: service["now"].ready_pid())
    return service


@pytest.mark.parametrize("ended,ready,checks,restarted", [
    (True, True, 3, True),          # up once its model loaded
    (True, False, 0, False),        # ended at once: a broken setting
    (True, False, 3, False),        # ended loading its model
    (True, True, None, None),       # still loading: asking to download?
    (False, True, 0, False)])       # the old one wouldn't go
def test_preferences_restart_says_whether_it_worked(tmp_path, monkeypatch, started, ended,
                                                    ready, checks, restarted):
    from tuxtalk import prefs
    monkeypatch.setattr(single_instance, "runtime_dir", lambda: str(tmp_path))
    monkeypatch.setattr(prefs, "_READY_WAIT_S", 2)
    monkeypatch.setattr(service_launcher, "end_dictation_service", lambda: ended)
    started["now"] = _Started(ready, checks)
    assert prefs._restart_without_tray() is restarted


def test_a_closed_preferences_stops_waiting_for_the_service(tmp_path, monkeypatch, started):
    """No one is left to tell, and a process still waiting looked to an
    uninstall like an open Preferences window with changes in it."""
    import threading
    from tuxtalk import prefs
    monkeypatch.setattr(single_instance, "runtime_dir", lambda: str(tmp_path))
    monkeypatch.setattr(service_launcher, "end_dictation_service", lambda: True)
    closed = threading.Event()
    closed.set()
    monkeypatch.setattr(prefs, "_window_closed", closed)
    started["now"] = _Started(checks=None)        # would load for the whole wait
    began = time.monotonic()
    assert prefs._restart_without_tray() is None
    assert time.monotonic() - began < 5


@pytest.mark.parametrize("answer,goes_ahead,saved", [
    ("apply", True, True), ("discard", True, False), ("keep", False, False)])
def test_uninstall_asks_about_changes_not_applied(answer, goes_ahead, saved):
    """An edited snippet, not applied yet, vanished when the uninstall closed
    Preferences (settings kept): it asks first, as closing the window does."""
    from tuxtalk.prefs_updates import UpdatesTab
    done = []
    win = types.SimpleNamespace(
        _has_unapplied_changes=lambda: True, _ask_apply_before_closing=lambda: answer,
        save_before_restart=lambda: done.append("saved") or True)
    tab = UpdatesTab.__new__(UpdatesTab)
    tab.win = win
    assert tab._settle_unapplied_changes() is goes_ahead
    assert (done == ["saved"]) is saved


def _uninstall_up_to_the_root_step(monkeypatch, told):
    """uninstall.run with its question answered and its windows stood in for."""
    from tuxtalk import uninstall
    monkeypatch.setattr(uninstall.installation, "get_install_type", lambda: "deb")
    monkeypatch.setattr(uninstall, "find", lambda *a, **k: types.SimpleNamespace(needs_root=True))
    monkeypatch.setattr(uninstall, "_ask", lambda plan: (False, False, False))
    monkeypatch.setattr(uninstall, "_working_window",
                        lambda: types.SimpleNamespace(destroy=lambda: None))
    monkeypatch.setattr(uninstall, "_wait_for", lambda work: work())
    monkeypatch.setattr(uninstall, "_tell", lambda kind, heading, body, **k: told.append(heading))
    monkeypatch.setattr(uninstall, "_remove_root_part",
                        lambda *a: pytest.fail("went on to remove things"))
    return uninstall


def test_an_uninstall_waits_for_its_turn_or_removes_nothing(monkeypatch):
    """Behind a restart from Preferences that ran long, the uninstall went
    ahead without the lock, and the restart started a service under it."""
    told = []
    uninstall = _uninstall_up_to_the_root_step(monkeypatch, told)
    monkeypatch.setattr(uninstall, "_hold_tray_lock", lambda: False)
    assert uninstall.run() is False and told == ["TuxTalk couldn't uninstall"]


def test_an_uninstall_removes_nothing_while_preferences_stays_open(monkeypatch):
    """Preferences closes as its own close does, asking about changes not
    applied; Keep Editing stops the uninstall, so nothing is lost."""
    told = []
    uninstall = _uninstall_up_to_the_root_step(monkeypatch, told)
    monkeypatch.setattr(uninstall, "_hold_tray_lock", lambda: None)
    monkeypatch.setattr("tuxtalk.prefs_ipc.ask_to_close", lambda wait_s: False)     # Keep Editing
    assert uninstall.run() is False and told == ["Uninstall stopped"]


def _stand_in_preferences(on_close):
    """A process standing in for a Preferences window: on CLOSE it ends
    ("asks and closes"), ignores it ("keep editing") or has no handler at
    all ("older", which CLOSE would end without asking)."""
    handler = {"asks and closes": "signal.signal(signal.SIGUSR2, lambda *a: sys.exit(0))\n",
               "keep editing": "signal.signal(signal.SIGUSR2, signal.SIG_IGN)\n",
               "older": ""}[on_close]
    code = ("import signal, sys, time\n" + handler
            + "print('ready', flush=True)\ntime.sleep(30)\n")
    window = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert window.stdout.readline() == "ready\n"
    return window


@pytest.mark.parametrize("on_close,advertised,closed", [
    ("asks and closes", True, True), ("keep editing", True, False), ("older", False, False)])
def test_preferences_is_asked_to_close_and_waited_for(on_close, advertised, closed,
                                                      tmp_path, monkeypatch):
    """An older Preferences (no "close" in its pidfile) is never sent CLOSE,
    which would end it with its changes: the uninstall stops instead."""
    from tuxtalk import prefs_ipc
    monkeypatch.setattr(single_instance, "runtime_dir", lambda: str(tmp_path))
    window = _stand_in_preferences(on_close)
    try:
        (tmp_path / "talktype-prefs.pid").write_text(
            f"{window.pid} close" if advertised else str(window.pid))
        monkeypatch.setattr(prefs_ipc, "is_prefs_pid", lambda pid: pid == window.pid)
        assert prefs_ipc.ask_to_close(wait_s=1) is closed
        assert (window.poll() is not None) is closed        # an older one is left running
    finally:
        window.kill()
        window.wait()
        window.stdout.close()


def test_preferences_that_runs_the_uninstall_takes_no_edits_meanwhile(monkeypatch):
    """With no tray, Preferences runs the uninstall itself; an edit made
    while it waited for the password was thrown away at the end."""
    from tuxtalk import control, uninstall
    from tuxtalk.prefs_updates import UpdatesTab
    states = []

    class Window:
        _busy = False

        def _has_unapplied_changes(self):
            return False

        def busy(self):
            import contextlib

            @contextlib.contextmanager
            def guard():
                self._busy = True
                yield
                self._busy = False
            return guard()

        def on_cancel(self, _):
            states.append("closed")
    tab = UpdatesTab.__new__(UpdatesTab)
    tab.win = Window()
    monkeypatch.setattr(control, "open_in_running", lambda name: None)
    monkeypatch.setattr(uninstall, "run", lambda: states.append(tab.win._busy) or True)
    tab._on_uninstall_clicked(None)
    assert states == [True, "closed"]


def test_no_menu_window_opens_once_quit_has_begun():
    t = _bare_tray(_quitting=True)
    assert t.show_menu_window_if_no_tray() is False


def test_an_uninstall_stops_while_a_preferences_window_it_couldnt_reach_is_open(monkeypatch):
    """An older Preferences, or one whose pidfile couldn't be written: the
    close request never reached it, and the sweep after the package was
    removed ended it with its changes."""
    from tuxtalk import prefs_ipc, uninstall
    monkeypatch.setattr(prefs_ipc, "ask_to_close", lambda wait_s: True)   # no pidfile
    monkeypatch.setattr(prefs_ipc, "open_windows", lambda: [4242])
    monkeypatch.setattr(uninstall, "_is_dev", lambda pid: False)
    assert uninstall._close_preferences() is False
    monkeypatch.setattr(uninstall, "_is_dev", lambda pid: True)             # a dev copy's
    assert uninstall._close_preferences() is True


def test_a_package_update_installs_nothing_after_quit(monkeypatch):
    """Downloaded while the last dictation was typed after Quit: it started
    the password prompt and the installer anyway, and showed its result."""
    from tuxtalk import update_ui
    installed, shown = [], []
    monkeypatch.setattr(update_ui.update_install, "get_package_asset",
                        lambda release, kind: ("https://x/tuxtalk.deb", "tuxtalk.deb"))
    monkeypatch.setattr(update_ui.update_install, "download_update", lambda *a, **k: "/tmp/x.deb")
    monkeypatch.setattr(update_ui.update_install, "install_package_update",
                        lambda path, kind: installed.append(path) or (True, ""))
    monkeypatch.setattr(update_ui, "_installed_restart_prompt", lambda cb=None: shown.append(1))
    monkeypatch.setattr(update_ui, "_error", lambda message: shown.append(message))
    from test_update_restart import _progress_in_place
    _progress_in_place(monkeypatch)
    update_ui.run_package_update({}, "deb", quitting=lambda: True)
    assert installed == [] and shown == []


def test_preferences_closes_on_the_uninstalls_request_as_its_own_close_does(monkeypatch):
    """CLOSE reaches the window's own close (which asks about changes not
    applied), and is handled before the pidfile tells anyone where to send it."""
    import dbus.mainloop.glib
    from tuxtalk import desktop_identity, prefs, prefs_ipc, ui_style
    handlers, steps = {}, []

    class Window:
        def on_window_close(self, widget, event):
            steps.append("asks and closes")

    def handle(priority, sig, fn, *args):
        handlers[sig] = lambda: fn(*args)
        steps.append(f"handles {sig.name}")
    monkeypatch.setattr(prefs.GLib, "unix_signal_add", handle)
    monkeypatch.setattr(prefs, "_acquire_prefs_singleton", lambda tab: steps.append("pidfile"))
    monkeypatch.setattr(prefs, "PreferencesWindow", Window)
    monkeypatch.setattr(prefs.Gtk, "main", lambda: None)
    monkeypatch.setattr(desktop_identity, "apply", lambda: None)
    monkeypatch.setattr(ui_style, "prepare_windows", lambda: None)
    monkeypatch.setattr(dbus.mainloop.glib, "DBusGMainLoop", lambda **k: None)
    monkeypatch.setattr(sys, "argv", ["tuxtalk-prefs"])
    prefs.main()
    assert steps.index(f"handles {prefs_ipc.CLOSE.name}") < steps.index("pidfile")
    assert handlers[prefs_ipc.CLOSE]() is True          # kept for a second request
    assert steps[-1] == "asks and closes"


def test_every_preferences_window_of_the_users_is_found_whatever_the_pidfile(monkeypatch):
    """Found in /proc: an older window, or one that couldn't write its
    pidfile, is never ended by an uninstall without its changes asked about."""
    from tuxtalk import prefs_ipc
    procs = {
        "1": (1000, ["python3", "-m", "tuxtalk.prefs"]),           # this process
        "42": (1000, ["/usr/bin/python3", "-m", "talktype.prefs"]),  # an older window
        "43": (1001, ["python3", "-m", "tuxtalk.prefs"]),          # another user's
        "44": (1000, ["python3", "-c", "job()", "-m", "tuxtalk.prefs"]),
        "45": (1000, ["/opt/tuxtalk/bin/dictate-prefs"]),
    }
    monkeypatch.setattr(prefs_ipc.os, "listdir", lambda path: [*procs, "self"])
    monkeypatch.setattr(prefs_ipc.os, "stat",
                        lambda path: types.SimpleNamespace(st_uid=procs[path.split("/")[2]][0]))
    monkeypatch.setattr(prefs_ipc.os, "getuid", lambda: 1000)
    monkeypatch.setattr(prefs_ipc.os, "getpid", lambda: 1)
    monkeypatch.setattr("tuxtalk.service_launcher.read_argv", lambda pid: procs[str(pid)][1])
    assert sorted(prefs_ipc.open_windows()) == [42, 45]


def test_quit_waits_for_an_update_going_in_but_not_for_its_download():
    """Only the install runs on a thread a Quit's fallback exit waits for:
    with the download on it too, a slow connection kept the old TuxTalk
    and its lock alive for minutes after Quit."""
    seen = {}
    t = _bare_tray()

    def install():
        seen["daemon"] = threading.current_thread().daemon
        seen["tracked"] = t._install_thread is threading.current_thread()
        return True, ""
    # Called, as in use, from the download's worker, which is a daemon: a
    # thread it starts is one too unless told otherwise.
    result = []
    worker = threading.Thread(target=lambda: result.append(t._install_not_cut_off(install)),
                              daemon=True)
    worker.start()
    worker.join(5)
    assert result == [(True, "")]
    assert seen == {"daemon": False, "tracked": True}
