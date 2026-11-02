"""Chunked dictation: type each phrase at the pause after it.

While a recording runs, PauseDetector listens for the speaker pausing. At each
pause the audio so far is handed to the dictation worker, which transcribes and types it
while the speaker goes on talking. The result is finished phrases appearing at
natural pauses, never words appearing and changing mid-sentence (upstream's
"live text", which it decided against).

Off unless chunked_dictation is set in config.toml; see type_at_pause.arm_chunker.
How the phrases' text is joined at each pause is chunk_text.py's job.

What is speech is voice_detector's verdict, not loudness: by loudness a
dryer's thumps were talking, and a phrase said next to one wasn't typed
until the recording ended. Loudness still decides what a phrase may lose
from its end (cut_from_end).
"""
import collections
from typing import NamedTuple

import numpy as np

from .audio_windows import RoomLoudness
from .voice_detector import FRAME_S, VoiceDetector

# Talking needed before a pause can end a chunk: a cough or a click is shorter.
MIN_SPEECH_S = 0.3
# Kept at the end of a chunk after the last speech, or after the last sound
# loud enough to be a word, so the last word's decay is never cut. The voice
# detector ends speech before a soft last sound has faded: keeping 0.3 s
# after it, 132 of 3,024 simulated recordings lost speech loudness kept.
TAIL_KEEP_S = 0.5
# The chunk after a cut starts this long before it: a word that ended the
# pause is speech to the voice detector only some 0.1 s in, and its soft
# first sound ("h", "f", "s") may be under that.
LEAD_KEEP_S = 0.3
# Speech for less than this (two of the voice detector's frames) doesn't start
# a pause over: one uncertain frame in a long quiet is no word yet.
MIN_BURST_S = 0.04


class PauseSnapshot(NamedTuple):
    """What a PauseDetector hears right now: the loudness (int16 RMS after the
    high-pass) and whether the voice detector hears speech."""
    level: float
    talking: bool
    silence_s: float
    cuts: int


