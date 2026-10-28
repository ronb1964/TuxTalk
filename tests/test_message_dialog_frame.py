"""Every TuxTalk message box gets the desktop's own window frame.

Gtk.MessageDialog installs an empty titlebar of its own, so GTK draws the
frame and the desktop never does. On KDE that frame has square corners while
every other TuxTalk window gets KDE's rounded one. ui_style.message_dialog()
removes that titlebar.
"""
import pathlib
import re

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "tuxtalk"


@pytest.fixture
def Gtk():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    return Gtk


def test_the_desktop_draws_the_frame(Gtk):
    from tuxtalk.ui_style import message_dialog
    dialog = message_dialog(message_type=Gtk.MessageType.INFO,
                            buttons=Gtk.ButtonsType.OK, text="You're Up to Date!")
    assert isinstance(dialog, Gtk.MessageDialog)
    assert dialog.get_titlebar() is None
    assert dialog.get_title() == "TuxTalk"  # the frame shows a title now
    assert dialog.get_deletable()  # an X, like Escape, answers the box


def test_a_self_closing_box_has_no_close_button(Gtk):
    """'Checking for Updates...' style boxes close themselves when the work
    is done. Closing one early isn't something to invite with an X."""
    from tuxtalk.ui_style import message_dialog
    dialog = message_dialog(closable=False, buttons=Gtk.ButtonsType.NONE, text="Working...")
    assert not dialog.get_deletable()


def test_a_question_that_adds_its_own_buttons_keeps_its_close_button(Gtk):
    """No buttons at construction does not mean "self-closing": most
    questions pass buttons=NONE and add theirs afterwards, and they keep the
    X that every other dialog has."""
    from tuxtalk.ui_style import message_dialog
    dialog = message_dialog(buttons=Gtk.ButtonsType.NONE, text="Replace it?")
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("Replace", Gtk.ResponseType.OK)
    assert dialog.get_deletable()


def _open_box(Gtk, text):
    """The visible message box whose main text is *text*, or None."""
    return next((w for w in Gtk.Window.list_toplevels()
                 if isinstance(w, Gtk.MessageDialog) and w.props.text == text
                 and w.get_visible()), None)


def test_checking_for_updates_has_no_close_button(Gtk, monkeypatch):
    import types
    from gi.repository import GLib
    from tuxtalk import tray, update_checker
    monkeypatch.setattr(update_checker, "check_for_updates", lambda: {"success": False})
    monkeypatch.setattr(GLib, "idle_add", lambda *a: None)     # the result never comes
    tray.DictationTray.check_for_updates_clicked(types.SimpleNamespace(), None)
    box = _open_box(Gtk, "Checking for Updates...")
    try:
        assert box is not None and not box.get_deletable()
    finally:
        box.destroy()


def test_installing_an_update_has_no_close_button(Gtk, monkeypatch):
    import queue
    import types
    from gi.repository import GLib
    from tuxtalk import tray, update_checker
    waiting = queue.Queue()
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *args: waiting.put(lambda: fn(*args)))
    monkeypatch.setattr(update_checker, "download_update", lambda *a, **k: "/u/TuxTalk.AppImage")
    monkeypatch.setattr(update_checker, "install_appimage_update", lambda path: (True, ""))
    tray.DictationTray._download_update(types.SimpleNamespace(restart_app=lambda: None, _quitting=False),
                                        {"appimage_url": "https://x/TuxTalk.AppImage"})
    waiting.get(timeout=5)()          # downloaded: the box shows, the install starts
    box = _open_box(Gtk, "Installing Update...")
    assert box is not None and not box.get_deletable()
    waiting.get(timeout=5)()          # installed, back on the GTK thread: the box goes


def test_an_explicit_title_is_kept(Gtk):
    from tuxtalk.ui_style import message_dialog
    assert message_dialog(title="Fix a Word", text="x").get_title() == "Fix a Word"


def test_no_module_builds_a_message_dialog_directly():
    """One stock Gtk.MessageDialog anywhere brings the square box back."""
    offenders = [
        f"{path.name}:{n}"
        for path in SRC.rglob("*.py") if path.name != "ui_style.py"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if re.search(r"\bGtk\.MessageDialog\(", line)
    ]
    assert offenders == []
