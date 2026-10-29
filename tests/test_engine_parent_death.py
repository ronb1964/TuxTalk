"""An engine dies with the thread that started it, set up without running
Python between fork and exec.

The engines hold a gigabyte or more each. If the dictation service is
killed, the kernel must kill them too; and setting that up from preexec_fn,
in a service full of threads, could hang an engine's start. Every engine
starts through engine_launcher, a process of its own; these tests start
real ones.
"""
import os
import subprocess
import threading
import time

from tuxtalk import engine_launcher, engine_process


def _start_from_a_thread_that_ends(argv, env=None):
    """Start the engine *argv* from a thread that exits once the launcher
    has become it, as the service's launcher thread does when the service
    dies. (The launcher thread lives as long as its engine.)"""
    command, env = engine_process.parent_death_command(argv, env)
    started = []

    def launch():
        proc = subprocess.Popen(command, env=env, stdout=subprocess.PIPE)
        started.append(proc)
        name = os.path.basename(argv[0])[:15]
        for _ in range(500):
            with open(f"/proc/{proc.pid}/comm") as f:
                if f.read().strip() == name:
                    return
            time.sleep(0.01)
    thread = threading.Thread(target=launch)
    thread.start()
    thread.join()
    return started[0]


def _killed_within(proc, seconds=5):
    deadline = time.monotonic() + seconds
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    if proc.poll() is None:
        proc.kill()
        proc.wait()
    proc.stdout.close()
    return proc.returncode == -9 and time.monotonic() < deadline


def test_an_engine_is_killed_when_the_thread_that_started_it_ends():
    assert _killed_within(_start_from_a_thread_that_ends(["/bin/sleep", "30"]))


def test_a_launcher_whose_service_died_first_starts_nothing(tmp_path):
    """Had the service died before the signal was set, it would never fire:
    the launcher sees it has another parent and stops there."""
    marker = tmp_path / "ran"
    command, env = engine_process.parent_death_command(["/bin/touch", str(marker)])
    command[command.index(str(os.getpid()))] = "1"
    assert subprocess.run(command, env=env).returncode == engine_launcher.PARENT_GONE
    assert not marker.exists()


def test_the_engine_gets_its_own_libraries_and_the_launcher_the_services(monkeypatch):
    """The launcher is Python, which may need the service's LD_LIBRARY_PATH
    (the AppImage's); the engine needs its own folder's."""
    monkeypatch.setenv("LD_LIBRARY_PATH", "/service/libs")
    command, env = engine_process.parent_death_command(
        ["/bin/sh", "-c", 'echo "$LD_LIBRARY_PATH"'],
        {**os.environ, "LD_LIBRARY_PATH": "/engine/libs", "LLAMA_API_KEY": "k"})
    assert env["LD_LIBRARY_PATH"] == "/service/libs" and env["LLAMA_API_KEY"] == "k"
    out = subprocess.run(command, env=env, capture_output=True, text=True).stdout
    assert out.strip() == "/engine/libs"


def test_an_engine_that_cant_start_says_why(tmp_path):
    command, env = engine_process.parent_death_command([str(tmp_path / "missing-server")])
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == engine_launcher.NO_ENGINE
    assert "missing-server" in result.stderr
    assert engine_process.launch_failure(result.returncode) == (
        ", because its program couldn't be started")
    assert engine_process.launch_failure(-9) == ""
