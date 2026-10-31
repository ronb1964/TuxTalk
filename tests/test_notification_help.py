"""The KDE notification guide works from the keyboard: it opens with Close
focused and default, so Enter or Space closes it, and Escape closes it as
it does a dialog. A plain window had neither."""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from tuxtalk import notification_help  # noqa: E402


@pytest.fixture
def guide():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
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
