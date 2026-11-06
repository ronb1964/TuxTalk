"""Volume up, volume down, mute and unmute by voice (sound_control.py), with
stand-ins for wpctl and pactl: no test touches the real volume."""
import shutil
import subprocess

import pytest

from conftest import esc
from sound_fakes import FakePactl
from tuxtalk import audio_duck, sound_control
from tuxtalk.command_phrases import SoundRequest

SINK = "@DEFAULT_SINK@"
AUDIO_SINK = "@DEFAULT_AUDIO_SINK@"


class Tools(FakePactl):
    """wpctl and pactl as one sound system: the output's level and mute,
    and the one app stream audio_duck turns down. Records every command."""

    def __init__(self):
        super().__init__({7: {"pid": 100, "volumes": [65536, 65536]}})
        self.percent = 50
        self.muted = False
        self.commands = []

    def __call__(self, cmd, **kw):
        self.commands.append(list(cmd))
        args = cmd[1:]
        out = ""
        if cmd[0] == "wpctl" and args[0] == "get-volume":
            out = f"Volume: {self.percent / 100:.2f}" + (" [MUTED]" if self.muted else "")
        elif cmd[0] == "wpctl" and args[0] == "set-volume":
            self.percent = round(float(args[2]) * 100)
        elif cmd[0] == "wpctl" and args[0] == "set-mute":
            self.muted = args[2] == "1"
        elif args[0] == "get-sink-volume":
            out = f"Volume: front-left: 1 /  {self.percent}% / -20.00 dB,   front-right: 1"
        elif args[0] == "get-sink-mute":
            out = "Mute: " + ("yes" if self.muted else "no")
        elif args[0] == "set-sink-volume":
            self.percent = int(args[2].rstrip("%"))
        elif args[0] == "set-sink-mute":
            self.muted = args[2] == "1"
        else:
            return super().__call__(cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, out, "")


def _installed(monkeypatch, *names):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}" if name in names else None)


@pytest.fixture
def tools(monkeypatch):
    fake = Tools()
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


@pytest.fixture
def wpctl(monkeypatch):
    _installed(monkeypatch, "wpctl", "pactl")


@pytest.fixture
def pactl(monkeypatch):
    _installed(monkeypatch, "pactl")


