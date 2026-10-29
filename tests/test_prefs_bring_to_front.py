"""A second "open Preferences" brings the open window to the front.

Doing nothing when Preferences is already open would leave a window hidden
behind a browser looking like Preferences had failed to open.
"""
import os
import signal

import pytest

from tuxtalk import prefs_ipc


def test_running_pid_reads_a_live_preferences_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert prefs_ipc.running_pid() is None                 # no pidfile

    monkeypatch.setattr(prefs_ipc, "_process_argv",
                        lambda pid: ["/usr/bin/python3", "-m", "tuxtalk.prefs"])
    (tmp_path / "talktype-prefs.pid").write_text(str(os.getpid()))
    assert prefs_ipc.running_pid() == os.getpid()


def test_a_stale_pid_is_not_trusted(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "talktype-prefs.pid").write_text("999999999")  # gone
    assert prefs_ipc.running_pid() is None


def test_a_reused_pid_is_not_trusted(tmp_path, monkeypatch):
    """A Preferences killed without its cleanup leaves its pid behind. Once
    another program has that pid, counting any live pid would make the tray
    send it SIGUSR1, whose default action ends the process. This test run's
    own pid stands in for that other program."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "talktype-prefs.pid").write_text(str(os.getpid()))
    assert prefs_ipc.running_pid() is None


def test_preferences_is_recognised_by_whole_arguments():
    assert prefs_ipc.is_prefs_argv(["/usr/bin/python3", "-m", "tuxtalk.prefs", "--tab=stats"])
    assert prefs_ipc.is_prefs_argv(["/home/u/.local/bin/dictate-prefs"])
    assert prefs_ipc.is_prefs_argv(["/usr/bin/python3.12", "/home/u/.local/bin/dictate-prefs"])
    assert not prefs_ipc.is_prefs_argv(["bash", "-c", "pgrep -f tuxtalk.prefs"])
    assert not prefs_ipc.is_prefs_argv(["vim", "/home/u/.local/bin/dictate-prefs"])
    assert not prefs_ipc.is_prefs_argv([])


def test_a_pid_that_vanishes_while_checked_is_not_running(monkeypatch):
    """It exited between the existence check and reading its command line:
    treating that as "running" would make the new window exit and nothing open."""
    def gone(pid):
        raise FileNotFoundError
    monkeypatch.setattr(prefs_ipc, "_process_argv", gone)
    assert prefs_ipc.is_prefs_pid(4242) is False


def test_bring_to_front_passes_the_tab_through_a_request_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    sent = []
    monkeypatch.setattr(prefs_ipc.os, "kill", lambda pid, sig: sent.append((pid, sig)))

    assert prefs_ipc.bring_to_front(42, "stats") is True
    assert sent == [(42, signal.SIGUSR1)]
    assert prefs_ipc.take_tab_request() == "stats"
    assert prefs_ipc.take_tab_request() is None          # read once

    prefs_ipc.bring_to_front(42, "updates")
    prefs_ipc.bring_to_front(42)                         # plain "front" clears it
    assert prefs_ipc.take_tab_request() is None


def test_bring_to_front_reports_a_vanished_window(monkeypatch):
    def gone(pid, sig):
        raise ProcessLookupError
    monkeypatch.setattr(prefs_ipc.os, "kill", gone)
    assert prefs_ipc.bring_to_front(42) is False


def test_prefs_handles_the_signal_before_it_announces_its_pid(prefs_main):
    """SIGUSR1 terminates a process that doesn't handle it. The pidfile is how
    others find Preferences, so the handlers must be installed first."""
    started = prefs_main()
    assert started.index("handles bring-to-front") < started.index("announces its pid")


def test_a_second_launch_forwards_and_exits(tmp_path, monkeypatch):
    from tuxtalk import prefs
    pidfile = tmp_path / "talktype-prefs.pid"
    pidfile.write_text("4242")
    monkeypatch.setattr(prefs.prefs_ipc, "pidfile_path", lambda: str(pidfile))
    monkeypatch.setattr(prefs.prefs_ipc, "is_prefs_pid", lambda pid: True)
    sent = []
    monkeypatch.setattr(prefs.prefs_ipc, "bring_to_front",
                        lambda pid, tab: sent.append((pid, tab)) or True)   # it answered
    monkeypatch.setattr("tuxtalk.single_instance.runtime_dir", lambda: str(tmp_path))
    with pytest.raises(SystemExit):
        prefs._acquire_prefs_singleton("updates")
    assert sent == [(4242, "updates")]
    assert pidfile.read_text() == "4242"                   # the open one keeps it


@pytest.fixture
def one_window(tmp_path, monkeypatch):
    """Preferences' pidfile and lock in a folder of their own."""
    from tuxtalk import prefs, single_instance
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    monkeypatch.setattr(single_instance, "runtime_dir", lambda: str(run))
    pidfile = tmp_path / "talktype-prefs.pid"
    monkeypatch.setattr(prefs.prefs_ipc, "pidfile_path", lambda: str(pidfile))
    monkeypatch.setattr(single_instance, "FRONT_WAIT_S", 0.3)
    monkeypatch.setattr(single_instance, "CLOSING_WAIT_S", 0.3)
    return prefs, single_instance, pidfile


