"""Mouse by voice, step by step (mouse_grid.py): "mouse grid", a number and
"click" put the pointer on the spot, see it land there, and only then click.

The grid's windows are a stand-in that reads where conftest.py's recording
pointer was sent; nothing is drawn and nothing really moves or clicks.
"""
import logging
import types

import pytest
from evdev import ecodes as e

from conftest import FakeClock, esc
from tuxtalk import (beeps, command_notices, desktop_zoom, dictation_worker, focused_window,
                     keyboard_output, modifier_hotkeys, mouse_grid, mouse_grid_window, notices,
                     service_tick, voice_actions, virtual_pointer)
from tuxtalk import live_settings as live
from tuxtalk.mouse_grid_model import Rect, close_enough
from tuxtalk.mouse_phrases import MouseRequest
from tuxtalk.virtual_keyboard import GaveUp

SCREEN = Rect(0, 0, 1920, 1080)
LEFT_CLICK = [(e.BTN_LEFT, 1), ("SYN", 0), (e.BTN_LEFT, 0), ("SYN", 0)]


class FakeWindows:
    """The grid's windows: each move of the recording pointer since they
    showed is a motion event when it changes where the pointer is, as on X."""

    def __init__(self, rig, on_real_click):
        self.rig = rig
        self.on_real_click = on_real_click
        self.showing = False
        self.drawn = []
        self.dots = []          # the click dot at each redraw
        self._seen = None       # (time, position) of the last motion
        self._moves = 0         # the moves already looked at
        self._at = None         # where the pointer is

    def show(self, grid):
        landings = self.rig.landings()
        self._moves, self._at = len(landings), (landings[-1] if landings else None)
        self.showing = True
        self.drawn.append(grid.region)
        return self.rig.refused

    def redraw(self, grid):
        self.drawn.append(grid.region)
        self.dots.append(grid.spot)

    def hide(self):
        self.showing = False
        self.rig.hidden_at.append(len(self.rig.pointer.events))

    def pointer_since(self, after):
        landings = self.rig.landings()
        for spot in landings[self._moves:]:
            if spot != self._at:
                self._seen = (self.rig.clock.now, spot)
            self._at = spot
        self._moves = len(landings)
        if self._seen and self._seen[0] > after:
            return self._seen[1]
        return None


class FakeMagnifier:
    """The desktop's zoom as desktop_zoom.find gives it, KDE's by default:
    whether it's zoomed now, how many restores really put it back, and each
    zoom_in and restore with where the pointer was sent last when it came.
    With *stuck* on, a restore can't put the zoom back (KWin not answering)."""

    name = "KDE zoom"
    view = desktop_zoom.View(3.0, "proportional", False)

    def __init__(self, rig, works=True):
        self.rig = rig
        self.works = works
        self.zoomed = False
        self.stuck = False
        self.put_back = 0
        self.calls = []

    def zoom_in(self):
        self.calls.append(("zoom_in", self.rig.landing()))
        self.while_zooming()
        self.zoomed = self.works
        return self.works

    def while_zooming(self):
        """What else happens while the desktop zooms in: nothing, unless a
        test says so."""

    def restore(self):
        self.calls.append(("restore", self.rig.landing()))
        if self.stuck:
            return False
        self.put_back += self.zoomed
        self.zoomed = False
        return True

    def count(self, call):
        return [name for name, _ in self.calls].count(call)


class Rig:
    """The stand-ins' settings and what came out. *offset* puts the landing
    that many pixels to the right; with *visible* off no motion comes at all
    (the pointer is off every grid window); *refused* is why no grid shows;
    *magnifier* is the desktop's zoom (None: a desktop without one)."""

    def __init__(self, pointer):
        self.pointer = pointer
        self.clock = FakeClock()
        self.refused = None
        self.magnifier = None
        self.visible = True
        self.offset = 0
        self.screens = [SCREEN]
        self.windows = []
        self.hidden_at = []      # how many pointer events had gone out at each hide
        self.timers = []         # [id, callback, args, removed]
        self.beeps = []
        self.notes = []

    def landings(self):
        """Where each move sent the pointer, in screen pixels, in order; none
        while the pointer is off the grid (*visible* off)."""
        if not self.visible:
            return []
        left, top = min(s.x for s in self.screens), min(s.y for s in self.screens)
        right = max(s.x + s.width for s in self.screens)
        bottom = max(s.y + s.height for s in self.screens)
        sent, spots = {}, []
        for code, value in self.pointer.events:
            if code in (e.ABS_X, e.ABS_Y):
                sent[code] = value
            elif code == "SYN" and len(sent) == 2:
                spots.append(self._onto_a_screen(
                    left + round(sent[e.ABS_X] / virtual_pointer.MAX * (right - left))
                    + self.offset,
                    top + round(sent[e.ABS_Y] / virtual_pointer.MAX * (bottom - top))))
        return spots

    def _onto_a_screen(self, x, y):
        """Where the desktop puts a pointer sent off every screen: the nearest
        point of the nearest screen."""
        def clamped(s):
            return (min(max(x, s.x), s.x + s.width - 1), min(max(y, s.y), s.y + s.height - 1))
        return min((clamped(s) for s in self.screens),
                   key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)

    def landing(self):
        """Where the pointer was sent last, in screen pixels, or None."""
        landings = self.landings()
        return landings[-1] if landings else None

    def _first_press(self):
        return next((i for i, ev in enumerate(self.pointer.events)
                     if ev[0] in (e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE)),
                    len(self.pointer.events))

    def buttons(self):
        """The button events and the SYN after each, from the first press."""
        return self.pointer.events[self._first_press():]

    def sent_before_the_click(self):
        """The last ABS_X and ABS_Y sent before the first button press."""
        sent = {code: value for code, value in self.pointer.events[:self._first_press()]
                if code in (e.ABS_X, e.ABS_Y)}
        return sent.get(e.ABS_X), sent.get(e.ABS_Y)

    def fire_timer(self):
        live_timers = [t for t in self.timers if not t[3]]
        assert len(live_timers) == 1
        _id, callback, args, _removed = live_timers[0]
        return callback(*args)


