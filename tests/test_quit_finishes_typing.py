"""Quit, Restart Service and an update stop the dictation service with
SIGTERM. Unhandled, it ended the process at once: recordings not yet
transcribed were lost without a word, and its exit hooks (music resumed,
sounds turned back up) never ran. Now it finishes what it was given first."""
import threading
import types

import pytest

from tuxtalk import app, dictation_worker, pipeline, recording, service_tick
from tuxtalk import recording_state as rs
from tuxtalk.dictation_worker import DictationWorker


@pytest.fixture
def quitting(monkeypatch):
    monkeypatch.setattr(recording, "quit_requested", threading.Event())
    recording.quit_requested.set()
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


def test_a_recording_under_way_is_stopped_and_kept(quitting, monkeypatch):
    """Hands-free still listening when Quit was clicked: its words go to
    Recent Dictations and are typed, as letting go of the key would."""
    cfg, svc = quitting
    stopped = []
    monkeypatch.setattr(rs.state, "is_recording", True)
    monkeypatch.setattr("tuxtalk.keyboard_grab.release_all", lambda: None)
    monkeypatch.setattr(pipeline, "stop_recording", lambda *a, **k: stopped.append(True))
    with pytest.raises(SystemExit):
        service_tick.tick(cfg, svc)
    assert stopped == [True]


def test_sigterm_asks_the_loop_to_quit_once_dictation_runs(monkeypatch):
    monkeypatch.setattr(recording, "quit_requested", threading.Event())
    monkeypatch.setattr(app, "_service_ready", True)
    app._handle_sigterm(15, None)
    assert recording.quit_requested.is_set()


def test_sigterm_while_the_model_loads_ends_at_once(monkeypatch):
    """Nothing to finish yet, and the input loop that would take the
    request isn't running."""
    monkeypatch.setattr(app, "_service_ready", False)
    with pytest.raises(SystemExit):
        app._handle_sigterm(15, None)
