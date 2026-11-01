"""Tests for exclusive input-device grabbing.

TuxTalk takes an exclusive grab on the keyboard while recording, so the hotkey
doesn't leak into whatever app has focus. If a grab is ever taken and not
released, the user's keyboard stops working *system-wide* until TuxTalk exits.

These tests pin down the invariant that makes that impossible:

    no matter how recording ends, every grab is released.
"""

import time
import pytest
from evdev import ecodes

from conftest import KeyEvent
from tuxtalk import send_countdown
from tuxtalk import hotkeys
from tuxtalk import tray_link
from tuxtalk import keyboard_grab
from tuxtalk import recording_state as rs
from tuxtalk import keyboard_devices
from tuxtalk import recording
from tuxtalk import mic_stream
from tuxtalk import pipeline


class FakeDevice:
    """Stands in for an evdev InputDevice.

    Records grab/ungrab calls so tests can assert on the real end state
    rather than on a mock's call log.
    """

    def __init__(self, name="Fake Keyboard", caps=None, fail_grab=False, fail_ungrab=False):
        self.name = name
        self.is_grabbed = False
        self._caps = caps if caps is not None else {ecodes.EV_KEY: [ecodes.KEY_A, ecodes.KEY_Z]}
        self._fail_grab = fail_grab
        self._fail_ungrab = fail_ungrab

    def grab(self):
        if self._fail_grab:
            raise OSError("device busy")
        self.is_grabbed = True

    def ungrab(self):
        if self._fail_ungrab:
            raise OSError("device gone")
        self.is_grabbed = False

    def capabilities(self, absinfo=True):
        return self._caps

    def close(self):
        self.closed = True


def keyboard(name="Keyboard"):
    return FakeDevice(name, {ecodes.EV_KEY: [ecodes.KEY_A, ecodes.KEY_M, ecodes.KEY_Z]})


def mouse(name="Logitech MX Master"):
    return FakeDevice(name, {
        ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT],
        ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y],
    })


def touchpad(name="Synaptics Touchpad"):
    return FakeDevice(name, {
        ecodes.EV_KEY: [ecodes.BTN_TOUCH],
        ecodes.EV_ABS: [ecodes.ABS_X, ecodes.ABS_Y],
    })


def power_button(name="Power Button"):
    return FakeDevice(name, {ecodes.EV_KEY: [ecodes.KEY_POWER]})


@pytest.fixture(autouse=True)
def clean_grab_registry():
    """Every test starts and ends with no devices grabbed."""
    keyboard_grab.release_all()
    yield
    keyboard_grab.release_all()


@pytest.fixture
def quiet_recording(monkeypatch):
    """Silence the parts of start_recording that touch real hardware."""
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: None)
    monkeypatch.setattr(tray_link, "notify_recording_state", lambda *a, **k: None)
    monkeypatch.setattr(mic_stream, "input_samplerate", lambda idx, rate: 16000)
    monkeypatch.setattr(recording, "indicator", None)
    rs.state.is_recording = False
    yield
    rs.state.is_recording = False
    rs.state.current.stream = None


# --- releasing grabs -------------------------------------------------------

def test_release_all_grabs_releases_every_grabbed_device():
    devices = [keyboard("kb1"), keyboard("kb2"), keyboard("kb3")]
    keyboard_grab.grab_all_devices(devices)
    assert all(d.is_grabbed for d in devices)

    keyboard_grab.release_all()

    assert not any(d.is_grabbed for d in devices)


def test_release_all_grabs_is_idempotent():
    devices = [keyboard()]
    keyboard_grab.grab_all_devices(devices)

    keyboard_grab.release_all()
    keyboard_grab.release_all()  # must not raise

    assert not devices[0].is_grabbed


def test_release_continues_when_one_device_fails_to_ungrab():
    """A dead device must not strand the grabs on every other keyboard."""
    good_before = keyboard("before")
    broken = FakeDevice("unplugged", fail_ungrab=True)
    good_after = keyboard("after")
    keyboard_grab.grab_all_devices([good_before, broken, good_after])

    keyboard_grab.release_all()

    assert not good_before.is_grabbed
    assert not good_after.is_grabbed


def test_devices_that_fail_to_grab_are_not_tracked():
    ok = keyboard("ok")
    denied = FakeDevice("no permission", fail_grab=True)

    keyboard_grab.grab_all_devices([ok, denied])

    assert ok.is_grabbed
    assert denied not in keyboard_grab.grabbed_devices


# --- a failed start must not lock the keyboard -----------------------------

def test_failed_recording_start_releases_the_keyboard(monkeypatch, quiet_recording):
    """The system-wide keyboard lockup.

    If the microphone can't be opened after the keyboard is grabbed, the grab
    must still be released — otherwise the user's keyboard is dead everywhere
    with no way to recover.
    """
    def boom(*args, **kwargs):
        raise Exception("PortAudioError: Error querying device 99")

    monkeypatch.setattr(recording.sd, "InputStream", boom)

    devices = [keyboard("kb"), keyboard("laptop-internal")]
    keyboard_grab.grab_all_devices(devices)

    started = recording.start_recording(beeps_on=False, notify_on=False, input_device_idx=99)

    assert started is None
    assert not any(d.is_grabbed for d in devices), "keyboard left grabbed after mic failure"


