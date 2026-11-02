"""A screen reader reads the emoji that starts a message aloud: "💡 How It
Works" came out as "light bulb How It Works". The messages keep their icon on
screen and are named without it (ui_style.spoken_without_icons)."""
import types

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from gtk_text import widgets  # noqa: E402
from tuxtalk import prefs_speech, ui_style  # noqa: E402


def _spoken(root, shown_start):
    """What a screen reader says for the label in *root* whose text starts
    with *shown_start*."""
    label = next(w for w in widgets(root)
                 if isinstance(w, Gtk.Label) and w.get_text().startswith(shown_start))
    return label.get_accessible().get_name()


@pytest.mark.parametrize("markup, spoken", [
    ('<span size="large"><b>💡 How It Works</b></span>', "How It Works"),
    ("<i>⏯️ Or tap <b>F9</b> to start</i>", "Or tap F9 to start"),
    ("📋 Typed into the wrong window?", "Typed into the wrong window?"),
    ("→ an arrow is read as it is", "→ an arrow is read as it is"),
])
def test_the_icon_that_starts_a_message_isnt_read_aloud(markup, spoken):
    label = Gtk.Label()
    label.set_markup(markup)
    assert ui_style.spoken_without_icons(label).get_accessible().get_name() == spoken


def test_the_welcome_screen_reads_how_it_works_without_the_bulb(welcome_window):
    assert _spoken(welcome_window.dialog, "💡 How It Works") == "How It Works"


def test_setup_completes_tips_are_read_without_their_icons(monkeypatch):
    from tuxtalk import config, setup_complete_dialog
    monkeypatch.setattr(config, "load_config", config.Settings)
    spoken = []

    def run(dialog):
        spoken.extend(w.get_accessible().get_name() for w in widgets(dialog)
                      if isinstance(w, Gtk.Label) and w.get_text()[:1] in "💡⏯📋⚙🔁")
        return Gtk.ResponseType.OK
    monkeypatch.setattr(Gtk.Dialog, "run", run)
    setup_complete_dialog.show_setup_complete_dialog()
    assert spoken and 'Say "period", "comma" or "new line" as you talk' in spoken
    assert not [name for name in spoken if name[:1] in "💡⏯📋⚙🔁"]


def test_the_large_model_question_has_no_bulb_to_read(monkeypatch):
    asked = []
    monkeypatch.setattr("tuxtalk.model_helper.is_model_cached_fast", lambda model: False)
    monkeypatch.setattr(prefs_speech, "show_message",
                        lambda parent, kind, title, text, **kw: asked.append(text))
    prefs_speech.SpeechSection._confirm_large_v3_download(types.SimpleNamespace(window=None))
    assert asked and "💡" not in asked[0]
