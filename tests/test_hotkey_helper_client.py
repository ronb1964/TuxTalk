"""TuxTalk's side of the hotkey helper (hotkey_helper_client.py).

These run the service's real key handling (app._handle_key_event and the
grab code around it) against a real helper over a real socket, with a fake
keyboard underneath, to show the helper can stand in for the keyboards
without changing what hold, Shift+hold and the safety nets do.
"""
import socket

import pytest

from tuxtalk import app, hotkey_helper as hh
from tuxtalk.hotkey_helper_client import HelperKeyboard, connect_helper, watch_specs
from tuxtalk.hotkey_keys import track_modifier
from test_hotkey_helper import FakeKeyboard

F8, F9, ESC = 66, 67, 1
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
def rig(tmp_path, monkeypatch):
    seat = tmp_path / "seat0"
    import os
    seat.write_text(f"ACTIVE_UID={os.getuid()}\n")
    (tmp_path / "input").mkdir()
    helper = hh.Helper(socket_dir=str(tmp_path), seat_file=str(seat),
                       input_dir=str(tmp_path / "input"))
    helper.listen()
    kb = FakeKeyboard()
    helper.keyboards[kb.path] = kb
    helper.next_scan = float("inf")

    client = HelperKeyboard(helper.socket_path)
    # connect() waits for HELLO's answer, which needs the helper to run.
    import threading
    answered = threading.Event()

    def serve():
        while not answered.is_set():
            helper.run_once(timeout=0.01)
    t = threading.Thread(target=serve)
    t.start()
    assert client.connect()
    answered.set()
    t.join()
    client.watch(["66", "67", "1"])
    for _ in range(3):
        helper.run_once(timeout=0.01)       # the helper takes the WATCH in
    client.active_keys()                    # and TuxTalk its OK

    calls = []
    monkeypatch.setattr(app, "_grabbed_devices", [])
    monkeypatch.setattr(app, "_injected_pending", [])
    monkeypatch.setattr(app, "_voice_editing", False)
    monkeypatch.setattr(app, "_hotkey_test_active", lambda: False)
    monkeypatch.setattr(app.state, "is_recording", False)
    monkeypatch.setattr(app.state, "edit_mode", False, raising=False)

    def start(*_a, edit_mode=False, **_k):
        calls.append(("start", edit_mode))
        app.state.is_recording = True

    def stop(*_a, **_k):
        calls.append(("stop",))
        app.state.is_recording = False

    monkeypatch.setattr(app, "start_recording", start)
    monkeypatch.setattr(app, "stop_recording", stop)
    held = set()

    class Cfg:
        beeps = notify = smart_quotes = auto_space = auto_period = True
        language = ""
        injection_mode = "type"

    def loop(times=3):
        """What _loop_evdev does each pass, for the helper's one 'keyboard'."""
        for _ in range(times):
            helper.run_once(timeout=0.01)
            for dev in app._devices_with_input([client], 0.01):
                try:
                    events = list(dev.read())
                except BlockingIOError:
                    continue
                for event in events:
                    if app._is_own_injected_event(dev, event):
                        continue
                    track_modifier(held, event)
                    app._handle_key_event(event, F8, F9, None, held,
                                          [client], Cfg, None)

    yield helper, kb, client, loop, calls
    client.close()
    helper.listener.close()


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
    assert app._grabbed_devices == []


def test_shift_with_the_dictation_key_starts_an_edit_and_hides_shift_too(rig, monkeypatch):
    helper, kb, client, loop, calls = rig
    monkeypatch.setattr(app, "_voice_editing", True)
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
    monkeypatch.setattr(app, "cancel_recording", lambda *a, **k: cancelled.append(a[-1]))
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
    for _ in range(3):
        for dev in app._devices_with_input([client], 0.01):
            try:
                events = list(dev.read())
            except BlockingIOError:
                continue
            for event in events:
                if not app._is_own_injected_event(dev, event):
                    app._handle_key_event(event, F8, F9, None, set(),
                                          [client], type("C", (), {"beeps": True, "notify": True,
                                                                    "smart_quotes": True, "language": "",
                                                                    "auto_space": True, "auto_period": True,
                                                                    "injection_mode": "type"}), None)
    assert calls == [("start", False), ("stop",)]
    assert helper.hide is None           # and the keyboards were given back


def test_events_taken_in_early_are_read_without_waiting(rig):
    helper, kb, client, loop, calls = rig
    kb.press(F8)
    helper.run_once(timeout=0.01)
    client.active_keys()                 # takes the DOWN in, as _grab_keyboards_hiding does
    assert client.needs_service()
    assert app._devices_with_input([client], 5.0) == [client]


def test_no_helper_means_none(tmp_path):
    assert connect_helper(str(tmp_path / "nothing.sock")) is None


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
