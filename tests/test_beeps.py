"""Beeps are heard in full, however slow the sound output (issue #21), and a
sound output that fails or hangs never holds up a recording.

The fake sound system below works like PortAudio's ALSA output: a stream has
a buffer of its latency plus one period, the callback is asked for a period
whenever there is room, and the speakers take one period at a time, in real
time, from a buffer kept full. Closing the stream throws away whatever is
still in the buffer, which is how sounddevice.play() lost the hands-free beep
on a desktop with 0.17 s or more of output latency.

A sound server can hold more than the stream's buffer (*downstream*): it
takes what it has room for at once, says so in the time the callback is
told the samples will be heard, and a close throws that away too, as
PulseAudio's flush does.
"""
import logging
import threading
import time
import types

import numpy as np
import pytest

from tuxtalk import beeps

RATE = beeps.SAMPLE_RATE


class FakeStream:
    def __init__(self, system, callback, samplerate, **settings):
        self.system = system
        self.callback = callback
        self.latency = system.latency
        self.period = system.period
        self.holds = round(system.latency * RATE) + system.period + round(system.downstream * RATE)
        self.buffer = []                  # what the speakers haven't taken yet
        self.closed = threading.Event()
        self.feeder = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        started, written, taken = time.monotonic(), 0, 0
        while not self.closed.is_set():
            while written - taken + self.period <= self.holds and not self.closed.is_set():
                out = np.full((self.period, 1), np.nan, np.float32)
                underflow = written == 0 and self.system.underflow
                status = types.SimpleNamespace(output_underflow=underflow)
                now = time.monotonic()
                # The buffer's first frame is heard once the speakers have
                # taken every frame written before it. A backend that knows
                # no time reports 0 for both.
                heard = started + written / RATE if self.system.reports_time else 0.0
                info = types.SimpleNamespace(currentTime=now if self.system.reports_time else 0.0,
                                             outputBufferDacTime=heard)
                self.callback(out, self.period, info, status)
                self.buffer.append(out[:, 0].copy())
                written += self.period
            take_at = started + taken / RATE
            if self.closed.wait(max(0.0, take_at - time.monotonic())):
                break
            self.system.heard.append(self.buffer.pop(0))
            self.system.heard_at.append(time.monotonic())
            taken += self.period

    def start(self):
        if self.system.broken == "start":
            raise self.system.PortAudioError("start failed")
        if self.system.broken != "stuck":
            self.feeder.start()

    def close(self, ignore_errors=True):
        self.closed.set()
        if self.feeder.is_alive():
            self.feeder.join()
        self.system.closes += 1
        if self.system.broken == "close" and not ignore_errors:
            raise self.system.PortAudioError("close failed")


class FakeSoundSystem:
    """Stands in for the sounddevice module: one output device, and what
    reached its speakers, a period at a time, and when."""

    PortAudioError = OSError
    _initialized = 1          # sounddevice's count of PortAudio starts, ended at exit

    def __init__(self, latency, period, downstream=0.0):
        self.latency, self.period, self.downstream = latency, period, downstream
        self.reports_time = downstream > 0
        self.heard = []
        self.heard_at = []
        self.underflow = False
        self.broken = None
        self.closes = 0

    def OutputStream(self, callback, **settings):
        if self.broken == "open":
            raise self.PortAudioError("Device unavailable")
        return FakeStream(self, callback, **settings)

    def query_devices(self):
        return [{"name": "pipewire", "max_output_channels": 64, "default_samplerate": 48000}]

    def check_output_settings(self, **settings):
        pass

    def speakers(self):
        return np.concatenate(self.heard) if self.heard else np.zeros(0, np.float32)


@pytest.fixture
def sound(monkeypatch):
    def make(latency=0.035, period=383, downstream=0.0):
        system = FakeSoundSystem(latency, period, downstream)
        monkeypatch.setattr(beeps, "sd", system)
        monkeypatch.setattr(beeps, "STUCK_S", 0.5)
        for name, value in (("_devices", {}), ("_latency_logged", False),
                            ("_underflow_logged", False), ("_failures_logged", set())):
            monkeypatch.setattr(beeps, name, value)
        return system
    return make


