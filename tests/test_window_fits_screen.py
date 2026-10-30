"""Every TuxTalk window fits the screen with its buttons showing.

On Pop!_OS at 1280x800 About and Setup Complete were as tall as the screen
and their buttons sat behind COSMIC's dock (VM, 2026-10-21). One rule in
ui_style fits every window as it's created, so none has to remember to.
These build real windows without showing them.
"""
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import GObject, Gtk  # noqa: E402

from tuxtalk import ui_style  # noqa: E402

USABLE = 600                   # three quarters of an 800 px screen
CAP = USABLE - ui_style._TITLE_BAR
MEASURE_THE_MONITOR = ui_style.workarea_height    # short_screen stands in for it


@pytest.fixture(autouse=True)
def short_screen(monkeypatch):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    monkeypatch.setattr(ui_style, "workarea_height", lambda near=None: USABLE)


def _tall_label():
    label = Gtk.Label(label="What's new")
    label.set_size_request(400, 1200)
    return label


def _min_height(window):
    """The height GTK holds the window to: its minimum at its minimum width."""
    return window.get_preferred_size()[0].height


def _ancestors(widget):
    while widget is not None:
        yield widget
        widget = widget.get_parent()


@pytest.mark.parametrize("desktop", ["COSMIC", "KDE"])
def test_a_tall_dialog_scrolls_and_keeps_its_buttons_in_view(monkeypatch, desktop):
    """About: a long What's New and a Close button."""
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)
    dialog = Gtk.Dialog(title="About TuxTalk")
    label = _tall_label()
    dialog.get_content_area().pack_start(label, True, True, 0)
    close = dialog.add_button("Close", Gtk.ResponseType.CLOSE)
    dialog.get_content_area().show_all()
    try:
        assert _min_height(dialog) > CAP
        ui_style.fit_window_to_screen(dialog)
        if desktop == "COSMIC":
            # As tall as it may be: COSMIC opens a window short unless its
            # minimum asks for more.
            assert CAP - 10 <= _min_height(dialog) <= CAP
        else:
            # It opens at the cap and can still be made shorter.
            assert _min_height(dialog) < CAP / 2
            assert dialog.get_size()[1] == CAP
        assert any(isinstance(w, Gtk.ScrolledWindow) for w in _ancestors(label))
        assert not any(isinstance(w, Gtk.ScrolledWindow) for w in _ancestors(close))
    finally:
        dialog.destroy()


def test_a_window_with_its_own_scrolling_text_shrinks_that_and_keeps_its_buttons():
    """Setup Complete: scrolling text with Open Preferences and Start Using
    TuxTalk under it, inside its content rather than the dialog's own button
    row. Scrolling the whole window pushed them out of view (Pop!_OS VM,
    2026-10-21); the text gives up the height instead."""
    dialog = Gtk.Dialog(title="TuxTalk - Ready!")
    text = Gtk.ScrolledWindow()
    text.set_min_content_height(700)
    text.add(_tall_label())
    start = Gtk.Button(label="Start Using TuxTalk!")
    dialog.get_content_area().pack_start(text, True, True, 0)
    dialog.get_content_area().pack_start(start, False, False, 0)
    dialog.get_content_area().show_all()
    try:
        ui_style.fit_window_to_screen(dialog)
        assert _min_height(dialog) <= CAP
        assert text.get_min_content_height() > 400      # as much text as fits
        assert start.get_parent() is dialog.get_content_area()
    finally:
        dialog.destroy()


def test_scrolling_areas_side_by_side_both_give_up_height():
    """Shrinking one of two equally tall areas changes nothing by itself;
    the window is measured again and the other gives way too, rather than
    the whole window being wrapped."""
    dialog = Gtk.Dialog(title="Two columns")
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    for _ in range(2):
        column = Gtk.ScrolledWindow()
        column.set_min_content_height(800)
        column.add(_tall_label())
        row.pack_start(column, True, True, 0)
    start = Gtk.Button(label="OK")
    dialog.get_content_area().pack_start(row, True, True, 0)
    dialog.get_content_area().pack_start(start, False, False, 0)
    dialog.get_content_area().show_all()
    try:
        ui_style.fit_window_to_screen(dialog)
        assert _min_height(dialog) <= CAP
        assert start.get_parent() is dialog.get_content_area()
    finally:
        dialog.destroy()


