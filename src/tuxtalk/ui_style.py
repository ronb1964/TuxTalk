"""Shared GTK styling that must be installed screen-wide, and the widgets
built on it.

Kept in one place because TuxTalk draws dropdowns from two different processes
— the tray (welcome dialog) and Preferences — and neither inherits the other's
CSS. Duplicating the rule invites fixing it in one and forgetting the other.
"""

import ctypes
import logging
import os

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gtk, Gdk, GObject

from . import desktop_detect

logger = logging.getLogger(__name__)

# GTK3 defaults ComboBox popups to "menu mode", which positions the list so the
# selected item sits on top of the button. On the Preferences window that means
# the Device popup opens over the Model row and hides it, which reads as a
# rendering glitch. List mode anchors the popup under the button instead.
#
# appears-as-list is a style property, deprecated since GTK 3.20 but still
# honoured in 3.24.52 (verified: the resolved value flips False -> True). If a
# future GTK drops it the popups revert to menu mode — cosmetic only, nothing
# stops working.
#
# X11 only. On Wayland GTK3 clamps the window-relative list position against
# the monitor's global work area, which pushes the popup off screen on a second
# monitor (GTK #6105, TuxTalk #9). Wayland gets _drop_down_below() instead.
_DROPDOWN_CSS = b"""
combobox {
    -GtkComboBox-appears-as-list: 1;
}
"""

# The color picker's preset swatches draw a checkmark on the SELECTED color.
# The theme draws that checkmark white, which is invisible on the white swatch.
# GTK already tags each swatch .light or .dark by its own luminance, so key off
# that: a near-black check on light swatches (white, amber…) and a near-white
# check on dark ones (black, purple…). The checkmark is a symbolic icon that follows the node's `color`, so
# setting `color` on the swatch and its overlay child recolors just the check.
_SWATCH_CSS = b"""
colorswatch.light,
colorswatch.light overlay {
    color: #141414;
}
colorswatch.dark,
colorswatch.dark overlay {
    color: #f5f5f5;
}
"""

# Style properties resolve from the widget's own context, and a provider added
# to a window's context does NOT reach that window's children (verified: a child
# combo still read False). It has to go on the screen, so guard against stacking
# a fresh provider every time a dialog opens.
_installed = False


def is_x11(display):
    """Whether the Gdk *display* is X11 (XWayland included). By its GType
    name, so it needs no GdkX11 typelib: PyGObject calls the Python class
    X11Display, and checking that name for "GdkX11" kept list-style
    dropdowns off on X11 from 0.14.3 to 0.17."""
    gtype = getattr(type(display), "__gtype__", None)
    return gtype is not None and gtype.name == "GdkX11Display"


def _match_menu_width(combo, allocation):
    """size-allocate handler: make a wrap-mode popup as wide as its button
    (GTK sizes it to the longest item). The menu hangs from the button's text,
    not its edge, so the inset comes off or it overhangs on the right."""
    acc = combo.get_popup_accessible()
    menu = acc.get_widget() if acc is not None else None
    if menu is None:
        return
    width = allocation.width
    child = combo.get_child()
    if child is not None:
        inset = child.get_allocation().x - allocation.x
        if 0 < inset < width:
            width -= inset
    if menu.get_size_request()[0] != width:
        menu.set_size_request(width, -1)


# On Wayland, lists longer than this open as a grid; see _drop_down_below.
_GRID_AFTER = 15
_GRID_COLUMNS = 3


def _drop_down_below(combo):
    """Give a combo a wrap width, so on Wayland its popup hangs below the button.

    Plain menu mode lines the selected item up over the button and fixes its
    scroll position after the compositor has placed it, which on KDE opens it
    squashed, with scroll arrows, until the mouse moves. In wrap mode GTK
    anchors the menu below the button and the compositor places it, right on
    any monitor. A long list (the 33 languages) gets columns: GTK doesn't
    scroll a wrap-mode menu to the current choice, and as a grid it all fits.
    A combo that already has a wrap width is left alone.
    """
    if combo.get_wrap_width() == 0:
        model = combo.get_model()
        rows = len(model) if model is not None else 0
        combo.set_wrap_width(_GRID_COLUMNS if rows > _GRID_AFTER else 1)
        combo.connect("size-allocate", _match_menu_width)


