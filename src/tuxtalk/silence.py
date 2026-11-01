"""
Whether, and when, someone is talking: hands-free auto-stop, judged by the
voice detector, and from the loudness of 20 ms blocks of audio, an
accidental tap of the hold key, a start beep the microphone picked up, a
dead microphone, and a recording in which nobody talked.

Hands-free auto-stop is used only for toggle recordings (tap the toggle key,
talk, and it stops on its own). A hold recording already ends when the key
is released.

Auto-stop decides WHEN to stop and never removes audio from the start or
middle of a recording. That distinction matters: Whisper's built-in
voice-activity filter is off because trimming audio clips the first words
after a pause. The only audio ever dropped is the silent tail this detector
measured (see tail_to_trim), which is safe and also keeps Whisper from
inventing words like "thank you" in trailing silence.

How hands-free listens: the voice detector says, every 32 ms, whether that
is speech, so a dryer, a fan or a kitchen isn't talking. Nothing counts until
the speaker has actually talked for a moment, so a slow start is never
mistaken for being done.
"""
import threading

import numpy as np

from .audio_windows import WINDOW_S, RoomLoudness, window_length
from .voice_detector import FRAME_S, VoiceDetector

# How much talking arms hands-free: a cough or a click is shorter than this.
_SPEECH_TO_ARM_S = 0.3
# Kept after the last frame that may have been speech (voice_detector.MAYBE)
# when hands-free trims the quiet end, so a soft last sound is never cut.
_TAIL_KEEP_S = 0.7
# A breath before the next sentence isn't speech to the voice detector, and
# with a short wait the pause and the breath together ended the recording
# before it. So once the quiet reaches the wait, a sound loudness heard in the
# last _BREATH_S puts the stop off, for _BREATH_S at most.
_BREATH_S = 0.7
# The quietest a voice can be and still count as talking in the mic test's
# noisy-room check, int16 RMS (about -40 dBFS).
_MIN_SPEECH_RMS = 300.0


