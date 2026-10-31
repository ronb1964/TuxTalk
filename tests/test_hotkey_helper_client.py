"""TuxTalk's side of the hotkey helper (hotkey_helper_client.py).

These run the service's real key handling (hotkeys._handle_key_event and the
grab code around it) against a real helper over a real socket, with a fake
keyboard underneath, to show the helper can stand in for the keyboards
without changing what hold, Shift+hold and the safety nets do.
"""
import os
import socket
import threading

import pytest

from hotkey_helper_fakes import FakeKeyboard
from tuxtalk import focused_window, hotkey_helper as hh
from tuxtalk import hotkeys, keyboard_devices
from tuxtalk import keyboard_output
from tuxtalk import keyboard_grab
from tuxtalk import recording_state as rs
from tuxtalk.hotkey_helper_client import HelperKeyboard, connect_helper, watch_specs
from tuxtalk.hotkey_keys import track_modifier
from tuxtalk import live_settings as live
from tuxtalk import recording
from tuxtalk import pipeline

F8, F9, ESC = 66, 67, 1
RCTRL = 97
LSHIFT = 42


def test_watch_specs():
    combos = (({"CTRL", "ALT"}, 47), ({"CTRL", "ALT"}, 25), None)
    assert watch_specs(F8, F9, ESC, combos) == ["66", "67", "1", "AC:47", "AC:25"]
    assert watch_specs(F8, None, ESC, ()) == ["66", "1"]
    for spec in watch_specs(F8, F9, ESC, combos):
        hh.parse_spec(spec)                 # TuxTalk's defaults are all allowed


def test_a_combo_the_helper_wont_report_is_named():
    from tuxtalk.hotkey_helper_client import refused_combo
    assert refused_combo({"CTRL", "ALT"}, 25) is None
    assert "two of Ctrl, Alt and Super" in refused_combo({"CTRL", "SHIFT"}, 35)


@pytest.fixture
def rig(tmp_path, monkeypatch, request):
    """A real helper with one fake keyboard, and TuxTalk's client connected
    to it and watching F8, F9 and Esc. Each part is closed afterwards, also
    when setting up a later one fails."""
    seat = tmp_path / "seat0"
    seat.write_text(f"ACTIVE_UID={os.getuid()}\n")
    (tmp_path / "input").mkdir()
    helper = hh.Helper(socket_dir=str(tmp_path), seat_file=str(seat),
                       input_dir=str(tmp_path / "input"))
    helper.listen()
    request.addfinalizer(helper.listener.close)
    kb = FakeKeyboard()
    request.addfinalizer(lambda: (kb.close(), os.close(kb.w), FakeKeyboard.made.remove(kb)))
    helper.keyboards[kb.path] = kb
    helper.next_scan = float("inf")

    client = HelperKeyboard(helper.socket_path)
    request.addfinalizer(client.close)
    # connect() waits for HELLO's answer, which needs the helper to run.
    answered = threading.Event()

    def serve():
        while not answered.is_set():
            helper.run_once(timeout=0.01)
    t = threading.Thread(target=serve)
    t.start()
    try:
        assert client.connect()
    finally:
        answered.set()
        t.join(5)
    assert not t.is_alive(), "the helper's serving thread didn't stop"
    client.watch(["66", "67", "1"])
    for _ in range(3):
        helper.run_once(timeout=0)          # the helper takes the WATCH in
    client.active_keys()                    # and TuxTalk its OK

    calls = []
    monkeypatch.setattr(keyboard_grab, "grabbed_devices", [])
    monkeypatch.setattr(keyboard_grab, "_injected_pending", [])
    monkeypatch.setattr(live, "voice_editing", False)
    monkeypatch.setattr(hotkeys, "_hotkey_test_active", lambda: False)
    monkeypatch.setattr(rs.state, "is_recording", False)
    monkeypatch.setattr(rs.state.current, "edit_mode", False)

    def start(*_a, edit_mode=False, **_k):
        calls.append(("start", edit_mode))
        rs.state.is_recording = True

    def stop(*_a, **_k):
        calls.append(("stop",))
        rs.state.is_recording = False

    monkeypatch.setattr(recording, "start_recording", start)
    monkeypatch.setattr(pipeline, "stop_recording", stop)
    held = set()

    class Cfg:
        beeps = notify = smart_quotes = auto_space = auto_period = True
        language = ""
        injection_mode = "type"

    def loop(times=3, hold=F8):
        """What hotkeys.run does each pass, for the helper's one 'keyboard'."""
        for _ in range(times):
            helper.run_once(timeout=0)
            for dev in keyboard_devices.devices_with_events([client], 0):
                try:
                    events = list(dev.read())
                except BlockingIOError:
                    continue
                for event in events:
                    if keyboard_grab.is_own_injected_event(dev, event):
                        continue
                    track_modifier(held, event)
                    hotkeys._handle_key_event(event, hold, F9, None, held,
                                          [client], Cfg, None)

    return helper, kb, client, loop, calls