def test_failed_recording_start_clears_the_recording_flag(monkeypatch, quiet_recording):
    """A stuck is_recording flag makes every later press take the wrong branch."""
    monkeypatch.setattr(recording.sd, "InputStream", lambda *a, **k: (_ for _ in ()).throw(Exception("no mic")))

    recording.start_recording(beeps_on=False, notify_on=False, input_device_idx=0)

    assert rs.state.is_recording is False


def test_failed_recording_start_tells_the_user(monkeypatch, quiet_recording):
    """A silent failure leaves the user no way to recover, so they must be told."""
    monkeypatch.setattr(recording.sd, "InputStream", lambda *a, **k: (_ for _ in ()).throw(Exception("no mic")))
    seen = []
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, msg: seen.append((title, msg)))

    recording.start_recording(beeps_on=False, notify_on=True, input_device_idx=0)

    assert seen, "no notification shown when recording failed to start"
    assert "microphone" in " ".join(seen[0]).lower()


def test_the_dictation_stream_leaves_other_streams_their_own_settings(monkeypatch,
                                                                      quiet_recording):
    """The microphone's rate and one channel go to its own stream, not into
    sounddevice's defaults for every stream the process opens after it."""
    opened = []

    class FakeStream:
        def __init__(self, **kwargs):
            opened.append(kwargs)

        def start(self):
            pass

    monkeypatch.setattr(mic_stream, "input_samplerate", lambda idx, rate: 44100)
    monkeypatch.setattr(recording.sd, "InputStream", FakeStream)
    defaults = (recording.sd.default.channels, recording.sd.default.samplerate)
    assert recording.start_recording(beeps_on=False, notify_on=False, input_device_idx=0)
    assert opened[0]["samplerate"] == 44100 and opened[0]["channels"] == 1
    assert (recording.sd.default.channels, recording.sd.default.samplerate) == defaults


def test_successful_recording_start_reports_success(monkeypatch, quiet_recording):
    class FakeStream:
        def start(self):
            pass

    monkeypatch.setattr(recording.sd, "InputStream", lambda *a, **k: FakeStream())

    started = recording.start_recording(beeps_on=False, notify_on=False, input_device_idx=0)

    assert started is rs.state.current
    assert rs.state.is_recording is True


# --- device filtering ------------------------------------------------------

def test_pointer_devices_are_not_treated_as_keyboards():
    """Grabbing mice freezes the pointer for the whole recording."""
    assert keyboard_devices.is_keyboard_device(mouse()) is False
    assert keyboard_devices.is_keyboard_device(touchpad()) is False


def test_real_keyboards_are_recognised():
    assert keyboard_devices.is_keyboard_device(keyboard()) is True


def test_power_button_is_not_treated_as_a_keyboard():
    """It reports EV_KEY but has no letter keys — grabbing it kills the power button."""
    assert keyboard_devices.is_keyboard_device(power_button()) is False


def test_grab_filtering_leaves_the_mouse_alone():
    kb = keyboard()
    mx = mouse()

    keyboard_grab.grab_all_devices([d for d in (kb, mx) if keyboard_devices.is_keyboard_device(d)])

    assert kb.is_grabbed
    assert not mx.is_grabbed, "mouse was grabbed — pointer would freeze while recording"


# --- key-event handling: every path must end with the keyboard released ------

class FakeConfig:
    mic = None
    beeps = False
    notify = False
    smart_quotes = False
    language = None
    auto_space = True
    auto_period = True
    injection_mode = "type"
    mode = "hold"


HOLD = ecodes.KEY_F8
TOGGLE = ecodes.KEY_F9


@pytest.fixture
def key_env(monkeypatch):
    """Drive _handle_key_event without touching audio hardware."""
    started, stopped = [], []

    def fake_start(beeps_on, notify_on, input_device_idx, from_toggle=False, edit_mode=False,
                   held_send=False):
        rs.state.is_recording = True
        started.append(True)
        return rs.state.current

    def fake_stop(*args, **kwargs):
        rs.state.is_recording = False
        stopped.append(True)

    monkeypatch.setattr(recording, "start_recording", fake_start)
    monkeypatch.setattr(pipeline, "stop_recording", fake_stop)
    monkeypatch.setattr(recording, "cancel_recording", lambda *a, **k: setattr(rs.state, "is_recording", False))
    rs.state.is_recording = False
    yield started, stopped
    rs.state.is_recording = False


def press_hold(devices):
    hotkeys._handle_key_event(KeyEvent(HOLD, 1), HOLD, TOGGLE,
                          None, set(), devices, FakeConfig(), 0)


def test_holding_the_hotkey_grabs_only_keyboards(key_env):
    kb, mx = keyboard(), mouse()
    press_hold([kb, mx])

    assert kb.is_grabbed
    assert not mx.is_grabbed, "pointer would freeze for the whole recording"


