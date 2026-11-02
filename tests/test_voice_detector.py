"""The voice detector: real speech is heard, steady and bumpy noise isn't,
at any microphone rate and block size, and the bundled model is the one
the measurements were made with. The model loads before the audio thread
needs it, and a detector that fails, or a model that won't load, never
costs the rest of a recording.

Speech is a LibriSpeech sentence (CC BY 4.0, tests/data/README.md).
"""
import hashlib
import logging
import types
import wave
from pathlib import Path

import numpy as np
import pytest

from conftest import next_recording
from tuxtalk import (live_settings, recording, resampling, silence, type_at_pause,
                     voice_detector)
from tuxtalk import recording_state as rs
from tuxtalk.chunk_meter import PauseTest
from tuxtalk.voice_detector import FRAME_S, VoiceDetector, speech_seconds

DATA = Path(__file__).with_name("data")
SR = 16000
MODEL_SHA256 = "7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49"


def _read(name):
    with wave.open(str(DATA / name)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


READER = _read("librispeech-7902-96591-0018.wav")


def _quiet(seconds):
    return np.zeros(int(seconds * SR), np.float32)


def _at_rms(audio, int16_rms):
    """*audio* scaled so that its loudness is *int16_rms*."""
    return audio * (int16_rms / 32768) / np.sqrt(np.mean(audio ** 2))


def _sentence(int16_rms=1750.0):
    """The sentence in 0.5 s of quiet each side; 1750 is its own level."""
    return np.concatenate([_quiet(0.5), _at_rms(READER, int16_rms), _quiet(0.5)])


def _dryer(seconds, rng):
    """Rumble with a thump of 2.5 times its level every 0.8 s, as a tumble
    dryer: loud enough, in its bumps, to pass for talking by loudness."""
    n = int(seconds * SR)
    f = np.fft.rfftfreq(n, 1 / SR)
    x = np.fft.irfft(np.fft.rfft(rng.normal(0, 1, n)) / (1 + f / 200), n)
    for start in np.arange(0.3, seconds - 0.1, 0.8):
        i = int(start * SR)
        x[i:i + int(0.1 * SR)] *= 2.5
    return (x / np.sqrt(np.mean(x ** 2))).astype(np.float32)


def test_the_bundled_model_is_the_one_measured():
    data = Path(voice_detector.MODEL).read_bytes()
    assert hashlib.sha256(data).hexdigest() == MODEL_SHA256
    assert "MIT License" in (Path(voice_detector.MODEL).parent / "LICENSE").read_text()


def test_hears_most_of_a_sentence():
    assert speech_seconds(_sentence(), SR) >= 0.8 * len(READER) / SR


@pytest.mark.parametrize("int16_rms", [300, 100])
def test_a_quiet_voice_is_heard_as_well_as_a_normal_one(int16_rms):
    """Hands-free never heard a voice under int16 RMS 300 by loudness
    (CODE_HEALTH row 61); 100 is quieter than a working microphone gives."""
    assert speech_seconds(_sentence(int16_rms), SR) >= speech_seconds(_sentence(), SR) - 0.2


@pytest.mark.parametrize("kind", ["hiss", "dryer"])
def test_noise_alone_is_not_speech(kind):
    rng = np.random.default_rng(1)
    audio = (rng.normal(0, 0.05, 5 * SR).astype(np.float32) if kind == "hiss"
             else _dryer(5.0, rng) * 0.05)
    assert speech_seconds(audio, SR) <= 2 * FRAME_S


def test_speech_in_a_dryer_20_db_under_it_is_still_heard():
    rng = np.random.default_rng(2)
    sentence = _sentence()
    dryer = _dryer(len(sentence) / SR, rng) * (1750 / 32768) * 10 ** (-20 / 20)
    assert speech_seconds(sentence + dryer, SR) >= speech_seconds(sentence, SR) - 0.2


@pytest.mark.parametrize("rate", [44100, 48000])
def test_a_microphone_at_another_rate_is_heard_the_same(rate):
    """Resampled linearly on the way in, as the engine's audio is: at 44.1 kHz
    a frame or a few more can count, never fewer."""
    sentence = _sentence()
    at_16k = speech_seconds(sentence, SR)
    at_rate = speech_seconds(resampling.resample(sentence, SR, rate), rate)
    assert at_16k - FRAME_S <= at_rate <= at_16k + 5 * FRAME_S


def test_block_size_doesnt_change_the_verdicts():
    """Some onboard inputs deliver 6 ms blocks; the frames judged must be
    the same as when the recording arrives in one piece."""
    block = (_sentence() * 32767).astype(np.int16)
    whole = [talking for talking, _ in VoiceDetector().feed(block.tobytes())]
    detector, pieces = VoiceDetector(), []
    for i in range(0, len(block), 96):
        pieces += [talking for talking, _ in detector.feed(block[i:i + 96].tobytes())]
    assert pieces == whole


@pytest.mark.parametrize("rate", [44100, 48000])
@pytest.mark.parametrize("block", [0, 256, 960, 1023])
def test_a_microphone_at_another_rate_keeps_time_in_any_block_size(rate, block):
    """In blocks of 256 a 48 kHz microphone's 10 s came out as 160,312
    samples at 16 kHz: the detector's time ran ahead of the microphone's,
    and after a few minutes a cut fell inside the next word."""
    audio = np.sin(np.arange(rate * 10) / 37.0).astype(np.float32)
    whole = voice_detector._Resampler(rate).feed(audio)
    resampler = voice_detector._Resampler(rate)
    if block == 0:                                  # an empty block first, then the rest
        pieces = [resampler.feed(audio[:0]), resampler.feed(audio)]
    else:
        pieces = [resampler.feed(audio[i:i + block]) for i in range(0, len(audio), block)]
    streamed = np.concatenate(pieces)
    assert len(streamed) == len(whole) == 160000
    assert np.allclose(streamed, whole, atol=1e-6)


def test_time_since_maybe_speech_counts_the_quiet_after_the_last_word():
    """1 s of quiet follows the sentence; the reader's own pause after its
    last word (about 0.1 s) is quiet too."""
    detector = VoiceDetector()
    block = (np.concatenate([_at_rms(READER, 1750), _quiet(1.0)]) * 32767).astype(np.int16)
    detector.feed(block.tobytes())
    assert 1.0 - FRAME_S <= detector.since_maybe <= 1.25


# --- When the detector fails ---------------------------------------------------

class _Broken:
    """A detector whose every feed() fails, as a bug in one would."""

    def feed(self, block):
        raise ValueError("broken")


@pytest.fixture
def recording_now(monkeypatch):
    """A recording under way at 16 kHz, nothing heard yet."""
    monkeypatch.setattr(recording, "indicator", None)
    monkeypatch.setattr(rs.state, "is_recording", True)
    return next_recording()


def _hear(rec, blocks=3):
    for _ in range(blocks):
        recording._sd_callback(rec, np.zeros((320, 1), np.int16), 320, None, None)


@pytest.mark.parametrize("which", ["chunker", "silence"])
def test_a_failing_detector_doesnt_end_the_recording(recording_now, which, caplog):
    """sounddevice ends the stream on an error in its callback: what's said
    after it was lost, and hands-free never stopped."""
    setattr(recording_now, which, _Broken())
    with caplog.at_level(logging.ERROR):
        _hear(recording_now)
    assert len(recording_now.frames) == 3 and getattr(recording_now, which) is None
    assert len([r for r in caplog.records if "stopped listening" in r.message]) == 1


def test_hands_free_taken_away_while_a_block_is_judged_logs_no_failure(recording_now, caplog):
    """A recording found silent (_dropped_as_silent) loses its hands-free on
    another thread: read twice, the second read found nothing to feed, and
    "Hands-free stopped listening" was logged for a detector that never failed."""
    class Quiet:
        def feed(self, block):
            return False
    detector = Quiet()

    class TakenAway(type(recording_now)):
        reads = 0

        @property
        def silence(self):
            TakenAway.reads += 1
            return detector if TakenAway.reads == 1 else None

        @silence.setter
        def silence(self, value):
            pass
    recording_now.__class__ = TakenAway
    with caplog.at_level(logging.ERROR):
        _hear(recording_now, blocks=1)
    assert not [r for r in caplog.records if "stopped listening" in r.message]


def test_the_audio_thread_never_loads_the_model(monkeypatch):
    """Loaded when Type at pause is armed, before the microphone's first
    block: a cold load in the audio callback overran its buffer."""
    monkeypatch.setattr(live_settings, "chunk_pause_seconds", 0.8)
    rec = types.SimpleNamespace(edit_mode=False, samplerate=16000, chunker=None)
    type_at_pause.arm_chunker(rec)

    def no_loading_now():
        raise AssertionError("the model was loaded while audio was judged")
    monkeypatch.setattr(voice_detector, "_model", no_loading_now)
    rec.chunker.feed(np.zeros(1600, np.int16).tobytes())


@pytest.fixture
def no_model(monkeypatch, tmp_path):
    """A model file that isn't there, never loaded yet in this process."""
    monkeypatch.setattr(voice_detector, "MODEL", str(tmp_path / "missing.onnx"))
    monkeypatch.setattr(voice_detector, "_session", None)
    monkeypatch.setattr(voice_detector, "_load_failed", False)


def test_a_model_that_wont_load_is_said_once(no_model, caplog):
    with caplog.at_level(logging.ERROR):
        assert not voice_detector.load()
        assert not voice_detector.load()
    assert len([r for r in caplog.records if "didn't load" in r.message]) == 1


def test_without_the_model_nothing_is_dropped_as_silent(no_model):
    quiet = np.zeros(SR, np.float32)
    assert silence.has_a_word(quiet, SR) and not silence.has_no_speech(quiet, SR)


def test_without_the_model_type_at_pause_types_the_recording_whole(no_model, monkeypatch):
    monkeypatch.setattr(live_settings, "chunk_pause_seconds", 0.8)
    rec = types.SimpleNamespace(edit_mode=False, samplerate=16000, chunker=None)
    type_at_pause.arm_chunker(rec)
    assert rec.chunker is None


def test_without_the_model_a_double_tap_records_on(no_model, recording_now, monkeypatch):
    """Hands-free can't hear the speaker finish: the recording goes on until
    the next tap, not stopped by the second tap of the double-tap."""
    monkeypatch.setattr(live_settings, "auto_stop_seconds", 2.0)
    recording_now.toggle_started_at = recording.time.time()
    assert recording.toggle_pressed_while_recording(False)
    assert recording_now.silence is None


def test_the_preferences_test_stops_when_its_detector_fails(caplog):
    test = PauseTest(SR, 0.8)
    test._detector = _Broken()
    with caplog.at_level(logging.ERROR):
        assert not test.feed(np.zeros(320, np.int16))
        assert not test.feed(np.zeros(320, np.int16))
    assert test.failed
    assert len([r for r in caplog.records if "stopped listening" in r.message]) == 1


def test_a_word_is_found_without_judging_the_whole_recording(monkeypatch):
    """has_a_word and has_no_speech run after every transcription: a long
    dictation's audio was judged whole again, about 170 ms a minute."""
    judged = []
    feed = VoiceDetector.feed
    monkeypatch.setattr(VoiceDetector, "feed",
                        lambda self, block: judged.append(len(block)) or feed(self, block))
    audio = np.concatenate([_sentence(), _quiet(60.0)])
    assert silence.has_a_word(audio, SR) and not silence.has_no_speech(audio, SR)
    assert sum(judged) < 2 * 5 * SR * 2         # two checks, each well inside the sentence
