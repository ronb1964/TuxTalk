"""The voice detector: real speech is heard, steady and bumpy noise isn't,
at any microphone rate and block size, and the bundled model is the one
the measurements were made with.

Speech is a LibriSpeech sentence (CC BY 4.0, tests/data/README.md).
"""
import hashlib
import wave
from pathlib import Path

import numpy as np
import pytest

from tuxtalk import resampling, voice_detector
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


def test_time_since_maybe_speech_counts_the_quiet_after_the_last_word():
    """1 s of quiet follows the sentence; the reader's own pause after its
    last word (about 0.1 s) is quiet too."""
    detector = VoiceDetector()
    block = (np.concatenate([_at_rms(READER, 1750), _quiet(1.0)]) * 32767).astype(np.int16)
    detector.feed(block.tobytes())
    assert 1.0 - FRAME_S <= detector.since_maybe <= 1.25
