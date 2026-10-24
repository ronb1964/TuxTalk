"""The VM check that both hands-free beeps are heard in full can tell a
whole beep from one cut short, on recordings made here with known tones.

The check (tests/vm/checks_dictation.py) records the VM's speakers and
measures each beep. A detector that reads every beep as whole passes a
TuxTalk that still cuts them short, the bug of issue #21.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent / "vm"))
import checks_dictation as check  # noqa: E402  (the VM checks' own folder, added above)

RATE = 16000
SLOW_LOG = "Double-tap: this recording will stop by itself\nBeeps: the output's latency is 0.174s\n"
rng = np.random.default_rng(3)


def speakers(*tones, hiss=30.0, seconds=1.5):
    """A recording of the speakers: hiss, and each (Hz, length, where) tone
    at a beep's loudness."""
    recording = rng.normal(0, hiss, int(RATE * seconds))
    for freq, length, at in tones:
        t = np.arange(int(RATE * length)) / RATE
        start = int(RATE * at)
        recording[start:start + len(t)] += np.sin(2 * np.pi * freq * t) * 32767 * 0.2
    return recording


def double_tap(start_cut=0.0, hands_free_cut=0.0, offset=0.0, swapped=False, hiss=30.0):
    """The start beep, then the hands-free beep 0.3 s later (or the other
    way round), each cut short by as much as asked."""
    first, second = (0.6, 0.3) if swapped else (0.3, 0.6)
    (start_hz, start_s), (hands_free_hz, hands_free_s) = check.START_BEEP, check.HANDS_FREE_BEEP
    return speakers((start_hz, start_s - start_cut, first + offset),
                    (hands_free_hz, hands_free_s - hands_free_cut, second + offset), hiss=hiss)


# Where a beep falls against the detector's steps changes what it reads.
OFFSETS = [i / 2000 for i in range(10)]


@pytest.mark.parametrize("offset", OFFSETS)
def test_both_beeps_heard_whole_pass(offset):
    assert check.beeps_outcome(SLOW_LOG, double_tap(offset=offset), RATE).status == "pass"


@pytest.mark.parametrize("cut", [0.01, 0.02, 0.04])
@pytest.mark.parametrize("beep", ["start", "hands-free"])
def test_a_beep_cut_short_fails(beep, cut):
    cuts = {"start_cut": cut} if beep == "start" else {"hands_free_cut": cut}
    for offset in OFFSETS:
        outcome = check.beeps_outcome(SLOW_LOG, double_tap(offset=offset, **cuts), RATE)
        assert outcome.status == "fail", (offset, outcome.detail)


def test_beeps_in_the_wrong_order_fail():
    outcome = check.beeps_outcome(SLOW_LOG, double_tap(swapped=True), RATE)
    assert outcome.status == "fail" and "came first" in outcome.detail


def test_beeps_over_loud_hiss_still_pass():
    outcome = check.beeps_outcome(SLOW_LOG, double_tap(hiss=300.0), RATE)   # -40 dBFS
    assert outcome.status == "pass", outcome.detail


def test_a_missing_beep_fails():
    start = (check.START_BEEP[0], check.START_BEEP[1], 0.3)
    outcome = check.beeps_outcome(SLOW_LOG, speakers(start), RATE)
    assert outcome.status == "fail" and "hands-free beep 0 of 70 ms" in outcome.detail


def test_an_output_that_wasnt_slowed_is_skipped():
    log = SLOW_LOG.replace("0.174s", "0.035s")
    assert check.beeps_outcome(log, double_tap(), RATE).status == "skip"


def test_no_double_tap_in_the_log_fails():
    log = "Beeps: the output's latency is 0.174s\n"
    assert check.beeps_outcome(log, double_tap(), RATE).status == "fail"