@pytest.fixture
def rig(pointer, monkeypatch):
    """The grid with stand-in windows on one 1920x1080 screen, its waits on a
    clock of its own, its 60 s timer kept to be fired by hand, and the beeps
    and notes it gave."""
    rig = Rig(pointer)

    def windows(on_real_click):
        made = FakeWindows(rig, on_real_click)
        rig.windows.append(made)
        return made

    def add_timer(seconds, callback, *args):
        assert seconds == mouse_grid.TIMEOUT_S
        rig.timers.append([len(rig.timers) + 1, callback, args, False])
        return len(rig.timers)

    def remove_timer(source_id):
        rig.timers[source_id - 1][3] = True
        return True

    monkeypatch.setattr(mouse_grid_window, "GridWindows", windows)
    monkeypatch.setattr(mouse_grid_window, "monitors", lambda: list(rig.screens))
    monkeypatch.setattr(desktop_zoom, "find", lambda: rig.magnifier)
    monkeypatch.setattr(mouse_grid, "time", rig.clock)
    monkeypatch.setattr(mouse_grid.GLib, "timeout_add_seconds", add_timer)
    monkeypatch.setattr(mouse_grid.GLib, "source_remove", remove_timer)
    monkeypatch.setattr(beeps, "beep", lambda on, freq, length: rig.beeps.append((freq, length)))
    monkeypatch.setattr(notices, "command_refused",
                        lambda label, sentence, beeps_on: rig.notes.append(label))
    monkeypatch.setattr(mouse_grid, "_session", None)
    yield rig
    mouse_grid.close("the test is over")


def act(action, number=None, direction=None):
    return mouse_grid.act(MouseRequest(action, number, direction=direction), True)


def test_grid_number_click_clicks_the_cells_centre(rig):
    assert act("grid") is True and mouse_grid.showing()
    assert act("zoom", 5) is True
    assert act("click") is True
    # Cell 5 of a 1920x1080 screen is its middle: half of the tablet's range.
    assert rig.sent_before_the_click() == (16384, 16384)
    assert rig.buttons() == LEFT_CLICK
    assert not mouse_grid.showing() and not rig.windows[0].showing
    assert rig.beeps == [beeps.READY_BEEP]


def test_the_pointer_moves_again_after_the_grid_goes_before_the_click(rig):
    """COSMIC sends a press to the window under the pointer only once the
    pointer moves after the grid's window went: without that, the click is lost."""
    act("grid")
    act("click", 5)
    since_hidden = rig.pointer.events[rig.hidden_at[0]:rig._first_press()]
    across = [value for code, value in since_hidden if code == e.ABS_X]
    assert len(across) == 2 and across[0] != across[1] == 16384
    assert rig.sent_before_the_click() == (16384, 16384)


def test_a_move_moves_again_after_the_grid_goes(rig):
    """The same for "move": the app under the grid gets its hover."""
    act("grid")
    act("move", 5)
    across = [value for code, value in rig.pointer.events[rig.hidden_at[0]:]
              if code == e.ABS_X]
    assert len(across) == 2 and across[-1] == 16384


def test_click_and_a_number_clicks_that_cell_at_once(rig):
    act("grid")
    assert act("right click", 1) is True
    # Cell 1's centre: a sixth across and down.
    assert rig.sent_before_the_click() == (round(32767 / 6), round(32767 / 6))
    assert rig.buttons()[0] == (e.BTN_RIGHT, 1)


def test_double_click_clicks_twice(rig):
    act("grid")
    act("double click", 9)
    # The pointer is put back on the spot before the second, too.
    presses = [ev for ev in rig.buttons() if ev[0] == e.BTN_LEFT]
    assert presses == [(e.BTN_LEFT, 1), (e.BTN_LEFT, 0)] * 2


def test_move_puts_the_pointer_there_without_clicking(rig, caplog):
    act("grid")
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert act("move", 3) is True
    assert rig.landing() == (1600, 180) and rig.buttons() == []
    assert not mouse_grid.showing()
    assert "Mouse grid: moved to cell 3, level 1, at 1600,180" in caplog.text


