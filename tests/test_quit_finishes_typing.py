"""Quit, Restart Service and an update stop the dictation service with
SIGTERM. Unhandled, it ended the process at once: recordings not yet
transcribed were lost without a word, and its exit hooks (music resumed,
sounds turned back up) never ran. Now it finishes what it was given first."""
import os
import signal
import subprocess
import sys
import threading
import time
import types

import pytest

from tuxtalk import app, dictation_worker, pipeline, recording, service_launcher, service_tick
from tuxtalk import recording_state as rs
from tuxtalk.dictation_worker import DictationWorker


@pytest.fixture
def alarms(monkeypatch):
    """The quit deadlines the handler set; a real one would end the test run."""
    armed = []
    monkeypatch.setattr(app.signal, "alarm", armed.append)
    monkeypatch.setattr(recording, "quit_asked", False)
    return armed


@pytest.fixture
def quitting(monkeypatch):
    monkeypatch.setattr(recording, "quit_asked", True)
    cfg = types.SimpleNamespace(beeps=False, smart_quotes=False, notify=False, language=None,
                                auto_space=True, auto_period=True, injection_mode="type")
    return cfg, types.SimpleNamespace(last_activity=0.0)


def test_quitting_types_what_the_worker_still_holds_first(quitting, monkeypatch):
    cfg, svc = quitting
    worker = DictationWorker()
    monkeypatch.setattr(dictation_worker, "_instance", worker)
    typing, may_finish, typed = threading.Event(), threading.Event(), []

    def dictation():
        typing.set()
        assert may_finish.wait(5)
        typed.append("See you at noon.")
    worker.submit(dictation)
    assert typing.wait(5)
    threading.Timer(0.2, may_finish.set).start()
    with pytest.raises(SystemExit):
        service_tick.tick(cfg, svc)
    assert typed == ["See you at noon."]


def test_a_recording_under_way_is_transcribed_before_the_quit_ends(quitting, monkeypatch):
    """Hands-free still listening when Quit was clicked: what was said goes
    through the real stop (pipeline.stop_recording) to transcription, typing
    and Recent Dictations (transcribe_and_inject) before the process ends,
    as letting go of the key would, instead of dying with it."""
    cfg, svc = quitting
    monkeypatch.setattr(dictation_worker, "_instance", DictationWorker())
    monkeypatch.setattr(rs.state, "is_recording", True)
    monkeypatch.setattr("tuxtalk.keyboard_grab.release_all", lambda: None)
    monkeypatch.setattr(recording, "finish_capture", lambda beeps, notify: (["what was said"], 16000))
    transcribed = []

    def transcribe(frames, rate, *a, **k):
        time.sleep(0.3)                 # a dictation takes a moment to transcribe
        transcribed.append((frames, rate))
    monkeypatch.setattr(pipeline, "transcribe_and_inject", transcribe)
    with pytest.raises(SystemExit):
        service_tick.tick(cfg, svc)
    assert transcribed == [(["what was said"], 16000)]


def test_sigterm_asks_the_loop_to_quit_with_a_deadline_set_once(alarms, monkeypatch):
    """A second SIGTERM (a restart and a quit together) must not push the
    deadline out, or a stuck service would outlive every wait for it."""
    monkeypatch.setattr(app, "_service_ready", True)
    app._handle_sigterm(15, None)
    app._handle_sigterm(15, None)
    assert recording.quit_asked is True and alarms == [service_launcher.QUIT_LIMIT_S]


def test_a_quit_that_hangs_is_ended_by_the_kernel_at_its_deadline(tmp_path):
    """A hung exit hook (a stuck audio stream, a player that never answers)
    kept the old service alive, and with it the lock the new one waited
    for. The real handler, in a process of its own, with a 1 s deadline."""
    script = (
        "import atexit, os, signal, sys, time\n"
        "import tuxtalk.app as app\n"
        "app.QUIT_LIMIT_S = 1\n"
        "app._service_ready = True\n"
        "signal.signal(signal.SIGTERM, app._handle_sigterm)\n"
        "atexit.register(time.sleep, 60)\n"
        "os.kill(os.getpid(), signal.SIGTERM)\n"
        "sys.exit(0)\n")
    started = time.monotonic()
    done = subprocess.run([sys.executable, "-c", script], env=dict(os.environ),
                          capture_output=True, timeout=30)
    assert done.returncode == -signal.SIGALRM, done.stderr.decode()
    assert time.monotonic() - started < 15


def test_work_still_unfinished_at_the_end_is_said(quitting, monkeypatch):
    """Only a log line said a dictation was dropped."""
    cfg, svc = quitting
    told = []
    monkeypatch.setattr(dictation_worker, "drain", lambda timeout: False)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body: told.append(title))
    with pytest.raises(SystemExit):
        service_tick.tick(cfg, svc)
    assert told == ["A dictation wasn't typed"]


def test_the_signals_are_handled_before_the_lock_is_waited_for(monkeypatch):
    """A restart's tray toggles the new service by its PID while it still
    waits for the old one's lock: SIGUSR1 ended it, and dictation stayed off."""
    order = []
    monkeypatch.setattr(app, "_install_signal_handlers", lambda: order.append("signals"))
    monkeypatch.setattr(app, "_acquire_single_instance", lambda: order.append("lock"))
    monkeypatch.setattr(app, "load_config", lambda: (_ for _ in ()).throw(SystemExit))
    monkeypatch.setattr("tuxtalk.desktop_identity.apply", lambda: None)
    monkeypatch.setattr("tuxtalk.ui_style.prepare_windows", lambda: None)
    with pytest.raises(SystemExit):
        app._load_settings()
    assert order == ["signals", "lock"]


def test_sigterm_while_the_model_loads_ends_at_once_with_the_deadline_set(alarms, monkeypatch):
    """Nothing to finish yet, and the input loop that would take the
    request isn't running. The deadline still holds: a model load in native
    code can't be interrupted by the exception until it returns."""
    monkeypatch.setattr(app, "_service_ready", False)
    with pytest.raises(SystemExit):
        app._handle_sigterm(15, None)
    assert alarms == [service_launcher.QUIT_LIMIT_S]
