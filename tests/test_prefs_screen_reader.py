"""Preferences' number boxes are named for a screen reader by the words
beside them: the hands-free seconds and Power Saving's minutes were read
with no name at all."""
import gi
import pytest

gi.require_version("Atk", "1.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Atk, Gtk  # noqa: E402

from gtk_text import find  # noqa: E402


def _labelled_by(widget):
    """The text of the label a screen reader names *widget* by, or None."""
    relation = widget.get_accessible().ref_relation_set().get_relation_by_type(
        Atk.RelationType.LABELLED_BY)
    if relation is None:
        return None
    return [target.get_name() for target in relation.get_target()]


@pytest.mark.parametrize("words", ["seconds of silence", "minutes without dictating"])
def test_each_number_box_is_named_by_the_words_beside_it(prefs_window, words):
    window = prefs_window()
    label = find(window.window, Gtk.Label, words)
    spin = next(child for child in label.get_parent().get_children()
                if isinstance(child, Gtk.SpinButton))
    assert _labelled_by(spin) == [words]