def _on_widget_realize(widget, *_args):
    """Emission hook for every widget's "realize": each ComboBox gets the
    Wayland drop-down before it can be clicked. True keeps the hook installed."""
    if isinstance(widget, Gtk.ComboBox):
        _drop_down_below(widget)
    return True


# The style class of the windows drawn in TuxTalk's own look (Preferences and
# Help). prefs_style.css must be installed for the whole screen to reach a
# window's children at all, so every selector in it is scoped to this class:
# it styles these windows and no other. Keep the two in step.
STYLE_CLASS = "talktype-prefs"
_tuxtalk_style_installed = False


def use_tuxtalk_style(window):
    """Give *window* TuxTalk's own look (prefs_style.css), as Preferences and
    Help have: the stylesheet goes on the screen once per process, and
    *window* carries the class its selectors match. A sheet that can't be
    read leaves the theme's look, logged."""
    global _tuxtalk_style_installed
    if not _tuxtalk_style_installed:
        provider = Gtk.CssProvider()
        try:
            provider.load_from_path(os.path.join(os.path.dirname(__file__), "prefs_style.css"))
        except GLib.Error as e:
            logger.warning(f"Couldn't load TuxTalk's stylesheet: {e}")
            return
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _tuxtalk_style_installed = True
    window.get_style_context().add_class(STYLE_CLASS)


# For a theme that names no accent: a plain green, the same in the recording
# indicator and in Preferences' preview of it.
FALLBACK_ACCENT = (0x4c / 255, 0xaf / 255, 0x50 / 255)


def desktop_accent_rgb():
    """The desktop theme's accent color as (r, g, b) in 0..1: what the
    recording indicator paints in "System accent" mode, and what
    Preferences' swatch shows for it."""
    found, rgba = Gtk.Label().get_style_context().lookup_color("theme_selected_bg_color")
    return (rgba.red, rgba.green, rgba.blue) if found else FALLBACK_ACCENT


def apply_dropdown_list_style():
    """Install shared screen-wide GTK styling: ComboBox popups drop down below
    the button (not over it), through list mode on X11 and the wrap path on
    Wayland; and color-picker swatch checkmarks contrast with the swatch color
    so the selection is visible on white and black alike.

    Safe to call more than once; only the first call installs anything. Never
    raises — a styling failure must not stop a window from opening.
    """
    global _installed
    if _installed:
        return

    screen = Gdk.Screen.get_default()
    if screen is None:
        # No display (headless run). Nothing to style.
        return

    try:
        provider = Gtk.CssProvider()
        # X11 is the one backend where GTK3 places list popups right.
        list_mode = is_x11(screen.get_display())
        css = _DROPDOWN_CSS + _SWATCH_CSS if list_mode else _SWATCH_CSS
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        if not list_mode:
            # One hook for the whole process catches every combo, including
            # ones built later, so no window has to remember to opt in.
            GObject.add_emission_hook(Gtk.Widget, "realize", _on_widget_realize)
        _installed = True
    except Exception as e:
        logger.warning(f"Could not apply dropdown list style: {e}")


def forward_combo_scroll(combo):
    """Make scrolling over a ComboBox scroll the *page*, not change the combo.

    GTK ComboBoxes eat scroll events to cycle their selection. Swallowing the
    event alone would also stop the page scrolling while the pointer is over
    a dropdown, so the scroll is forwarded to the nearest ScrolledWindow
    ancestor, and True is still returned so the combo's value never changes.
    """
    def _on_scroll(widget, event):
        """Scroll the page the combo sits in, never the combo's own value."""
        sw = widget.get_ancestor(Gtk.ScrolledWindow)
        adj = sw.get_vadjustment() if sw is not None else None
        if adj is not None:
            step = adj.get_step_increment()
            if not step or step <= 0:
                step = 40.0
            direction = event.direction
            if direction == Gdk.ScrollDirection.UP:
                adj.set_value(adj.get_value() - step)
            elif direction == Gdk.ScrollDirection.DOWN:
                adj.set_value(adj.get_value() + step)
            elif direction == Gdk.ScrollDirection.SMOOTH:
                ok, _dx, dy = event.get_scroll_deltas()
                if ok:
                    adj.set_value(adj.get_value() + dy * step)
        return True  # never let the combo change its value on scroll

    combo.connect("scroll-event", _on_scroll)


