"""Snap, centre, show desktop and desktops on X11 (x11_desktop), against the
stand-in window manager of desktop_fakes.py: what it is asked is what a
Cinnamon, Xfce or MATE user would see happen."""
import threading
import time

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


# _NET_MOVERESIZE_WINDOW's first word for a move of the frame's corner (NorthWest).
CORNER_MOVE = 0x2301
MAXIMIZED = (b"_NET_WM_STATE_MAXIMIZED_VERT", b"_NET_WM_STATE_MAXIMIZED_HORZ")


def _middle_after(request, width, height):
    """Where a frame *width* x *height* has its middle once a window
    manager has done the corner move *request* (ICCCM: with NorthWest
    gravity x and y are the frame's corner)."""
    _window, (flags, x, y, *_size) = request
    assert flags == CORNER_MOVE
    return x + width // 2, y + height // 2


def test_x11_centre_puts_the_middle_of_the_window_in_the_middle(desktops, one_monitor):
    """A 400 x 200 window in the 1280 x 1000 work area goes to (440, 400)."""
    w = _active_window(desktops)
    desktops.move(w, 30, 40, 400, 200)
    assert x11_desktop.act("center", None) == "done"
    [request] = desktops.requests(b"_NET_MOVERESIZE_WINDOW")
    assert request == (w, (CORNER_MOVE, 440, 400, 0, 0))
    assert _middle_after(request, 400, 200) == (640, 500)


def test_x11_centre_counts_the_frame(desktops, one_monitor):
    w = _active_window(desktops, (1, 1, 24, 2))
    desktops.move(w, 30, 40, 400, 200)
    x11_desktop.act("center", None)
    [request] = desktops.requests(b"_NET_MOVERESIZE_WINDOW")
    assert _middle_after(request, 402, 226) == (640, 500)


@pytest.fixture
def restores_to(desktops, monkeypatch):
    """restores_to(window, width, height): the window manager gives *window*
    that size back while TuxTalk waits after asking for the un-maximize."""
    def restores(window, width, height):
        def wait(_seconds):
            desktops.move(window, 0, 0, width, height)
        monkeypatch.setattr(x11_desktop.time, "sleep", wait)
    return restores


def test_x11_centre_a_maximized_window_centres_it_at_its_restored_size(desktops, one_monitor,
                                                                       restores_to):
    """Maximized, its frame is the whole work area: centring that frame put
    the restored window in the top left corner."""
    w = _active_window(desktops)
    desktops.move(w, 0, 0, 1280, 1000)
    desktops.set_states(w, *MAXIMIZED)
    restores_to(w, 400, 200)
    assert x11_desktop.act("center", None) == "done"
    assert desktops.requests(b"_NET_WM_STATE")[0][1][0] == 0        # unmaximized first
    [request] = desktops.requests(b"_NET_MOVERESIZE_WINDOW")
    assert _middle_after(request, 400, 200) == (640, 500)


def test_x11_a_maximized_window_that_doesnt_come_back_is_only_unmaximized(desktops, one_monitor,
                                                                          monkeypatch):
    """Its size isn't known, so it isn't moved by a guess: the window
    manager puts it back where it was."""
    w = _active_window(desktops)
    desktops.move(w, 0, 0, 1280, 1000)
    desktops.set_states(w, *MAXIMIZED)
    waited = []
    monkeypatch.setattr(x11_desktop.time, "sleep", waited.append)
    assert x11_desktop.act("center", None) == "done"
    assert desktops.requests(b"_NET_WM_STATE")[0][1][0] == 0
    assert desktops.requests(b"_NET_MOVERESIZE_WINDOW") == []
    assert 0.3 <= sum(waited) <= 1.0


def test_x11_a_move_left_of_the_screen_reaches_the_window_manager(desktops):
    """X11 coordinates are signed: a window partly off the left edge is at a
    negative x, which a window manager reads back as such."""
    w = _active_window(desktops)
    display = x11_activate.open_display()
    try:
        display.ask_window_manager(w, b"_NET_MOVERESIZE_WINDOW", (0x2301, -160, 10))
    finally:
        display.close()
    assert desktops.requests(b"_NET_MOVERESIZE_WINDOW") == [(w, (0x2301, 2**32 - 160, 10, 0, 0))]


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


def test_x11_a_maximized_window_moved_to_the_other_screen_stays_maximized(desktops,
                                                                         monkeypatch):
    monkeypatch.setattr(x11_desktop, "monitors",
                        lambda d: [(0, 0, 1280, 1024), (1280, 0, 1920, 1080)])
    w = _active_window(desktops)
    desktops.move(w, 0, 0, 1280, 1000)
    desktops.set_states(w, *MAXIMIZED)
    assert x11_desktop.act("to next screen", None) == "done"
    assert desktops.requests(b"_NET_MOVERESIZE_WINDOW") == [(w, (0x2301, 1280, 0, 0, 0))]
    vert, horz = (desktops.atom(state) for state in MAXIMIZED)
    assert desktops.requests(b"_NET_WM_STATE")[-1] == (w, (1, vert, horz, 2, 0))


