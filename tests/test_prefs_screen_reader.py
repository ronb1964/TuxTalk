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


def test_the_light_bulb_beside_a_setting_isnt_read_aloud(prefs_window):
    """The bulb that says a setting has a tip was read as "light bulb", in
    its name and in the name of the dropdown beside it ("Model light bulb")."""
    from gtk_text import widgets
    window = prefs_window()
    bulbs = [w for root in (window.window, window.audio_tab.indicator.grid)
             for w in widgets(root) if isinstance(w, Gtk.Label) and "💡" in w.get_text()]

    assert len(bulbs) >= 9      # the keys, model, language, sound, numbers, indicator
    for label in bulbs:
        name = label.get_accessible().get_name()
        assert name == label.get_text().replace(" 💡", "")
        control = label.get_mnemonic_widget()
        assert control is not None and _labelled_by(control) == [name]
