"""Chunked dictation: type each phrase at the pause after it.

While a recording runs, PauseDetector listens for the speaker pausing. At each
pause the audio so far is handed to the dictation worker, which transcribes and types it
while the speaker goes on talking. The result is finished phrases appearing at
natural pauses, never words appearing and changing mid-sentence (upstream's
"live text", which it decided against).

Off unless chunked_dictation is set in config.toml; see type_at_pause.arm_chunker.
"""
import collections
import difflib
import re
from typing import NamedTuple

import numpy as np

from .logger import setup_logger

logger = setup_logger(__name__)

# Talking needed before a pause can end a chunk: a cough or a click is shorter.
MIN_SPEECH_S = 0.3
# Silence kept at the end of a chunk, so the last word's decay is never cut.
TAIL_KEEP_S = 0.3
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
# Levels are measured over windows this long, whatever size of block the audio
# callback delivers: on cbox it delivered ~6 ms blocks, too short to measure.
WINDOW_S = 0.02
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
    the quietest block heard, rising only slowly, so a room that gets noisier
    while you talk is learned late. Here the audio is high-passed, measured
    over fixed WINDOW_S windows, and the room is a low percentile of those
    levels over the last ROOM_WINDOW_S, digital silence left out, which
    follows the room within seconds.
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
        self.speech_time = 0.0
        self.silence_time = 0.0
        self.last_pause = 0.0
        self._tail_s = 0.0
        # High-pass: the signal minus its moving average over 1/HIGH_PASS_HZ.
        self._hp_len = max(2, int(self.sample_rate / HIGH_PASS_HZ))
        self._hp_tail = np.zeros(self._hp_len - 1)
        self._win_len = max(1, int(WINDOW_S * self.sample_rate))
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
        self._pending = np.concatenate([self._pending, hp])
        fired = False
        n = self._win_len
        while self._pending.size >= n:
            window, self._pending = self._pending[:n], self._pending[n:]
            level = float(np.sqrt(np.mean(window ** 2)))
            fired = self._window(level, n / self.sample_rate) or fired
        return fired

    def _window(self, level: float, duration: float) -> bool:
        """Count one WINDOW_S window of *level*: True when it ends a pause
        long enough to cut a chunk."""
        self.level = level
        self._learn_room(level, duration)
        self.loudest = max(self.loudest, level)
        if level >= self.threshold():
            self._burst += duration
            if self._burst >= MIN_BURST_S:
                self.speech_time += duration
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
            # Forget it, so two coughs a minute apart don't add up to speech.
            self.speech_time = 0.0
            return False
        self.last_pause = self._tail_s = self.silence_time
        self.speech_time = 0.0
        self.silence_time = 0.0
        self.cuts += 1
        return True

    def tail_samples(self) -> int:
        """Samples of measured silence to drop from the chunk just cut."""
        return max(0, int((self._tail_s - TAIL_KEEP_S) * self.sample_rate))


# --- where a sentence ends at a pause ------------------------------------------
# The engine punctuates each chunk as if it were a whole sentence, so nearly
# every pause would end in a period. The chunk's final mark is held back
# instead, and once the next chunk arrives the engine hears both together:
# with the words (and the voice) on both sides of the pause, the mark it puts
# at the seam is a real verdict. Only that mark is taken from the joint text;
# the words typed come from each chunk's own transcription, except for a
# phrase too short to be heard alone (words_in_context).

_WORD = re.compile(r"\w+(?:['’-]\w+)*")
_MARKS = ".?!…,;:"


def _seam(prev_raw: str, joint_raw: str):
    """(the words of *joint_raw*, the index of *prev_raw*'s last word among
    them), or None when the words can't be lined up or nothing follows them."""
    prev = [w.casefold() for w in _WORD.findall(prev_raw)]
    joint = list(_WORD.finditer(joint_raw))
    if not prev or not joint:
        return None
    words = [m.group().casefold() for m in joint]
    last = len(prev) - 1
    matcher = difflib.SequenceMatcher(None, prev, words, autojunk=False)
    for a, b, size in matcher.get_matching_blocks():
        if size and a <= last < a + size:
            j = b + (last - a)
            break
    else:
        return None
    if j == len(joint) - 1:
        return None
    return joint, j


def boundary_mark(prev_raw: str, joint_raw: str):
    """The punctuation the engine put after the last word of *prev_raw* when it
    transcribed that chunk together with the next one (*joint_raw*): ".", "?",
    ",", ... or "" for none. None when the words can't be lined up, or when
    nothing follows them (so there is no context to judge by)."""
    seam = _seam(prev_raw, joint_raw)
    if seam is None:
        return None
    joint, j = seam
    gap = joint_raw[joint[j].end():joint[j + 1].start()]
    marks = [c for c in gap if c in _MARKS]
    return marks[0] if marks else ""


def words_in_context(prev_raw: str, joint_raw: str):
    """What *joint_raw* says after the words of *prev_raw*: the next chunk as
    the engine heard it with the one before. None when they can't be lined up.

    A very short chunk alone is barely a word or two, and Parakeet sometimes
    invents a sentence for it: "it" came out as "It's a little bit
    more than a little bit." (Ron, 2026-10-18), while the joint pass heard
    "... run out it"."""
    seam = _seam(prev_raw, joint_raw)
    if seam is None:
        return None
    joint, j = seam
    return joint_raw[joint[j + 1].start():].strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text))


def _last_word(text: str) -> str:
    words = re.findall(r"\w+", text.casefold())
    return words[-1] if words else ""


def hold_back_boundary(text: str, raw: str):
    """Split a middle chunk's text into what to type now and the final mark to
    hold back: (text, mark), or (text, None) when nothing is held back.

    The engine heard the chunk alone, so its final period or question mark is
    a guess; the next chunk decides it (type_at_pause.resolve_boundary). The trailing
    space waits too, so whatever comes next lands right after the last word."""
    core = text.rstrip(" ")
    if not core:
        return text, None
    m = re.search(r"[.?!…]+$", core)
    mark = m.group() if m else ""
    if mark:
        core = core[:m.start()].rstrip()
        # A mark the speaker dictated ("Punkt") is theirs, not the engine's
        # guess: the word before it then isn't the raw text's last word.
        if not core or _last_word(core) != _last_word(raw):
            return text, None
    elif not core[-1].isalnum():
        return text, None   # ends in a line break, a quote, a bracket...
    return core, mark


def carry_question(text: str) -> str:
    """End a phrase that finishes an open question with "?" instead of the
    period. Only a phrase holding one sentence: with a second one in it, the
    question has already ended somewhere the engine couldn't mark."""
    m = re.fullmatch(r"([^.?!]*\w)\.(\s*)", text)
    if not m:
        return text
    logger.info("The question goes on across the pause: it ends with ?")
    return m.group(1) + "?" + m.group(2)


def drop_tail_samples(frames, to_trim):
    """Remove whole trailing blocks (int16 mono) holding at most *to_trim*
    samples. A block is never split, so speech is never touched."""
    while frames and to_trim > 0:
        block_samples = len(frames[-1]) // 2          # int16 mono
        if block_samples > to_trim:
            break
        frames.pop()
        to_trim -= block_samples