def test_x11_a_window_that_wasnt_maximized_isnt_maximized_on_the_other_screen(desktops,
                                                                             monkeypatch):
    monkeypatch.setattr(x11_desktop, "monitors",
                        lambda d: [(0, 0, 1280, 1024), (1280, 0, 1920, 1080)])
    _active_window(desktops)
    x11_desktop.act("to next screen", None)
    assert all(data[0] == 0 for _w, data in desktops.requests(b"_NET_WM_STATE"))


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


@pytest.mark.parametrize("x11_flags", [("-extension", "RANDR")])
def test_x11_without_randr_the_screen_is_the_one_monitor(desktops):
    # The "-extension RANDR" server has none; asking would shut the connection.
    display = x11_activate.open_display()
    try:
        assert x11_desktop.monitors(display) == [display.geometry(display.root)] != [None]
    finally:
        display.close()
    w = _active_window(desktops)
    assert x11_desktop.act("snap left", None) == "done"
    assert desktops.requests(b"_NET_MOVERESIZE_WINDOW")[0][0] == w


def _two_monitors_with_a_panel_on_the_first(wm, monkeypatch):
    monkeypatch.setattr(x11_desktop, "monitors",
                        lambda d: [(0, 0, 1280, 1024), (1280, 0, 1920, 1080)])
    wm.set_cardinals(wm.root, b"_NET_WORKAREA", (0, 0, 3200, 1080))
    panel = wm.window("panel", "panel", b"_NET_WM_WINDOW_TYPE_DOCK")
    wm.set_cardinals(panel, b"_NET_WM_STRUT_PARTIAL",
                     (0, 0, 0, 30, 0, 0, 0, 0, 0, 0, 0, 1279))
    wm.stack([panel])


def test_x11_snap_stays_above_a_panel_on_a_smaller_monitor(desktops, monkeypatch):
    _two_monitors_with_a_panel_on_the_first(desktops, monkeypatch)
    w = _active_window(desktops)
    x11_desktop.act("snap left", None)
    assert desktops.requests(b"_NET_MOVERESIZE_WINDOW") == [(w, (0x2F01, 0, 0, 640, 994))]


def test_x11_a_panel_on_one_monitor_leaves_the_other_whole(desktops, monkeypatch):
    _two_monitors_with_a_panel_on_the_first(desktops, monkeypatch)
    w = _active_window(desktops)
    desktops.move(w, 1500, 100)
    x11_desktop.act("snap left", None)
    assert desktops.requests(b"_NET_MOVERESIZE_WINDOW") == [(w, (0x2F01, 1280, 0, 960, 1080))]


def test_x11_a_fullscreen_window_is_taken_out_of_fullscreen_first(desktops, one_monitor):
    _active_window(desktops)
    x11_desktop.act("snap left", None)
    fullscreen = desktops.atom(b"_NET_WM_STATE_FULLSCREEN")
    assert any(data[:2] == (0, fullscreen) for _w, data in desktops.requests(b"_NET_WM_STATE"))


@pytest.mark.parametrize("missing", [b"_NET_MOVERESIZE_WINDOW", b"_NET_WM_STATE_FULLSCREEN"])
def test_x11_a_window_manager_without_move_requests_leaves_the_window_alone(
        desktops, one_monitor, monkeypatch, missing):
    """Nothing is sent unless everything can be: a window taken out of
    maximized and then not moved would be left half changed."""
    _active_window(desktops)
    real = x11_activate.Display.atom

    def atom(self, name):
        if name == missing:
            raise OSError("no window manager here takes it")
        return real(self, name)
    monkeypatch.setattr(x11_activate.Display, "atom", atom)
    with pytest.raises(OSError):
        x11_desktop.act("snap left", None)
    assert desktops.requests(b"_NET_WM_STATE") == []


def test_on_cinnamon_an_x_server_that_doesnt_answer_keeps_popups_away_without_waiting(
        monkeypatch):
    """Asked as a recording starts, on the thread that also reads the keys:
    waiting for a server that never answers would leave the key's release
    and Esc unread, and the recording running. Asked again meanwhile, it
    starts no second question that could hang as well."""
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "X-Cinnamon")
    release = threading.Event()
    asked = []

    def hung():
        asked.append(True)
        release.wait(10)

    monkeypatch.setattr(x11_activate, "open_display", hung)
    started = time.monotonic()
    try:
        assert [x11_desktop.popup_ends_show_desktop() for _ in range(3)] == [True] * 3
        assert time.monotonic() - started < 3
        assert len(asked) == 1
    finally:
        release.set()
        x11_desktop._asking[0].join(5)
    assert x11_desktop.popup_ends_show_desktop() is False      # answered again


def test_on_cinnamon_an_error_asking_is_logged_and_shows_the_popups(monkeypatch, caplog):
    """Uncaught on the question's thread, it reached only stderr, and the log
    blamed a slow X server while every popup stayed away."""
    import logging
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "X-Cinnamon")

    def broken():
        raise ValueError("a bug in the read")

    monkeypatch.setattr(x11_activate, "open_display", broken)
    with caplog.at_level(logging.INFO):
        assert x11_desktop.popup_ends_show_desktop() is False
    assert "ValueError" in caplog.text and "in time" not in caplog.text