def test_releasing_the_hotkey_releases_the_keyboard(key_env):
    kb = keyboard()
    press_hold([kb])
    assert kb.is_grabbed

    hotkeys._handle_key_event(KeyEvent(HOLD, 0), HOLD, TOGGLE,
                          None, set(), [kb], FakeConfig(), 0)

    assert not kb.is_grabbed


def test_toggle_key_pressed_while_holding_releases_the_keyboard(key_env):
    """The fat-finger lockup: brushing F9 mid-dictation must not strand the grab."""
    kb = keyboard()
    press_hold([kb])
    assert kb.is_grabbed

    hotkeys._handle_key_event(KeyEvent(TOGGLE, 1), HOLD, TOGGLE,
                          None, set(), [kb], FakeConfig(), 0)

    assert not rs.state.is_recording
    assert not kb.is_grabbed, "keyboard left grabbed after toggle stopped recording"


def test_escape_releases_the_keyboard(key_env):
    kb = keyboard()
    press_hold([kb])

    hotkeys._handle_key_event(KeyEvent(ecodes.KEY_ESC, 1), HOLD, TOGGLE,
                          None, set(), [kb], FakeConfig(), 0)

    assert not kb.is_grabbed


def test_escape_gives_the_keyboard_back_before_the_microphone_stops(key_env, monkeypatch):
    """Stopping the stream can take seconds with a slow sound server; the
    keyboard mustn't stay grabbed through that wait."""
    kb = keyboard()
    press_hold([kb])
    grabbed_while_cancelling = []
    real_cancel = recording.cancel_recording

    def slow_cancel(*a, **k):
        grabbed_while_cancelling.append(kb.is_grabbed)
        return real_cancel(*a, **k)
    monkeypatch.setattr(recording, "cancel_recording", slow_cancel)
    hotkeys._handle_key_event(KeyEvent(ecodes.KEY_ESC, 1), HOLD, TOGGLE,
                          None, set(), [kb], FakeConfig(), 0)
    assert grabbed_while_cancelling == [False]


def test_microphone_failure_during_hold_leaves_nothing_grabbed(monkeypatch, quiet_recording):
    """End to end: real start_recording, failing mic, keyboard must come back."""
    monkeypatch.setattr(recording.sd, "InputStream",
                        lambda *a, **k: (_ for _ in ()).throw(Exception("no mic")))
    kb = keyboard()

    press_hold([kb])

    assert not kb.is_grabbed, "SYSTEM-WIDE KEYBOARD LOCKUP"
    assert rs.state.is_recording is False


# --- stale microphone index -------------------------------------------------

def test_stale_microphone_index_is_re_resolved_and_retried(monkeypatch, quiet_recording):
    """The mic is stored as a device *number*, resolved once at startup. Unplug a
    USB device and the numbers shift, so the stored one points at nothing.
    Rather than failing, re-resolve it by name and try once more.
    """
    class FakeStream:
        def start(self):
            pass

    tried = []

    def fake_input_stream(*args, **kwargs):
        device = kwargs.get("device")
        tried.append(device)
        if device == 99:
            raise Exception("PortAudioError: Error querying device 99")
        return FakeStream()

    monkeypatch.setattr(recording.sd, "InputStream", fake_input_stream)
    monkeypatch.setattr(recording, "pick_input_device", lambda mic: 3)
    monkeypatch.setattr(recording, "load_config", lambda: FakeConfig())

    kb = keyboard()
    keyboard_grab.grab_all_devices([kb])

    started = recording.start_recording(beeps_on=False, notify_on=False, input_device_idx=99)

    assert started is rs.state.current
    assert tried == [99, 3], "stale index was not re-resolved"
    assert rs.state.is_recording is True


def test_keyboard_is_released_when_even_the_re_resolved_mic_fails(monkeypatch, quiet_recording):
    monkeypatch.setattr(recording.sd, "InputStream",
                        lambda *a, **k: (_ for _ in ()).throw(Exception("no mic anywhere")))
    monkeypatch.setattr(recording, "pick_input_device", lambda mic: 3)
    monkeypatch.setattr(recording, "load_config", lambda: FakeConfig())

    kb = keyboard()
    keyboard_grab.grab_all_devices([kb])

    started = recording.start_recording(beeps_on=False, notify_on=False, input_device_idx=99)

    assert started is None
    assert not kb.is_grabbed


# --- safety net -------------------------------------------------------------

def test_grabs_held_while_not_recording_are_released():
    """Backstop for any future path that forgets to release.

    Holding an exclusive grab while not recording has no legitimate meaning, so
    the event loop treats it as a bug and recovers instead of leaving the user
    with a dead keyboard.
    """
    kb = keyboard()
    keyboard_grab.grab_all_devices([kb])
    rs.state.is_recording = False

    recovered = keyboard_grab.release_if_not_recording()

    assert recovered is True
    assert not kb.is_grabbed


def test_grabs_held_while_recording_are_left_alone():
    kb = keyboard()
    keyboard_grab.grab_all_devices([kb])
    rs.state.is_recording = True
    try:
        recovered = keyboard_grab.release_if_not_recording()
    finally:
        rs.state.is_recording = False

    assert recovered is False
    assert kb.is_grabbed, "released the keyboard mid-recording"


