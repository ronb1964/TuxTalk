"""
Hands-free auto-stop: notice when the speaker has finished talking.

Used only for toggle recordings (tap the toggle key, talk, and it stops on its
own). A hold recording already ends when the key is released.

This decides WHEN to stop and never removes audio from the start or middle of
a recording. That distinction matters: Whisper's built-in voice-activity
filter is off because trimming audio clips the first words after a pause.
The only audio ever dropped is the silent tail this detector measured
(see tail_to_trim), which is safe and also keeps Whisper from inventing words
like "thank you" in trailing silence.

How it listens: each audio block's loudness (RMS) is compared with the room's
background noise, which it learns as it goes (the quietest level heard, rising
slowly if the room gets noisier). Nothing counts until the speaker has
actually talked for a moment, so a slow start is never mistaken for being
done, and a single click or bump does not count as talking.
"""
import numpy as np

# Speech must be this much louder than the background noise (about +8 dB)...
_SPEECH_OVER_NOISE = 2.5
# ...and never quieter than this, so a silent room with a near-zero noise
# floor does not turn breathing into "speech". int16 RMS; ~-40 dBFS.
_MIN_SPEECH_RMS = 300.0
# How much talking arms the detector: a cough or a click is shorter than this.
_SPEECH_TO_ARM_S = 0.3
# Keep this much of the silent tail when trimming, so the last word's natural
# decay is never cut.
_TAIL_KEEP_S = 0.3
# How fast the learned noise floor may rise per second (x1.5/s), so it follows
# a room that gets noisier without ever chasing the speaker's own voice up.
_NOISE_RISE_PER_S = 1.5


def block_rms(block, sample_rate: int):
    """(loudness as int16 RMS, duration in seconds) of one block of int16 samples."""
    samples = np.frombuffer(block, dtype=np.int16).astype(np.float64)
    if samples.size == 0:
        return 0.0, 0.0
    return float(np.sqrt(np.mean(samples ** 2))), samples.size / sample_rate


class NoiseFloor:
    """The room's background noise, learned as blocks arrive, and the loudness
    that counts as speech over it. Shared by auto-stop (SilenceDetector) and
    chunked dictation (chunking.PauseDetector), so both hear the room alike."""

    def __init__(self):
        self.level = None

    def threshold(self) -> float:
        """The loudness (int16 RMS) that counts as speech in this room."""
        return max(_MIN_SPEECH_RMS, (self.level or 0.0) * _SPEECH_OVER_NOISE)

    def update(self, rms: float, duration: float) -> bool:
        """Learn from one block; True if the block is speech.

        The floor is the quietest level heard so far, allowed to rise slowly
        while it is quiet. Speech has short gaps between syllables where only
        the room is heard, so even when someone starts talking the instant
        recording begins, the floor settles on the room within a word or two.
        """
        if self.level is None:
            # The first block may already be speech (talking the instant the
            # key goes down), so the first guess is capped at a quiet level;
            # in a noisier room it rises to the real noise within a second.
            self.level = min(rms, _MIN_SPEECH_RMS)
        elif rms < self.level:
            self.level = rms
        elif rms < self.threshold():
            self.level = min(rms, self.level * (_NOISE_RISE_PER_S ** duration))
        return rms >= self.threshold()


class SilenceDetector:
    """Feed it audio blocks as they arrive; it says when the speaker is done."""

    # With nothing said at all for this long, stop anyway (nothing_said is
    # set, and the recording is dropped rather than transcribed). Before the
    # first word the silence setting doesn't apply, so a slow start isn't cut
    # off; without this limit a recording with nothing said would never end.
    # Long enough to look at what you're answering first.
    NOTHING_SAID_S = 10.0

    def __init__(self, silence_seconds: float, sample_rate: int):
        self.silence_seconds = float(silence_seconds)
        self.sample_rate = int(sample_rate)
        self._floor = NoiseFloor()
        self.speech_time = 0.0
        self.silence_time = 0.0
        self.elapsed = 0.0
        self.fired = False
        self.nothing_said = False

    @property
    def noise_floor(self):
        return self._floor.level

    @noise_floor.setter
    def noise_floor(self, value):
        self._floor.level = value

    @property
    def armed(self) -> bool:
        """Whether the speaker has talked long enough for silence to count."""
        return self.speech_time >= _SPEECH_TO_ARM_S

    def _threshold(self) -> float:
        """The loudness (int16 RMS) that counts as speech in this room."""
        return self._floor.threshold()

    def feed(self, block) -> bool:
        """Add one block of int16 samples. True exactly once: when the speaker
        has been silent for silence_seconds after having talked."""
        rms, duration = block_rms(block, self.sample_rate)
        if duration == 0:
            return False
        self.elapsed += duration

        if self.fired:
            # A few blocks still arrive before the recording actually stops.
            # Keep measuring them so tail_to_trim() covers the whole silent end.
            self.silence_time = self.silence_time + duration if rms < self._threshold() else 0.0
            return False

        if self._floor.update(rms, duration):
            self.speech_time += duration
            self.silence_time = 0.0
            return False

        if not self.armed:
            if self.elapsed >= self.NOTHING_SAID_S:
                self.fired = self.nothing_said = True
                return True
            return False
        self.silence_time += duration
        if self.silence_time >= self.silence_seconds:
            self.fired = True
            return True
        return False

    def tail_to_trim(self) -> int:
        """Samples of measured silence to drop from the end once it fired."""
        if not self.fired:
            return 0
        return max(0, int((self.silence_time - _TAIL_KEEP_S) * self.sample_rate))


