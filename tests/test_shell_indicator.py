"""The GNOME Shell recording indicator (shell_indicator): the extension's side
is a stand-in on a real session bus, so Show, Update and Hide go over D-Bus
as they do on GNOME, and the X11 window takes over when it doesn't answer."""
import logging
import threading

import numpy as np
import pytest

gi = pytest.importorskip("gi")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from tuxtalk import shell_indicator as si  # noqa: E402
from tuxtalk import live_text_window, recording  # noqa: E402
from tuxtalk.indicator_dsp import SpectrumProcessor  # noqa: E402
from tuxtalk.recording_indicator import RecordingIndicator  # noqa: E402
from tuxtalk.window_geometry import Rect  # noqa: E402

INTERFACE_XML = f"""
<node>
  <interface name="{si.INTERFACE}">
    <method name="Show"><arg type="a{{sv}}" direction="in" name="settings"/></method>
    <method name="SetRecording"><arg type="b" direction="in" name="recording"/></method>
    <method name="Update">
      <arg type="d" direction="in" name="level"/>
      <arg type="ad" direction="in" name="values"/>
    </method>
    <method name="Hide"/>
  </interface>
</node>"""


def _wait_for(condition, seconds=3.0):
    context = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + int(seconds * 1_000_000)
    timer = GLib.timeout_add(10, lambda: True)
    try:
        while not condition() and GLib.get_monotonic_time() < deadline:
            context.iteration(True)
        return bool(condition())
    finally:
        GLib.source_remove(timer)


@pytest.fixture
def bus():
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        pytest.skip("no session bus")


@pytest.fixture
def shell(bus):
    """Calls the extension's indicator received, as (method, arguments)."""

    class Calls(list):
        disconnected = False
        defer_show = False
        fail_hide = False
        pending = None

        def disconnect(self):
            bus.unregister_object(registration)
            self.disconnected = True

        def reconnect(self):
            nonlocal registration
            registration = bus.register_object(si.OBJECT_PATH, info, on_call, None, None)
            self.disconnected = False

    calls = Calls()

    def on_call(_bus, _sender, _path, _iface, method, params, invocation):
        calls.append((method, params.unpack()))
        if method == "Show" and calls.defer_show:
            calls.pending = invocation
        elif method == "Hide" and calls.fail_hide:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", "no hide")
        else:
            invocation.return_value(None)

    info = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML).interfaces[0]
    registration = bus.register_object(si.OBJECT_PATH, info, on_call, None, None)
    owned = []
    owner = Gio.bus_own_name_on_connection(
        bus, si.BUS_NAME, Gio.BusNameOwnerFlags.NONE, lambda *_: owned.append(True), None
    )
    assert _wait_for(lambda: owned)
    yield calls
    # Let a test's queued hide run, so its updates stop before the next test.
    _wait_for(lambda: False, seconds=0.1)
    Gio.bus_unown_name(owner)
    if not calls.disconnected:
        calls.disconnect()


def _methods(calls):
    return [method for method, _ in calls]