def test_keyboard_declaring_the_pointer_event_type_is_still_a_keyboard():
    """A Logitech K800, modelled from its real capabilities.

    Unifying receivers advertise a superset HID descriptor, so this keyboard
    declares the EV_REL *type* while exposing no actual REL_X axis. Rejecting
    on the event type alone would throw out a real keyboard and let the
    hotkey leak into whatever app has focus.
    """
    k800 = FakeDevice("Logitech K800", {
        ecodes.EV_KEY: sorted(keyboard_devices.LETTER_KEYS) + [ecodes.KEY_F8],
        ecodes.EV_REL: [],
        ecodes.EV_ABS: [],
    })

    assert keyboard_devices.is_keyboard_device(k800) is True


def test_unifying_mouse_advertising_letter_keys_is_not_grabbed():
    """Logitech MX Master / MX Ergo / MX Vertical mice.

    The same superset descriptor makes these mice claim all 26 letter keys and
    all 12 F-keys. What actually distinguishes them is real pointer motion plus
    mouse buttons. Grabbing them freezes the pointer for the whole recording.
    """
    mx = FakeDevice("Logitech MX Master", {
        ecodes.EV_KEY: sorted(keyboard_devices.LETTER_KEYS) + [ecodes.KEY_F8, ecodes.BTN_LEFT, ecodes.BTN_RIGHT],
        ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y],
    })

    assert keyboard_devices.is_keyboard_device(mx) is False


def test_the_apps_own_virtual_keyboard_is_not_grabbed():
    """TuxTalk types on it: grabbed while recording (Type at pause), what it
    types would never reach the app."""
    from tuxtalk import virtual_keyboard
    ours = FakeDevice(virtual_keyboard.NAME, virtual_keyboard._capabilities())

    assert keyboard_devices.is_keyboard_device(ours) is False


def _kanata():
    """kanata's virtual keyboard, from its source (src/oskbd/linux.rs): every
    key code below 560 and mouse motion, so mouse keys pass through too."""
    return FakeDevice("kanata", {ecodes.EV_KEY: list(range(560)),
                                 ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL]})


def test_a_key_remappers_virtual_keyboard_is_read(monkeypatch):
    """kanata takes the real keyboards and passes every key on through its
    own, shaped like a mouse too: skipped, TuxTalk heard no key at all with
    it running (issue #23)."""
    monkeypatch.setattr(keyboard_devices, "is_uinput_device", lambda path: True)
    assert keyboard_devices.is_keyboard_device(_kanata()) is True
    assert keyboard_devices.is_remapper_keyboard(_kanata()) is True


def test_a_real_keyboard_and_mouse_in_one_is_still_skipped(monkeypatch):
    """The same shape from hardware is a Logitech receiver's mouse."""
    monkeypatch.setattr(keyboard_devices, "is_uinput_device", lambda path: False)
    assert keyboard_devices.is_keyboard_device(_kanata()) is False


def test_a_plain_keyboard_is_no_remapper_keyboard(monkeypatch):
    monkeypatch.setattr(keyboard_devices, "is_uinput_device", lambda path: True)
    assert keyboard_devices.is_remapper_keyboard(keyboard("keyd virtual keyboard")) is False


def test_the_apps_own_keyboard_is_skipped_though_a_program_made_it(monkeypatch):
    from tuxtalk import virtual_keyboard
    monkeypatch.setattr(keyboard_devices, "is_uinput_device", lambda path: True)
    ours = FakeDevice(virtual_keyboard.NAME, virtual_keyboard._capabilities())
    assert keyboard_devices.is_keyboard_device(ours) is False


class _HeldKeyboard(FakeDevice):
    """A keyboard holding *held* (F8), recording what's written into it."""

    def __init__(self, name, caps, path, held=(ecodes.KEY_F8,)):
        super().__init__(name, caps)
        self.path, self.written, self.held = path, [], list(held)

    def active_keys(self):
        return self.held

    def write(self, etype, code, value):
        self.written.append((code, value))

    def syn(self):
        pass


def test_a_key_remappers_keyboard_is_never_grabbed_or_written_to(monkeypatch):
    """Held during a recording, kanata's keyboard would freeze the mouse
    and swallow what TuxTalk types meanwhile (Type at pause, typing while
    the key is held): kanata passes TuxTalk's own typing through it too."""
    monkeypatch.setattr(keyboard_devices, "is_uinput_device",
                        lambda path: path == "/dev/input/event18")
    real = _HeldKeyboard("USB Keyboard", keyboard()._caps, "/dev/input/event3")
    kanata = _HeldKeyboard("kanata", _kanata()._caps, "/dev/input/event18")
    try:
        keyboard_grab.grab_keyboards_hiding([real, kanata], {ecodes.KEY_F8})
        assert real.is_grabbed and real.written
        assert not kanata.is_grabbed and kanata.written == []
    finally:
        keyboard_grab.release_all()
        keyboard_grab._injected_pending.clear()


