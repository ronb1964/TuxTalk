"""Custom voice commands must take effect without restarting the service.

Custom commands live in their own file, not config.toml, so the service must
watch that file as well; otherwise an edit in the Commands tab reaches disk and
stops there, while Preferences' Apply dialog says "your changes are already in
effect".

A damaged commands file must never wipe the commands the service is already
running with — the same rule the config reload follows.
"""

import types

import pytest

from tuxtalk import service_tick
from tuxtalk import live_settings as live


@pytest.fixture
def commands_file(tmp_path, monkeypatch):
    """Point the loader at a throwaway commands file."""
    from tuxtalk import config as config_module

    path = tmp_path / "custom_commands.toml"
    monkeypatch.setattr(config_module, "CUSTOM_COMMANDS_PATH", str(path))
    monkeypatch.setattr(live, "_last_commands_mtime", None)
    monkeypatch.setattr(live, "commands", {})
    return path


def _write(path, mapping):
    body = "[commands]\n" + "".join(f'"{k}" = "{v}"\n' for k, v in mapping.items())
    path.write_text(body)


def _cfg():
    return types.SimpleNamespace(
        auto_timeout_enabled=False, auto_timeout_minutes=5, notify=False
    )


class TestDetectingTheEdit:
    def test_a_new_commands_file_is_a_change(self, commands_file):
        _write(commands_file, {"my email": "ron@example.com"})
        assert live.commands_file_changed() is True

    def test_an_unchanged_file_is_not_a_change(self, commands_file):
        _write(commands_file, {"my email": "ron@example.com"})
        live.commands_file_changed()
        assert live.commands_file_changed() is False

    def test_a_missing_file_is_not_a_change(self, commands_file):
        assert live.commands_file_changed() is False


class TestApplyingTheEdit:
    def test_an_added_command_reaches_the_running_service(self, commands_file):
        _write(commands_file, {"my email": "ron@example.com"})

        live.reload_commands()

        assert live.commands == {"my email": "ron@example.com"}

    def test_a_removed_command_stops_being_applied(self, commands_file):
        _write(commands_file, {"my email": "ron@example.com"})
        live.reload_commands()

        _write(commands_file, {})
        live.reload_commands()

        assert live.commands == {}

    def test_a_damaged_file_keeps_the_commands_already_running(self, commands_file):
        """Same rule as the config reload: never degrade a live session."""
        _write(commands_file, {"my email": "ron@example.com"})
        live.reload_commands()

        commands_file.write_text("[commands\nthis is not valid toml")
        live.reload_commands()

        assert live.commands == {"my email": "ron@example.com"}


class TestTheServiceTickPicksItUp:
    def test_editing_commands_alone_applies_without_a_restart(
        self, commands_file, monkeypatch, idle_service
    ):
        """The whole point: no config.toml change, and it still takes effect."""
        monkeypatch.setattr(live, "config_file_changed", lambda: False)
        cfg = _cfg()
        svc = service_tick.ServiceState(cfg)

        _write(commands_file, {"btw": "by the way"})
        service_tick.tick(cfg, svc)

        assert live.commands == {"btw": "by the way"}
