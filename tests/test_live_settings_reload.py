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

import dis

import pytest

from tuxtalk import ai_download, config, hotkey_keys, service_tick
from tuxtalk import live_settings as live


class StubIndicator:
    """Records apply_settings calls without needing a display."""

    def __init__(self):
        self.calls = []
        self.options = {}

    def apply_settings(self, position, size, offset_x, offset_y, **options):
        self.calls.append((position, size, offset_x, offset_y))
        self.options = options


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
        monkeypatch.setattr(service_tick, "load_config", lambda: fresh)

        service_tick._reload_live_settings(cfg, None)

        assert cfg.auto_period is False
        assert cfg.beeps is False
        assert cfg.injection_mode == "paste"

    def test_does_not_touch_restart_required_settings(self, app, monkeypatch):
        """model/device belong to the model that is already built and running."""
        cfg = _settings(model="small", device="cpu")
        fresh = _settings(model="large-v3", device="cuda")
        monkeypatch.setattr(service_tick, "load_config", lambda: fresh)

        service_tick._reload_live_settings(cfg, None)

        assert cfg.model == "small", (
            "Refreshing model on a running service would disagree with the "
            "WhisperModel actually loaded."
        )
        assert cfg.device == "cpu"

    def test_returns_recomputed_hotkey_values(self, app, monkeypatch):
        cfg = _settings(hotkey="F8", toggle_hotkey="F9")
        fresh = _settings(hotkey="F7", toggle_hotkey="F9")
        monkeypatch.setattr(service_tick, "load_config", lambda: fresh)

        live = service_tick._reload_live_settings(cfg, None)

        assert live is not None
        assert live.hold_key == hotkey_keys.keycode_from_name("F7")
        assert live.toggle_key == hotkey_keys.keycode_from_name("F9")

    def test_applies_indicator_settings(self, app, monkeypatch):
        cfg = _settings()
        fresh = _settings(
            indicator_position="top-left", indicator_size="large",
            indicator_offset_x=50, indicator_offset_y=-10,
        )
        monkeypatch.setattr(service_tick, "load_config", lambda: fresh)
        indicator = StubIndicator()

        service_tick._reload_live_settings(cfg, indicator)

        assert indicator.calls == [("top-left", "large", 50, -10)]

    def test_the_indicators_look_and_follow_setting_reach_it(self, app, monkeypatch):
        fresh = _settings(indicator_style="orb", indicator_color_mode="custom",
                          indicator_color="#ff5522", indicator_sensitivity=1.5,
                          indicator_follow_window=True)
        monkeypatch.setattr(service_tick, "load_config", lambda: fresh)
        indicator = StubIndicator()

        service_tick._reload_live_settings(_settings(), indicator)

        assert indicator.options == {"style": "orb", "color_mode": "custom",
                                     "custom_color": "#ff5522", "sensitivity": 1.5,
                                     "follow_window": True}

    def test_survives_a_damaged_config(self, app, monkeypatch):
        """A broken file must not raise into the main loop or lose settings."""
        cfg = _settings(auto_period=True)

        def boom():
            raise config.ConfigNotLoadedError("unreadable")

        monkeypatch.setattr(service_tick, "load_config", boom)

        live = service_tick._reload_live_settings(cfg, None)

        assert live is None
        assert cfg.auto_period is True, "previous settings must survive"

    def test_survives_any_unexpected_error(self, app, monkeypatch):
        """The input loop calls this; nothing here may escape into it."""
        cfg = _settings()
        monkeypatch.setattr(service_tick, "load_config", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        assert service_tick._reload_live_settings(cfg, None) is None


# Each setting the reload copies into live_settings: a change from the
# defaults, the module global it lands in, and the value that must arrive.
# The reload copies only LIVE_APPLIED_KEYS, so a setting Preferences calls
# live but apply() ignores, or one apply() reads but the list leaves out,
# fails here.
_MIRRORED = [
    ({"log_transcripts": True}, "log_transcripts", True),
    ({"usage_stats": False}, "usage_stats", False),
    ({"restore_clipboard": True}, "restore_clipboard", True),
    ({"pause_media": False}, "pause_media", False),
    ({"duck_audio": True}, "duck_audio", True),
    ({"paste_last_hotkey": "Ctrl+Alt+V"}, "paste_last_hotkey", "Ctrl+Alt+V"),
    ({"auto_stop_silence": True, "auto_stop_seconds": 7.0}, "auto_stop_seconds", 7.0),
    ({"chunked_dictation": True, "chunk_pause_seconds": 1.5}, "chunk_pause_seconds", 1.5),
    ({"chunk_speech_db": 9.0}, "chunk_speech_db", 9.0),
    ({"chunk_context_marks": False}, "chunk_context_marks", False),
    ({"remove_fillers": True}, "remove_fillers", True),
    ({"swiss_spelling": True}, "swiss_spelling", True),
    ({"voice_navigation": True}, "voice_navigation", True),
    ({"adapt_to_app": False}, "adapt_to_app", False),
    ({"press_enter_after": True}, "press_enter_after", True),
    ({"auto_languages": "en,es"}, "auto_languages", ("en", "es")),
    ({"numbers_as_digits": True}, "numbers_mode", "all"),
    ({"numbers_as_digits": True, "numbers_small_as_words": True},
     "numbers_mode", "small_as_words"),
    ({"language_mode": "manual", "language": "de"}, "numbers_language", "de"),
    ({"ai_corrections": True}, "ai_corrections", True),
    ({"voice_editing": True}, "voice_editing", True),
]


@pytest.mark.parametrize("changed, name, expected", _MIRRORED,
                         ids=[",".join(changed) for changed, _n, _e in _MIRRORED])
def test_a_change_in_preferences_is_in_force_after_the_reload(app, monkeypatch,
                                                              changed, name, expected):
    monkeypatch.setattr(ai_download, "is_installed", lambda: False)  # no AI engine starts
    cfg = _settings(swiss_spelling=False)     # its default follows the locale
    live.apply(cfg)
    assert getattr(live, name) != expected
    monkeypatch.setattr(service_tick, "load_config",
                        lambda: _settings(**{"swiss_spelling": False, **changed}))

    service_tick._reload_live_settings(cfg, None)

    assert getattr(live, name) == expected


def test_every_setting_apply_keeps_is_in_the_table_above():
    """A setting added to live_settings.apply() later gets a row there too."""
    kept = {i.argval for i in dis.get_instructions(live.apply) if i.opname == "STORE_GLOBAL"}
    # The settings object itself, and the AI engine the AI settings start.
    kept -= {"chunk_cfg", "ai_engine"}
    assert kept == {name for _c, name, _e in _MIRRORED}


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
