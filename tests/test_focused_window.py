"""Which window has the focus (focused_window.py): one look says what the
window is and which one, so typing that waited and an automatic send can
tell the window the text went to from any other."""
from tuxtalk import compositor_focus, focused_window
from tuxtalk.window_geometry import Rect

FRAME = "0,0,800,600|DP-1|0,0,1920,1080"


def test_the_compositors_window_id_says_which_window_it_is(monkeypatch):
    """On X11, Sway or Hyprland the id stays the same when the window moves or
    is re-tiled; without one, where it is stands in."""
    monkeypatch.setattr(focused_window, "reported", lambda: ("", FRAME))
    monkeypatch.setattr(compositor_focus, "focused_window", lambda: ("XTerm", 0x2a00003))
    assert focused_window.look() == ("XTerm", 0x2a00003)
    monkeypatch.setattr(compositor_focus, "focused_window", lambda: (None, None))
    assert focused_window.look() == (None, (Rect(0, 0, 800, 600), "DP-1",
                                            Rect(0, 0, 1920, 1080)))


def test_the_tray_is_asked_with_a_time_limit(monkeypatch):
    """Paste asks between the hotkey's release and the text appearing: a
    busy tray must not hold the text up for D-Bus's 25 seconds."""
    import dbus
    calls = []

    class Bus:
        def get_name_owner(self, name):
            return ":1.7"

        def get_unique_name(self):
            return ":1.9"

        def get_object(self, *a, **kw):
            return self

        def GetFocusedWindow(self, **kw):
            calls.append(kw)
            return ("org.kde.konsole", "")
    monkeypatch.setattr(dbus, "SessionBus", Bus)
    assert focused_window.reported() == ("org.kde.konsole", "")
    assert calls and 0 < calls[0]["timeout"] <= 5
