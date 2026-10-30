"""An engine dies with the thread that started it, set up without running
Python between fork and exec.

The engines hold a gigabyte or more each. If the dictation service is
killed, the kernel must kill them too; and setting that up from preexec_fn,
in a service full of threads, could hang an engine's start. Every engine
starts through engine_launcher, a process of its own; these tests start
real ones.
"""
import os
import signal
import subprocess
import sys
import threading
import time

from tuxtalk import engine_launcher, engine_process

# Standard modules of the AppImage's Python 3.10 that later ones dropped
# (PEP 594 and distutils, imp, asynchat, asyncore, smtpd).
_STANDARD_IN_THE_APPIMAGES_PYTHON = {
    "aifc", "asynchat", "asyncore", "audioop", "binhex", "cgi", "cgitb", "chunk", "crypt",
    "distutils", "imghdr", "imp", "lib2to3", "mailcap", "msilib", "nis", "nntplib",
    "ossaudiodev", "pipes", "smtpd", "sndhdr", "spwd", "sunau", "telnetlib", "uu", "xdrlib"}


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


def _ended_with(argv, env=None):
    """launch_failure for *argv* started through the launcher, once it ends."""
    command, launch_env = engine_process.parent_death_command(argv, env)
    proc = subprocess.Popen(command, env=launch_env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    output = engine_process.OutputTail(proc.stderr)
    proc.wait(timeout=30)
    return engine_process.launch_failure(proc.returncode, output)


def test_a_server_that_ends_while_starting_says_what_it_printed():
    said = _ended_with([sys.executable, "-c",
                        "import sys; print('no Vulkan device', file=sys.stderr); sys.exit(3)"])
    assert said == " (exit code 3: no Vulkan device)"


def test_a_server_that_prints_a_lot_is_never_held_up_and_its_last_lines_kept():
    """More than a pipe holds (64 KB) before it ends: read as it goes, or
    the server would block on its own output and never end."""
    said = _ended_with([sys.executable, "-c",
                        "import sys\n"
                        "for i in range(20000): print(f'loading layer {i}', file=sys.stderr)\n"
                        "print('out of memory', file=sys.stderr); sys.exit(4)"])
    assert said.startswith(" (exit code 4: loading layer 19996 |")
    assert said.endswith("loading layer 19999 | out of memory)")


def test_one_endless_line_is_kept_to_a_few_pieces():
    said = _ended_with([sys.executable, "-c",
                        "import sys; sys.stderr.write('x' * 1_000_000); sys.exit(5)"])
    assert len(said) < 6 * engine_process.OutputTail.LINE_BYTES


def test_a_launcher_python_cant_run_says_why():
    """Before the launcher's own code runs (a broken Python setup), its
    message went nowhere and the log said only that the engine stopped."""
    said = _ended_with(["/bin/true"], {**os.environ, "PYTHONHOME": "/nonexistent"})
    assert said.startswith(" (exit code 1: ") and "encodings" in said


def test_a_program_that_cant_be_started_says_what_the_launcher_saw(tmp_path):
    said = _ended_with([str(tmp_path / "missing-server")])
    assert said.startswith(", because its program couldn't be started: ")
    assert "missing-server" in said


def test_a_server_killed_by_a_signal_says_so():
    said = _ended_with([sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"])
    assert said == " (killed by SIGKILL)"


def test_a_real_time_signal_is_named_by_its_number():
    """Those have no name of their own: naming one mustn't fail."""
    rt = signal.SIGRTMIN + 6
    said = _ended_with([sys.executable, "-c", f"import os; os.kill(os.getpid(), {int(rt)})"])
    assert said == f" (killed by signal {int(rt)})"


def test_a_character_split_between_two_reads_comes_through_whole():
    """The 500-byte read ends inside the first é's two bytes."""
    said = _ended_with([sys.executable, "-c",
                        "import sys; sys.stderr.buffer.write(b'x' * 499 + 'été'.encode())"])
    assert "\ufffd" not in said
    assert said.endswith(" | été)")


def test_an_engine_starts_where_tuxtalks_own_start_up_would_fail(tmp_path):
    """The launcher imports nothing of TuxTalk: the package's start-up opens
    the log and moves old folders, and a log that can't be written mustn't
    stop an engine. Here the package can be imported; its log can't open."""
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    command, env = engine_process.parent_death_command(["/bin/true"])
    env["TUXTALK_LOG_DIR"] = str(blocker / "logs")
    assert subprocess.run(command, env=env, timeout=30).returncode == 0


def test_an_engine_gets_sigpipe_and_sigxfsz_as_any_program_does():
    """Python ignores both; an engine started as Popen would start it mustn't."""
    command, env = engine_process.parent_death_command(["/bin/cat", "/proc/self/status"])
    status = subprocess.run(command, env=env, capture_output=True, text=True).stdout
    ignored = int(next(line for line in status.splitlines()
                       if line.startswith("SigIgn:")).split()[1], 16)
    assert not ignored & (1 << (13 - 1) | 1 << (25 - 1))     # SIGPIPE, SIGXFSZ


def test_no_module_beside_the_launcher_hides_a_standard_one():
    """Run by its path, the launcher's folder comes first on sys.path: a
    tuxtalk/signal.py would be imported in place of Python's own."""
    folder = os.path.dirname(engine_launcher.__file__)
    ours = {name.removesuffix(".py") for name in os.listdir(folder)}
    assert not ours & (set(sys.stdlib_module_names) | _STANDARD_IN_THE_APPIMAGES_PYTHON)