def tones(heard):
    """Each run of sound in what the speakers played: (samples, peak, Hz).
    Unwritten frames are NaN, so a callback that leaves any shows."""
    assert not np.isnan(heard).any(), "the callback left part of a buffer unwritten"
    loud = np.flatnonzero(heard != 0)
    runs = np.split(loud, np.flatnonzero(np.diff(loud) > 1) + 1) if len(loud) else []
    found = []
    for run in runs:
        part = heard[run[0]:run[-1] + 1]
        rising = np.flatnonzero((part[:-1] < 0) & (part[1:] >= 0))
        assert len(rising) >= 2, f"a sound of {len(part)} samples, under one whole cycle"
        hz = (len(rising) - 1) * RATE / (rising[-1] - rising[0])   # whole cycles between them
        found.append((len(part), float(np.abs(part).max()), hz))
    return found


def heard_in_full(found, freq, duration):
    samples, peak, hz = found
    # Every sample of the tone, but its first and last, which are 0.
    return (abs(samples - len(beeps.tone(freq, duration))) <= 2
            and peak == pytest.approx(beeps.VOLUME, rel=0.01)
            and hz == pytest.approx(freq, rel=0.02))


# Latency and period of real outputs: PipeWire's usual one, the slow one in
# #21, and periods of 4096 and 8192 frames, longer than any fixed margin.
# Then a sound server holding 0.1 s more than the stream, which it throws
# away on a close, and says it holds.
OUTPUTS = [(0.035, 383, 0), (0.174, 512, 0), (0.279, 4096, 0), (0.557, 8192, 0),
           (0.035, 383, 0.1), (0.174, 512, 0.1)]


@pytest.mark.parametrize("latency,period,downstream", OUTPUTS)
def test_a_double_tap_plays_both_beeps_in_full_however_slow_the_output(
        sound, latency, period, downstream):
    system = sound(latency, period, downstream)
    beeps.beep(True, *beeps.START_BEEP)
    beeps.beep(True, *beeps.HANDS_FREE_BEEP)
    assert beeps.wait_until_played(5)
    start, hands_free = tones(system.speakers())
    assert heard_in_full(start, *beeps.START_BEEP)
    assert heard_in_full(hands_free, *beeps.HANDS_FREE_BEEP)


def test_beep_returns_before_the_tone_has_played(sound):
    """The hotkeys wait on the thread that beeps."""
    system = sound(0.279, 4096)
    asked = time.monotonic()
    beeps.beep(True, *beeps.START_BEEP)
    assert time.monotonic() - asked < 0.05
    beeps.wait_until_played(5)
    assert len(tones(system.speakers())) == 1