def test_the_log_has_the_cell_and_where_but_nothing_else(rig, caplog):
    act("grid")
    act("zoom", 5)
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        act("click")
    assert "Mouse grid: clicked the middle of cell 5, level 2, at 960,540" in caplog.text


def test_nothing_is_clicked_when_the_pointer_lands_elsewhere(rig, caplog):
    """A click in the wrong place is worse than none: 10 px off, nothing is
    clicked, the note says so and the grid goes."""
    act("grid")
    rig.offset = 10
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert act("click", 5) is False
    assert rig.buttons() == []
    assert rig.notes == ["Can't place the pointer exactly on this screen setup"]
    assert not mouse_grid.showing() and not rig.windows[0].showing
    assert "sent to 960,540 landed at 970,540" in caplog.text


def test_a_pointer_two_pixels_off_still_clicks(rig):
    act("grid")
    rig.offset = 2
    assert act("click", 5) is True and rig.buttons() == LEFT_CLICK


def test_a_desktop_that_maps_the_tablet_differently_is_fitted(rig):
    """The pointer lands 40 px right of the plain mapping everywhere: the fit
    measured when the grid opened corrects for it, and the click lands."""
    rig.offset = 40
    act("grid")
    assert act("click", 5) is True
    assert rig.landing() == (960, 540) and rig.buttons() == LEFT_CLICK


def test_a_calibration_that_never_arrives_refuses(rig):
    rig.visible = False
    assert act("grid") is False
    assert rig.notes == ["Can't place the pointer exactly on this screen setup"]
    assert not mouse_grid.showing() and not rig.windows[0].showing


def test_a_pointer_already_on_the_spot_is_still_seen_to_arrive(rig):
    """A move onto the spot the pointer is on sends no motion: the grid moves
    it a pixel aside and back to see it."""
    virtual_pointer.move_to(1 / 6, 1 / 6)       # cell 1's centre, where the first move goes
    assert act("grid") is True and rig.notes == []
    aside = round((1 / 6 + 1 / SCREEN.width) * virtual_pointer.MAX)
    assert (e.ABS_X, aside) in rig.pointer.events


@pytest.mark.parametrize("why, note", [
    ("grid_needs_x", "The mouse grid needs X (XWayland) here"),
    ("grid_needs_compositing", "The mouse grid needs see-through windows here")])
def test_a_grid_that_cant_show_says_why_and_moves_nothing(rig, why, note):
    rig.refused = why
    assert act("grid") is False
    assert rig.notes == [note]
    assert not mouse_grid.showing() and rig.pointer.events == []


def test_a_number_not_shown_keeps_the_grid(rig):
    act("grid")
    assert act("zoom", 12) is False
    assert rig.notes == ["No cell 12 here"] and mouse_grid.showing()


def test_the_smallest_cell_says_so_and_stays(rig):
    act("grid")
    for _ in range(3):              # cells of 360, 120, 40 and 13 px high
        assert act("zoom", 5) is True
    assert act("zoom", 5) is False
    assert rig.notes == ["That's as small as it goes"] and mouse_grid.showing()


def test_click_with_two_screens_and_no_number_asks_for_one(rig):
    rig.screens = [SCREEN, Rect(1920, 0, 1920, 1080)]
    act("grid")
    assert act("click") is False
    assert rig.notes == ["Say a number first"] and mouse_grid.showing()


def test_the_second_screens_cells_go_on_from_ten(rig):
    rig.screens = [SCREEN, Rect(1920, 0, 1920, 1080)]
    act("grid")
    assert act("click", 14) is True
    assert rig.landing() == (1920 + 960, 540) and rig.buttons() == LEFT_CLICK


def test_two_screens_that_dont_fill_their_box_still_click(rig):
    """A 4K screen with a 1080p one to its right, tops aligned: a calibration
    spot below the small screen came back held at its edge, and the fit from
    it refused every click."""
    rig.screens = [Rect(0, 0, 3840, 2160), Rect(3840, 0, 1920, 1080)]
    assert act("grid") is True
    assert act("click", 14) is True and rig.notes == []
    assert rig.landing() == (3840 + 960, 540) and rig.buttons() == LEFT_CLICK


def test_staggered_screens_on_a_desktop_that_maps_differently_still_click(rig):
    """The first screen's cell 1 and the second's cell 9 are at the same
    height there, and a fit needs two spots apart on both axes: every grid
    refused to open."""
    rig.screens = [Rect(0, 0, 1280, 1080), Rect(1280, -1620, 3840, 2160)]
    rig.offset = 40
    assert act("grid") is True
    assert act("click", 14) is True and rig.notes == []
    # Fitted on one screen and used across both, the landing's whole pixels
    # can put it a pixel or two off, inside what a click allows.
    assert close_enough(rig.landing(), (1280 + 1920, -1620 + 1080))
    assert rig.buttons() == LEFT_CLICK


