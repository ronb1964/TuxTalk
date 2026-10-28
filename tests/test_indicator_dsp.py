"""Spectrum DSP for the 'bars' indicator style.

Per-band background subtraction: each band learns its own quiet baseline and
shows only energy above it, so a steady tone (AC hum, mic DC offset) fades to
nothing while speech transients pop. Pure numpy — tested without a display.
"""
import numpy as np

from tuxtalk.indicator_dsp import SpectrumProcessor

SR = 16000


def _tone(freq, n, amp=0.3):
    t = np.arange(n) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_output_shape_and_range():
    sp = SpectrumProcessor(sample_rate=SR, bins=20)
    out = sp.process(_tone(1000, 1600))
    assert out.shape == (20,)
    assert np.all(out >= 0.0) and np.all(out <= 1.0)


def test_steady_tone_is_subtracted_to_near_zero():
    sp = SpectrumProcessor(sample_rate=SR, bins=20)
    tone = _tone(1000, 1600)
    last = None
    for _ in range(400):
        last = sp.process(tone)
    assert last.max() < 0.15, "a steady tone should decay toward zero"


def test_silence_is_zero():
    sp = SpectrumProcessor(sample_rate=SR, bins=20)
    out = sp.process(np.zeros(1600, dtype=np.float32))
    assert out.max() < 1e-6


def test_steady_dc_offset_is_removed():
    """A constant (pure DC) signal must not show as spectrum energy — the mean
    is subtracted before the FFT, so once the ring is filled it reads as zero
    and the lowest bar doesn't sit pinned high."""
    sp = SpectrumProcessor(sample_rate=SR, bins=20)
    const = np.full(1600, 0.8, dtype=np.float32)
    out = None
    for _ in range(6):        # fill the ring buffer with the constant
        out = sp.process(const)
    assert out.max() < 0.05, "steady DC offset must be removed, not shown"


def _bars_over(make_block, seconds, proc=None):
    from tuxtalk.indicator_dsp import SpectrumProcessor
    import numpy as np
    proc = proc or SpectrumProcessor(bins=20)
    out = [proc.process(make_block(i)) for i in range(int(seconds * 100))]
    return proc, np.array(out)


def _room(db, seed=0):
    import numpy as np
    rng = np.random.default_rng(seed)
    return lambda i: (rng.standard_normal(160) * 10 ** (db / 20)).astype(np.float32)


def _voice(db, start=0):
    """Voiced speech-like sound: harmonics on a moving pitch, syllable rhythm."""
    import numpy as np
    def block(i):
        t = (np.arange(160) + (start + i) * 160) / 16000
        f0 = 140 + 30 * np.sin(2 * np.pi * 1.3 * t)
        env = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)
        sig = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 12))
        return (10 ** (db / 20) * env * sig / 2).astype(np.float32)
    return block


def test_silence_does_not_grow_into_tall_bars():
    """With nothing said, the bars stay low. A per-band ceiling that decays
    with no floor would shrink onto the room's own noise and stretch that to
    full height (half height within a second)."""
    _proc, bars = _bars_over(_room(-55), 10)
    assert bars[100:].mean() < 0.1          # after the first second
    assert bars[-100:].max() < 0.35         # and still low ten seconds in


def test_speech_after_silence_still_fills_the_bars():
    import numpy as np
    proc, _ = _bars_over(_room(-55), 3)
    room = _room(-55, seed=1)
    voice = _voice(-30, start=300)
    _proc, bars = _bars_over(lambda i: voice(i) + room(i), 1, proc)
    assert np.percentile(bars, 90) > 0.6


def test_a_48_khz_microphone_lights_the_same_bar_as_16_khz(monkeypatch):
    """The bars were built for 16 kHz whatever the microphone recorded at,
    so on a 48 kHz one a 1 kHz voice lit a bar three times too high. The
    recording builds them for the rate it opens the stream with."""
    from tuxtalk import mic_stream, recording
    from tuxtalk import recording_state as rs
    monkeypatch.setattr(recording, "spectrum_processor", SpectrumProcessor(bins=20))
    monkeypatch.setattr(mic_stream, "input_samplerate", lambda idx, default: 48000)

    class Stream:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass
    monkeypatch.setattr(recording.sd, "InputStream", Stream)
    monkeypatch.setattr(rs.state, "stream", None)
    recording.open_input_stream(None)

    def loudest_bar(sp, rate):
        t = np.arange(rate) / rate
        sp.process(np.zeros(rate, dtype=np.float32))            # a quiet baseline first
        return int(np.argmax(sp.process((0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32))))
    assert loudest_bar(recording.spectrum_processor, 48000) == \
        loudest_bar(SpectrumProcessor(bins=20), 16000)
