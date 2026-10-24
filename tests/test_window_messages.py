"""Messages where the user is looking: a voice-edit problem floats over the
middle of the active window instead of only in the corner notification.
"""
import json
import pathlib

import pytest

from tuxtalk import compositor_focus, window_geometry as wg
from tuxtalk.window_geometry import Rect

SRC = pathlib.Path(wg.__file__).parent
EXT = next((SRC.parent.parent / "gnome-extension").glob("*/"))


# --- reading what the KWin script / GNOME extension reported ------------------------

def test_parse_a_report():
    win, output, output_rect = wg.parse_reported("100,200,800,600|DP-6|0,0,3440,1440")
    assert win == Rect(100, 200, 800, 600)
    assert output == "DP-6" and output_rect == Rect(0, 0, 3440, 1440)


def test_parse_a_report_without_an_output_name():
    win, output, output_rect = wg.parse_reported("10,20,30,40||0,0,1920,1080")
    assert output == "" and output_rect == Rect(0, 0, 1920, 1080)


@pytest.mark.parametrize("bad", ["", "nonsense", "1,2,3|x|1,2,3,4", "1,2,3,4|x|y"])
def test_a_bad_report_is_nothing(bad):
    assert wg.parse_reported(bad) is None


# --- compositor coordinates -> where GTK draws ----------------------------------------

def _mapped_center(rect, output, output_rect, monitors):
    """The middle of *rect* once mapped, worked out as active_window_center does."""
    r = wg.to_screen_rect(rect, output, output_rect, monitors)
    return (round(r.x + r.w / 2), round(r.y + r.h / 2))


def test_unscaled_screen_is_one_to_one():
    monitors = [("DP-6", Rect(0, 0, 3440, 1440))]
    assert _mapped_center(Rect(100, 200, 800, 600), "DP-6", Rect(0, 0, 3440, 1440), monitors) == (500, 500)


def test_a_scaled_screen_is_scaled():
    """Plasma's XWayland default: X apps see physical pixels, KWin reports logical."""
    monitors = [("DP-1", Rect(0, 0, 3840, 2160))]
    point = _mapped_center(Rect(100, 100, 400, 200), "DP-1", Rect(0, 0, 1920, 1080), monitors)
    assert point == (600, 400)


def test_the_window_is_found_on_its_own_monitor():
    monitors = [("HDMI-A-1", Rect(0, 0, 1920, 1080)), ("DP-2", Rect(1920, 0, 2560, 1440))]
    point = _mapped_center(Rect(2020, 100, 400, 200), "DP-2", Rect(1920, 0, 2560, 1440), monitors)
    assert point == (2220, 200)


def test_without_a_name_a_single_monitor_still_scales():
    monitors = [("", Rect(0, 0, 3840, 2160))]
    assert _mapped_center(Rect(0, 0, 200, 100), "", Rect(0, 0, 1920, 1080), monitors) == (200, 100)


def test_without_a_name_several_monitors_are_one_to_one():
    monitors = [("", Rect(0, 0, 1920, 1080)), ("", Rect(1920, 0, 1920, 1080))]
    assert _mapped_center(Rect(2000, 0, 200, 100), "", Rect(1920, 0, 1920, 1080), monitors) == (2100, 50)


# --- which source answers ---------------------------------------------------------------

@pytest.fixture
def sources(monkeypatch):
    monkeypatch.setattr(wg, "screen_monitors", lambda: [("DP-6", Rect(0, 0, 3440, 1440))])
    monkeypatch.setattr(wg, "_reported", lambda: None)
    monkeypatch.setattr(compositor_focus, "focused_rect", lambda: None)
    monkeypatch.setattr(wg, "_x11_active_rect", lambda: None)


def test_the_tray_report_comes_first(sources, monkeypatch):
    monkeypatch.setattr(wg, "_reported", lambda: "100,200,800,600|DP-6|0,0,3440,1440")
    monkeypatch.setattr(compositor_focus, "focused_rect", lambda: Rect(0, 0, 10, 10))
    assert wg.active_window_center() == (500, 500)


def test_then_the_compositor(sources, monkeypatch):
    monkeypatch.setattr(compositor_focus, "focused_rect", lambda: Rect(0, 0, 1000, 400))
    assert wg.active_window_center() == (500, 200)


def test_then_x11(sources, monkeypatch):
    monkeypatch.setattr(wg, "_x11_active_rect", lambda: Rect(10, 10, 100, 100))
    assert wg.active_window_center() == (60, 60)


