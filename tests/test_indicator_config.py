"""New recording-indicator config keys: defaults, validation, live-apply.

These settings drive the selectable indicator styles (waveform, bars, radial)
alongside the orb, plus color and sensitivity. They must all apply
live — the service reads them without a restart — so every one belongs in
LIVE_APPLIED_KEYS.
"""
from tuxtalk import config


def test_new_indicator_fields_have_approved_defaults():
    s = config.Settings()
    assert s.indicator_style == "orb"
    # "Cyan" is not a mode of its own: the out-of-box look is the classic cyan
    # expressed as a custom color, so the orb keeps its classic gradient.
    assert s.indicator_color_mode == "custom"
    assert s.indicator_color == config.CLASSIC_CYAN_HEX
    assert s.indicator_sensitivity == 1.0


def test_valid_sets_exist():
    assert config.VALID_INDICATOR_STYLES == {"orb", "waveform", "bars", "radial"}
    # There are two choices: match the desktop accent, or pick a color.
    assert config.VALID_COLOR_MODES == {"system", "custom"}


def test_legacy_cyan_mode_migrates_to_custom_classic_cyan():
    """Configs written before the two-mode redesign say color_mode='cyan'.
    They must load as custom + the classic cyan color, so the orb these users
    have always seen is preserved and the dropdown never shows a blank."""
    s = config.Settings()
    s.indicator_color_mode = "cyan"
    s.indicator_color = "#48b7f5"   # the old ignored default
    config._migrate_settings(s)
    assert s.indicator_color_mode == "custom"
    assert s.indicator_color == config.CLASSIC_CYAN_HEX


def test_migration_leaves_a_real_custom_color_untouched():
    """A user who chose their own custom color keeps it across the migration."""
    s = config.Settings()
    s.indicator_color_mode = "custom"
    s.indicator_color = "#ff5522"
    config._migrate_settings(s)
    assert s.indicator_color_mode == "custom"
    assert s.indicator_color == "#ff5522"


def test_new_keys_are_live_applied_not_restart():
    keys = {"indicator_style", "indicator_color_mode", "indicator_color",
            "indicator_sensitivity"}
    assert keys <= config.LIVE_APPLIED_KEYS
    assert not (keys & config.RESTART_REQUIRED_KEYS)


def test_invalid_style_is_reported():
    s = config.Settings()
    s.indicator_style = "bogus"
    problems = dict(config._validation_problems(s))
    assert "indicator_style" in problems


def test_sensitivity_out_of_range_is_reported():
    s = config.Settings()
    s.indicator_sensitivity = 9.0
    problems = dict(config._validation_problems(s))
    assert "indicator_sensitivity" in problems


def test_float_sensitivity_round_trips_as_a_float():
    """A float field must survive save/reload as a float, not a string: the
    TOML writer writes it bare and the loader casts it back."""
    assert config._toml_value(1.3) == "1.3"          # bare, not quoted
    assert config._TYPE_CASTERS["float"] is float     # loader casts it back


def test_hand_edited_capitals_load_as_the_values_the_app_compares():
    """Validation accepts "Vulkan" or "Type" in any case, so the rest of the
    app must see them lowercase too: kept as written, the indicator went
    blank and "Type" took the paste path's Claude Desktop detour."""
    s = config.Settings()
    s.device, s.injection_mode, s.mode = "Vulkan", "Type", "Hold"
    s.indicator_style, s.indicator_size = "Bars", "Large"
    s = config.repaired(s)
    assert (s.device, s.injection_mode, s.mode) == ("vulkan", "type", "hold")
    assert (s.indicator_style, s.indicator_size) == ("bars", "large")
