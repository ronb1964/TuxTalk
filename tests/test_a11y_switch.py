"""The desktop's accessibility switch (org.a11y.Status IsEnabled): turned on
only with the user's yes, kept on until they turn it off, and turned off
by TuxTalk only if TuxTalk turned it on (a11y_switch)."""
import types

import pytest
from gi.repository import GLib

from tuxtalk import a11y_switch


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    """The switch as the desktop holds it; None: no accessibility bus. What
    TuxTalk remembers goes in a file of its own under *tmp_path*."""
    monkeypatch.setattr(a11y_switch, "state_path", lambda: str(tmp_path / "a11y-switch.json"))
    state = types.SimpleNamespace(on=False, sets=[], screen_reader=False)

    def call(method, args):
        if state.on is None:
            raise GLib.Error("The name org.a11y.Bus was not provided by any .service files")
        if method == "Get":
            return (state.screen_reader if args.unpack()[1] == "ScreenReaderEnabled"
                    else state.on,)
        state.on = args.unpack()[2]
        state.sets.append(state.on)
        return ()
    monkeypatch.setattr(a11y_switch, "_call", call)
    return state


def remembered(answer="", ours=False):
    """What TuxTalk remembers, as if an earlier start had saved it."""
    a11y_switch._save({"answer": answer, "ours": ours})


def test_saying_yes_turns_it_on_and_remembers_it(desktop):
    assert a11y_switch.allow() is True
    assert desktop.on is True
    assert a11y_switch.answer() == "on" and a11y_switch._load()["ours"] is True


def test_saying_yes_when_it_was_already_on_doesnt_make_it_tuxtalks(desktop):
    desktop.on = True
    a11y_switch.allow()
    assert a11y_switch.answer() == "on" and a11y_switch._load()["ours"] is False
    assert desktop.sets == []


def test_it_is_turned_on_again_at_start_after_a_yes(desktop):
    remembered("on", ours=True)
    a11y_switch.keep_on()
    assert desktop.on is True


@pytest.mark.parametrize("answer", ["", "not_now"])
def test_without_a_yes_it_is_never_turned_on(desktop, answer):
    remembered(answer)
    a11y_switch.keep_on()
    assert desktop.sets == []


def test_not_asked_yet_until_the_user_answers(desktop):
    assert a11y_switch.answer() == ""
    a11y_switch.decline()
    assert a11y_switch.answer() == "not_now"


def test_turning_it_off_only_turns_off_what_tuxtalk_turned_on(desktop):
    desktop.on = True
    remembered("on", ours=False)        # a screen reader's, or the user's own
    a11y_switch.decline()
    assert desktop.on is True and a11y_switch.answer() == "not_now"
    remembered("on", ours=True)
    a11y_switch.decline()
    assert desktop.on is False and a11y_switch._load()["ours"] is False


def test_a_damaged_file_counts_as_not_asked(desktop, tmp_path):
    (tmp_path / "a11y-switch.json").write_text("{not json")
    assert a11y_switch.answer() == ""


def test_no_accessibility_bus_is_not_known_and_nothing_breaks(desktop):
    desktop.on = None
    assert a11y_switch.is_on() is None
    assert a11y_switch.allow() is False
    remembered("on", ours=True)
    a11y_switch.keep_on()
    a11y_switch.decline()


def test_a_screen_reader_started_since_keeps_it_on(desktop):
    """TuxTalk turned it on, then a screen reader started: off would cut it out."""
    a11y_switch.allow()
    desktop.screen_reader = True
    a11y_switch.decline()
    assert desktop.on is True
    assert a11y_switch.give_back() is True and desktop.on is True


def test_a_yes_that_cant_be_saved_leaves_the_switch_as_it_was(desktop, monkeypatch):
    """Nothing would remember TuxTalk turned it on, so nothing could turn it off."""
    def full_disk(state):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(a11y_switch, "_save", full_disk)
    assert a11y_switch.allow() is False
    assert desktop.on is False


def test_turning_it_off_says_whether_it_went_off(desktop, monkeypatch):
    a11y_switch.allow()
    monkeypatch.setattr(a11y_switch, "_set", lambda on: False)
    assert a11y_switch.decline() is False
    assert a11y_switch._load()["ours"] is True
