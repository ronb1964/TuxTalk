"""Transcribing and typing run on the dictation worker, not the hotkey
loop: a dictation key pressed while text is still being typed is read at
once and starts the next recording (CODE_HEALTH task 16). Before, the loop
read it only once typing was done: with F8 the start of what was said was
lost, and with Right Ctrl or Right Alt the press and its release came in
together, a start beep and a cancel beep."""
import threading

import pytest

from tuxtalk import dictation_worker, keyboard_output, pipeline, recording, send_countdown
from tuxtalk import recording_state as rs
from tuxtalk.dictation_worker import DictationWorker
from tuxtalk.focused_window import Destination

CHAT = Destination("brave-browser", ("tray", "{5c1d0e2a}"))


@pytest.fixture
def real_worker(monkeypatch):
    worker = DictationWorker()
    monkeypatch.setattr(dictation_worker, "_instance", worker)
    return worker


def _stop():
    pipeline.stop_recording(beeps_on=False, smart_quotes=False, notify_on=False)


def test_stopping_returns_while_the_text_is_still_typing(real_worker, monkeypatch):
    typing, may_finish, typed = threading.Event(), threading.Event(), []

    def long_dictation(frames, rec_sr, *a, **k):
        typing.set()
        assert may_finish.wait(5)
        typed.append(frames)
    monkeypatch.setattr(recording, "finish_capture", lambda b, n: (["first"], 16000))
    monkeypatch.setattr(pipeline, "transcribe_and_inject", long_dictation)
    _stop()
    assert typing.wait(5)
    assert typed == [] and not real_worker.idle()     # the loop is free again meanwhile
    may_finish.set()
    assert real_worker.drain(5) and typed == [["first"]]


def test_dictations_are_typed_in_the_order_they_were_recorded(real_worker, monkeypatch):
    first_typing, may_finish, typed = threading.Event(), threading.Event(), []

    def dictation(frames, rec_sr, *a, **k):
        if frames == ["first"]:
            first_typing.set()
            assert may_finish.wait(5)
        typed.append(frames[0])
    monkeypatch.setattr(pipeline, "transcribe_and_inject", dictation)
    monkeypatch.setattr(recording, "finish_capture", lambda b, n: (["first"], 16000))
    _stop()
    assert first_typing.wait(5)
    monkeypatch.setattr(recording, "finish_capture", lambda b, n: (["second"], 16000))
    _stop()                                     # recorded while the first was typing
    may_finish.set()
    assert real_worker.drain(5) and typed == ["first", "second"]


@pytest.fixture
def sent(monkeypatch):
    """Enters and spaces sent, and "Not sent" notices said."""
    keys, said = [], []
    monkeypatch.setattr(keyboard_output, "press_key", lambda key, why, claim=None:
                        keys.append(key) or True)
    monkeypatch.setattr(send_countdown, "say_held", lambda: said.append("Not sent"))
    monkeypatch.setattr(send_countdown, "_owed", None)
    return keys, said


def test_a_send_waits_for_a_recording_started_while_its_words_were_typed(sent, monkeypatch):
    """The next recording began before this dictation's Enter: it's held, as
    a press during the countdown holds it, and what's said next joins it."""
    keys, said = sent
    message = send_countdown.Message(CHAT, has_words=True)
    monkeypatch.setattr(rs.state, "message", send_countdown.Message())   # the next recording
    pipeline.send_automatically(" ", "type", message)
    assert keys == [] and said == [] and send_countdown.owes_space()


def test_a_tap_while_the_words_were_typed_holds_the_send_and_says_so(sent, monkeypatch):
    keys, said = sent
    message = send_countdown.Message(CHAT, has_words=True)
    monkeypatch.setattr(rs.state, "message", send_countdown.Message(cancelled=True))
    pipeline.send_automatically(" ", "type", message)
    assert keys == [] and said == ["Not sent"]
