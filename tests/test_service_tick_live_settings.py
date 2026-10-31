"""Settings changed in Preferences reach the running service through
service_tick.tick, which both the evdev hotkey loop and the Flatpak's portal
path call: it reloads them when the config file changed
(live.config_file_changed, then _reload_live_settings). Without it the
recording indicator's style and position, and every other live setting,
would stay as they were though Preferences says "Applied without restart".
"""

import types

from tuxtalk import service_tick
from tuxtalk import recording_state as rs
from tuxtalk import live_settings as live
from tuxtalk import recording


def test_service_tick_reloads_live_settings_when_config_changed(monkeypatch):
    recording.start_requested.clear()
    recording.stop_requested.clear()
    monkeypatch.setattr(rs.state, "is_recording", False, raising=False)

    calls = []
    monkeypatch.setattr(live, "config_file_changed", lambda: True)
    monkeypatch.setattr(service_tick, "_reload_live_settings",
                        lambda cfg, indicator: calls.append((cfg, indicator)))

    cfg = types.SimpleNamespace(auto_timeout_enabled=False, auto_timeout_minutes=5)
    svc = service_tick.ServiceState(cfg)

    service_tick.tick(cfg, svc)

    assert calls, "a config change must trigger a live-settings reload in service_tick.tick"
    assert calls[0][0] is cfg


def test_service_tick_skips_reload_when_config_unchanged(monkeypatch):
    recording.start_requested.clear()
    recording.stop_requested.clear()
    monkeypatch.setattr(rs.state, "is_recording", False, raising=False)

    calls = []
    monkeypatch.setattr(live, "config_file_changed", lambda: False)
    monkeypatch.setattr(service_tick, "_reload_live_settings",
                        lambda cfg, indicator: calls.append(1))

    cfg = types.SimpleNamespace(auto_timeout_enabled=False, auto_timeout_minutes=5)
    svc = service_tick.ServiceState(cfg)

    service_tick.tick(cfg, svc)

    assert not calls, "no reload when the config file is unchanged"
