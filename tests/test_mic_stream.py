"""The microphone's stream: a rate the device records at, a stop that an
audio system that stopped answering can't hang, and quitting with a stream
stuck in it."""
import logging
import subprocess
import sys
import threading
import time
import types

import pytest

from tuxtalk import mic_stream


class Stream:
    """Stops and closes as asked; *stop* can hang until released, and *fails*
    ("stop" or "close") with a PortAudio error, which sounddevice raises only
    when asked not to ignore it."""

    def __init__(self, stop=None, fails=None):
        self.calls = []
        self._stop, self._fails = stop, fails
        self.release = threading.Event()
        self.closed = threading.Event()

    def stop(self, ignore_errors=True):
        self.calls.append("stop")
        if self._stop == "hangs":
            self.release.wait(5)
        if self._fails == "stop" and not ignore_errors:
            raise OSError("device unplugged")

    def close(self, ignore_errors=True):
        self.calls.append("close")
        self.closed.set()
        if self._fails == "close" and not ignore_errors:
            raise OSError("bad stream")


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(mic_stream, "STOP_TIMEOUT_S", 0.1)
    monkeypatch.setattr(mic_stream, "_closing", set())


def test_a_stream_is_stopped_then_closed():
    stream = Stream()
    assert mic_stream.close(stream) is True
    assert stream.calls == ["stop", "close"]
    assert mic_stream._closing == set()


@pytest.mark.parametrize("fails,said", [
    ("stop", "Could not stop the microphone stream: device unplugged"),
    ("close", "Could not close the microphone stream: bad stream")])
def test_a_stream_that_fails_to_stop_or_close_is_still_closed_and_it_is_logged(
        caplog, fails, said):
    """An unplugged microphone: closing it frees the device for next time.
    sounddevice drops PortAudio's errors on stop() and close() unless told
    not to, so they went unlogged."""
    caplog.set_level(logging.INFO)
    stream = Stream(fails=fails)
    assert mic_stream.close(stream) is True
    assert stream.calls == ["stop", "close"]
    assert any(said in r.getMessage() for r in caplog.records)


def test_a_stream_that_never_stops_is_given_up_on_and_kept(caplog):
    caplog.set_level(logging.INFO)
    stream = Stream(stop="hangs")
    started = time.monotonic()
    assert mic_stream.close(stream) is False
    assert time.monotonic() - started < 1.0
    assert mic_stream._closing == {stream}, "kept, so nothing closes it again"
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
    while not caplog.records and time.monotonic() < deadline:
        time.sleep(0.01)
    assert any("didn't stop within" in r.getMessage() for r in caplog.records)
    stream.release.set()
    assert stream.closed.wait(5)


@pytest.mark.parametrize("stop", [None, "hangs"], ids=["stops", "never stops"])
def test_without_waiting_it_says_once_the_stream_is_closed_or_given_up_on(stop):
    """So the next recording opens the device only once it is free (an ALSA
    hw: device stays busy until then), and never waits longer than a stream
    may take to stop."""
    stream = Stream(stop=stop)
    over = mic_stream.close(stream, wait=False)
    assert over.wait(1)
    assert stream.closed.is_set() is (stop is None)
    stream.release.set()


@pytest.mark.parametrize("stop,ended", [("slow", True), ("hangs", False)],
                         ids=["closing", "stuck"])
def test_quitting_waits_for_a_stream_closing_and_leaves_a_stuck_one(monkeypatch, stop, ended):
    """At exit sounddevice ends PortAudio, which can hang or crash under a
    stream another thread is still stopping: one that is closing is waited
    for, briefly, and one stuck in stop() is left to the system."""
    system = types.SimpleNamespace(_initialized=1)
    monkeypatch.setattr(mic_stream, "sd", system)
    monkeypatch.setattr(mic_stream, "EXIT_WAIT_S", 0.3)
    mic_stream._at_exit()
    assert system._initialized == 1, "with nothing closing, PortAudio is ended as usual"
    stream = Stream(stop="hangs")
    if stop == "slow":
        threading.Timer(0.1, stream.release.set).start()
    mic_stream.close(stream, wait=False)
    mic_stream._at_exit()
    assert (system._initialized == 1) is ended
    assert stream.closed.is_set() is ended
    stream.release.set()


_QUIT_WITH_A_STUCK_MICROPHONE = """
import threading
import sounddevice
from tuxtalk import mic_stream

def ended():
    sounddevice._initialized -= 1
    print("PortAudio ended", flush=True)
sounddevice._terminate = ended

class Stuck:
    def stop(self, ignore_errors=True):
        threading.Event().wait()
mic_stream.STOP_TIMEOUT_S = 0.1
print("given up on" if mic_stream.close(Stuck()) is False else "stopped", flush=True)
"""


def test_quitting_with_a_stuck_microphone():
    """The real exit order: Preferences or the service quits while a
    microphone stream is stuck in stop(), and sounddevice's own exit handler
    leaves PortAudio alone."""
    started = time.monotonic()
    ran = subprocess.run([sys.executable, "-c", _QUIT_WITH_A_STUCK_MICROPHONE],
                         capture_output=True, text=True, timeout=20)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.splitlines() == ["given up on"]
    assert time.monotonic() - started < 10


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


def test_sounddevice_ends_portaudio_as_often_as_it_counts_it_started(monkeypatch):
    """leave_portaudio_running keeps a stuck stream's PortAudio running by
    setting this count to 0; the installed sounddevice's own exit handler
    must go by it."""
    import sounddevice
    ended = []

    def terminate():
        sounddevice._initialized -= 1
        ended.append(1)
    monkeypatch.setattr(sounddevice, "_terminate", terminate)
    monkeypatch.setattr(sounddevice, "_last_callback", None)
    for started in (0, 2):
        monkeypatch.setattr(sounddevice, "_initialized", started)
        ended.clear()
        sounddevice._exit_handler()
        assert len(ended) == started
    assert mic_stream._check_exit_handler(sounddevice)


def test_a_sounddevice_without_the_count_is_logged(caplog):
    caplog.set_level(logging.INFO)
    assert not mic_stream._check_exit_handler(types.SimpleNamespace())
    assert any("keeps no _initialized" in r.getMessage() for r in caplog.records)