def test_the_grid_closes_by_itself_after_a_minute(rig, caplog):
    act("grid")
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert rig.fire_timer() is False         # a one-off timer
    assert not mouse_grid.showing() and not rig.windows[0].showing
    assert rig.beeps == [beeps.CANCEL_BEEP]
    assert "Mouse grid closed: no grid command for 60 s" in caplog.text


def test_each_grid_command_starts_the_minute_again(rig):
    act("grid")
    act("zoom", 5)
    assert [removed for *_, removed in rig.timers] == [True, False]


def test_cancel_closes_with_the_cancel_beep(rig):
    act("grid")
    assert act("cancel") is True
    assert not mouse_grid.showing() and rig.beeps == [beeps.CANCEL_BEEP]
    assert [removed for *_, removed in rig.timers] == [True]


def test_a_real_mouse_click_closes_the_grid(rig):
    act("grid")
    rig.windows[0].on_real_click()
    assert not mouse_grid.showing() and not rig.windows[0].showing


def test_esc_closes_the_grid(rig):
    """For someone at the keyboard who no longer wants it: no need to say
    "cancel" or wait the minute."""
    act("grid")
    esc()
    assert not mouse_grid.showing() and not rig.windows[0].showing


def test_mouse_grid_again_starts_a_fresh_grid(rig):
    act("grid")
    act("zoom", 5)
    act("grid")
    assert not rig.windows[0].showing and rig.windows[1].drawn == [None]


def test_left_click_without_a_grid_clicks_where_the_pointer_is(rig):
    assert act("click") is True
    assert rig.pointer.events == LEFT_CLICK and rig.beeps == [beeps.READY_BEEP]


@pytest.mark.parametrize("action, number", [("click", None), ("right click", None),
                                            ("click", 5), ("zoom", 5), ("move", None),
                                            ("cancel", None)])
def test_a_grid_command_whose_grid_went_does_nothing(rig, action, number):
    """Heard while the grid showed, done after it closed (the minute ran out,
    a real click): never a click wherever the pointer was left."""
    request = MouseRequest(action, number, in_grid=True)
    assert mouse_grid.act(request, True) is False
    assert rig.pointer.events == [] and rig.beeps == [beeps.CANCEL_BEEP]


def test_a_grid_that_closes_as_its_command_starts_does_nothing(rig, monkeypatch):
    act("grid")
    call = mouse_grid.gtk_loop.call

    def timeout_first(fn, *args):
        if fn is mouse_grid._restart_timer:
            rig.fire_timer()
        return call(fn, *args)
    monkeypatch.setattr(mouse_grid.gtk_loop, "call", timeout_first)
    assert act("zoom", 5) is False
    assert rig.windows[0].drawn == [None]       # never zoomed


def _key_stays_down(monkeypatch):
    """A dictation key held past modifier_hotkeys' limit: the wait gives up."""
    def wait():
        raise GaveUp("key_held")
    monkeypatch.setattr(modifier_hotkeys, "wait_while_held", lambda **_: lambda: wait)


def test_a_key_held_down_says_so_and_clicks_nothing(rig, monkeypatch):
    """Not "the mouse didn't work": the user can fix this by letting go."""
    act("grid")
    _key_stays_down(monkeypatch)
    assert act("click", 5) is False
    assert rig.buttons() == []
    assert rig.notes == ["Not clicked: a dictation key stayed down"]
    assert not mouse_grid.showing() and not rig.windows[0].showing


def _key_held_until_waited(monkeypatch, while_held):
    """A dictation key down when the click comes, up once *while_held* has
    run during the wait for it."""
    held = [True]

    def wait():
        while_held()
        held[0] = False
    monkeypatch.setattr(modifier_hotkeys, "wait_while_held",
                        lambda **_: lambda: wait if held[0] else None)


def _sent_before_each_press(rig):
    """The last ABS_X and ABS_Y sent before each button press."""
    sent, places = {}, []
    for code, value in rig.pointer.events:
        if code in (e.ABS_X, e.ABS_Y):
            sent[code] = value
        elif code == e.BTN_LEFT and value == 1:
            places.append((sent.get(e.ABS_X), sent.get(e.ABS_Y)))
    return places


def test_a_click_waits_for_a_held_key_with_the_grid_up_then_clicks_the_spot(rig, monkeypatch):
    """The grid shows while it waits, so the user sees why nothing happened
    yet; the real mouse moving the pointer meanwhile doesn't move the click."""
    act("grid")
    showing = []

    def mouse_moved_meanwhile():
        showing.append(rig.windows[0].showing)
        virtual_pointer.move_to(0.01, 0.01)
    _key_held_until_waited(monkeypatch, mouse_moved_meanwhile)
    assert act("click", 5) is True
    assert showing == [True]
    assert _sent_before_each_press(rig) == [(16384, 16384)]
    assert rig.buttons() == LEFT_CLICK


def test_a_real_click_while_it_waits_for_a_held_key_cancels(rig, monkeypatch):
    act("grid")
    _key_held_until_waited(monkeypatch, rig.windows[0].on_real_click)
    assert act("click", 5) is False
    assert rig.buttons() == [] and not mouse_grid.showing()


