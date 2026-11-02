"""
Whether, and when, someone is talking. The voice detector judges
hands-free auto-stop, an accidental tap of the hold key with no word in it,
and a recording in which nobody talked; the loudness of 20 ms windows
judges a dead microphone and a noisy room in the microphone test.

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
import collections
import threading

import numpy as np

from .audio_windows import WINDOW_S, RoomLoudness, window_length
from .voice_detector import FRAME_S, Unavailable, VoiceDetector, speech_seconds

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

    Simulated with 125 recorded readers in seven noises (six recorded rooms
    and a dryer) from 40 to 10 dB under the voice, a 2 s wait and sentences
    1.2 s apart, 2,268 recordings, against stopping by loudness: it stops
    in the 236 that loudness never stopped (next to a dryer, in a kitchen);
    noise sent to the engine after the last word is under 0.9 s, where
    loudness sent up to 4 s; 8 recordings lose speech (a soft last sound
    under the noise), against 37. It stops before the speaker is done about
    as often, at waits of 2 and 3 s. Other people talking is speech to it,
    so near a TV it doesn't stop by itself.
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
        # Loudness windows measured and not yet reached by the voice
        # detector's frames: (where each ends, in seconds; a sound in it).
        self._windows = collections.deque()
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
            loudness = self._loudness
            start = loudness.measured
            for n, sound in enumerate(loudness.feed(block)):
                self._windows.append(((start + (n + 1) * loudness.window) / self.sample_rate,
                                      sound))
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
        # Loudness up to this frame's end, not the rest of the block: a sound
        # heard ahead put a stop off that smaller blocks made 0.7 s sooner.
        while self._windows and self._windows[0][0] <= self.elapsed + 1e-9:
            sound = self._windows.popleft()[1]
            self._sound_quiet = 0.0 if sound else self._sound_quiet + WINDOW_S
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


# --- Was a word said? --------------------------------------------------------------
# An accidental tap of the hold key records a fraction of a second of room
# noise and the key's click, and Parakeet can "hear" a word in it ("Thank you.",
# "Yeah."). Whisper has a no-speech score to catch this; Parakeet has none. So
# the voice detector says whether a word was said, which loudness couldn't:
# measured on real everyday sounds (ESC-50, 40 each of coughs, sneezes,
# breaths, laughs, claps, knocks, mouse clicks and typing) and real short words
# (English and German, said normally and fast), at two levels in a quiet and a
# noisy room, every word counted;
# clicks, typing and claps 0 to 3% of the time, where loudness counted every
# one; knocks and breaths 5 to 20%; laughs up to 40%; coughs 45 to 70% and
# sneezes 52 to 80%, voiced as they are. TuxTalk's start beep, which the
# microphone may pick up before a double-tap's second tap, never counts.

TAP_MAX_S = 0.6             # longer recordings are always transcribed, silent or not
# A word's worth of speech: three of the voice detector's frames (the loudness
# rule before it asked for 0.08 s).
_WORD_S = 0.09


def _windows_rms(audio_f32, sample_rate):
    """The loudness (int16 RMS) of each whole WINDOW_S window of *audio_f32*."""
    size = window_length(sample_rate)
    count = len(audio_f32) // size
    windows = np.asarray(audio_f32[:count * size], dtype=np.float64).reshape(count, size)
    windows = windows * 32768.0
    return np.sqrt(np.mean(windows * windows, axis=1))


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


def has_a_word(audio_f32, sample_rate) -> bool:
    """Whether *audio_f32* holds at least a word's worth of speech."""
    return _speech_up_to(audio_f32, sample_rate, _WORD_S) >= _WORD_S


def _speech_up_to(audio_f32, sample_rate, seconds: float) -> float:
    """voice_detector.speech_seconds, counted up to *seconds*. That is the
    answer too when the voice detector didn't load: without it, nothing
    is dropped as speechless."""
    try:
        return speech_seconds(audio_f32, sample_rate, up_to=seconds)
    except Unavailable:
        return seconds


def has_no_speech(audio_f32, sample_rate) -> bool:
    """True for a recording of any length in which nobody talked: less than a
    moment of it is speech, so a cough or a key click still counts as nothing
    said.

    It stands in for a no-speech score where the engine's own can't be
    trusted. It only decides whether stock phrases were invented, never
    whether a recording is transcribed: a long clip's background can be the
    speaker's own voice, so it is too blunt to drop real dictation on."""
    return _speech_up_to(audio_f32, sample_rate, _SPEECH_TO_ARM_S) < _SPEECH_TO_ARM_S


# --- A noisy room, for the microphone test ------------------------------------
# How far a test recording's speech stands over its room, in dB: the 90th
# percentile of its blocks while talking (from the first to the last 0.1 s
# clearly louder than the room's quietest moments) over the room's median
# outside that stretch. Only that stretch is speech: a quick "testing" in an
# eight-second take left the whole take's 90th percentile in the room. The
# room is its median, not its quietest moments: a kitchen, a cafe or traffic
# goes quiet now and then between its clatter, and against those moments a
# voice stood well over a room that was loud next to it. The warning is about
# how well the speech model hears the voice (a headset or the microphone
# closer helps); hands-free no longer depends on it, as the voice detector
# tells the voice from such a room. Measured with 125 recorded readers in six
# recorded rooms (DEMAND: kitchen, living room, laundry, office, cafeteria,
# traffic) and five made ones, at three voice levels: with a second or more
# of talking, of 1,800 takes in the recorded rooms at
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
    Under NOISY_ROOM_DB the room is loud next to that voice, and a headset
    or the microphone closer to the mouth helps the speech model hear it."""
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
