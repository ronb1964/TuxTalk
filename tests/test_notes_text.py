"""Release notes (Markdown from GitHub) in About and Update Available: their
links show as their words and open in the browser. Put in as plain text, a
link was "[vote on the AUR page](https://...)" and couldn't be followed."""
import types

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk  # noqa: E402

from tuxtalk import notes_text  # noqa: E402

AUR = "https://aur.archlinux.org/packages/tuxtalk-appimage"
NOTES = (f"Using TuxTalk on Arch? A [vote on the AUR page]({AUR}) helps. "
         "Details: https://tuxtalk.app/about.html.")
ENTER = types.SimpleNamespace(keyval=Gdk.KEY_Return)


def text_of(notes):
    buffer = notes.view.get_buffer()
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)


def at(notes, offset):
    return notes.view.get_buffer().get_iter_at_offset(offset)


@pytest.fixture
def opened(monkeypatch):
    """The addresses that would have opened in the browser (none really do)."""
    urls = []
    monkeypatch.setattr(notes_text, "_open", lambda view, url: urls.append(url))
    return urls


def test_a_markdown_link_shows_as_its_words_and_bare_addresses_stay():
    notes = notes_text.NotesView(NOTES)
    text = text_of(notes)
    assert text == ("Using TuxTalk on Arch? A vote on the AUR page helps. "
                    "Details: https://tuxtalk.app/about.html.")
    assert notes.link_at(at(notes, text.index("AUR page"))) == AUR
    assert notes.link_at(at(notes, text.index("tuxtalk.app"))) == "https://tuxtalk.app/about.html"
    assert notes.link_at(at(notes, text.index("helps"))) is None
    assert notes.link_at(at(notes, len(text) - 1)) is None         # the closing period isn't


def test_enter_on_a_link_opens_it(opened):
    """For someone using the keyboard: the cursor on the link, then Enter."""
    notes = notes_text.NotesView(NOTES)
    notes.view.get_buffer().place_cursor(at(notes, text_of(notes).index("AUR page")))
    assert notes._on_key(notes.view, ENTER) and opened == [AUR]


def test_enter_off_a_link_does_nothing(opened):
    notes = notes_text.NotesView(NOTES)
    notes.view.get_buffer().place_cursor(at(notes, 3))
    assert not notes._on_key(notes.view, ENTER) and opened == []


def test_notes_shown_again_replace_the_first_and_their_links():
    """About shows "Loading release notes..." first, then the notes."""
    notes = notes_text.NotesView("Loading [x](https://old.example/)")
    notes.show(NOTES)
    assert text_of(notes).startswith("Using TuxTalk")
    assert "https://old.example/" not in notes.links.values()
