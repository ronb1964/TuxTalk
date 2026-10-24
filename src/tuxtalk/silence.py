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
        if rms < 1.0:
            # Digital silence (many streams open with a block of zeros) says
            # nothing about the room. Learned, it held the floor at 0, which
            # can never rise, so steady room noise counted as speech and
            # hands-free never stopped by itself.
            return False
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
        self._busy = 0           # hold() calls not yet released

    def heard_speech(self) -> None:
        """Count speech said before the detector started (a word before the
        second tap of a double-tap): going quiet after it then stops the
        recording to type it, instead of dropping it as nothing said."""
        self.speech_time = max(self.speech_time, _SPEECH_TO_ARM_S)

    def hold(self) -> None:
        """TuxTalk is working on a phrase (Type at pause: transcribing, an AI
        rewrite, typing): the quiet that passes meanwhile is the user waiting
        for it, not the user finished. Called from another thread."""
        self._busy += 1

    def release(self) -> None:
        """The phrase is done and on screen: the quiet counts from now, so
        there's the full silence_seconds to read it (and say "press enter")."""
        self._busy = max(0, self._busy - 1)
        self.silence_time = 0.0

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
        if self._busy:
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
# "Yeah." from the processor engine for 0.4 s of loud hiss). Whisper has a
# no-speech score to catch this; Parakeet has none. So a recording this short
# is only transcribed if some of it is clearly louder than its own background.

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


def _blocks_rms(audio_f32, sample_rate):
    """The loudness (int16 RMS) of each whole _TAP_BLOCK_S block of *audio_f32*."""
    block = max(1, int(_TAP_BLOCK_S * sample_rate))
    count = len(audio_f32) // block
    blocks = np.asarray(audio_f32[:count * block], dtype=np.float64).reshape(count, block) * 32768.0
    return np.sqrt(np.mean(blocks * blocks, axis=1))


def _loud_seconds(audio_f32, sample_rate, ignoring_hz=None) -> float:
    """Seconds of *audio_f32* clearly louder than its own background, not
    counting what is mostly a tone of *ignoring_hz* Hz (the start beep).

    Loudness is judged against the clip's own background (its quietest
    blocks), so a quiet microphone saying a quick word still counts, and
    normal speaking volume counts on its own, so a word that fills the whole
    clip does too."""
    rms = _blocks_rms(audio_f32, sample_rate)
    if len(rms) < 3:
        return 0.0                  # under 60 ms: nothing to say in that
    background = np.percentile(rms, 20)
    loud = ((rms > max(background * _TAP_SPEECH_OVER_NOISE, _TAP_MIN_SPEECH_RMS))
            | (rms > _TAP_VOICE_RMS))
    if ignoring_hz is not None and loud.any():
        left = _blocks_rms(_without_tone(audio_f32, sample_rate, ignoring_hz), sample_rate)
        loud &= _share_left(rms, left, background) >= _TONE_SHARE_LEFT
    return loud.sum() * _TAP_BLOCK_S


# --- The start beep, picked up by the microphone ------------------------------------
# A microphone near the speakers records the start beep, and before the second
# tap of a double-tap TuxTalk asks whether a word was said. When the beep
# reached the recording can't be known (an output or a microphone can be
# slower than it reports), so it is told apart by its pitch: a block is the
# beep where filtering out the beep's pitch and harmonics takes away most of
# what makes the block louder than the room. Whether a block is loud is still
# judged on what was recorded, so the filter can't make a soft word softer.
#
# The beep is one exact pitch that fades in and out over 5 ms (beeps.FADE_S):
# 99% of its energy lies within 40 Hz of it, and all but 0.2% within 100 Hz.
# The harmonics a small speaker or an overloaded microphone adds sit on
# multiples of it; what clipping aliases off them was under 0.1% at 16, 44.1
# and 48 kHz. A narrower notch rings on as the beep starts and stops. At
# 80 Hz either side, eased over another 80 Hz, one 20 ms block of a beep
# still counted at most, a quarter of a word. Measured as tests/data/README.md
# describes, beeps from faint to clipped at 1.6 times full scale left at most
# 0.31 of their loudness over the room in every other block, and no block of
# 121 recordings of quick phrases and sentences kept less than 0.47.
_TONE_STOP_HZ = 80          # either side of the pitch and each harmonic: taken out
_TONE_EASE_HZ = 80          # beyond that: faded back in
_TONE_SHARE_LEFT = 0.4      # of a block's loudness over the room, left: not the beep


