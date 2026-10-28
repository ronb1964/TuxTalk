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
    monkeypatch.setattr(recording, "quit_asked", False)
    monkeypatch.setattr(app, "_service_ready", True)
    app._handle_sigterm(15, None)
    app._handle_sigterm(15, None)               # a second one returns too
    assert recording.quit_asked is True


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


def test_sigterm_while_the_model_loads_ends_at_once(monkeypatch):
    """Nothing to finish yet, and the input loop that would take the
    request isn't running."""
    monkeypatch.setattr(app, "_service_ready", False)
    with pytest.raises(SystemExit):
        app._handle_sigterm(15, None)
