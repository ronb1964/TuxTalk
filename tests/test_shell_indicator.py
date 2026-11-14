import numpy as np
import pytest

gi = pytest.importorskip("gi")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from tuxtalk import shell_indicator as si  # noqa: E402
from tuxtalk import live_text_window  # noqa: E402
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
        invocation.return_value(None)

    info = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML).interfaces[0]
    registration = bus.register_object(si.OBJECT_PATH, info, on_call, None, None)
    owned = []
    owner = Gio.bus_own_name_on_connection(bus, si.BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                                           lambda *_: owned.append(True), None)
    assert _wait_for(lambda: owned)
    yield calls
    # Let a test's queued hide run, so its updates stop before the next test.
    _wait_for(lambda: False, seconds=0.1)
    Gio.bus_unown_name(owner)
    if not calls.disconnected:
        calls.disconnect()
    _wait_for(lambda: not si.available())


def _methods(calls):
    return [method for method, _ in calls]


def test_without_the_extension_the_x11_window_draws_the_indicator(bus, monkeypatch):
    monkeypatch.setattr(RecordingIndicator, "__init__", lambda self, **_: None)
    assert isinstance(si.create_recording_indicator(style="orb"), RecordingIndicator)


def test_with_the_extension_the_shell_draws_the_indicator(shell):
    assert isinstance(si.create_recording_indicator(style="orb"), si.ShellIndicator)


def test_showing_tells_the_shell_where_and_how_to_draw(shell):
    ind = si.ShellIndicator(position="Bottom-Center", size="large", custom_color="#ff0000",
                            follow_window=True)
    ind.show_at_position()
    assert _wait_for(lambda: "Show" in _methods(shell))
    (settings,) = dict(shell)["Show"]
    assert settings == {
        "style": "orb", "rgb": [1.0, 0.0, 0.0], "scale": 1.4,
        "position": "bottom-center", "follow": True, "offset_x": 0, "offset_y": 0,
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


def test_no_updates_after_hiding(shell):
    ind = si.ShellIndicator()
    ind.show_at_position()
    assert _wait_for(lambda: "Update" in _methods(shell))
    ind.hide_indicator()
    assert _wait_for(lambda: "Hide" in _methods(shell))
    count = _methods(shell).count("Update")
    _wait_for(lambda: False, seconds=0.2)
    assert _methods(shell).count("Update") == count


def test_extension_disabling_mid_recording_shows_the_x11_indicator(shell):
    existing = set(Gtk.Window.list_toplevels())
    ind = si.ShellIndicator()
    ind.show_at_position()
    ind.start_recording()
    assert _wait_for(lambda: "Update" in _methods(shell))
    shell.disconnect()
    assert _wait_for(lambda: any(isinstance(window, RecordingIndicator) and
                                 window not in existing and window.get_visible()
                                 for window in Gtk.Window.list_toplevels()))
    ind.hide_indicator()


def test_the_shell_indicator_returns_after_the_extension_is_enabled(shell):
    existing = set(Gtk.Window.list_toplevels())
    ind = si.ShellIndicator()
    ind.show_at_position()
    assert _wait_for(lambda: "Update" in _methods(shell))
    shell.disconnect()
    assert _wait_for(lambda: any(isinstance(window, RecordingIndicator) and
                                 window not in existing and window.get_visible()
                                 for window in Gtk.Window.list_toplevels()))
    ind.hide_indicator()
    shell.reconnect()
    ind.show_at_position()
    assert _wait_for(lambda: _methods(shell).count("Show") == 2)
    ind.hide_indicator()


def test_style_changes_wait_until_the_next_show(shell):
    ind = si.ShellIndicator(style="bars")
    ind.show_at_position()
    assert _wait_for(lambda: "Show" in _methods(shell))
    ind.apply_settings("center", "medium", 0, 0, style="radial")
    ind.set_spectrum([0.3, 0.7])
    assert _wait_for(lambda: ("Update", (0.0, [0.3, 0.7])) in shell)
    ind.hide_indicator()
    assert _wait_for(lambda: "Hide" in _methods(shell))
    ind.show_at_position()
    assert _wait_for(lambda: _methods(shell).count("Show") == 2)
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


def test_the_waveform_keeps_one_point_per_pixel_within_range():
    points = si.waveform_points(np.linspace(-1, 1, 1024), 120, 1.0)
    assert len(points) == 120
    assert min(points) == -1.0 and max(points) == 1.0


def test_the_waveform_follows_sensitivity():
    quiet = si.waveform_points([0.1] * 10, 10, 1.0)
    assert quiet[0] == pytest.approx(0.3)
    assert si.waveform_points([0.1] * 10, 10, 2.0)[0] == pytest.approx(0.6)
