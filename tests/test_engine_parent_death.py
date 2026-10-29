"""An engine dies with the thread that started it, set up without running
Python between fork and exec.

The engines hold a gigabyte or more each. If the dictation service is
killed, the kernel must kill them too; and setting that up from preexec_fn,
in a service full of threads, could hang an engine's start.
"""
import os
import shutil
import subprocess
import sys
import threading
import time

import pytest

from tuxtalk import engine_process


def _start_from_a_thread_that_ends(argv, signal_is_set):
    """Start *argv* from a thread that exits once *signal_is_set(proc)*, as
    the service's launcher thread does when the service dies. (A thread that
    ended before the signal was set would leave the process to the main
    thread, which lives on; the launcher thread lives as long as its engine.)"""
    started = []

    def launch():
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE)
        started.append(proc)
        signal_is_set(proc)
    thread = threading.Thread(target=launch)
    thread.start()
    thread.join()
    return started[0]


def _became_sleep(proc):
    """setpriv sets the signal, then becomes the program it starts."""
    for _ in range(500):
        with open(f"/proc/{proc.pid}/comm") as f:
            if f.read().strip() == "sleep":
                return
        time.sleep(0.01)


def _killed_within(proc, seconds=5):
    deadline = time.monotonic() + seconds
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    if proc.poll() is None:
        proc.kill()
        proc.wait()
    proc.stdout.close()
    return proc.returncode == -9 and time.monotonic() < deadline


@pytest.fixture(autouse=True)
def _ask_setpriv_afresh():
    engine_process._setpriv.cache_clear()
    yield
    engine_process._setpriv.cache_clear()


@pytest.mark.skipif(not shutil.which("setpriv"), reason="no setpriv on this computer")
def test_an_engine_is_killed_when_the_thread_that_started_it_ends():
    proc = _start_from_a_thread_that_ends(engine_process.with_parent_death(["sleep", "30"]),
                                          _became_sleep)
    assert _killed_within(proc)


def test_the_parakeet_helper_sets_its_own_parent_death_signal():
    proc = _start_from_a_thread_that_ends(
        [sys.executable, "-c", "from tuxtalk.parakeet_gpu_worker import die_with_parent; "
                               "die_with_parent(); print('set', flush=True); "
                               "import time; time.sleep(30)"],
        lambda proc: proc.stdout.readline())
    assert _killed_within(proc)


def test_without_setpriv_the_engine_starts_as_it_is(monkeypatch):
    """The next start's kill_orphans is the backstop then."""
    monkeypatch.setattr(engine_process.shutil, "which", lambda name: None)
    assert engine_process.with_parent_death(["llama-server", "-m", "m.gguf"]) == [
        "llama-server", "-m", "m.gguf"]


def test_a_setpriv_too_old_for_pdeathsig_isnt_used(monkeypatch, tmp_path):
    """util-linux before 2.33 has setpriv but refuses --pdeathsig: using it
    would stop every engine from starting."""
    old = tmp_path / "setpriv"
    old.write_text("#!/bin/sh\necho 'setpriv: unrecognized option' >&2\nexit 1\n")
    old.chmod(0o755)
    monkeypatch.setattr(engine_process.shutil, "which", lambda name: str(old))
    assert engine_process.with_parent_death(["whisper-server"]) == ["whisper-server"]


def test_setpriv_is_asked_once(monkeypatch):
    runs = []
    monkeypatch.setattr(engine_process.shutil, "which", lambda name: "/usr/bin/setpriv")
    monkeypatch.setattr(engine_process.subprocess, "run", lambda *a, **k: runs.append(a))
    engine_process.with_parent_death(["a"])
    engine_process.with_parent_death(["b"])
    assert len(runs) == 1
    assert os.path.basename(engine_process.with_parent_death(["c"])[0]) == "setpriv"
