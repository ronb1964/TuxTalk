"""TuxTalk's own virtual keyboard (virtual_keyboard.py).

Every test here runs on the recording keyboard from conftest.py; a real one
would type into whatever window has the focus on the computer running them.
"""
import pytest

from tuxtalk import app, virtual_keyboard
from tuxtalk import keyboard_output
from tuxtalk import recording_state as rs


@pytest.fixture
def settled(keyboard):
    """The keyboard the service made at start, long taken on by the desktop."""
    virtual_keyboard.start()
    keyboard.clock.now = virtual_keyboard._ready_at
    return keyboard


def test_a_short_dictation_types_in_a_fraction_of_the_time_ydotool_took(settled):
    """ydotool needed about 0.6 s for 25 characters (24 ms a letter, VM
    measurement 2026-10-20); typing is the slowest step anyone sees."""
    started = settled.clock.now
    assert keyboard_output.type_text_raw("see you at noon tomorrow.") is True
    assert settled.clock.now - started < 0.15


def test_a_long_dictation_is_typed_to_the_end(keyboard):
    """ydotool's time limit cut long dictations off partway; nothing here may."""
    text = "word " * 400
    assert keyboard_output.type_text_raw(text) is True
    assert keyboard.typed_keys().count(57) == 400      # every space


def test_the_key_delay_setting_slows_every_key(settled, monkeypatch):
    monkeypatch.setattr(keyboard_output, "key_delay_ms", 20)
    started = settled.clock.now
    keyboard_output.type_text_raw("abcde")
    assert settled.clock.now - started == pytest.approx(10 * 0.020)


def test_keys_wait_until_the_desktop_has_taken_on_a_new_keyboard(keyboard, monkeypatch):
    """Keys sent the moment the keyboard appears are lost (the first letters
    of the dictation were missing in the VMs)."""
    seen_at = []
    write = keyboard.write
    monkeypatch.setattr(keyboard, "write", lambda *a: seen_at.append(keyboard.clock.now) or write(*a))
    virtual_keyboard.send([(30, 1), (30, 0)])
    assert seen_at[0] >= virtual_keyboard.SETTLE_S


def test_a_keyboard_that_fails_before_any_key_reports_nothing_sent(keyboard):
    keyboard.fail_after = 0
    assert keyboard_output.send_backspaces(5) is False


def test_a_keyboard_that_fails_partway_says_so(keyboard):
    """Undo must know some backspaces landed: its buffer no longer matches."""
    keyboard.fail_after = 3
    with pytest.raises(virtual_keyboard.StoppedPartway):
        keyboard_output.send_backspaces(5)


def test_a_keyboard_that_failed_is_made_again_next_time(keyboard):
    keyboard.fail_after = 1
    with pytest.raises(virtual_keyboard.StoppedPartway):
        virtual_keyboard.send([(42, 1), (30, 1), (30, 0), (42, 0)])
    assert keyboard.closed and virtual_keyboard._device is None
    keyboard.fail_after = None
    assert virtual_keyboard.send([(30, 1), (30, 0)]) is True


def test_keys_left_down_are_let_go(keyboard):
    """A Shift left down on any keyboard capitalises what the user types next."""
    virtual_keyboard.send([(42, 1), (30, 1), (30, 0)])
    assert keyboard.events[-1] == (42, 0)


def test_without_access_to_uinput_nothing_is_typed(monkeypatch):
    monkeypatch.setattr(virtual_keyboard, "_open", lambda: None)
    monkeypatch.setattr(keyboard_output, "which", lambda name: None)
    assert keyboard_output.type_text_raw("hello") is False


def test_an_undo_stopped_partway_forgets_the_buffer(monkeypatch):
    """Most of the text may be gone already, so keeping the buffer would make
    the next undo backspace that many characters again, into the user's own
    writing."""
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: None)

    def partway(count):
        raise virtual_keyboard.StoppedPartway("backspaces")

    monkeypatch.setattr(keyboard_output, "send_backspaces", partway)
    rs.state.last_inserted_text = "a long dictation that was being undone"
    try:
        assert app._handle_undo("undo last sentence", beeps_on=False, notify_on=False) is True
        assert rs.state.last_inserted_text == ""
    finally:
        rs.state.last_inserted_text = ""


# --- its shape ---------------------------------------------------------------

def test_the_installed_hotkey_helper_skips_it_too():
    """Helpers already on users' computers skip pointers, as they skipped
    ydotoold's device; ours must look like one to them."""
    from tuxtalk import hotkey_helper
    caps = virtual_keyboard._capabilities()
    from evdev import ecodes
    assert hotkey_helper.REL_X in caps[ecodes.EV_REL]
    assert hotkey_helper.BTN_LEFT in caps[ecodes.EV_KEY]


def test_it_has_every_key_a_layout_can_type_and_no_joystick_buttons():
    """A joystick button would make udev call it a game controller."""
    from evdev import ecodes
    keys = set(virtual_keyboard._capabilities()[ecodes.EV_KEY])
    assert set(range(1, 249)) <= keys
    assert not keys & {ecodes.BTN_TRIGGER, ecodes.BTN_SOUTH, ecodes.BTN_TRIGGER_HAPPY1}