@pytest.fixture
def no_tools(monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    _installed(monkeypatch)
    monkeypatch.setattr(subprocess, "run", missing)


def test_volume_down_lowers_the_output_by_five(wpctl, tools):
    tools.percent = 50
    assert sound_control.run(SoundRequest("volume down"), False) is True
    assert ["wpctl", "set-volume", AUDIO_SINK, "0.45"] in tools.commands


def test_volume_up_three_is_fifteen_and_unmutes(wpctl, tools):
    tools.percent, tools.muted = 50, True
    assert sound_control.run(SoundRequest("volume up", 3), False) is True
    assert ["wpctl", "set-volume", AUDIO_SINK, "0.65"] in tools.commands
    assert ["wpctl", "set-mute", AUDIO_SINK, "0"] in tools.commands and not tools.muted


def test_volume_up_stops_at_full(pactl, tools):
    tools.percent = 98
    sound_control.run(SoundRequest("volume up"), False)
    assert ["pactl", "set-sink-volume", SINK, "100%"] in tools.commands


def test_volume_up_stops_at_full_with_wpctl(wpctl, tools):
    tools.percent = 98
    sound_control.run(SoundRequest("volume up"), False)
    assert ["wpctl", "set-volume", AUDIO_SINK, "1.00"] in tools.commands


def test_volume_up_never_lowers_a_boosted_volume(pactl, tools):
    tools.percent = 120
    sound_control.run(SoundRequest("volume up"), False)
    assert ["pactl", "set-sink-volume", SINK, "120%"] in tools.commands


def test_volume_down_stops_at_zero(wpctl, tools):
    tools.percent = 3
    sound_control.run(SoundRequest("volume down"), False)
    assert ["wpctl", "set-volume", AUDIO_SINK, "0.00"] in tools.commands


def test_mute_and_unmute_the_output(wpctl, tools):
    assert sound_control.run(SoundRequest("mute"), False) is True
    assert tools.muted is True and ["wpctl", "set-mute", AUDIO_SINK, "1"] in tools.commands
    assert sound_control.run(SoundRequest("unmute"), False) is True
    assert tools.muted is False and ["wpctl", "set-mute", AUDIO_SINK, "0"] in tools.commands


def test_pactl_reads_and_mutes_the_output_where_wpctl_is_missing(pactl, tools):
    tools.percent = 40
    sound_control.run(SoundRequest("mute"), False)
    assert tools.muted is True
    sound_control.run(SoundRequest("volume down"), False)
    assert tools.percent == 35


@pytest.mark.parametrize("action", ["mute", "unmute"])
def test_muting_reads_nothing_first(wpctl, tools, action):
    """Only a volume change needs the level: one program less to wait for or fail."""
    assert sound_control.run(SoundRequest(action), False) is True
    assert [c[1] for c in tools.commands] == ["set-mute"]


def test_a_volume_change_reads_only_the_level(pactl, tools):
    sound_control.run(SoundRequest("volume down"), False)
    assert [c[1] for c in tools.commands] == ["get-sink-volume", "set-sink-volume"]


def test_the_new_level_floats_over_the_window(wpctl, tools, actions_on):
    tools.percent = 50
    sound_control.run(SoundRequest("volume down"), False)
    sound_control.run(SoundRequest("mute"), False)
    assert actions_on == ["Volume 45%", "Sound muted"]


def test_no_sound_server_says_so(no_tools, actions_on):
    assert sound_control.run(SoundRequest("volume up"), False) is False
    assert actions_on == ["Couldn't change the volume"]


def test_a_volume_command_never_changes_an_apps_own_level(wpctl, tools):
    for action in ("volume up", "volume down", "mute", "unmute"):
        sound_control.run(SoundRequest(action), False)
    assert not [c for c in tools.commands if "set-sink-input-volume" in c]


def test_putting_sounds_back_leaves_a_volume_command_alone(pactl, tools, tmp_path):
    """"Turn everything down" is on: a stream is lowered, "volume down" is said,
    the stream is put back; the output keeps the new level and nothing undoes the mute."""
    ducker = audio_duck.AudioDucker(run=tools, own_pid=1, state_path=str(tmp_path / "d.json"),
                                    reset=lambda *a: True)
    tools.percent = 60
    ducker.duck()
    assert ducker.wait_idle(2)
    sound_control.run(SoundRequest("volume down"), False)
    sound_control.run(SoundRequest("mute"), False)
    ducker.restore()
    assert ducker.wait_idle(2)
    mute = ["pactl", "set-sink-mute", SINK, "1"]
    after = tools.commands[tools.commands.index(mute) + 1:]
    assert not [c for c in after if SINK in c]
    assert any("set-sink-input-volume" in c for c in after)     # the stream did come back
    assert tools.percent == 55 and tools.muted is True


def test_esc_while_the_volume_is_read_changes_nothing(actions_on, chat, say, wpctl, tools,
                                                      monkeypatch):
    read = tools.__call__

    def read_then_esc(cmd, **kw):
        if cmd[1] == "get-volume":
            esc()
        return read(cmd, **kw)
    monkeypatch.setattr(subprocess, "run", read_then_esc)
    tools.percent = 50
    say("Volume down.")
    assert tools.percent == 50 and chat[2] == []
    assert not [c for c in tools.commands if c[1] == "set-volume"]


def test_volume_down_said_alone_types_nothing(actions_on, chat, say, wpctl, tools):
    tools.percent = 50
    say("Volume down.")
    assert chat[2] == [] and tools.percent == 45
