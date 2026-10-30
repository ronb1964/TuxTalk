"""
Whether, and when, someone is talking, judged from the loudness of 20 ms
blocks of audio: hands-free auto-stop, an accidental tap of the hold key, a
start beep the microphone picked up, a dead microphone, and a recording in
which nobody talked.

Hands-free auto-stop is used only for toggle recordings (tap the toggle key,
talk, and it stops on its own). A hold recording already ends when the key
is released.

Auto-stop decides WHEN to stop and never removes audio from the start or
middle of a recording. That distinction matters: Whisper's built-in
voice-activity filter is off because trimming audio clips the first words
after a pause. The only audio ever dropped is the silent tail this detector
measured (see tail_to_trim), which is safe and also keeps Whisper from
inventing words like "thank you" in trailing silence.

How it listens: each audio block's loudness (RMS) is compared with the room's
background noise, which it learns as it goes (the quietest level heard,
rising slowly if the room gets noisier, or noise that has stayed steady for
three seconds, a fan or a dryer). Nothing counts until the speaker has
actually talked for a moment, so a slow start is never mistaken for being
done, and a single click or bump does not count as talking.
"""
import collections
import threading

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
# Steady noise: over the last _STEADY_S, the 10th and 75th percentile of
# the levels of 50 ms frames lie within _STEADY_DB of each other, and those
# of the blocks themselves within _STEADY_BLOCKS_DB. No 3 s of the 125
# readers' speech came within 3.3 dB by frames or 4.4 dB by blocks, alone
# or with pink noise 10 and 5 dB under the voice; a fan, a dryer, white
# noise and a gusty fan stayed within 3.3 dB either way, pink noise mostly
# not. Frames smooth out a voice that swings every few blocks (a held note
# with a tremolo); blocks alone let a gusty fan be learned so often that
# the next sentence's soft start was cut off twice as often. The margins
# are thin, set by one reader (0.3 dB by frames, 1 dB by blocks; the next
# closest came within 6.4 and 8.9 dB), so a voice held steadier than that,
# a sung note, is taken for the room.
_STEADY_S = 3.0
_FRAME_S = 0.05
_STEADY_DB = 3.0
_STEADY_BLOCKS_DB = 3.4
_STEADY_LOW, _STEADY_HIGH = 10, 75
# Speech then has to be this much louder (+3 dB) than the noise's loud end,
# the 98th percentile of its blocks in frames no louder than the 75th.
_NOISE_TOP = 98
_OVER_NOISE_TOP = 1.4
# Steady noise is learned only once something this much louder than it (22
# dB, the loudest 50 ms heard) shows the voice is well clear of it. Closer to
# the voice, its quiet words drown in the noise, and learning it ended the
# recording during them.
_CLEAR_OF_NOISE_DB = 22.0
# A block this much quieter than the steady noise (6 dB) means it stopped.
_NOISE_GONE = 0.5
# Steady noise that raises speech's threshold this many times over (6 dB)
# was misjudged until then, and the last _STEADY_S is judged again.
_MISJUDGED = 2.0
# In steady noise louder than _MIN_SPEECH_RMS, loud for less than this, with
# gaps under _BUMP_GAP_S, is a bump (a dryer's thump, a fan's gust): it
# pauses the quiet count rather than starting it over. Never more than a
# tenth of the wait: with a 0.5 s wait, 0.15 s took the start of a quiet word
# for a bump.
_BUMP_S = 0.15
_BUMP_SHARE = 0.1
_BUMP_GAP_S = 0.1
# The steady noise has bumps when its last _STEADY_S holds runs over
# speech's threshold in each of its thirds, every one shorter than _BUMP_S:
# a dryer thumps every second or so, a fan gusts more often. In a pause the
# middle third has none. The edges can: the last sentence's tail and the
# next one's first short syllables, which taken for bumps ended the
# recording in that sentence; and a word lasts longer.
_BUMP_THIRDS = 3