def test_nobody_knows(sources):
    assert wg.active_window_center() is None


# --- Sway and Hyprland say where their focused window is --------------------------------

def test_sway_focused_rect(monkeypatch):
    tree = {"nodes": [{"type": "con", "focused": True, "app_id": "foot",
                       "rect": {"x": 10, "y": 20, "width": 300, "height": 200}}]}
    monkeypatch.setenv("SWAYSOCK", "/tmp/sway.sock")
    for var in ("HYPRLAND_INSTANCE_SIGNATURE", "NIRI_SOCKET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(compositor_focus, "_exchange", lambda path, req, read: tree)
    assert compositor_focus.focused_rect() == Rect(10, 20, 300, 200)


def test_hyprland_focused_rect(monkeypatch):
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "abc")
    monkeypatch.setattr(compositor_focus, "_exchange",
                        lambda path, req, read: json.dumps({"at": [5, 6], "size": [70, 80]}).encode())
    assert compositor_focus.focused_rect() == Rect(5, 6, 70, 80)


# --- the tray keeps the latest report ------------------------------------------------------

def test_the_tray_caches_the_geometry():
    from tuxtalk import dbus_service
    svc = object()
    dbus_service.TuxTalkDBusService.SetFocusedWindowGeometry(svc, "1,2,3,4|DP-6|0,0,10,10")
    assert dbus_service.TuxTalkDBusService.GetFocusedWindow(svc)[-1] == "1,2,3,4|DP-6|0,0,10,10"


# --- KDE and GNOME report it ------------------------------------------------------------------

def test_the_kwin_script_reports_geometry_and_follows_moves():
    from tuxtalk import kwin_focus
    js = kwin_focus.build_script()
    assert "SetFocusedWindowGeometry" in js
    assert "frameGeometryChanged" in js and "output" in js
    assert "SetFocusedWindowClass" in js           # still reports the class


def test_the_gnome_extension_reports_geometry():
    js = (EXT / "extension.js").read_text()
    assert '<method name="SetFocusedWindowGeometry">' in js
    assert "SetFocusedWindowGeometryRemote" in js
    assert "position-changed" in js and "size-changed" in js
    assert json.loads((EXT / "metadata.json").read_text())["version"] >= 19


# --- the floating label -------------------------------------------------------------------------

def test_the_label_is_centred_on_the_point():
    from tuxtalk import screen_message
    assert screen_message.place(200, 40, (500, 500), Rect(0, 0, 3440, 1440)) == (400, 480)


def test_the_label_stays_on_the_monitor():
    from tuxtalk import screen_message
    assert screen_message.place(200, 40, (10, 1435), Rect(0, 0, 3440, 1440)) == (0, 1400)


# --- voice edit problems show there -----------------------------------------------------------------

def test_a_voice_edit_problem_floats_over_the_active_window(monkeypatch):
    from tuxtalk import app, screen_message
    shown = []
    monkeypatch.setattr(screen_message, "show_over_active_window", shown.append)
    monkeypatch.setattr(app, "_notify", lambda *a: None)
    monkeypatch.setattr(app, "_beep", lambda *a: None)
    app._edit_problem("no_selection", False)
    assert shown == ["Select some text first"]


def test_gdk_is_only_used_on_the_gtk_thread(sources, monkeypatch):
    """The voice-edit problem is raised on the dictation thread; asking GDK
    where the window is from there would race the GTK thread drawing the
    indicator."""
    from gi.repository import GLib
    from tuxtalk import screen_message
    queued, gdk_calls, shown = [], [], []
    monkeypatch.setattr(wg, "_reported", lambda: "100,200,800,600|DP-6|0,0,3440,1440")
    monkeypatch.setattr(wg, "screen_monitors",
                        lambda: gdk_calls.append("monitors") or [("DP-6", Rect(0, 0, 3440, 1440))])
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *args: queued.append((fn, args)))
    monkeypatch.setattr(screen_message, "_show_now", lambda text, center, *opts: shown.append((text, center)))
    screen_message.show_over_active_window("Select some text first")
    assert gdk_calls == [] and len(queued) == 1        # nothing from GDK yet
    fn, args = queued[0]
    fn(*args)                                           # the GTK thread
    assert gdk_calls == ["monitors"]
    assert shown == [("Select some text first", (500, 500))]