def test_holding_the_dictation_key_records_and_hides_it(rig):
    helper, kb, client, loop, calls = rig
    kb.press(F8)
    loop()
    assert calls == [("start", False)]
    assert helper.hide is not None
    assert kb.calls == [("inject", F8, 0), ("grab", True), ("inject", F8, 1)]
    kb.press(F8, 0)
    loop()
    assert calls == [("start", False), ("stop",)]
    assert helper.hide is None and kb.calls[-1] == ("grab", False)
    assert keyboard_grab.grabbed_devices == []


def test_shift_with_the_dictation_key_starts_an_edit_and_hides_shift_too(rig, monkeypatch):
    helper, kb, client, loop, calls = rig
    monkeypatch.setattr(live, "voice_editing", True)
    kb.press(LSHIFT)
    kb.press(F8)
    loop()
    assert calls == [("start", True)]
    assert {code for _, code in helper.hide["presses"]} == {F8}
    assert ("inject", LSHIFT, 0) in kb.calls     # Shift hidden from the desktop too
    kb.press(LSHIFT, 0)
    kb.press(F8, 0)
    loop()
    assert calls[-1] == ("stop",) and helper.hide is None


def test_a_quick_tap_still_starts_and_stops(rig):
    helper, kb, client, loop, calls = rig
    kb.press(F8)
    kb.press(F8, 0)
    loop(5)
    assert calls == [("start", False), ("stop",)]
    assert helper.hide is None


def test_esc_cancels(rig, monkeypatch):
    helper, kb, client, loop, calls = rig
    cancelled = []
    monkeypatch.setattr(recording, "cancel_recording", lambda *a, **k: cancelled.append(a[-1]))
    kb.press(F9)
    kb.press(F9, 0)
    loop()
    kb.press(ESC)
    kb.press(ESC, 0)
    loop()
    assert cancelled == ["Cancelled by ESC"]


def test_if_the_helper_goes_away_a_held_key_is_let_go(rig):
    helper, kb, client, loop, calls = rig
    kb.press(F8)
    loop()
    for c in list(helper.clients.values()):
        helper.drop_client(c)            # helper restarted
    loop()
    assert calls == [("start", False), ("stop",)]
    assert helper.hide is None           # and the keyboards were given back


def test_after_the_helper_restarts_the_dictation_keys_work_again(rig):
    """Updating the hotkey helper restarts it, and dictation isn't restarted
    with it (a dictation under way would be lost; update_offers.update_helper):
    the connection must come back by itself and watch its keys again."""
    helper, kb, client, loop, calls = rig
    for c in list(helper.clients.values()):
        helper.drop_client(c)                   # the helper restarted
    serving = threading.Event()

    def serve():
        while not serving.is_set():
            helper.run_once(timeout=0.01)
    t = threading.Thread(target=serve)
    t.start()
    try:
        for _ in range(100):
            client._pump()                      # sees it gone, then connects again
            client.reconnect_at = 0.0
            if client.sock is not None and helper.clients:
                break
            serving.wait(0.01)
    finally:
        serving.set()
        t.join(5)
    assert client.sock is not None
    kb.press(F8)
    loop()
    assert calls == [("start", False)]