def test_both_clicks_of_a_double_click_land_on_the_spot(rig, monkeypatch):
    """The real mouse moved between the two: the second click still goes
    where the grid was asked to click."""
    act("grid")
    sleep = rig.pointer.clock.sleep

    def mouse_moved_in_the_gap(seconds):
        if seconds == virtual_pointer.DOUBLE_CLICK_GAP_S:
            virtual_pointer.move_to(0.9, 0.9)
        sleep(seconds)
    monkeypatch.setattr(rig.pointer.clock, "sleep", mouse_moved_in_the_gap)
    assert act("double click", 5) is True
    assert _sent_before_each_press(rig) == [(16384, 16384)] * 2


@pytest.mark.parametrize("action", ["move", "click"])
def test_esc_as_the_grid_goes_moves_and_clicks_nothing(rig, monkeypatch, caplog, action):
    act("grid")
    escaped = []
    monkeypatch.setattr(keyboard_output, "esc_cancelled", lambda: bool(escaped))
    sleep = rig.clock.sleep

    def esc_while_the_grid_goes(seconds):
        if seconds == mouse_grid.GONE_S:
            escaped.append(True)
        sleep(seconds)
    monkeypatch.setattr(rig.clock, "sleep", esc_while_the_grid_goes)
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert act(action, 5) is False
    assert rig.pointer.events[rig.hidden_at[0]:] == []
    assert "moved to" not in caplog.text and rig.beeps == []


def test_a_pointer_that_cant_be_put_back_clicks_nothing(rig, monkeypatch):
    act("grid")
    move_to = virtual_pointer.move_to
    monkeypatch.setattr(virtual_pointer, "move_to",
                        lambda fx, fy: not rig.hidden_at and move_to(fx, fy))
    assert act("click", 5) is False
    assert rig.buttons() == [] and rig.notes == ["The mouse didn't work this time"]


def test_the_held_key_note_fits_every_dictation_key():
    """Left Ctrl+Space is a dictation key too: the note mustn't name keys
    its user never touched."""
    sentence = command_notices.NOTES["click_key_held"][1]
    assert "dictation key" in sentence and "Right" not in sentence


def test_a_plain_click_with_a_key_held_down_says_so(rig, monkeypatch):
    _key_stays_down(monkeypatch)
    assert act("right click") is False
    assert rig.pointer.events == []
    assert rig.notes == ["Not clicked: a dictation key stayed down"]


def test_esc_before_a_plain_click_clicks_nothing(rig, monkeypatch):
    monkeypatch.setattr(keyboard_output, "esc_cancelled", lambda: True)
    assert act("click") is False
    assert rig.pointer.events == [] and rig.notes == [] and rig.beeps == []


def test_esc_between_the_clicks_of_a_double_click_stops_the_second(rig, monkeypatch):
    act("grid")
    escaped = []
    monkeypatch.setattr(keyboard_output, "esc_cancelled", lambda: bool(escaped))
    sleep = rig.pointer.clock.sleep

    def gap_then_esc(seconds):
        if seconds == virtual_pointer.DOUBLE_CLICK_GAP_S:
            escaped.append(True)
        sleep(seconds)
    monkeypatch.setattr(rig.pointer.clock, "sleep", gap_then_esc)
    assert act("double click", 5) is False
    assert rig.buttons() == LEFT_CLICK and rig.notes == []


def test_a_pointer_that_cant_be_made_says_so(rig, monkeypatch):
    monkeypatch.setattr(virtual_pointer, "move_to", lambda fx, fy: False)
    assert act("grid") is False
    assert rig.notes == ["The mouse didn't work this time"]
    assert not mouse_grid.showing()


@pytest.fixture
def grid_on_screen(rig, monkeypatch):
    """Voice commands on and a grid showing."""
    monkeypatch.setattr(live, "open_apps", True)
    act("grid")
    return rig


def test_a_grid_command_ends_a_hands_free_recording(grid_on_screen):
    ongoing = types.SimpleNamespace(cancelled=False)
    assert voice_actions.run("Five.", False, ongoing) is True
    assert ongoing.cancelled is True


def test_another_dictation_closes_the_grid_and_is_handled_as_usual(grid_on_screen, chat, say):
    say("Hello there.")
    assert [text for text, _ in chat[2]] == ["Hello there."]
    assert not mouse_grid.showing()


def test_a_number_with_the_grid_up_is_a_command_not_text(grid_on_screen, actions_on, chat, say):
    say("Five.")
    assert chat[2] == [] and mouse_grid.showing()
    assert grid_on_screen.windows[0].drawn[-1] == Rect(640, 360, 640, 360)


def test_literal_and_a_grid_word_types_it_and_closes_the_grid(grid_on_screen, actions_on, chat,
                                                              say):
    say("Literal five.")
    assert [text for text, _ in chat[2]] == ["Five."]
    assert not mouse_grid.showing()