# The amber of TuxTalk's warnings (an unsafe setting, a backup never saved,
# dictations in a report's log).
WARNING_AMBER = "#e6aa3c"


# The share of the screen's height a window may count on, whatever the work
# area says. Under Wayland GTK 3 isn't told about panels and docks, so the work
# area is the whole screen: on Pop!_OS at 1280x800, Setup Complete filled it
# and its buttons sat behind COSMIC's dock (in the VM). Three quarters is
# also as tall as COSMIC opens a window, title bar included; asked for more,
# it cut the bottom off.
USABLE_SHARE = 0.75


def _usable_height(monitor):
    return min(monitor.get_workarea().height, int(monitor.get_geometry().height * USABLE_SHARE))


def _monitor_for(near):
    """The monitor showing window *near* (it has to be on screen). Without
    one, the shortest monitor: a window with no parent may open on any of
    them, and Wayland says which only once it's shown, after it's sized
    (Sway with two monitors, 2026-10-22: Setup Complete opened sized for the
    taller one, its buttons below the shorter one's edge). None when there's
    no monitor."""
    display = Gdk.Display.get_default()
    if display is None:
        return None
    shown = near.get_window() if near is not None else None
    if shown is not None:
        return display.get_monitor_at_window(shown)
    monitors = [display.get_monitor(i) for i in range(display.get_n_monitors())]
    return min(monitors, key=_usable_height, default=None)


def workarea_height(near=None):
    """A cautious usable height, in logical pixels, of the monitor for window
    *near* (_monitor_for); None when unknown."""
    try:
        monitor = _monitor_for(near)
        return _usable_height(monitor) if monitor is not None else None
    except Exception as e:
        logger.warning(f"Couldn't read the screen's usable height: {e}")
        return None


def workarea_width():
    """The usable width, in logical pixels, of the narrowest monitor (a
    window may open on any; _monitor_for picks the shortest, which beside a
    portrait screen is the wide one); None when unknown."""
    try:
        display = Gdk.Display.get_default()
        if display is None:
            return None
        monitors = [display.get_monitor(i) for i in range(display.get_n_monitors())]
        return min((m.get_workarea().width for m in monitors if m is not None), default=None)
    except Exception as e:
        logger.warning(f"Couldn't read the screen's usable width: {e}")
        return None


def fit_dialog_to_screen(dialog, width, desired_height):
    """Open a dialog *width* wide and *desired_height* tall (below 0: as tall
    as its content), resizable. Onboarding dialogs are tall; on a short
    screen fit_window_to_screen takes the height down when the window is
    created, and content in a Gtk.ScrolledWindow then scrolls instead of
    running the buttons off the screen."""
    dialog.set_resizable(True)
    dialog.set_default_size(width, desired_height if desired_height > 0 else -1)


# What the desktop's title bar adds above a window when it draws one; GTK
# sizes the window without it. When GTK draws the title bar itself (its
# "csd" windows, as on COSMIC and GNOME), it's already in the size.
_TITLE_BAR = 40


