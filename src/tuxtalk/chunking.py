"""Chunked dictation: type each phrase at the pause after it.

While a recording runs, PauseDetector listens for the speaker pausing. At each
pause the audio so far is handed to the dictation worker, which transcribes and types it
while the speaker goes on talking. The result is finished phrases appearing at
natural pauses, never words appearing and changing mid-sentence (upstream's
"live text", which it decided against).

Off unless chunked_dictation is set in config.toml; see type_at_pause.arm_chunker.
How the phrases' text is joined at each pause is chunk_text.py's job.
"""
import collections
from typing import NamedTuple

import numpy as np

from .audio_windows import split_windows, window_length

# Talking needed before a pause can end a chunk: a cough or a click is shorter.
MIN_SPEECH_S = 0.3
# Silence kept at the end of a chunk, so the last word's decay is never cut.
TAIL_KEEP_S = 0.3
# Silence kept at the start of the chunk after a cut, before the sound that
# ended the pause: a word's soft first sound ("h", "f", "s") can be under
# speech's threshold there (take_cut).
LEAD_KEEP_S = 0.3
# The quietest level (int16 RMS, about -58 dBFS) that can count as speech.
# Measured on a Beyerdynamic M 70 PRO X into a MiniFuse 2 in a quiet room: the
# room at 1-6, speech at 50-600. Auto-stop's 300 never heard that as speech.
MIN_SPEECH_RMS = 40.0
# Speech must be at least this much louder than the room, in dB (Preferences →
# Advanced → TuxTalk Labs → Type at pause → Noise filter). Measured on cbox
# (onboard ALC887 at +50 dB gain): the room's hiss at 370-410, speech at
# 600-4000, so auto-stop's +8 dB would take the quieter words for pauses.
DEFAULT_SPEECH_DB = 6.0
# Rumble below this is filtered out before measuring: it is loud on an onboard
# mic input and carries no speech.
HIGH_PASS_HZ = 150
# The room level is a low percentile of the levels over this long, so a room
# that gets noisier is followed within seconds. For the first ROOM_START_S it
# is the quietest level yet: a recording often starts mid-word, and then every
# level heard so far is speech.
ROOM_WINDOW_S = 5.0
ROOM_PERCENTILE = 10
ROOM_START_S = 1.0
# Loud for less than this is a click or a burst of hiss, not speech; it does not
# start a pause over. A consonant lasts longer. Longer limits that let a
# dryer's thumps through (0.07 s) cut phrases mid-sentence 10 to 19% more
# often in a quiet room, measured on 125 recorded readers.
MIN_BURST_S = 0.04


class PauseSnapshot(NamedTuple):
    """What a PauseDetector hears right now (int16 RMS after the high-pass)."""
    level: float
    room: float
    threshold: float
    silence_s: float
    cuts: int


