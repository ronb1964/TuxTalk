"""Linear resampling of mono audio: a recording to the speech engine's rate
(the dictation pipeline and voice editing), and the mic test's replay to the
speakers' rate (beeps.play; a beep itself is made at the speakers' rate).
Good enough for speech, and it needs nothing beyond numpy.
"""
import numpy as np


def resample(audio, orig_sr, target_sr):
    """*audio* recorded at *orig_sr* as it would be at *target_sr*, in the
    same dtype; *audio* itself when the rates match.

    Its length is the exact one, rounded down, worked out in integers: as a
    float, len / orig_sr * target_sr can land just under a whole number
    (3,003 samples at 48 kHz to 16 kHz is 1000.999...), and the length would
    then hang on rounding error. Rounded down, never up, so the result never
    lasts longer than the audio it came from."""
    if orig_sr == target_sr:
        return audio
    target_len = len(audio) * target_sr // orig_sr
    positions = np.linspace(0, len(audio) - 1, target_len)
    return np.interp(positions, np.arange(len(audio)), audio).astype(audio.dtype)
