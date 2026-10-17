"""Type at pause: the live test's logic in Preferences, without GTK or a mic."""
import numpy as np
import pytest

SR = 16000
BLOCK = 320
rng = np.random.default_rng(2)


def room(seconds, sr=SR):
    return rng.normal(0, 380, int(seconds * sr)).astype(np.int16)


def speech(seconds, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    tone = np.sin(2 * np.pi * 220 * t) * 2000 * ((t % 0.25) < 0.18)
    return np.clip(tone + rng.normal(0, 380, t.size), -32768, 32767).astype(np.int16)


def two_pauses(sr=SR):
    return np.concatenate([room(1.0, sr), speech(2.0, sr), room(1.5, sr),
                           speech(2.0, sr), room(1.5, sr)])


def feed_all(test, audio, block=BLOCK):
    return sum(test.feed(audio[i:i + block].tobytes()) for i in range(0, len(audio), block))


@pytest.mark.parametrize("rms,fraction", [
    (0.0, 0.0),
    (32768.0, 1.0),
    (32768.0 * 10 ** (-35 / 20), 0.5),     # -35 dBFS: the middle of -70..0
    (1.0, 0.0),                             # below -70 dBFS: clamped
    (40000.0, 1.0),                         # above full scale: clamped
])
def test_level_to_fraction(rms, fraction):
    from tuxtalk.chunk_meter import level_to_fraction
    assert level_to_fraction(rms) == pytest.approx(fraction, abs=0.01)


def test_pause_test_counts_the_cuts():
    from tuxtalk.chunk_meter import PauseTest
    test = PauseTest(SR, 0.8, 6.0)
    assert feed_all(test, two_pauses()) == 2
    assert test.snapshot().cuts == 2


def test_pause_test_at_48k():
    """Review focus 3: the test runs at the mic's native rate."""
    from tuxtalk.chunk_meter import PauseTest
    test = PauseTest(48000, 0.8, 6.0)
    assert feed_all(test, two_pauses(48000), block=960) == 2


def test_configure_mid_test_keeps_the_count_and_applies():
    """Review focus 1: moving a slider rebuilds the detector at once; the count
    carries on, and the new value is what decides from then on."""
    from tuxtalk.chunk_meter import PauseTest
    test = PauseTest(SR, 0.8, 6.0)
    audio = two_pauses()
    half = int(4.5 * SR)                     # just after the first pause
    feed_all(test, audio[:half])
    assert test.snapshot().cuts == 1
    test.configure(3.0, 6.0)                 # pauses now need 3 s: the second one is too short
    feed_all(test, audio[half:])
    assert test.snapshot().cuts == 1


def test_take_cut_reports_each_cut_once():
    from tuxtalk.chunk_meter import PauseTest
    test = PauseTest(SR, 0.8, 6.0)
    audio = two_pauses()
    feed_all(test, audio[:int(4.5 * SR)])
    assert test.take_cut() is True
    assert test.take_cut() is False


def test_status_text():
    from tuxtalk.chunk_meter import PauseTest
    test = PauseTest(SR, 0.8, 6.0)
    feed_all(test, two_pauses())
    text = test.status_text()
    assert text.endswith(" · 2 pauses heard")
    assert "Room" not in text and "dB" not in text      # no raw levels


def test_status_text_in_plain_words():
    from tuxtalk.chunk_meter import PauseTest
    test = PauseTest(SR, 0.8, 6.0)
    feed_all(test, room(1.5))
    assert test.status_text() == "Quiet. Say something, then pause"
    feed_all(test, speech(0.9))                 # ends mid-syllable
    assert test.status_text() == "Hearing your voice"
    feed_all(test, room(0.4))
    assert test.status_text().startswith("Quiet for 0.")
    assert "of the 0.8 seconds a pause needs" in test.status_text()
    feed_all(test, room(0.6))
    assert test.status_text().endswith(" · 1 pause heard")


def test_threshold_fraction_follows_the_sensitivity():
    from tuxtalk.chunk_meter import level_to_fraction, threshold_fraction
    assert threshold_fraction(400.0, 6.0) == pytest.approx(level_to_fraction(400 * 10 ** (6 / 20)))
    assert threshold_fraction(400.0, 12.0) > threshold_fraction(400.0, 6.0)
    assert threshold_fraction(1.0, 6.0) == pytest.approx(level_to_fraction(40.0))   # the floor
