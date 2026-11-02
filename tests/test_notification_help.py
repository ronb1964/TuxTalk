"""The KDE notification guide works from the keyboard: it opens with Close
focused and default, so Enter or Space closes it, and Escape closes it as
it does a dialog. A plain window had neither."""
import pytest

from gi.repository import Gdk, Gtk

from tuxtalk import notification_help


@pytest.fixture
def guide():
    win = notification_help.show_notification_help_window()
    yield win
    if notification_help._window is not None:
        notification_help._window.destroy()


def test_close_is_focused_and_the_default(guide):
    focused = guide.get_focus()
    assert isinstance(focused, Gtk.Button) and focused.get_label() == "Close"
    assert focused.has_default()


def test_escape_closes_the_guide(guide):
    closed = []
    guide.connect("destroy", lambda _w: closed.append(True))
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval = Gdk.KEY_Escape
    guide.emit("key-press-event", event)
    assert closed == [True] and notification_help._window is None
