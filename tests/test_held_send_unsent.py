"""A send held for a recording that then adds nothing to it: the microphone
didn't open, Esc, a tap, hands-free hearing nothing. The message stays
unsent, "Not sent" is said once, and the space the send left off goes in
once the dictation key is up, never while it's down (GNOME without the
extension sees the key too and loses the space) and never with Alt or Ctrl
held (Alt+Space opens KRunner)."""
import types

import pytest
from evdev import ecodes

from test_send_destination import (  # noqa: F401  (fixtures)
    FIRST, HELD_TITLE, Countdown, chat, notifications)
from tuxtalk import (delivery, dictation_worker, focused_window, hotkeys, keyboard_grab,
                     modifier_hotkeys, recording, send_countdown)
from tuxtalk import recording_state as rs

F8, F9, ESC = ecodes.KEY_F8, ecodes.KEY_F9, ecodes.KEY_ESC
CFG = types.SimpleNamespace(beeps=False, notify=False, smart_quotes=False, language=None,
                            auto_space=True, auto_period=True, injection_mode="type")


@pytest.fixture
def mic(monkeypatch):
    """The microphone and what a recording's start and end touch, faked.
    mic.fails(meanwhile) makes it fail to open; *meanwhile* runs once, while
    it's first tried."""
    monkeypatch.setattr(rs.state, "is_recording", False)
    for name in ("pick_input_device", "stop_stream_safely", "pause_media_players",
                 "resume_media_players"):
        monkeypatch.setattr(recording, name, lambda *a, **k: 0)
    monkeypatch.setattr(recording, "open_input_stream", lambda idx: None)
    monkeypatch.setattr(recording, "indicator", None)
    monkeypatch.setattr("tuxtalk.type_at_pause.arm_chunker", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.tray_link.notify_recording_state", lambda *a, **k: None)
    monkeypatch.setattr(keyboard_grab, "grab_keyboards_hiding", lambda *a, **k: None)
    monkeypatch.setattr(keyboard_grab, "release_all", lambda: None)

    def fails(meanwhile=lambda: None):
        pending = [meanwhile]

        def no_microphone(idx):
            if pending:
                pending.pop()()
            raise OSError("Device unavailable")
        monkeypatch.setattr(recording, "open_input_stream", no_microphone)
    return types.SimpleNamespace(fails=fails)


class Keyboards:
    """The keyboards the hotkey loop reads (modifier_hotkeys.note), with the
    keys in *down* held on them."""

    def __init__(self):
        self.down = set()

    def capabilities(self, verbose=False):
        return {ecodes.EV_KEY: [ecodes.KEY_A, ecodes.KEY_M, ecodes.KEY_Z]}

    def active_keys(self):
        return sorted(self.down)


@pytest.fixture
def keys_held(monkeypatch):
    """The keys held on the user's keyboard; empty to start with."""
    keyboards = Keyboards()
    monkeypatch.setattr(modifier_hotkeys, "_noted", ([keyboards], frozenset()))
    return keyboards.down


class QueuedWorker:
    """The dictation worker, busy: jobs wait until run() runs them."""

    thread = None

    def __init__(self):
        self.jobs = []

    def submit(self, job):
        self.jobs.append(job)

    def run(self):
        while self.jobs:
            self.jobs.pop(0)()


@pytest.fixture
def busy_worker(monkeypatch):
    worker = QueuedWorker()
    monkeypatch.setattr(dictation_worker, "_instance", worker)
    return worker


def key(code, value, held=(), cfg=CFG):
    """One key event through the hotkey loop's handler, *held* modifiers down."""
    event = types.SimpleNamespace(type=ecodes.EV_KEY, code=code, value=value)
    hotkeys._handle_key_event(event, F8, F9, None, set(held), [], cfg, 0)


def words_done(space=" "):
    """The last dictation's words went in: its automatic send is set up."""
    delivery.send_automatically(space, "type", send_countdown.Message(
        focused_window.destination(), has_words=True))


def counting_down(monkeypatch):
    """The last dictation's send counts down (2 s), owing its space if held."""
    monkeypatch.setattr(send_countdown, "_delay", 2.0)
    monkeypatch.setattr(send_countdown.threading, "Timer", Countdown)
    words_done()


# --- the microphone doesn't open -------------------------------------------

@pytest.mark.parametrize("send_comes", ["while the microphone is tried",
                                        "after it failed, the key still down"])
def test_a_send_held_for_a_recording_whose_microphone_fails_waits_for_the_key(
        chat, keyboard, mic, notifications, send_comes):
    """Nothing said the message wasn't sent; and with the send coming after
    the failure, the space was typed while F8 was still down."""
    focus, heard, typed, notices = chat
    mic.fails(meanwhile=words_done if send_comes.startswith("while") else lambda: None)
    key(F8, 1)
    assert rs.state.is_recording is False
    if send_comes.startswith("after"):
        words_done()
    assert notices == [send_countdown.HELD_NOTICE] and notifications() == [HELD_TITLE]
    assert typed == []                                  # not while F8 is down
    key(F8, 0)
    assert typed == [(" ", FIRST[1])] and not send_countdown.owes_space()
    assert keyboard.events == [] and notices == [send_countdown.HELD_NOTICE]


def test_a_send_that_comes_once_the_key_is_up_gives_its_space_back_at_once(
        chat, keyboard, mic, notifications):
    focus, heard, typed, notices = chat
    mic.fails()
    key(F8, 1)
    key(F8, 0)
    words_done()
    assert notices == [send_countdown.HELD_NOTICE] and notifications() == [HELD_TITLE]
    assert typed == [(" ", FIRST[1])] and keyboard.events == []


def test_a_countdown_held_by_a_press_whose_microphone_fails_gets_its_space_at_the_release(
        chat, keyboard, mic, monkeypatch, notifications):
    """Owed, the space waited for the next dictation, and started the next
    message after the user pressed Enter themselves (" Hi")."""
    focus, heard, typed, notices = chat
    counting_down(monkeypatch)
    mic.fails()
    key(F8, 1)
    assert notices == [send_countdown.HELD_NOTICE] and typed == []
    key(F8, 0)
    assert typed == [(" ", FIRST[1])] and keyboard.events == []


def test_a_start_without_a_key_gives_the_space_back_when_the_microphone_fails(
        chat, keyboard, mic, monkeypatch):
    """The tray, tuxtalk --start: no key will come up, and the space stayed
    owed for good."""
    focus, heard, typed, notices = chat
    counting_down(monkeypatch)
    mic.fails()
    assert recording.start_recording(False, False, 0) is False
    assert notices == [send_countdown.HELD_NOTICE] and typed == [(" ", FIRST[1])]


# --- a press that wasn't TuxTalk's ------------------------------------------

@pytest.mark.parametrize("modifier", [ecodes.KEY_LEFTALT, ecodes.KEY_LEFTCTRL])
@pytest.mark.parametrize("hotkey", [F8, F9])
def test_another_apps_shortcut_on_a_dictation_key_types_no_owed_space(
        chat, keyboard, mic, monkeypatch, modifier, hotkey):
    """Alt+F8 resizes a window on KDE, and Space confirms it; a space typed
    with Alt still down is Alt+Space (KRunner), with Ctrl, Ctrl+Space."""
    focus, heard, typed, notices = chat
    send_countdown._owe_space({"space": " ", "to": focused_window.destination(),
                               "type": delivery.type_onto_dictation, "mode": "type"})
    started = []
    monkeypatch.setattr(recording, "start_recording", lambda *a, **k: started.append(1))
    key(hotkey, 1, held={modifier})
    key(hotkey, 0, held={modifier})
    assert started == [] and typed == [] and send_countdown.owes_space()


def test_a_release_with_ctrl_pressed_meanwhile_keeps_the_space_owed(chat, keyboard, mic,
                                                                    monkeypatch, keys_held):
    focus, heard, typed, notices = chat
    counting_down(monkeypatch)
    mic.fails()
    key(F8, 1)
    keys_held.add(ecodes.KEY_LEFTCTRL)
    key(F8, 0, held={ecodes.KEY_LEFTCTRL})
    assert typed == [] and send_countdown.owes_space()


def test_a_release_with_shift_still_down_gives_the_space_back(chat, keyboard, mic, monkeypatch,
                                                              keys_held):
    """Shift+F8 is a voice edit; Shift+Space is still just a space."""
    focus, heard, typed, notices = chat
    counting_down(monkeypatch)
    mic.fails()
    key(F8, 1)
    keys_held.add(ecodes.KEY_LEFTSHIFT)
    key(F8, 0, held={ecodes.KEY_LEFTSHIFT})
    assert typed == [(" ", FIRST[1])] and not send_countdown.owes_space()


def test_a_toggle_tap_that_holds_the_send_gives_its_space_back_at_the_release(
        chat, keyboard, mic, monkeypatch):
    focus, heard, typed, notices = chat
    counting_down(monkeypatch)
    key(F9, 1)
    assert notices == [send_countdown.HELD_NOTICE] and typed == []
    key(F9, 0)
    assert typed == [(" ", FIRST[1])]


# --- the recording ends with nothing added ----------------------------------

@pytest.mark.parametrize("held_when", ["pressed", "while recording"])
def test_esc_during_a_hold_leaves_the_send_unsent_and_gives_the_space_at_the_release(
        chat, keyboard, mic, monkeypatch, notifications, held_when):
    """Esc cancels the recording: the message stays unsent and that's said;
    the space waits for F8 to come up. A send set up during the recording
    said nothing at all."""
    focus, heard, typed, notices = chat
    if held_when == "pressed":
        counting_down(monkeypatch)
    key(F8, 1)
    assert rs.state.is_recording
    if held_when == "while recording":
        words_done()
    key(ESC, 1)
    assert notices == [send_countdown.HELD_NOTICE] and notifications() == [HELD_TITLE]
    assert typed == []
    key(F8, 0)
    assert typed == [(" ", FIRST[1])] and keyboard.events == []


def test_a_tap_whose_send_was_held_while_it_ran_says_not_sent_once(
        chat, keyboard, mic, monkeypatch, notifications):
    """Notifications on: a "Cancelled" one came as well, or nothing at all."""
    focus, heard, typed, notices = chat
    # Pressed and let go at the same moment: a tap, however busy the machine.
    monkeypatch.setattr(recording, "time", types.SimpleNamespace(time=lambda: 1000.0))
    monkeypatch.setattr(hotkeys, "time", types.SimpleNamespace(time=lambda: 1000.0))
    cfg = types.SimpleNamespace(**{**vars(CFG), "notify": True})
    key(F8, 1, cfg=cfg)
    words_done()
    key(F8, 0, cfg=cfg)                         # a tap: under MIN_HOLD_MS
    assert notices == [send_countdown.HELD_NOTICE]
    assert [t for t in notifications() if not t.startswith("Recording")] == [HELD_TITLE]
    assert typed == [(" ", FIRST[1])]


def test_hands_free_hearing_nothing_leaves_a_send_held_meanwhile_unsent(
        chat, keyboard, mic, monkeypatch, notifications):
    focus, heard, typed, notices = chat
    assert recording.start_recording(False, False, 0, from_toggle=True)
    words_done()
    monkeypatch.setattr(rs.state, "silence", types.SimpleNamespace(nothing_said=True))
    monkeypatch.setattr(recording, "frames_are_dead_mic", lambda frames, rate: False)
    assert recording.finish_capture(False, False) is None
    assert notices == [send_countdown.HELD_NOTICE] and notifications() == [HELD_TITLE]
    assert typed == [(" ", FIRST[1])]


# --- looked at when the space is typed, not when it was queued ---------------

def test_alt_pressed_while_the_space_waits_its_turn_keeps_it_owed(
        chat, keyboard, mic, monkeypatch, keys_held, busy_worker):
    """F8 let go while a long dictation was still being typed: the space
    waited its turn, the user pressed Alt meanwhile, and it went out as
    Alt+Space."""
    focus, heard, typed, notices = chat
    counting_down(monkeypatch)
    mic.fails()
    key(F8, 1)
    key(F8, 0)
    keys_held.add(ecodes.KEY_LEFTALT)
    busy_worker.run()
    assert typed == [] and send_countdown.owes_space()


def test_f8_pressed_again_while_the_space_waits_its_turn_keeps_it_owed(
        chat, keyboard, mic, monkeypatch, busy_worker):
    focus, heard, typed, notices = chat
    counting_down(monkeypatch)
    mic.fails()
    key(F8, 1)
    key(F8, 0)
    key(F8, 1)                                  # its microphone fails again
    busy_worker.run()
    assert typed == [] and send_countdown.owes_space()
    key(F8, 0)
    busy_worker.run()
    assert typed == [(" ", FIRST[1])]


def test_overlapping_dictation_keys_keep_the_space_until_both_are_up(
        chat, keyboard, mic, monkeypatch, busy_worker):
    """F8 held after its microphone failed, F9 pressed and let go (failing
    too): F9's release typed the space while F8 was still down."""
    focus, heard, typed, notices = chat
    counting_down(monkeypatch)
    mic.fails()
    key(F8, 1)
    key(F9, 1)
    key(F9, 0)
    busy_worker.run()
    assert typed == [] and send_countdown.owes_space()
    key(F8, 0)
    busy_worker.run()
    assert typed == [(" ", FIRST[1])]


def test_a_tap_let_go_with_alt_down_keeps_the_space_owed(chat, keyboard, mic, monkeypatch,
                                                         keys_held):
    """A tap that held the send, its recording started: its release gave
    the space back without looking at Alt."""
    focus, heard, typed, notices = chat
    counting_down(monkeypatch)
    key(F8, 1)
    assert rs.state.is_recording
    keys_held.add(ecodes.KEY_LEFTALT)
    key(F8, 0, held={ecodes.KEY_LEFTALT})
    assert notices == [send_countdown.HELD_NOTICE]
    assert typed == [] and send_countdown.owes_space()


def test_alt_going_down_as_the_space_goes_out_keeps_it_owed(keyboard, monkeypatch):
    """Looked at when the job ran, Alt was up; by the space's key it was
    down: the check is made again right before the key."""
    looks = []

    class AltFromTheSecondLook(Keyboards):
        def active_keys(self):
            looks.append(1)
            return [ecodes.KEY_LEFTALT] if len(looks) > 1 else []
    monkeypatch.setattr(modifier_hotkeys, "_noted", ([AltFromTheSecondLook()], frozenset()))
    chat_window = focused_window.Destination("brave-browser", ("tray", "{1}"))
    monkeypatch.setattr(focused_window, "look", lambda: chat_window[:2])
    monkeypatch.setattr(focused_window, "is_password", lambda: False)
    monkeypatch.setattr("time.sleep", lambda s: None)
    send_countdown._owe_space({"space": " ", "to": chat_window, "mode": "type",
                               "type": delivery.type_onto_dictation})
    send_countdown.give_back_space()
    assert len(looks) == 2                      # when the job ran, and right before the key
    assert keyboard.events == [] and send_countdown.owes_space()

