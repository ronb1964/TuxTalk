"""Linear resampling of mono audio: a recording to the speech engine's rate
(the dictation pipeline and voice editing), and a beep or the mic test's
replay to the speakers' own rate (beeps.play). Good enough for speech and
for a beep, and it needs nothing beyond numpy.
"""
import numpy as np


def resample(audio, orig_sr, target_sr):
    """*audio* recorded at *orig_sr* as it would be at *target_sr*, in the
    same dtype; *audio* itself when the rates match.

    Its length is the exact one, rounded down, worked out in integers. As a
    float, len / orig_sr * target_sr can land just under a whole number
    (3,003 samples at 48 kHz gave 1,000 rather than 1,001), so the length
    hung on rounding error. Rounded down, never up: the result never lasts
    longer than the audio it came from."""
    if orig_sr == target_sr:
        return audio
    target_len = len(audio) * target_sr // orig_sr
    positions = np.linspace(0, len(audio) - 1, target_len)
    resampled = np.interp(positions, np.arange(len(audio)), audio.astype(np.float64))
    return resampled.astype(audio.dtype)
