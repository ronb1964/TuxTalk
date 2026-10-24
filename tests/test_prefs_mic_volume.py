"""The Input Volume slider must control the mic TuxTalk records from.

Changing @DEFAULT_SOURCE@ while a specific mic is picked in the Microphone
dropdown would show and change a different device: the test recording
wouldn't change and another app's mic would get turned down.
"""
from types import SimpleNamespace

import pytest

from tuxtalk import mic_volume as V
from tuxtalk.prefs_audio import AudioTab

SOURCES = [
    {"name": "alsa_output.pci-0000_14_00.6.analog-stereo.monitor",
     "description": "Monitor of Ryzen HD Audio Controller Analog Stereo",
     "properties": {"device.class": "monitor", "alsa.card": "2", "alsa.device": "0"}},
    {"name": "alsa_input.pci-0000_14_00.6.analog-stereo",
     "description": "Ryzen HD Audio Controller Analog Stereo",
     "properties": {"device.class": "sound", "alsa.card": "2", "alsa.device": "0"}},
    {"name": "alsa_input.usb-Sennheiser-00.mono-fallback",
     "description": "Sennheiser Profile Mono",
     "properties": {"device.class": "sound", "alsa.card": "3"}},
]
# What the Microphone dropdown lists (sounddevice's input devices).
DEVICES = ["Sennheiser Profile: USB Audio (hw:3,0)",
           "HD-Audio Generic: ALC897 Analog (hw:2,0)",
           "HD-Audio Generic: ALC897 Alt Analog (hw:2,2)",
           "Some USB Mic (hw:9,0)", "Sennheiser Profile Mono", "pipewire", "default"]


def test_an_alsa_mic_maps_to_its_cards_input_not_the_monitor():
    assert V.source_for_mic("Sennheiser Profile: USB Audio (hw:3,0)", SOURCES) \
        == "alsa_input.usb-Sennheiser-00.mono-fallback"
    assert V.source_for_mic("HD-Audio Generic: ALC897 Analog (hw:2,0)", SOURCES) \
        == "alsa_input.pci-0000_14_00.6.analog-stereo"


def test_a_pipewire_device_maps_by_its_description():
    assert V.source_for_mic("Sennheiser Profile Mono", SOURCES) \
        == "alsa_input.usb-Sennheiser-00.mono-fallback"


def test_a_second_input_on_a_card_is_not_mistaken_for_its_first():
    """hw:2,2 must not match the card's hw:2,0 source just by the card number."""
    assert V.source_for_mic("HD-Audio Generic: ALC897 Alt Analog (hw:2,2)",
                                   SOURCES) is None


def test_an_unknown_mic_maps_to_nothing():
    assert V.source_for_mic("Some USB Mic (hw:9,0)", SOURCES) is None
    assert V.source_for_mic("Monitor of Ryzen HD Audio Controller Analog Stereo",
                                   SOURCES) is None


class FakeScale:
    def __init__(self):
        self.sensitive = True
        self.value = None
        self.blocked = False

    def set_sensitive(self, value):
        self.sensitive = value

    def set_value(self, value):
        assert self.blocked, "showing the volume would write it back"
        self.value = value

    def handler_block_by_func(self, fn):
        self.blocked = True

    def handler_unblock_by_func(self, fn):
        self.blocked = False


@pytest.fixture
def reads(monkeypatch):
    """The sources whose volume was read; each reads as 42."""
    sources = []
    monkeypatch.setattr(V, "get_volume", lambda source: sources.append(source) or 42)
    return sources


def _stub(mic, reads=()):
    d = SimpleNamespace(config={"mic": mic}, volume_scale=FakeScale(),
                        _input_device_names=DEVICES,
                        volume_note=SimpleNamespace(visible=None),
                        on_volume_changed=lambda s: None, reads=reads)
    d.volume_note.set_visible = lambda v: setattr(d.volume_note, "visible", v)
    return d


