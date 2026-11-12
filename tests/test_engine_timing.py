"""How long a dictation took, as the log says it (engine.py)."""
import types

import numpy as np

from tuxtalk import engine
from tuxtalk import live_settings as live


class SlowDecoder:
    """faster-whisper's model, as far as timing goes: transcribe returns at
    once, and the decoding happens while its segments are read."""

    def __init__(self, clock):
        self.clock = clock

    def transcribe(self, audio, language=None, **kwargs):
        def segments():
            self.clock.now += 3.0       # three seconds of decoding
            yield types.SimpleNamespace(text="Hello there.", no_speech_prob=0.0)
        return segments(), types.SimpleNamespace(language="en", all_language_probs=None)


def test_the_logged_time_includes_the_decoding(monkeypatch):
    """The time was taken before the segments were read, so a 3 s decode
    was logged as instant and never warned about."""
    clock = types.SimpleNamespace(now=100.0)
    monkeypatch.setattr(engine, "time", types.SimpleNamespace(time=lambda: clock.now))
    monkeypatch.setattr(engine, "model", SlowDecoder(clock), raising=False)
    monkeypatch.setattr(engine, "is_quiet", lambda: False)
    monkeypatch.setattr(live, "words", [])
    monkeypatch.setattr(live, "auto_languages", ())
    warnings = []
    monkeypatch.setattr(engine.logger, "warning", lambda msg, *a, **k: warnings.append(msg))
    assert engine.transcribe(np.zeros(16000, dtype=np.float32), "en") == "Hello there."
    assert any("took 3.00s" in w for w in warnings)
