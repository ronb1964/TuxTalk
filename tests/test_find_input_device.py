"""Which microphone "System Default" records from (microphones.find_input_device).

The Mint test (2026-10-13) had a virtual default microphone. PortAudio's list
has no entry with its name, so TuxTalk fell back to the first raw sound card
and recorded silence while Preferences said "System Default". Noise
suppression (EasyEffects) makes the same kind of virtual default source.
"""
import sys
import types

import pytest

from tuxtalk import microphones

DEVICES = [
    {"name": "HDA Intel PCH: ALC892 Analog (hw:0,0)", "max_input_channels": 2},
    {"name": "USB Audio: Sennheiser USB headset (hw:1,0)", "max_input_channels": 1},
    {"name": "sysdefault", "max_input_channels": 128},
    {"name": "pipewire", "max_input_channels": 64},
    {"name": "default", "max_input_channels": 64},
]


@pytest.fixture
def system(monkeypatch):
    """Fake sounddevice and wpctl; set .default to the default source's name."""
    fake_sd = types.SimpleNamespace(query_devices=lambda: DEVICES)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    state = types.SimpleNamespace(default="Sennheiser USB headset")

    def run(cmd, **kwargs):
        if state.default is None:
            raise FileNotFoundError("wpctl")
        out = f' * node.description = "{state.default}"\n   node.nick = "{state.default}"\n'
        return types.SimpleNamespace(returncode=0, stdout=out)
    monkeypatch.setattr(microphones.subprocess, "run", run)
    return state


def test_a_hardware_default_is_found_by_name(system):
    assert microphones.find_input_device("") == 1


def test_a_virtual_default_records_through_pipewire(system):
    system.default = "FakeMic"            # also EasyEffects' "Easy Effects Source"
    assert microphones.find_input_device("") == 3


def test_without_pipewire_the_hardware_fallback_still_applies(system):
    system.default = None                 # wpctl not installed
    assert microphones.find_input_device("") == 1   # the USB mic is preferred


def test_a_named_mic_is_used_as_chosen(system):
    system.default = "FakeMic"
    assert microphones.find_input_device("ALC892") == 0


def test_a_default_source_name_that_isnt_utf8_still_finds_a_microphone(tmp_path, monkeypatch):
    """wpctl's answer is read as text: one byte in another encoding mustn't
    stop the service from finding the microphone."""
    from shell_stubs import stub
    fake_sd = types.SimpleNamespace(query_devices=lambda: DEVICES)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    stub(tmp_path, "wpctl", r"printf '  * node.description = \"Caf\351 mic\"\n'")
    monkeypatch.setenv("PATH", str(tmp_path))
    # Not a hardware name in the list, so it records through PipeWire's device.
    assert microphones.find_input_device(None) == 3


def test_a_pipewire_that_hangs_is_logged_and_a_microphone_still_found(monkeypatch, caplog):
    """A wpctl that times out used to be dropped without a word, so the log
    never said why "System Default" picked another microphone."""
    import logging
    import subprocess
    monkeypatch.setitem(sys.modules, "sounddevice",
                        types.SimpleNamespace(query_devices=lambda: DEVICES))

    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 2)
    monkeypatch.setattr(microphones.subprocess, "run", hang)
    caplog.set_level(logging.INFO)

    assert microphones.find_input_device("") == 1
    assert any("which microphone is the default" in r.getMessage() for r in caplog.records)
