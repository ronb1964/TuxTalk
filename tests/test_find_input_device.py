"""Which microphone "System Default" records from (microphones.find_input_device).

The Mint test (2026-10-13) had a virtual default microphone. PortAudio's list
has no entry with its name, so TuxTalk fell back to the first raw sound card
and recorded silence while Preferences said "System Default". Noise
suppression (EasyEffects) makes the same kind of virtual default source.
"""
import sys
import types

import pytest

from tuxtalk import mic_volume, microphones

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
    monkeypatch.setattr(mic_volume.subprocess, "run", run)
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
    monkeypatch.setattr(mic_volume.subprocess, "run", hang)
    caplog.set_level(logging.INFO)

    assert microphones.find_input_device("") == 1
    assert any("wpctl inspect @DEFAULT_SOURCE@ failed" in r.getMessage()
               for r in caplog.records)


@pytest.fixture
def pulseaudio(monkeypatch):
    """A PulseAudio desktop: no wpctl, and pactl reporting *.default* as the
    default source, with the built-in mic on card 0 and the headset on card 1."""
    import json
    import subprocess
    monkeypatch.setitem(sys.modules, "sounddevice",
                        types.SimpleNamespace(query_devices=lambda: DEVICES))
    state = types.SimpleNamespace(default="alsa_input.pci-0000_00_1f.3.analog-stereo",
                                  pactl=True)
    sources = [{"name": "alsa_input.pci-0000_00_1f.3.analog-stereo",
                "properties": {"alsa.card": "0", "alsa.device": "0"}},
               {"name": "alsa_input.usb-Sennheiser_headset-00.mono-fallback",
                "properties": {"alsa.card": "1", "alsa.device": "0"}},
               {"name": "noise_suppressed", "properties": {}}]

    def run(cmd, **kwargs):
        if cmd[0] == "wpctl" or not state.pactl:
            raise FileNotFoundError(cmd[0])
        if state.pactl == "fails":
            return types.SimpleNamespace(returncode=1, stdout="", stderr="Connection failure")
        if state.pactl == "hangs":
            raise subprocess.TimeoutExpired(cmd, 2)
        if cmd[1:] == ["get-default-source"]:
            return types.SimpleNamespace(returncode=0, stdout=state.default + "\n")
        return types.SimpleNamespace(returncode=0, stdout=json.dumps(sources))
    monkeypatch.setattr(mic_volume.subprocess, "run", run)
    return state


def test_without_pipewire_the_pulseaudio_default_is_recorded(pulseaudio):
    """With a USB headset plugged in, System Default recorded from it although
    PulseAudio's default was the built-in mic: silence, or the wrong room."""
    assert microphones.find_input_device("") == 0


def test_a_pulseaudio_default_on_the_headset_is_found_too(pulseaudio):
    pulseaudio.default = "alsa_input.usb-Sennheiser_headset-00.mono-fallback"
    assert microphones.find_input_device("") == 1


@pytest.mark.parametrize("pactl", [False, "fails", "hangs"])
def test_without_pactl_the_hardware_fallback_still_applies(pulseaudio, pactl):
    pulseaudio.pactl = pactl
    assert microphones.find_input_device("") == 1       # the USB mic is preferred


def test_a_virtual_pulseaudio_default_falls_back_to_hardware(pulseaudio):
    """Never PortAudio's "pulse" device: the ALSA pulse plugin broke the
    beeps on real PulseAudio."""
    pulseaudio.default = "noise_suppressed"
    assert microphones.find_input_device("") == 1


def test_device_1_of_a_card_is_never_taken_for_device_10(pulseaudio, monkeypatch):
    """"(hw:3,1" is also the start of "(hw:3,10)", listed first here."""
    import json
    import subprocess
    devices = [{"name": "Card: Rear Mic (hw:3,10)", "max_input_channels": 2},
               {"name": "Card: Front Mic (hw:3,1)", "max_input_channels": 2}]
    monkeypatch.setitem(sys.modules, "sounddevice",
                        types.SimpleNamespace(query_devices=lambda: devices))
    sources = [{"name": "front", "properties": {"alsa.card": "3", "alsa.device": "1"}}]
    replies = {"get-default-source": "front\n", "-f": json.dumps(sources)}
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: types.SimpleNamespace(
        returncode=0 if cmd[0] == "pactl" else 1, stdout=replies.get(cmd[1], ""), stderr=""))
    assert microphones.find_input_device("") == 1


@pytest.mark.parametrize("default, properties", [
    ("alsa_output.pci-0000_00_1f.3.analog-stereo.monitor", {"alsa.card": "0"}),
    ("speakers_loopback", {"alsa.card": "0", "alsa.device": "0", "device.class": "monitor"}),
])
def test_a_monitor_as_the_default_is_not_taken_for_its_cards_input(pulseaudio, monkeypatch,
                                                                   default, properties):
    """The monitor of the speakers shares their card's numbers: it isn't the
    built-in mic, so the hardware fallback applies as without pactl."""
    import json
    import subprocess
    sources = json.dumps([{"name": default, "properties": properties}])
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: (
        types.SimpleNamespace(returncode=1, stdout="", stderr="")
        if cmd[0] == "wpctl" else types.SimpleNamespace(
            returncode=0, stdout=default if cmd[1] == "get-default-source" else sources,
            stderr="")))
    assert microphones.find_input_device("") == 1       # the USB mic, as before


def test_a_pactl_that_fails_says_why_in_the_log(pulseaudio, caplog):
    import logging
    pulseaudio.pactl = "fails"
    caplog.set_level(logging.INFO)
    assert microphones.find_input_device("") == 1
    assert any("pactl get-default-source failed with exit code 1: Connection failure"
               in r.getMessage() for r in caplog.records)