def test_beeps_from_two_threads_both_play_in_full_one_after_the_other(sound):
    system = sound(0.174, 512)
    threads = [threading.Thread(target=beeps.beep, args=(True, *tone))
               for tone in (beeps.READY_BEEP, beeps.START_BEEP)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert beeps.wait_until_played(5)
    found = sorted(tones(system.speakers()), key=lambda f: f[2])
    assert len(found) == 2
    assert heard_in_full(found[0], *beeps.READY_BEEP)
    assert heard_in_full(found[1], *beeps.START_BEEP)


def test_beeps_off_plays_nothing(sound):
    system = sound()
    beeps.beep(False, *beeps.START_BEEP)
    assert beeps.wait_until_played(5)
    assert system.closes == 0


def test_the_output_latency_is_logged_once(sound, caplog):
    caplog.set_level(logging.INFO)
    sound(0.174, 512)
    beeps.beep(True, *beeps.START_BEEP)
    beeps.beep(True, *beeps.HANDS_FREE_BEEP)
    beeps.wait_until_played(5)
    said = [r.getMessage() for r in caplog.records if "latency" in r.getMessage()]
    assert said == ["Beeps: the output's latency is 0.174s"]


@pytest.mark.parametrize("broken", ["open", "start", "stuck"])
def test_a_broken_sound_output_is_logged_once_and_not_raised(sound, caplog, broken):
    caplog.set_level(logging.INFO)
    system = sound()
    system.broken = broken
    for _ in range(3):
        beeps.beep(True, *beeps.START_BEEP)
    assert beeps.wait_until_played(5)
    warned = [r for r in caplog.records if r.getMessage().startswith("Could not play a beep")]
    assert len(warned) == 1 and warned[0].levelno == logging.WARNING
    if broken != "open":
        assert system.closes == 3, "every stream that was opened is closed again"


def test_a_stream_that_fails_to_close_is_logged(sound, caplog):
    caplog.set_level(logging.INFO)
    system = sound()
    system.broken = "close"
    beeps.beep(True, *beeps.START_BEEP)
    beeps.wait_until_played(5)
    assert heard_in_full(tones(system.speakers())[0], *beeps.START_BEEP)
    assert any("Could not close a sound stream" in r.getMessage() for r in caplog.records)


def test_a_gap_in_the_output_is_logged_once(sound, caplog):
    caplog.set_level(logging.INFO)
    system = sound()
    system.underflow = True
    beeps.beep(True, *beeps.START_BEEP)
    beeps.beep(True, *beeps.START_BEEP)
    beeps.wait_until_played(5)
    assert sum("ran dry" in r.getMessage() for r in caplog.records) == 1


def test_waiting_for_a_stuck_output_gives_up(sound, monkeypatch):
    """A sound server that stopped answering holds up only the sound thread."""
    sound()
    release = threading.Event()
    monkeypatch.setattr(beeps, "_play", lambda *a: release.wait(5))
    beeps.beep(True, *beeps.START_BEEP)
    asked = time.monotonic()
    assert beeps.wait_until_played(0.2) is False
    assert time.monotonic() - asked < 0.5
    release.set()
    assert beeps.wait_until_played(5)


def test_a_beep_held_up_too_long_is_dropped(sound, monkeypatch):
    """Once the output answers again, the beeps it held up don't all play."""
    system = sound()
    release = threading.Event()
    real_play = beeps._play
    monkeypatch.setattr(beeps, "_play", lambda *a: release.wait(5) and real_play(*a))
    monkeypatch.setattr(beeps, "STALE_S", 0.1)
    beeps.beep(True, *beeps.START_BEEP)
    beeps.beep(True, *beeps.CANCEL_BEEP)
    time.sleep(0.2)
    release.set()
    assert beeps.wait_until_played(5)
    assert [round(hz) for _n, _p, hz in tones(system.speakers())] == [beeps.START_BEEP[0]]


def only_its_own_rate(system, monkeypatch):
    """The device plays 48 kHz and nothing else, like a raw ALSA device."""
    def check(device, samplerate, channels):
        if samplerate != 48000:
            raise system.PortAudioError("Invalid sample rate")
    monkeypatch.setattr(system, "check_output_settings", check)


def test_a_device_that_cant_play_the_rate_gets_its_own(sound, monkeypatch):
    only_its_own_rate(sound(), monkeypatch)
    assert beeps.output_device(44100) == (0, 48000)


def test_a_device_search_that_failed_is_tried_again(sound, monkeypatch):
    system = sound()
    answers = iter([system.PortAudioError("not ready"), None])

    def query():
        answer = next(answers)
        if answer:
            raise answer
        return FakeSoundSystem.query_devices(system)
    monkeypatch.setattr(system, "query_devices", query)
    with pytest.raises(OSError):
        beeps.output_device(44100)
    assert beeps.output_device(44100) == (0, 44100)


def test_a_recording_is_played_back_to_the_end_at_the_output_rate(sound, monkeypatch):
    """Preferences' microphone test replays what was recorded, all of it."""
    system = sound(0.279, 4096)
    only_its_own_rate(system, monkeypatch)
    t = np.arange(16000) / 16000
    recording = (np.sin(2 * np.pi * 300 * t) * 0.5).astype(np.float32)   # 1 s at 16 kHz
    told = []
    beeps.play(recording, 16000, finished=told.append)
    assert beeps.wait_until_played(5)
    heard = system.speakers()
    assert told == [None]
    assert np.count_nonzero(heard) == pytest.approx(48000, abs=48)


def test_sounds_behind_a_stuck_output_never_pile_up(sound, monkeypatch):
    """Beeps and replays asked for while the output hangs wait for it, but
    only the newest few; asking never blocks, and a replay that is dropped
    says so (Preferences then lets Replay be clicked again)."""
    sound()
    release = threading.Event()
    played = []
    monkeypatch.setattr(beeps, "_play", lambda *a: played.append(1) or release.wait(5))
    beeps.beep(True, *beeps.START_BEEP)                 # the output hangs on this one
    while not played:
        time.sleep(0.01)
    told = []
    beeps.play(np.zeros(100, np.float32), RATE, finished=told.append)
    asked = time.monotonic()
    for _ in range(beeps.MAX_WAITING):
        beeps.beep(True, *beeps.CANCEL_BEEP)
    assert time.monotonic() - asked < 0.05
    assert told == ["the sound output is held up"]
    release.set()
    assert beeps.wait_until_played(5)
    assert len(played) == 1 + beeps.MAX_WAITING


def test_a_wait_lasts_while_the_sound_before_it_plays_whatever_is_dropped_after(
        sound, monkeypatch):
    """Sounds asked for after a wait began, and dropped from the queue, are
    over before the one playing; the wait still lasts until that one is.
    The double-tap is timed from the start beep heard (issue #21)."""
    sound()
    release, playing = threading.Event(), threading.Event()
    monkeypatch.setattr(beeps, "_play", lambda *a: playing.set() or release.wait(5))
    start = beeps.beep(True, *beeps.START_BEEP)
    assert playing.wait(5)
    waits = {}
    waiters = [threading.Thread(target=lambda key=key: waits.update({key: beeps.wait_until_played(
        5, over=start if key == "start beep" else None)})) for key in ("start beep", "all")]
    for waiter in waiters:
        waiter.start()
    for _ in range(beeps.MAX_WAITING + 2):            # two of them pushed out of the queue
        beeps.beep(True, *beeps.CANCEL_BEEP)
    time.sleep(0.3)
    assert waits == {}, "a wait ended while the start beep still played"
    release.set()
    for waiter in waiters:
        waiter.join(5)
    assert waits == {"start beep": True, "all": True}


def test_a_callback_that_fails_leaves_the_sound_over_and_the_sounds_playing(sound, caplog):
    """Whoever asked for a sound is told after it counts as over, so their
    callback failing can't hold up a wait, nor stop the sounds after it."""
    system = sound()
    caplog.set_level(logging.INFO)

    def fails(error):
        raise ValueError("the window is gone")
    beeps.play(np.zeros(100, np.float32), RATE, finished=fails)
    beeps.beep(True, *beeps.START_BEEP)
    assert beeps.wait_until_played(limit=5)
    assert heard_in_full(tones(system.speakers())[0], *beeps.START_BEEP), "the beep after it played"
    assert any(r.exc_info and "the window is gone" in str(r.exc_info[1]) for r in caplog.records)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_sound_asked_for_as_the_sound_thread_ends_still_plays(sound, monkeypatch):
    """A job that ends the sound thread itself (SystemExit) counts as failed,
    not played, and a sound asked for while that thread is on its way out
    gets a thread of its own. It waited, unplayed, until some later sound
    was asked for."""
    system = sound()
    real_play = beeps._play
    in_callback, go_on = threading.Event(), threading.Event()
    told = []

    def ends_the_thread(*args):
        raise SystemExit

    def told_then_held_up(error):
        told.append(error)
        in_callback.set()
        go_on.wait(5)
    monkeypatch.setattr(beeps, "_play", ends_the_thread)
    beeps.play(np.zeros(100, np.float32), RATE, finished=told_then_held_up)
    assert in_callback.wait(5)
    monkeypatch.setattr(beeps, "_play", real_play)
    start = beeps.beep(True, *beeps.START_BEEP)
    go_on.set()
    assert start.wait(5), "the beep asked for as the thread ended never played"
    assert told == ["the sound thread stopped"]
    assert heard_in_full(tones(system.speakers())[0], *beeps.START_BEEP)


def test_a_sound_called_off_before_it_starts_never_opens_a_stream(sound):
    system = sound()
    called_off, told = threading.Event(), []
    called_off.set()
    beeps.play(np.full(RATE, 0.1, np.float32), RATE, finished=told.append, called_off=called_off)
    assert beeps.wait_until_played(5)
    assert told == [None] and system.closes == 0 and system.speakers().size == 0


def test_a_sound_called_off_while_its_stream_opens_never_starts(sound, monkeypatch):
    """A sound server can take seconds to open a stream; the replay may be
    given up on meanwhile."""
    system = sound()
    called_off = threading.Event()
    open_stream = system.OutputStream

    def slow_open(callback, **settings):
        called_off.set()
        return open_stream(callback, **settings)
    monkeypatch.setattr(system, "OutputStream", slow_open)
    beeps.play(np.full(RATE, 0.1, np.float32), RATE, called_off=called_off)
    assert beeps.wait_until_played(5)
    assert system.closes == 1 and system.speakers().size == 0


def test_a_sound_called_off_as_it_plays_stops_at_once(sound):
    system = sound()
    called_off = threading.Event()
    beeps.play(np.full(2 * RATE, 0.1, np.float32), RATE, called_off=called_off)
    deadline = time.monotonic() + 5
    while not system.heard and time.monotonic() < deadline:
        time.sleep(0.01)
    called_off.set()
    asked = time.monotonic()
    assert beeps.wait_until_played(5)
    assert time.monotonic() - asked < 0.5
    assert 0 < np.count_nonzero(system.speakers()) < RATE


def test_each_kind_of_sound_failure_is_logged_once(sound, caplog):
    """A sound output that is missing fails every beep and says so once; a
    different failure later still shows in the log."""
    caplog.set_level(logging.INFO)
    system = sound()
    for broken in ("open", "open", "start", "start"):
        system.broken = broken
        beeps.beep(True, *beeps.START_BEEP)
        assert beeps.wait_until_played(5)
    warned = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Could not")]
    assert len(warned) == 2
    assert "Device unavailable" in warned[0] and "start failed" in warned[1]


def test_quitting_waits_for_a_sound_and_leaves_a_stuck_one_to_the_system(sound, monkeypatch):
    """At exit, sounddevice ends PortAudio; under a stream that still plays
    that can hang or crash, so a stuck sound's stream is left alone."""
    system = sound()
    release = threading.Event()
    monkeypatch.setattr(beeps, "_play", lambda *a: release.wait(5))
    monkeypatch.setattr(beeps, "EXIT_WAIT_S", 0.1)
    beeps._at_exit()
    assert system._initialized == 1, "with nothing playing, PortAudio is ended as usual"
    beeps.beep(True, *beeps.START_BEEP)
    asked = time.monotonic()
    beeps._at_exit()
    assert time.monotonic() - asked < 0.5
    assert system._initialized == 0
    release.set()


_QUIT_DURING_A_SOUND = """
import threading, time
import sounddevice
from tuxtalk import beeps

def ended():
    sounddevice._initialized -= 1
    print("PortAudio ended", flush=True)
sounddevice._terminate = ended
beeps.output_device = lambda rate: (None, rate)

def play(*a):
    {play}
    print("sound over", flush=True)
beeps._play = play
beeps.beep(True, *beeps.START_BEEP)
time.sleep(0.1)
print("quitting", flush=True)
"""


@pytest.mark.parametrize("play,printed", [
    ("time.sleep(0.2)", ["quitting", "sound over", "PortAudio ended"]),
    ("threading.Event().wait()", ["quitting"])], ids=["sound that ends", "stuck sound"])
def test_quitting_during_a_sound(play, printed):
    """The real exit order: TuxTalk waits for a sound that is ending before
    sounddevice ends PortAudio, and a sound that never ends doesn't hold up
    quitting: PortAudio is left to the system."""
    import subprocess
    import sys
    started = time.monotonic()
    ran = subprocess.run([sys.executable, "-c", _QUIT_DURING_A_SOUND.format(play=play)],
                         capture_output=True, text=True, timeout=20)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.splitlines() == printed
    assert time.monotonic() - started < 10


def test_sounddevice_ends_portaudio_as_often_as_it_counts_it_started(monkeypatch):
    """_at_exit leaves a stuck sound's PortAudio running by setting this
    count to 0; the installed sounddevice's own exit handler must go by it."""
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
    assert beeps._check_exit_handler(sounddevice)


def test_a_sounddevice_without_the_count_is_logged(caplog):
    caplog.set_level(logging.INFO)
    assert not beeps._check_exit_handler(types.SimpleNamespace())
    assert any("keeps no _initialized" in r.getMessage() for r in caplog.records)
