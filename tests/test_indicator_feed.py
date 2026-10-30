"""The audio callback feeds only the data the active style needs."""
import numpy as np
from tuxtalk import recording


class StubInd:
    def __init__(self, style):
        self.style = style
        self.level = None
        self.waveform = None
        self.spectrum = None

    def set_audio_level(self, v):
        self.level = v

    def set_waveform(self, s):
        self.waveform = s

    def set_spectrum(self, b):
        self.spectrum = b


def _samples():
    return (np.sin(np.linspace(0, 40, 512)) * 8000).astype(np.int16)


def test_orb_gets_level_only(app):
    ind = StubInd("orb")
    recording.feed_indicator(ind, _samples(), None)
    assert ind.level is not None
    assert ind.waveform is None and ind.spectrum is None


def test_waveform_style_gets_samples(app):
    ind = StubInd("waveform")
    recording.feed_indicator(ind, _samples(), None)
    assert ind.waveform is not None
    assert ind.level is not None       # level still drives brightness


def test_radial_style_gets_samples(app):
    ind = StubInd("radial")
    recording.feed_indicator(ind, _samples(), None)
    assert ind.waveform is not None


def test_bars_style_gets_spectrum(app):
    from tuxtalk.indicator_dsp import SpectrumProcessor
    ind = StubInd("bars")
    recording.feed_indicator(ind, _samples(), SpectrumProcessor(bins=20))
    assert ind.spectrum is not None and len(ind.spectrum) == 20


def test_bars_without_a_processor_does_not_crash(app):
    ind = StubInd("bars")
    recording.feed_indicator(ind, _samples(), None)   # no processor available
    assert ind.level is not None                  # still fed the level
