"""Tells speech from other sound: hands-free's stop, Type at pause's cuts and
the checks of whether a word was said.

Loudness alone can't tell a soft word from a dryer's thump, a cough or a
kitchen's clatter, so those decisions used to rest on rules about the room's
noise that each new kind of noise broke. This asks a model trained on speech
instead: Silero VAD v6.2.3 (MIT, models/LICENSE; snakers4/silero-vad at commit
5cd7945676eb32225748052e2e6a0580e4686a08, its silero_vad_16k_op15.onnx), 1.3 MB,
run through the onnxruntime TuxTalk already ships for Parakeet. It judges 32 ms
frames of 16 kHz audio and costs under 0.1 ms of one core per frame.

Measured on 125 recorded readers in twelve noises, six of them recorded rooms,
from 40 to 10 dB under the voice: it hears 97 to 99% of speech at any voice
level and takes under 0.3% of machine and room noise for speech down to
20 dB, 1 to 3% at 10 dB. Other people talking is
speech to it too; no detector of speech can tell the speaker from a TV.
"""
import os
import threading

import numpy as np

from .logger import error_without_words, setup_logger

logger = setup_logger(__name__)

SAMPLE_RATE = 16000
# The frame the model judges, and the samples of the one before it that it
# sees with each (as Silero's own OnnxWrapper feeds it).
FRAME = 512
FRAME_S = FRAME / SAMPLE_RATE
_CONTEXT = 64

# Speech from this probability on, until it falls under SPEECH_ENDS: Silero's
# own threshold and the hysteresis of its VADIterator, so one uncertain frame
# in the middle of a word doesn't end it.
SPEECH = 0.5
SPEECH_ENDS = SPEECH - 0.15
# Anything at least this likely may be a word: where audio is dropped or
# trimmed, this is what has to be clear of it. Clipped 0.1 s fragments of
# speech reach it 98.6% of the time (0.5: 95.7%), while 0.33% of 0.16 s
# stretches of noise do.
MAYBE = 0.1

MODEL = os.path.join(os.path.dirname(__file__), "models", "silero_vad.onnx")

_session = None
_load_failed = False
_session_lock = threading.Lock()


class Unavailable(RuntimeError):
    """The model couldn't be loaded (onnxruntime or the model file missing
    or broken). Said in the log once, when it was tried."""


def _model():
    """The loaded model, shared by every detector: onnxruntime's run() may
    be called from several threads at once, and each detector carries its
    own state. One thread each, as the audio is judged as it arrives.

    Loaded on first use and tried once: a model that failed to load raises
    Unavailable from then on."""
    global _session, _load_failed
    with _session_lock:
        if _session is None and not _load_failed:
            try:
                import onnxruntime
                options = onnxruntime.SessionOptions()
                options.inter_op_num_threads = 1
                options.intra_op_num_threads = 1
                _session = onnxruntime.InferenceSession(
                    MODEL, sess_options=options, providers=["CPUExecutionProvider"])
            except Exception as e:
                _load_failed = True
                logger.error("The voice detector didn't load, so Type at pause doesn't type "
                             "at pauses, hands-free doesn't stop by itself, and no recording "
                             f"is dropped as silent: {error_without_words(e)}")
        if _session is None:
            raise Unavailable("the voice detector didn't load")
        return _session


def load() -> bool:
    """Load the model now, on the calling thread, so the first recording
    neither waits for it nor loads it on the audio thread, where a slow
    start would drop audio. False when it can't be loaded."""
    try:
        _model()
    except Unavailable:
        return False
    return True


class _Resampler:
    """Linear resampling to 16 kHz as blocks arrive, carrying the position
    from one block to the next, so a 44.1 kHz microphone's blocks join up
    without a click or a drift where each one ends."""

    def __init__(self, rate: int):
        self.step = rate / SAMPLE_RATE
        self.buffer = np.zeros(0, np.float32)
        self.position = 0.0     # of the next output sample, in self.buffer

    def feed(self, samples):
        """The 16 kHz samples *samples* completes, as floats; what falls past
        the last whole step waits for the next block."""
        self.buffer = np.concatenate([self.buffer, samples])
        if not self.buffer.size:
            return self.buffer
        count = max(0, int(np.ceil((len(self.buffer) - 1 - self.position) / self.step)))
        points = self.position + self.step * np.arange(count)
        out = np.interp(points, np.arange(len(self.buffer)), self.buffer).astype(np.float32)
        self.position += self.step * count
        # The last sample stays: the next output may fall between it and the
        # next block's first. Dropping more than the buffer held would move
        # the position back and repeat samples, a drift that grows.
        used = min(int(self.position), len(self.buffer) - 1)
        self.buffer = self.buffer[used:]
        self.position -= used
        return out


class VoiceDetector:
    """One recording's listener. feed() takes its int16 blocks as they come
    and judges each whole 32 ms frame. After it, talking is whether the last
    frame was speech, and since_maybe the time since the last frame that may
    have been (MAYBE)."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        """A listener for audio at *sample_rate*, nothing heard yet. The
        model is loaded here, never in feed(), which runs on the audio
        thread: Unavailable when it can't be."""
        self._session = _model()
        self.sample_rate = int(sample_rate)
        self._resampler = (None if self.sample_rate == SAMPLE_RATE
                           else _Resampler(self.sample_rate))
        self._state = np.zeros((2, 1, 128), np.float32)
        self._context = np.zeros((1, _CONTEXT), np.float32)
        self._pending = np.zeros(0, np.float32)
        self.talking = False
        self.since_maybe = 0.0

    def pending_seconds(self) -> float:
        """Audio fed and not judged yet: less than a frame."""
        return self._pending.size / SAMPLE_RATE

    def feed(self, block) -> list:
        """Judge the whole frames *block* (int16 samples, as bytes or an
        array) completes, the rest kept for the next: (speech, probability)
        for each, in order."""
        samples = np.frombuffer(block, dtype=np.int16).astype(np.float32) / 32768.0
        if self._resampler is not None:
            samples = self._resampler.feed(samples)
        joined = np.concatenate([self._pending, samples])
        whole = joined.size - joined.size % FRAME
        self._pending = joined[whole:].copy()
        return [self._judge(frame) for frame in joined[:whole].reshape(-1, FRAME)]

    def _judge(self, frame):
        x = np.concatenate([self._context, frame[None, :]], axis=1)
        out, self._state = self._session.run(None, {"input": x, "state": self._state,
                                               "sr": np.array(SAMPLE_RATE, np.int64)})
        self._context = x[:, -_CONTEXT:]
        probability = float(out[0, 0])
        self.talking = probability >= (SPEECH_ENDS if self.talking else SPEECH)
        self.since_maybe = 0.0 if probability >= MAYBE else self.since_maybe + FRAME_S
        return self.talking, probability


def speech_seconds(audio_f32, sample_rate: int, up_to: float | None = None) -> float:
    """Seconds of *audio_f32* (a whole recording, -1..1) that Silero judges
    at least SPEECH likely to be speech, counted only until they reach
    *up_to* when it's given: a minute of audio takes about 170 ms to judge,
    and whether a word was said is known at the first."""
    block = (np.clip(np.asarray(audio_f32), -1.0, 1.0) * 32767).astype(np.int16)
    detector = VoiceDetector(sample_rate)
    frames = 0
    for start in range(0, len(block), int(sample_rate)):        # a second at a time
        judged = detector.feed(block[start:start + int(sample_rate)].tobytes())
        frames += sum(probability >= SPEECH for _, probability in judged)
        if up_to is not None and frames * FRAME_S >= up_to:
            break
    return frames * FRAME_S