def test_the_slider_follows_the_chosen_mic(monkeypatch, reads):
    monkeypatch.setattr(V, "list_sources", lambda: SOURCES)
    d = _stub("Sennheiser Profile: USB Audio (hw:3,0)", reads)

    AudioTab._refresh_volume_slider(d)

    assert d._volume_source == "alsa_input.usb-Sennheiser-00.mono-fallback"
    assert reads == ["alsa_input.usb-Sennheiser-00.mono-fallback"]
    assert d.volume_scale.sensitive and d.volume_scale.value == 42
    assert d.volume_note.visible is False


def test_system_default_keeps_the_default_source(monkeypatch, reads):
    monkeypatch.setattr(V, "list_sources", lambda: SOURCES)
    for mic in ("", "pipewire", "default"):
        d = _stub(mic)
        AudioTab._refresh_volume_slider(d)
        assert d._volume_source == V.DEFAULT_SOURCE


def test_a_mic_that_is_no_longer_listed_uses_the_default_source(monkeypatch, reads):
    """An unplugged mic: the dropdown shows System Default and the service
    records from the default, so the slider follows the default too."""
    monkeypatch.setattr(V, "list_sources", lambda: SOURCES)
    d = _stub("Unplugged USB Mic (hw:5,0)")

    AudioTab._refresh_volume_slider(d)

    assert d._volume_source == V.DEFAULT_SOURCE
    assert d.volume_scale.sensitive


def test_a_mic_with_no_source_disables_the_slider(monkeypatch, reads):
    monkeypatch.setattr(V, "list_sources", lambda: SOURCES)
    d = _stub("Some USB Mic (hw:9,0)")

    AudioTab._refresh_volume_slider(d)

    assert d._volume_source is None
    assert not d.volume_scale.sensitive
    assert d.volume_note.visible is True
    assert reads == []


def test_a_drag_sets_the_volume_once_on_the_chosen_source(monkeypatch):
    """A drag must not run wpctl/pactl on the GTK thread for each slider tick."""
    timers = []
    from gi.repository import GLib
    monkeypatch.setattr(GLib, "timeout_add", lambda ms, fn: timers.append(fn) or len(timers))
    calls = []
    monkeypatch.setattr(V, "set_volume", lambda v, src: calls.append((v, src)) or True)
    d = SimpleNamespace(_volume_source="alsa_input.x", _volume_timer=None)
    d._apply_pending_volume = lambda: AudioTab._apply_pending_volume(d)

    for value in (10, 20, 30, 40):
        AudioTab.on_volume_changed(d, SimpleNamespace(get_value=lambda v=value: v))
    assert len(timers) == 1
    timers[0]()

    assert calls == [(40, "alsa_input.x")]


def test_a_volume_set_just_before_switching_mics_stays_with_its_mic(monkeypatch):
    """Move the slider for one mic and pick another before the change is
    applied: the first mic gets its volume, and the second is left alone."""
    timers = []
    from gi.repository import GLib
    monkeypatch.setattr(GLib, "timeout_add", lambda ms, fn: timers.append(fn) or len(timers))
    calls = []
    monkeypatch.setattr(V, "set_volume", lambda v, src: calls.append((v, src)) or True)
    d = SimpleNamespace(_volume_source="alsa_input.usb", _volume_timer=None)
    d._apply_pending_volume = lambda: AudioTab._apply_pending_volume(d)

    AudioTab.on_volume_changed(d, SimpleNamespace(get_value=lambda: 15))
    d._volume_source = "alsa_input.laptop"      # another mic picked
    timers[0]()

    assert calls == [(15, "alsa_input.usb")]



def test_a_volume_that_cant_be_read_disables_the_slider(monkeypatch):
    """Neither wpctl nor pactl answered: the slider showed 50 % and moved
    nothing."""
    monkeypatch.setattr(V, "get_volume", lambda source: None)
    d = _stub("")
    AudioTab._refresh_volume_slider(d)
    assert not d.volume_scale.sensitive and d.volume_note.visible is True
    assert d._volume_source is None             # so a drag sets nothing