def block_rms(block, sample_rate: int):
    """(loudness as int16 RMS, duration in seconds) of one block of int16 samples."""
    samples = np.frombuffer(block, dtype=np.int16).astype(np.float64)
    if samples.size == 0:
        return 0.0, 0.0
    return float(np.sqrt(np.mean(samples ** 2))), samples.size / sample_rate


class NoiseFloor:
    """The room's background noise, learned as blocks arrive, and the loudness
    that counts as speech over it.

    The floor is the quietest level heard so far, allowed to rise slowly
    while it is quiet. Speech has short gaps between syllables where only the
    room is heard, so even when someone starts talking the instant recording
    begins, the floor settles on the room within a word or two.

    That rule can't learn noise louder than speech's threshold (a clothes
    dryer, a loud hiss): none of it is quiet enough to rise through. So noise
    that stays steady for _STEADY_S is learned whatever its level, and speech
    must then be louder than its loud end. Speech is never that steady: on
    125 recorded readers (Ron's sentences, LibriSpeech, FLEURS), alone and
    with pink noise 10 and 5 dB under the voice, no 3 s of speech came within
    3.3 dB by frames or 4.4 dB by blocks; white noise, a fan, a dryer and a
    gusty fan did in 80 to 100% of windows. It is learned only once a sound
    _CLEAR_OF_NOISE_DB louder was heard, the voice: steady noise heard alone
    keeps counting as speech, so hands-free with nothing said in a loud room
    goes on until the hotkey ends it, as it did before. A note held steady
    for three seconds after something much louder (sung, hummed, a long
    "mmm") is taken for the room, and counts as a pause that long.
    """

    def __init__(self):
        """A floor for one recording, with nothing heard yet."""
        self.level = None
        # The loud end of the steady noise learned, while it lasts (None in a
        # room learned by the quietest level), and that noise's median.
        self.top = None
        self.steady_level = None
        self._bumps = False
        # Whether the latest update() learned steady noise that changes what
        # counted as speech until now: it raised the threshold by _MISJUDGED
        # or more, or its bumps are to be taken for bumps from now on.
        self.changed = False
        self._frames = collections.deque()      # (level, seconds, its blocks)
        self._span = 0.0                        # seconds in _frames
        self._blocks = []                       # the frame being gathered
        self._energy = 0.0
        self._gathered = 0.0
        self._loudest = 0.0                     # the loudest frame heard

    def threshold(self) -> float:
        """The loudness (int16 RMS) that counts as speech in this room."""
        if self.top is not None:
            return max(_MIN_SPEECH_RMS, self.top * _OVER_NOISE_TOP)
        return max(_MIN_SPEECH_RMS, (self.level or 0.0) * _SPEECH_OVER_NOISE)

    def bumpy(self) -> bool:
        """Whether the steady noise learned has bumps loud enough to count as
        speech (a dryer's thumps, a fan's gusts). A quiet room's are not."""
        return self.top is not None and self._bumps

    def update(self, rms: float, duration: float) -> bool:
        """Learn from one block; True if the block is speech."""
        self.changed = False
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
        if self.top is not None and rms < self.steady_level * _NOISE_GONE:
            self.top = self.steady_level = None
        self._gather(rms, duration)
        return rms >= self.threshold()

    def _gather(self, rms: float, duration: float) -> None:
        """Add a block to the frame being gathered; a whole frame joins the
        last _STEADY_S of them, which may show steady noise to learn."""
        self._blocks.append(rms)
        self._energy += rms * rms * duration
        self._gathered += duration
        if self._gathered < _FRAME_S:
            return
        frame = float(np.sqrt(self._energy / self._gathered))
        self._frames.append((frame, self._gathered, self._blocks))
        self._span += self._gathered
        self._loudest = max(self._loudest, frame)
        self._blocks, self._energy, self._gathered = [], 0.0, 0.0
        while self._span - self._frames[0][1] >= _STEADY_S:
            self._span -= self._frames.popleft()[1]
        if self._span >= _STEADY_S:
            self._learn_steady()

    def _learn_steady(self) -> None:
        """If the last _STEADY_S is steady noise well under the voice, it is
        the room: the floor becomes its median, and speech must be louder
        than its loud end. Only frames no louder than its 75th percentile
        give that end, so a word or a thump among them doesn't."""
        levels = [level for level, _, _ in self._frames]
        low, usual = np.percentile(levels, (_STEADY_LOW, _STEADY_HIGH))
        blocks = [rms for _, _, frame_blocks in self._frames for rms in frame_blocks]
        block_low, block_high = np.percentile(blocks, (_STEADY_LOW, _STEADY_HIGH))
        median = float(np.median(levels))
        if (usual > low * 10 ** (_STEADY_DB / 20)
                or block_high > block_low * 10 ** (_STEADY_BLOCKS_DB / 20)
                or self._loudest < median * 10 ** (_CLEAR_OF_NOISE_DB / 20)):
            return
        before, was_bumpy = self.threshold(), self.bumpy()
        noise = [rms for level, _, frame_blocks in self._frames if level <= usual
                 for rms in frame_blocks]
        self.level = self.steady_level = median
        self.top = float(np.percentile(noise, _NOISE_TOP))
        self._bumps = _bumps_throughout(blocks, self.threshold(), self._span / len(blocks))
        self.changed = self.threshold() >= before * _MISJUDGED or self.bumpy() > was_bumpy


