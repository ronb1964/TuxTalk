"""Fix a Word: picking words in the text box (fix_word_dialog._on_pick).

Ron, 2026-10-17: double-clicking "Goul's?" highlighted the question mark too,
and after that first pick no other word could be picked: the release handler
returned GLib.idle_add's source id, which GTK read as "handled", so the text
box never saw the button come up and ignored every later click.
"""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from tuxtalk import fix_word_dialog as F  # noqa: E402

TEXT = "What's the reply to Goul's? Is it thanks."


@pytest.fixture
def dialog(monkeypatch):
    if Gtk.init_check()[0] is False:
        pytest.skip("no display")
    d = F._FixWordDialog([TEXT], 0, lambda t: None)
    yield d
    d.window.destroy() if hasattr(d, "window") else None


def _highlighted(d):
    buf = d.view.get_buffer()
    it, out = buf.get_start_iter(), []
    while not it.is_end():
        if it.has_tag(d.picked_tag):
            out.append(it.get_char())
        it.forward_char()
    return "".join(out)


def _click(d, offset):
    buf = d.view.get_buffer()
    it = buf.get_iter_at_offset(offset)
    buf.select_range(it, it)
    d._on_pick()


def test_the_release_is_never_swallowed(dialog, monkeypatch):
    monkeypatch.setattr(F.GLib, "idle_add", lambda fn, *a: 4321)
    assert dialog._pick_soon(None, None, True) is False


def test_the_highlight_leaves_out_punctuation(dialog):
    _click(dialog, TEXT.index("Goul's") + 2)
    assert dialog._phrase() == "goul's"
    assert _highlighted(dialog) == "Goul's"


def test_a_second_click_picks_another_word(dialog):
    _click(dialog, TEXT.index("Goul's") + 2)
    _click(dialog, TEXT.index("reply") + 1)
    assert dialog._phrase() == "reply" and _highlighted(dialog) == "reply"


def test_the_arrow_keys_can_move_past_the_first_word(dialog):
    """A pick by key keeps the focus and the cursor in the text: moving to
    the "Should be" box after each key stopped a keyboard user at the first
    word."""
    dialog.window.show_all()
    dialog.view.grab_focus()
    buf = dialog.view.get_buffer()
    buf.place_cursor(buf.get_iter_at_offset(TEXT.index("reply") + 1))
    dialog._on_pick(by_mouse=False)
    assert dialog._phrase() == "reply"
    assert dialog.window.get_focus() is dialog.view
    assert buf.get_iter_at_mark(buf.get_insert()).get_offset() == TEXT.index("reply") + 1


def test_should_be_names_its_box(dialog):
    label = next(w for w in dialog.entry.get_parent().get_children() if isinstance(w, Gtk.Label))
    assert label.get_mnemonic_widget() is dialog.entry


def test_the_arrow_keys_move_the_cursor_word_by_word(dialog):
    """Hidden, GTK's cursor doesn't move on the arrows: the box scrolls."""
    buf = dialog.view.get_buffer()
    buf.place_cursor(buf.get_start_iter())
    dialog.view.emit("move-cursor", Gtk.MovementStep.WORDS, 2, False)
    cursor = buf.get_iter_at_mark(buf.get_insert())
    assert cursor.get_offset() > 0 and cursor.ends_word()


def test_the_dictation_dropdown_is_named_too(dialog):
    label = next(w for w in dialog.combo_row.get_children() if isinstance(w, Gtk.Label))
    assert label.get_mnemonic_widget() is dialog.combo
