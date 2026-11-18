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
FILLER, LIST, LIST_ITEM, PAGE_TAB, PASSWORD, TERMINAL, TEXT, PARAGRAPH, SECTION = (
    20, 31, 32, 37, 40, 60, 61, 73, 85)
PUSH_BUTTON = 43
DOCUMENT_WEB = 95


class TextBus:
    """The accessibility bus as call_sync sees it: apps, each with one
    window holding a focused text widget."""

    def __init__(self, clock):
        self.clock, self.nodes, self.pids, self.apps = clock, {}, {}, []
        self.text_calls, self.toolkits = [], {}

    def app(self, name, text="", caret=0, selection=None, active=True, is_text=True,
            hang=False, pid=None, framed=False, role=TEXT, toolkit="GTK"):
        """An app *name* whose focused widget holds *text* with its cursor at
        *caret*, or *selection* (start, end) selected. *is_text* False: the
        focused widget has no Text interface. *hang*: its text never comes.
        *framed*: the widget sits in a frame without text that says it has
        the focus too, ahead of it (KDE's editor, KTextEditor). *role*: the
        widget's AT-SPI role; *toolkit*: as the app tells it, "Chromium" for
        Chrome and Electron apps."""
        own = f":{name}"
        self.toolkits[own] = toolkit
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
        if method == "Get" and args.unpack() == (atspi_helper._APPLICATION, "ToolkitName"):
            return GLib.Variant("(v)", (GLib.Variant("s", self.toolkits[name]),))
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
            if node.get("qt"):
                raise Gio.DBusError.new_for_dbus_error(
                    "org.freedesktop.DBus.Error.UnknownMethod", "No Collection interface")
            # Every focused descendant in tree order, as many as the rule asks.
            count = args.unpack()[2]
            found = [c for c in self._tree(node) if self.nodes[c]["states"] & FOCUSED]
            return GLib.Variant("(a(so))", (found[:count] if count else found,))
        if method == "GetInterfaces":
            text = ["org.a11y.atspi.Text"] if node.get("is_text") else []
            return GLib.Variant("(as)", (["org.a11y.atspi.Accessible"] + text,))
        raise AssertionError(f"unexpected call {method}")

    def kate(self, text, role=TEXT, selection=None, qt5=False):
        """Kate as Qt shows it, in the order a real one gave: a tab of its
        side panel and a frame without text say they have the focus, ahead
        of the focused widget of *role* (its editor, or its terminal panel)
        with *text*, and a status bar button after it says so too. *qt5*:
        without the Collection interface, so it's walked."""
        self.app("kate", text, caret=len(text), role=role, selection=selection, toolkit="Qt")
        ahead, after = [(":kate", "/tab"), (":kate", "/frame")], (":kate", "/button")
        for ref, kind in zip(ahead + [after], (PAGE_TAB, FILLER, PUSH_BUTTON), strict=True):
            self.nodes[ref] = dict(states=SHOWING | FOCUSED, is_text=False, hang=False,
                                   children=[], role=kind)
        window = self.nodes[(":kate", "/win")]
        window["children"] = ahead + window["children"] + [after]
        window["qt"] = qt5

    def web_editor(self, name, box, toolkit="Gecko"):
        """A web page's rich text box as Firefox gives it, built from *box*:
        a dict with "role", "text" (a paragraph or list is one U+FFFC in its
        parent's text), "caret" (-1 where the cursor isn't: Firefox gives an
        offset only to the object whose own text holds it), "selection",
        "is_text" and "children", each a dict the same way."""
        own, count = f":{name}", [0]
        self.toolkits[own] = toolkit

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
    # Its processes are made up: none is a real one's, a Flatpak proxy's.
    monkeypatch.setattr(atspi_helper, "_command_of", lambda pid: None)
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


def test_chromes_box_pointing_at_the_paragraph_with_the_cursor_is_read_there(bus):
    """Chrome gives the box the offset of the paragraph holding the cursor
    (Claude Desktop's message box), where Firefox gives -1."""
    bus.web_editor("claude-desktop", {"role": TEXT, "text": "\ufffc\ufffc", "caret": 1,
                                      "children": [paragraph("First one."),
                                                   paragraph("the big dog", caret=7)]},
                   toolkit="Chromium")
    assert around(focus=("com.anthropic.Claude", None)) == ("the big", " dog")


def test_a_cursor_right_before_an_image_is_read_as_before(bus):
    bus.web_editor("Firefox", {"role": TEXT, "text": "the big \ufffc", "caret": 8,
                               "children": [{"role": 27, "is_text": False}]})
    assert around(focus=("firefox", None)) == ("the big ", "\n")


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
    bus.app("Google Chrome", "the big dog", caret=7, active=False, toolkit="Chromium")
    assert around(focus=("google-chrome", None)) == ("the big", " dog")


def test_fedoras_chromium_is_the_chromium_browser_window(bus):
    bus.app("Chromium", "the big dog", caret=7, active=False, toolkit="Chromium")
    assert around(focus=("chromium-browser", None)) == ("the big", " dog")