def fit_window_to_screen(window):
    """Keep *window* no taller than workarea_height() less its title bar, so
    its buttons stay clear of the screen's edge and of docks Wayland doesn't
    report (About and Setup Complete sat behind COSMIC's dock at 1280x800).

    A window whose content can't shrink that far gives up height from the
    scrolling areas it has (_shrink_scrollers), and if that isn't enough
    its content goes in one (_scroll_body); a dialog's own button row stays
    below it, always in view. Called for every window by
    fit_windows_to_screen(). Measured on the monitor of the window's parent,
    or for a window without one the shortest (_monitor_for). Never raises: a
    window that doesn't fit beats one that doesn't open."""
    try:
        avail = workarea_height(window.get_transient_for())
        if not avail or window.is_maximized():
            return
        drawn_by_gtk = window.get_style_context().has_class("csd")
        cap = avail if drawn_by_gtk else avail - _TITLE_BAR
        _shrink_scrollers(window, cap)
        # Measured before the content goes in a scrolling area, which asks for
        # a wrapping label's height as if it were one line.
        natural = window.get_preferred_height_for_width(window.get_size()[0])[1]
        scrolled = _scroll_body(window) if _min_height(window) > cap else None
        width, height = window.get_size()
        if scrolled is not None and desktop_detect.get_desktop_environment() == "cosmic":
            # COSMIC opens a window at a height of its own choosing (about
            # 420 px at 1280x800) unless its minimum asks for more, so there
            # the scrolling part asks for what fills the cap, not a sliver.
            # Elsewhere the opening size is honoured, and a smaller minimum
            # lets the window be made shorter afterwards.
            rest = _min_height(window) - _min_height(scrolled)
            content = scrolled.get_child().get_preferred_height_for_width(width)[1]
            scrolled.set_min_content_height(max(0, min(content, cap - rest)))
        # A window that now scrolls opens as tall as its content, up to the cap.
        wanted = max(height, window.get_allocated_height(), natural if scrolled else 0)
        if wanted > cap or scrolled:
            height = min(wanted, cap)
            # Wayland goes by the default size when the window first shows,
            # X11 by the resize.
            window.set_default_size(width, height)
            window.resize(width, height)
    except Exception as e:
        logger.warning(f"Could not fit {window.get_title()!r} to the screen: {e}")


def _min_height(widget):
    """The height GTK won't let *widget* go below: its minimum at its
    minimum width. Asked without a width, a wrapping label counts as one
    line, so a dialog with a long message looked as if it fitted and opened
    3837 px tall on a 1280x800 screen."""
    return widget.get_preferred_size()[0].height


def _shrink_scrollers(window, cap):
    """Take height off the minimums the scrolling areas in *window* ask for,
    the tallest first, until the window's minimum fits *cap*. Setup Complete
    keeps its buttons under its own scrolling text, and About its links
    under What's New; shrinking those keeps them in view, where scrolling
    the whole window would push them out of it. Measured again after each,
    since one beside a taller one, or on a hidden page, changes nothing."""
    scrollers = []

    def collect(w):
        if not w.get_visible():
            return                      # nor anything in it
        if isinstance(w, Gtk.ScrolledWindow):
            scrollers.append(w)
        elif isinstance(w, Gtk.Container):
            w.foreach(collect)
    window.foreach(collect)
    for scroller in sorted(scrollers, key=Gtk.ScrolledWindow.get_min_content_height,
                           reverse=True):
        too_tall = _min_height(window) - cap
        asked = scroller.get_min_content_height()
        if too_tall <= 0 or asked <= 0:
            return
        scroller.set_min_content_height(max(0, asked - too_tall))


def _scroll_body(window):
    """Move the window's content, but not a dialog's button row, into a
    vertical scrolling area, and return that (None if there's no content)."""
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_propagate_natural_height(True)
    scrolled.set_propagate_natural_width(True)
    if isinstance(window, Gtk.Dialog):
        body = window.get_content_area()
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=body.get_spacing())
        for part in body.get_children():
            if Gtk.Buildable.get_name(part) == "action_box":    # GtkDialog's own name
                continue
            expand, fill, padding, pack_type = (body.child_get_property(part, name) for name in
                                                ("expand", "fill", "padding", "pack-type"))
            body.remove(part)
            pack = inner.pack_start if pack_type == Gtk.PackType.START else inner.pack_end
            pack(part, expand, fill, padding)
        scrolled.add(inner)
        body.pack_start(scrolled, True, True, 0)
        body.reorder_child(scrolled, 0)
    else:
        inner = window.get_child()
        if inner is None:
            return None
        window.remove(inner)
        scrolled.add(inner)
        window.add(scrolled)
    inner.show()
    scrolled.get_child().show()     # the viewport GTK puts round a plain box
    scrolled.show()
    return scrolled


def _on_window_realize(widget, *_args):
    """Emission hook for every widget's "realize": a toplevel window is
    fitted then, sized but not yet on screen. True keeps the hook installed."""
    if isinstance(widget, Gtk.Window) and widget.get_window_type() == Gtk.WindowType.TOPLEVEL:
        fit_window_to_screen(widget)
    return True


