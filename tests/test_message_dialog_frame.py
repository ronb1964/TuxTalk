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
    from gi.repository import GLib
    from tuxtalk import update_checker, update_ui
    monkeypatch.setattr(update_checker, "check_for_updates", lambda: {"success": False})
    monkeypatch.setattr(GLib, "idle_add", lambda *a: None)     # the result never comes
    update_ui.check_from_menu(lambda result: None)
    box = _open_box(Gtk, "Checking for Updates...")
    try:
        assert box is not None and not box.get_deletable()
    finally:
        box.destroy()


def test_installing_an_update_has_no_close_button(Gtk, monkeypatch):
    """The tray's update window (progress_window's, as Preferences') can't
    be closed while the update downloads and goes in."""
    import queue
    import types
    from gi.repository import GLib
    from tuxtalk import tray, update_install
    waiting, may_finish = queue.Queue(), queue.Queue()
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *args: waiting.put(lambda: fn(*args)))
    monkeypatch.setattr(update_install, "download_update", lambda *a, **k: "/u/TuxTalk.AppImage")
    monkeypatch.setattr(update_install, "install_appimage_update",
                        lambda path, progress: may_finish.get(timeout=5) or (True, ""))
    stub = types.SimpleNamespace(restart_app=lambda: None, _quitting=False)
    stub._install_not_cut_off = lambda install: tray.DictationTray._install_not_cut_off(stub, install)
    tray.DictationTray._download_update(stub, {"appimage_url": "https://x/TuxTalk.AppImage"})
    box = next((w for w in Gtk.Window.list_toplevels()
                if w.get_title() == "Updating TuxTalk" and w.get_visible()), None)
    assert box is not None and not box.get_deletable()
    may_finish.put(False)
    while True:                       # progress updates, then the end, on the GTK thread
        waiting.get(timeout=5)()
        if not box.get_visible():
            break


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


# --- show_message: every message box in one place --------------------------------

def _answered(Gtk, monkeypatch, response):
    """Make every box answer *response* at once; returns what each showed,
    read while it was open."""
    shown = []

    def run(box):
        buttons = box.get_action_area().get_children()
        shown.append({
            "title": box.props.text, "text": box.props.secondary_text,
            "markup": box.props.secondary_use_markup,
            "buttons": [b.get_label() for b in buttons],
            "default": [b.get_label() for b in buttons if b.has_default()],
            "warning": [b.get_label() for b in buttons
                        if b.get_style_context().has_class("destructive-action")]})
        return response
    monkeypatch.setattr(Gtk.Dialog, "run", run)
    return shown


def test_a_question_has_its_buttons_in_order_and_enter_gives_its_default(Gtk, monkeypatch):
    from tuxtalk.ui_style import show_message
    modal = []
    shown = _answered(Gtk, monkeypatch, Gtk.ResponseType.CANCEL)
    answering = Gtk.Dialog.run
    monkeypatch.setattr(Gtk.Dialog, "run",
                        lambda box: modal.append(box.get_modal()) or answering(box))
    answer = show_message(None, Gtk.MessageType.QUESTION, "Apply your changes?", "Or not.",
                          [("Keep Editing", Gtk.ResponseType.CANCEL),
                           ("Apply", Gtk.ResponseType.OK)], default=Gtk.ResponseType.OK)
    assert answer == Gtk.ResponseType.CANCEL
    assert shown == [{"title": "Apply your changes?", "text": "Or not.", "markup": False,
                      "buttons": ["Keep Editing", "Apply"], "default": ["Apply"],
                      "warning": []}]
    assert modal == [True]          # a box that waits holds its window


def test_a_button_that_deletes_or_reboots_is_drawn_as_a_warning(Gtk, monkeypatch):
    from tuxtalk.ui_style import show_message
    shown = _answered(Gtk, monkeypatch, Gtk.ResponseType.CANCEL)
    show_message(None, Gtk.MessageType.WARNING, "Clear the log?", "",
                 [("Cancel", Gtk.ResponseType.CANCEL), ("Clear log", Gtk.ResponseType.OK)],
                 destructive=Gtk.ResponseType.OK)
    assert shown[0]["warning"] == ["Clear log"]


def test_markup_is_drawn_and_plain_text_is_shown_as_typed(Gtk, monkeypatch):
    """A file name or a dictated word can hold & or <: plain text must never
    be read as markup."""
    from tuxtalk.ui_style import show_message
    shown = _answered(Gtk, monkeypatch, Gtk.ResponseType.OK)
    show_message(None, Gtk.MessageType.INFO, "Saved", "Tom & <Jerry>.txt")
    show_message(None, Gtk.MessageType.INFO, "Updated", "<b>v0.19</b>", markup=True)
    assert (shown[0]["text"], shown[0]["markup"]) == ("Tom & <Jerry>.txt", False)
    assert shown[1]["markup"] is True


def test_a_box_from_the_tray_opens_over_the_other_windows(Gtk, monkeypatch):
    from tuxtalk.ui_style import show_message
    centred, above = [], []
    monkeypatch.setattr(Gtk.Window, "set_position", lambda box, pos: centred.append(pos))
    monkeypatch.setattr(Gtk.Window, "set_keep_above", lambda box, on: above.append(on))
    _answered(Gtk, monkeypatch, Gtk.ResponseType.OK)
    show_message(None, Gtk.MessageType.INFO, "Update installed", "", keep_above=True)
    show_message(None, Gtk.MessageType.INFO, "Saved", "")
    assert centred == [Gtk.WindowPosition.CENTER] * 2
    assert above == [True]


def test_a_box_that_doesnt_wait_closes_itself_when_answered(Gtk, monkeypatch):
    """From the tray: the menu and its other windows (the menu window where
    there's no tray) keep working while it's open."""
    from tuxtalk.ui_style import show_message
    monkeypatch.setattr(Gtk.Dialog, "run", lambda box: pytest.fail("waited"))
    assert show_message(None, Gtk.MessageType.ERROR, "Update Failed", "No network.",
                        wait=False) is None
    box = _open_box(Gtk, "Update Failed")
    assert box is not None and not box.get_modal()
    box.response(Gtk.ResponseType.OK)
    assert _open_box(Gtk, "Update Failed") is None
