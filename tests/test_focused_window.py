"""Which window has the focus (focused_window.py): one look says what the
window is and which one, so typing that waited and an automatic send can
tell the window the text went to from any other."""
import pytest

from tuxtalk import compositor_focus, focused_window

FRAME = "0,0,800,600|DP-1|0,0,1920,1080"


def test_the_compositors_window_id_says_which_window_it_is(monkeypatch):
    """On X11, Sway or Hyprland the id stays the same when the window moves or
    is re-tiled. Where it is doesn't stand in for one: that changes as it
    moves, and two maximized windows share it."""
    monkeypatch.setattr(focused_window, "reported", lambda: ("", "", FRAME))
    monkeypatch.setattr(compositor_focus, "focused_window", lambda: ("XTerm", 0x2a00003))
    assert focused_window.look() == ("XTerm", ("compositor", 0x2a00003))
    monkeypatch.setattr(compositor_focus, "focused_window", lambda: (None, None))
    assert focused_window.look() == (None, None)


def test_the_tray_is_asked_with_a_time_limit(monkeypatch):
    """Paste asks between the hotkey's release and the text appearing, from
    the thread that types: neither the bus daemon nor a busy tray may hold
    the text up for dbus-python's 25 seconds. A proxy for the tray's name
    would look its owner up again without one."""
    import dbus
    calls = []

    class Bus:
        def get_unique_name(self):
            return ":1.9"

        def call_blocking(self, bus_name, path, interface, method, signature, args, timeout=-1):
            calls.append((bus_name, method, timeout))
            return ":1.7" if method == "GetNameOwner" else ("org.kde.konsole", "", "")

        def get_object(self, *a, **kw):
            raise AssertionError("a proxy asks the bus for the owner with no time limit")
    monkeypatch.setattr(dbus, "SessionBus", Bus)
    assert focused_window.reported() == ("org.kde.konsole", "", "")
    assert [(name, method) for name, method, _ in calls] == [
        ("org.freedesktop.DBus", "GetNameOwner"), (":1.7", "GetFocusedWindow")]
    assert all(0 < timeout <= 5 for _, _, timeout in calls)


# --- KDE and GNOME: the window's own id, through the tray -----------------------------

KWRITE_MAXIMIZED = "0,0,1920,1050|DP-1|0,0,1920,1080"


def _tray_reports(monkeypatch):
    """The tray's real focus cache, reported to the way the KWin script and
    the GNOME extension do (the class, the id and the frame in one call; with
    no id, as the extension of 0.18 did: the class, then the frame), and
    asked the way the dictation service asks it."""
    from tuxtalk import dbus_service
    monkeypatch.setattr(dbus_service, "_focus", dbus_service.FocusReport())
    monkeypatch.setattr(dbus_service, "_clipboard_tool_focused", False)
    service = object.__new__(dbus_service.TuxTalkDBusService)
    monkeypatch.setattr(focused_window, "reported",
                        lambda: dbus_service.TuxTalkDBusService.GetFocusedWindow(service))
    monkeypatch.setattr(compositor_focus, "focused_window", lambda: (None, None))

    def report(window_id, wm_class, frame):
        if window_id is None:
            dbus_service.TuxTalkDBusService.SetFocusedWindowClass(service, wm_class)
            dbus_service.TuxTalkDBusService.SetFocusedWindowGeometry(service, frame)
        else:
            dbus_service.TuxTalkDBusService.SetFocusedWindow(service, wm_class, window_id, frame)
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


def test_an_extension_that_sends_no_id_tells_only_the_app(monkeypatch):
    """The extension still loaded from before an update sends only the class
    and the frame: the id the last window had mustn't stick to the next one."""
    report = _tray_reports(monkeypatch)
    report("4183", "kwrite", KWRITE_MAXIMIZED)
    report(None, "firefox", "0,0,800,600|DP-1|0,0,1920,1080")
    assert focused_window.look() == ("firefox", None)


def test_wl_copys_brief_window_takes_no_id(monkeypatch):
    report = _tray_reports(monkeypatch)
    report("4183", "kwrite", KWRITE_MAXIMIZED)
    report("4190", "io.github.bugaevc.wl-clipboard", "0,0,1,1|DP-1|0,0,1920,1080")
    assert focused_window.look() == ("kwrite", ("tray", "4183"))


