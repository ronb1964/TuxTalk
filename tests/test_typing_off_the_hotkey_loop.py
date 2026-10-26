"""Transcribing and typing run on the dictation worker, not the hotkey
loop: a dictation key pressed while text is still being typed is read at
once and starts the next recording (CODE_HEALTH task 16). Before, the loop
read it only once typing was done: with F8 the start of what was said was
lost, and with Right Ctrl or Right Alt the press and its release came in
together, a start beep and a cancel beep."""
import threading
import types

import pytest
from evdev import ecodes

from tuxtalk import dictation_worker, hotkeys, keyboard_output, pipeline, recording, send_countdown
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


def _key(code, value):
    """One key event, as the hotkey loop hands it to its handler."""
    event = types.SimpleNamespace(type=ecodes.EV_KEY, code=code, value=value)
    cfg = types.SimpleNamespace(beeps=False, notify=False, smart_quotes=False, language=None,
                                auto_space=True, auto_period=True, injection_mode="type")
    hotkeys._handle_key_event(event, ecodes.KEY_F8, ecodes.KEY_F9, None, set(), [], cfg, 0)


def test_the_hotkey_is_read_while_the_last_dictation_is_still_typing(real_worker, monkeypatch):
    """F8 held, let go, and held again while that dictation is still being
    typed: the second press starts a recording at once. Read only once
    typing was done, the start of what was said was lost."""
    starts, typing, may_finish = [], threading.Event(), threading.Event()

    def start(beeps_on, notify_on, device, from_toggle=False, edit_mode=False, held_send=False):
        rs.state.is_recording = True
        starts.append(device)
        return True

    def stop(beeps_on, notify_on):
        rs.state.is_recording = False
        return ["audio"], 16000

    def long_dictation(*a, **k):
        typing.set()
        assert may_finish.wait(5)
    monkeypatch.setattr(recording, "start_recording", start)
    monkeypatch.setattr(recording, "finish_capture", stop)
    monkeypatch.setattr(pipeline, "transcribe_and_inject", long_dictation)
    _key(ecodes.KEY_F8, 1)
    _key(ecodes.KEY_F8, 0)                      # let go: typed on the worker
    assert typing.wait(5)
    _key(ecodes.KEY_F8, 1)                      # pressed again while it types
    assert len(starts) == 2 and rs.state.is_recording and not real_worker.idle()
    may_finish.set()
    _key(ecodes.KEY_F8, 0)
    assert real_worker.drain(5)


@pytest.fixture
def sent(monkeypatch):
    """Enters pressed, spaces typed and "Not sent" notices said, for a
    dictation whose words went into a chat box."""
    keys, typed, said = [], [], []
    monkeypatch.setattr(keyboard_output, "press_key", lambda key, why, claim=None:
                        keys.append(key) or True)
    monkeypatch.setattr(pipeline, "type_onto_dictation", lambda text, mode, into=None:
                        typed.append(text) or "ok")
    monkeypatch.setattr(send_countdown, "say_held", lambda: said.append("Not sent"))
    monkeypatch.setattr(send_countdown, "_delay", 0.0)
    monkeypatch.setattr(send_countdown, "_send_right_away", send_countdown._send)
    return keys, typed, said


def test_a_recording_begun_while_the_words_were_typed_holds_their_send(sent):
    """Its key was read (and its microphone was still opening) before this
    dictation's Enter was set up: the Enter went out 2 s later, mid-dictation,
    and the message went out in two."""
    keys, typed, said = sent
    message = send_countdown.Message(CHAT, has_words=True)
    send_countdown.recording_begins()                   # the next recording
    pipeline.send_automatically(" ", "type", message)
    assert keys == [] and said == [] and send_countdown.owes_space()


def test_a_recording_begun_once_the_send_is_set_up_holds_it_too(sent, monkeypatch):
    keys, typed, said = sent
    monkeypatch.setattr(send_countdown, "_send_right_away", lambda pending: None)
    pipeline.send_automatically(" ", "type", send_countdown.Message(CHAT, has_words=True))
    number, held = send_countdown.recording_begins()
    assert held and keys == []


def test_without_a_newer_recording_the_send_goes(sent):
    keys, typed, said = sent
    pipeline.send_automatically(" ", "type", send_countdown.Message(CHAT, has_words=True))
    assert keys == ["ENTER"]


def test_a_tap_while_the_words_were_typed_holds_the_send_and_says_so(sent, monkeypatch):
    """The tap is over, its key up: "Not sent", and the space goes in now
    rather than in front of the next message after the user's own Enter."""
    keys, typed, said = sent
    message = send_countdown.Message(CHAT, has_words=True)
    send_countdown.recording_begins()
    monkeypatch.setattr(rs.state, "message", send_countdown.Message(serial=1, cancelled=True))
    monkeypatch.setattr(rs.state, "is_recording", False)
    pipeline.send_automatically(" ", "type", message)
    assert keys == [] and said == ["Not sent"] and typed == [" "]
    assert not send_countdown.owes_space()


def test_a_send_about_to_claim_its_enter_when_a_recording_begins_stays_held(sent, monkeypatch):
    """Counted, then held: an Enter claimed in between went out at the
    start of the next recording (Codex, checking the fix)."""
    from tuxtalk import focused_window, virtual_keyboard
    monkeypatch.setattr(focused_window, "look", lambda: CHAT[:2])
    monkeypatch.setattr(focused_window, "destination", lambda: CHAT)
    monkeypatch.setattr(send_countdown, "_send_right_away", lambda pending: None)
    pipeline.send_automatically(" ", "type", send_countdown.Message(CHAT, has_words=True))
    pending = send_countdown._pending
    take = send_countdown._claim(pending)          # the claim's checks pass...
    send_countdown.recording_begins()              # ...then the next recording begins
    with pytest.raises(virtual_keyboard.GaveUp):
        take()                                     # right before the Enter goes down