def _without_tone(audio_f32, sample_rate, hz):
    """*audio_f32* with a tone of *hz* Hz and its harmonics filtered out. It
    is padded with silence, at least 50 ms, so what the filter smears past
    its end doesn't wrap round to its start."""
    size = 1 << (len(audio_f32) + sample_rate // 20 - 1).bit_length()
    pitches = np.fft.rfftfreq(size, 1 / sample_rate)
    from_harmonic = np.abs((pitches + hz / 2) % hz - hz / 2)
    gain = 0.5 - 0.5 * np.cos(np.pi * np.clip((from_harmonic - _TONE_STOP_HZ) / _TONE_EASE_HZ,
                                              0, 1))
    gain[pitches < hz / 2] = 1.0    # what lies nearest 0 Hz is no harmonic
    return np.fft.irfft(np.fft.rfft(audio_f32, size) * gain, size)[:len(audio_f32)]


def _share_left(raw, left, background):
    """For each block, of its loudness (*raw*) over the room, the share still
    there in *left*, as amplitude. The room is the clip's *background*, but
    one as loud as speaking is the speaker, so the share is then of the
    whole block."""
    room = background if background < _TAP_VOICE_RMS else 0.0
    over_room = np.maximum(raw * raw - room * room, 1.0)
    return np.sqrt(np.clip(left * left - room * room, 0.0, None) / over_room)


# A working microphone is never this quiet: even in a silent room its own
# electronics hiss well above this. A muted, unplugged or dead one (a wireless
# mic whose battery died) sends digital silence. int16 peak; about -72 dBFS.
_DEAD_MIC_PEAK = 8.0
DEAD_MIC_MIN_S = 1.0


def is_dead_mic(audio_f32, sample_rate) -> bool:
    """True for a recording of at least a second that is digital silence
    from start to end: the microphone sent nothing at all, which is not the
    same as a quiet room."""
    if len(audio_f32) < DEAD_MIC_MIN_S * sample_rate:
        return False
    return float(np.max(np.abs(audio_f32))) * 32768.0 <= _DEAD_MIC_PEAK


def is_speechless_tap(audio_f32, sample_rate) -> bool:
    """True for a recording shorter than TAP_MAX_S with no stretch of speech
    in it, which should be dropped rather than transcribed."""
    if len(audio_f32) >= TAP_MAX_S * sample_rate:
        return False
    return not has_a_word(audio_f32, sample_rate)


def has_a_word(audio_f32, sample_rate, ignoring_hz=None) -> bool:
    """Whether *audio_f32* holds at least a word's worth of speech; a key
    click or a desk thump is too short to count. With *ignoring_hz*, the
    pitch of a beep that played into the recording, the beep isn't speech
    either; without it nothing is filtered."""
    return _loud_seconds(audio_f32, sample_rate, ignoring_hz) >= _TAP_MIN_SPEECH_S


def has_no_speech(audio_f32, sample_rate) -> bool:
    """True for a recording of any length in which nobody talked: less than a
    moment of it is louder than its background, so a cough or a key click
    still counts as nothing said.

    It stands in for a no-speech score where the engine's own can't be
    trusted. It only decides whether stock phrases were invented, never
    whether a recording is transcribed: a long clip's background can be the
    speaker's own voice, so it is too blunt to drop real dictation on."""
    return _loud_seconds(audio_f32, sample_rate) < _SPEECH_TO_ARM_S
