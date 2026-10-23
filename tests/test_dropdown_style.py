"""Dropdown popups must render as a list below the button, not over it.

By default GTK3 puts a ComboBox popup in "menu mode": it positions the list so
the SELECTED item sits on top of the button, which means the popup covers the
row above and the widget itself. On the Preferences window that reads as a
glitch — the Model row disappears behind the Device popup.

Setting the appears-as-list style property switches it to a normal drop-down
list anchored under the button.

Three things are easy to get wrong here, and all are covered below:

1. The property is deprecated (GTK 3.20+). It still resolves in GTK 3.24.52,
   but a future GTK could drop it, and this test says so plainly rather than
   leaving a silent cosmetic regression.

2. Scope. prefs.py attaches prefs_style.css to the WINDOW's style context, and
   a window-scoped provider does NOT reach child widgets — measured: the child
   combo still read False. The style must be installed for the whole screen or
   it silently does nothing.

3. Wayland. List mode is X11-only: on Wayland GTK3 can place the list popup off
   screen on a second monitor, so the dropdown can't be changed (issue #9).
   Plain menu mode isn't good enough either: on KDE it opens squashed, with
   scroll arrows and half-hidden items, until the mouse moves. So on Wayland
   every combo gets wrap_width=1, GTK's "drop the menu below the button" path,
   which the compositor places, and the menu is made as wide as the button.
"""

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def gtk():
    """Import Gtk, skipping when there is no usable display."""
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        if not Gtk.init_check(None)[0]:
            pytest.skip("no display available")
        return Gtk
    except (ImportError, ValueError) as exc:
        pytest.skip(f"GTK stack unavailable: {exc}")


class _FakeDisplay:
    """Stands in for a GdkDisplay; only its GType matters."""


def _display_of(gtype):
    """A stand-in display of the given GType, under the Python class name
    PyGObject really gives an X11 display (checked under Xvfb, 2026-10-20)."""
    return type("X11Display", (_FakeDisplay,), {"__gtype__": gtype})()


def test_list_mode_only_on_x11():
    """Issue #9: on Wayland, GTK3's list-mode popup positions itself with
    window-relative coordinates clamped against the monitor's GLOBAL work area
    (gtkcombobox.c gtk_combo_box_list_position). On a monitor that does not
    start at 0,0 that shoves the popup off screen, so the value can't be
    changed. Menu mode uses xdg_popup positioning and works."""
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkX11", "3.0")
    from gi.repository import Gdk, GdkX11
    from tuxtalk.ui_style import is_x11

    assert is_x11(_display_of(GdkX11.X11Display.__gtype__)) is True
    assert is_x11(_display_of(Gdk.Display.__gtype__)) is False


def test_list_mode_never_raises_on_odd_input():
    from tuxtalk.ui_style import is_x11

    assert is_x11(None) is False


_FRESH_PROCESS = """
import gi
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk
from tuxtalk.ui_style import apply_dropdown_list_style, is_x11

def pump():
    while Gtk.events_pending():
        Gtk.main_iteration()

combo = Gtk.ComboBoxText()
combo.append_text("CPU")
combo.set_active(0)
win = Gtk.Window()
win.add(combo)
win.show_all()
pump()
before = combo.style_get_property("appears-as-list")
apply_dropdown_list_style()
pump()
print(before, combo.style_get_property("appears-as-list"), is_x11(Gdk.Display.get_default()))
"""


def test_helper_sets_the_right_mode_for_this_display(gtk):
    """A real ComboBox must end up in list mode on X11 and menu mode elsewhere.

    In a fresh process: the style is screen-wide and stays for good once any
    window (Preferences, first run) has installed it in this one."""
    import os
    import subprocess
    import sys
    env = {**os.environ, "GDK_BACKEND": "x11"}     # the test display, as conftest opens it
    out = subprocess.run([sys.executable, "-c", _FRESH_PROCESS], env=env, capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-800:]
    before, after, x11 = out.stdout.split()[-3:]
    assert before == "False", (
        "Expected GTK's default menu mode before the helper runs. If this fails, "
        "the theme already sets it and this helper may be unnecessary.")
    assert after == x11, (
        "appears-as-list did not match the display backend. On X11 GTK may have "
        "finally dropped this deprecated property (cosmetic only); on Wayland it "
        "must stay off or popups can open off screen (issue #9).")