def test_a_message_with_a_lot_of_text_fits_too():
    dialog = ui_style.message_dialog(message_type=Gtk.MessageType.INFO,
                                     buttons=Gtk.ButtonsType.OK, text="Uninstall TuxTalk?")
    dialog.format_secondary_text("A line that goes on.\n" * 80)
    dialog.get_content_area().show_all()
    try:
        ui_style.fit_window_to_screen(dialog)
        assert _min_height(dialog) <= CAP
        ok = dialog.get_widget_for_response(Gtk.ResponseType.OK)
        assert not any(isinstance(w, Gtk.ScrolledWindow) for w in _ancestors(ok))
    finally:
        dialog.destroy()


def test_a_long_wrapping_message_fits_too():
    """A wrapping label measured without a width counts as one line; at its
    narrowest it is thousands of pixels tall, and GTK held the window to
    that (3837 px on a 1280x800 screen)."""
    dialog = Gtk.Dialog(title="TuxTalk")
    ui_style.fit_dialog_to_screen(dialog, 480, -1)
    label = Gtk.Label(label="A long sentence that wraps over several lines. " * 60)
    label.set_line_wrap(True)
    dialog.get_content_area().pack_start(label, True, True, 0)
    ok = dialog.add_button("OK", Gtk.ResponseType.OK)
    dialog.get_content_area().show_all()
    try:
        assert _min_height(dialog) > CAP
        ui_style.fit_window_to_screen(dialog)
        assert _min_height(dialog) <= CAP
        assert dialog.get_size() == (480, CAP)
        assert not any(isinstance(w, Gtk.ScrolledWindow) for w in _ancestors(ok))
    finally:
        dialog.destroy()


def test_a_tall_plain_window_scrolls_as_a_whole():
    window = Gtk.Window(title="Voice Commands")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.pack_start(_tall_label(), True, True, 0)
    box.pack_start(Gtk.Button(label="Close"), False, False, 0)
    window.add(box)
    box.show_all()
    try:
        ui_style.fit_window_to_screen(window)
        assert _min_height(window) <= CAP
        assert isinstance(window.get_child(), Gtk.ScrolledWindow)
    finally:
        window.destroy()


def test_a_window_that_already_fits_is_left_as_it_is():
    dialog = Gtk.Dialog(title="TuxTalk")
    label = Gtk.Label(label="Settings applied")
    dialog.get_content_area().pack_start(label, False, False, 0)
    dialog.add_button("OK", Gtk.ResponseType.OK)
    dialog.set_default_size(400, 200)
    dialog.get_content_area().show_all()
    try:
        ui_style.fit_window_to_screen(dialog)
        assert label.get_parent() is dialog.get_content_area()
        assert dialog.get_size() == (400, 200)
    finally:
        dialog.destroy()


def test_a_window_asking_for_more_than_the_screen_opens_shorter():
    """Preferences-style: content that scrolls by itself, a tall default size."""
    window = Gtk.Window(title="TuxTalk Preferences")
    scrolled = Gtk.ScrolledWindow()
    scrolled.add(_tall_label())
    window.add(scrolled)
    window.set_default_size(500, 900)
    try:
        ui_style.fit_window_to_screen(window)
        assert window.get_size()[1] == CAP
        assert window.get_child() is scrolled
    finally:
        window.destroy()


def test_every_window_is_fitted_as_it_is_created(monkeypatch):
    """Through the hook, as the app does it: created (not shown), the tall
    dialog already fits, its Close button outside the scrolling part."""
    monkeypatch.setattr(ui_style, "_fitting_hook", None)
    ui_style.fit_windows_to_screen()
    dialog = Gtk.Dialog(title="About TuxTalk")
    try:
        label = _tall_label()
        dialog.get_content_area().pack_start(label, True, True, 0)
        close = dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.get_content_area().show_all()
        dialog.realize()
        assert _min_height(dialog) <= CAP
        assert any(isinstance(w, Gtk.ScrolledWindow) for w in _ancestors(label))
        assert not any(isinstance(w, Gtk.ScrolledWindow) for w in _ancestors(close))
    finally:
        dialog.destroy()
        GObject.signal_remove_emission_hook(GObject.signal_lookup("realize", Gtk.Widget),
                                            ui_style._fitting_hook)