# The hook's id once installed (for tests to take it out again).
_fitting_hook = None


def fit_windows_to_screen():
    """Fit every window this process opens to the screen
    (fit_window_to_screen), including ones built later, so no window has to
    remember to opt in. Called once at each process's start; safe again."""
    global _fitting_hook
    if _fitting_hook is None:
        _fitting_hook = GObject.add_emission_hook(Gtk.Widget, "realize", _on_window_realize)


EMOJI_FONT = os.path.join(os.path.dirname(__file__), "fonts", "tuxtalk-emoji.ttf")


def load_emoji_font():
    """Give this process TuxTalk's own emoji (packaging/build-emoji-font.py),
    so they show on a computer without an emoji font too: there they were
    boxes with the character's code in them (issue #19, Mint). Only this
    process sees the font; nothing is installed. Before the first window,
    so GTK's font list has it."""
    try:
        fontconfig = ctypes.CDLL("libfontconfig.so.1")
    except OSError as e:
        logger.warning(f"No fontconfig to give TuxTalk's emoji font to: {e}")
        return
    fontconfig.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    if not fontconfig.FcConfigAppFontAddFile(None, EMOJI_FONT.encode()):
        logger.warning(f"Couldn't load TuxTalk's emoji font from {EMOJI_FONT}")


def prepare_windows():
    """What every TuxTalk process does before its first window: emoji that
    show everywhere, and windows that fit the screen."""
    load_emoji_font()
    fit_windows_to_screen()


# Onboarding screens that scroll. While there's more below, the bottom edge of
# the text fades into a shadow (GTK draws the "undershoot" only then) and a
# "More below" bar sits above the buttons.
_MORE_BELOW_CSS = b"""
scrolledwindow.tt-more-scroll undershoot.bottom {
    background-image: linear-gradient(to top, @theme_bg_color, alpha(@theme_bg_color, 0));
    background-size: 100% 40px;
    background-position: bottom;
    background-repeat: no-repeat;
}
/* The theme's own text colour: accent-blue text on the accent tint was
   unreadable on GNOME's dark theme (Ron, Silverblue, 2026-10-16). */
button.tt-more-below,
button.tt-more-below label {
    color: @theme_fg_color;
}
button.tt-more-below {
    background-image: none;
    background-color: alpha(@theme_selected_bg_color, 0.25);
    border: 1px solid @theme_selected_bg_color;
    border-radius: 6px;
    padding: 2px 12px;
    font-weight: bold;
}
button.tt-more-below:hover {
    background-color: alpha(@theme_selected_bg_color, 0.4);
}
"""
_more_below_css_installed = False


def more_below_bar(scrolled, label="More below: scroll down to see everything",
                   tooltip="There's more on this page"):
    """Make it obvious that `scrolled` has more to read, and return a
    "▼ More below ▼" bar to pack just above the window's buttons.

    GTK's scrollbar hides until the mouse moves over it, so an onboarding
    screen that ends neatly at the window's edge looks finished, and people
    clicked "Set it up" without seeing what was below (Ron, 2026-10-16). So:
    the scrollbar is always shown, the bottom edge fades while there's more,
    and the bar shows only while there's more. Clicking it scrolls down most
    of a page. It keeps its height when hidden, so nothing jumps around.
    """
    global _more_below_css_installed
    scrolled.set_overlay_scrolling(False)
    scrolled.get_style_context().add_class("tt-more-scroll")
    if not _more_below_css_installed:
        screen = Gdk.Screen.get_default()
        if screen is not None:
            provider = Gtk.CssProvider()
            provider.load_from_data(_MORE_BELOW_CSS)
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            _more_below_css_installed = True

    button = Gtk.Button(label=f"▼  {label}  ▼")
    button.get_style_context().add_class("tt-more-below")
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.set_halign(Gtk.Align.CENTER)
    button.set_margin_top(6)
    button.set_margin_bottom(2)
    button.set_tooltip_text(tooltip)
    adj = scrolled.get_vadjustment()

    def more_to_see():
        # A few pixels of slack: the last bit is the content's bottom margin.
        return adj.get_value() + adj.get_page_size() < adj.get_upper() - 8

    def update(*_args):
        # set_child_visible hides it but keeps its height in the layout.
        button.set_child_visible(more_to_see())

    def scroll_down(_button):
        adj.set_value(min(adj.get_value() + adj.get_page_size() * 0.8,
                          adj.get_upper() - adj.get_page_size()))

    button.connect("clicked", scroll_down)
    adj.connect("value-changed", update)
    adj.connect("changed", update)  # the content or the window was resized
    update()
    return button