class PauseDetector:
    """Feed it the recording's audio blocks; it says when a chunk ends.

    Unlike silence.SilenceDetector (which ends the recording once), this fires
    at every pause and then waits for new speech before it can fire again.

    The voice detector says what is speech, frame by frame. Loudness against
    the room (audio_windows.RoomLoudness) is measured beside it, to know what
    a phrase can lose from its end without losing a word (_mark_cut).

    Simulated with 125 recorded readers in nine noises from 40 to 10 dB under
    the voice and a 0.8 s pause, 3,024 recordings, against cutting by
    loudness: of the pauses between sentences loudness missed, as next to a
    dryer (458 of 1,008) or a gusty fan (503), it misses about a quarter;
    noise after the last phrase reaches the engine in 260 recordings, not
    1,476, and none in a quiet room; trimming loses 1.9 s of speech in all,
    not 38.6 s. Other people talking near the speaker is speech to it: there
    it misses more pauses (287 of 1,008, 215 by loudness).
    """

    def __init__(self, pause_seconds: float, sample_rate: int):
        """A detector for one recording at *sample_rate*: a pause of
        *pause_seconds* ends a chunk."""
        self.pause_seconds = float(pause_seconds)
        self.sample_rate = int(sample_rate)
        self._voice = VoiceDetector(self.sample_rate)
        self._loudness = RoomLoudness(self.sample_rate)
        self.talking = False
        self.cuts = 0
        # Talking toward the next cut; a cough before a pause is forgotten.
        self.speech_time = 0.0
        # Talking since the last cut, never forgotten: whether the rest of
        # the recording holds a word ("yes" is too short to cut a phrase).
        self.speech_since_cut = 0.0
        self.silence_time = 0.0
        self.last_pause = 0.0
        # Samples fed and the seconds of them the voice detector has judged,
        # since the first block; where the phrase before the last cut ends
        # and the one after it starts, in samples fed (_mark_cut).
        self._fed = 0
        self._judged_s = 0.0
        self._phrase_end = 0
        self._next_start = 0
        # Where the windows loud enough to be a word ended, over the last
        # pause and a second, in samples measured.
        self._sounds = collections.deque()
        self._burst = 0.0
        self.blocks = 0
        # The longest quiet stretch after speech, for the log: how close a
        # recording with no cut came to a pause.
        self.longest_quiet = 0.0

    def describe(self) -> str:
        """What it heard, for the log: the voice detector's speech and longest
        quiet, which decide a pause, and the loudness that only keeps a trim
        out of a soft word ending."""
        loudness = self._loudness
        return (f"speech {self.speech_time:.1f}s, longest quiet {self.longest_quiet:.2f}s of "
                f"the {self.pause_seconds:.2f}s a pause needs, {self.blocks} blocks at "
                f"{self.sample_rate} Hz; loudness for the trim: room {loudness.room:.0f}, "
                f"a sound from {loudness.threshold():.0f}, loudest {loudness.loudest:.0f} "
                f"(int16 RMS after the high-pass)")

    def snapshot(self) -> PauseSnapshot:
        """What it hears now, for the live test in Preferences."""
        return PauseSnapshot(self._loudness.level, self.talking, self.silence_time, self.cuts)

    def sounding(self) -> bool:
        """Whether a sound that could be a word was going on in the last
        loudness window: a stop now would cut it short."""
        return self._loudness.sounding

    def feed(self, block) -> bool:
        """Add one block of int16 samples. True when the speaker has paused for
        pause_seconds after talking for at least MIN_SPEECH_S."""
        size = np.frombuffer(block, dtype=np.int16).size
        if size == 0:
            return False
        self.blocks += 1
        self._fed += size
        self._note_sounds(block)
        fired = False
        for talking, _ in self._voice.feed(block):
            self._judged_s += FRAME_S
            self.talking = talking
            fired = self._frame(talking, FRAME_S) or fired
        return fired

    def _note_sounds(self, block) -> None:
        """Measure *block*'s loudness, noting where each window loud enough
        to be a word ends, over the last pause and a second."""
        loudness = self._loudness
        start = loudness.measured
        for n, sound in enumerate(loudness.feed(block)):
            if sound:
                self._sounds.append(start + (n + 1) * loudness.window)
        oldest = loudness.measured - int((self.pause_seconds + 1.0) * self.sample_rate)
        while self._sounds and self._sounds[0] < oldest:
            self._sounds.popleft()

    def _frame(self, talking: bool, duration: float) -> bool:
        """Count one frame the voice detector judged, *duration* seconds
        long: True when it ends a pause long enough to cut a chunk."""
        if talking:
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
        """Note where the phrases either side of the cut, at the frame just
        judged, end and start: the next one LEAD_KEEP_S before it, and
        everything after that, the rest of the block included, is its.

        The one before keeps TAIL_KEEP_S of its pause after its last speech,
        or after the last sound loudness heard in the pause before the next
        one's lead, the end of a soft last word the voice detector had taken
        for quiet; never past where the next one starts. So only what both
        took for quiet is dropped, between the two. A dryer's thumps in the
        pause are kept with the phrase they follow: by loudness alone the
        phrase never ended, and at a pause of 0.8 s or less nothing is
        dropped at all."""
        rate = self.sample_rate
        cut_at = round(self._judged_s * rate)
        speech_end = cut_at - round(self.silence_time * rate)
        self._next_start = cut_at - round(LEAD_KEEP_S * rate)
        soft_end = max((end for end in self._sounds if speech_end < end <= self._next_start),
                       default=speech_end)
        self._phrase_end = min(soft_end + round(TAIL_KEEP_S * rate), self._next_start)

    def cut_from_end(self) -> tuple[int, int]:
        """take_cut's *cut* for the last cut: (where the phrase ends, where
        the next phrase starts), each in samples counted back from the end
        of the audio fed so far."""
        return self._fed - self._phrase_end, self._fed - self._next_start


def take_cut(frames, cut):
    """(the phrase, the audio after it) from *frames*, a recording's int16
    mono blocks since the last cut, the newest last, at *cut*
    (PauseDetector.cut_from_end, read before another block is added).

    The phrase loses the end of its pause in whole blocks, as many as lie
    after where it ends. Trimmed to the sample, the cut took a soft last
    sound or a click that block-sized trimming had kept. But the next phrase
    starts where the detector says, split to the sample, so the start of a
    word that ended the pause, in the trimmed blocks or later in the block
    the cut was heard in, is whole there, never in neither phrase. Only the
    block the split falls in is copied, as this runs on the audio thread."""
    trim, resume = cut
    keep = max(resume, _whole_tail(frames, trim))
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