@pytest.mark.parametrize("clicks", [["Left click."], ["Mouse grid.", "Click five."]])
def test_a_click_forgets_the_last_dictation_for_undo(rig, actions_on, chat, say, keyboard,
                                                     monkeypatch, clicks):
    """The click may have put the cursor in other text: "delete last
    sentence" must not erase from there."""
    monkeypatch.setattr(live, "press_enter_after", False)
    say("Hello there.")
    for words in clicks:
        say(words)
    say("Delete last sentence.")
    assert rig.buttons() == LEFT_CLICK
    assert 14 not in keyboard.typed_keys()               # no Backspace


@pytest.fixture
def in_a_password_field(rig, actions_on, chat, monkeypatch):
    """Voice commands on and the cursor in a password field; the refusals
    are counted."""
    refused = []
    monkeypatch.setattr(focused_window, "is_password", lambda: True)
    monkeypatch.setattr(notices, "password_field_refused", lambda beeps_on: refused.append(1))
    return refused


def test_the_mouse_works_from_a_password_field(rig, in_a_password_field, chat, say, caplog):
    """Mouse commands type nothing, so they can click out of a password
    field; anything else said there is still neither typed nor logged."""
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        say("Left click.")
        say("Mouse grid.")
        say("Five.")
        say("Cancel.")
        say("Swordfish forever.")
    assert rig.pointer.events[:4] == LEFT_CLICK          # the click, before the grid's moves
    assert rig.windows[0].drawn == [None, Rect(640, 360, 640, 360)]
    assert not mouse_grid.showing()
    assert chat[2] == [] and in_a_password_field == [1]
    assert "wordfish" not in caplog.text


def test_a_grid_word_whose_grid_went_meanwhile_isnt_typed_in_a_password_field(
        grid_on_screen, in_a_password_field, chat, say, monkeypatch):
    """Let through as a grid command, then the minute ran out before it ran:
    not typed, and not handed to the AI either."""
    from tuxtalk import dictation_text
    shaped = []
    monkeypatch.setattr(dictation_text, "prepare_text", lambda *a, **k: shaped.append(a) or "x")
    monkeypatch.setattr(voice_actions, "close_grid_unless_for_it",
                        lambda raw: mouse_grid.close("the minute ran out"))
    say("Five.")
    assert chat[2] == [] and shaped == [] and in_a_password_field == [1]


def test_a_voice_edit_closes_the_grid(grid_on_screen, monkeypatch):
    """Shift and the dictation key: the edit's recording goes to voice
    editing, not through the voice commands, and closes the grid too."""
    from tuxtalk import pipeline, recording, voice_edit
    from tuxtalk import recording_state as rs
    edited = []
    monkeypatch.setattr(rs.state.current, "edit_mode", True)
    monkeypatch.setattr(recording, "finish_capture", lambda rec, beeps, notify: ([b""], 16000))
    monkeypatch.setattr(voice_edit, "transcribe_and_edit",
                        lambda *a: edited.append(mouse_grid.showing()))
    pipeline.stop_recording(False, False, False)
    dictation_worker.drain(5)
    assert edited == [False] and not grid_on_screen.windows[0].showing


def test_quitting_with_a_grid_open_closes_it(grid_on_screen, monkeypatch):
    monkeypatch.setattr(dictation_worker, "drain", lambda limit: True)
    monkeypatch.setattr(service_tick, "_leave_the_clipboard", lambda: None)
    with pytest.raises(SystemExit):
        service_tick._quit(types.SimpleNamespace(), types.SimpleNamespace())
    assert not mouse_grid.showing() and not grid_on_screen.windows[0].showing


# The desktop's own zoom


@pytest.fixture
def magnifier(rig):
    """A desktop whose zoom TuxTalk can drive (KDE's, by default)."""
    rig.magnifier = FakeMagnifier(rig)
    return rig.magnifier


def test_the_first_number_zooms_the_desktop_even_on_a_big_screen(rig, magnifier, caplog):
    """On 4K the first number's cells are still 427x240, but the desktop zooms
    in all the same, and a second number re-aims it rather than zooming again."""
    rig.screens = [Rect(0, 0, 3840, 2160)]
    act("grid")
    assert magnifier.calls == []
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert act("zoom", 5) is True
    assert magnifier.zoomed and magnifier.count("zoom_in") == 1
    assert "Mouse grid: zoomed the desktop in 3.0x (KDE zoom, proportional)" in caplog.text
    assert act("zoom", 5) is True
    assert magnifier.count("zoom_in") == 1          # re-aimed, not zoomed again


def test_no_magnifier_means_no_zoom_and_the_grid_still_works(rig):
    act("grid")
    moved = len(rig.pointer.events)
    assert act("zoom", 5) is True
    assert len(rig.pointer.events) == moved         # nothing parked
    assert act("click") is True
    assert rig.landing() == (960, 540) and rig.buttons() == LEFT_CLICK


@pytest.mark.parametrize("screens, number, monitor", [
    ([SCREEN], 1, SCREEN),
    ([SCREEN, Rect(1920, 0, 1920, 1080)], 14, Rect(1920, 0, 1920, 1080))])
