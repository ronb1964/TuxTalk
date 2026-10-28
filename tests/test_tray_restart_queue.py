"""A tray restart asked for while another is running is queued, not dropped.

Dropping it would mean a model change saved in that window never reaches the
running service, though the menu and config.toml show the new model.
"""


import pytest

from tuxtalk import tray as tray_mod


class FakeGLib:
    """Collects main-loop callbacks so the test decides when each one runs."""

    def __init__(self):
        self.idle, self.timers = [], []

    def idle_add(self, fn, *args):
        self.idle.append(lambda: fn(*args))

    def timeout_add_seconds(self, _secs, fn, *args):
        self.timers.append(lambda: fn(*args))


@pytest.fixture
def fake_loop(monkeypatch):
    loop = FakeGLib()
    monkeypatch.setattr(tray_mod, "GLib", loop)
    return loop


def make_tray(monkeypatch, loop):
    t = tray_mod.DictationTray.__new__(tray_mod.DictationTray)
    t.__dict__.update(onboarding_in_progress=False, _quitting=False, _service_gen=0,
                      _restarting=False, _restart_pending=False, kills=0, launches=0)

    def end_service(then):
        """The old service ends at once; the tray hears so on its loop."""
        t.kills += 1
        loop.idle_add(then, True)
    monkeypatch.setattr("tuxtalk.service_launcher.end_in_background", end_service)
    t._launch_service = lambda: setattr(t, "launches", t.launches + 1)
    t.update_status_and_menu = lambda repeat=True: None
    return t


def run_finish(loop):
    for cb in loop.idle:
        cb()
    loop.idle.clear()
    timers, loop.timers = loop.timers, []
    for cb in timers:
        cb()


def test_a_restart_asked_for_during_another_runs_after_it(fake_loop, monkeypatch):
    t = make_tray(monkeypatch, fake_loop)
    t.restart_service(None)
    t.restart_service(None)          # arrives while the first is still running
    assert t.kills == 1
    run_finish(fake_loop)            # first finishes, the queued one starts
    assert t.kills == 2
    run_finish(fake_loop)
    assert t.launches == 2 and not t._restarting and fake_loop.timers == []


def test_a_single_restart_does_not_repeat(fake_loop, monkeypatch):
    t = make_tray(monkeypatch, fake_loop)
    t.restart_service(None)
    run_finish(fake_loop)
    assert t.kills == 1 and t.launches == 1 and fake_loop.timers == []