@pytest.fixture
def right_ctrl(rig, keyboard, monkeypatch):
    """The rig with Right Ctrl as the hold key, and typing's waits on the
    test keyboard's fake clock. Returns how to type a letter."""
    from evdev import ecodes
    from tuxtalk import modifier_hotkeys
    helper, kb, client, loop, calls = rig
    client.watch([str(RCTRL), "67", "1"])
    for _ in range(3):
        helper.run_once(timeout=0)
    client.active_keys()
    for name, value in (("_noted", ([], frozenset())), ("_hidden", (frozenset(), ())),
                        ("_stuck", set()), ("time", keyboard.clock)):
        monkeypatch.setattr(modifier_hotkeys, name, value)
    monkeypatch.setattr(focused_window, "destination", lambda: ("kwrite", None, False))
    modifier_hotkeys.note([client], RCTRL, F9)
    return lambda: keyboard_output.send_keys([(ecodes.KEY_A, 1), (ecodes.KEY_A, 0)], what="a")


def test_a_hold_key_the_helper_couldnt_hide_still_holds_typing_up(rig, right_ctrl, keyboard):
    """The helper said no (a remapper or a VM viewer holds the keyboard), so
    the desktop still sees Right Ctrl down. Counted as hidden once HIDE was
    sent, a phrase of Type at pause went in as Ctrl shortcuts (Ctrl+W)."""
    helper, kb, client, loop, calls = rig

    def grab_refused(on):
        kb.calls.append(("grab", on))
        return False
    kb.grab = grab_refused
    kb.press(RCTRL)
    loop(hold=RCTRL)
    assert calls == [("start", False)] and helper.hide is None
    assert right_ctrl() is False
    assert keyboard.events == []


def test_typing_waits_for_the_helper_to_say_the_hold_key_is_hidden(rig, right_ctrl, keyboard,
                                                                   monkeypatch):
    """HIDE is answered after a round trip through the helper: until then
    the desktop may still see the key down."""
    helper, kb, client, loop, calls = rig
    kb.press(RCTRL)
    loop(1, hold=RCTRL)                  # HIDE sent, not yet taken in by the helper
    assert calls == [("start", False)] and helper.hide is None
    from tuxtalk import modifier_hotkeys
    typed_while_waiting = []

    class HelperAnswersMeanwhile:
        """Typing's waits for the key, during which the helper runs."""
        now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            typed_while_waiting.append(list(keyboard.events))
            helper.run_once(timeout=0)
            self.now += seconds
    monkeypatch.setattr(modifier_hotkeys, "time", HelperAnswersMeanwhile())
    assert right_ctrl()
    assert typed_while_waiting and typed_while_waiting[0] == []
    assert keyboard.typed_keys() == [30]            # KEY_A, once the helper had hidden it
    assert helper.hide is not None and ("inject", RCTRL, 0) in kb.calls


def test_events_taken_in_early_are_read_without_waiting(rig):
    helper, kb, client, loop, calls = rig
    kb.press(F8)
    helper.run_once(timeout=0)
    client.active_keys()                 # takes the DOWN in, as grab_keyboards_hiding does
    assert client.needs_service()
    assert keyboard_devices.devices_with_events([client], 5.0) == [client]


def test_no_helper_means_none(tmp_path):
    assert connect_helper(str(tmp_path / "nothing.sock")) is None


def test_an_installed_helper_whose_socket_isnt_there_yet_is_still_used(tmp_path, monkeypatch):
    """Stopped, or restarting just as dictation starts: systemd removes its
    folder. Once System check's Update starts it again, the hotkeys work
    without a restart, as it says."""
    from tuxtalk import secure_input, uinput_helper
    monkeypatch.setattr(secure_input, "is_installed", lambda: True)
    monkeypatch.setattr(uinput_helper, "_readable_keyboards", lambda: [])
    keyboard = connect_helper(str(tmp_path / "not-yet.sock"))
    assert keyboard is not None and keyboard.sock is None
    keyboard.close()