@pytest.mark.parametrize("real_keyboard", [False, True])
def test_a_right_ctrl_held_through_a_remapper_is_never_called_hidden(monkeypatch, real_keyboard):
    """kanata's keyboard isn't grabbed, so the desktop sees its Right Ctrl
    down: typing must wait for it, or the dictation turns into Ctrl
    shortcuts (Ctrl+W closes the tab). Also with another keyboard grabbed."""
    from tuxtalk import modifier_hotkeys
    monkeypatch.setattr(keyboard_devices, "is_uinput_device",
                        lambda path: path == "/dev/input/event18")
    kanata = _HeldKeyboard("kanata", _kanata()._caps, "/dev/input/event18",
                           held=[ecodes.KEY_RIGHTCTRL])
    real = _HeldKeyboard("USB Keyboard", keyboard()._caps, "/dev/input/event3", held=[])
    devices = [real, kanata] if real_keyboard else [kanata]
    try:
        keyboard_grab.grab_keyboards_hiding(devices, {ecodes.KEY_RIGHTCTRL})
        assert real.is_grabbed is real_keyboard
        assert ecodes.KEY_RIGHTCTRL not in modifier_hotkeys._hidden_now()
    finally:
        keyboard_grab.release_all()
        keyboard_grab._injected_pending.clear()


@pytest.mark.parametrize("node,made", [
    ("/sys/devices/virtual/input/input42", True),               # uinput: kanata
    ("/sys/devices/virtual/misc/uhid/0005:046D:B023.0001/input/input23", False),  # BLE
    ("/sys/devices/pci0000:00/0000:00:14.0/usb1/1-2/input/input7", False)])
def test_a_program_made_device_is_told_by_where_sysfs_keeps_it(monkeypatch, node, made):
    asked = []
    monkeypatch.setattr(keyboard_devices.os.path, "realpath",
                        lambda path: asked.append(path) or node)
    assert keyboard_devices.is_uinput_device("/dev/input/event18") is made
    assert asked == ["/sys/class/input/event18/device"]


# --- the Voice Commands hotkey is a grab path too ---------------------------

def _press_voice_combo(devices, monkeypatch, dbus=None):
    """Press the Ctrl+Alt+V style combo (no modifiers required, for brevity)."""
    monkeypatch.setattr(tray_link, "show_voice_commands", dbus or (lambda: None))
    hotkeys._handle_key_event(KeyEvent(ecodes.KEY_V, 1), HOLD, TOGGLE,
                          (set(), ecodes.KEY_V), set(), devices, FakeConfig(), 0)


def test_voice_commands_hotkey_does_not_grab_the_mouse(key_env, monkeypatch):
    """Like the recording path, this branch grabs keyboards only. The grab is
    held across the call to the tray, so that is when a grabbed pointer would
    freeze — check the state *during* the call, not after it."""
    kb, mx = keyboard(), mouse()
    during = {}

    _press_voice_combo([kb, mx], monkeypatch,
                       dbus=lambda: during.update(mouse=mx.is_grabbed,
                                                  keyboard=kb.is_grabbed))

    assert during["mouse"] is False, "pointer was frozen while the tray was called"


def test_voice_commands_grabs_are_visible_to_the_recovery_backstop(key_env,
                                                                   monkeypatch):
    """The grabs go in the shared registry, so keyboard_grab.release_all() and the
    stranded-grab backstop can see and recover them."""
    kb = keyboard()
    during = {}

    _press_voice_combo([kb], monkeypatch,
                       dbus=lambda: during.update(tracked=list(keyboard_grab.grabbed_devices)))

    assert kb in during["tracked"], "grab was invisible to the recovery paths"


def test_voice_commands_hotkey_leaves_nothing_grabbed(key_env, monkeypatch):
    kb, mx = keyboard(), mouse()
    _press_voice_combo([kb, mx], monkeypatch)

    assert not kb.is_grabbed
    assert not mx.is_grabbed
    assert keyboard_grab.grabbed_devices == []


def test_voice_commands_grabs_are_released_even_if_the_tray_call_fails(key_env,
                                                                       monkeypatch):
    """An exception between grab and ungrab must not strand the grabs.

    The exception itself is allowed to propagate — the event loop logs it
    with a traceback (app.py) rather than swallowing it silently. What must
    never happen is the keyboard staying grabbed on the way out.
    """
    kb = keyboard()

    def boom():
        raise RuntimeError("tray is not responding")

    with pytest.raises(RuntimeError):
        _press_voice_combo([kb], monkeypatch, dbus=boom)

    assert not kb.is_grabbed, "keyboard left grabbed after the tray call failed"
    assert keyboard_grab.grabbed_devices == []


# --- no unbounded blocking while the keyboard is grabbed --------------------

class RecordingBus:
    """Stands in for the session bus, recording each call to the tray."""

    def __init__(self):
        self.calls = []

    def call_blocking(self, name, path, interface, method, signature, args, **kwargs):
        self.calls.append((method, kwargs))