def test_the_pointer_parks_where_the_part_shows_centred(rig, magnifier, screens, number,
                                                        monitor):
    """Proportional tracking: the pointer waits where the view shows the part
    centred on its own monitor, not on the part itself; there it goes a pixel
    aside and back once zoomed, for the desktop to aim the view."""
    rig.screens = screens
    act("grid")
    act("zoom", number)
    grid = mouse_grid._session.grid
    spot = desktop_zoom.parking_spot(grid.region, monitor, grid.bounds, magnifier.view)
    assert close_enough(rig.landing(), spot, tolerance=1)
    assert magnifier.calls == [("zoom_in", rig.landing())]
    aside, back = rig.landings()[-2:]
    assert back == rig.landing() and aside == (back[0] + 1, back[1])


def test_cell_one_parks_the_pointer_near_the_corner_on_kde(rig, magnifier):
    """The spike's check: the pointer at 0,0 and a 3x zoom show cell 1 filling
    the screen; it waits a few pixels in from the hot corner."""
    act("grid")
    act("zoom", 1)
    margin = desktop_zoom.EDGE_MARGIN_PX
    assert rig.landing() == (margin, margin)


def test_a_second_number_re_aims_without_zooming_again(rig, magnifier, caplog):
    act("grid")
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        act("zoom", 5)
        assert act("zoom", 1) is True
    grid = mouse_grid._session.grid
    spot = desktop_zoom.parking_spot(grid.region, SCREEN, grid.bounds, magnifier.view)
    assert close_enough(rig.landing(), spot, tolerance=1)
    assert magnifier.count("zoom_in") == 1 and magnifier.zoomed
    assert caplog.text.count("zoomed the desktop in") == 1


def test_the_zoom_goes_back_before_the_click_moves_the_pointer(rig, magnifier):
    """The click comes on the normal view, with no pan between: the zoom is
    put back while the pointer still waits at its parking spot."""
    act("grid")
    act("zoom", 5)
    act("zoom", 1)
    parked = rig.landing()
    assert act("click") is True
    assert magnifier.calls[-1] == ("restore", parked) and magnifier.put_back == 1
    assert rig.landing() == _last_dot(rig) and rig.buttons() == LEFT_CLICK


def _last_dot(rig):
    """The last dot the grid drew, as whole pixels."""
    x, y = rig.windows[-1].dots[-1]
    return (round(x), round(y))


def _pointer_fails(rig, monkeypatch):
    monkeypatch.setattr(virtual_pointer, "move_to", lambda fx, fy: False)
    act("zoom", 5)


def _cant_place(rig, monkeypatch):
    rig.offset = 10
    act("click", 5)


@pytest.mark.parametrize("closing", [
    lambda rig, mp: act("cancel"),
    lambda rig, mp: rig.fire_timer(),
    lambda rig, mp: rig.windows[0].on_real_click(),
    lambda rig, mp: esc(),
    lambda rig, mp: mouse_grid.close("another dictation"),
    lambda rig, mp: mouse_grid.close("a voice edit"),
    lambda rig, mp: mouse_grid.close("quitting"),
    lambda rig, mp: act("grid"),
    _cant_place,
    _pointer_fails,
    lambda rig, mp: act("click"),
    lambda rig, mp: act("move")],
    ids=["cancel", "timeout", "real click", "Esc", "another dictation", "voice edit",
         "quitting", "new grid", "cant place", "pointer failed", "click", "move"])
def test_every_way_the_grid_closes_puts_the_zoom_back(rig, magnifier, monkeypatch, caplog,
                                                      closing):
    act("grid")
    act("zoom", 5)
    assert magnifier.zoomed
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        closing(rig, monkeypatch)
    assert not rig.windows[0].showing
    assert not magnifier.zoomed and magnifier.put_back == 1
    assert caplog.text.count("Mouse grid: put the desktop zoom back") == 1


def test_a_grid_that_closes_while_the_desktop_zooms_in_puts_the_zoom_back(rig, magnifier,
                                                                         caplog):
    """A real click as the zoom comes on: the close found nothing to put
    back yet, so the zoom that came on after it is put back too."""
    act("grid")
    magnifier.while_zooming = rig.windows[0].on_real_click
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        act("zoom", 5)
    assert not mouse_grid.showing()
    assert not magnifier.zoomed and magnifier.put_back == 1
    assert caplog.text.count("Mouse grid: put the desktop zoom back") == 1


def test_a_failed_zoom_is_logged_and_the_grid_goes_on(rig, monkeypatch, caplog):
    rig.magnifier = FakeMagnifier(rig, works=False)
    act("grid")
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert act("zoom", 5) is True
        assert act("zoom", 1) is True
    assert rig.magnifier.count("zoom_in") == 1          # tried once, not at every number
    assert caplog.text.count("the desktop didn't zoom in") == 1
    assert "zoomed the desktop in" not in caplog.text
    assert mouse_grid.showing() and act("click") is True and rig.buttons() == LEFT_CLICK