# --- An accidental tap of the hold key ---------------------------------------------
# A tap just over MIN_HOLD_MS records a fraction of a second of room noise and
# the key's click, and Parakeet can "hear" a word in it ("Thank you.", or
# "Yeah." from the processor engine for 0.4 s of loud hiss). Whisper has a no-speech score to catch this; Parakeet
# has none. So a recording this short is only transcribed if some of it is
# clearly louder than its own background.

TAP_MAX_S = 0.6             # longer recordings are always transcribed, silent or not
_TAP_BLOCK_S = 0.02
# Tuned on 26 real taps from a Sennheiser Profile desk mic (noisy background,
# int16 RMS 70-130; the key's thump reached 1.6-4.7x that for a few tens of
# ms) and 5,039 real speech clips of 0.3-0.5 s, plus real words laid over that
# mic's own background. At 2.5x: all 26 taps dropped, no speech
# at normal or loud level dropped, 3 of 138 very soft single words dropped.
# 1.5x let 10 of the 26 taps through, and Parakeet on the processor typed
# "Yeah." for them. A ratio needs the second route below: when a short clip is
# all speech, its quietest blocks are speech too (4x alone dropped 224 of 510
# half-second speech windows).
_TAP_SPEECH_OVER_NOISE = 2.5    # over the clip's quietest blocks
_TAP_MIN_SPEECH_RMS = 60.0      # int16 RMS, about -55 dBFS: far below any voice
_TAP_VOICE_RMS = 500.0          # about -36 dBFS: normal speaking level, speech on its own
_TAP_MIN_SPEECH_S = 0.08    # a key click is ~10 ms, a desk thump ~50; a word is longer


def _loud_seconds(audio_f32, sample_rate) -> float:
    """Seconds of *audio_f32* clearly louder than its own background.

    Loudness is judged against the clip's own background (its quietest
    blocks), so a quiet microphone saying a quick word still counts, and
    normal speaking volume counts on its own, so a word that fills the whole
    clip does too."""
    block = max(1, int(_TAP_BLOCK_S * sample_rate))
    count = len(audio_f32) // block
    if count < 3:
        return 0.0                  # under 60 ms: nothing to say in that
    blocks = np.asarray(audio_f32[:count * block], dtype=np.float64).reshape(count, block) * 32768.0
    rms = np.sqrt(np.mean(blocks * blocks, axis=1))
    background = np.percentile(rms, 20)
    loud = ((rms > max(background * _TAP_SPEECH_OVER_NOISE, _TAP_MIN_SPEECH_RMS))
            | (rms > _TAP_VOICE_RMS))
    return loud.sum() * _TAP_BLOCK_S


def is_speechless_tap(audio_f32, sample_rate) -> bool:
    """True for a recording shorter than TAP_MAX_S with no stretch of speech
    in it, which should be dropped rather than transcribed."""
    if len(audio_f32) >= TAP_MAX_S * sample_rate:
        return False
    return _loud_seconds(audio_f32, sample_rate) < _TAP_MIN_SPEECH_S


def has_no_speech(audio_f32, sample_rate) -> bool:
    """True for a recording of any length in which nobody talked: less than a
    moment of it is louder than its background, so a cough or a key click
    still counts as nothing said.

    It stands in for a no-speech score where the engine's own can't be
    trusted. It only decides whether stock phrases were invented, never
    whether a recording is transcribed: a long clip's background can be the
    speaker's own voice, so it is too blunt to drop real dictation on."""
    return _loud_seconds(audio_f32, sample_rate) < _SPEECH_TO_ARM_S
