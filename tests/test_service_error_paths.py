"""Error paths in the dictation service must not look like success or block
the hotkeys."""

import threading

from tuxtalk import app
from tuxtalk import recording_state as rs


def test_turning_the_ai_off_does_not_block_the_caller(monkeypatch):
    """stop() waits up to 5 s for the AI server to exit, and the settings
    reload that calls it runs on the input thread, so waiting there would
    stop hotkeys being read."""
    release = threading.Event()
    stopped = threading.Event()

    class SlowEngine:
        def stop(self):
            release.wait(5)
            stopped.set()

    monkeypatch.setattr(app, "_ai_engine", SlowEngine())

    class Cfg:
        auto_stop_silence = False
        remove_fillers = False
        ai_corrections = False
        voice_editing = False

    app._apply_cleanup_settings(Cfg())

    assert app._ai_engine is None
    assert not stopped.is_set(), "the caller waited for the engine to stop"
    release.set()
    assert stopped.wait(2), "the engine was never stopped"


def test_a_transcription_error_sounds_like_a_failure_and_says_so(monkeypatch):
    beeps, notes = [], []
    monkeypatch.setattr(app, "_beep", lambda on, *tone: beeps.append(tone))
    monkeypatch.setattr(app, "_notify", lambda title, body: notes.append(f"{title}\n{body}"))

    def broken(*a, **k):
        raise RuntimeError("engine crashed")

    monkeypatch.setattr(app, "_transcribe_audio", broken)
    monkeypatch.setattr("tuxtalk.silence.is_speechless_tap", lambda *a: False)

    frames = [b"\xe8\x03" * 16000]   # speaking level; pure silence is a dead mic
    app._transcribe_and_inject(frames, app.SAMPLE_RATE, True, False, notify_on=False)

    assert beeps == [app.CANCEL_BEEP], "the 'done' beep played for a failure"
    assert notes and "Transcription failed" in notes[0]


def test_the_mic_stream_is_closed_even_when_stopping_it_fails(monkeypatch):
    class Stream:
        closed = False

        def stop(self, ignore_errors=True):
            raise OSError("device unplugged")

        def close(self, ignore_errors=True):
            Stream.closed = True

    monkeypatch.setattr(rs.state, "stream", Stream())

    app._stop_stream_safely()

    assert Stream.closed
    assert rs.state.stream is None
