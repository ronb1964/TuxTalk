"""The one question before TuxTalk turns on the desktop's accessibility
switch, and the message after a yes saying an app already open may need
a restart
(startup_notices.offer_a11y_switch)."""
import types

import pytest
from gi.repository import Gtk

from tuxtalk import a11y_switch, first_run_marker, startup_notices


@pytest.fixture
def switch(monkeypatch, tmp_path):
    """The desktop's switch (on: None for no accessibility bus) and the
    file TuxTalk remembers the answer in, both stand-ins."""
    monkeypatch.setattr(a11y_switch, "state_path", lambda: str(tmp_path / "a11y-switch.json"))
    state = types.SimpleNamespace(on=False, screen_reader=False)
    monkeypatch.setattr(a11y_switch, "_status",
                        lambda name: state.on if name == "IsEnabled" else state.screen_reader)

    def set_on(on):
        state.on = on
        return True
    monkeypatch.setattr(a11y_switch, "_set", set_on)
    monkeypatch.setattr(first_run_marker, "is_first_run", lambda: False)
    return state


def test_turn_on_turns_it_on_and_says_an_open_app_may_need_a_restart(switch, message_boxes):
    message_boxes.answer = Gtk.ResponseType.OK
    startup_notices.offer_a11y_switch()
    question, after = message_boxes.shown
    assert question.title == startup_notices.A11Y_QUESTION_TITLE
    assert "stays on until you turn it off in Preferences" in question.text
    assert question.buttons == ["Not Now", "Turn On"]
    # Enter alone mustn't say yes: TuxTalk's own auto-send presses it.
    assert question.default == Gtk.ResponseType.CANCEL
    assert switch.on is True and a11y_switch.answer() == "on"
    assert after.title == startup_notices.A11Y_ON_TITLE
    assert "closed and opened once" in after.text


@pytest.mark.parametrize("response", [Gtk.ResponseType.CANCEL, Gtk.ResponseType.DELETE_EVENT],
                         ids=["Not Now", "window closed"])
def test_not_now_leaves_it_off_and_says_nothing_more(switch, message_boxes, response):
    message_boxes.answer = response
    startup_notices.offer_a11y_switch()
    assert len(message_boxes.shown) == 1
    assert switch.on is False and a11y_switch.answer() == "not_now"


@pytest.mark.parametrize("response", [Gtk.ResponseType.OK, Gtk.ResponseType.CANCEL])
def test_asked_only_once_whatever_the_answer(switch, message_boxes, response):
    message_boxes.answer = response
    startup_notices.offer_a11y_switch()
    switch.on = False       # turned off elsewhere since: still not asked again
    asked = len(message_boxes.shown)
    startup_notices.offer_a11y_switch()
    assert len(message_boxes.shown) == asked


@pytest.mark.parametrize("on", [True, None], ids=["already on", "no accessibility bus"])
def test_never_asked_when_it_is_already_on_or_cant_be(switch, message_boxes, on):
    switch.on = on
    startup_notices.offer_a11y_switch()
    assert message_boxes.shown == []


def test_after_setup_dictation_starts_before_the_question_is_asked(monkeypatch):
    """Asked at setup's end, the question held the dictation service back
    until it was answered; now the service starts and the question comes a
    moment later, on its own timer."""
    from tuxtalk import menu_cache, tray
    started, timers = [], []
    fake_tray = types.SimpleNamespace(
        onboarding_in_progress=True, refresh_menu=lambda: None,
        show_menu_window_if_no_tray=lambda: False,
        _auto_start_service=lambda delay: started.append(delay))
    monkeypatch.setattr(first_run_marker, "mark_first_run_complete", lambda: None)
    monkeypatch.setattr(menu_cache, "refresh_menu_cache", lambda: None)
    monkeypatch.setattr(tray.GLib, "timeout_add", lambda ms, fn, *a: timers.append(fn))
    monkeypatch.setattr(startup_notices, "offer_a11y_switch",
                        lambda parent=None: pytest.fail("asked before the service started"))
    tray._finish_first_run(fake_tray, {"continue": True})
    assert started and startup_notices.offer_a11y_switch_after_setup in timers


def test_the_question_leaves_the_keyboard_where_it_is(switch, monkeypatch):
    """It can come up while the user dictates: taking the keyboard, the
    words would go into it and a space would press Not Now."""
    asked = []
    monkeypatch.setattr(startup_notices, "show_message",
                        lambda *a, **k: asked.append(k) or Gtk.ResponseType.CANCEL)
    startup_notices.offer_a11y_switch()
    assert asked[0]["take_focus"] is False


def test_not_asked_over_first_run_setup(switch, message_boxes, monkeypatch):
    """Setup has it asked once dictation runs; the tray's start mustn't ask over it."""
    monkeypatch.setattr(first_run_marker, "is_first_run", lambda: True)
    startup_notices.offer_a11y_switch_at_start()
    assert message_boxes.shown == []


def test_at_start_it_is_turned_on_again_after_a_yes(switch, message_boxes):
    a11y_switch._save({"answer": "on", "ours": True})
    startup_notices.offer_a11y_switch_at_start()
    assert switch.on is True and message_boxes.shown == []
