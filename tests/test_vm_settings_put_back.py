"""A VM check that changes a setting puts back what the VM had, not what a
fresh VM has: run.py --as-is runs the checks on a VM set up otherwise (Right
Ctrl to dictate, keystroke typing), which must be left as it was found.
"""
import pytest
from conftest import import_vm_tool

# The harness reads its settings with tomllib, new in Python 3.11.
pytest.importorskip("tomllib", reason="the VM harness needs Python 3.11 or newer")
harness = import_vm_tool("harness")


class GuestConfig:
    """The guest's config.toml as the harness reads and writes it over ssh."""

    def __init__(self, text):
        self.text = text

    def read(self, remote):
        return self.text

    def ssh(self, command, input=None, check=True, timeout=600):
        self.text = input


@pytest.fixture
def run(monkeypatch, tmp_path):
    """A harness run on a VM whose dictation keys are Right Ctrl and F6, with
    keystroke typing and no Paste Last Dictation line; .restarts counts the
    dictation service's restarts."""
    vm = GuestConfig('hotkey = "Right Ctrl"\ntoggle_hotkey = "F6"\ninjection_mode = "type"\n')
    run = harness.Run(vm, tmp_path, tmp_path / "TuxTalk.AppImage")
    run.restarts = 0
    monkeypatch.setattr(run, "restart_service",
                        lambda: setattr(run, "restarts", run.restarts + 1))
    return run


def test_a_changed_key_goes_back_to_what_the_vm_had(run):
    with run.settings_put_back("hotkey", "injection_mode"):
        run.set_config(hotkey="Scroll Lock", injection_mode="auto")
        assert run.config()["hotkey"] == '"Scroll Lock"'
    assert run.config() == {"hotkey": '"Right Ctrl"', "toggle_hotkey": '"F6"',
                            "injection_mode": '"type"'}
    assert run.restarts == 2       # the new keys in force, then the old ones


def test_a_setting_the_vm_didnt_have_is_taken_out_again(run):
    with run.settings_put_back("paste_last_hotkey"):
        run.set_config(paste_last_hotkey="Ctrl+Alt+Y")
    assert "paste_last_hotkey" not in run.config()


def test_put_back_after_a_check_that_failed_part_way(run):
    with pytest.raises(harness.Abort), run.settings_put_back("toggle_hotkey"):
        run.set_config(toggle_hotkey="Right Ctrl")
        raise harness.Abort("the dictation service didn't come back")
    assert run.config()["toggle_hotkey"] == '"F6"'


def test_nothing_changed_restarts_nothing(run):
    with run.settings_put_back("hotkey"):
        pass
    assert run.restarts == 0