def test_on_x11_the_window_manager_is_asked_on_the_gtk_thread(sources, monkeypatch):
    from gi.repository import GLib
    from tuxtalk import screen_message
    queued, shown = [], []
    monkeypatch.setattr(wg, "_x11_active_rect", lambda: Rect(10, 10, 100, 100))
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *args: queued.append((fn, args)))
    monkeypatch.setattr(screen_message, "_show_now", lambda text, center, *opts: shown.append(center))
    screen_message.show_over_active_window("x")
    assert queued[0][1][1] is None                      # no answer without GDK
    queued[0][0](*queued[0][1])
    assert shown == [(60, 60)]


# --- on screen long enough to read, and short ------------------------------------------------

def test_long_enough_to_read():
    from tuxtalk import screen_message
    assert screen_message.reading_seconds("Select some text first") == 4.0
    assert 4.0 < screen_message.reading_seconds("Window changed, so the rewrite is in Recent Dictations") <= 8.0
    assert screen_message.reading_seconds(" ".join(["word"] * 60)) == 8.0


def test_the_label_says_it_short_and_the_notification_in_full(monkeypatch):
    from tuxtalk import app, edit_notices, screen_message
    shown, notified = [], []
    monkeypatch.setattr(screen_message, "show_over_active_window", shown.append)
    monkeypatch.setattr(app, "_notify", lambda title, msg: notified.append(msg))
    monkeypatch.setattr(app, "_beep", lambda *a: None)
    for kind in edit_notices.NOTICES:
        shown.clear(); notified.clear()
        app._edit_problem(kind, False)
        assert len(shown[0].split()) <= 7, (kind, shown[0])
        assert notified[0] == edit_notices.NOTICES[kind]


# --- with no tray, the service answers itself --------------------------------------------------

class _FakeBus:
    def __init__(self, owner, me):
        self.owner, self.me, self.asked = owner, me, []

    def get_name_owner(self, name):
        return self.owner

    def get_unique_name(self):
        return self.me

    def get_object(self, *a, **k):
        import dbus
        self.asked.append(a)
        raise dbus.exceptions.DBusException("no tray in this test")


def test_the_service_holding_the_name_reads_its_own_cache(monkeypatch):
    """Calling its own bus name from the GTK thread would wait out the timeout
    every time the indicator follows a window."""
    import dbus
    from tuxtalk import dbus_service
    bus = _FakeBus(":1.5", ":1.5")
    monkeypatch.setattr(dbus, "SessionBus", lambda: bus)
    monkeypatch.setattr(dbus_service, "_focused_window_geometry", "1,2,3,4|DP-6|0,0,10,10")
    assert wg._reported() == "1,2,3,4|DP-6|0,0,10,10"
    assert bus.asked == []


def test_the_tray_is_asked_when_it_holds_the_name(monkeypatch):
    import dbus
    bus = _FakeBus(":1.2", ":1.5")
    monkeypatch.setattr(dbus, "SessionBus", lambda: bus)
    assert wg._reported() is None and len(bus.asked) == 1


def test_a_low_label_sits_above_a_chat_windows_message_box():
    """The send countdown goes over the lower part of the window, near where a
    chat app's message box is, not in the middle (Ron, 2026-10-18)."""
    from tuxtalk import screen_message
    from tuxtalk.window_geometry import Rect
    assert screen_message.low_center(Rect(100, 50, 1000, 1200)) == (600, 1050)
    # A short window: a quarter of its height above the bottom.
    assert screen_message.low_center(Rect(0, 0, 400, 400)) == (200, 300)


def test_without_an_x_display_the_label_opens_no_window_that_takes_the_keyboard(monkeypatch):
    """On native Wayland GTK 3 makes the label an ordinary window that takes
    the focus: everything typed while "Press Enter when you're ready" showed
    was lost (VM test, 2026-10-20). There it isn't shown at all."""
    from gi.repository import Gdk, Gtk
    from tuxtalk import screen_message

    class Monitor:
        def get_geometry(self):
            area = Gdk.Rectangle()
            area.width, area.height = 1920, 1080
            return area

    class WaylandDisplay:          # everything a display has here, but not GdkX11.X11Display
        def get_primary_monitor(self):
            return Monitor()
        get_monitor = get_monitor_at_point = lambda self, *_: Monitor()
    monkeypatch.setattr(Gdk.Display, "get_default", staticmethod(lambda: WaylandDisplay()))

    windows = []
    monkeypatch.setattr(Gtk, "Window", lambda *a, **k: windows.append(a) or 1 / 0)
    screen_message._show_now("Sending in 2 s", None)
    assert windows == []
