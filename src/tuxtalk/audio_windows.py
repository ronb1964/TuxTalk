"""The fixed windows that loudness is judged over: hands-free auto-stop
(silence.SilenceDetector), Type at pause (chunking.PauseDetector), and the
checks for a tap of the hold key and a noisy room (silence.py).

A microphone sends blocks of whatever size its driver picks, about 6 ms on
some onboard inputs. How steady a noise is, and how long a sound lasts, depend
on the length they are measured over, so the detectors gather the blocks into
windows of one length and judge those: the same with any block size.

It is a module of its own so that neither detector imports the other's.
"""
import numpy as np

# The window's length. Every threshold and duration the detectors and the
# tap and noisy-room checks use was measured with windows this long:
# changing it means measuring them again.
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