def test_someone_still_in_the_input_group_reads_the_keyboards_while_the_helper_is_down(
        tmp_path, monkeypatch):
    """They kept the group when switching to the helper, or couldn't leave
    it: their hotkeys work either way, as System check tells them, so a
    stopped helper mustn't leave them with none."""
    from tuxtalk import secure_input, uinput_helper
    monkeypatch.setattr(secure_input, "is_installed", lambda: True)
    monkeypatch.setattr(uinput_helper, "_readable_keyboards", lambda: ["/dev/input/event3"])
    assert connect_helper(str(tmp_path / "not-yet.sock")) is None


def test_a_helper_that_doesnt_answer_yet_is_tried_again(tmp_path):
    """Restarting, or this session not in front yet: keep trying rather than
    fall back for good, which would leave the hotkeys dead."""
    stale = tmp_path / "stale.sock"
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(stale))
    s.close()                            # a socket file nobody listens on
    keyboard = connect_helper(str(stale))
    assert keyboard is not None and keyboard.sock is None
    keyboard.close()


def test_the_service_uses_the_helper_when_it_is_there(monkeypatch):
    from tuxtalk import input_backends, hotkey_helper_client
    sentinel = object()
    monkeypatch.setattr(hotkey_helper_client, "connect_helper", lambda: sentinel)
    backend = input_backends.get_input_backend(flatpak_id="")
    assert isinstance(backend, input_backends.EvdevInputBackend) and backend.helper is sentinel
    monkeypatch.setattr(hotkey_helper_client, "connect_helper", lambda: None)
    assert input_backends.get_input_backend(flatpak_id="").helper is None


def test_a_remappers_keyboard_not_held_isnt_logged_as_a_fault(tmp_path, caplog):
    """With kanata the helper never holds its keyboard: every dictation said
    "refused HIDE" as a warning, which read as a fault in a problem report."""
    import logging
    from collections import deque
    client = HelperKeyboard(str(tmp_path / "helper.sock"))
    client.asked = deque(["HIDE"])
    with caplog.at_level(logging.INFO):
        client._handle(["ERR", "listen-only", "keyboard"])
    assert not client.hidden
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "didn't hide" in caplog.text
    client.close()


def test_an_answer_out_of_step_isnt_taken_as_a_hide(tmp_path, caplog):
    """Each OK names its command; an OK WATCH where HIDE's answer was due
    must not let typing go on under a key the helper never hid."""
    from collections import deque
    client = HelperKeyboard(str(tmp_path / "helper.sock"))
    try:
        client.asked = deque(["HIDE"])
        with caplog.at_level("WARNING", logger="tuxtalk.hotkey_helper_client"):
            client._handle(["OK", "WATCH"])
        assert client.hidden is False
        assert "answered OK WATCH to HIDE" in caplog.text
        client.asked = deque(["HIDE"])
        client._handle(["OK", "HIDE"])
        assert client.hidden is True
    finally:
        client.close()


def test_a_grab_with_nothing_let_up_hides_nothing(tmp_path):
    """A quick tap already up when the grab came: the desktop was told
    nothing, so a Right Ctrl dictation key held meanwhile with another
    modifier (the user's own shortcut) isn't hidden either."""
    ours, helpers = socket.socketpair()
    client = HelperKeyboard(str(tmp_path / "helper.sock"))
    client.sock = ours
    client.held = {97}
    client.grab()
    helpers.setblocking(False)
    with pytest.raises(BlockingIOError):
        helpers.recv(100)
    client.write(1, 66, 0)                  # F8 was let up for the desktop
    client.grab()
    assert helpers.recv(100) == b"HIDE 66\n"
    helpers.close()
    client.close()
