"""Preferences must restart the service only when the change requires it.

Restarting unconditionally on Apply/OK would make toggling auto-punctuation
rebuild the Whisper model — about ten seconds with the hotkey dead. Only model
and device need that.

These tests drive the decision helpers directly with a stub `self`, so they
exercise the real logic without constructing a GTK window.
"""


from types import SimpleNamespace
import pytest


@pytest.fixture
def prefs_cls():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "3.0")
    from tuxtalk.prefs import PreferencesWindow

    return PreferencesWindow


class StubPrefs:
    """Only what the decision helpers touch."""

    def __init__(self, at_open, current):
        self._config_at_open = dict(at_open)
        self.config = dict(current)
        self._restart_owed = set()
        self._restarts_asked = 0
        self.restart_calls = 0
        self.restart_result = True
        self.words_tab = SimpleNamespace(save=lambda: True)     # snippets saved

    def restart_service(self, on_failed=None):
        self.restart_calls += 1
        self.on_failed = on_failed
        return self.restart_result


class TestChangedSinceOpen:
    def test_reports_only_what_this_window_changed(self, prefs_cls):
        stub = StubPrefs({"beeps": True, "model": "small"},
                         {"beeps": False, "model": "small"})

        assert prefs_cls._changed_since_open(stub) == {"beeps"}

    def test_nothing_changed_is_empty(self, prefs_cls):
        stub = StubPrefs({"beeps": True}, {"beeps": True})
        assert prefs_cls._changed_since_open(stub) == set()


class TestApplyOrRestart:
    def test_live_change_does_not_restart(self, prefs_cls):
        """The common case, which would otherwise cost ten seconds."""
        stub = StubPrefs({}, {})

        result = prefs_cls._apply_or_restart(stub, {"auto_period", "beeps"})

        assert result is True
        assert stub.restart_calls == 0, (
            "Restarted for settings the running service picks up on its own."
        )

    def test_indicator_change_does_not_restart(self, prefs_cls):
        stub = StubPrefs({}, {})
        assert prefs_cls._apply_or_restart(stub, {"indicator_position"}) is True
        assert stub.restart_calls == 0

    def test_model_change_restarts(self, prefs_cls):
        stub = StubPrefs({}, {})

        assert prefs_cls._apply_or_restart(stub, {"model"}) is True
        assert stub.restart_calls == 1

    def test_a_restart_that_fails_after_apply_is_said_and_stays_owed(self, prefs_cls):
        """With no tray the restart runs on a thread, after Apply has
        answered: a start that failed there was only logged, and the next
        Apply found nothing changed to restart for."""
        stub = StubPrefs({}, {})
        said = []
        stub._say_restart_failed = lambda: said.append(1)
        stub.window = SimpleNamespace(get_visible=lambda: True)
        assert prefs_cls._apply_or_restart(stub, {"model"}) is True
        stub.on_failed(True)
        assert stub._restart_owed == {"model"} and said == [1]

    def test_a_restart_still_loading_stays_owed_without_a_failure(self, prefs_cls):
        """A service still loading at the wait's end may be asking to
        download its model: not a failure to report, but not done either."""
        stub = StubPrefs({}, {})
        said = []
        stub._say_restart_failed = lambda: said.append(1)
        stub.window = SimpleNamespace(get_visible=lambda: True)
        prefs_cls._apply_or_restart(stub, {"model"})
        stub.on_failed(False)
        assert stub._restart_owed == {"model"} and said == []

    def test_the_restart_thread_reports_a_failed_start(self, prefs_cls, monkeypatch):
        from tuxtalk import prefs, single_instance
        failed = []

        class InlineThread:
            def __init__(self, target, name):
                self.target = target

            def start(self):
                self.target()
        monkeypatch.setattr(single_instance, "held_elsewhere", lambda lock: False)
        monkeypatch.setattr(prefs, "_restart_without_tray", lambda: False)
        monkeypatch.setattr(prefs.threading, "Thread", InlineThread)
        monkeypatch.setattr(prefs.GLib, "idle_add", lambda fn, *args: fn(*args))
        assert prefs_cls.restart_service(None, on_failed=failed.append) is True
        assert failed == [True]

    def test_mixed_change_restarts(self, prefs_cls):
        stub = StubPrefs({}, {})
        prefs_cls._apply_or_restart(stub, {"beeps", "device"})
        assert stub.restart_calls == 1

    def test_no_change_does_not_restart(self, prefs_cls):
        stub = StubPrefs({}, {})
        assert prefs_cls._apply_or_restart(stub, set()) is True
        assert stub.restart_calls == 0

    def test_unknown_key_restarts(self, prefs_cls):
        """Fail safe: a setting added later costs time, not correctness."""
        stub = StubPrefs({}, {})
        prefs_cls._apply_or_restart(stub, {"some_future_setting"})
        assert stub.restart_calls == 1

    def test_reports_a_failed_restart(self, prefs_cls):
        """OK must still show its 'restart failed' warning."""
        stub = StubPrefs({}, {})
        stub.restart_result = False

        assert prefs_cls._apply_or_restart(stub, {"model"}) is False
