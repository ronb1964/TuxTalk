"""The waveform/radial indicator must draw from a rolling window of recent audio,
not just the latest raw block.

Showing only the latest block makes the waveform hostage to the audio driver's
block size: the system PortAudio hands small varied blocks ~193x/sec, but the
Flatpak's bundled PortAudio hands uniform 384-frame blocks ~115x/sec, so the same
code would render a lively wave on one and a stretched sine on the other. A rolling
window makes the display identical regardless of block size. And stop_recording
must clear it, or a bright dash lingers for seconds after releasing the key.
"""

import pytest


@pytest.fixture
def indicator():
    from gi.repository import Gtk

    from tuxtalk.recording_indicator import RecordingIndicator

    ind = RecordingIndicator(position="center", size="medium", style="waveform")
    yield ind
    ind.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration()


def test_waveform_accumulates_a_bounded_rolling_window(indicator):
    # 50 small blocks of 100 samples = 5000 fed. The display must accumulate a
    # rolling window across blocks (not show only the last 100), yet stay bounded.
    for _ in range(50):
        indicator.set_waveform([0.1] * 100)
    buf = indicator.waveform
    assert buf is not None
    assert len(buf) > 100, "must accumulate across blocks, not show only the latest"
    assert len(buf) <= 4096, "rolling window must stay bounded, not grow unbounded"


def test_waveform_window_is_block_size_independent(indicator):
    # Feeding the SAME total samples in different block sizes must yield the same
    # window length — that's the whole point (driver block size stops mattering).
    for _ in range(20):
        indicator.set_waveform([0.2] * 384)     # big uniform blocks (Flatpak-like)
    big = len(indicator.waveform)

    from tuxtalk.recording_indicator import RecordingIndicator
    ind2 = RecordingIndicator(position="center", size="medium", style="waveform")
    try:
        for _ in range(20 * 6):
            ind2.set_waveform([0.2] * 64)        # small varied-ish blocks (native-like)
        small = len(ind2.waveform)
    finally:
        ind2.destroy()
    assert big == small, "window length must not depend on the feeding block size"


def test_stop_recording_clears_waveform_and_spectrum(indicator):
    indicator.set_waveform([0.5] * 200)
    indicator.set_spectrum([0.5] * 24)
    indicator.stop_recording()
    assert not indicator.waveform, "waveform must clear on stop (no lingering dash)"
    assert not indicator.spectrum, "spectrum must clear on stop"


def test_hide_indicator_also_clears_the_buffers(indicator):
    # The app only ever calls hide_indicator(), so hiding must clear them too,
    # or the last dictation's sound flashes up when the next one starts.
    from gi.repository import Gtk
    indicator.set_waveform([0.5] * 200)
    indicator.set_spectrum([0.5] * 24)
    indicator.hide_indicator()
    while Gtk.events_pending():
        Gtk.main_iteration()
    assert not indicator.waveform
    assert not indicator.spectrum
