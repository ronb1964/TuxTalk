"""Which window has the focus (focused_window.py): one look says what the
window is and which one, so typing that waited and an automatic send can
tell the window the text went to from any other."""
from tuxtalk import compositor_focus, focused_window
from tuxtalk.window_geometry import Rect

FRAME = "0,0,800,600|DP-1|0,0,1920,1080"


def test_the_compositors_window_id_says_which_window_it_is(monkeypatch):
    """On X11, Sway or Hyprland the id stays the same when the window moves or
    is re-tiled; without one, where it is stands in."""
    monkeypatch.setattr(focused_window, "reported", lambda: ("", "", FRAME))
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
            return ("org.kde.konsole", "", "")
    monkeypatch.setattr(dbus, "SessionBus", Bus)
    assert focused_window.reported() == ("org.kde.konsole", "", "")
    assert calls and 0 < calls[0]["timeout"] <= 5


# --- KDE and GNOME: the window's own id, through the tray -----------------------------

KWRITE_MAXIMIZED = "0,0,1920,1050|DP-1|0,0,1920,1080"


def _tray_reports(monkeypatch):
    """The tray's real focus cache, reported to the way the KWin script and
    the GNOME extension do (the id, then the class, then the frame), and
    asked the way the dictation service asks it."""
    from tuxtalk import dbus_service
    for name, value in (("_focused_window_class", None), ("_focused_window_id", ""),
                        ("_next_window_id", ""), ("_focused_window_geometry", ""),
                        ("_clipboard_tool_focused", False)):
        monkeypatch.setattr(dbus_service, name, value)
    service = object.__new__(dbus_service.TuxTalkDBusService)
    monkeypatch.setattr(focused_window, "reported",
                        lambda: dbus_service.TuxTalkDBusService.GetFocusedWindow(service))
    monkeypatch.setattr(compositor_focus, "focused_window", lambda: (None, None))

    def report(window_id, wm_class, frame):
        if window_id is not None:
            dbus_service.TuxTalkDBusService.SetFocusedWindowId(service, window_id)
        dbus_service.TuxTalkDBusService.SetFocusedWindowClass(service, wm_class)
        dbus_service.TuxTalkDBusService.SetFocusedWindowGeometry(service, frame)
    return report


def test_two_maximized_windows_of_one_app_are_told_apart(monkeypatch):
    """Two KWrite windows maximized on one screen: same class, same frame.
    An automatic send set up for one went into the other."""
    report = _tray_reports(monkeypatch)
    report("{5c1d0e2a}", "org.kde.kwrite", KWRITE_MAXIMIZED)
    first = focused_window.look()
    report("{9f04b7c3}", "org.kde.kwrite", KWRITE_MAXIMIZED)
    assert focused_window.changed(first, focused_window.look())


def test_a_window_moved_or_resized_is_still_the_same_window(monkeypatch):
    report = _tray_reports(monkeypatch)
    report("4183", "kwrite", KWRITE_MAXIMIZED)
    first = focused_window.look()
    report("4183", "kwrite", "200,100,900,700|DP-1|0,0,1920,1080")
    assert not focused_window.changed(first, focused_window.look())


def test_an_extension_that_sends_no_id_falls_back_to_the_frame(monkeypatch):
    """The extension still loaded from before an update sends only the class:
    the id the last window had mustn't stick to the next one."""
    report = _tray_reports(monkeypatch)
    report("4183", "kwrite", KWRITE_MAXIMIZED)
    report(None, "firefox", "0,0,800,600|DP-1|0,0,1920,1080")
    assert focused_window.look() == ("firefox", (Rect(0, 0, 800, 600), "DP-1",
                                                 Rect(0, 0, 1920, 1080)))


def test_wl_copys_brief_window_takes_no_id(monkeypatch):
    report = _tray_reports(monkeypatch)
    report("4183", "kwrite", KWRITE_MAXIMIZED)
    report("4190", "io.github.bugaevc.wl-clipboard", "0,0,1,1|DP-1|0,0,1920,1080")
    assert focused_window.look() == ("kwrite", "4183")


def test_with_the_id_known_the_compositor_isnt_asked(monkeypatch):
    """KDE on X11: the tray knows it all, so no X11 connection per look."""
    report = _tray_reports(monkeypatch)
    report("4183", "kwrite", KWRITE_MAXIMIZED)
    monkeypatch.setattr(compositor_focus, "focused_window",
                        lambda: (_ for _ in ()).throw(AssertionError("asked the compositor")))
    assert focused_window.look() == ("kwrite", "4183")


def test_the_kwin_script_and_the_extension_send_the_id_before_the_class():
    import pathlib
    from tuxtalk import kwin_focus
    script = kwin_focus.build_script()
    assert script.index('"SetFocusedWindowId"') < script.index('"SetFocusedWindowClass"')
    assert "w.internalId" in script
    ext = (pathlib.Path(__file__).resolve().parent.parent / "gnome-extension"
           / "talktype@ronb1964.github.io" / "extension.js").read_text()
    assert '<method name="SetFocusedWindowId">' in ext
    push = ext[ext.index("_pushFocusedWindowClass() {"):]
    assert push.index("SetFocusedWindowIdRemote") < push.index("SetFocusedWindowClassRemote")
    assert "win.get_id()" in push