def test_a_shortcut_cut_off_partway_reports_failure(keyboard):
    """Ctrl+V that broke after Ctrl went down: the paste didn't happen."""
    keyboard.fail_after = 1
    assert keyboard_output.send_keys([(29, 1), (47, 1), (47, 0), (29, 0)], what="paste") is False


def test_events_sent_before_a_failed_syn_count_as_landed(keyboard, monkeypatch):
    """The key went to the kernel; only the SYN after it failed."""
    def broken_syn():
        raise OSError(19, "No such device")
    monkeypatch.setattr(keyboard, "syn", broken_syn)
    with pytest.raises(virtual_keyboard.StoppedPartway):
        virtual_keyboard.send([(30, 1), (30, 0)])


def test_after_this_process_was_held_up_keys_never_go_out_in_a_burst(settled, monkeypatch):
    """Catching up on a held-up schedule sent dozens of keys at once, more
    than the kernel's buffer of about 64 holds: whole words lost."""
    sent_at = []
    write = settled.write

    def held_up_once(*a):
        sent_at.append(settled.clock.now)
        if len(sent_at) == 10:
            settled.clock.now += 0.2        # a stall of 200 ms, 100 key times
        write(*a)
    monkeypatch.setattr(settled, "write", held_up_once)
    virtual_keyboard.send([(30, 1), (30, 0)] * 50, delay_ms=2)
    gaps = [b - a for a, b in zip(sent_at, sent_at[1:])]
    assert sum(1 for g in gaps if g < 0.002 * 0.99) <= 1


# --- making the keyboard -------------------------------------------------------

def test_making_the_keyboard_waits_for_nothing(monkeypatch):
    """python-evdev reads a new device back, and where only the hotkey
    helper may read /dev/input it retried for two seconds first: the service
    and every "fix that word" started two seconds late. Runs python-evdev's
    own constructor, with a stand-in for its kernel calls."""
    import types
    from evdev import uinput as evdev_uinput
    real = evdev_uinput._uinput
    fake = types.SimpleNamespace(
        __file__=real.__file__, maxnamelen=real.maxnamelen,
        open=lambda devnode: 99, set_phys=lambda fd, phys: None, set_prop=lambda fd, p: None,
        enable=lambda fd, t, c: None, setup=lambda *a: None, create=lambda fd: None,
        get_sysname=lambda fd: "input-not-there", close=lambda fd: None)
    monkeypatch.setattr(evdev_uinput, "_uinput", fake)
    monkeypatch.setattr(evdev_uinput.UInput, "_verify", lambda self: None)
    waits = []
    monkeypatch.setattr(evdev_uinput.time, "sleep", waits.append)
    device = virtual_keyboard._keyboard_class()(virtual_keyboard._capabilities(),
                                                name=virtual_keyboard.NAME)
    assert waits == [] and device.device is None
    device.close()


# --- a keyboard that fails partway: what landed is never sent again ------------

@pytest.fixture
def breaks_once(keyboard, monkeypatch):
    """The keyboard fails once, after 6 key events (a USB hub reset, say)."""
    write = keyboard.write
    state = {"failed": False}

    def write_then_fail(*a):
        if len(keyboard.events) == 6 and not state["failed"]:
            state["failed"] = True
            raise OSError(19, "No such device")
        write(*a)
    monkeypatch.setattr(keyboard, "write", write_then_fail)
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a: None)
    return keyboard


def test_a_dictation_typed_partway_is_partial_and_not_typed_again(breaks_once, monkeypatch):
    """Claude Desktop: the typing fell back to typing it all again, so the
    first words came out twice."""
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "Claude")
    monkeypatch.setattr(app, "_determine_injection_method", lambda m: ("paste", "test"))
    monkeypatch.setattr(rs.state, "last_inserted_text", "earlier dictation")
    assert app._inject_text("hello world", "auto") == "partial"
    assert len(breaks_once.events) == 6
    assert rs.state.last_inserted_text == "", "undo would count on text that may not be there"


def test_a_paste_shortcut_cut_off_is_partial_and_not_typed_after(breaks_once, monkeypatch):
    """Ctrl+V may have pasted before the keyboard failed; typing the text
    then would put it in twice."""
    breaks_once.events.extend([(0, 0)] * 4)       # the failure comes inside Ctrl+V
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "org.kde.kwrite")
    monkeypatch.setattr(keyboard_output, "which", lambda name: f"/usr/bin/{name}")

    class Copied:
        stdin = type("In", (), {"write": lambda self, d: None, "close": lambda self: None})()
        stderr = None

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0
    monkeypatch.setattr(keyboard_output.subprocess, "Popen", lambda *a, **k: Copied())
    assert app._inject_text("hello world", "paste") == "partial"
    keys = {code for code, _value in breaks_once.events[4:]}
    assert keys == {29, 47}, "typed the text after a paste that may have landed"
    assert breaks_once.events[-2:] == [(47, 0), (29, 0)], "Ctrl and V left down"
