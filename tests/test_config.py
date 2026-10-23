"""Tests for config module validation logic"""
import pytest
from tuxtalk.config import Settings, validate_config, ConfigError


def test_valid_config():
    """Test that a valid configuration passes validation"""
    s = Settings(
        model="small",
        device="cpu",
        hotkey="F8",
        mode="hold",
        toggle_hotkey="F9"
    )
    # Should not raise
    validate_config(s)


def test_invalid_model():
    """Test that invalid model name fails validation"""
    s = Settings(model="invalid-model")
    with pytest.raises(ConfigError) as exc_info:
        validate_config(s)
    assert "model" in str(exc_info.value)


def test_invalid_device():
    """Test that invalid device fails validation"""
    s = Settings(device="gpu")  # Should be "cuda" not "gpu"
    with pytest.raises(ConfigError):
        validate_config(s)


def test_invalid_mode():
    """Test that invalid mode fails validation"""
    s = Settings(mode="press")  # Should be "hold" or "toggle"
    with pytest.raises(ConfigError):
        validate_config(s)


def test_invalid_injection_mode():
    """Test that invalid injection_mode fails validation"""
    s = Settings(injection_mode="invalid")
    with pytest.raises(ConfigError):
        validate_config(s)


def test_negative_timeout():
    """Test that negative timeout fails validation"""
    s = Settings(auto_timeout_minutes=-1)
    with pytest.raises(ConfigError):
        validate_config(s)


def test_zero_timeout():
    """Test that zero timeout fails validation"""
    s = Settings(auto_timeout_minutes=0)
    with pytest.raises(ConfigError):
        validate_config(s)


def test_empty_hotkey():
    """Test that empty hotkey passes validation (allowed during onboarding)."""
    s = Settings(hotkey="")
    # Empty hotkey is valid — the welcome dialog handles hotkey assignment
    validate_config(s)


def test_empty_toggle_hotkey_in_toggle_mode():
    """Test that empty toggle_hotkey passes even in toggle mode (allowed during onboarding)."""
    s = Settings(mode="toggle", toggle_hotkey="")
    # Empty toggle_hotkey is valid — set during onboarding
    validate_config(s)


def test_empty_toggle_hotkey_in_hold_mode():
    """Test that empty toggle_hotkey is OK when mode is hold"""
    s = Settings(mode="hold", toggle_hotkey="")
    # Should not raise (toggle_hotkey not used in hold mode)
    validate_config(s)


def test_all_valid_models():
    """Test that all documented valid models pass validation"""
    valid_models = [
        "tiny", "tiny.en",
        "base", "base.en",
        "small", "small.en",
        "medium", "medium.en",
        "large", "large-v1", "large-v2", "large-v3"
    ]
    for model in valid_models:
        s = Settings(model=model)
        validate_config(s)  # Should not raise


def test_valid_devices():
    """Test that cpu and cuda pass validation in either case"""
    for device in ["cpu", "cuda", "CPU", "CUDA"]:  # Case insensitive
        s = Settings(device=device)
        validate_config(s)  # Should not raise


def test_valid_modes():
    """Test both valid mode types"""
    for mode in ["hold", "toggle", "HOLD", "TOGGLE"]:  # Case insensitive
        s = Settings(mode=mode)
        validate_config(s)  # Should not raise


def test_valid_injection_modes():
    """Test both valid injection mode types"""
    for mode in ["type", "paste", "TYPE", "PASTE"]:  # Case insensitive
        s = Settings(injection_mode=mode)
        validate_config(s)  # Should not raise


def test_positive_timeout():
    """Test that positive timeouts are valid"""
    for minutes in [1, 5, 10, 30, 60]:
        s = Settings(auto_timeout_minutes=minutes)
        validate_config(s)  # Should not raise


# =====================================================================
# language_mode round-trip and merging only the keys the user changed
# =====================================================================

def test_settings_has_language_mode_and_launch_at_login():
    """These keys must round-trip via Settings, or any save by the tray or
    service would drop them from config.toml."""
    s = Settings()
    assert s.language_mode == "auto"
    assert s.launch_at_login is False


