"""Backend B (the portal/Flatpak path) runs ONLY _service_tick — never the evdev
main loop. The evdev loop is where settings changed in Preferences get picked up
without a restart (_config_file_changed -> _reload_live_settings). If _service_tick
doesn't do the same, the recording indicator's style/position and every other live
setting never update in the Flatpak, even though Preferences reports "Applied
without restart". This test pins the reload into _service_tick.
"""

import types

from tuxtalk import app
from tuxtalk import recording_state as rs
from tuxtalk import live_settings as live


def test_service_tick_reloads_live_settings_when_config_changed(monkeypatch):
    app._cmd_start_recording.clear()
    app._cmd_stop_recording.clear()
    monkeypatch.setattr(rs.state, "is_recording", False, raising=False)

    calls = []
    monkeypatch.setattr(live, "config_file_changed", lambda: True)
    monkeypatch.setattr(app, "_reload_live_settings",
                        lambda cfg, indicator: calls.append((cfg, indicator)))

    cfg = types.SimpleNamespace(auto_timeout_enabled=False, auto_timeout_minutes=5)
    svc = app._ServiceState(cfg)

    app._service_tick(cfg, svc)

    assert calls, "a config change must trigger a live-settings reload in _service_tick"
    assert calls[0][0] is cfg


def test_service_tick_skips_reload_when_config_unchanged(monkeypatch):
    app._cmd_start_recording.clear()
    app._cmd_stop_recording.clear()
    monkeypatch.setattr(rs.state, "is_recording", False, raising=False)

    calls = []
    monkeypatch.setattr(live, "config_file_changed", lambda: False)
    monkeypatch.setattr(app, "_reload_live_settings",
                        lambda cfg, indicator: calls.append(1))

    cfg = types.SimpleNamespace(auto_timeout_enabled=False, auto_timeout_minutes=5)
    svc = app._ServiceState(cfg)

    app._service_tick(cfg, svc)

    assert not calls, "no reload when the config file is unchanged"
