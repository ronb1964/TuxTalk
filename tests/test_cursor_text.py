"""The few characters on either side of the cursor, asked of the
accessibility bus (atspi_helper.text_around_cursor), so a dictation can fit
the sentence it goes into. Whenever that can't be told, quickly and about
the app with the focus, the answer is None and typing goes on as always."""
import pytest
from conftest import FakeClock
from gi.repository import Gio, GLib

from tuxtalk import atspi_helper

ACTIVE, FOCUSED, SHOWING = 1 << 1, 1 << 12, 1 << 25
FILLER, PASSWORD, TEXT, PARAGRAPH, SECTION = 39, 40, 61, 73, 85     # AT-SPI roles


class TextBus:
    """The accessibility bus as call_sync sees it: apps, each with one
    window holding a focused text widget."""

    def __init__(self, clock):
        self.clock, self.nodes, self.pids, self.apps = clock, {}, {}, []
        self.text_calls = []

    def app(self, name, text="", caret=0, selection=None, active=True, is_text=True,
            hang=False, pid=None, framed=False, role=TEXT):
        """An app *name* whose focused widget holds *text* with its cursor at
        *caret*, or *selection* (start, end) selected. *is_text* False: the
        focused widget has no Text interface. *hang*: its text never comes.
        *framed*: the widget sits in a frame without text that says it has
        the focus too, ahead of it (KDE's editor, KTextEditor). *role*: the
        widget's AT-SPI role."""
        own = f":{name}"
        if pid is not None:
            self.pids[own] = pid
        self.nodes[(own, "/w")] = dict(states=SHOWING | FOCUSED, text=text, caret=caret,
                                       selection=selection, is_text=is_text, hang=hang,
                                       children=[], role=role)
        top = (own, "/w")
        if framed:
            self.nodes[(own, "/frame")] = dict(states=SHOWING | FOCUSED, is_text=False,
                                               hang=False, children=[(own, "/w")],
                                               role=FILLER)
            top = (own, "/frame")
        self.nodes[(own, "/win")] = dict(states=SHOWING | (ACTIVE if active else 0),
                                         children=[top])
        self.nodes[(own, "/root")] = dict(name=name, children=[(own, "/win")])
        self.apps.append((own, "/root"))

    def call_sync(self, name, path, interface, method, args, reply_type, flags, timeout_ms,
                  cancellable):
        if method == "GetConnectionUnixProcessID":
            return GLib.Variant("(u)", (self.pids[args.unpack()[0]],))
        if (name, path) == (atspi_helper._REGISTRY, atspi_helper._ROOT):
            return GLib.Variant("(a(so))", (self.apps,))
        node = self.nodes[(name, path)]
        if interface == "org.a11y.atspi.Text":
            return self._text(node, method, args, timeout_ms)
        if method == "Get":
            if args.unpack()[1] == "Name":
                return GLib.Variant("(v)", (GLib.Variant("s", node["name"]),))
            return self._text(node, args.unpack()[1], args, timeout_ms)
        if method == "GetRole":
            return GLib.Variant("(u)", (node.get("role", FILLER),))
        if method == "GetChildren":
            return GLib.Variant("(a(so))", (node["children"],))
        if method == "GetState":
            return GLib.Variant("(au)", ([node["states"] & 0xFFFFFFFF, node["states"] >> 32],))
        if method == "GetMatches":
            # Every focused descendant in tree order, as many as the rule asks.
            count = args.unpack()[2]
            found = [c for c in self._tree(node) if self.nodes[c]["states"] & FOCUSED]
            return GLib.Variant("(a(so))", (found[:count] if count else found,))
        if method == "GetInterfaces":
            text = ["org.a11y.atspi.Text"] if node.get("is_text") else []
            return GLib.Variant("(as)", (["org.a11y.atspi.Accessible"] + text,))
        raise AssertionError(f"unexpected call {method}")

    def web_editor(self, name, paragraphs, box_caret=-1):
        """A web page's rich text box as Firefox gives it (*box_caret* -1,
        or Chromium's offset of the paragraph's placeholder): its own text
        a U+FFFC per paragraph, each paragraph an object with its own text,
        (text, caret) in *paragraphs*, the caret -1 where it isn't."""
        own = f":{name}"
        children = []
        for i, (text, caret) in enumerate(paragraphs):
            ref = (own, f"/p{i}")
            self.nodes[ref] = dict(states=SHOWING, text=text, caret=caret, selection=None,
                                   is_text=True, hang=False, children=[], role=PARAGRAPH)
            children.append(ref)
        self.nodes[(own, "/w")] = dict(states=SHOWING | FOCUSED, text="\ufffc" * len(paragraphs),
                                       caret=box_caret, selection=None, is_text=True,
                                       hang=False, children=children, role=SECTION)
        self.nodes[(own, "/win")] = dict(states=SHOWING | ACTIVE, children=[(own, "/w")])
        self.nodes[(own, "/root")] = dict(name=name, children=[(own, "/win")])
        self.apps.append((own, "/root"))

    def _tree(self, node):
        for child in node.get("children", []):
            yield child
            yield from self._tree(self.nodes[child])

    def _text(self, node, method, args, timeout_ms):
        if node["hang"]:
            self.clock.sleep(timeout_ms / 1000)
            raise GLib.Error("Timeout was reached")
        if not node["is_text"]:
            raise Gio.DBusError.new_for_dbus_error(
                "org.freedesktop.DBus.Error.UnknownInterface", "No such interface")
        self.text_calls.append(method)
        if method == "CaretOffset":
            return GLib.Variant("(v)", (GLib.Variant("i", node["caret"]),))
        if method == "CharacterCount":
            return GLib.Variant("(v)", (GLib.Variant("i", len(node["text"])),))
        if method == "GetNSelections":
            return GLib.Variant("(i)", (1 if node["selection"] else 0,))
        if method == "GetSelection":
            return GLib.Variant("(ii)", node["selection"])
        if method == "GetText":
            # As Firefox does: nothing at all for a range past the end.
            start, end = args.unpack()
            text = node["text"][start:end] if end <= len(node["text"]) else ""
            return GLib.Variant("(s)", (text,))
        raise AssertionError(f"unexpected text call {method}")


