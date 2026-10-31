"""Linear resampling of mono audio: a recording to the speech engine's rate,
and a beep to the speakers' own rate. Good enough for speech and for a
beep, and it needs nothing beyond numpy.
"""
import numpy as np


def resample(audio, orig_sr, target_sr):
    """*audio* recorded at *orig_sr* as it would be at *target_sr*, in the
    same dtype; *audio* itself when the rates match."""
    if orig_sr == target_sr:
        return audio
    target_len = int(len(audio) * target_sr / orig_sr)
    positions = np.linspace(0, len(audio) - 1, target_len)
    resampled = np.interp(positions, np.arange(len(audio)), audio.astype(np.float64))
    return resampled.astype(audio.dtype)
