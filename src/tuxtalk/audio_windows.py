"""The fixed windows that loudness is judged over, and how loud a recording
is against its room in them (RoomLoudness). The voice detector says what is
speech; loudness says where a soft word it missed may still be: hands-free
(silence.SilenceDetector) waits out a breath, Type at pause
(chunking.PauseDetector) keeps a word's soft end with its phrase, and the
microphone test finds a noisy room (silence.speech_over_room_db).

A microphone sends blocks of whatever size its driver picks, about 6 ms on
some onboard inputs. How steady a noise is, and how long a sound lasts, depend
on the length they are measured over, so the detectors gather the blocks into
windows of one length and judge those: the same with any block size.

It is a module of its own so that neither detector imports the other's.
"""
import collections

import numpy as np

# The window's length. Every loudness threshold and duration was measured
# with windows this long, so changing it means measuring them all again:
# the breath hands-free waits out, the soft word end Type at pause keeps,
# and the noisy room in the mic test.
WINDOW_S = 0.02


def window_length(sample_rate: int) -> int:
    """Samples in one WINDOW_S window at *sample_rate*, never fewer than one."""
    return max(1, int(WINDOW_S * sample_rate))


def split_windows(leftover, samples, size: int):
    """*leftover* followed by *samples*, cut into whole windows of *size*
    samples: (the windows, one to a row, and the samples left over, fewer
    than *size*, to go in front of the next block).

    Both are new arrays the caller owns, never views of *samples*: the audio
    system may reuse the memory a block came in. What is left over is a copy
    of its own, so keeping it doesn't keep the whole block's array."""
    joined = np.concatenate([leftover, samples])
    whole = joined.size - joined.size % size
    return joined[:whole].reshape(-1, size), joined[whole:].copy()


# A sound that could be a word: at least this loud (int16 RMS, about -58
# dBFS), measured on a Beyerdynamic M 70 PRO X into a MiniFuse 2 in a quiet
# room (the room at 1-6, speech at 50-600)...
MIN_SOUND_RMS = 40.0
# ...and at least this much louder than the room, in dB. Measured on cbox
# (onboard ALC887 at +50 dB gain): the room's hiss at 370-410, speech at
# 600-4000.
SOUND_OVER_ROOM_DB = 6.0
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


class RoomLoudness:
    """How loud a recording is against its room, WINDOW_S at a time: whether
    each window holds a sound that could be a word. The voice detector says
    what is speech; this says where trimming or stopping could still take a
    soft word it missed, and where the end of a word is being said.

    The audio is high-passed, and the room is a low percentile of the
    windows' levels over the last ROOM_WINDOW_S, digital silence left out,
    which follows the room within seconds."""

    def __init__(self, sample_rate: int):
        """Nothing heard yet, of audio at *sample_rate*."""
        self.sample_rate = int(sample_rate)
        self.level = 0.0
        self.loudest = 0.0
        self.room = 0.0
        self.sounding = False
        # Samples measured in whole windows since the first block.
        self.measured = 0
        self._over_room = 10 ** (SOUND_OVER_ROOM_DB / 20)
        # High-pass: the signal minus its moving average over 1/HIGH_PASS_HZ.
        self._hp_len = max(2, int(self.sample_rate / HIGH_PASS_HZ))
        self._hp_tail = np.zeros(self._hp_len - 1)
        self.window = window_length(self.sample_rate)
        self._pending = np.zeros(0)
        self._levels = collections.deque()   # (seconds measured, level) over ROOM_WINDOW_S

    def threshold(self) -> float:
        """The level (int16 RMS) from which a sound in this room right now
        could be a word."""
        return max(MIN_SOUND_RMS, self.room * self._over_room)

    def feed(self, block) -> list:
        """Measure the whole windows *block* (int16 samples) completes:
        whether each holds a sound that could be a word, in order."""
        samples = np.frombuffer(block, dtype=np.int16).astype(np.float64)
        if samples.size == 0:
            return []
        windows, self._pending = split_windows(self._pending, self._high_pass(samples),
                                               self.window)
        return [self._measure(float(np.sqrt(np.mean(w ** 2)))) for w in windows]

    def _high_pass(self, samples):
        buf = np.concatenate([self._hp_tail, samples])
        sums = np.cumsum(np.concatenate([[0.0], buf]))
        average = (sums[self._hp_len:] - sums[:-self._hp_len]) / self._hp_len
        self._hp_tail = buf[-(self._hp_len - 1):]
        return samples - average

    def _measure(self, level: float) -> bool:
        """Count one window of *level*: follow the room with it, and say
        whether it holds a sound that could be a word."""
        self.level = level
        self.loudest = max(self.loudest, level)
        self.measured += self.window
        clock = self.measured / self.sample_rate
        if level >= 1.0:   # digital silence is a dropout, not the room
            self._levels.append((clock, level))
        while self._levels and self._levels[0][0] < clock - ROOM_WINDOW_S:
            self._levels.popleft()
        if self._levels:
            levels = [v for _, v in self._levels]
            self.room = (min(levels) if clock < ROOM_START_S
                         else float(np.percentile(levels, ROOM_PERCENTILE)))
        self.sounding = level >= self.threshold()
        return self.sounding
