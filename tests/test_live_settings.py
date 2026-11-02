"""Settings must apply without restarting the dictation service.

Restarting the service on every Apply/OK would make toggling auto-punctuation
tear it down and reload a 3 GB Whisper model onto the GPU — about ten seconds
during which F8 does nothing, with no beep, no indicator and no message.

Most settings don't need that. They are read off the long-lived
`cfg` object at the moment they are used (every dictation ends by passing
cfg.beeps, cfg.auto_period and the rest into stop_recording), so refreshing
that object's fields makes them live. Only `model` and `device` require
rebuilding the model.
"""

import dataclasses

import pytest

from tuxtalk import config


class TestChangedKeys:
    """One definition of "changed", shared with merge_changed_keys."""

    def test_reports_only_differing_keys(self):
        original = {"a": 1, "b": 2, "c": 3}
        current = {"a": 1, "b": 99, "c": 3}
        assert config.changed_keys(original, current) == {"b"}

    def test_reports_keys_absent_from_original(self):
        """A key the original never had is a change, not a no-op."""
        assert config.changed_keys({"a": 1}, {"a": 1, "new": 5}) == {"new"}

    def test_no_changes_is_empty(self):
        assert config.changed_keys({"a": 1}, {"a": 1}) == set()

    def test_merge_overlays_exactly_the_changed_keys(self):
        """merge_changed_keys and changed_keys must never disagree."""
        original = {"model": "small", "beeps": True}
        current = {"model": "medium", "beeps": True}
        base = {"model": "small", "beeps": True, "device": "cpu"}

        changed = config.changed_keys(original, current)
        merged = config.merge_changed_keys(original, current, dict(base))

        for key in changed:
            assert merged[key] == current[key]
        for key in set(base) - changed:
            assert merged[key] == base[key], (
                f"{key} was not changed by the user but merge overwrote it"
            )


class TestRestartDecision:
    def test_live_settings_do_not_restart(self):
        assert config.needs_service_restart({"auto_period", "beeps"}) is False

    def test_model_change_restarts(self):
        assert config.needs_service_restart({"model"}) is True

    def test_device_change_restarts(self):
        assert config.needs_service_restart({"device"}) is True

    def test_mixed_change_restarts(self):
        """One restart-required key is enough."""
        assert config.needs_service_restart({"beeps", "model"}) is True

    def test_no_change_does_not_restart(self):
        assert config.needs_service_restart(set()) is False

    def test_unknown_key_restarts(self):
        """Fail in the safe direction: a forgotten setting costs time, not correctness.

        Expressed as a model/device denylist instead, a setting added later
        would default to "live", silently fail to apply, and present as a
        setting that lies about itself.
        """
        assert config.needs_service_restart({"some_future_setting"}) is True


class TestSettingsCoverage:
    """Every field must be deliberately classified, not accidentally omitted."""

    def test_every_setting_is_classified(self):
        fields = {f.name for f in dataclasses.fields(config.Settings)}
        classified = config.LIVE_APPLIED_KEYS | config.RESTART_REQUIRED_KEYS
        unclassified = fields - classified

        assert not unclassified, (
            f"These settings are in neither LIVE_APPLIED_KEYS nor "
            f"RESTART_REQUIRED_KEYS: {sorted(unclassified)}. Decide which — an "
            f"unclassified setting silently triggers a restart, and if it were "
            f"ever defaulted the other way it would silently not apply."
        )

    def test_classifications_do_not_overlap(self):
        overlap = config.LIVE_APPLIED_KEYS & config.RESTART_REQUIRED_KEYS
        assert not overlap, f"Settings classified both ways: {sorted(overlap)}"

    def test_model_and_device_are_restart_required(self):
        """The WhisperModel is built once; these are the ten seconds."""
        assert {"model", "device"} <= config.RESTART_REQUIRED_KEYS

    @pytest.mark.parametrize(
        "key",
        ["auto_period", "auto_space", "beeps", "notify", "smart_quotes",
         "language", "injection_mode", "hotkey", "toggle_hotkey", "mode",
         "key_delay", "indicator_position", "indicator_size",
         "launch_at_login", "auto_check_updates",
         # Read only by the tray, first run and the Stats tab.
         "dictation_language", "recommend_notice_shown", "vulkan_slower", "typing_wpm"],
    )
    def test_known_live_settings_are_live(self, key):
        assert key in config.LIVE_APPLIED_KEYS, (
            f"{key} was established as safe to apply live; leaving it out costs "
            f"a needless ten-second model reload."
        )
