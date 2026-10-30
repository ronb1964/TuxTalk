"""With the mic on System Default, recording follows the desktop's choice.

Issue #14: with the default input switched in GNOME's Quick Settings, TuxTalk
kept recording from the old mic until it was restarted. The default
was turned into a device number once, at startup, and only a change of the
*mic setting* re-resolved it.

Now each recording asks PipeWire which source is the default (wpctl answers in
about a millisecond) and re-resolves only when that changed. A mic chosen by
name in Preferences is left alone: that user asked for that mic.
"""
import types

import pytest

from tuxtalk import mic_volume, microphones
from tuxtalk import recording_state as rs
from tuxtalk import recording


@pytest.fixture
def desktop(monkeypatch):
    """Fake PipeWire default source and device resolution."""
    state = types.SimpleNamespace(source="alsa_input.usb-Logitech_C920",
                                  idx_for={"alsa_input.usb-Logitech_C920": 4,
                                           "alsa_input.usb-TONOR_TD520S": 6},
                                  resolves=[])
    # The source stands for wpctl's whole answer; its name is itself.
    monkeypatch.setattr(microphones, "default_source", lambda: state.source)
    monkeypatch.setattr(microphones, "source_name", lambda source: source)

    def default(source):
        state.resolves.append(source)
        return state.idx_for.get(source, 2)
    monkeypatch.setattr(microphones, "default_microphone", default)
    monkeypatch.setattr(microphones, "find_input_device", lambda mic: 9)
    monkeypatch.setattr(recording, "_system_default_mic", None)
    return state


def test_a_switched_default_is_used_by_the_next_recording(desktop):
    assert recording.pick_input_device("") == 4

    desktop.source = "alsa_input.usb-TONOR_TD520S"   # Quick Settings → TONOR

    assert recording.follow_system_default(4) == 6


def test_an_unchanged_default_is_not_re_resolved(desktop):
    recording.pick_input_device("")
    desktop.resolves.clear()

    assert recording.follow_system_default(4) == 4
    assert desktop.resolves == []


def test_a_mic_chosen_by_name_is_kept(desktop):
    assert recording.pick_input_device("Fast Track") == 9

    desktop.source = "alsa_input.usb-TONOR_TD520S"

    assert recording.follow_system_default(9) == 9


def test_back_on_system_default_follows_again(desktop):
    recording.pick_input_device("Fast Track")
    recording.pick_input_device("")
    desktop.source = "alsa_input.usb-TONOR_TD520S"

    assert recording.follow_system_default(4) == 6


def test_without_wpctl_the_device_in_use_is_kept(desktop):
    recording.pick_input_device("")
    desktop.source = None                              # wpctl missing or failed
    desktop.resolves.clear()

    assert recording.follow_system_default(4) == 4
    assert desktop.resolves == []


def test_a_failed_re_resolve_keeps_the_device_in_use(desktop, monkeypatch):
    recording.pick_input_device("")
    desktop.source = "alsa_input.usb-TONOR_TD520S"

    def boom(source):
        raise RuntimeError("PortAudio went away")
    monkeypatch.setattr(microphones, "default_microphone", boom)

    assert recording.follow_system_default(4) == 4


def _start_recording(monkeypatch, idx):
    """start_recording with no real microphone: the device numbers it opened."""
    opened = []
    monkeypatch.setattr(recording, "open_input_stream", lambda rec, i: opened.append(i))
    monkeypatch.setattr("tuxtalk.type_at_pause.arm_chunker", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.recording.pause_media_players", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.tray_link.notify_recording_state", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: None)
    monkeypatch.setattr(recording, "indicator", None)
    try:
        assert recording.start_recording(False, False, idx) is not None
    finally:
        rs.state.is_recording = False
        rs.state.current.stream = None
    return opened


def test_start_recording_opens_the_new_default(desktop, monkeypatch):
    recording.pick_input_device("")
    desktop.source = "alsa_input.usb-TONOR_TD520S"
    assert _start_recording(monkeypatch, 4) == [6]


C920 = {"name": "C920 (hw:2,0)", "max_input_channels": 2}
TONOR = {"name": "TONOR TD510 (hw:3,0)", "max_input_channels": 1}