def _answers(monkeypatch, outputs):
    """subprocess.run answering each tool's command from *outputs*
    ({tool: (returncode, stdout)}); a tool not in it isn't installed."""
    ran = []

    def run(cmd, **kw):
        ran.append(cmd)
        if cmd[0] not in outputs:
            raise FileNotFoundError(cmd[0])
        code, out = outputs[cmd[0]]
        return SimpleNamespace(returncode=code, stdout=out)
    monkeypatch.setattr(V.subprocess, "run", run)
    return ran


def test_pipewire_volume_is_read_as_a_rounded_percentage(monkeypatch):
    _answers(monkeypatch, {"wpctl": (0, "Volume: 0.29 [MUTED]\n")})
    assert V.get_volume() == 29             # int(0.29 * 100) would say 28


def test_pulseaudio_volume_is_read_when_wpctl_isnt_there(monkeypatch):
    _answers(monkeypatch, {"pactl": (0, "Volume: mono: 43423 /  66% / -10.73 dB\n")})
    assert V.get_volume() == 66


def test_no_sound_tool_reads_no_volume(monkeypatch):
    _answers(monkeypatch, {"pactl": (1, "")})
    assert V.get_volume() is None


def test_a_refused_set_is_reported(monkeypatch):
    ran = _answers(monkeypatch, {"pactl": (1, "")})
    assert V.set_volume(40, "alsa_input.x") is False
    assert ran == [["pactl", "set-source-volume", "alsa_input.x", "40%"]]   # named: pactl only
    _answers(monkeypatch, {"wpctl": (0, "")})
    assert V.set_volume(40) is True

PACTL_TEXT = """Source #60
\tState: SUSPENDED
\tName: alsa_output.pci-0000_14_00.6.analog-stereo.monitor
\tDescription: Monitor of Ryzen HD Audio Controller Analog Stereo
\tVolume: front-left: 65536 / 100% / 0.00 dB,   front-right: 65536 / 100% / 0.00 dB
\t        balance 0.00
\tProperties:
\t\talsa.card = "2"
\t\tdevice.class = "monitor"
\tFormats:
\t\tpcm
Source #61
\tState: SUSPENDED
\tName: alsa_input.usb-Sennheiser-00.mono-fallback
\tDescription: Sennheiser Profile Mono
\tProperties:
\t\talsa.card = "3"
\t\talsa.device = "0"
\t\tdevice.class = "sound"
\tPorts:
\t\tanalog-input-mic: Microphone (type: Mic, priority: 8700, available)
"""


def test_pactl_without_json_is_read_from_its_text_output(monkeypatch):
    """pactl before 16 (Ubuntu 22.04, Mint 21) has no -f json; without the text
    fallback no specific mic would get a source and the slider would stay
    disabled."""
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw.get("env", {}).get("LC_ALL")))
        if "-f" in cmd:
            return SimpleNamespace(returncode=1, stdout="", stderr="unknown option")
        return SimpleNamespace(returncode=0, stdout=PACTL_TEXT)
    monkeypatch.setattr(V.subprocess, "run", run)

    sources = V.list_sources()

    assert calls[1] == (["pactl", "list", "sources"], "C")
    assert [s["name"] for s in sources] == [
        "alsa_output.pci-0000_14_00.6.analog-stereo.monitor",
        "alsa_input.usb-Sennheiser-00.mono-fallback"]
    assert sources[1]["description"] == "Sennheiser Profile Mono"
    assert sources[1]["properties"] == {"alsa.card": "3", "alsa.device": "0",
                                        "device.class": "sound"}
    assert V.source_for_mic("Sennheiser Profile: USB Audio (hw:3,0)", sources) \
        == "alsa_input.usb-Sennheiser-00.mono-fallback"
