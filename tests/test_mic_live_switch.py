"""Switching microphone in Preferences must affect the next dictation.

The mic is stored as a name but recorded from as a device *number*. 'mic' is in
LIVE_APPLIED_KEYS, so Preferences reports it applied without a restart, and
refreshing cfg.mic is not enough: the number must be re-resolved too, or the
service keeps recording from the device it started with.

The failure path in start_recording re-resolves only when opening the old
device *raises*, so on its own it does nothing when switching between two
microphones that both work.

The resolved number lives on _ServiceState next to the cfg it came from, so
there is one owner rather than a snapshot and a parameter that disagree.
"""

import types


from tuxtalk import app


def _idle(monkeypatch):
    app._cmd_start_recording.clear()
    app._cmd_stop_recording.clear()
    monkeypatch.setattr(app.state, "is_recording", False, raising=False)


def _cfg(mic=""):
    return types.SimpleNamespace(
        auto_timeout_enabled=False, auto_timeout_minutes=5, notify=False,
        beeps=False, mic=mic,
    )


class TestResolvingTheMic:
    def test_a_changed_mic_is_re_resolved(self, monkeypatch):
        cfg = _cfg(mic="Built-in")
        svc = app._ServiceState(cfg, input_device_idx=3)
        monkeypatch.setattr(app, "_pick_input_device", lambda name: 7)

        cfg.mic = "Sennheiser"  # what _reload_live_settings does
        svc.refresh_input_device()

        assert svc.input_device_idx == 7

    def test_an_unchanged_mic_is_not_re_resolved(self, monkeypatch):
        """Re-enumerating audio devices every second would be wasteful."""
        cfg = _cfg(mic="Built-in")
        svc = app._ServiceState(cfg, input_device_idx=3)
        calls = []
        monkeypatch.setattr(app, "_pick_input_device",
                            lambda name: calls.append(name) or 7)

        svc.refresh_input_device()

        assert calls == []
        assert svc.input_device_idx == 3

    def test_a_failed_re_resolve_keeps_the_working_device(self, monkeypatch):
        """A mic that cannot be resolved must not break the one in use."""
        cfg = _cfg(mic="Built-in")
        svc = app._ServiceState(cfg, input_device_idx=3)

        def boom(name):
            raise RuntimeError("no such device")

        monkeypatch.setattr(app, "_pick_input_device", boom)

        cfg.mic = "Unplugged USB"
        svc.refresh_input_device()

        assert svc.input_device_idx == 3


class TestTheNextDictationUsesIt:
    def test_recording_starts_on_the_newly_chosen_mic(self, monkeypatch):
        """End to end through the tick both backends share."""
        _idle(monkeypatch)
        monkeypatch.setattr(app, "_config_file_changed", lambda: False)
        monkeypatch.setattr(app, "_commands_file_changed", lambda: False)
        monkeypatch.setattr(app, "_pick_input_device", lambda name: 7)

        cfg = _cfg(mic="Built-in")
        svc = app._ServiceState(cfg, input_device_idx=3)

        started = []
        monkeypatch.setattr(app, "start_recording",
                            lambda beeps, notify, idx, **kw: started.append(idx))

        cfg.mic = "Sennheiser"
        svc.refresh_input_device()
        app._cmd_start_recording.set()
        app._service_tick(cfg, svc)

        assert started == [7]
