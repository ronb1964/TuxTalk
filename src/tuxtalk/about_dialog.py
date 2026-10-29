"""The About TuxTalk window: the icon, name and version, the credits the
models' licences ask for, links to the website, the code and the changelog,
and this version's release notes, fetched from GitHub while it's open."""
import os
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import notes_text  # noqa: E402
from .logger import setup_logger  # noqa: E402

logger = setup_logger(__name__)

NOTES_NOT_AVAILABLE = "Release notes not available.\n\nVisit GitHub for the full changelog."


def about_icon_source(theme_has_icon):
    """Where the About dialog's icon comes from: ("file", path), ("icon",
    theme name) or None. *theme_has_icon* is the icon theme's has_icon."""
    from .autostart import get_icon_path
    from .config import APP_ID
    path = get_icon_path()
    if os.path.isfile(path):
        return ("file", path)
    appdir = os.environ.get("APPDIR")
    if appdir:
        bundled = os.path.join(appdir, f"{APP_ID}.svg")
        if os.path.isfile(bundled):
            return ("file", bundled)
    # deb/rpm install the icon under the app ID; the AppImage's first run
    # installs it as "tuxtalk".
    for name in (APP_ID, "tuxtalk"):
        if theme_has_icon(name):
            return ("icon", name)
    return None


def _about_icon():
    """The TuxTalk icon at 64 px for the About dialog, or None."""
    theme = Gtk.IconTheme.get_default()
    source = about_icon_source(theme.has_icon if theme else (lambda name: False))
    if source is None:
        return None
    kind, value = source
    if kind == "file":
        from gi.repository import GdkPixbuf
        return Gtk.Image.new_from_pixbuf(
            GdkPixbuf.Pixbuf.new_from_file_at_scale(value, 64, 64, True))
    image = Gtk.Image.new_from_icon_name(value, Gtk.IconSize.DIALOG)
    image.set_pixel_size(64)
    return image


def show_about_dialog():
    """Open About TuxTalk, and fetch this version's release notes into it."""
    dialog = Gtk.Dialog(title="About TuxTalk", flags=Gtk.DialogFlags.MODAL)
    dialog.set_default_size(500, 450)
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)

    content = dialog.get_content_area()
    content.set_spacing(10)
    content.set_margin_start(20)
    content.set_margin_end(20)
    content.set_margin_top(20)
    content.set_margin_bottom(10)
    _header(content)
    _credits(content)
    notes = _notes_view(content)
    _links(content)
    dialog.add_button("Close", Gtk.ResponseType.CLOSE)

    # Shown with a response callback and present() rather than run(): a
    # nested run() loop started from a tray menu callback can fail to map
    # the window on Wayland.
    dialog.connect("response", lambda d, _r: d.destroy())
    dialog.show_all()
    dialog.present()
    _fetch_notes(dialog, notes)


def _header(content):
    """The icon, name, version, former name and what TuxTalk does."""
    from . import __version__
    try:
        icon = _about_icon()
        if icon:
            content.pack_start(icon, False, False, 0)
    except Exception as e:
        logger.info(f"Could not load icon for About dialog: {e}")

    title_label = Gtk.Label()
    title_label.set_markup('<span size="x-large"><b>TuxTalk</b></span>')
    content.pack_start(title_label, False, False, 0)
    content.pack_start(Gtk.Label(label=f"Version {__version__}"), False, False, 0)

    # Renamed in 0.15.0 (TALKTYPE is another company's trademark); this
    # line lets people who knew the old name know they have the right app.
    formerly_label = Gtk.Label()
    formerly_label.set_markup('<span size="small"><i>Formerly TalkType</i></span>')
    formerly_label.set_opacity(0.75)
    content.pack_start(formerly_label, False, False, 0)

    desc_label = Gtk.Label(
        label="Voice dictation for Linux: hold a key, talk, and your words are typed for you.")
    desc_label.set_line_wrap(True)
    content.pack_start(desc_label, False, False, 5)


def _credits(content):
    """The copyright and licence, and the credits for what TuxTalk runs."""
    copyright_label = Gtk.Label()
    copyright_label.set_markup('<span size="small">© 2024-2026 Ron Brand • MIT License</span>')
    content.pack_start(copyright_label, False, False, 0)

    # CC-BY-4.0 requires crediting NVIDIA for the Parakeet model. The AI
    # cleanup engine and model are downloaded from their own projects, not
    # shipped, but credit them here too.
    from .parakeet_engine import PARAKEET_ATTRIBUTION
    credit_label = Gtk.Label()
    credit_label.set_markup(
        f'<span size="small">{PARAKEET_ATTRIBUTION}\n'
        'AI cleanup uses llama.cpp (MIT) and Qwen2.5 by Alibaba Cloud (Apache 2.0).</span>')
    credit_label.set_justify(Gtk.Justification.CENTER)
    credit_label.set_line_wrap(True)
    credit_label.set_opacity(0.7)
    content.pack_start(credit_label, False, False, 0)


def _notes_view(content):
    """"What's New in This Version" and the box its notes go in, saying
    they're loading for now; the box's NotesView."""
    whats_new_label = Gtk.Label()
    whats_new_label.set_markup('<b>What\'s New in This Version</b>')
    whats_new_label.set_xalign(0)
    whats_new_label.set_margin_top(15)
    content.pack_start(whats_new_label, False, False, 0)
    scroll, notes = notes_text.scrolled_notes("Loading release notes...")
    content.pack_start(scroll, True, True, 0)
    return notes


def _links(content):
    """The website, then the code on GitHub, then the full changelog."""
    from . import update_checker
    links_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
    links_box.set_halign(Gtk.Align.CENTER)
    links_box.set_margin_top(10)
    for uri, label in (("https://tuxtalk.app/", "Website"),
                       ("https://github.com/ronb1964/TuxTalk", "GitHub Repository"),
                       (update_checker.get_releases_url(), "View Full Changelog")):
        links_box.pack_start(Gtk.LinkButton.new_with_label(uri, label), False, False, 0)
    content.pack_start(links_box, False, False, 0)


def _fetch_notes(dialog, notes):
    """Fetch this version's release notes on a thread and show them in
    *notes*, this window's own box: not if the window was closed meanwhile,
    when the box is gone."""
    from . import __version__, update_checker
    closed = []
    dialog.connect("destroy", lambda _d: closed.append(True))

    def show(text):
        if not closed:
            notes.show(text)
        return False    # once

    def fetch():
        release = update_checker.fetch_release_by_tag(__version__)
        GLib.idle_add(show, release["body"] if release and release.get("body")
                      else NOTES_NOT_AVAILABLE)

    threading.Thread(target=fetch, daemon=True).start()
