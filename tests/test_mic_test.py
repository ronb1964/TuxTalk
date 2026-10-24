"""Preferences' microphone test, recording: each recording gets only its own
stream's sound, a new one opens the microphone only once the last one's
stream is closed, and one called off while it waits never opens it. The
streams are fakes; nothing is recorded."""
import threading

import gi
import numpy as np
import pytest

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from tuxtalk import mic_test  # noqa: E402
from tuxtalk.mic_test import MicTest  # noqa: E402


class Microphone:
    """A stream that records nothing by itself: the test calls its callback.
    Its stop() can be held until *release* is set."""

    def __init__(self, opened, release, **settings):
        self.callback = settings["callback"]
        self.release = release
        self.closed = threading.Event()
        opened.append(self)

    def start(self):
        pass

    def stop(self, ignore_errors=True):
        self.release.wait(5)

    def close(self, ignore_errors=True):
        self.closed.set()


@pytest.fixture
def mic(monkeypatch):
    """A microphone test whose streams go in mic.opened, and whose GTK
    main-loop calls go in mic.scheduled, run with run_main_loop(mic)."""
    import sounddevice
    opened, scheduled, shown = [], [], []
    release = threading.Event()
    release.set()
    monkeypatch.setattr(mic_test.GLib, "idle_add", lambda fn, *args: scheduled.append((fn, args)))
    monkeypatch.setattr(mic_test.mic_stream, "input_samplerate", lambda device, rate: rate)
    monkeypatch.setattr(sounddevice, "InputStream",
                        lambda **settings: Microphone(opened, release, **settings))
    test = MicTest(lambda: None, lambda title, text: shown.append((title, text)))
    test.opened, test.scheduled, test.shown, test.release = opened, scheduled, shown, release
    yield test
    release.set()


def run_main_loop(mic):
    while mic.scheduled:
        fn, args = mic.scheduled.pop(0)
        fn(*args)


def after_the_close(mic):
    """Let the held stop end, then run what the wait for it handed to the
    main loop."""
    mic.release.set()
    assert mic.opened[0].closed.wait(2)
    for _ in range(200):
        if mic.scheduled:
            break
        threading.Event().wait(0.01)
    run_main_loop(mic)


def block(level):
    return np.full((160, 1), level, np.float32)


def test_a_stream_still_stopping_adds_nothing_to_the_next_recording(mic):
    """Stop hands the stream to another thread to close, and Start comes
    back at once: the old stream's callback still ran, and appended to the
    new recording (at the old microphone's rate if it changed) and moved
    its meter."""
    mic.start()
    old = mic.opened[0]
    old.callback(block(0.05), 160, None, None)
    mic.stop()
    assert mic._reopen.closing.wait(2)
    mic.start()
    new = mic.opened[1]
    old.callback(block(0.09), 160, None, None)     # late, from the old stream's thread
    new.callback(block(0.01), 160, None, None)
    run_main_loop(mic)                  # with the old stream's level, queued before Stop
    assert mic._take.levels == [pytest.approx(0.1)]
    mic.stop()
    assert np.allclose(mic.recorded_audio, 0.01)


def test_the_next_recording_waits_until_the_last_stream_is_closed(mic):
    """An ALSA hw: device is held until its stream is closed, so opening it
    again at once failed as busy ("Device unavailable")."""
    mic.release.clear()                            # stopping the stream takes a moment
    mic.start()
    mic.stop()
    mic.start()
    assert len(mic.opened) == 1 and not mic.start_btn.get_sensitive()
    after_the_close(mic)
    assert len(mic.opened) == 2 and mic.recording


@pytest.mark.parametrize("stop", ["stop", "close"])
def test_a_recording_called_off_while_it_waits_never_opens_the_microphone(mic, stop):
    """Start again while the last stream is still stopping, then Stop, or
    close the window: the start that was waiting still opened the mic
    afterwards, and held it from dictation."""
    mic.release.clear()
    mic.start()
    mic.stop()
    mic.start()
    getattr(mic, stop)()
    after_the_close(mic)
    assert len(mic.opened) == 1 and not mic.recording
    if stop == "stop":
        assert mic.start_btn.get_sensitive()


