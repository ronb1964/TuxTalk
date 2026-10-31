"""The splash screen before the first-run welcome: TuxTalk's icon on a dark
rounded box that fades in, glows and fades out again.

fade_in is the ramp the welcome screen fades in with too.
"""

import os

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from .logger import setup_logger

logger = setup_logger(__name__)

_APP_ID = "io.github.ronb1964.TuxTalk"
_SCALABLE = f"share/icons/hicolor/scalable/apps/{_APP_ID}.svg"

# The glow's steps, one every 100 ms: (blur in px, opacity) of each shadow.
# From step 9 a wider, fainter second shadow joins in.
_GLOW_STEPS = (
    ((10, 0.4),), ((15, 0.45),), ((20, 0.5),), ((25, 0.55),), ((30, 0.6),),
    ((35, 0.65),), ((40, 0.7),), ((45, 0.75),), ((50, 0.8),),
    ((55, 0.82), (90, 0.4)), ((60, 0.84), (100, 0.5)), ((65, 0.86), (110, 0.55)),
    ((70, 0.88), (120, 0.6)), ((75, 0.9), (130, 0.65)), ((80, 0.95), (140, 0.7)),
    ((90, 1.0), (160, 0.8)),
)
_FULL_GLOW = len(_GLOW_STEPS) - 1


def _glow_rule(step, shadows):
    """One glow step's rule; -gtk-icon-shadow follows the icon's shape."""
    shadow = ", ".join(f"0 0 {blur}px rgba(66, 133, 244, {alpha})" for blur, alpha in shadows)
    return f".tt-splash image.glow-{step} {{ -gtk-icon-shadow: {shadow}; }}"


# A transparent window around a rounded dark box. Every rule is scoped to
# .tt-splash: the provider goes on the whole screen (see _load_css).
_CSS = ("""
    window.tt-splash {
        background-color: transparent;
    }
    .tt-splash .splash-box {
        background-color: #262626;
        border-radius: 16px;
        padding: 80px;
    }
    .tt-splash image {
        padding: 0px;
        margin: 0px;
    }
""" + "\n".join(_glow_rule(step, shadows) for step, shadows in enumerate(_GLOW_STEPS))
).encode()


def fade_in(window, then=None, opacity=0.0):
    """Raise *window*'s opacity to full over about half a second (20 steps
    of 25 ms), then call *then*. A window closed meanwhile (the welcome
    screen can be, in its first half second) ends the ramp."""
    if window.get_window() is None:
        return
    opacity += 0.05
    window.set_opacity(opacity)
    if opacity < 1.0:
        GLib.timeout_add(25, lambda: fade_in(window, then, opacity))
    elif then is not None:
        then()


def _icon_candidates():
    """Where the icon may be, best first; an SVG renders more cleanly at
    256 px than a PNG does. All absolute: which icon shows mustn't depend on
    the folder TuxTalk was started from."""
    paths = []
    appdir = os.environ.get('APPDIR')       # a running AppImage, with a PNG at its root
    if appdir:
        paths += [os.path.join(appdir, f"{_APP_ID}.png"),
                  os.path.join(appdir, f"{_APP_ID}.svg"),
                  os.path.join(appdir, "usr", _SCALABLE)]
    # The project's own icons/, beside src/, when running from the source.
    project_icons = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "icons")
    paths += [
        os.path.join(project_icons, "OFFICIAL_ICON_DO_NOT_CHANGE.svg"),
        f"/app/{_SCALABLE}",
        # System install locations (.deb / .rpm): the hicolor SVG, then the
        # pixmaps PNG. A packaged install has no $APPDIR and no project
        # icons/ folder, so without these the splash shows plain text.
        f"/usr/{_SCALABLE}",
        f"/usr/share/icons/hicolor/256x256/apps/{_APP_ID}.png",
        f"/usr/share/pixmaps/{_APP_ID}.png",
        os.path.expanduser(f"~/.local/{_SCALABLE}"),
    ]
    return paths