def _bumps_throughout(levels, threshold: float, seconds_each: float) -> bool:
    """Whether *levels* (blocks *seconds_each* long) have runs at or over
    *threshold* starting in each of their _BUMP_THIRDS parts, all shorter
    than _BUMP_S. A run cut off by either end of *levels* may be part of a
    word (the last sentence's final syllable), so it shows nothing."""
    starts, run, start = set(), 0, 0
    for i, level in enumerate([*levels, 0.0]):
        if level >= threshold:
            if not run:
                start = i
            run += 1
        elif run:
            if run * seconds_each >= _BUMP_S:
                return False
            if start > 0 and i < len(levels):
                starts.add(start * _BUMP_THIRDS // len(levels))
            run = 0
    return len(starts) == _BUMP_THIRDS


class _Runs:
    """Each block judged as speech, a bump of the room, or quiet. Loud
    blocks make a run, which a quiet gap of _BUMP_GAP_S ends; a run is
    speech once it has been loud for the bump length asked, all of it, so a
    short word still counts in full. Shorter runs are bumps."""

    def __init__(self):
        self.run = 0.0          # loud seconds in the latest run
        self.gap = 0.0          # quiet seconds since its last loud block
        self.counted = 0.0      # of run, seconds already counted as speech

    def judge(self, loud: bool, duration: float, bump_s: float):
        """("speech", seconds of it it adds), ("bump", 0.0) or ("quiet", 0.0),
        with runs shorter than *bump_s* taken for bumps."""
        if not loud:
            self.gap += duration
            if self.gap >= _BUMP_GAP_S:
                self.run = self.counted = 0.0
            return "quiet", 0.0
        self.run += duration
        self.gap = 0.0
        if self.run < bump_s:
            return "bump", 0.0
        added, self.counted = self.run - self.counted, self.run
        return "speech", added


class SilenceDetector:
    """Feed it audio blocks as they arrive; it says when the speaker is done.

    In steady noise learned with bumps in it (NoiseFloor), the quiet that
    counts towards silence_seconds may have bumps in it: loud for under
    _BUMP_S, or a tenth of silence_seconds if that is shorter, pauses the
    count instead of starting it over. Elsewhere every loud block starts it
    over, as the start of a quiet word after a pause can be that short.

    Measured with three of 125 recorded readers' clips joined by pauses of
    0.6 and of 0.92 of the setting, in white, pink, fan, gusty fan, dryer
    and babble noise from 40 to 10 dB under the voice, with waits of 1 to
    4 s (7,584 recordings; rv2/sim2.py with PYTHONHASHSEED=2, and another
    seed moves each count by a few): it stopped mid-sentence where it used
    to stop after the last word in 1, and the other way round in 57. Of
    the 2,452 that never stopped by themselves before (a dryer, a gusty
    fan, a loud voice over steady noise), 910 now stop, mostly 1 to 3 s
    late, and 66 cut off the next sentence's soft start: a dryer or a gusty
    fan 20 dB or less under the voice with pauses close to the wait, as a
    steady fan already did. 13 that stopped before now don't, most in a
    steady fan 20 dB under the voice with a 4 s wait: learned as the room,
    its wobble starts the wait over. A quiet room behaves as before, and so
    do six recorded real rooms (DEMAND: kitchen, living room, laundry,
    office, cafeteria, traffic) in all but a few of 2,800 recordings: they
    are almost never steady enough to learn.
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
        self._floor = NoiseFloor()
        self.speech_time = 0.0
        self.silence_time = 0.0
        self.elapsed = 0.0
        self.fired = False
        self.nothing_said = False
        self._busy = 0           # hold() calls not yet released
        self._bump_s = min(_BUMP_S, self.silence_seconds * _BUMP_SHARE)
        self._runs = _Runs()
        self._tail = 0.0         # quiet seconds since the last loud block
        self._heard = collections.deque()   # (rms, seconds, speech added), last _STEADY_S
        self._heard_s = 0.0
        self._said_before = False
        # feed() runs on the audio thread, hold() and release() on Type at
        # pause's phrase worker: release() clearing _heard while feed()
        # judged it again raised in the audio callback, which then stops
        # for good.
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
        there's the full silence_seconds to read it (and say "press enter").
        Noise learned later doesn't count the time before it, either."""
        with self._lock:
            self._busy = max(0, self._busy - 1)
            self.silence_time = 0.0
            self._heard.clear()
            self._heard_s = 0.0

    @property
    def armed(self) -> bool:
        """Whether the speaker has talked long enough for silence to count."""
        return self._said_before or self.speech_time >= _SPEECH_TO_ARM_S

    def _threshold(self) -> float:
        """The loudness (int16 RMS) that counts as speech in this room."""
        return self._floor.threshold()

    def _bump_length(self) -> float:
        """Loud for less than this is a bump of the room, not speech: only
        in noise that has bumps, as in a quiet room the start of a quiet word
        after a pause can be as short."""
        return self._bump_s if self._floor.bumpy() else 0.0

    def _remember(self, rms: float, duration: float, added: float) -> None:
        """Keep the last _STEADY_S of blocks, for _judge_again."""
        self._heard.append((rms, duration, added))
        self._heard_s += duration
        while self._heard_s - self._heard[0][1] >= _STEADY_S:
            self._heard_s -= self._heard.popleft()[1]

    def _judge_again(self) -> None:
        """Steady noise was learned that counted as speech until now (all of
        it, or its bumps). The last _STEADY_S is judged again as it would be
        now, so its quiet counts after all, and what it took for talking no
        longer arms the detector. What it trims is left alone: audio taken
        back as noise might be words after all."""
        threshold, bump_s = self._floor.threshold(), self._bump_length()
        runs, said, spoken, quiet = _Runs(), 0.0, 0.0, 0.0
        heard = collections.deque()
        for rms, seconds, added in self._heard:
            kind, now = runs.judge(rms >= threshold, seconds, bump_s)
            said, spoken = said + added, spoken + now
            if kind == "speech":
                quiet = 0.0
            elif kind == "quiet":
                quiet += seconds
            # As judged now, so judging again within _STEADY_S doesn't take
            # the same speech back twice.
            heard.append((rms, seconds, now))
        self._heard, self._runs = heard, runs
        self.speech_time = max(0.0, self.speech_time - said + spoken)
        self.silence_time = max(self.silence_time, quiet)

    def feed(self, block) -> bool:
        """Add one block of int16 samples. True exactly once: when the speaker
        has been silent for silence_seconds after having talked."""
        rms, duration = block_rms(block, self.sample_rate)
        if duration == 0:
            return False
        with self._lock:
            self.elapsed += duration

            if self.fired:
                # A few blocks still arrive before the recording actually stops.
                # Keep measuring them so tail_to_trim() covers the whole silent end.
                self._tail = self._tail + duration if rms < self._threshold() else 0.0
                return False

            loud = self._floor.update(rms, duration)
            if self._floor.changed:
                self._judge_again()
            kind, added = self._runs.judge(loud, duration, self._bump_length())
            self.speech_time += added
            self._remember(rms, duration, added)
            self._tail = 0.0 if loud else self._tail + duration
            if kind == "speech" or self._busy:
                self.silence_time = 0.0
                return False

            if not self.armed:
                if self.elapsed >= self.NOTHING_SAID_S:
                    self.fired = self.nothing_said = True
                    return True
                return False
            if kind == "bump":
                return False        # a bump of the room: the count waits
            self.silence_time += duration
            if self.silence_time >= self.silence_seconds:
                self.fired = True
                return True
            return False

    def tail_to_trim(self) -> int:
        """Samples of measured silence to drop from the end once it fired:
        the quiet since the last loud block, less _TAIL_KEEP_S."""
        if not self.fired:
            return 0
        return max(0, int((self._tail - _TAIL_KEEP_S) * self.sample_rate))


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


def _room(rms):
    """The loudness of a clip's room: that of its quietest blocks (the 20th
    percentile), which a quick word doesn't reach."""
    return np.percentile(rms, 20)


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
    clip does too. So does a steady room that loud (int16 RMS about 480 and
    up: a fan right by the microphone): by loudness alone it can't be told
    from such a word, and asking each block to stand a quarter above the
    room as well dropped 6% of 0.3 to 0.5 s stretches of real speech."""
    rms = _blocks_rms(audio_f32, sample_rate)
    if len(rms) < 3:
        return 0.0                  # under 60 ms: nothing to say in that
    loud = ((rms > max(_room(rms) * _TAP_SPEECH_OVER_NOISE, _TAP_MIN_SPEECH_RMS))
            | (rms > _TAP_VOICE_RMS))
    if ignoring_hz is not None and loud.any():
        left = _blocks_rms(_without_tone(audio_f32, sample_rate, ignoring_hz), sample_rate)
        loud &= _share_left(rms, left) >= _TONE_SHARE_LEFT
    return loud.sum() * _TAP_BLOCK_S


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
# left 7. With a second, 1 (as the quietest moments did).
_ROOM_TO_JUDGE_S = 1.0
# A shorter take can't hold both.
_SHORTEST_TAKE_S = _TALKING_TO_JUDGE_S + _ROOM_TO_JUDGE_S


def speech_over_room_db(audio_f32, sample_rate):
    """How much louder a recording's speech is than its room, in dB, or None
    without a second of clear talking and a second of the room around it.
    Under NOISY_ROOM_DB the room is noisy for that voice: in recorded rooms
    like it hands-free stops by itself about half the time or less, and in
    steady noise it cuts the speaker off mid-sentence more often."""
    rms = _blocks_rms(audio_f32, sample_rate)
    if len(rms) * _TAP_BLOCK_S < _SHORTEST_TAKE_S:
        return None
    loud = rms >= max(_MIN_SPEECH_RMS, np.percentile(rms, 10) * _TALKING_OVER_ROOM)
    run = max(1, round(_TALKING_RUN_S / _TAP_BLOCK_S))
    starts = np.flatnonzero(np.convolve(loud, np.ones(run, dtype=int), "valid") == run)
    if starts.size == 0:
        return None
    first, last = starts[0], starts[-1] + run
    outside = np.concatenate([rms[:first], rms[last:]])
    if ((last - first) * _TAP_BLOCK_S < _TALKING_TO_JUDGE_S
            or len(outside) * _TAP_BLOCK_S < _ROOM_TO_JUDGE_S):
        return None
    speech = np.percentile(rms[first:last], 90)
    return float(20 * np.log10(max(speech, 1.0) / max(np.median(outside), 1.0)))
