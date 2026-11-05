"""Snap, centre, show desktop and desktops on X11 (x11_desktop), against the
stand-in window manager of desktop_fakes.py: what it is asked is what a
Cinnamon, Xfce or MATE user would see happen."""
import pytest

from tuxtalk import x11_activate, x11_desktop

ROOT_STATE = ((b"_NET_WORKAREA", (0, 0, 1280, 1000)), (b"_NET_NUMBER_OF_DESKTOPS", (4,)),
              (b"_NET_CURRENT_DESKTOP", (0,)))


@pytest.fixture
def desktops(window_manager):
    """The window manager with a four-desktop root, the first one current."""
    for name, values in ROOT_STATE:
        window_manager.set_cardinals(window_manager.root, name, values)
    return window_manager


@pytest.fixture
def one_monitor(monkeypatch):
    monkeypatch.setattr(x11_desktop, "monitors", lambda display: [(0, 0, 1280, 1024)])


def _active_window(wm, extents=(0, 0, 0, 0)):
    window = wm.window("kwrite", "kwrite")
    wm.set_active(window)
    wm.set_cardinals(window, b"_NET_FRAME_EXTENTS", extents)
    return window


def test_x11_snap_left_asks_for_the_left_half_less_the_frame(desktops, one_monitor):
    w = _active_window(desktops, (1, 1, 24, 1))
    assert x11_desktop.act("snap left", None) == "done"
    assert desktops.requests(b"_NET_WM_STATE")[0][1][0] == 0          # unmaximized first
    assert desktops.requests(b"_NET_MOVERESIZE_WINDOW") == [(w, (0x2F01, 0, 0, 638, 975))]


def test_x11_snap_bottom_right_takes_the_quarter(desktops, one_monitor):
    w = _active_window(desktops, (1, 1, 24, 1))
    assert x11_desktop.act("snap bottom right", None) == "done"
    assert desktops.requests(b"_NET_MOVERESIZE_WINDOW") == [(w, (0x2F01, 640, 500, 638, 475))]


def test_x11_centre_moves_without_resizing(desktops, one_monitor):
    w = _active_window(desktops)
    assert x11_desktop.act("center", None) == "done"
    assert desktops.requests(b"_NET_MOVERESIZE_WINDOW") == [(w, (0x2301, 635, 495, 0, 0))]


def test_x11_snap_uses_the_monitor_the_window_is_on(desktops, monkeypatch):
    monkeypatch.setattr(x11_desktop, "monitors",
                        lambda d: [(0, 0, 1280, 1024), (1280, 0, 1920, 1080)])
    desktops.set_cardinals(desktops.root, b"_NET_WORKAREA", (0, 0, 3200, 1080))
    w = _active_window(desktops)
    desktops.move(w, 1500, 100)
    x11_desktop.act("snap left", None)
    assert desktops.requests(b"_NET_MOVERESIZE_WINDOW") == [(w, (0x2F01, 1280, 0, 960, 1080))]


def test_x11_show_desktop_toggles(desktops):
    desktops.set_cardinals(desktops.root, b"_NET_SHOWING_DESKTOP", (0,))
    assert x11_desktop.act("show desktop", None) == "done"
    assert desktops.requests(b"_NET_SHOWING_DESKTOP") == [(desktops.root, (1, 0, 0, 0, 0))]
    desktops.set_cardinals(desktops.root, b"_NET_SHOWING_DESKTOP", (1,))
    x11_desktop.act("show desktop", None)
    assert desktops.requests(b"_NET_SHOWING_DESKTOP")[-1] == (desktops.root, (0, 0, 0, 0, 0))


def test_x11_switch_to_desktop_two(desktops):
    assert x11_desktop.act("desktop", 2) == "done"
    assert desktops.requests(b"_NET_CURRENT_DESKTOP") == [(desktops.root, (1, 0, 0, 0, 0))]


def test_x11_a_desktop_that_isnt_there_says_so(desktops):
    assert x11_desktop.act("desktop", 5) == "no_desktop"
    assert desktops.requests(b"_NET_CURRENT_DESKTOP") == []


def test_x11_previous_desktop_at_the_first_says_edge(desktops):
    assert x11_desktop.act("previous desktop", None) == "edge"
    assert desktops.requests(b"_NET_CURRENT_DESKTOP") == []


def test_x11_next_desktop_goes_one_on(desktops):
    assert x11_desktop.act("next desktop", None) == "done"
    assert desktops.requests(b"_NET_CURRENT_DESKTOP") == [(desktops.root, (1, 0, 0, 0, 0))]


def test_x11_a_single_desktop_has_no_next(desktops):
    desktops.set_cardinals(desktops.root, b"_NET_NUMBER_OF_DESKTOPS", (1,))
    assert x11_desktop.act("next desktop", None) == "one_desktop"


def test_x11_move_window_to_desktop_three(desktops):
    w = _active_window(desktops)
    assert x11_desktop.act("to desktop", 3) == "done"
    assert desktops.requests(b"_NET_WM_DESKTOP") == [(w, (2, 2, 0, 0, 0))]


def test_x11_move_window_to_next_desktop(desktops):
    w = _active_window(desktops)
    assert x11_desktop.act("to next desktop", None) == "done"
    assert desktops.requests(b"_NET_WM_DESKTOP") == [(w, (1, 2, 0, 0, 0))]


def test_x11_one_monitor_moves_nothing(desktops, one_monitor):
    _active_window(desktops)
    assert x11_desktop.act("to next screen", None) == "one_screen"
    assert desktops.requests(b"_NET_MOVERESIZE_WINDOW") == []


def test_x11_window_moves_to_the_next_monitor_at_the_same_offset(desktops, monkeypatch):
    monkeypatch.setattr(x11_desktop, "monitors",
                        lambda d: [(0, 0, 1280, 1024), (1280, 0, 1920, 1080)])
    w = _active_window(desktops)
    desktops.move(w, 100, 50)
    assert x11_desktop.act("to next screen", None) == "done"
    assert desktops.requests(b"_NET_MOVERESIZE_WINDOW") == [(w, (0x2301, 1380, 50, 0, 0))]


def test_x11_has_no_overview(desktops):
    assert x11_desktop.act("overview", None) is None


def test_x11_with_no_active_window_does_nothing(desktops, one_monitor):
    assert x11_desktop.act("snap left", None) == "no_window"
    assert desktops.requests(b"_NET_MOVERESIZE_WINDOW") == []


def test_x11_monitors_come_from_randr(window_manager):
    display = x11_activate.open_display()
    try:
        assert x11_desktop.monitors(display) == [display.geometry(display.root)]   # Xvfb: one
    finally:
        display.close()
