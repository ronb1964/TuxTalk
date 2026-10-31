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
    """The test runs at the mic's native rate, not only at 16 kHz."""
    from tuxtalk.chunk_meter import PauseTest
    test = PauseTest(48000, 0.8, 6.0)
    assert feed_all(test, two_pauses(48000), block=960) == 2


def test_configure_mid_test_keeps_the_count_and_applies():
    """Moving a slider rebuilds the detector at once; the count
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


def test_a_cut_while_a_slider_moves_is_still_counted():
    """The audio thread is already feeding the old detector when a slider
    rebuilds it; the cut that feed makes counts like any other."""
    from tuxtalk.chunk_meter import PauseTest
    audio = two_pauses()
    blocks = [audio[i:i + BLOCK].tobytes() for i in range(0, len(audio), BLOCK)]
    first_cut = next(n for n, fired in enumerate(map(PauseTest(SR, 0.8, 6.0).feed, blocks))
                     if fired)
    test = PauseTest(SR, 0.8, 6.0)
    for block in blocks[:first_cut]:
        test.feed(block)
    old = test._detector
    feed_old = old.feed

    def slider_moved_meanwhile(block):
        test.configure(0.8, 6.0)            # the GTK thread
        return feed_old(block)
    old.feed = slider_moved_meanwhile

    assert test.feed(blocks[first_cut]) is True
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
    assert text.endswith(" · 2 pauses so far")
    assert "Room" not in text and "dB" not in text      # no raw levels
    assert test.result_text().startswith(
        "Test done: 2 pauses found, so TuxTalk would have typed 2 phrases while you talked.")


def test_status_text_in_plain_words():
    from tuxtalk.chunk_meter import PauseTest
    test = PauseTest(SR, 0.8, 6.0)
    feed_all(test, room(1.5))
    assert test.status_text() == "Waiting for you to talk"
    assert test.result_text() == "Test stopped. It didn't hear you talk."
    feed_all(test, speech(0.9))                 # ends mid-syllable
    assert test.status_text() == "Hearing you talk"
    feed_all(test, room(0.4))
    assert test.status_text().startswith("Quiet for 0.")
    assert test.status_text().endswith("s. At 0.8 s it types what you said")
    assert test.result_text().startswith("Test done: no pauses found")
    feed_all(test, room(0.6))
    assert test.status_text().endswith(" · 1 pause so far")
    assert "1 pause found, so TuxTalk would have typed 1 phrase" in test.result_text()


def test_threshold_fraction_follows_the_sensitivity():
    from tuxtalk.chunk_meter import level_to_fraction, threshold_fraction
    assert threshold_fraction(400.0, 6.0) == pytest.approx(level_to_fraction(400 * 10 ** (6 / 20)))
    assert threshold_fraction(400.0, 12.0) > threshold_fraction(400.0, 6.0)
    assert threshold_fraction(1.0, 6.0) == pytest.approx(level_to_fraction(40.0))   # the floor