@pytest.fixture
def tray_proxy(monkeypatch):
    import dbus
    bus = RecordingBus()
    monkeypatch.setattr(dbus, "SessionBus", lambda: bus)
    return bus


def test_recording_state_notification_is_time_bounded(tray_proxy):
    """tray_link.notify_recording_state runs inside start_recording — after every
    keyboard has been exclusively grabbed. dbus-python's default reply
    timeout is 25s, and building the proxy adds a 25s introspect on top, so
    an unresponsive tray could freeze every keyboard system-wide for ~50
    seconds. Nothing on that path may block without a bound.
    """
    tray_link.notify_recording_state(True)

    assert tray_proxy.calls, "no D-Bus call was made"
    _, kwargs = tray_proxy.calls[0]
    assert "timeout" in kwargs, "unbounded call while the keyboard is grabbed"
    assert kwargs["timeout"] <= 5


def test_voice_commands_notification_is_time_bounded(tray_proxy):
    """Same path, but this one also holds the grab across the call."""
    tray_link.show_voice_commands()

    assert tray_proxy.calls
    _, kwargs = tray_proxy.calls[0]
    assert "timeout" in kwargs
    assert kwargs["timeout"] <= 5


def test_hotkey_press_notification_is_time_bounded(tray_proxy):
    tray_link.notify_hotkey_pressed("hold")

    assert tray_proxy.calls
    _, kwargs = tray_proxy.calls[0]
    assert "timeout" in kwargs
    assert kwargs["timeout"] <= 5


# --- a device dying mid-hold must not strand the grab -----------------------

def test_dropping_a_device_mid_hold_ends_the_recording(key_env):
    """The hold hotkey's key-up can only arrive from the device it was
    pressed on. If that device dies — a Unifying receiver blip, a USB reset,
    a resume from suspend — the release event is unreachable and
    state.is_recording stays True forever.

    That is not a cosmetic leak: keyboard_grab.release_if_not_recording() is gated
    on `not is_recording`, so the backstop is disabled and every OTHER
    keyboard stays exclusively grabbed indefinitely. The auto-timeout is
    gated the same way, so the process never exits either.
    """
    started, stopped = key_env
    kb1, kb2 = keyboard("dies"), keyboard("survives")
    devices = [kb1, kb2]
    press_hold(devices)
    assert rs.state.is_recording

    hotkeys._drop_dead_devices([kb1], devices, FakeConfig())

    assert rs.state.is_recording is False, "recording could never be ended"
    assert stopped, "stop_recording was not called"
    assert kb1 not in devices
    assert kb2 in devices


def test_dropping_a_device_releases_every_other_keyboard(key_env):
    kb1, kb2 = keyboard("dies"), keyboard("survives")
    devices = [kb1, kb2]
    press_hold(devices)
    assert kb2.is_grabbed

    hotkeys._drop_dead_devices([kb1], devices, FakeConfig())

    assert not kb2.is_grabbed, "other keyboards left grabbed — user cannot type"
    assert keyboard_grab.grabbed_devices == []


def test_dropping_a_device_while_idle_just_removes_it(key_env):
    """No recording in progress: drop the handle and carry on quietly."""
    started, stopped = key_env
    kb1, kb2 = keyboard("dies"), keyboard("survives")
    devices = [kb1, kb2]

    hotkeys._drop_dead_devices([kb1], devices, FakeConfig())

    assert devices == [kb2]
    assert not stopped, "stopped a recording that was never running"


class ToggleModeConfig(FakeConfig):
    mode = "toggle"


def test_dropping_a_device_mid_hold_ends_the_recording_in_toggle_mode(key_env):
    """The hold key records whatever the mode setting says (old configs still
    carry mode = "toggle"), so the recovery must not depend on it saying
    "hold", or the other keyboards stay grabbed."""
    started, stopped = key_env
    kb1, kb2 = keyboard("dies"), keyboard("survives")
    devices = [kb1, kb2]
    hotkeys._handle_key_event(KeyEvent(HOLD, 1), HOLD, TOGGLE,
                          None, set(), devices, ToggleModeConfig(), 0)
    assert rs.state.is_recording and kb2.is_grabbed

    hotkeys._drop_dead_devices([kb1], devices, ToggleModeConfig())

    assert rs.state.is_recording is False
    assert stopped
    assert not kb2.is_grabbed, "other keyboards left grabbed — user cannot type"


def test_a_toggle_recording_survives_an_unrelated_keyboard_dropping_out(key_env):
    """A tap-to-toggle recording holds no grab, and any keyboard can stop it,
    so a keyboard blip must not end someone's hands-free dictation."""
    started, stopped = key_env
    kb1, kb2 = keyboard("dies"), keyboard("survives")
    devices = [kb1, kb2]
    hotkeys._handle_key_event(KeyEvent(TOGGLE, 1), HOLD, TOGGLE,
                          None, set(), devices, FakeConfig(), 0)
    assert rs.state.is_recording

    hotkeys._drop_dead_devices([kb1], devices, FakeConfig())

    assert rs.state.is_recording is True
    assert not stopped
    assert devices == [kb2]