def _find_icon():
    """The first icon file there is, or None."""
    for path in _icon_candidates():
        if os.path.exists(path):
            logger.debug(f"Found splash icon at: {path}")
            return path
    logger.warning("No icon path found for splash screen")
    return None


def _icon_image(path):
    """The icon at *path* at 256 px, or None when there is none or it won't load."""
    if not path:
        return None
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(path, 256, 256)
    except Exception as e:
        logger.warning(f"Could not load splash icon from {path}: {e}")
        return None
    logger.info(f"Splash screen loaded icon from: {path}")
    return Gtk.Image.new_from_pixbuf(pixbuf)


class SplashScreen:
    """TuxTalk's icon, shown for about three seconds before the welcome
    screen: it fades in, glows to full and fades out."""

    def __init__(self):
        """The window, its stylesheet and the icon (or the name, when no icon loads)."""
        self.window = self._make_window()
        self.css_provider = self._load_css()
        self.glow_level = 0
        self._stop_glow = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.get_style_context().add_class('splash-box')
        self.image_widget = _icon_image(_find_icon())
        if self.image_widget is None:
            label = Gtk.Label()
            label.set_markup('<span size="xx-large" weight="bold">TuxTalk</span>')
            box.pack_start(label, False, False, 0)
        else:
            box.pack_start(self.image_widget, False, False, 0)
        self.window.add(box)

    @staticmethod
    def _make_window():
        """An undecorated, transparent window that stays on top."""
        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        window.set_title("TuxTalk")
        window.set_default_size(400, 400)
        window.set_position(Gtk.WindowPosition.CENTER)
        window.set_decorated(False)
        window.set_keep_above(True)
        window.set_type_hint(Gdk.WindowTypeHint.SPLASHSCREEN)
        window.get_style_context().add_class('tt-splash')
        visual = window.get_screen().get_rgba_visual()
        if visual:
            window.set_visual(visual)
        window.set_app_paintable(True)
        return window

    def _load_css(self):
        """The splash's stylesheet, added to the screen until the splash goes."""
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        # Screen-wide, because a provider on the window's own style context never
        # reaches the child image. Every rule is scoped to .tt-splash, and the
        # provider is removed with the splash, so other windows in the tray
        # process keep their normal background.
        self._screen = self.window.get_screen() or Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(
            self._screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        return provider

    def _animate_glow(self):
        """Grow the glow one step; it grows once to full and stays there."""
        if not self.image_widget or self._stop_glow:
            return False
        style_context = self.image_widget.get_style_context()
        style_context.remove_class(f'glow-{self.glow_level}')
        if self.glow_level < _FULL_GLOW:
            self.glow_level += 1
            style_context.add_class(f'glow-{self.glow_level}')
            return True
        style_context.add_class(f'glow-{self.glow_level}')
        return False

    def show_and_fade(self, callback):
        """Show the splash, then fade it out and call *callback* (no arguments)
        once it has gone. A splash closed early (Alt+F4 under some window
        managers) calls it too: the welcome screen waits on it."""
        self.window.connect("destroy", self._on_destroy, callback)
        self.window.set_opacity(0.0)
        self.window.show_all()
        GLib.timeout_add(100, self._animate_glow)
        # Held for 2.5 s at full while the glow grows, then faded out.
        fade_in(self.window,
                lambda: GLib.timeout_add(2500, lambda: self._fade_out(1.0)))

    def _fade_out(self, opacity):
        """Lower the opacity a step at the fade-in's pace; at nothing, close
        the splash."""
        if self._stop_glow:         # closed already
            return
        opacity -= 0.05
        self.window.set_opacity(opacity)
        if opacity > 0.0:
            GLib.timeout_add(25, lambda: self._fade_out(opacity))
            return
        self.window.destroy()

    def _on_destroy(self, _window, callback):
        """The splash has gone, faded out or closed: its stylesheet goes too,
        and *callback* runs once its window is out of the way."""
        self._stop_glow = True      # the glow's timer stops at its next step
        Gtk.StyleContext.remove_provider_for_screen(self._screen, self.css_provider)
        GLib.idle_add(callback)