class PauseDetector:
    """Feed it the recording's audio blocks; it says when a chunk ends.

    Unlike silence.SilenceDetector (which ends the recording once), this fires
    at every pause and then waits for new speech before it can fire again.

    The room level is measured differently too. SilenceDetector's floor is
    the quietest window heard, rising only slowly, so a room that gets
    noisier while you talk is learned late. Here the audio is high-passed,
    and the room is a low percentile of its windows' levels (each
    audio_windows.WINDOW_S long) over the last ROOM_WINDOW_S, digital silence
    left out, which follows the room within seconds.
    """

    def __init__(self, pause_seconds: float, sample_rate: int,
                 speech_db: float = DEFAULT_SPEECH_DB):
        """A detector for one recording at *sample_rate*: a pause of
        *pause_seconds* ends a chunk, and speech is *speech_db* over the room."""
        self.pause_seconds = float(pause_seconds)
        self.sample_rate = int(sample_rate)
        self.speech_over_room = 10 ** (float(speech_db) / 20)
        self.cuts = 0
        self.level = 0.0
        # Talking toward the next cut; a cough before a pause is forgotten.
        self.speech_time = 0.0
        # Talking since the last cut, never forgotten: whether the rest of
        # the recording holds a word ("yes" is too short to cut a phrase).
        self.speech_since_cut = 0.0
        self.silence_time = 0.0
        self.last_pause = 0.0
        # Samples fed, and those judged in whole windows, since the first
        # block; where the phrase after the last cut starts, counted the
        # same way (_mark_cut).
        self._fed = 0
        self._judged = 0
        self._resume_at = 0
        # High-pass: the signal minus its moving average over 1/HIGH_PASS_HZ.
        self._hp_len = max(2, int(self.sample_rate / HIGH_PASS_HZ))
        self._hp_tail = np.zeros(self._hp_len - 1)
        self._win_len = window_length(self.sample_rate)
        self._pending = np.zeros(0)
        self._levels = collections.deque()   # (clock, level) over ROOM_WINDOW_S
        self._room = 0.0
        self._clock = 0.0
        self._burst = 0.0
        self.loudest = 0.0
        self.blocks = 0
        # The longest quiet stretch after speech, for the log: how close a
        # recording with no cut came to a pause.
        self.longest_quiet = 0.0

    def describe(self) -> str:
        """The levels it is working with, for the log."""
        return (f"room {self._room:.0f}, speech from {self.threshold():.0f}, "
                f"loudest {self.loudest:.0f} (int16 RMS after the high-pass), "
                f"{self.blocks} blocks at {self.sample_rate} Hz, longest quiet "
                f"{self.longest_quiet:.2f}s of the {self.pause_seconds:.2f}s a pause needs")

    def snapshot(self) -> PauseSnapshot:
        """The levels it is working with now, for the live test in Preferences."""
        return PauseSnapshot(self.level, self._room, self.threshold(), self.silence_time,
                             self.cuts)

    def _high_pass(self, block):
        samples = np.frombuffer(block, dtype=np.int16).astype(np.float64)
        if samples.size == 0:
            return samples
        buf = np.concatenate([self._hp_tail, samples])
        sums = np.cumsum(np.concatenate([[0.0], buf]))
        average = (sums[self._hp_len:] - sums[:-self._hp_len]) / self._hp_len
        self._hp_tail = buf[-(self._hp_len - 1):]
        return samples - average

    def threshold(self) -> float:
        """The level (int16 RMS) that counts as speech in this room right now."""
        return max(MIN_SPEECH_RMS, self._room * self.speech_over_room)

    def _learn_room(self, level: float, duration: float) -> None:
        self._clock += duration
        if level >= 1.0:   # digital silence is a dropout, not the room
            self._levels.append((self._clock, level))
        while self._levels and self._levels[0][0] < self._clock - ROOM_WINDOW_S:
            self._levels.popleft()
        if self._levels:
            levels = [v for _, v in self._levels]
            self._room = (min(levels) if self._clock < ROOM_START_S
                          else float(np.percentile(levels, ROOM_PERCENTILE)))

    def feed(self, block) -> bool:
        """Add one block of int16 samples. True when the speaker has paused for
        pause_seconds after talking for at least MIN_SPEECH_S."""
        hp = self._high_pass(block)
        if hp.size == 0:
            return False
        self.blocks += 1
        self._fed += hp.size
        windows, self._pending = split_windows(self._pending, hp, self._win_len)
        fired = False
        for window in windows:
            self._judged += self._win_len
            level = float(np.sqrt(np.mean(window ** 2)))
            fired = self._window(level, self._win_len / self.sample_rate) or fired
        return fired

    def _window(self, level: float, duration: float) -> bool:
        """Count one window of *level*, *duration* seconds long: True when
        it ends a pause long enough to cut a chunk."""
        self.level = level
        self._learn_room(level, duration)
        self.loudest = max(self.loudest, level)
        if level >= self.threshold():
            self._burst += duration
            if self._burst >= MIN_BURST_S:
                self.speech_time += duration
                self.speech_since_cut += duration
                self.silence_time = 0.0
                return False
        else:
            self._burst = 0.0
        self.silence_time += duration
        if self.speech_time >= MIN_SPEECH_S:
            self.longest_quiet = max(self.longest_quiet, self.silence_time)
        if self.silence_time < self.pause_seconds:
            return False
        if self.speech_time < MIN_SPEECH_S:
            # Too little talking for a chunk (a cough in a long silence).
            # Forget it for the next cut, so two coughs a minute apart don't
            # add up to speech; speech_since_cut keeps it, as it may be a word.
            self.speech_time = 0.0
            return False
        self._mark_cut()
        self.last_pause = self.silence_time
        self.speech_time = self.speech_since_cut = 0.0
        self.silence_time = 0.0
        self.cuts += 1
        return True

    def _mark_cut(self) -> None:
        """Note where the phrase after the cut starts, at the window just
        judged: LEAD_KEEP_S before the sound that ended the pause, a loud
        run too short to count as speech yet (_burst), which is the next
        word starting; never inside the TAIL_KEEP_S the phrase before keeps
        of its pause. Everything after it, the rest of the block included,
        is the next phrase's, as the windows judged there count toward it.

        With no sound at the cut there is no word to start early, so
        nothing is noted and take_cut splits where the phrase before ends:
        a lead would take the soft end of its last word away from it, lost
        with the rest when nothing more is said."""
        if self._burst == 0:
            self._resume_at = None
            return
        rate = self.sample_rate
        onset = self._judged - round(self._burst * rate)
        speech_end = self._judged - round(self.silence_time * rate)
        self._resume_at = min(onset, max(onset - int(LEAD_KEEP_S * rate),
                                         speech_end + int(TAIL_KEEP_S * rate)))

    def cut_from_end(self) -> tuple[int, int | None]:
        """take_cut's *cut* for the last cut: (the pause's samples the phrase
        may lose from its end, where the next phrase starts or None when it
        starts where the phrase ends), each counted back from the end of
        the audio fed so far."""
        trim = max(0, int((self.last_pause - TAIL_KEEP_S) * self.sample_rate))
        if self._resume_at is None:
            return trim, None
        return trim, self._fed - self._resume_at


