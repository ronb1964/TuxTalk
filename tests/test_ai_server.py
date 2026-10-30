"""The local AI's server (ai_server): started through the launcher with its
key kept off the command line, orphans from an earlier run killed, and a
server that died started again."""
import io

import pytest

from tuxtalk import ai_download, ai_server, engine_process

ASKED = [{"role": "user", "content": "one, no wait, two"}]


def test_orphaned_engines_are_killed_but_live_ones_are_left_alone(monkeypatch):
    """Engines left by an earlier run (parent gone, adopted by systemd) are
    killed when a new engine starts; ones owned by a live TuxTalk are not."""
    import signal as _signal
    killed = []
    monkeypatch.setattr(ai_server, "_engine_processes", lambda: [(100, 1), (200, 50), (300, ai_server.os.getpid())])
    monkeypatch.setattr(engine_process, "is_talktype_process", lambda pid: pid == 50)
    monkeypatch.setattr(ai_server.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    assert ai_server.kill_orphaned_engines() == 1
    assert killed == [(100, _signal.SIGKILL)]


def test_an_engine_left_to_the_flatpak_sandbox_is_killed(monkeypatch):
    """In the Flatpak an orphan's new parent is the sandbox's pid 1, bwrap,
    whose command line names TuxTalk: it isn't TuxTalk all the same."""
    import signal as _signal
    killed = []
    monkeypatch.setattr(ai_server, "_engine_processes", lambda: [(100, 1)])
    monkeypatch.setattr(engine_process, "is_talktype_process", lambda pid: True)
    monkeypatch.setattr(ai_server.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    assert ai_server.kill_orphaned_engines() == 1
    assert killed == [(100, _signal.SIGKILL)]


def test_the_engine_starts_through_the_launcher_with_its_own_libraries(dead_engine, monkeypatch):
    """The launcher makes it die with the service (test_engine_parent_death),
    never Python code run between fork and exec."""
    options = []
    monkeypatch.setattr(ai_server.subprocess, "Popen",
                        lambda argv, **k: options.append((argv, k)) or _Proc())
    dead_engine.start(_replacing=dead_engine._proc)
    argv, kwargs = options[0]
    assert argv[1] == engine_process.engine_launcher.__file__
    assert argv[argv.index("--") + 1] == ai_download.server_binary()
    assert kwargs["env"][engine_process.engine_launcher.ENGINE_LIBRARY_PATH] == "/x"
    assert "preexec_fn" not in kwargs
    assert kwargs["stderr"] is ai_server.subprocess.PIPE       # read, to explain a failed start


def test_the_engine_key_is_not_on_its_command_line(dead_engine, monkeypatch):
    """Every user of the computer can read a process's command line; with the
    key from there, another user could talk to the engine."""
    calls = []
    monkeypatch.setattr(ai_server.subprocess, "Popen", lambda *a, **k: calls.append((a, k)) or _Proc())
    dead_engine.start(_replacing=dead_engine._proc)
    (argv,), options = calls[0]
    assert dead_engine._key not in " ".join(argv)
    assert "--api-key" not in argv
    assert options["env"]["LLAMA_API_KEY"] == dead_engine._key


# --- an engine that dies is started again ---------------------------------------------------

def _no_output():
    return engine_process.OutputTail(io.BytesIO())


class _Proc:
    def __init__(self, code=None, said=b""):
        self.returncode, self.pid = code, 42
        self.stderr = io.BytesIO(said)

    def poll(self):
        return self.returncode


@pytest.fixture
def dead_engine(monkeypatch):
    """A ready engine whose server has since exited, with starting faked."""
    e = ai_server.AIServer(warm_up=ASKED)
    e._proc, e._port, e._key = _Proc(code=-9), 1234, "k"
    e._ready.set()
    started = []
    monkeypatch.setattr(ai_download, "is_installed", lambda: True)
    monkeypatch.setattr(ai_server, "kill_orphaned_engines", lambda: 0)
    monkeypatch.setattr(ai_download, "server_binary", lambda: "/x/llama-server")
    monkeypatch.setattr(ai_download, "model_path", lambda: "/x/model.gguf")
    monkeypatch.setattr(ai_server.subprocess, "Popen", lambda *a, **k: started.append(a) or _Proc())
    monkeypatch.setattr(e, "_wait_until_ready", lambda proc, output: None)
    e.started = started
    return e


def test_turning_the_ai_off_while_it_starts_is_not_an_error(caplog):
    """stop() during the start-up wait: the engine went because it was told
    to, not "exited during startup", and it never counts as ready."""
    import logging
    e = ai_server.AIServer(warm_up=ASKED)
    starting = _Proc()
    e._proc = None                      # stop() ran
    with caplog.at_level(logging.INFO, logger=ai_server.logger.name):
        e._wait_until_ready(starting, _no_output(), limit=1)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not e._ready.is_set()


def test_an_engine_that_ends_while_starting_logs_what_it_printed(caplog):
    import logging
    e = ai_server.AIServer(warm_up=ASKED)
    dead = _Proc(code=1, said=b"llama: error loading model\n")
    e._proc = dead
    with caplog.at_level(logging.INFO, logger=ai_server.logger.name):
        e._wait_until_ready(dead, engine_process.OutputTail(dead.stderr), limit=1)
    assert any("exit code 1: llama: error loading model" in r.getMessage()
               for r in caplog.records if r.levelno == logging.ERROR)


def test_a_warm_up_without_an_answer_is_logged(monkeypatch, caplog):
    """Else a slow first correction has nothing in the log to explain it."""
    import logging
    e = ai_server.AIServer(warm_up=ASKED)
    e._proc, e._port = _Proc(), 1234
    monkeypatch.setattr(engine_process, "answers", lambda port: True)
    monkeypatch.setattr(e, "_post", lambda *a, **k: None)
    with caplog.at_level(logging.INFO, logger=ai_server.logger.name):
        e._wait_until_ready(e._proc, _no_output(), limit=1)
    assert any("warm-up" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)


def test_a_stop_during_the_warm_up_logs_no_warning(monkeypatch, caplog):
    """The warm-up gets no answer because the engine was stopped: nothing
    for the log to explain."""
    import logging
    e = ai_server.AIServer(warm_up=ASKED)
    starting = _Proc()
    e._proc, e._port = starting, 1234
    monkeypatch.setattr(engine_process, "answers", lambda port: True)

    def stopped_meanwhile(*a, **k):
        e._proc = None
        return None
    monkeypatch.setattr(e, "_post", stopped_meanwhile)
    with caplog.at_level(logging.INFO, logger=ai_server.logger.name):
        e._wait_until_ready(starting, _no_output(), limit=1)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not e._ready.is_set()


def test_a_server_that_died_is_started_again(dead_engine):
    """is_ready() notices the dead server, so corrections and edits don't fail all session."""
    assert dead_engine.ask(ASKED, 200, 6.0, "test") is None
    assert len(dead_engine.started) == 1
    assert dead_engine.is_ready() is False and len(dead_engine.started) == 1   # starting


def test_a_restart_from_a_dictation_thread_outlives_that_thread(dead_engine, monkeypatch):
    """The server dies with the thread that started it. Restarted on the
    dictation's own thread, it was killed as soon as that dictation ended,
    and AI corrections stayed off for the rest of the session."""
    import threading
    starters = []
    monkeypatch.setattr(ai_server.subprocess, "Popen", lambda *a, **k: starters.append(
        threading.current_thread().name) or _Proc())
    dictation = threading.Thread(target=dead_engine.ask, args=(ASKED, 200, 6.0, "test"),
                                 name="tt-inject")
    dictation.start()
    dictation.join()
    assert len(starters) == 1 and starters[0].startswith("ai-engine")


def test_a_stopped_engine_is_not_started_again(dead_engine):
    dead = dead_engine._proc
    dead_engine.stop()
    dead_engine.start(_replacing=dead)
    assert dead_engine.started == []