# --- devices that come back must be picked up again -------------------------

def test_a_device_that_returns_is_monitored_again(monkeypatch):
    """A wireless receiver blip or a USB reset drops the keyboard from the
    poll list, so devices are enumerated again; otherwise the hotkey silently
    stops working until TuxTalk is restarted."""
    kb = keyboard("reconnected")
    monkeypatch.setattr(hotkeys, "list_devices", lambda: ["/dev/input/event3"])
    monkeypatch.setattr(hotkeys, "InputDevice", lambda p: kb)

    devices = []
    added = hotkeys._rediscover_devices(devices)

    assert added == 1
    assert kb in devices


def test_rediscovery_does_not_duplicate_devices_already_watched(monkeypatch):
    existing = keyboard("already here")
    existing.path = "/dev/input/event3"
    monkeypatch.setattr(hotkeys, "list_devices", lambda: ["/dev/input/event3"])
    monkeypatch.setattr(hotkeys, "InputDevice", lambda p: keyboard("duplicate"))

    devices = [existing]
    added = hotkeys._rediscover_devices(devices)

    assert added == 0
    assert devices == [existing]


def test_rediscovery_ignores_non_keyboards(monkeypatch):
    mx = mouse()
    mx.path = "/dev/input/event9"
    monkeypatch.setattr(hotkeys, "list_devices", lambda: ["/dev/input/event9"])
    monkeypatch.setattr(hotkeys, "InputDevice", lambda p: mx)

    devices = []

    assert hotkeys._rediscover_devices(devices) == 0
    assert devices == []
    assert getattr(mx, "closed", False), "a rejected device was left open"


def test_a_keyboard_unplugged_while_the_nodes_are_listed_keeps_the_hotkeys_going(monkeypatch):
    """evdev's list_devices() checks a node exists, then stats it: an unplug
    in between raises there, inside the hotkey loop. That scan finds nothing;
    the next one tries again."""
    def unplugged_mid_listing():
        raise FileNotFoundError("/dev/input/event7")
    monkeypatch.setattr(hotkeys, "list_devices", unplugged_mid_listing)
    devices = []
    assert hotkeys._rediscover_devices(devices) == 0
    assert devices == []


def test_rediscovery_survives_an_unreadable_device(monkeypatch):
    """Permission denied on one node must not stop the others being found."""
    good = keyboard("good")
    good.path = "/dev/input/event4"

    def make(path):
        if path == "/dev/input/event3":
            raise OSError("permission denied")
        return good

    monkeypatch.setattr(hotkeys, "list_devices", lambda: ["/dev/input/event3", "/dev/input/event4"])
    monkeypatch.setattr(hotkeys, "InputDevice", make)

    devices = []

    assert hotkeys._rediscover_devices(devices) == 1
    assert good in devices


def test_a_device_whose_name_isnt_utf8_doesnt_end_the_hotkey_loop(monkeypatch):
    """evdev decodes a device's name strictly: a Bluetooth device with a
    Latin-1 name raised out of the rescan, which ended the hotkeys."""
    good = keyboard("good")
    good.path = "/dev/input/event4"

    def make(path):
        if path == "/dev/input/event3":
            raise UnicodeDecodeError("utf-8", b"Clavier \xe9", 8, 9, "invalid byte")
        return good

    monkeypatch.setattr(hotkeys, "list_devices", lambda: ["/dev/input/event3", "/dev/input/event4"])
    monkeypatch.setattr(hotkeys, "InputDevice", make)
    devices = []
    assert hotkeys._rediscover_devices(devices) == 1
    assert devices == [good]


def test_the_voice_commands_combo_mid_hold_keeps_the_recordings_grab(key_env, monkeypatch):
    """The combo's brief grab must not end by releasing the hold recording's
    grab too, or the rest of that dictation's keys reach the app."""
    monkeypatch.setattr(tray_link, "show_voice_commands", lambda: None)
    kb = keyboard()
    press_hold([kb])
    assert kb.is_grabbed

    combo = (set(), ecodes.KEY_H)
    hotkeys._handle_key_event(KeyEvent(ecodes.KEY_H, 1), HOLD, TOGGLE,
                          combo, set(), [kb], FakeConfig(), 0)

    assert rs.state.is_recording
    assert kb.is_grabbed, "the hold recording lost its keyboard grab"


def test_a_toggle_tap_during_a_send_countdown_only_holds_it(key_env, monkeypatch):
    """Tap F9 while "Send each dictation automatically" counts down: the send
    is held and no recording starts (Ron, 2026-10-18)."""
    started, _ = key_env
    held = []
    monkeypatch.setattr(send_countdown, "hold", lambda announce: held.append(announce) or True)
    hotkeys._handle_key_event(KeyEvent(TOGGLE, 1), HOLD, TOGGLE,
                          None, set(), [keyboard()], FakeConfig(), 0)
    assert held == [True]
    assert started == []


