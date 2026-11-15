"""The few characters on either side of the cursor, asked of the
accessibility bus (atspi_helper.text_around_cursor), so a dictation can fit
the sentence it goes into. Whenever that can't be told, quickly and about
the app with the focus, the answer is None and typing goes on as always."""
import pytest
from conftest import FakeClock
from gi.repository import Gio, GLib

from tuxtalk import atspi_helper

ACTIVE, EDITABLE, FOCUSED, SHOWING = 1 << 1, 1 << 7, 1 << 12, 1 << 25
# AT-SPI roles (atspi-constants.h)
FILLER, LIST, LIST_ITEM, PASSWORD, TEXT, PARAGRAPH, SECTION = 20, 31, 32, 40, 61, 73, 85
DOCUMENT_WEB = 95


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
        self.nodes[(own, "/w")] = dict(states=SHOWING | FOCUSED | EDITABLE, text=text,
                                       caret=caret,
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

    def web_editor(self, name, box):
        """A web page's rich text box as Firefox gives it, built from *box*:
        a dict with "role", "text" (a paragraph or list is one U+FFFC in its
        parent's text), "caret" (-1 where the cursor isn't: Firefox gives an
        offset only to the object whose own text holds it), "selection",
        "is_text" and "children", each a dict the same way."""
        own, count = f":{name}", [0]

        def build(spec, focused=False):
            count[0] += 1
            ref = (own, f"/n{count[0]}")
            self.nodes[ref] = dict(
                states=SHOWING | (FOCUSED if focused else 0), text=spec.get("text", ""),
                caret=spec.get("caret", -1), selection=spec.get("selection"),
                is_text=spec.get("is_text", True), hang=False, role=spec.get("role", SECTION),
                children=[build(child) for child in spec.get("children", [])])
            return ref
        self.nodes[(own, "/win")] = dict(states=SHOWING | ACTIVE, children=[build(box, True)])
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


def paragraph(text, caret=-1, **more):
    return dict(role=PARAGRAPH, text=text, caret=caret, **more)


def test_the_paragraph_of_a_web_editor_with_the_cursor_in_it_is_read(bus):
    """Gmail and the like in Firefox: the box gives its paragraphs as
    U+FFFC, and only the one with the cursor in it says where it is."""
    bus.web_editor("Firefox", dict(text="\ufffc\ufffc", children=[
        paragraph("First one here"), paragraph("the big dog runs", 7)]))
    assert around(focus=("firefox", None)) == ("the big", " dog run")


def test_a_list_in_a_web_editor_is_looked_into(bus):
    bus.web_editor("Firefox", dict(text="\ufffc\ufffc", children=[
        paragraph("Intro"),
        dict(role=LIST, is_text=False, children=[
            dict(role=LIST_ITEM, text="\ufffc", children=[paragraph("buy more milk", 8)])])]))
    assert around(focus=("firefox", None)) == ("buy more", " milk")


def test_a_list_item_holding_its_own_text_is_read(bus):
    """<ul><li>buy more milk</li></ul>: most editors' bullets."""
    bus.web_editor("Firefox", dict(text="\ufffc", children=[
        dict(role=LIST, is_text=False, children=[
            dict(role=LIST_ITEM, text="buy more milk", caret=8)])]))
    assert around(focus=("firefox", None)) == ("buy more", " milk")


def test_a_password_field_inside_a_web_editor_is_never_read(bus):
    bus.web_editor("Firefox", dict(text="\ufffc", children=[
        dict(role=PASSWORD, text="hunter2", caret=3)]))
    assert around(focus=("firefox", None)) is None
    assert "GetText" not in bus.text_calls


def test_text_in_the_box_itself_beside_a_paragraph_counts_up_to_it(bus):
    """Gmail writes a message's first line straight into the box and the
    next as a paragraph: past the paragraph counts as another line."""
    bus.web_editor("Firefox", dict(text="Hello there\ufffc", caret=5,
                                   children=[paragraph("Second line")]))
    assert around(focus=("firefox", None)) == ("Hello", " there\n")


def test_a_selection_inside_one_paragraph_is_read_there(bus):
    """The box selects the paragraph's U+FFFC; the paragraph says what's
    selected inside it."""
    bus.web_editor("Firefox", dict(text="\ufffc", selection=(0, 1), children=[
        paragraph("the big red dog", 11, selection=(8, 11))]))
    assert around(focus=("firefox", None)) == ("the big ", " dog")


def test_a_selection_across_paragraphs_says_nothing(bus):
    """Its two ends are in two paragraphs, and what's beside them can't
    both be read."""
    bus.web_editor("Firefox", dict(text="\ufffc\ufffc", selection=(0, 2), children=[
        paragraph("the big dog", 11, selection=(4, 11)), paragraph("red fox runs", -1)]))
    assert around(focus=("firefox", None)) is None


def test_a_web_editor_whose_paragraphs_dont_say_where_the_cursor_is_says_nothing(bus):
    bus.web_editor("Firefox", dict(text="\ufffc\ufffc", children=[
        paragraph("the big dog runs"), paragraph("Second line here")]))
    assert around(focus=("firefox", None)) is None


def test_one_active_app_isnt_read_when_the_desktop_cant_say_it_has_the_focus(bus):
    """It may be a browser left in the background (issue #40)."""
    bus.app("Brave", "draft text", caret=5)
    assert around(focus=None) is None


# Chromium and Electron apps (Chrome, Claude Desktop) mark a window active
# only now and then on Plasma 6, and each window keeps its own focused box.
def another_window(bus, name, text, caret):
    """A second window of the app *name*, with a focused box of its own."""
    own = f":{name}"
    bus.nodes[(own, "/w2")] = dict(states=SHOWING | FOCUSED | EDITABLE, text=text,
                                   caret=caret,
                                   selection=None, is_text=True, hang=False, children=[],
                                   role=TEXT)
    bus.nodes[(own, "/win2")] = dict(states=SHOWING, children=[(own, "/w2")])
    bus.nodes[(own, "/root")]["children"].append((own, "/win2"))


def test_chrome_with_no_window_marked_active_is_read(bus):
    bus.app("Google Chrome", "the big dog", caret=7, active=False)
    assert around(focus=("google-chrome", None)) == ("the big", " dog")


def test_fedoras_chromium_is_the_chromium_browser_window(bus):
    bus.app("Chromium", "the big dog", caret=7, active=False)
    assert around(focus=("chromium-browser", None)) == ("the big", " dog")


def test_two_windows_each_with_a_focused_box_say_nothing(bus):
    """Either may be the one in front."""
    bus.app("Google Chrome", "the big dog", caret=7, active=False)
    another_window(bus, "Google Chrome", "other draft", 5)
    assert around(focus=("google-chrome", None)) is None


def test_a_window_holding_a_page_without_a_box_isnt_a_second_window(bus):
    """Claude Desktop keeps a hidden window whose page says it's focused."""
    bus.app("claude-desktop", "the big dog", caret=7, active=False)
    own = ":claude-desktop"
    bus.nodes[(own, "/page2")] = dict(states=SHOWING | FOCUSED, text="", caret=0,
                                      selection=None, is_text=True, hang=False,
                                      children=[], role=DOCUMENT_WEB)
    bus.nodes[(own, "/win2")] = dict(states=SHOWING, children=[(own, "/page2")])
    bus.nodes[(own, "/root")]["children"].append((own, "/win2"))
    assert around(focus=("com.anthropic.Claude", None)) == ("the big", " dog")


def test_the_box_typed_into_wins_over_a_page_that_says_it_is_focused(bus):
    """Claude Desktop's own page says it's focused, ahead of the claude.ai
    page whose box has the cursor."""
    bus.app("claude-desktop", "the big dog", caret=7, active=False)
    own = ":claude-desktop"
    bus.nodes[(own, "/shell")] = dict(states=SHOWING | FOCUSED, text="", caret=0,
                                      selection=None, is_text=True, hang=False,
                                      children=[], role=DOCUMENT_WEB)
    bus.nodes[(own, "/win")]["children"].insert(0, (own, "/shell"))
    assert around(focus=("com.anthropic.Claude", None)) == ("the big", " dog")


def test_an_unmarked_app_in_the_background_isnt_read(bus):
    bus.app("Google Chrome", "secret draft", caret=6, active=False)
    assert around(focus=("org.kde.konsole", None)) is None


def test_an_unmarked_apps_password_field_is_never_read(bus):
    bus.app("Google Chrome", "swordfish", caret=4, active=False, role=PASSWORD)
    assert around(focus=("google-chrome", None)) is None


# Whether anything is selected, for a voice edit in KDE's editors: their
# Ctrl+C with nothing selected copies the line and moves the cursor.
def selected(focus=("org.kde.kwrite", None)):
    return atspi_helper.has_selection(focused_app=lambda: focus)


def test_kwrite_with_nothing_selected_says_so(bus):
    bus.app("kwrite", "the big dog", caret=7, framed=True)
    assert selected() is False


def test_kwrite_with_text_selected_says_so(bus):
    bus.app("kwrite", "the big red dog", selection=(8, 12), framed=True)
    assert selected() is True


def test_an_empty_selection_counts_as_nothing_selected(bus):
    bus.app("kwrite", "the big dog", selection=(4, 4), framed=True)
    assert selected() is False


@pytest.mark.parametrize("kind", ["not text", "no answer", "focus unknown"])
def test_when_it_can_t_be_told_the_answer_is_none(bus, kind):
    bus.app("kwrite", "the big dog", is_text=kind != "not text", hang=kind == "no answer",
            framed=True)
    assert selected(None if kind == "focus unknown" else ("org.kde.kwrite", None)) is None