def _pump(gtk):
    while gtk.events_pending():
        gtk.main_iteration()


def test_drop_down_below_uses_the_wrap_path_and_full_width(gtk):
    """The Wayland treatment for one combo: wrap path, menu as wide as it."""
    from tuxtalk.ui_style import _drop_down_below

    combo = gtk.ComboBoxText()
    for t in ("CPU", "CUDA (GPU)", "Vulkan (any GPU)"):
        combo.append_text(t)
    combo.set_active(2)
    combo.set_size_request(400, -1)
    win = gtk.Window()
    win.add(combo)

    _drop_down_below(combo)
    win.show_all()
    _pump(gtk)

    assert combo.get_wrap_width() == 1
    menu = combo.get_popup_accessible().get_widget()
    # The menu hangs from the button's text, so it's as wide as the button
    # minus that inset: its right edge lines up with the button's.
    inset = combo.get_child().get_allocation().x - combo.get_allocation().x
    assert inset >= 0
    assert menu.get_size_request()[0] == combo.get_allocated_width() - inset
    assert menu.get_size_request()[0] > 300
    win.destroy()


def test_drop_down_below_leaves_an_explicit_wrap_width_alone(gtk):
    from tuxtalk.ui_style import _drop_down_below

    combo = gtk.ComboBoxText()
    combo.set_wrap_width(3)
    _drop_down_below(combo)
    assert combo.get_wrap_width() == 3


@pytest.mark.parametrize("items, columns", [(6, 1), (12, 1), (15, 1), (16, 3), (33, 3)])
def test_long_lists_open_as_a_grid(gtk, items, columns):
    """On Wayland the list hangs below the button and starts at the top, so
    in one column the 33 languages run off the screen with the current one
    out of sight. In three columns they fit, all visible, the current one
    highlighted."""
    from tuxtalk.ui_style import _drop_down_below

    combo = gtk.ComboBoxText()
    for i in range(items):
        combo.append_text(f"Language {i}")
    _drop_down_below(combo)
    assert combo.get_wrap_width() == columns


def test_helper_gives_combos_the_right_popup_for_this_display(gtk):
    """Combos realised after the helper runs: wrap path on Wayland only."""
    from gi.repository import Gdk
    from tuxtalk.ui_style import apply_dropdown_list_style, is_x11

    apply_dropdown_list_style()
    combo = gtk.ComboBoxText()
    combo.append_text("F8")
    win = gtk.Window()
    win.add(combo)
    win.show_all()
    _pump(gtk)

    expected = 0 if is_x11(Gdk.Display.get_default()) else 1
    assert combo.get_wrap_width() == expected
    win.destroy()


def test_helper_is_idempotent(gtk):
    """The tray calls it once and Preferences calls it again in-process."""
    from tuxtalk.ui_style import apply_dropdown_list_style

    apply_dropdown_list_style()
    apply_dropdown_list_style()  # must not raise or stack providers unboundedly


@pytest.mark.parametrize(
    "module",
    [
        "src/tuxtalk/prefs.py",           # Model, Device, Language, hotkeys
        "src/tuxtalk/welcome_dialog.py",  # first-run model picker
        "src/tuxtalk/fix_word_dialog.py", # which recent dictation
    ],
)
def test_every_window_with_dropdowns_applies_the_style(module):
    """Both live in different processes; neither inherits the other's CSS."""
    text = (ROOT / module).read_text()
    assert "apply_dropdown_list_style" in text, (
        f"{module} builds dropdowns but never calls apply_dropdown_list_style(), "
        f"so its popups will still open in menu mode covering the row above."
    )
