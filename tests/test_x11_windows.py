"""Close, minimize and maximize windows by voice on X11 (x11_windows.py):
TuxTalk asks the window manager, as a taskbar does, and never closes a
window itself. The window manager is a stand-in on a private X server
(desktop_fakes.py): nothing here touches the real desktop.
"""
from desktop_fakes import set_desktop
from tuxtalk import x11_windows

FIREFOX = frozenset({"firefox"})


def test_x11_close_asks_the_window_manager_politely(window_manager):
    """_NET_CLOSE_WINDOW, from a taskbar: the window manager asks the app to
    close, as its X button does, so "Save changes?" still comes up."""
    firefox = window_manager.window("Navigator", "firefox")
    window_manager.stack([firefox])
    assert x11_windows.act("close", "firefox", FIREFOX, False) is True
    assert window_manager.requests(b"_NET_CLOSE_WINDOW") == [(firefox, (0, 2, 0, 0, 0))]


def test_x11_close_takes_the_window_used_last(window_manager):
    older = window_manager.window("Navigator", "firefox")
    newer = window_manager.window("Navigator", "firefox")
    window_manager.stack([older, newer, window_manager.window("mousepad", "Mousepad")])
    assert x11_windows.act("close", "firefox", FIREFOX, False) is True
    assert [w for w, _ in window_manager.requests(b"_NET_CLOSE_WINDOW")] == [newer]


def test_x11_close_all_asks_for_each_window(window_manager):
    first = window_manager.window("Navigator", "firefox")
    second = window_manager.window("Navigator", "firefox")
    window_manager.stack([first, window_manager.window("mousepad", "Mousepad"), second])
    assert x11_windows.act("close", "firefox", FIREFOX, True) is True
    assert window_manager.requests(b"_NET_CLOSE_WINDOW") == [(second, (0, 2, 0, 0, 0)),
                                                             (first, (0, 2, 0, 0, 0))]


def test_x11_close_window_closes_the_active_one(window_manager):
    """"close window": the one with the focus, and no other."""
    editor = window_manager.window("mousepad", "Mousepad")
    firefox = window_manager.window("Navigator", "firefox")
    window_manager.stack([editor, firefox])
    window_manager.set_active(editor)
    assert x11_windows.act("close", "", frozenset(), False) is True
    assert window_manager.requests(b"_NET_CLOSE_WINDOW") == [(editor, (0, 2, 0, 0, 0))]


def test_x11_minimize_sends_wm_change_state_iconic(window_manager):
    """ICCCM's way: IconicState (3), which every window manager knows."""
    editor = window_manager.window("mousepad", "Mousepad")
    window_manager.stack([editor])
    window_manager.set_active(editor)
    assert x11_windows.act("minimize", "", frozenset(), False) is True
    assert window_manager.requests(b"WM_CHANGE_STATE") == [(editor, (3, 0, 0, 0, 0))]


def test_x11_maximize_adds_both_maximized_states(window_manager):
    editor = window_manager.window("mousepad", "Mousepad")
    window_manager.set_active(editor)
    assert x11_windows.act("maximize", "", frozenset(), False) is True
    vert = window_manager.atom(b"_NET_WM_STATE_MAXIMIZED_VERT")
    horz = window_manager.atom(b"_NET_WM_STATE_MAXIMIZED_HORZ")
    assert window_manager.requests(b"_NET_WM_STATE") == [(editor, (1, vert, horz, 2, 0))]


def test_x11_restore_removes_them(window_manager):
    editor = window_manager.window("mousepad", "Mousepad")
    window_manager.set_active(editor)
    assert x11_windows.act("unmaximize", "", frozenset(), False) is True
    (_window, data), = window_manager.requests(b"_NET_WM_STATE")
    assert data[0] == 0


def test_x11_without_an_active_window_does_nothing(window_manager):
    window_manager.stack([window_manager.window("mousepad", "Mousepad")])
    assert x11_windows.act("close", "", frozenset(), False) is False
    assert window_manager.requests(b"_NET_CLOSE_WINDOW") == []


def test_x11_an_app_with_no_window_does_nothing(window_manager):
    window_manager.stack([window_manager.window("mousepad", "Mousepad")])
    assert x11_windows.act("close", "firefox", FIREFOX, False) is False
    assert window_manager.requests(b"_NET_CLOSE_WINDOW") == []


def test_no_x_server_does_nothing(monkeypatch):
    set_desktop(monkeypatch, "XFCE", "x11")
    monkeypatch.setenv("DISPLAY", ":9999")
    assert x11_windows.act("close", "", frozenset(), False) is False
