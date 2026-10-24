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


def test_a_second_instance_exits_and_leaves_the_pid_alone(lock_dir):
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

