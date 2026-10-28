"""The tray's single-instance lock.

Only a lock held by another tray means "already running". An error opening
the lock file (no runtime folder, no permission) must not make the tray exit
quietly, and a second tray must not wipe the first one's PID from the file.
A second tray says it is already running in a window, and a TalkType tray
from before the rename gives way to TuxTalk.
"""

import os
import signal
import subprocess
import sys
import textwrap

import pytest

from tuxtalk import service_launcher, single_instance, tray_instance


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(single_instance, "runtime_dir", lambda: str(tmp_path))
    saved = tray_instance._lockfile_handle
    yield tmp_path
    if tray_instance._lockfile_handle is not None and tray_instance._lockfile_handle is not saved:
        tray_instance._lockfile_handle.close()
    tray_instance._lockfile_handle = saved


def test_the_first_tray_takes_the_lock_and_records_its_pid(lock_dir):
    tray_instance.acquire()
    assert (lock_dir / "talktype-tray.lock").read_text() == str(os.getpid())


def test_a_second_tray_exits_and_leaves_the_pid_alone(lock_dir, monkeypatch):
    shown = []
    monkeypatch.setattr(tray_instance, "_show_already_running",
                        lambda older_talktype: shown.append(older_talktype))
    monkeypatch.setattr(service_launcher, "pre_rename_tray_pids", lambda: [])
    monkeypatch.setattr(service_launcher, "QUIT_WAIT_S", 0.0)      # not a quitting one
    monkeypatch.setattr(tray_instance, "_TRAY_QUIT_EXTRA_S", 0.2)
    tray_instance.acquire()
    first = tray_instance._lockfile_handle
    try:
        with pytest.raises(SystemExit) as exit_info:
            tray_instance.acquire()
        assert exit_info.value.code == 0
        assert (lock_dir / "talktype-tray.lock").read_text() == str(os.getpid())
        # Started from the menu, a silent exit looks like nothing happened.
        assert shown == [False]
    finally:
        tray_instance._lockfile_handle = first


def test_an_unusable_runtime_folder_is_not_reported_as_already_running(monkeypatch, tmp_path):
    monkeypatch.setattr(single_instance, "runtime_dir", lambda: str(tmp_path / "missing"))
    tray_instance.acquire()   # must not raise SystemExit



def test_a_full_runtime_folder_is_not_reported_as_already_running(lock_dir, monkeypatch):
    """Writing the PID can fail (the runtime folder is a small tmpfs)."""
    def full(lockfile, wait_s=0.0):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(single_instance, "claim", full)
    tray_instance.acquire()   # must not raise

# A stand-in for a TalkType 0.7.0 tray: run as `python -m talktype.tray`, it
# opens the shared lock file the way 0.7.0 did and holds it.
_FAKE_OLD_TRAY = textwrap.dedent("""
    import fcntl, os, signal, sys, time
    if sys.argv[2] == "stubborn":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    lock = open(os.path.join(sys.argv[1], "talktype-tray.lock"), "w")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    lock.write(str(os.getpid()))
    lock.flush()
    print("ready", flush=True)
    time.sleep(60)
""")


@pytest.fixture
def old_tray(lock_dir, tmp_path_factory):
    """Start a fake pre-rename tray holding the lock; yields a starter."""
    if service_launcher.pre_rename_tray_pids():
        pytest.skip("a real pre-rename TalkType tray is running; never signal it")
    pkg = tmp_path_factory.mktemp("oldpkg")
    (pkg / "talktype").mkdir()
    (pkg / "talktype" / "__init__.py").write_text("")
    (pkg / "talktype" / "tray.py").write_text(_FAKE_OLD_TRAY)
    procs = []

    def start(mode="normal"):
        env = dict(os.environ, PYTHONPATH=str(pkg))
        proc = subprocess.Popen(
            [sys.executable, "-m", "talktype.tray", str(lock_dir), mode],
            env=env, stdout=subprocess.PIPE, text=True)
        assert proc.stdout.readline().strip() == "ready"
        procs.append(proc)
        return proc

    yield start
    for proc in procs:
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=5)
        proc.stdout.close()


def test_an_older_talktype_tray_is_stopped_and_tuxtalk_takes_the_lock(
        lock_dir, old_tray, monkeypatch):
    shown = []
    monkeypatch.setattr(tray_instance, "_show_already_running",
                        lambda older_talktype: shown.append(older_talktype))
    proc = old_tray()

    tray_instance.acquire()          # must not exit

    assert proc.wait(timeout=5) == -signal.SIGTERM
    assert shown == []
    assert (lock_dir / "talktype-tray.lock").read_text() == str(os.getpid())


def test_an_older_talktype_tray_that_wont_quit_is_named_in_the_notice(
        lock_dir, old_tray, monkeypatch):
    shown = []
    monkeypatch.setattr(tray_instance, "_show_already_running",
                        lambda older_talktype: shown.append(older_talktype))
    monkeypatch.setattr(tray_instance, "_OLD_TRAY_EXIT_WAIT_S", 0.3)
    proc = old_tray("stubborn")

    with pytest.raises(SystemExit) as exit_info:
        tray_instance.acquire()

    assert exit_info.value.code == 0
    assert shown == [True]
    assert proc.poll() is None              # never escalated past SIGTERM


def test_the_notice_says_how_to_quit_each_kind_of_tray():
    heading, body = tray_instance.already_running_message(older_talktype=True)
    assert "TalkType" in heading and "Quit TalkType" in body
    heading, body = tray_instance.already_running_message(older_talktype=False)
    assert heading == "TuxTalk is already running" and "Quit TuxTalk" in body


@pytest.mark.parametrize("argv, expected", [
    (["/tmp/.mount_x/usr/bin/python3", "-m", "talktype.tray"], True),
    (["python3", "-m", "tuxtalk.tray"], False),
    (["python3", "-m", "talktype.app"], False),
    (["bash", "-c", "pgrep -f '-m talktype.tray'"], False),
])
def test_only_a_pre_rename_tray_matches(argv, expected):
    assert service_launcher.is_pre_rename_tray_argv(argv) is expected