def test_two_quick_launches_open_one_window(one_window, monkeypatch):
    """The first had its lock but hadn't written its pid yet: the second saw
    no window and opened another."""
    prefs, single_instance, pidfile = one_window
    first = single_instance.open_lock(prefs.WINDOW_LOCK)
    assert single_instance.claim(first)               # the first launch, still starting
    sent = []

    def front(pid, tab):
        sent.append(pid)
        return True
    monkeypatch.setattr(prefs.prefs_ipc, "bring_to_front", front)
    monkeypatch.setattr(prefs.prefs_ipc, "is_prefs_pid", lambda pid: True)
    import threading                                  # ...which it writes a moment later
    threading.Timer(0.1, lambda: pidfile.write_text("4242")).start()
    with pytest.raises(SystemExit):
        prefs._acquire_prefs_singleton(None)
    assert sent == [4242]
    first.close()


def test_a_window_that_has_just_closed_doesnt_stop_the_next_from_opening(one_window,
                                                                        monkeypatch):
    """Its pid was still written but it had gone: the new one exited anyway,
    and no window appeared."""
    prefs, single_instance, pidfile = one_window
    pidfile.write_text("4242")
    monkeypatch.setattr(prefs.prefs_ipc, "is_prefs_pid", lambda pid: True)
    monkeypatch.setattr(prefs.prefs_ipc, "bring_to_front", lambda pid, tab: False)
    monkeypatch.setattr(prefs.atexit, "register", lambda fn: None)
    prefs._acquire_prefs_singleton(None)              # opens: no SystemExit
    assert pidfile.read_text().split()[0] == str(prefs.os.getpid())
    prefs._window_lock.close()


def test_a_tab_request_that_cant_be_written_still_brings_the_window_forward(monkeypatch,
                                                                            caplog):
    """The request is written whole or not at all; one that can't be written
    is logged, and the window still comes to the front, on its own tab."""
    import logging
    sent = []

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr("tuxtalk.config.write_text_atomic", refuse)
    monkeypatch.setattr(prefs_ipc.os, "kill", lambda pid, sig: sent.append(pid))
    with caplog.at_level(logging.INFO, logger=prefs_ipc.logger.name):
        assert prefs_ipc.bring_to_front(42, "updates") is True
    assert sent == [42] and "tab to open" in caplog.text


def test_a_closed_window_lets_the_next_one_open_while_its_process_lingers(one_window,
                                                                         monkeypatch):
    """An Apply restart can keep the process running some 40 s after the
    window closes; holding the lock till then made a new launch wait and
    exit with no window."""
    prefs, single_instance, pidfile = one_window
    monkeypatch.setattr(prefs.atexit, "register", lambda fn: None)
    prefs._acquire_prefs_singleton(None)                 # this window opens
    window = prefs.PreferencesWindow.__new__(prefs.PreferencesWindow)
    monkeypatch.setattr(window, "_stop_tests", lambda closing: None, raising=False)
    window._release()                                    # ...and closes; the process stays
    other = single_instance.open_lock(prefs.WINDOW_LOCK)
    assert single_instance.claim(other)                  # the next window can have it
    other.close()


def test_the_tray_asks_again_when_preferences_is_starting_or_closing(monkeypatch):
    """Its Preferences process was alive with no pid to signal: the click
    (Your Stats, say) was logged as "still opening" and lost."""
    import types
    from tuxtalk import service_launcher, tray
    launched = []
    t = tray.DictationTray.__new__(tray.DictationTray)
    t.preferences_process = types.SimpleNamespace(poll=lambda: None)   # alive
    monkeypatch.setattr(prefs_ipc, "running_pid", lambda: None)        # no pid yet
    monkeypatch.setattr(service_launcher, "launch_preferences",
                        lambda tab: launched.append(tab) or types.SimpleNamespace())
    t._launch_preferences(tab="stats")
    assert launched == ["stats"]


def test_a_lock_that_fails_otherwise_doesnt_stop_preferences_opening(one_window,
                                                                    monkeypatch):
    """flock can fail for reasons other than "held" (no locks on that disk);
    Preferences opens without the lock rather than crashing before its window."""
    prefs, single_instance, pidfile = one_window
    def no_locks(lockfile, wait_s=0.0):
        raise OSError(37, "No locks available")
    monkeypatch.setattr(single_instance, "claim", no_locks)
    monkeypatch.setattr(prefs.atexit, "register", lambda fn: None)
    prefs._acquire_prefs_singleton(None)                # opens: no exception, no exit
    assert pidfile.read_text().split()[0] == str(prefs.os.getpid())
    prefs._let_go_of_the_window()


def test_a_request_written_while_the_last_is_read_isnt_lost(tmp_path, monkeypatch):
    """The window read the request, then deleted the file: one written in
    between was deleted unread, and its tab never opened."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(prefs_ipc.os, "kill", lambda pid, sig: None)
    prefs_ipc.bring_to_front(42, "stats")
    real_open = open

    def open_and_get_another_request(path, *args, **kwargs):
        f = real_open(path, *args, **kwargs)
        prefs_ipc.bring_to_front(42, "updates")       # the next click, meanwhile
        return f
    monkeypatch.setattr("builtins.open", open_and_get_another_request)
    assert prefs_ipc.take_tab_request() == "stats"
    monkeypatch.setattr("builtins.open", real_open)
    assert prefs_ipc.take_tab_request() == "updates"