def test_with_the_id_known_the_compositor_isnt_asked(monkeypatch):
    """KDE on X11: the tray knows it all, so no X11 connection per look."""
    report = _tray_reports(monkeypatch)
    report("4183", "kwrite", KWRITE_MAXIMIZED)
    monkeypatch.setattr(compositor_focus, "focused_window",
                        lambda: (_ for _ in ()).throw(AssertionError("asked the compositor")))
    assert focused_window.look() == ("kwrite", ("tray", "4183"))


def test_the_kwin_script_sends_the_window_in_one_call():
    """Sent apart, a look between the calls paired one window's class with
    the next one's id (the extension's: test_gnome_extension)."""
    from tuxtalk import kwin_focus
    script = kwin_focus.build_script()
    report = script[script.index("function tuxtalkReport(w)"):]
    call = report[report.index("callDBus("):report.index(");") + 2]
    assert '"SetFocusedWindow"' in call and "w.internalId" in call and "tuxtalkFrame(w)" in call
    assert "SetFocusedWindowClass" not in script


def test_the_extension_of_0_18_never_pairs_a_class_with_the_last_windows_frame(monkeypatch):
    """Still loaded until the user logs out, it sends the class, then the
    frame. A look in between got the new class with the old window's frame."""
    report = _tray_reports(monkeypatch)
    report("4183", "kwrite", KWRITE_MAXIMIZED)
    from tuxtalk import dbus_service
    service = object.__new__(dbus_service.TuxTalkDBusService)
    dbus_service.TuxTalkDBusService.SetFocusedWindowClass(service, "firefox")
    monkeypatch.setattr(compositor_focus, "focused_rect", lambda: None)
    assert focused_window.look() == ("firefox", None)


def test_a_tray_id_is_never_compared_with_an_x11_id(monkeypatch):
    """Plasma X11 or GNOME on Xorg: a tray too busy to answer leaves the look
    to X11, whose number for the same KWrite window isn't KWin's id. Taken as
    another window, the countdown said "you switched windows" in the window
    the user never left."""
    report = _tray_reports(monkeypatch)
    report("{5c1d0e2a}", "kwrite", KWRITE_MAXIMIZED)
    first = focused_window.look()
    monkeypatch.setattr(focused_window, "reported", lambda: None)
    monkeypatch.setattr(compositor_focus, "focused_window", lambda: ("kwrite", 0x3200007))
    now = focused_window.look()
    assert not focused_window.changed(first, now)
    assert focused_window.same_window(first, now) is None


def test_one_window_named_by_kwin_and_by_x11_is_still_one_app(monkeypatch):
    """Plasma X11 with the tray busy: KWin's class is lowercased, X11's is as
    Chrome set it. Taken for another app, a send said "you switched
    windows" in the window the user never left."""
    report = _tray_reports(monkeypatch)
    report("{5c1d0e2a}", "google-chrome", KWRITE_MAXIMIZED)
    first = focused_window.look()
    monkeypatch.setattr(focused_window, "reported", lambda: None)
    monkeypatch.setattr(compositor_focus, "focused_window", lambda: ("Google-chrome", 0x3200007))
    assert not focused_window.changed(first, focused_window.look())


@pytest.mark.parametrize("before, after, same", [
    (("kwrite", ("tray", "{a}")), ("kwrite", ("tray", "{a}")), True),
    (("kwrite", ("tray", "{a}")), ("kwrite", ("tray", "{b}")), False),
    (("kwrite", ("tray", "{a}")), ("konsole", ("tray", "{a}")), False),
    (("kwrite", None), ("kwrite", None), None),                       # COSMIC: only the app
    ((None, None), ("kwrite", ("tray", "{a}")), None),                # a look that timed out
    (("kwrite", ("tray", "{a}")), (None, ("own", ("tray", "{t}"))), False),   # Preferences
    (("cosmic-term", None), (None, ("own", None)), False),            # COSMIC: Preferences
    ((None, ("own", None)), (None, ("own", None)), None),             # which of TuxTalk's?
    ((None, None), (None, ("own", None)), None),                      # a look that timed out
])
def test_which_looks_are_one_window(before, after, same):
    assert focused_window.same_window(before, after) is same
    assert focused_window.changed(before, after) is (same is False)


def test_going_into_a_password_field_changes_where_the_keys_go():
    before = focused_window.Destination("firefox", ("tray", "{a}"), False)
    assert focused_window.changed(before, before._replace(password=True))