class SilenceDetector:
    """Feed it audio blocks as they arrive; it says when the speaker is done:
    quiet for silence_seconds, by the voice detector, after talking.

    Measured on 125 recorded readers in seven noises (six of them recorded
    rooms and a dryer) from 40 to 10 dB under the voice, with a 2 s wait and
    sentences 1.2 s apart (.superpowers/vad-2026-11-01, sim_vad.py hf):
    where the loudness rules it replaces never stopped by themselves (236
    of 2,268 recordings, as next to a dryer or in a kitchen), it stops; noise
    sent to the engine after the last word fell from up to 4 s to under 0.9
    s; recordings losing speech from 37 to 8, which lost a soft last sound
    under the noise. Stopping before the speaker was done stayed about as
    often as before at waits of 2 and 3 s. Other people talking is speech to
    it, so near a TV it doesn't stop, as before.
    """

    # With nothing said at all for this long, stop anyway (nothing_said is
    # set, and the recording is dropped rather than transcribed). Before the
    # first word the silence setting doesn't apply, so a slow start isn't cut
    # off; without this limit a recording with nothing said would never end.
    # Long enough to look at what you're answering first.
    NOTHING_SAID_S = 10.0

    def __init__(self, silence_seconds: float, sample_rate: int):
        """A detector for one recording, stopping after *silence_seconds* of
        quiet, of audio at *sample_rate*; nothing heard yet."""
        self.silence_seconds = float(silence_seconds)
        self.sample_rate = int(sample_rate)
        self._voice = VoiceDetector(self.sample_rate)
        self._loudness = RoomLoudness(self.sample_rate)
        self.speech_time = 0.0
        self.silence_time = 0.0
        self.elapsed = 0.0
        self.fired = False
        self.nothing_said = False
        self._busy = 0           # hold() calls not yet released
        self._sound_quiet = 0.0  # since loudness last heard a sound that could be a word
        # A block reached NOTHING_SAID_S and was kept for the speech in it.
        self._past_nothing_said = False
        self._said_before = False
        # feed() runs on the audio thread, hold() and release() on Type at
        # pause's phrase worker.
        self._lock = threading.Lock()

    def heard_speech(self) -> None:
        """Count speech said before the detector started (a word before the
        second tap of a double-tap): going quiet after it then stops the
        recording to type it, instead of dropping it as nothing said."""
        self._said_before = True

    def hold(self) -> None:
        """TuxTalk is working on a phrase (Type at pause: transcribing, an AI
        rewrite, typing): the quiet that passes meanwhile is the user waiting
        for it, not the user finished. Called from another thread."""
        with self._lock:
            self._busy += 1

    def release(self) -> None:
        """The phrase is done and on screen: the quiet counts from now, so
        there's the full silence_seconds to read it (and say "press enter")."""
        with self._lock:
            self._busy = max(0, self._busy - 1)
            self.silence_time = 0.0

    @property
    def armed(self) -> bool:
        """Whether the speaker has talked long enough for silence to count."""
        return self._said_before or self.speech_time >= _SPEECH_TO_ARM_S

    def feed(self, block) -> bool:
        """Add one block of int16 samples, of any size. True exactly once:
        when the speaker has been silent for silence_seconds after having
        talked, judged in the voice detector's frames, the rest of the block
        kept for the next.

        Nothing said for NOTHING_SAID_S is decided once the whole block is
        heard: speech in any frame of the block that reaches it means
        something was said. Only that block: from the next one on, nothing
        said stops the recording unless the speech has armed the detector,
        so clicks in later blocks can't put the drop off block after block."""
        with self._lock:
            for sound in self._loudness.feed(block):
                self._sound_quiet = 0.0 if sound else self._sound_quiet + WINDOW_S
            verdicts = {self._frame(talking) for talking, _ in self._voice.feed(block)}
            if "nothing said" in verdicts and not self.fired:
                if "speech" not in verdicts or self._past_nothing_said:
                    self.fired = self.nothing_said = True
                    return True
                self._past_nothing_said = True
            return "stop" in verdicts

    def _frame(self, talking: bool):
        """Count one frame the voice detector judged: "stop" when it ends the
        recording, "nothing said" when nothing has been said for
        NOTHING_SAID_S, "speech" when it is talking, else None. Called with
        the lock held."""
        self.elapsed += FRAME_S
        if self.fired:
            return None
        if talking:
            self.speech_time += FRAME_S
            self.silence_time = 0.0
            return "speech"
        if self._busy:
            self.silence_time = 0.0
            return None
        if not self.armed:
            return "nothing said" if self.elapsed >= self.NOTHING_SAID_S else None
        self.silence_time += FRAME_S
        waited = self.silence_time >= self.silence_seconds
        if waited and (self._sound_quiet >= _BREATH_S
                       or self.silence_time >= self.silence_seconds + _BREATH_S):
            self.fired = True
            return "stop"
        return None

    def tail_to_trim(self) -> int:
        """Samples of the quiet end to drop once it fired: those after the
        last frame that may have been speech, less _TAIL_KEEP_S. A few blocks
        arrive after the stop, so this is read once the recording ends."""
        with self._lock:
            # Nothing said: what it took for quiet may be a voice it missed,
            # which Type at pause heard (recording._dropped_as_silent).
            if not self.fired or self.nothing_said:
                return 0
            tail = self._voice.since_maybe + self._voice.pending_seconds()
            return max(0, int((tail - _TAIL_KEEP_S) * self.sample_rate))


# --- An accidental tap of the hold key ---------------------------------------------
# A tap just over MIN_HOLD_MS records a fraction of a second of room noise and
# the key's click, and Parakeet can "hear" a word in it ("Thank you.", or
# "Yeah." from the processor engine for 0.4 s of loud hiss). Whisper has a
# no-speech score to catch this; Parakeet has none. So a recording this short
# is only transcribed if some of it is clearly louder than its own background.

TAP_MAX_S = 0.6             # longer recordings are always transcribed, silent or not
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


def _room(rms):
    """The loudness of a clip's room: that of its quietest blocks (the 20th
    percentile), which a quick word doesn't reach."""
    return np.percentile(rms, 20)