@pytest.fixture
def bus(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(atspi_helper, "time", clock)
    fake = TextBus(clock)
    monkeypatch.setattr(atspi_helper, "_a11y_bus", lambda deadline: fake)
    return fake


def around(focus=("gedit", None)):
    """What's read, with the desktop saying *focus* (class, process) has
    the focus; None: it can't say."""
    return atspi_helper.text_around_cursor(8, focused_app=lambda: focus)


def test_the_text_on_either_side_of_the_cursor_is_read(bus):
    bus.app("gedit", "the big dog", caret=7)
    assert around() == ("the big", " dog")


def test_the_text_inside_a_focused_frame_is_read(bus):
    """KDE's editor says its frame has the focus, ahead of the text in it."""
    bus.app("kwrite", "the big dog", caret=7, framed=True)
    assert around(focus=("org.kde.kwrite", None)) == ("the big", " dog")


def test_with_text_selected_the_text_outside_the_selection_counts(bus):
    bus.app("gedit", "the big red dog", selection=(8, 12))
    assert around() == ("the big ", "dog")


def test_only_a_few_characters_are_read(bus):
    bus.app("gedit", "a long sentence before the cursor and more after it", caret=33)
    assert around() == ("e cursor", " and mor")


def test_a_widget_that_isnt_text_says_nothing(bus):
    bus.app("gedit", is_text=False)
    assert around() is None


def test_an_app_that_doesnt_answer_in_time_says_nothing(bus):
    bus.app("gedit", "the big dog", caret=7, hang=True)
    started = atspi_helper.time.monotonic()
    assert around() is None
    assert atspi_helper.time.monotonic() - started <= atspi_helper.CONTEXT_BUDGET_S


def test_no_accessibility_bus_says_nothing(monkeypatch):
    monkeypatch.setattr(atspi_helper, "_a11y_bus", lambda deadline: None)
    assert around() is None


def test_an_app_in_the_background_isnt_read_when_another_has_the_focus(bus):
    """A browser left with a window it calls active, while the user types
    in Claude Desktop, which isn't on the bus: the browser's text isn't the
    text around the user's cursor."""
    bus.app("Brave", "secret draft", caret=6, pid=100)
    assert around(focus=("Claude", 200)) is None


def test_the_app_with_the_focus_is_read_when_two_say_they_are_active(bus):
    bus.app("Brave", "other text", caret=5, pid=100)
    bus.app("kwrite", "the big dog", caret=7, pid=300)
    assert around(focus=("org.kde.kwrite", 300)) == ("the big", " dog")


def test_two_active_apps_and_no_way_to_tell_which_has_the_focus_says_nothing(bus):
    bus.app("Brave", "other text", caret=5)
    bus.app("kwrite", "the big dog", caret=7)
    assert around(focus=None) is None


def test_the_text_after_the_cursor_is_read_near_the_end_of_the_field(bus):
    bus.app("gedit", "the big dog.", caret=8)
    assert around() == ("the big ", "dog.")


def test_a_password_field_inside_a_frame_is_never_read(bus):
    bus.app("gedit", "hunter2", caret=7, role=PASSWORD, framed=True)
    assert around() is None
    assert "GetText" not in bus.text_calls


def test_a_password_field_is_never_read(bus):
    bus.app("gedit", "hunter2", caret=7, role=PASSWORD)
    assert around() is None
    assert "GetText" not in bus.text_calls


def test_no_cursor_says_nothing(bus):
    bus.app("gedit", "the big dog", caret=-1)
    assert around() is None


@pytest.mark.parametrize("box_caret", [-1, 0], ids=["Firefox", "Chromium"])
def test_a_web_editor_s_paragraph_with_the_cursor_is_read(bus, box_caret):
    """Claude Desktop, Gmail, Discord: the box gives its paragraphs as
    U+FFFC, and the one with the cursor has the text around it."""
    bus.web_editor("Firefox", [("the big dog runs", 7), ("Second line here", -1)], box_caret)
    assert around(focus=("firefox", None)) == ("the big", " dog run")


def test_a_web_editor_whose_paragraphs_dont_say_where_the_cursor_is_says_nothing(bus):
    bus.web_editor("Firefox", [("the big dog runs", -1), ("Second line here", -1)])
    assert around(focus=("firefox", None)) is None


def test_one_active_app_isnt_read_when_the_desktop_cant_say_it_has_the_focus(bus):
    """It may be a browser left in the background (issue #40)."""
    bus.app("Brave", "draft text", caret=5)
    assert around(focus=None) is None