def test_other_desktops_draw_the_x11_indicator(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    window = si.create_recording_indicator(style="orb")
    assert isinstance(window, RecordingIndicator)
    window.destroy()


def test_gnome_without_a_session_bus_uses_x11(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")

    def unavailable(*_args):
        raise GLib.Error("session bus disconnected")

    monkeypatch.setattr(Gio, "bus_get_sync", unavailable)
    window = si.create_recording_indicator()
    assert isinstance(window, RecordingIndicator)
    window.destroy()


def test_with_the_extension_the_shell_draws_the_indicator(shell, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    assert isinstance(si.create_recording_indicator(style="orb"), si.ShellIndicator)


def test_showing_tells_the_shell_where_and_how_to_draw(shell):
    ind = si.ShellIndicator(
        position="Bottom-Center", size="large", custom_color="#ff0000", follow_window=True
    )
    ind.show_at_position()
    assert _wait_for(lambda: "Show" in _methods(shell))
    (settings,) = dict(shell)["Show"]
    assert settings == {
        "style": "orb",
        "rgb": [1.0, 0.0, 0.0],
        "scale": 1.4,
        "position": "bottom-center",
        "follow": True,
        "offset_x": 0,
        "offset_y": 0,
    }
    ind.hide_indicator()


def test_recording_starts_after_the_show(shell):
    ind = si.ShellIndicator()
    ind.show_at_position()
    ind.start_recording()
    assert _wait_for(lambda: "SetRecording" in _methods(shell))
    assert _methods(shell).index("Show") < _methods(shell).index("SetRecording")
    ind.hide_indicator()


def test_the_shell_gets_the_audio_level_while_showing(shell):
    ind = si.ShellIndicator(sensitivity=2.0)
    ind.show_at_position()
    ind.set_audio_level(0.25)
    assert _wait_for(lambda: ("Update", (0.5, [])) in shell)
    ind.hide_indicator()


def test_the_bars_follow_animation_response(shell):
    ind = si.ShellIndicator(style="bars", sensitivity=2.0)
    ind.show_at_position()
    ind.set_spectrum([0.2, 0.8])
    assert _wait_for(lambda: ("Update", (0.0, [0.4, 1.0])) in shell)
    ind.hide_indicator()


def test_live_text_anchors_by_the_shell_indicator(bus, monkeypatch):
    monkeypatch.setattr(si, "active_window_rect", lambda: Rect(100, 200, 800, 600))
    monkeypatch.setattr(si, "monitor_rect_at", lambda horizontal, vertical: Rect(0, 0, 1920, 1080))
    ind = si.ShellIndicator(position="bottom-center", follow_window=True)
    ind.show_at_position()
    assert live_text_window.LiveTextWindow(lambda: ind)._anchor() == (400, 500, 200, 180)
    ind.hide_indicator()


def test_live_text_maps_shell_bounds_to_xwayland(shell, bus, monkeypatch):
    monkeypatch.setattr(si, "main_monitor_rect", lambda: Rect(2560, 0, 2560, 1440))
    ind = si.ShellIndicator()
    ind.show_at_position()
    assert _wait_for(lambda: "Show" in _methods(shell))
    bus.emit_signal(
        None,
        si.OBJECT_PATH,
        si.INTERFACE,
        "GeometryChanged",
        GLib.Variant("((iiii)(iiii))", ((220, 120, 200, 180), (1280, 0, 1280, 720))),
    )
    assert _wait_for(lambda: (ind.get_position(), ind.get_size()) == ((440, 240), (400, 360)))
    ind.hide_indicator()


def test_no_updates_after_hiding(shell):
    ind = si.ShellIndicator()
    ind.show_at_position()
    assert _wait_for(lambda: "Update" in _methods(shell))
    ind.hide_indicator()
    assert _wait_for(lambda: "Hide" in _methods(shell))
    count = _methods(shell).count("Update")
    _wait_for(lambda: False, seconds=0.2)
    assert _methods(shell).count("Update") == count


def test_extension_disabling_mid_recording_shows_the_x11_indicator(shell, caplog):
    existing = set(Gtk.Window.list_toplevels())
    ind = si.ShellIndicator()
    ind.show_at_position()
    ind.start_recording()
    assert _wait_for(lambda: "Update" in _methods(shell))
    shell.disconnect()
    assert _wait_for(
        lambda: any(
            isinstance(window, RecordingIndicator)
            and window not in existing
            and window.get_visible()
            for window in Gtk.Window.list_toplevels()
        )
    )
    assert "indicator Update failed" in caplog.text
    assert "UnknownMethod" in caplog.text
    ind.hide_indicator()


def test_extension_enabled_after_startup_takes_over_the_next_dictation(shell, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    shell.disconnect()
    existing = set(Gtk.Window.list_toplevels())
    ind = si.create_recording_indicator(style="bars")
    ind.show_at_position()

    def windows():
        return set(Gtk.Window.list_toplevels()) - existing

    assert _wait_for(lambda: any(window.get_visible() for window in windows()))
    (fallback,) = windows()
    ind.start_recording()
    assert _wait_for(lambda: fallback.is_recording)
    ind.set_audio_level(0.75)
    ind.set_spectrum([0.4, 0.7])
    ind.set_waveform([0.1, 0.2])
    assert (fallback.audio_level, fallback.spectrum, fallback.waveform) == (
        0.75,
        [0.4, 0.7],
        [0.1, 0.2],
    )
    assert (ind.get_position(), ind.get_size()) == (fallback.get_position(), fallback.get_size())
    ind.hide_indicator()
    assert _wait_for(lambda: not fallback.get_visible())
    ind.apply_settings("top-left", "large", 4, 8, style="radial")
    ind.show_at_position()
    assert _wait_for(fallback.get_visible)
    assert _wait_for(lambda: fallback.get_position() == (24, 28))
    assert (windows(), fallback.style) == ({fallback}, "radial")
    ind.hide_indicator()
    shell.reconnect()
    ind.show_at_position()
    assert _wait_for(lambda: "Show" in _methods(shell))
    assert _wait_for(lambda: not fallback.get_visible())
    ind.hide_indicator()


def test_a_late_show_error_cannot_switch_the_next_dictation_to_x11(shell, caplog):
    shell.defer_show = True
    ind = si.ShellIndicator()
    ind.show_at_position()
    assert _wait_for(lambda: shell.pending is not None)
    ind.hide_indicator()
    shell.defer_show = False
    ind.show_at_position()
    assert _wait_for(lambda: _methods(shell).count("Show") == 2)
    shell.pending.return_dbus_error("org.freedesktop.DBus.Error.Failed", "old dictation")
    ind.set_audio_level(0.4)
    assert _wait_for(lambda: ("Update", (0.4, [])) in shell)
    assert "Switching to X11" not in caplog.text
    ind.hide_indicator()


def test_key_release_during_x11_construction_hides_the_window(shell, monkeypatch):
    shell.disconnect()
    ind = si.ShellIndicator()
    built = threading.Event()
    released = threading.Event()
    windows = []

    def build(**settings):
        window = RecordingIndicator(**settings)
        windows.append(window)
        built.set()
        assert released.wait(timeout=3)
        return window

    def release():
        if built.wait(timeout=3):
            ind.hide_indicator()
            released.set()

    monkeypatch.setattr(si, "RecordingIndicator", build)
    key = threading.Thread(target=release)
    key.start()
    ind.show_at_position()
    assert _wait_for(lambda: released.is_set())
    key.join(timeout=3)
    # Drain both the Shell backend's idle callbacks and the GTK window's own show/hide.
    idle = []
    GLib.idle_add(lambda: idle.append(True), priority=GLib.PRIORITY_LOW)
    assert _wait_for(lambda: idle)
    assert not windows[0].get_visible()


def test_style_and_geometry_changes_wait_until_the_next_show(shell, monkeypatch):
    monkeypatch.setattr(si, "main_monitor_rect", lambda: Rect(0, 0, 1920, 1080))
    ind = si.ShellIndicator(style="bars", position="bottom-center")
    ind.show_at_position()
    assert _wait_for(lambda: "Show" in _methods(shell))
    ind.apply_settings("top-left", "large", 8, 12, style="radial", sensitivity=2)
    audio = (np.sin(np.linspace(0, 40, 512)) * 8000).astype(np.int16)
    recording.feed_indicator(ind, audio, SpectrumProcessor(bins=20))
    assert _wait_for(
        lambda: any(m == "Update" and len(args[1]) == 20 and max(args[1]) > 0 for m, args in shell)
    )
    assert (ind.get_position(), ind.get_size()) == ((860, 880), (200, 180))
    ind.hide_indicator()
    assert _wait_for(lambda: "Hide" in _methods(shell))
    ind.show_at_position()
    assert _wait_for(lambda: _methods(shell).count("Show") == 2)
    recording.feed_indicator(ind, audio, None)
    assert _wait_for(lambda: any(m == "Update" and len(args[1]) == 96 for m, args in shell))
    assert (ind.get_position(), ind.get_size()) == ((28, 32), (280, 252))
    assert [args[0]["style"] for method, args in shell if method == "Show"] == ["bars", "radial"]
    ind.hide_indicator()


def test_the_radial_style_sends_its_ring(shell):
    ind = si.ShellIndicator(style="radial")
    ind.show_at_position()
    ind.set_waveform(np.sin(np.linspace(0, 60, 2048)).astype(np.float32) * 0.3)
    assert _wait_for(lambda: any(m == "Update" and args[1] for m, args in shell))
    ring = next(args[1] for m, args in shell if m == "Update" and args[1])
    assert len(ring) == 96 and all(0.0 <= d <= 1.0 for d in ring)
    ind.hide_indicator()


def test_waveform_updates_reach_shell_after_a_quick_restart(shell):
    ind = si.ShellIndicator(style="waveform")
    ind.show_at_position()
    assert _wait_for(lambda: "Show" in _methods(shell))
    ind.hide_indicator()
    ind.show_at_position()
    ind.set_waveform([0.25, -0.5])
    assert _wait_for(lambda: ("Update", (0.0, [0.75, -1.0])) in shell)
    ind.hide_indicator()


def test_failure_to_show_x11_hides_the_window(shell, monkeypatch, caplog):
    shell.disconnect()
    windows = []
    show = RecordingIndicator.show_at_position

    def fail(window):
        windows.append(window)
        show(window)
        raise GLib.Error("lost X display")

    monkeypatch.setattr(RecordingIndicator, "show_at_position", fail)
    ind = si.ShellIndicator()
    ind.show_at_position()
    assert _wait_for(lambda: "lost X display" in caplog.text)
    idle = []
    GLib.idle_add(lambda: idle.append(True), priority=GLib.PRIORITY_LOW)
    assert _wait_for(lambda: idle)
    assert not windows[0].get_visible()
    ind.hide_indicator()


def test_repeated_show_removes_the_previous_timer(shell, monkeypatch):
    timers = []
    timeout_add = GLib.timeout_add

    def track_timer(interval, callback, *args):
        timer = timeout_add(interval, callback, *args)
        if interval == si.UPDATE_MS:
            timers.append(timer)
        return timer

    monkeypatch.setattr(GLib, "timeout_add", track_timer)
    ind = si.ShellIndicator()
    ind.show_at_position()
    assert _wait_for(lambda: "Update" in _methods(shell))
    timer = timers[0]
    ind.show_at_position()
    assert _wait_for(lambda: _methods(shell).count("Show") == 2)
    assert GLib.MainContext.default().find_source_by_id(timer) is None
    ind.hide_indicator()


def test_unavailable_shell_warns_once_then_logs_at_debug(shell, caplog):
    caplog.set_level(logging.DEBUG, logger=si.__name__)
    shell.disconnect()
    existing = set(Gtk.Window.list_toplevels())
    ind = si.ShellIndicator()
    ind.show_at_position()
    assert _wait_for(
        lambda: (
            ind.get_visible()
            and any("Switching to X11" in record.message for record in caplog.records)
        )
    )
    assert _wait_for(
        lambda: any(window.get_visible() for window in set(Gtk.Window.list_toplevels()) - existing)
    )
    (fallback,) = set(Gtk.Window.list_toplevels()) - existing
    ind.hide_indicator()
    assert _wait_for(lambda: not fallback.get_visible())
    ind.show_at_position()
    assert _wait_for(
        lambda: sum("Switching to X11" in record.message for record in caplog.records) == 2
    )
    assert [
        record.levelno for record in caplog.records if "Switching to X11" in record.message
    ] == [
        logging.WARNING,
        logging.DEBUG,
    ]
    ind.hide_indicator()


def test_x11_owns_the_radial_history_after_fallback(shell, monkeypatch):
    gains = []
    gain_class = si.RadialGain

    class CountedGain(gain_class):
        def __init__(self):
            super().__init__()
            self.blocks = []
            gains.append(self)

        def update(self, samples, now):
            self.blocks.append(list(samples))
            super().update(samples, now)

    monkeypatch.setattr(si, "RadialGain", CountedGain)
    ind = si.ShellIndicator(style="radial")
    ind.show_at_position()
    ind.set_waveform([0.1, 0.2])
    assert _wait_for(lambda: "Update" in _methods(shell))
    existing = set(Gtk.Window.list_toplevels())
    shell.disconnect()
    assert _wait_for(
        lambda: any(
            window not in existing and window.get_visible()
            for window in Gtk.Window.list_toplevels()
        )
    )
    ind.set_waveform([0.3, 0.4])
    assert gains[0].blocks == [[0.1, 0.2]]
    (fallback,) = set(Gtk.Window.list_toplevels()) - existing
    assert fallback.waveform == [0.1, 0.2, 0.3, 0.4]
    ind.hide_indicator()


def test_after_the_shell_drew_again_a_switch_to_x11_warns_again(shell, caplog):
    """Warned once and then quiet, a real failure hours after the Shell came
    back went only to the debug log, which the log file never keeps."""
    caplog.set_level(logging.DEBUG, logger=si.__name__)
    ind = si.ShellIndicator()

    def switched():
        return [r.levelno for r in caplog.records if "Switching to X11" in r.message]

    def dictate(done):
        ind.show_at_position()
        assert _wait_for(done)
        ind.hide_indicator()
        _wait_for(lambda: False, seconds=0.1)

    shell.disconnect()
    dictate(lambda: len(switched()) == 1)
    shell.reconnect()
    dictate(lambda: not ind._fallback_warned)
    shell.disconnect()
    dictate(lambda: len(switched()) == 2)
    assert switched() == [logging.WARNING, logging.WARNING]


def test_a_hide_the_shell_refused_is_logged(shell, caplog):
    """The indicator would stay up until its watchdog, with nothing saying why."""
    ind = si.ShellIndicator()
    ind.show_at_position()
    assert _wait_for(lambda: "Show" in _methods(shell))
    shell.fail_hide = True
    ind.hide_indicator()
    assert _wait_for(lambda: "didn't hide the recording indicator" in caplog.text)


def test_no_hide_line_on_the_way_to_x11(shell, caplog):
    """Without the extension every Hide fails too: not a line per dictation."""
    shell.disconnect()
    ind = si.ShellIndicator()
    ind.show_at_position()
    assert _wait_for(lambda: "Switching to X11" in caplog.text)
    ind.hide_indicator()
    _wait_for(lambda: False, seconds=0.2)
    assert "didn't hide" not in caplog.text


def test_a_hide_refused_before_the_shows_reply_is_logged_too(shell, caplog):
    """A quick tap lets go before the Shell answers Show: its Hide counts
    the same, and a Show that worked late still clears the warned flag."""
    caplog.set_level(logging.DEBUG, logger=si.__name__)
    ind = si.ShellIndicator()
    ind._fallback_warned = True
    shell.defer_show = shell.fail_hide = True
    ind.show_at_position()
    assert _wait_for(lambda: shell.pending is not None)
    ind.hide_indicator()
    assert _wait_for(lambda: "didn't hide the recording indicator" in caplog.text)
    shell.pending.return_value(None)
    assert _wait_for(lambda: not ind._fallback_warned)