def _windows_rms(audio_f32, sample_rate):
    """The loudness (int16 RMS) of each whole WINDOW_S window of *audio_f32*."""
    size = window_length(sample_rate)
    count = len(audio_f32) // size
    windows = np.asarray(audio_f32[:count * size], dtype=np.float64).reshape(count, size)
    windows = windows * 32768.0
    return np.sqrt(np.mean(windows * windows, axis=1))


def _loud_seconds(audio_f32, sample_rate, ignoring_hz=None) -> float:
    """Seconds of *audio_f32* clearly louder than its own background, not
    counting what is mostly a tone of *ignoring_hz* Hz (the start beep).

    Loudness is judged against the clip's own background (its quietest
    blocks), so a quiet microphone saying a quick word still counts, and
    normal speaking volume counts on its own, so a word that fills the whole
    clip does too. So does a steady room that loud (int16 RMS about 480 and
    up: a fan right by the microphone): by loudness alone it can't be told
    from such a word, and asking each block to stand a quarter above the
    room as well dropped 6% of 0.3 to 0.5 s stretches of real speech."""
    rms = _windows_rms(audio_f32, sample_rate)
    if len(rms) < 3:
        return 0.0                  # under 60 ms: nothing to say in that
    loud = ((rms > max(_room(rms) * _TAP_SPEECH_OVER_NOISE, _TAP_MIN_SPEECH_RMS))
            | (rms > _TAP_VOICE_RMS))
    if ignoring_hz is not None and loud.any():
        left = _windows_rms(_without_tone(audio_f32, sample_rate, ignoring_hz), sample_rate)
        loud &= _share_left(rms, left) >= _TONE_SHARE_LEFT
    return loud.sum() * WINDOW_S


# --- The start beep, picked up by the microphone ------------------------------------
# A microphone near the speakers records the start beep, and before the second
# tap of a double-tap TuxTalk asks whether a word was said. When the beep
# reached the recording can't be known (an output or a microphone can be
# slower than it reports), so it is told apart by its pitch: a block is the
# beep where filtering out the beep's pitch and harmonics takes away most of
# what makes the block louder than the room. Whether a block is loud is still
# judged on what was recorded.
#
# The beep is one exact pitch that fades in and out over 5 ms (beeps.FADE_S):
# 99% of its energy lies within 40 Hz of it, and all but 0.2% within 100 Hz.
# The harmonics a small speaker or an overloaded microphone adds sit on
# multiples of it: clipped at 1.6 times full scale, 2.8% of the beep is in
# them and under 0.2% lies more than 160 Hz from a multiple, at 16, 44.1 and
# 48 kHz. A narrower notch rings on as the beep starts and stops. Measured as
# tests/data/README.md describes, in rooms of int16 RMS 6 to 450 and at those
# three rates: a beep from faint to clipped at 4 times full scale adds one
# 20 ms block at most, a quarter of a word, and the filter raises how loud a
# word must be to count by at most 0.5 dB, or 2 dB in a room of 450, where
# the room's own unevenness is as loud as a soft word over it. A share of
# 0.48 let a beep add two blocks; 0.54 cost a soft word more.
_TONE_STOP_HZ = 80          # either side of the pitch and each harmonic: taken out
_TONE_EASE_HZ = 80          # beyond that: faded back in
_TONE_SHARE_LEFT = 0.5      # of a block's loudness over the room, left: not the beep


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


def _share_left(raw, left):
    """For each block, of its loudness over the room as recorded (*raw*),
    the share still over the room once the beep is filtered out (*left*), as
    amplitude. Each is measured against its own room: the filter takes part
    of the room's noise too, and that is no part of a word."""
    over_room = np.maximum(raw * raw - _room(raw) ** 2, 1.0)
    return np.sqrt(np.clip(left * left - _room(left) ** 2, 0.0, None) / over_room)


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


