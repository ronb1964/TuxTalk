"""The dictation service must pick up config changes without restarting.

The service reads its config once at startup and holds the Settings object for
the life of the process. Refreshing that object's fields in place makes every
setting that is read at point of use (beeps, auto_period, injection_mode …) go
live immediately, because each dictation passes them into stop_recording()
fresh.

Values copied into loop locals at startup — the hotkey keycodes, the hold/
toggle mode, key_delay, the indicator object — need explicit re-application,
which is what _reload_live_settings returns to the caller.

A damaged config must never take down a running dictation session: the reload
keeps the previous settings and carries on.
"""


import pytest

from tuxtalk import hotkeys
from tuxtalk import config
from tuxtalk import live_settings as live


@pytest.fixture
def app():
    """Import app.py, skipping when its heavy dependencies are unavailable."""
    return pytest.importorskip("tuxtalk.app")


class StubIndicator:
    """Records apply_settings calls without needing a display."""

    def __init__(self):
        self.calls = []

    def apply_settings(self, position, size, offset_x, offset_y, **kwargs):
        self.calls.append((position, size, offset_x, offset_y))


def _settings(**overrides):
    s = config.Settings()
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


class TestReloadLiveSettings:
    def test_refreshes_read_at_use_settings_in_place(self, app, monkeypatch):
        """The same object must be updated — the whole service holds a reference."""
        cfg = _settings(auto_period=True, beeps=True, injection_mode="type")
        fresh = _settings(auto_period=False, beeps=False, injection_mode="paste")
        monkeypatch.setattr(app, "load_config", lambda: fresh)

        app._reload_live_settings(cfg, None)

        assert cfg.auto_period is False
        assert cfg.beeps is False
        assert cfg.injection_mode == "paste"

    def test_does_not_touch_restart_required_settings(self, app, monkeypatch):
        """model/device belong to the model that is already built and running."""
        cfg = _settings(model="small", device="cpu")
        fresh = _settings(model="large-v3", device="cuda")
        monkeypatch.setattr(app, "load_config", lambda: fresh)

        app._reload_live_settings(cfg, None)

        assert cfg.model == "small", (
            "Refreshing model on a running service would disagree with the "
            "WhisperModel actually loaded."
        )
        assert cfg.device == "cpu"

    def test_returns_recomputed_hotkey_values(self, app, monkeypatch):
        cfg = _settings(hotkey="F8", toggle_hotkey="F9")
        fresh = _settings(hotkey="F7", toggle_hotkey="F9")
        monkeypatch.setattr(app, "load_config", lambda: fresh)

        live = app._reload_live_settings(cfg, None)

        assert live is not None
        assert live.hold_key == hotkeys.keycode_from_name("F7")
        assert live.toggle_key == hotkeys.keycode_from_name("F9")

    def test_applies_indicator_settings(self, app, monkeypatch):
        cfg = _settings()
        fresh = _settings(
            indicator_position="top-left", indicator_size="large",
            indicator_offset_x=50, indicator_offset_y=-10,
        )
        monkeypatch.setattr(app, "load_config", lambda: fresh)
        indicator = StubIndicator()

        app._reload_live_settings(cfg, indicator)

        assert indicator.calls == [("top-left", "large", 50, -10)]

    def test_survives_a_damaged_config(self, app, monkeypatch):
        """A broken file must not raise into the main loop or lose settings."""
        cfg = _settings(auto_period=True)

        def boom():
            raise config.ConfigNotLoadedError("unreadable")

        monkeypatch.setattr(app, "load_config", boom)

        live = app._reload_live_settings(cfg, None)

        assert live is None
        assert cfg.auto_period is True, "previous settings must survive"

    def test_survives_any_unexpected_error(self, app, monkeypatch):
        """The input loop calls this; nothing here may escape into it."""
        cfg = _settings()
        monkeypatch.setattr(app, "load_config", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        assert app._reload_live_settings(cfg, None) is None


class TestConfigChangeDetection:
    def test_reports_change_once_then_settles(self, app, monkeypatch, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("model = 'small'\n")
        monkeypatch.setattr(config, "CONFIG_PATH", str(cfg_file))
        monkeypatch.setattr(live, "_last_config_mtime", None, raising=False)

        assert live.config_file_changed() is True, "first call establishes a baseline"
        assert live.config_file_changed() is False, "unchanged file must not re-trigger"

        cfg_file.write_text("model = 'medium'\n")
        import os
        os.utime(cfg_file, (0, 0))  # force a distinct mtime

        assert live.config_file_changed() is True

    def test_missing_config_is_not_a_change(self, app, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "gone.toml"))
        monkeypatch.setattr(live, "_last_config_mtime", None, raising=False)

        assert live.config_file_changed() is False