def message_dialog(closable=True, **kwargs):
    """A Gtk.MessageDialog in the desktop's own window frame. Takes the same
    keyword arguments; use it instead of Gtk.MessageDialog everywhere.

    closable=False drops the title bar's close button, for a box that closes
    itself when its work is done ("Checking for Updates...").

    Gtk.MessageDialog installs an empty titlebar of its own, which makes GTK
    draw the frame instead of the desktop. On KDE that frame has square
    corners while every other TuxTalk window gets KDE's rounded one. Without
    the titlebar KDE (and X11 window managers) frame it like any window, and
    GNOME gives it a normal title bar with rounded corners.
    """
    dialog = Gtk.MessageDialog(**kwargs)
    dialog.set_titlebar(None)
    if not dialog.get_title():
        dialog.set_title("TuxTalk")
    # Asked for, not guessed from buttons=NONE: most questions pass NONE and
    # add their own buttons afterwards, and those keep their X.
    if not closable:
        dialog.set_deletable(False)
    return dialog


def show_message(parent, kind, title, text="", buttons=Gtk.ButtonsType.OK,
                 keep_above=False):
    """Show a message box over *parent* and wait for an answer: *title* in
    bold, *text* under it. Returns the button pressed (a Gtk.ResponseType).

    keep_above keeps it over other windows, for a box shown with no parent
    of its own (from the tray) that would otherwise open behind them.
    """
    dialog = message_dialog(transient_for=parent, modal=True, message_type=kind,
                            buttons=buttons, text=title)
    if text:
        dialog.format_secondary_text(text)
    if keep_above:
        dialog.set_keep_above(True)
    response = dialog.run()
    dialog.destroy()
    return response


class Fold(Gtk.Box):
    """Settings most people never need, closed behind a button that names
    them, with a short dim summary beside it. The button opens them right
    below and turns into *hide_label*.

    GTK's expander puts a small arrow by the title, and people missed it
    (Ron, 2026-10-18), so every fold in TuxTalk is one of these. Put the
    settings in .content (a Gtk.Grid). on_toggle(opening) runs after each
    open or close, for scrolling the settings into view.
    """

    def __init__(self, show_label, hide_label, on_toggle=None):
        """A closed fold: a button reading *show_label* (*hide_label* once
        open) with the summary beside it, and its settings in a grid that
        slides open below. *on_toggle* gets True or False each time it opens
        or closes."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._labels = (show_label, hide_label)
        self._on_toggle = on_toggle
        self.header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.button = Gtk.Button(label=show_label)
        self.button.connect("clicked", lambda _b: self.set_open(not self.is_open()))
        self.summary = Gtk.Label(xalign=0)
        self.summary.set_line_wrap(True)
        self.header.pack_start(self.button, False, False, 0)
        self.header.pack_start(self.summary, False, False, 0)
        # A revealer, not a hidden widget: the window's show_all() leaves it closed.
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(150)
        self.content = Gtk.Grid(column_spacing=10, row_spacing=10)
        self.content.set_margin_top(10)
        self.content.set_margin_start(20)
        self.revealer.add(self.content)
        self.pack_start(self.header, False, False, 0)
        self.pack_start(self.revealer, False, False, 0)
        self.set_summary("")

    def is_open(self):
        return self.revealer.get_reveal_child()

    def set_open(self, opening):
        if opening == self.is_open():
            return
        self.revealer.set_reveal_child(opening)
        self.button.set_label(self._labels[opening])
        if self._on_toggle is not None:
            self._on_toggle(opening)

    def set_summary(self, markup, dim=True):
        """*dim* False for a warning, which must not fade into the background."""
        self.summary.set_markup(markup)
        self.summary.set_opacity(0.65 if dim else 1.0)
