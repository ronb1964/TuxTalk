"""Beeps are heard in full, however slow the sound output (issue #21).

The fake sound system below keeps what a real one does when a stream is
closed while still playing: the samples still waiting in the stream's buffer
(its latency plus one period) are thrown away, and only the rest reaches the
speakers. sounddevice.play() closed its stream that way right after the last
samples went in, which lost the whole hands-free beep on a desktop with
0.17 s or more of output latency.
"""
import numpy as np
import pytest

from tuxtalk import beeps

PERIOD_S = 0.043          # PipeWire's largest period, 2048 frames at 48 kHz


class FakeStream:
    def __init__(self, system, **settings):
        self.system = system
        self.latency = system.latency
        self.settings = settings
        self.written = []

    def start(self):
        pass

    def write(self, data):
        self.written.append(np.asarray(data, np.float32))

    def close(self):
        sound = np.concatenate(self.written) if self.written else np.zeros(0, np.float32)
        still_buffered = int((self.latency + PERIOD_S) * beeps.SAMPLE_RATE)
        self.system.heard.append(sound[:max(0, len(sound) - still_buffered)])


class FakeSoundSystem:
    """Stands in for the sounddevice module: one output device and a list
    of what each stream got to the speakers before it was closed."""

    PortAudioError = OSError

    def __init__(self, latency):
        self.latency = latency
        self.heard = []

    def OutputStream(self, **settings):
        return FakeStream(self, **settings)

    def query_devices(self):
        return [{"name": "pipewire", "max_output_channels": 64}]

    def check_output_settings(self, **settings):
        pass


@pytest.fixture
def sound(monkeypatch):
    def make(latency):
        system = FakeSoundSystem(latency)
        monkeypatch.setattr(beeps, "sd", system)
        monkeypatch.setattr(beeps, "_device_found", False)
        return system
    return make


def heard_in_full(heard, freq, duration):
    expected = beeps.tone(freq, duration)
    return len(heard) >= len(expected) and np.array_equal(heard[:len(expected)], expected)


@pytest.mark.parametrize("latency", [0.035, 0.174, 0.36])
def test_the_hands_free_beep_is_heard_in_full_however_slow_the_output(sound, latency):
    system = sound(latency)
    beeps.beep(True, *beeps.HANDS_FREE_BEEP)
    assert heard_in_full(system.heard[0], *beeps.HANDS_FREE_BEEP)


def test_a_double_tap_plays_both_beeps_in_full(sound):
    system = sound(0.36)
    beeps.beep(True, *beeps.START_BEEP)
    beeps.beep(True, *beeps.HANDS_FREE_BEEP)
    assert len(system.heard) == 2
    assert heard_in_full(system.heard[0], *beeps.START_BEEP)
    assert heard_in_full(system.heard[1], *beeps.HANDS_FREE_BEEP)


def test_beeps_off_plays_nothing(sound):
    system = sound(0.035)
    beeps.beep(False, *beeps.START_BEEP)
    assert system.heard == []


def test_a_broken_sound_output_does_not_stop_the_recording(sound, monkeypatch):
    system = sound(0.035)

    def no_output(**settings):
        raise system.PortAudioError("Device unavailable")
    monkeypatch.setattr(system, "OutputStream", no_output)
    beeps.beep(True, *beeps.START_BEEP)     # must not raise
    assert system.heard == []
