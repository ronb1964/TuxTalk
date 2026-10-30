"""Transcribing and typing run on the dictation worker, not the hotkey
loop: a dictation key pressed while text is still being typed is read at
once and starts the next recording (CODE_HEALTH task 16). Before, the loop
read it only once typing was done: with F8 the start of what was said was
lost, and with Right Ctrl or Right Alt the press and its release came in
together, a start beep and a cancel beep."""
import threading
import time
import types

import pytest
from evdev import ecodes

from tuxtalk import (delivery, dictation_worker, hotkeys, keyboard_output, pipeline, recording,
                     send_countdown)
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
    monkeypatch.setattr(delivery, "type_onto_dictation", lambda text, mode, into=None:
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
    delivery.send_automatically(" ", "type", message)
    assert keys == [] and said == [] and send_countdown.owes_space()


def test_a_recording_begun_once_the_send_is_set_up_holds_it_too(sent, monkeypatch):
    keys, typed, said = sent
    monkeypatch.setattr(send_countdown, "_send_right_away", lambda pending: None)
    delivery.send_automatically(" ", "type", send_countdown.Message(CHAT, has_words=True))
    number, held = send_countdown.recording_begins()
    assert held and keys == []


def test_without_a_newer_recording_the_send_goes(sent):
    keys, typed, said = sent
    delivery.send_automatically(" ", "type", send_countdown.Message(CHAT, has_words=True))
    assert keys == ["ENTER"]


def test_a_tap_while_the_words_were_typed_holds_the_send_and_says_so(sent, monkeypatch):
    """The tap is over, its key up: "Not sent", and the space goes in now
    rather than in front of the next message after the user's own Enter."""
    keys, typed, said = sent
    message = send_countdown.Message(CHAT, has_words=True)
    send_countdown.recording_begins()
    monkeypatch.setattr(rs.state.current, "message", send_countdown.Message(serial=1, cancelled=True))
    monkeypatch.setattr(rs.state, "is_recording", False)
    delivery.send_automatically(" ", "type", message)
    assert keys == [] and said == ["Not sent"] and typed == [" "]
    assert not send_countdown.owes_space()


class _LockThatLetsAClaimIn:
    """send_countdown's _send_lock that runs *claim* the first time it's let
    go: another thread making the Enter's claim at that very moment."""

    def __init__(self, lock, claim):
        self.lock, self.claim, self.refused = lock, claim, None

    def __enter__(self):
        self.lock.acquire()

    def __exit__(self, *exc):
        self.lock.release()
        claim, self.claim = self.claim, None
        if claim is not None:
            from tuxtalk import virtual_keyboard
            try:
                claim()
                self.refused = False
            except virtual_keyboard.GaveUp:
                self.refused = True


def test_a_send_about_to_claim_its_enter_when_a_recording_begins_stays_held(sent, monkeypatch):
    """The claim's checks pass, then the next recording begins: an Enter
    claimed the moment that recording was counted, before its send was held,
    went out as the user started talking again."""
    from tuxtalk import focused_window
    monkeypatch.setattr(focused_window, "look", lambda: CHAT[:2])
    monkeypatch.setattr(focused_window, "destination", lambda: CHAT)
    monkeypatch.setattr(send_countdown, "_send_right_away", lambda pending: None)
    delivery.send_automatically(" ", "type", send_countdown.Message(CHAT, has_words=True))
    take = send_countdown._claim(send_countdown._pending)
    lock = _LockThatLetsAClaimIn(send_countdown._send_lock, take)
    monkeypatch.setattr(send_countdown, "_send_lock", lock)
    send_countdown.recording_begins()
    assert lock.refused is True


def _press_from_the_send_timer(worker):
    """delivery.press_key as the automatic send's timer thread calls it,
    returned once its Enter is queued behind the job *worker* is running:
    the thread, and a list that gets its answer."""
    answer = []
    thread = threading.Thread(target=lambda: answer.append(
        delivery.press_key("ENTER", "Send each dictation automatically is on")))
    thread.start()
    _until_queued(worker, 2)
    return thread, answer


def _until_queued(worker, jobs):
    """Wait until *worker* has *jobs* running or waiting."""
    for _ in range(500):
        if worker._queue.unfinished_tasks >= jobs:
            return
        time.sleep(0.01)
    raise AssertionError("the job never reached the worker")


def test_the_send_s_enter_waits_for_the_typing_under_way(real_worker, monkeypatch):
    """A Paste Last Dictation (or a phrase) had made its checks and not yet
    typed when the countdown ran out: pressed from the timer thread at once,
    the Enter went down between them, sent the box, and the text landed in
    the empty box after it."""
    order = []
    under_way, may_finish = threading.Event(), threading.Event()

    def paste():
        order.append("checks")
        under_way.set()
        assert may_finish.wait(5)
        order.append("typed")
    monkeypatch.setattr(keyboard_output, "press_key",
                        lambda key, why, claim=None: order.append(key) or True)
    real_worker.submit(paste)
    assert under_way.wait(5)
    thread, answer = _press_from_the_send_timer(real_worker)
    assert order == ["checks"]                  # the Enter waits its turn
    may_finish.set()
    thread.join(5)
    assert order == ["checks", "typed", "ENTER"] and answer == [True]


def test_an_enter_forgets_what_it_sent_and_not_what_comes_after(real_worker, monkeypatch):
    """The Enter and its forgetting are one step on the worker: the dictation
    typed before it is forgotten ("edit that" says it was sent), and one
    typed after it keeps its undo, "edit that" and voice moves."""
    monkeypatch.setattr(keyboard_output, "press_key", lambda key, why, claim=None: True)
    monkeypatch.setattr(rs.state, "sent", None)
    typing, may_finish = threading.Event(), threading.Event()

    def still_typing():
        rs.state.last_inserted_text = "See you at noon."
        typing.set()
        assert may_finish.wait(5)
    real_worker.submit(still_typing)
    assert typing.wait(5)
    thread, answer = _press_from_the_send_timer(real_worker)
    real_worker.submit(lambda: setattr(rs.state, "last_inserted_text", "How are you? "))
    may_finish.set()
    thread.join(5)
    assert real_worker.drain(5) and answer == [True]
    assert rs.state.last_inserted_text == "How are you? " and rs.state.sent is not None


@pytest.mark.parametrize("meanwhile", ["tap", "switch"])
def test_a_send_held_or_switched_away_from_while_its_enter_waits_its_turn(real_worker,
                                                                         monkeypatch, meanwhile):
    """The countdown ran out while the worker was still busy, and the Enter
    waited its turn there: a tap of the hold key, or a click into another
    window, during that wait must still keep it from going, and the
    dictation stays where undo reaches it."""
    from tuxtalk import focused_window, virtual_keyboard
    now = {"look": CHAT}
    monkeypatch.setattr(focused_window, "look", lambda: now["look"][:2])
    monkeypatch.setattr(focused_window, "destination", lambda: now["look"])
    monkeypatch.setattr(send_countdown, "_say_not_sent", lambda *a, **k: None)
    monkeypatch.setattr(send_countdown, "_delay", 0.0)
    queued = []
    monkeypatch.setattr(send_countdown, "_send_right_away", queued.append)
    keys = []

    def press_key(key, why, claim=None):
        try:
            claim()()                           # as the virtual keyboard makes it
        except virtual_keyboard.GaveUp:
            return False
        keys.append(key)
        return True
    monkeypatch.setattr(keyboard_output, "press_key", press_key)
    monkeypatch.setattr(rs.state, "last_inserted_text", "See you at noon.")
    delivery.send_automatically(" ", "type", send_countdown.Message(CHAT, has_words=True))
    busy, may_finish = threading.Event(), threading.Event()
    real_worker.submit(lambda: busy.set() or may_finish.wait(5))
    assert busy.wait(5)
    timer = threading.Thread(target=send_countdown._send, args=(queued[0],))
    timer.start()
    _until_queued(real_worker, 2)
    if meanwhile == "tap":
        send_countdown.recording_begins()
    else:
        now["look"] = Destination("org.kde.konsole", ("tray", "{9a0b}"))
    may_finish.set()
    timer.join(5)
    assert real_worker.drain(5)
    assert keys == [] and rs.state.last_inserted_text == "See you at noon."
