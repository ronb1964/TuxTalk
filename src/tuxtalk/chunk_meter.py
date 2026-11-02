"""Type at pause: the live test in Preferences (TuxTalk Labs), minus GTK.

Preferences streams the microphone into a PauseTest and draws what it reports
on a PauseMeter (prefs_type_at_pause.py). Kept apart from the window so it can be tested
with synthetic audio, without a display or a microphone.
"""
import math

from .chunking import MIN_SPEECH_S, PauseDetector, PauseSnapshot
from .logger import error_without_words, setup_logger

logger = setup_logger(__name__)

# The meter's scale, in dBFS: a quiet dynamic mic (room near -80, speech from
# -55) and a hissing onboard input (room near -38) both read clearly.
METER_FLOOR_DB = -70.0


def level_to_fraction(rms: float) -> float:
    """An int16 RMS as a 0-1 position on the meter (dB scale, clamped)."""
    if rms <= 0:
        return 0.0
    db = 20 * math.log10(rms / 32768.0)
    return max(0.0, min(1.0, (db - METER_FLOOR_DB) / -METER_FLOOR_DB))


class PauseTest:
    """A PauseDetector fed by the live test, rebuilt whenever a setting changes.

    feed() runs on the audio thread and the rest on the GTK thread; each
    writes only what the other just reads, so no lock is needed. The cuts are
    counted here, by feed(), not summed from the detectors: a detector being
    replaced could still cut once in the feed() that holds it.
    """

    def __init__(self, sample_rate: int, pause_seconds: float):
        self.sample_rate = int(sample_rate)
        self._cuts = 0
        self._cuts_flashed = 0
        self._heard_speech = False
        # Set by feed() when the detector failed; the test is over then.
        self.failed = False
        self._detector = PauseDetector(pause_seconds, self.sample_rate)

    def configure(self, pause_seconds: float) -> None:
        """Apply a new pause length from now on, keeping the cut count."""
        self._detector = PauseDetector(pause_seconds, self.sample_rate)

    def feed(self, block) -> bool:
        """Hear *block* (int16 samples); True when it ended a pause. A
        detector that fails sets failed, logged once, and hears nothing
        more: an error here would end the stream, the meter frozen."""
        if self.failed:
            return False
        try:
            fired = self._detector.feed(block)
        except Exception as e:
            self.failed = True
            logger.error(f"Type at pause's test stopped listening: {error_without_words(e)}")
            return False
        if fired:
            self._cuts += 1
        if fired or self._detector.speech_time >= MIN_SPEECH_S:
            self._heard_speech = True
        return fired

    def take_cut(self) -> bool:
        """True once after each cut, for the meter's flash. The audio thread
        only counts the cuts and this only notes how many it flashed: a flag
        both set and cleared could lose one in between."""
        cuts = self._cuts
        new, self._cuts_flashed = cuts != self._cuts_flashed, cuts
        return new

    def snapshot(self) -> PauseSnapshot:
        snap = self._detector.snapshot()
        return snap._replace(cuts=self._cuts)

    def status_text(self) -> str:
        """What the test hears right now, in plain words, while it runs. The
        raw levels stay in the log line PauseDetector.describe() writes; they
        mean nothing to most people."""
        snap = self.snapshot()
        detector = self._detector
        if snap.talking:
            now = "Hearing you talk"
        elif detector.speech_time >= MIN_SPEECH_S:
            now = (f"Quiet for {snap.silence_s:.1f} s. At {detector.pause_seconds:.1f} s "
                   "it types what you said")
        else:
            now = "Waiting for you to talk"
        if not snap.cuts:
            return now
        return f"{now} · {_pauses(snap.cuts)} so far"

    def result_text(self) -> str:
        """What the test found, for when it stops: the count, what it means,
        and what to change if it's wrong."""
        cuts = self.snapshot().cuts
        if cuts:
            phrases = "1 phrase" if cuts == 1 else f"{cuts} phrases"
            return (f"Test done: {_pauses(cuts)} found, so TuxTalk would have typed "
                    f"{phrases} while you talked. If one came while you were still "
                    "talking, make the pause longer.")
        if self._heard_speech:
            return ("Test done: no pauses found, so everything would have been typed "
                    "at the end. If you did pause, make the pause shorter.")
        return "Test stopped. It didn't hear you talk."


def _pauses(count: int) -> str:
    return "1 pause" if count == 1 else f"{count} pauses"