def test_a_failed_zoom_that_cant_be_undone_stays_owed_and_no_click_is_made(rig, caplog):
    """Part of a zoom went in and neither its own undo nor the clean-up took
    it out: the desktop is still zoomed, so the click refuses, as for any
    zoom that won't go back, rather than going on as if nothing were left."""
    rig.magnifier = FakeMagnifier(rig, works=False)
    rig.magnifier.stuck = True
    act("grid")
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert act("zoom", 5) is True
        sent = len(rig.pointer.events)
        assert act("click") is False
    assert rig.pointer.events[sent:] == [] and rig.buttons() == []
    assert rig.notes == [command_notices.NOTES["zoom_not_back"][0]]
    assert rig.magnifier.count("zoom_in") == 1
    assert "Mouse grid: put the desktop zoom back" not in caplog.text


def test_a_nudge_moves_the_dot_and_the_click_lands_on_it(rig, magnifier, caplog):
    act("grid")
    act("zoom", 5)
    sent = len(rig.pointer.events)
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert act("nudge", 3, "left") is True
    assert rig.windows[0].dots[-1] == (945, 540)
    assert len(rig.pointer.events) == sent              # the pointer stays parked
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert act("click") is True
    assert rig.landing() == (945, 540) and rig.buttons() == LEFT_CLICK
    assert "Mouse grid: moved the dot left 3, to 945,540" in caplog.text
    assert "clicked the dot in cell 5, level 2, at 945,540" in caplog.text


def test_a_nudge_before_any_number_asks_for_one(rig):
    act("grid")
    assert act("nudge", 1, "up") is False
    assert rig.notes == ["Say a number first"] and mouse_grid.showing()
    assert rig.windows[0].dots == []


# The zoom that can't be put back, and closes in the middle of a zoom


@pytest.mark.parametrize("action", ["click", "move"])
def test_with_the_zoom_stuck_on_nothing_is_clicked_or_moved(rig, magnifier, caplog, action):
    """A click on a desktop still zoomed in would land where the user can't
    see it: the grid closes and says why instead."""
    act("grid")
    act("zoom", 5)
    magnifier.stuck = True
    sent = len(rig.pointer.events)
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        assert act(action) is False
    assert rig.pointer.events[sent:] == []              # not moved, not clicked
    assert not mouse_grid.showing() and not rig.windows[0].showing
    assert rig.notes == [command_notices.NOTES["zoom_not_back"][0]]
    assert "Mouse grid: couldn't put the desktop zoom back" in caplog.text
    assert "Mouse grid: put the desktop zoom back" not in caplog.text


def test_a_close_that_cant_put_the_zoom_back_says_so_in_the_log(rig, magnifier, caplog):
    act("grid")
    act("zoom", 5)
    magnifier.stuck = True
    with caplog.at_level(logging.INFO, logger="tuxtalk"):
        act("cancel")
    assert "WARNING" in caplog.text and "couldn't put the desktop zoom back" in caplog.text
    assert "Mouse grid: put the desktop zoom back" not in caplog.text


def test_a_close_just_before_the_pointer_parks_leaves_the_pointer_alone(rig, magnifier,
                                                                         monkeypatch):
    """Esc or a real click right as a number is acted on: the pointer the
    user just took isn't moved, and the desktop doesn't zoom."""
    act("grid")
    view = magnifier.view
    monkeypatch.setattr(FakeMagnifier, "view", property(
        lambda self: mouse_grid.close("Esc") or view))
    sent = len(rig.pointer.events)
    assert act("zoom", 5) is True
    assert rig.pointer.events[sent:] == []
    assert magnifier.count("zoom_in") == 0


def test_a_close_while_the_zoom_settles_leaves_the_pointer_alone(rig, magnifier, monkeypatch):
    """The pointer goes a pixel aside and back once the view has settled,
    but not if the grid closed meanwhile: it's the user's pointer again."""
    act("grid")
    sleep = rig.clock.sleep

    def closing_sleep(seconds):
        if seconds == mouse_grid.ZOOM_SETTLE_S:
            mouse_grid.close("Esc")
        sleep(seconds)
    monkeypatch.setattr(rig.clock, "sleep", closing_sleep)
    act("zoom", 5)
    parked = rig.landing()
    assert magnifier.calls == [("zoom_in", parked), ("restore", parked)]
    assert rig.landing() == parked and rig.landings().count(parked) == 1


def test_the_desktops_zoom_is_looked_up_before_the_grid_shows(rig, monkeypatch):
    """On KDE that's a D-Bus call: done while the grid shows, a real click
    on it then would find no grid to close and leave it up."""
    shown_at_lookup = []
    monkeypatch.setattr(desktop_zoom, "find",
                        lambda: shown_at_lookup.append(bool(rig.windows)) or None)
    act("grid")
    assert shown_at_lookup == [False]


def test_a_real_click_as_soon_as_the_grid_shows_closes_it(rig, monkeypatch):
    real_pointer_since = FakeWindows.pointer_since

    def click_then_look(self, after):
        if self.showing:
            self.on_real_click()
        return real_pointer_since(self, after)
    monkeypatch.setattr(FakeWindows, "pointer_since", click_then_look)
    assert act("grid") is False
    assert not mouse_grid.showing() and not rig.windows[0].showing
