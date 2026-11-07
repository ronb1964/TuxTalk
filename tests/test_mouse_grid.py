"""Mouse by voice, step by step (mouse_grid.py): "mouse grid", a number and
"click" put the pointer on the spot, see it land there, and only then click.

The grid's windows are a stand-in that reads where conftest.py's recording
pointer was sent; nothing is drawn and nothing really moves or clicks.
"""
import logging
import types

import pytest
from evdev import ecodes as e

from conftest import FakeClock
from tuxtalk import (beeps, dictation_worker, focused_window, keyboard_output, modifier_hotkeys,
                     mouse_grid, mouse_grid_window, notices, service_tick, voice_actions,
                     virtual_pointer)
from tuxtalk import live_settings as live
from tuxtalk.mouse_grid_model import Rect
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


class Rig:
    """The stand-ins' settings and what came out. *offset* puts the landing
    that many pixels to the right; with *visible* off no motion comes at all
    (the pointer is off every grid window); *refused* is why no grid shows."""

    def __init__(self, pointer):
        self.pointer = pointer
        self.clock = FakeClock()
        self.refused = None
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
        right = max(s.x + s.width for s in self.screens)
        bottom = max(s.y + s.height for s in self.screens)
        sent, spots = {}, []
        for code, value in self.pointer.events:
            if code in (e.ABS_X, e.ABS_Y):
                sent[code] = value
            elif code == "SYN" and len(sent) == 2:
                spots.append(self._onto_a_screen(
                    round(sent[e.ABS_X] / virtual_pointer.MAX * right) + self.offset,
                    round(sent[e.ABS_Y] / virtual_pointer.MAX * bottom)))
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
    monkeypatch.setattr(mouse_grid, "time", rig.clock)
    monkeypatch.setattr(mouse_grid.GLib, "timeout_add_seconds", add_timer)
    monkeypatch.setattr(mouse_grid.GLib, "source_remove", remove_timer)
    monkeypatch.setattr(beeps, "beep", lambda on, freq, length: rig.beeps.append((freq, length)))
    monkeypatch.setattr(notices, "command_refused",
                        lambda label, sentence, beeps_on: rig.notes.append(label))
    monkeypatch.setattr(mouse_grid, "_session", None)
    yield rig
    mouse_grid.close("the test is over")


def act(action, number=None):
    return mouse_grid.act(MouseRequest(action, number), True)


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
    assert rig.buttons() == LEFT_CLICK * 2


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
    monkeypatch.setattr(modifier_hotkeys, "wait_while_held", lambda: lambda: wait)


def test_a_key_held_down_says_so_and_clicks_nothing(rig, monkeypatch):
    """Not "the mouse didn't work": the user can fix this by letting go."""
    act("grid")
    _key_stays_down(monkeypatch)
    assert act("click", 5) is False
    assert rig.buttons() == []
    assert rig.notes == ["Not clicked: a dictation key stayed down"]


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
