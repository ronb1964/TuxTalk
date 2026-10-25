"""Backend selection is keyed on FLATPAK_ID: portals inside a Flatpak,
evdev and TuxTalk's own virtual keyboard everywhere else."""
from tuxtalk.output_backends import (
    get_output_backend, VirtualKeyboardOutputBackend, LibeiOutputBackend,
)
from tuxtalk.input_backends import (
    get_input_backend, EvdevInputBackend, PortalInputBackend,
)
from tuxtalk import recording_state as rs


def test_output_backend_is_the_virtual_keyboard_without_flatpak():
    assert isinstance(get_output_backend(flatpak_id=""), VirtualKeyboardOutputBackend)


def test_output_backend_is_libei_in_flatpak():
    assert isinstance(
        get_output_backend(flatpak_id="io.github.ronb1964.TalkType"),
        LibeiOutputBackend,
    )


def test_input_backend_is_evdev_without_flatpak():
    assert isinstance(get_input_backend(flatpak_id=""), EvdevInputBackend)


def test_input_backend_is_portal_in_flatpak():
    assert isinstance(
        get_input_backend(flatpak_id="io.github.ronb1964.TalkType"),
        PortalInputBackend,
    )


class _Params:
    """Stands in for a GLib.Variant portal signal payload:
    (session_handle, shortcut_id, timestamp, options)."""

    def __init__(self, shortcut_id):
        self._shortcut_id = shortcut_id

    def unpack(self):
        return ("/session/handle", self._shortcut_id, 0, {})


class _Cfg:
    def __init__(self, mode="hold"):
        self.mode = mode


def test_portal_hold_shortcut_maps_press_and_release():
    """dictate_hold = push-to-talk: press starts, release stops (like Backend A F8)."""
    from tuxtalk import app
    b = PortalInputBackend(_Cfg())
    app._cmd_start_recording.clear()
    app._cmd_stop_recording.clear()
    b._on_activated(None, None, None, None, None, _Params("dictate_hold"))
    assert app._cmd_start_recording.is_set()
    b._on_deactivated(None, None, None, None, None, _Params("dictate_hold"))
    assert app._cmd_stop_recording.is_set()


def test_portal_toggle_shortcut_flips_on_press_and_ignores_release():
    """dictate_toggle = tap on/off (like Backend A F9): each press flips, release ignored."""
    from tuxtalk import app
    b = PortalInputBackend(_Cfg())
    app._cmd_start_recording.clear()
    app._cmd_stop_recording.clear()
    rs.state.is_recording = False
    b._on_activated(None, None, None, None, None, _Params("dictate_toggle"))  # start
    assert app._cmd_start_recording.is_set()
    app._cmd_start_recording.clear()
    rs.state.is_recording = True            # the service loop started it
    b._on_deactivated(None, None, None, None, None, _Params("dictate_toggle"))  # ignored
    assert not app._cmd_stop_recording.is_set()
    b._on_activated(None, None, None, None, None, _Params("dictate_toggle"))  # stop
    assert app._cmd_stop_recording.is_set()
    app._cmd_stop_recording.clear()
    rs.state.is_recording = False


def test_portal_toggle_starts_again_after_a_recording_ended_on_its_own():
    """Auto-stop (or Esc) ends a toggle recording without the key. The next
    press must start a new one, not send a stop for a recording that is gone."""
    from tuxtalk import app
    b = PortalInputBackend(_Cfg())
    app._cmd_start_recording.clear()
    app._cmd_stop_recording.clear()
    rs.state.is_recording = False           # ended by auto-stop
    b._on_activated(None, None, None, None, None, _Params("dictate_toggle"))
    assert app._cmd_start_recording.is_set() and not app._cmd_stop_recording.is_set()
    app._cmd_start_recording.clear()


def test_only_the_toggle_shortcut_start_can_become_hands_free():
    """A double-tap arms auto-stop only on a recording the toggle SHORTCUT
    started; the hold shortcut's start is a plain start."""
    from tuxtalk import app
    b = PortalInputBackend(_Cfg())
    rs.state.is_recording = False
    app._cmd_start_from_toggle_key.clear()
    b._on_activated(None, None, None, None, None, _Params("dictate_hold"))
    assert not app._cmd_start_from_toggle_key.is_set()
    b._on_activated(None, None, None, None, None, _Params("dictate_toggle"))
    assert app._cmd_start_from_toggle_key.is_set()
    app._cmd_start_from_toggle_key.clear()
    app._cmd_start_recording.clear()


def test_portal_hold_and_toggle_are_both_live():
    """Both shortcuts work regardless of cfg.mode — parity with Backend A, where
    F8 (hold) and F9 (toggle) are always active at the same time."""
    from tuxtalk import app
    b = PortalInputBackend(_Cfg("toggle"))  # mode must not gate either key
    app._cmd_start_recording.clear()
    b._on_activated(None, None, None, None, None, _Params("dictate_hold"))
    assert app._cmd_start_recording.is_set()


def test_portal_ignores_other_shortcut_ids():
    from tuxtalk import app
    b = PortalInputBackend(_Cfg())
    app._cmd_start_recording.clear()
    b._on_activated(None, None, None, None, None, _Params("something-else"))
    assert not app._cmd_start_recording.is_set()
