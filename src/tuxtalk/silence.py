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
background noise, which it learns as it goes (the quietest level heard, or
noise that has stayed steady for two seconds, a fan or a dryer). Nothing
counts until the speaker has actually talked for a moment, so a slow start
is never mistaken for being done, and a click or a bump of the room does
not count as talking.
"""
import collections

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
# Steady noise: the room is learned from frames of this long...
_FRAME_S = 0.05
# ...over the last _STEADY_S: when the 10th and 75th percentile of their
# levels lie within _STEADY_DB of each other, what is heard is steady noise,
# and the floor becomes its median, louder than speech's threshold or not.
_STEADY_S = 2.0
_STEADY_DB = 5.0
_STEADY_LOW, _STEADY_HIGH = 10, 75
# Steady noise that raises speech's threshold this many times over (6 dB)
# was misjudged until then, and the last _STEADY_S is judged again.
_MISJUDGED = 2.0
# Loud for less than this, with gaps under _BLIP_GAP_S, is a bump of the
# room, not speech: it pauses the quiet count rather than starting it over.
# Never more than a tenth of the wait: with a 0.5 s wait, 0.15 s took the
# start of a quiet word for a bump and stopped mid-sentence three times as
# often as before, in a quiet room too.
_BLIP_S = 0.15
_BLIP_SHARE = 0.1
_BLIP_GAP_S = 0.1


def block_rms(block, sample_rate: int):
    """(loudness as int16 RMS, duration in seconds) of one block of int16 samples."""
    samples = np.frombuffer(block, dtype=np.int16).astype(np.float64)
    if samples.size == 0:
        return 0.0, 0.0
    return float(np.sqrt(np.mean(samples ** 2))), samples.size / sample_rate


class NoiseFloor:
    """The room's background noise, learned as blocks arrive, and the loudness
    that counts as speech over it.

    Two rules learn it. The floor drops to the quietest 50 ms heard: that
    settles on a quiet room within a word or two, since speech has gaps
    between syllables where only the room is heard. And noise that is steady
    (the 10th and 75th percentile of its 50 ms levels over the last
    _STEADY_S within _STEADY_DB) sets the floor to its median, even noise
    louder than speech's threshold: a clothes dryer switched on, or one
    running from the start, which the first rule can never learn as none of
    it is quieter. Nothing else raises the floor, so it never chases a voice:
    speech is never that steady, its syllables and the gaps between them
    swinging by 10 dB or more. Measured on 26 recorded sentences and an 11 s
    speech, alone and in pink, fan, vent and rumble noise: no 2 s of speech
    came within 7.7 dB in a quiet room or with the noise 10 dB under the
    voice, and 4.8 dB with it 5 dB under, where the median taken was the
    noise's own level. Fan, vent, rumble, white and pink noise stayed within
    4.2 dB, a dryer's thumps included. One sound is as steady as noise: a
    note held for two seconds, sung, hummed or a long "mmm", is taken for
    the room, and so counts as a pause that long.
    """

    def __init__(self):
        self.level = None
        # Whether the latest update() found steady noise that raised the
        # threshold by _MISJUDGED or more: until now the room was misjudged,
        # and some of it counted as speech.
        self.raised = False
        self._frames = collections.deque()      # (level, seconds) over _STEADY_S
        self._span = 0.0                        # seconds in _frames
        self._energy = 0.0                      # the frame being gathered
        self._gathered = 0.0

    def threshold(self, level=None) -> float:
        """The loudness (int16 RMS) that counts as speech in this room, or
        in a room of *level*."""
        level = self.level if level is None else level
        return max(_MIN_SPEECH_RMS, (level or 0.0) * _SPEECH_OVER_NOISE)

    def quietest(self) -> float:
        """The quietest 50 ms of the last _STEADY_S: where the floor would
        have been, had it been learned when they were heard."""
        return min(level for level, _ in self._frames)

    def update(self, rms: float, duration: float) -> bool:
        """Learn from one block; True if the block is speech."""
        self.raised = False
        if rms < 1.0:
            # Digital silence (many streams open with a block of zeros) says
            # nothing about the room. Learned, it held the floor at 0, which
            # can never rise, so steady room noise counted as speech and
            # hands-free never stopped by itself.
            return False
        if self.level is None:
            # The first block may already be speech (talking the instant the
            # key goes down), so the first guess is capped at a quiet level.
            self.level = min(rms, _MIN_SPEECH_RMS)
        self._gather(rms, duration)
        return rms >= self.threshold()

    def _gather(self, rms: float, duration: float) -> None:
        """Add a block to the frame being gathered; a whole frame joins the
        last _STEADY_S of them, and if those are steady, sets the floor."""
        self._energy += rms * rms * duration
        self._gathered += duration
        if self._gathered < _FRAME_S:
            return
        frame = float(np.sqrt(self._energy / self._gathered))
        self._frames.append((frame, self._gathered))
        self._span += self._gathered
        self._energy = self._gathered = 0.0
        before = self.threshold()
        self.level = min(self.level, frame)
        while self._span - self._frames[0][1] >= _STEADY_S:
            self._span -= self._frames.popleft()[1]
        if self._span < _STEADY_S:
            return
        levels = [level for level, _ in self._frames]
        low, high = np.percentile(levels, (_STEADY_LOW, _STEADY_HIGH))
        if high <= low * 10 ** (_STEADY_DB / 20):
            self.level = float(np.median(levels))
            self.raised = self.threshold() >= before * _MISJUDGED


class _Runs:
    """Each block judged as speech, a bump of the room, or quiet. Loud
    blocks make a run, which a quiet gap of _BLIP_GAP_S ends; a run is
    speech once it has been loud for *blip_s*, all of it, so a short word
    still counts in full. Shorter runs are bumps."""

    def __init__(self, blip_s: float):
        self.blip_s = blip_s
        self.run = 0.0          # loud seconds in the latest run
        self.gap = 0.0          # quiet seconds since its last loud block
        self.counted = 0.0      # of run, seconds already counted as speech

    def judge(self, loud: bool, duration: float):
        """("speech", seconds of it it adds), ("bump", 0.0) or ("quiet", 0.0)."""
        if not loud:
            self.gap += duration
            if self.gap >= _BLIP_GAP_S:
                self.run = self.counted = 0.0
            return "quiet", 0.0
        self.run += duration
        self.gap = 0.0
        if self.run < self.blip_s:
            return "bump", 0.0
        added, self.counted = self.run - self.counted, self.run
        return "speech", added


class SilenceDetector:
    """Feed it audio blocks as they arrive; it says when the speaker is done.

    The quiet that counts towards silence_seconds may have bumps of the room
    in it: loud for under _BLIP_S, or a tenth of silence_seconds if that is
    shorter (a fan's gust, a dryer's thump, a click), pauses the count
    instead of starting it over. Words are loud for longer and start it
    over. A quick word said just before the wait runs out can therefore end
    the recording right after it; the word itself is kept, as only the
    quiet since the last loud block is trimmed (tail_to_trim).

    Measured with three of 26 recorded sentences joined by pauses of 0.6 of
    the setting, at three voice levels, in white, pink, rumble, fan, dryer
    and vent noise, 6 trials each. With a 2 s setting and the noise 10 dB
    or more under the voice, it stopped at most 0.1 s after the setting
    (1 s for a quiet voice in fan or dryer noise), and never while words
    were still coming; before, a dryer meant it never stopped from 20 dB,
    and a fan or a vent at 10 dB stopped it 1.5 to 3 s late. With a 1 s
    setting and the noise 10 dB under the voice, the voice's own soft
    stretches end it mid-sentence in 1 or 2 trials of 6, where it used to
    never stop in 2. With the noise 5 dB under the voice, quiet words drown
    in it, and it can stop while you talk, as it did before whenever it had
    learned the room (the microphone test warns before that). Steady noise
    louder than speech's threshold that starts while you talk is learned
    some 1.5 s after the last word, which a shorter setting then waits.
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
        self._blip_s = min(_BLIP_S, self.silence_seconds * _BLIP_SHARE)
        self._runs = _Runs(self._blip_s)
        self._tail = 0.0         # quiet seconds since the last loud block
        self._heard = collections.deque()   # (rms, seconds, speech added), last _STEADY_S
        self._heard_s = 0.0
        self._said_before = False

    def heard_speech(self) -> None:
        """Count speech said before the detector started (a word before the
        second tap of a double-tap): going quiet after it then stops the
        recording to type it, instead of dropping it as nothing said."""
        self._said_before = True

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
        return self._said_before or self.speech_time >= _SPEECH_TO_ARM_S

    def _threshold(self) -> float:
        """The loudness (int16 RMS) that counts as speech in this room."""
        return self._floor.threshold()

    def _remember(self, rms: float, duration: float, added: float) -> None:
        """Keep the last _STEADY_S of blocks, for _judge_again."""
        self._heard.append((rms, duration, added))
        self._heard_s += duration
        while self._heard_s - self._heard[0][1] >= _STEADY_S:
            self._heard_s -= self._heard.popleft()[1]

    def _judge_again(self) -> None:
        """Steady noise raised the floor a long way: the room was misjudged,
        and the noise counted as speech. The last _STEADY_S is judged again
        by the new threshold, so the quiet in it counts after all, and steady
        noise with nothing said still ends as nothing said."""
        runs, threshold = _Runs(self._blip_s), self._floor.threshold(self._floor.quietest())
        quiet = tail = spoken = said = 0.0
        for rms, seconds, added in self._heard:
            kind, now = runs.judge(rms >= threshold, seconds)
            said, spoken = said + added, spoken + now
            if kind == "speech":
                quiet = 0.0
            elif kind == "quiet":
                quiet += seconds
            tail = tail + seconds if kind == "quiet" else 0.0
        self._runs = runs
        self.speech_time = max(0.0, self.speech_time - said + spoken)
        self.silence_time = max(self.silence_time, quiet)
        self._tail = max(self._tail, tail)

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
            self._tail = self._tail + duration if rms < self._threshold() else 0.0
            return False

        loud = self._floor.update(rms, duration)
        if self._floor.raised:
            self._judge_again()
        kind, added = self._runs.judge(loud, duration)
        self.speech_time += added
        self._remember(rms, duration, added)
        if kind == "quiet":
            self._tail += duration
        else:
            self._tail = 0.0
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
# How far a test recording's loud blocks (its 90th percentile) stand over its
# quiet ones (its 10th), in dB. Measured on 26 recorded sentences with and
# without pauses around them, in white, pink, rumble, fan, dryer and vent
# noise: 22 dB and up in a quiet room, at least 17 dB with the noise 15 dB
# under the voice, 13 to 16 dB at 10 dB under it and 11 to 14 dB at 8 dB.
# Under 10 dB, quiet words start to be taken for pauses (hands-free stops
# during them at 5 dB), so the test warns from there, with a little margin.
NOISY_ROOM_DB = 16.0


def speech_over_room_db(audio_f32, sample_rate):
    """How much louder a recording's speech is than its room, in dB, or None
    for a recording under a second, too short to tell. Under NOISY_ROOM_DB
    the room is noisy for that voice."""
    rms = _blocks_rms(audio_f32, sample_rate)
    if len(rms) * _TAP_BLOCK_S < 1.0:
        return None
    room, speech = np.percentile(rms, (10, 90))
    return float(20 * np.log10(max(speech, 1.0) / max(room, 1.0)))