def _monitor(height):
    geometry = SimpleNamespace(height=height, width=1280)
    return SimpleNamespace(get_workarea=lambda: geometry, get_geometry=lambda: geometry)


def test_a_window_without_a_parent_fits_the_shortest_monitor(monkeypatch):
    """It may open on either monitor, and Wayland says which only after it's
    sized: fitted to the taller one, Setup Complete ran its buttons off the
    shorter one (Sway with two monitors, VM, 2026-10-22)."""
    tall, short = _monitor(1600), _monitor(800)
    display = SimpleNamespace(get_primary_monitor=lambda: tall, get_n_monitors=lambda: 2,
                              get_monitor=lambda i: (tall, short)[i])
    monkeypatch.setattr(ui_style, "workarea_height", MEASURE_THE_MONITOR)
    monkeypatch.setattr(ui_style.Gdk.Display, "get_default", lambda: display)
    window = Gtk.Window(title="Voice Commands")
    scrolled = Gtk.ScrolledWindow()
    scrolled.add(_tall_label())
    window.add(scrolled)
    window.set_default_size(500, 1100)
    try:
        ui_style.fit_window_to_screen(window)
        assert window.get_size()[1] == CAP
    finally:
        window.destroy()


def test_popups_are_never_touched():
    """Floating labels and the recording indicator are popups, sized to fit
    what they show."""
    popup = Gtk.Window(type=Gtk.WindowType.POPUP)
    popup.add(_tall_label())
    try:
        ui_style._on_window_realize(popup)
        assert isinstance(popup.get_child(), Gtk.Label)
    finally:
        popup.destroy()


@pytest.mark.parametrize("program", ["tray_main", "prefs_main", "service_main"])
def test_each_program_prepares_its_windows_before_it_opens_one(program, request):
    """The tray (and its dialogs), Preferences and the dictation service (the
    recording indicator) each run as their own process: each names itself
    (TuxTalk's icon, not a generic one), then prepares its windows (the
    fitting and the emoji font), before it opens anything."""
    assert request.getfixturevalue(program)()[:2] == ["names itself", "prepares windows"]


class _Opening(BaseException):
    """Stops a program's main() once its windows are prepared, or when one
    shows before that; a BaseException, so its own `except Exception` lets
    it through."""


def _shown(done):
    done.append("shows a window")
    raise _Opening


@pytest.mark.parametrize("program, argv", [("explain", ["nothing_said"]),
                                           ("fix_word_dialog", []),
                                           ("transcribe_window", [])])
def test_each_small_window_program_prepares_its_windows(program, argv, monkeypatch):
    """Fix a Word, Transcribe a File and the explanation windows run as
    processes of their own too."""
    import importlib
    from tuxtalk import desktop_identity, single_instance
    done = []
    monkeypatch.setattr(desktop_identity, "apply", lambda: done.append("names itself"))

    def prepare():
        done.append("prepares windows")
        raise _Opening
    monkeypatch.setattr(ui_style, "prepare_windows", prepare)
    monkeypatch.setattr(single_instance, "claim_window", lambda *a: (True, None))
    for name in ("show", "show_all"):
        monkeypatch.setattr(Gtk.Widget, name, lambda widget: _shown(done))
    with pytest.raises(_Opening):
        importlib.import_module(f"tuxtalk.{program}").main(argv)
    assert done == ["names itself", "prepares windows"]


def test_a_title_bar_gtk_draws_is_counted_once(monkeypatch):
    """On COSMIC and GNOME GTK draws the title bar inside the window's own
    size; taking the desktop's off as well left About needlessly short."""
    window = Gtk.Window(title="About TuxTalk")
    window.get_style_context().add_class("csd")
    window.add(Gtk.ScrolledWindow())
    window.set_default_size(500, 900)
    try:
        ui_style.fit_window_to_screen(window)
        assert window.get_size()[1] == USABLE
    finally:
        window.destroy()