def test_a_toggle_tap_with_nothing_counting_down_starts_recording(key_env, monkeypatch):
    started, _ = key_env
    monkeypatch.setattr(send_countdown, "hold", lambda announce: False)
    hotkeys._handle_key_event(KeyEvent(TOGGLE, 1), HOLD, TOGGLE,
                          None, set(), [keyboard()], FakeConfig(), 0)
    assert len(started) == 1


def test_a_microphone_that_never_stops_cannot_freeze_the_service(monkeypatch):
    """Ron, 2026-10-18: the stream vanished from PipeWire and stop() waited
    forever, leaving the indicator up and every hotkey dead."""
    import threading as _t
    release = _t.Event()

    class StuckStream:
        def stop(self, ignore_errors=True):
            release.wait(5)

        def close(self, ignore_errors=True):
            pass

    monkeypatch.setattr(mic_stream, "STOP_TIMEOUT_S", 0.1)
    monkeypatch.setattr(mic_stream, "_closing", set())
    stream = StuckStream()
    rs.state.current.stream = stream
    started = time.monotonic()
    recording.stop_stream_safely(rs.state.current)
    assert time.monotonic() - started < 1.0
    assert rs.state.current.stream is None
    assert mic_stream._closing == {stream}
    release.set()


def test_a_normal_stop_closes_the_stream(monkeypatch):
    calls = []

    class Stream:
        def stop(self, ignore_errors=True):
            calls.append("stop")

        def close(self, ignore_errors=True):
            calls.append("close")

    monkeypatch.setattr(mic_stream, "_closing", set())
    rs.state.current.stream = Stream()
    recording.stop_stream_safely(rs.state.current)
    assert calls == ["stop", "close"] and mic_stream._closing == set()


def test_falling_back_to_another_mic_says_so(monkeypatch, quiet_recording):
    """Ron, 2026-10-18: the USB headset stopped answering and TuxTalk quietly
    recorded from the motherboard input instead."""
    class FakeStream:
        def start(self):
            pass

    def fake_input_stream(*args, **kwargs):
        if kwargs.get("device") == 14:
            raise Exception("Unanticipated host error")
        return FakeStream()

    names = {14: "Sennheiser Profile Mono", 8: "HD-Audio Generic: ALC897 Analog"}
    notices = []
    monkeypatch.setattr(recording.sd, "InputStream", fake_input_stream)
    monkeypatch.setattr(recording.sd, "query_devices", lambda idx: {"name": names[idx]})
    monkeypatch.setattr(recording, "pick_input_device", lambda mic: 8)
    monkeypatch.setattr(recording, "load_config", lambda: FakeConfig())
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, text: notices.append(text))

    assert recording.start_recording(beeps_on=False, notify_on=False,
                                     input_device_idx=14) is not None
    assert len(notices) == 1
    assert notices[0].startswith("Sennheiser Profile Mono didn't respond, so TuxTalk is recording "
                                 "from HD-Audio Generic: ALC897 Analog instead.")
    keyboard_grab.release_all()


def test_reopening_the_same_mic_says_nothing(monkeypatch, quiet_recording):
    class FakeStream:
        def start(self):
            pass

    tries = []

    def fake_input_stream(*args, **kwargs):
        tries.append(kwargs.get("device"))
        if len(tries) == 1:
            raise Exception("busy for a moment")
        return FakeStream()

    notices = []
    monkeypatch.setattr(recording.sd, "InputStream", fake_input_stream)
    monkeypatch.setattr(recording, "pick_input_device", lambda mic: 14)
    monkeypatch.setattr(recording, "load_config", lambda: FakeConfig())
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, text: notices.append(text))

    assert recording.start_recording(beeps_on=False, notify_on=False,
                                     input_device_idx=14) is not None
    assert notices == []
    keyboard_grab.release_all()


@pytest.mark.parametrize("through_kanata", [False, True])
def test_only_a_compose_key_caught_down_on_a_real_keyboard_is_ours_to_end(monkeypatch,
                                                                          through_kanata):
    """Pop!_OS makes Right Ctrl Compose. Held on a real keyboard, the grab
    hides it and TuxTalk ends the compose its press began; through kanata it
    can't, since kanata would hand Compose pressed again back as the key."""
    from tuxtalk import modifier_hotkeys
    monkeypatch.setattr(keyboard_devices, "is_uinput_device",
                        lambda path: path == "/dev/input/event18")
    monkeypatch.setattr(modifier_hotkeys, "_compose_begun", ecodes.KEY_RIGHTCTRL)
    monkeypatch.setattr(modifier_hotkeys, "_compose_hidden", False)
    if through_kanata:
        board = _HeldKeyboard("kanata", _kanata()._caps, "/dev/input/event18",
                              held=[ecodes.KEY_RIGHTCTRL])
    else:
        board = _HeldKeyboard("USB Keyboard", keyboard()._caps, "/dev/input/event3",
                              held=[ecodes.KEY_RIGHTCTRL])
    try:
        keyboard_grab.grab_keyboards_hiding([board], {ecodes.KEY_RIGHTCTRL})
        assert modifier_hotkeys._compose_hidden is not through_kanata
    finally:
        keyboard_grab.release_all()
        keyboard_grab._injected_pending.clear()