def take_cut(frames, cut):
    """(the phrase, the audio after it) from *frames*, a recording's int16
    mono blocks since the last cut, the newest last, at *cut*
    (PauseDetector.cut_from_end, read before another block is added).

    The phrase loses the end of its pause in whole blocks, as many as the
    pause holds after TAIL_KEEP_S. Trimmed to the sample, the cut took a
    soft last sound or a click that block-sized trimming had kept. But the
    next phrase starts where the detector says, split to the sample, so
    the start of a word that ended the pause, in the trimmed blocks or
    later in the block the cut was heard in, is whole there, never in
    neither phrase; with no such word it starts where the phrase ends.
    Only the block the split falls in is copied, as this runs on the
    audio thread."""
    trim, resume = cut
    whole = _whole_tail(frames, trim)
    if resume is None:
        resume = whole
    keep = max(resume, whole)
    phrase = _split_off(frames, keep)[0]
    return phrase, _split_off(frames, resume)[1]


def _split_off(frames, samples):
    """(the blocks before the last *samples* samples of *frames*, those
    samples as blocks): the block the split falls in is cut in two."""
    before, after = list(frames), []
    while before and samples > 0:
        block = before.pop()
        size = len(block) // 2                  # int16 mono
        if size > samples:
            split = (size - samples) * 2
            before.append(block[:split])
            after.append(block[split:])
            break
        after.append(block)
        samples -= size
    after.reverse()
    return before, after


def _whole_tail(frames, to_trim) -> int:
    """Samples in the trailing blocks (int16 mono) of *frames* that fit
    whole in *to_trim*."""
    whole = 0
    for block in reversed(frames):
        size = len(block) // 2
        if whole + size > to_trim:
            break
        whole += size
    return whole


def drop_tail_samples(frames, to_trim):
    """Remove whole trailing blocks (int16 mono) holding at most *to_trim*
    samples: the silence hands-free's auto-stop measured at the end
    (recording.trim_auto_stop_tail). A block is never split, so nothing
    past what was measured is removed."""
    whole = _whole_tail(frames, to_trim)
    while whole > 0:
        whole -= len(frames.pop()) // 2