# --- A noisy room, for the microphone test ------------------------------------
# How far a test recording's speech stands over its room, in dB: the 90th
# percentile of its blocks while talking (from the first to the last 0.1 s
# clearly louder than the room's quietest moments) over the room's median
# outside that stretch. Only that stretch is speech: a quick "testing" in an
# eight-second take left the whole take's 90th percentile in the room. The
# room is its median, not its quietest moments: a kitchen, a cafe or traffic
# goes quiet now and then between its clatter, and against those moments a
# voice stood well over a room in which hands-free never stopped by itself.
# Measured with 125 recorded readers in six recorded rooms (DEMAND: kitchen,
# living room, laundry, office, cafeteria, traffic) and five made ones, at
# three voice levels. In the recorded rooms hands-free (1 to 3 s waits, 702
# runs at each level) stops by itself in 96% with the room 30 dB under the
# voice, 81% at 25 dB, 45% at 22 and 31% at 20; in steady noise it does stop,
# but cuts the speaker off mid-sentence more often as it nears the voice
# (white noise: 15% of runs at 30 dB, 27% at 25, 38% at 22, 45% at 20).
# With a second or more of talking, of 1,800 takes in the recorded rooms at
# each level, it warns in 8 at 40 dB (a reader whose first second is a soft
# start), 59 at 30, 214 at 25, 1,196 at 22 and 1,416 at 20; over the
# quietest moments, at 16 dB, it warned in 0, 25, 47, 77 and 86.
NOISY_ROOM_DB = 28.0
# Blocks this much louder than the room's quietest moments (+8 dB), and never
# quieter than speech's minimum, are clearly talking when they last
# _TALKING_RUN_S...
_TALKING_OVER_ROOM = 2.5
_TALKING_RUN_S = 0.1
# ...and talking for less than this, from the first to the last, is too
# little to tell how far over the room it stands: a take's first fraction of
# a second is often a word's soft start, and a quiet room was called noisy.
_TALKING_TO_JUDGE_S = 1.0
# With less than this outside the talking (talking from Start to Stop), what
# is outside is mostly the soft start and end of the words, not the room: a
# quiet room was called noisy in 33 of 41 such takes, and half a second still
# left 7. With a second, 1 or 2 (the quietest moments, 0 or 1).
_ROOM_TO_JUDGE_S = 1.0
# A shorter take can't hold both.
_SHORTEST_TAKE_S = _TALKING_TO_JUDGE_S + _ROOM_TO_JUDGE_S
# The room outside the talking is heard in the pauses between its words too.
# When those pauses are much quieter (+15 dB), what is outside is soft words
# at the start or end, not the room. In the recorded and made rooms the room
# stood at most 13.75 dB over the pauses, in all 16,180 takes with a second
# of talking and of room around it, at every level.
_ROOM_OVER_PAUSES = 10 ** (15 / 20)


def speech_over_room_db(audio_f32, sample_rate):
    """How much louder a recording's speech is than its room, in dB, or None
    without a second of clear talking and a second of the room around it,
    or when what is around it is louder than the pauses between the words.
    Under NOISY_ROOM_DB the room is noisy for that voice: in recorded rooms
    like it hands-free stops by itself about half the time or less, and in
    steady noise it cuts the speaker off mid-sentence more often."""
    rms = _windows_rms(audio_f32, sample_rate)
    if len(rms) * WINDOW_S < _SHORTEST_TAKE_S:
        return None
    loud = rms >= max(_MIN_SPEECH_RMS, np.percentile(rms, 10) * _TALKING_OVER_ROOM)
    run = max(1, round(_TALKING_RUN_S / WINDOW_S))
    starts = np.flatnonzero(np.convolve(loud, np.ones(run, dtype=int), "valid") == run)
    if starts.size == 0:
        return None
    first, last = starts[0], starts[-1] + run
    outside = np.concatenate([rms[:first], rms[last:]])
    if ((last - first) * WINDOW_S < _TALKING_TO_JUDGE_S
            or len(outside) * WINDOW_S < _ROOM_TO_JUDGE_S):
        return None
    room, talking = np.median(outside), rms[first:last]
    if room > np.percentile(talking, 10) * _ROOM_OVER_PAUSES:
        return None
    return float(20 * np.log10(max(np.percentile(talking, 90), 1.0) / max(room, 1.0)))
