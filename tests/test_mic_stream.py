"""The microphone's stream: a rate the device records at, and a stop that an
audio system that stopped answering can't hang."""
import logging
import threading
import time

import pytest

from tuxtalk import mic_stream


class Stream:
    """Stops and closes as asked; *stop* can raise, or hang until released."""

    def __init__(self, stop=None):
        self.calls = []
        self._stop = stop
        self.release = threading.Event()
        self.closed = threading.Event()

    def stop(self):
        self.calls.append("stop")
        if self._stop == "hangs":
            self.release.wait(5)
        elif self._stop == "fails":
            raise OSError("device unplugged")

    def close(self):
        self.calls.append("close")
        self.closed.set()


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(mic_stream, "STOP_TIMEOUT_S", 0.1)
    monkeypatch.setattr(mic_stream, "_stuck", [])


def test_a_stream_is_stopped_then_closed():
    stream = Stream()
    assert mic_stream.close(stream) is True
    assert stream.calls == ["stop", "close"]


def test_a_stream_that_fails_to_stop_is_still_closed_and_it_is_logged(caplog):
    """An unplugged microphone: closing it frees the device for next time."""
    caplog.set_level(logging.INFO)
    stream = Stream(stop="fails")
    assert mic_stream.close(stream) is True
    assert stream.calls == ["stop", "close"]
    assert any("Could not stop the microphone stream: device unplugged" in r.getMessage()
               for r in caplog.records)


def test_a_stream_that_never_stops_is_given_up_on_and_kept(caplog):
    caplog.set_level(logging.INFO)
    stream = Stream(stop="hangs")
    started = time.monotonic()
    assert mic_stream.close(stream) is False
    assert time.monotonic() - started < 1.0
    assert mic_stream._stuck == [stream]
    assert any("didn't stop within" in r.getMessage() for r in caplog.records)
    stream.release.set()


def test_without_waiting_it_returns_at_once_and_still_says_when_a_stream_hangs(caplog):
    """A window's thread never waits for the audio system."""
    caplog.set_level(logging.INFO)
    stream = Stream(stop="hangs")
    started = time.monotonic()
    mic_stream.close(stream, wait=False)
    assert time.monotonic() - started < 0.05
    deadline = time.monotonic() + 2
    while not mic_stream._stuck and time.monotonic() < deadline:
        time.sleep(0.01)
    assert mic_stream._stuck == [stream]
    stream.release.set()
    assert stream.closed.wait(5)


class Devices:
    """Stands in for sounddevice: input devices that take only *rates*."""

    PortAudioError = OSError

    def __init__(self, own, rates):
        self.own, self.rates = own, rates

    def check_input_settings(self, device, samplerate, channels):
        if samplerate not in self.rates:
            raise self.PortAudioError(f"Invalid sample rate {samplerate}")

    def query_devices(self, device):
        return {"default_samplerate": float(self.own)}


@pytest.mark.parametrize("own,rates,rate", [
    (48000, {16000, 48000}, 16000),
    (48000, {48000}, 48000),
    (44100, set(), 16000)], ids=["takes 16 kHz", "hw: device at its own rate", "takes neither"])
def test_the_microphone_records_at_16_khz_where_it_can(monkeypatch, own, rates, rate):
    monkeypatch.setattr(mic_stream, "sd", Devices(own, rates))
    assert mic_stream.input_samplerate(3, 16000) == rate
