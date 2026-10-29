"""Release notes from GitHub, written in Markdown, in a read-only text box
whose links work: "[words](https://...)" shows as its words, and those and
bare https:// addresses open in the browser on a click, or on Enter with
the cursor on them. The About window and Update Available show them; put
in as plain text, a link was its raw Markdown and couldn't be followed."""
import re

from gi.repository import Gdk, GLib, Gtk, Pango

from .logger import setup_logger

logger = setup_logger(__name__)

# A Markdown link, or a bare web address (its closing punctuation left out).
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)|(https?://[^\s)<>\]]+[^\s)<>\].,;:!?])")


class NotesView:
    """A read-only, word-wrapped text box (*view*) showing notes with working
    links; show() puts other notes in it (once they've been fetched)."""

    def __init__(self, text):
        # A cursor, though read-only, so the keyboard can move onto a link.
        self.view = Gtk.TextView(editable=False, cursor_visible=True, wrap_mode=Gtk.WrapMode.WORD,
                                 left_margin=10, right_margin=10, top_margin=10)
        self.links = {}             # a link's tag name -> its address
        self.view.connect("button-release-event", self._on_click)
        self.view.connect("key-press-event", self._on_key)
        self.view.connect("motion-notify-event", self._on_motion)
        self.show(text)

    def show(self, text):
        """Put *text* in the box, each link as a tag self.links names."""
        buffer = self.view.get_buffer()
        buffer.set_text("")
        table = buffer.get_tag_table()
        for name in self.links:
            table.remove(table.lookup(name))
        self.links.clear()
        done = 0
        for match in _LINK.finditer(text):
            buffer.insert(buffer.get_end_iter(), text[done:match.start()])
            words, url = ((match.group(1), match.group(2)) if match.group(1)
                          else (match.group(3),) * 2)
            name = f"link-{len(self.links)}"
            buffer.create_tag(name, foreground="#5a9bff", underline=Pango.Underline.SINGLE)
            self.links[name] = url
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), words, name)
            done = match.end()
        buffer.insert(buffer.get_end_iter(), text[done:])

    def link_at(self, text_iter):
        """The address of the link at *text_iter*, or None."""
        return next((self.links[t.props.name] for t in text_iter.get_tags()
                     if t.props.name in self.links), None)

    def _at_pointer(self, event):
        x, y = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET,
                                                 int(event.x), int(event.y))
        found, text_iter = self.view.get_iter_at_location(x, y)
        return self.link_at(text_iter) if found else None

    def _on_click(self, view, event):
        """A left click on a link, not the end of a drag that selected text."""
        if event.button == 1 and not view.get_buffer().get_has_selection():
            if url := self._at_pointer(event):
                _open(view, url)
        return False

    def _on_key(self, view, event):
        """Enter with the cursor on a link opens it, for the keyboard."""
        if event.keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return False
        buffer = view.get_buffer()
        url = self.link_at(buffer.get_iter_at_mark(buffer.get_insert()))
        if url:
            _open(view, url)
        return bool(url)

    def _on_motion(self, view, event):
        """The hand pointer over a link, so it looks like one."""
        window = view.get_window(Gtk.TextWindowType.TEXT)
        hand = self._at_pointer(event) and Gdk.Cursor.new_from_name(window.get_display(), "pointer")
        window.set_cursor(hand or None)
        return False


def scrolled_notes(text):
    """A NotesView showing *text* in a scrolled box at least 150 px tall, as
    About and Update Available show their notes: (box, NotesView)."""
    notes = NotesView(text)
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.set_min_content_height(150)
    scroll.add(notes.view)
    return scroll, notes


def _open(view, url):
    """Open *url* in the browser."""
    try:
        Gtk.show_uri_on_window(view.get_toplevel(), url, Gdk.CURRENT_TIME)
    except GLib.Error as e:
        logger.warning(f"Could not open {url}: {e.message}")
