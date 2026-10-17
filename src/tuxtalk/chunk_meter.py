"""Type at pause: the live test in Preferences (TuxTalk Labs), minus GTK.

Preferences streams the microphone into a PauseTest and draws what it reports
on a PauseMeter (prefs.py). Kept apart from the window so it can be tested
with synthetic audio, without a display or a microphone.
"""
import math

from .chunking import MIN_SPEECH_RMS, MIN_SPEECH_S, PauseDetector, PauseSnapshot

# The meter's scale, in dBFS: a quiet dynamic mic (room near -80, speech from
# -55) and a hissing onboard input (room near -38) both read clearly.
METER_FLOOR_DB = -70.0


def level_to_fraction(rms: float) -> float:
    """An int16 RMS as a 0-1 position on the meter (dB scale, clamped)."""
    if rms <= 0:
        return 0.0
    db = 20 * math.log10(rms / 32768.0)
    return max(0.0, min(1.0, (db - METER_FLOOR_DB) / -METER_FLOOR_DB))


def threshold_fraction(room: float, speech_db: float) -> float:
    """Where speech starts on the meter for this room level and sensitivity,
    the same rule PauseDetector.threshold() applies."""
    return level_to_fraction(max(MIN_SPEECH_RMS, room * 10 ** (speech_db / 20)))


class PauseTest:
    """A PauseDetector fed by the live test, rebuilt whenever a setting changes.

    feed() runs on the audio thread and the rest on the GTK thread; each only
    swaps or reads whole references, so no lock is needed.
    """

    def __init__(self, sample_rate: int, pause_seconds: float, speech_db: float):
        self.sample_rate = int(sample_rate)
        self._earlier_cuts = 0
        self._cut_pending = False
        self._detector = PauseDetector(pause_seconds, self.sample_rate, speech_db=speech_db)

    def configure(self, pause_seconds: float, speech_db: float) -> None:
        """Apply new settings from now on, keeping the cut count."""
        old = self._detector
        self._earlier_cuts += old.cuts
        self._detector = PauseDetector(pause_seconds, self.sample_rate, speech_db=speech_db)

    def feed(self, block) -> bool:
        fired = self._detector.feed(block)
        if fired:
            self._cut_pending = True
        return fired

    def take_cut(self) -> bool:
        """True once after each cut, for the meter's flash."""
        pending, self._cut_pending = self._cut_pending, False
        return pending

    def snapshot(self) -> PauseSnapshot:
        snap = self._detector.snapshot()
        return snap._replace(cuts=self._earlier_cuts + snap.cuts)

    def status_text(self) -> str:
        """What the test hears, in plain words. The raw levels stay in the log
        line PauseDetector.describe() writes; they mean nothing to most people."""
        snap = self.snapshot()
        detector = self._detector
        if snap.level >= snap.threshold:
            now = "Hearing your voice"
        elif detector.speech_time >= MIN_SPEECH_S:
            now = (f"Quiet for {snap.silence_s:.1f} of the "
                   f"{detector.pause_seconds:.1f} seconds a pause needs")
        else:
            now = "Quiet. Say something, then pause"
        if not snap.cuts:
            return now
        pauses = "1 pause" if snap.cuts == 1 else f"{snap.cuts} pauses"
        return f"{now} · {pauses} heard"