@pytest.fixture
def pipewire(monkeypatch):
    """A real wpctl answer for each call, counted; .default names the source."""
    import sys
    state = types.SimpleNamespace(default="C920", calls=0)

    def wpctl(cmd, **kw):
        state.calls += 1
        return types.SimpleNamespace(
            returncode=0, stderr="",
            stdout=f'  * node.description = "{state.default}"\n'
                   f'  * node.name = "alsa_input.{state.default}"\n')
    monkeypatch.setattr(mic_volume.subprocess, "run", wpctl)
    monkeypatch.setitem(sys.modules, "sounddevice",
                        types.SimpleNamespace(query_devices=lambda: [C920, TONOR]))
    monkeypatch.setattr(recording, "_system_default_mic", None)
    return state


def test_system_default_asks_pipewire_once(pipewire, monkeypatch):
    """The mic is resolved at startup, on a settings change and when a
    stream fails to open, and again by a recording after the default was
    switched: one wpctl answer names the default source and finds its device
    each time (it was asked twice, once for each)."""
    assert recording.pick_input_device("") == 0
    assert pipewire.calls == 1
    assert recording._system_default_mic == ("alsa_input.C920", 0)

    pipewire.default, pipewire.calls = "TONOR TD510", 0
    assert _start_recording(monkeypatch, 0) == [1]
    assert pipewire.calls == 1
    assert recording._system_default_mic == ("alsa_input.TONOR TD510", 1)


def test_without_a_device_list_the_default_is_still_followed_by_name(pipewire, monkeypatch):
    """PortAudio that can't list its devices leaves sounddevice's own default,
    but the source's name is kept: without it, every recording would take
    the default for switched and resolve it again."""
    import sys

    def cannot_list():
        raise OSError("PortAudio isn't running")
    monkeypatch.setitem(sys.modules, "sounddevice",
                        types.SimpleNamespace(query_devices=cannot_list))
    assert recording.pick_input_device("") is None
    assert recording._system_default_mic == ("alsa_input.C920", None)


class TestSourceName:
    def _wpctl(self, monkeypatch, stdout, returncode=0):
        monkeypatch.setattr(mic_volume.subprocess, "run",
                            lambda cmd, **kw: types.SimpleNamespace(
                                returncode=returncode, stdout=stdout, stderr=""))

    def test_reads_node_name(self, monkeypatch):
        self._wpctl(monkeypatch, 'id 57, type PipeWire:Interface:Node\n'
                    '  * node.description = "C920 Pro Webcam Analog Stereo"\n'
                    '  * node.name = "alsa_input.usb-046d_C920"\n')
        assert microphones.source_name(microphones.default_source()) \
            == "alsa_input.usb-046d_C920"

    def test_none_when_wpctl_fails(self, monkeypatch):
        self._wpctl(monkeypatch, "", returncode=1)
        assert microphones.source_name(microphones.default_source()) is None

    def test_none_without_wpctl(self, monkeypatch):
        def missing(cmd, **kw):
            raise FileNotFoundError("wpctl")
        monkeypatch.setattr(mic_volume.subprocess, "run", missing)
        assert microphones.source_name(microphones.default_source()) is None


def test_before_any_microphone_opens_audio_is_taken_at_the_engines_rate():
    """Chunks, the double-tap check and the trims read the stream's rate
    from the recording; before a stream opens it must be the rate the engines
    hear, or a phrase would be resampled from the wrong rate."""
    from tuxtalk import engine
    from tuxtalk.recording_state import Recording
    assert Recording().samplerate == engine.SAMPLE_RATE


def test_a_default_microphone_name_that_isnt_utf8_is_still_read(tmp_path, monkeypatch):
    """Asked at every recording start, after the recording counts as started:
    a device name in another encoding mustn't leave it started with no stream."""
    from shell_stubs import stub
    stub(tmp_path, "wpctl", r"""printf '  * node.name = "alsa_input.caf\351"\n'""")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert microphones.source_name(microphones.default_source()).startswith("alsa_input.caf")