def test_gnomes_text_editor_is_its_app_id_window(bus):
    """GNOME Text Editor 50 is "gnome-text-editor" on the bus and
    org.gnome.TextEditor as a window: the sentence fitting did nothing there
    (Ubuntu 26.04 VM)."""
    bus.app("gnome-text-editor", "the big dog", caret=7)
    assert around(focus=("org.gnome.TextEditor", None)) == ("the big", " dog")


def test_another_gnome_app_in_front_isnt_taken_for_the_text_editor(bus):
    bus.app("gnome-text-editor", "secret draft", caret=6)
    assert around(focus=("org.gnome.Nautilus", None)) is None


def test_a_longer_app_id_ending_like_gnomes_isnt_read_as_the_text_editor(bus):
    bus.app("gnome-text-editor", "secret draft", caret=6)
    assert around(focus=("io.github.gnome.TextEditor", None)) is None


def test_two_windows_each_with_a_focused_box_say_nothing(bus):
    """Either may be the one in front."""
    bus.app("Google Chrome", "the big dog", caret=7, active=False, toolkit="Chromium")
    another_window(bus, "Google Chrome", "other draft", 5)
    assert around(focus=("google-chrome", None)) is None


def test_a_window_holding_a_page_without_a_box_isnt_a_second_window(bus):
    """Claude Desktop keeps a hidden window whose page says it's focused."""
    bus.app("claude-desktop", "the big dog", caret=7, active=False, toolkit="Chromium")
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
    bus.app("claude-desktop", "the big dog", caret=7, active=False, toolkit="Chromium")
    own = ":claude-desktop"
    bus.nodes[(own, "/shell")] = dict(states=SHOWING | FOCUSED, text="", caret=0,
                                      selection=None, is_text=True, hang=False,
                                      children=[], role=DOCUMENT_WEB)
    bus.nodes[(own, "/win")]["children"].insert(0, (own, "/shell"))
    assert around(focus=("com.anthropic.Claude", None)) == ("the big", " dog")


def test_an_unmarked_app_in_the_background_isnt_read(bus):
    bus.app("Google Chrome", "secret draft", caret=6, active=False, toolkit="Chromium")
    assert around(focus=("org.kde.konsole", None)) is None


def test_an_unmarked_apps_password_field_is_never_read(bus):
    bus.app("Google Chrome", "swordfish", caret=4, active=False, role=PASSWORD, toolkit="Chromium")
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


@pytest.mark.parametrize("qt5", [False, True])
@pytest.mark.parametrize("role, is_terminal", [(TERMINAL, True), (TEXT, False)])
def test_a_terminal_panel_inside_kate_is_told_from_its_editor(bus, role, is_terminal, qt5):
    """Konsole's widget says it's a terminal (Kate's, KDevelop's and
    Dolphin's panel too), where the window class is the app's; found past
    the tabs that say they're focused too."""
    bus.kate("ron@pc:~$ make", role=role, qt5=qt5)
    focus = ("org.kde.kate", None)
    assert atspi_helper.focused_is_terminal(focused_app=lambda: focus) is is_terminal


def test_kates_editor_is_read_past_its_focused_tabs(bus):
    """The sentence fitting and the selection check read Kate's editor, not
    the side panel's tab ahead of it that says it's focused too."""
    bus.kate("the big red dog", selection=(8, 12))
    assert atspi_helper.has_selection(focused_app=lambda: ("org.kde.kate", None)) is True


def test_a_recording_start_wakes_the_app_with_the_focus(monkeypatch):
    """Chrome and Electron apps build their text boxes for the accessibility
    bus only once asked: asked when the recording starts, they're there by
    the time the words are typed."""
    from tuxtalk import engine, focused_window, recording
    from tuxtalk import recording_state as rs
    woken = []
    monkeypatch.setattr(focused_window, "wake_text_reading", lambda: woken.append(True))
    monkeypatch.setattr(rs.state, "is_recording", False)
    monkeypatch.setattr(engine, "model", object())
    monkeypatch.setattr(recording, "follow_system_default", lambda idx: idx)
    monkeypatch.setattr(recording, "open_input_stream", lambda rec, idx: None)
    monkeypatch.setattr(recording, "pause_media_players", lambda: None)
    monkeypatch.setattr(recording, "indicator", None)
    monkeypatch.setattr("tuxtalk.type_at_pause.arm_chunker", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.tray_link.notify_recording_state", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    assert recording.start_recording(False, False, 0) is not None
    assert woken == [True]


def test_only_a_chromium_window_is_searched_for_every_focused_widget(bus):
    """Every match would walk a whole web page in Firefox or a big GTK
    window, before each line of a dictation."""
    asked = []
    real = bus.call_sync

    def counting(name, path, interface, method, args, *rest):
        if method == "GetMatches":
            asked.append((name, args.unpack()[2]))
        return real(name, path, interface, method, args, *rest)
    bus.call_sync = counting
    bus.app("gedit", "the big dog", caret=7)
    assert around() == ("the big", " dog")
    bus.apps.clear()
    bus.app("Google Chrome", "the big dog", caret=7, toolkit="Chromium")
    assert around(focus=("google-chrome", None)) == ("the big", " dog")
    assert asked == [(":gedit", 1), (":Google Chrome", 0)]
