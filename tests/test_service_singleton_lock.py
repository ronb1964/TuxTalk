"""The service's single-instance lock.

An error opening the lock file is not "another copy is already running" with
a success exit code, which would send anyone debugging the wrong way. And
opening the file must not wipe the running instance's PID before the lock is
even tried.
"""

import os

import pytest

from tuxtalk import app, single_instance


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(single_instance, "runtime_dir", lambda: str(tmp_path))
    saved = app._lockfile_handle
    yield tmp_path
    if app._lockfile_handle is not None and app._lockfile_handle is not saved:
        app._lockfile_handle.close()
    app._lockfile_handle = saved


def test_the_first_instance_takes_the_lock_and_records_its_pid(lock_dir):
    app._acquire_single_instance()
    assert (lock_dir / "talktype.lock").read_text() == str(os.getpid())


def test_a_second_instance_exits_and_leaves_the_pid_alone(lock_dir, monkeypatch):
    monkeypatch.setattr(app, "LOCK_WAIT_S", 0.0)
    app._acquire_single_instance()
    first = app._lockfile_handle
    try:
        with pytest.raises(SystemExit) as exit_info:
            app._acquire_single_instance()
        assert exit_info.value.code == 0
        assert (lock_dir / "talktype.lock").read_text() == str(os.getpid())
    finally:
        app._lockfile_handle = first


def test_an_unusable_runtime_folder_is_not_reported_as_already_running(monkeypatch, tmp_path):
    monkeypatch.setattr(single_instance, "runtime_dir", lambda: str(tmp_path / "missing"))
    app._acquire_single_instance()   # must not raise SystemExit



def test_a_full_runtime_folder_is_not_reported_as_already_running(lock_dir, monkeypatch):
    """Writing the PID can fail (the runtime folder is a small tmpfs)."""
    def full(lockfile, wait_s=0.0):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(single_instance, "claim", full)
    app._acquire_single_instance()   # must not raise


def test_a_restarted_service_waits_for_the_old_one_to_finish(lock_dir):
    """A restart starts the new service while the old one still types what
    it was given (service_tick._quit): it found the lock held and exited,
    and dictation stayed off."""
    import threading
    old = single_instance.open_lock("talktype.lock")
    assert single_instance.claim(old)
    threading.Timer(0.3, old.close).start()         # the old service ends
    app._acquire_single_instance()                  # waits, then takes it
    assert (lock_dir / "talktype.lock").read_text() == str(os.getpid())