def test_save_config_includes_language_mode(tmp_path, monkeypatch):
    import tuxtalk.config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "config.toml"))
    cfg.save_config(Settings(language_mode="manual"))
    text = (tmp_path / "config.toml").read_text()
    assert 'language_mode = "manual"' in text
    assert 'launch_at_login = false' in text


def test_merge_changed_keys_preserves_external_changes():
    """Keys the user did NOT touch keep the on-disk value (e.g. a model
    switch made from the tray while Preferences was open)."""
    from tuxtalk.config import merge_changed_keys
    original = {"model": "small", "device": "cpu", "beeps": True}
    current = {"model": "small", "device": "cpu", "beeps": False}  # user toggled beeps
    base = {"model": "medium", "device": "cpu", "beeps": True}     # tray changed model meanwhile
    merged = merge_changed_keys(original, current, base)
    assert merged["model"] == "medium"  # tray's change survives Apply
    assert merged["beeps"] is False     # user's change is applied


def test_merge_changed_keys_user_wins_on_conflict():
    from tuxtalk.config import merge_changed_keys
    merged = merge_changed_keys({"model": "small"}, {"model": "large-v3"}, {"model": "medium"})
    assert merged["model"] == "large-v3"


def test_merge_changed_keys_adds_new_keys():
    from tuxtalk.config import merge_changed_keys
    merged = merge_changed_keys({}, {"language_mode": "manual"}, {"model": "small"})
    assert merged["language_mode"] == "manual"
    assert merged["model"] == "small"


def test_model_cache_check_fast_unknown_model():
    """Lightweight cache check must not load model weights and must return
    False for unknown models (used on every Apply/OK click in prefs)."""
    from tuxtalk.model_helper import is_model_cached_fast
    assert is_model_cached_fast("no-such-model") is False


def test_parakeet_is_the_default_model():
    """New installs start on Parakeet: more accurate than any Whisper model on
    English (Open ASR Leaderboard) and fast on a processor. Existing configs
    keep whatever model they saved."""
    from tuxtalk.config import Settings
    assert Settings().model == "parakeet-v3"
    assert Settings().device == "cpu"   # the graphics card is chosen at setup


def test_first_run_and_preferences_recommend_parakeet(prefs_window):
    """Parakeet, not Small, is the recommended model in Preferences; first
    run recommends through recommend.py (the welcome card)."""
    from tuxtalk import recommend
    assert recommend.recommend("en", recommend.Hardware(None)).model == "parakeet-v3"
    labels = {row[0]: row[1] for row in prefs_window().model_combo.get_model()}
    assert "recommended" in labels["parakeet-v3"]
    assert not [model for model, label in labels.items()
                if "recommended" in label and model != "parakeet-v3"]


def test_power_saving_defaults_on_with_a_battery_only(tmp_path):
    """A laptop's battery turns it on; a wireless mouse's battery (scope
    Device) and a desktop with none leave it off, so F8 always works there."""
    from tuxtalk.config import has_battery
    power = tmp_path / "power_supply"
    mouse = power / "hidpp_battery_0"
    mouse.mkdir(parents=True)
    (mouse / "type").write_text("Battery\n")
    (mouse / "scope").write_text("Device\n")
    (power / "AC").mkdir()
    (power / "AC" / "type").write_text("Mains\n")
    assert has_battery(str(power)) is False
    laptop = power / "BAT0"
    laptop.mkdir()
    (laptop / "type").write_text("Battery\n")
    assert has_battery(str(power)) is True
    assert has_battery(str(tmp_path / "missing")) is False


def test_power_saving_default_follows_the_battery(monkeypatch):
    import tuxtalk.config as config
    monkeypatch.setattr(config, "has_battery", lambda: True)
    assert config.Settings().auto_timeout_enabled is True
    monkeypatch.setattr(config, "has_battery", lambda: False)
    assert config.Settings().auto_timeout_enabled is False