def test_replay_is_greyed_out_while_a_recording_waits_and_while_it_records(mic, monkeypatch):
    """A replay clicked while Start waited for the last stream played into
    the recording, and its end turned Start Recording back on during it: a
    second Start then replaced that recording, whose stream held the mic
    until Preferences closed."""
    played = []
    monkeypatch.setattr(mic_test.beeps, "play", lambda samples, rate, finished, called_off:
                        played.append(finished))
    monkeypatch.setattr(mic_test.GLib, "timeout_add", lambda ms, fn, *args: None)
    mic.start()
    mic.opened[0].callback(block(0.05), 160, None, None)
    run_main_loop(mic)
    mic.release.clear()
    mic.stop()
    assert mic.replay_btn.get_sensitive()
    mic.start()
    assert not mic.replay_btn.get_sensitive()
    mic.replay()                                   # had it still been clickable
    after_the_close(mic)
    assert mic.recording and not mic.replay_btn.get_sensitive()
    played[0](None)
    run_main_loop(mic)
    assert not mic.start_btn.get_sensitive() and not mic.replay_btn.get_sensitive()


def test_a_second_start_keeps_the_recording_under_way(mic):
    """Replacing it would leave its stream open, holding the microphone."""
    mic.start()
    take = mic._take
    mic.start()
    assert len(mic.opened) == 1 and mic._take is take


def test_stop_calls_off_a_recording_waiting_to_start(mic):
    """Start again while the last stream is still stopping: Stop stayed
    greyed out while it waited, so the user couldn't call it off, and the
    microphone opened by itself once the close ended."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)  # kept: freed, it destroys the buttons
    mic.build(box)
    mic.release.clear()
    mic.start_btn.clicked()
    mic.stop_btn.clicked()
    mic.start_btn.clicked()                        # waits for the close
    assert mic.stop_btn.get_sensitive()
    mic.stop_btn.clicked()
    assert mic.start_btn.get_sensitive() and not mic.stop_btn.get_sensitive()
    after_the_close(mic)
    assert len(mic.opened) == 1 and not mic.recording
    assert mic.start_btn.get_sensitive() and not mic.stop_btn.get_sensitive()


# --- The grade, and a noisy room ------------------------------------------------

def _record(mic, room, talk=True, seconds=4.0, rate=16000, talk_s=None):
    """Record room noise of RMS *room* (full scale 1) for *seconds*, with a
    voice talking if *talk*, for *talk_s* from the first second or else the
    middle half, in 0.1 s blocks, and stop."""
    rng = np.random.default_rng(1)
    t = np.arange(int(seconds * rate)) / rate
    start, end = (1.0, 1.0 + talk_s) if talk_s else (seconds / 4, seconds * 3 / 4)
    talking = (t > start) & (t < end) & ((t % 0.25) < 0.18)
    voice = 0.08 * np.sin(2 * np.pi * 180 * t) * talking * talk
    audio = (voice + rng.normal(0, room, t.size)).astype(np.float32).reshape(-1, 1)
    mic.start()
    for i in range(0, len(audio), rate // 10):
        mic.opened[-1].callback(audio[i:i + rate // 10], rate // 10, None, None)
    run_main_loop(mic)
    mic.stop()
    return mic.status.get_text(), mic.status.get_style_context()


def test_a_quiet_room_gets_the_grade_alone(mic):
    text, style = _record(mic, room=0.001)
    assert text == "✓ Good levels - should transcribe well!"
    assert style.has_class("level-good")


def test_a_quick_word_and_a_long_wait_is_still_a_quiet_room(mic):
    """Saying "testing" and waiting before Stop: most of the take is the
    room, but only the talking is measured against it, so a quiet room isn't
    called noisy (the whole take's loudest tenth was the room itself)."""
    text, style = _record(mic, room=0.001, seconds=8.0, talk_s=0.6)
    assert mic_test.NOISY_ROOM_TIP not in text
    assert style.has_class("level-good")


def test_a_noisy_room_is_named_with_what_helps(mic):
    """The voice about 10 dB over the room: quiet words would be taken for
    pauses, so the grade says so, in amber, instead of "should transcribe well"."""
    text, style = _record(mic, room=0.015)
    assert text == "✓ Good levels\n" + mic_test.NOISY_ROOM_TIP
    assert style.has_class("level-noisy") and not style.has_class("level-good")


def test_a_room_with_nobody_talking_is_only_too_quiet(mic):
    """With no speech there's nothing to judge the room against."""
    text, _ = _record(mic, room=0.0005, talk=False)
    assert text.startswith("⚠️ Too quiet") and mic_test.NOISY_ROOM_TIP not in text
