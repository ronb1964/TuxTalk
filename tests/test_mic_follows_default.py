"""With the mic on System Default, recording follows the desktop's choice.

Issue #14 (Stefano, GNOME): the default input was switched in Quick Settings
and TuxTalk kept recording from the old mic until it was restarted. The default
was turned into a device number once, at startup, and only a change of the
*mic setting* re-resolved it.

Now each recording asks PipeWire which source is the default (wpctl answers in
about a millisecond) and re-resolves only when that changed. A mic chosen by
name in Preferences is left alone: that user asked for that mic.
"""
import types

import pytest

from tuxtalk import app, config
from tuxtalk import recording_state as rs
from tuxtalk import recording


@pytest.fixture
def desktop(monkeypatch):
    """Fake PipeWire default source and device resolution."""
    state = types.SimpleNamespace(source="alsa_input.usb-Logitech_C920",
                                  idx_for={"alsa_input.usb-Logitech_C920": 4,
                                           "alsa_input.usb-TONOR_TD520S": 6},
                                  resolves=[])
    monkeypatch.setattr(config, "default_source_name", lambda: state.source)

    def find(mic):
        state.resolves.append(mic)
        return 9 if mic else state.idx_for.get(state.source, 2)
    monkeypatch.setattr(config, "find_input_device", find)
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

    def boom(mic):
        raise RuntimeError("PortAudio went away")
    monkeypatch.setattr(config, "find_input_device", boom)

    assert recording.follow_system_default(4) == 4


def test_start_recording_opens_the_new_default(desktop, monkeypatch):
    opened = []
    monkeypatch.setattr(app, "_open_input_stream", lambda idx: opened.append(idx))
    monkeypatch.setattr("tuxtalk.type_at_pause.arm_chunker", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.recording.pause_media_players", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.tray_link.notify_recording_state", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: None)
    monkeypatch.setattr(app, "recording_indicator", None)

    recording.pick_input_device("")
    desktop.source = "alsa_input.usb-TONOR_TD520S"
    try:
        assert app.start_recording(False, False, 4) is True
    finally:
        rs.state.is_recording = False
        rs.state.stream = None

    assert opened == [6]


class TestDefaultSourceName:
    def _wpctl(self, monkeypatch, stdout, returncode=0):
        monkeypatch.setattr(config.subprocess, "run",
                            lambda cmd, **kw: types.SimpleNamespace(
                                returncode=returncode, stdout=stdout))

    def test_reads_node_name(self, monkeypatch):
        self._wpctl(monkeypatch, 'id 57, type PipeWire:Interface:Node\n'
                    '  * node.description = "C920 Pro Webcam Analog Stereo"\n'
                    '  * node.name = "alsa_input.usb-046d_C920"\n')
        assert config.default_source_name() == "alsa_input.usb-046d_C920"

    def test_none_when_wpctl_fails(self, monkeypatch):
        self._wpctl(monkeypatch, "", returncode=1)
        assert config.default_source_name() is None

    def test_none_without_wpctl(self, monkeypatch):
        def missing(cmd, **kw):
            raise FileNotFoundError("wpctl")
        monkeypatch.setattr(config.subprocess, "run", missing)
        assert config.default_source_name() is None
