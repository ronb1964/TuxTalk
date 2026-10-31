"""Report a Problem, opened from Help → Problems. It shows the whole report
(problem_report.py) before anything goes anywhere; the person copies it,
saves it, or opens a GitHub issue and pastes it there. TuxTalk sends nothing
itself.
"""
import logging
import os
import threading
import time
import urllib.parse

from gi.repository import Gdk, GLib, Gtk

logger = logging.getLogger(__name__)

ISSUE_URL = "https://github.com/ronb1964/TuxTalk/issues/new"

# The bug form's desktop and display dropdowns (.github/ISSUE_TEMPLATE),
# by desktop_detect's and XDG_SESSION_TYPE's names.
_FORM_DESKTOPS = {"gnome": "GNOME", "kde": "KDE Plasma", "cosmic": "COSMIC", "xfce": "XFCE",
                  "cinnamon": "Cinnamon", "mate": "MATE"}
_FORM_DISPLAYS = {"wayland": "Wayland", "x11": "X11"}


def issue_url():
    """The bug form with what TuxTalk knows filled in; the report itself is
    too long for an address, so it's pasted into Log Output."""
    from . import __version__, desktop_detect
    from .problem_report import fact, os_name
    fields = {"template": "bug_report.yml", "version": __version__,
              "distro": fact(os_name)}
    desktop = _FORM_DESKTOPS.get(desktop_detect.get_desktop_environment())
    if desktop:
        fields["desktop"] = desktop
    display = _FORM_DISPLAYS.get((os.environ.get("XDG_SESSION_TYPE") or "").lower())
    if display:
        fields["display"] = display
    return f"{ISSUE_URL}?{urllib.parse.urlencode(fields)}"


class ReportWindow:
    """Help's Report a problem window: the whole report shown first, then
    copied, saved to a file or put in a new GitHub issue by the user.
    Nothing leaves the computer by itself."""

    def __init__(self):
        """Open the window and gather the report on a thread of its own:
        asking the graphics card and the hotkey helper takes a moment, and
        the window shows meanwhile."""
        from .problem_report import fact, layout_text
        from .ui_style import WARNING_AMBER, close_on_escape
        self.window = Gtk.Window(title="Report a Problem")
        close_on_escape(self.window)
        # Not transient for Help: on KWin 6.7.5 closing this window as one
        # sent the compositor allocating gigabytes a second until the desktop
        # froze (a window of its own did not).
        self.window.set_default_size(760, 560)
        self.window.set_position(Gtk.WindowPosition.CENTER)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(16)
        self.window.add(box)

        intro = Gtk.Label(xalign=0)
        intro.set_line_wrap(True)
        intro.set_text("This is what helps find a problem with TuxTalk: your system, your "
                       "settings and the end of TuxTalk's log. Nothing leaves your computer "
                       "unless you copy it, save it or send it yourself, so read it first.")
        box.pack_start(intro, False, False, 0)

        # Shown once the report is in, if its log had dictated words.
        self.warning = Gtk.Label(xalign=0, no_show_all=True)
        self.warning.set_line_wrap(True)
        self.warning.set_markup(
            f"<span foreground='{WARNING_AMBER}'><b>TuxTalk's log had some of what you "
            "dictated in it</b>, from when logging it was on (Preferences → Advanced → "
            "Fine-tuning). It's left out of this report.</span>")
        box.pack_start(self.warning, False, False, 0)

        self.view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.get_buffer().set_text("Gathering…")
        self.view.get_accessible().set_name("Report")
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.add(self.view)
        box.pack_start(scrolled, True, True, 0)

        self.status = Gtk.Label(xalign=0)
        self.status.set_line_wrap(True)
        box.pack_start(self.status, False, False, 0)

        buttons = Gtk.Box(spacing=8)
        self.issue_button = Gtk.Button(label="Open a GitHub Issue")
        self.copy_button = Gtk.Button(label="Copy")
        self.save_button = Gtk.Button(label="Save to File…")
        close_button = Gtk.Button(label="Close")
        self.issue_button.connect("clicked", self._on_issue)
        self.copy_button.connect("clicked", self._on_copy)
        self.save_button.connect("clicked", self._on_save)
        close_button.connect("clicked", lambda _b: self.window.destroy())
        for button in (self.issue_button, self.copy_button, self.save_button):
            button.set_sensitive(False)
            buttons.pack_start(button, False, False, 0)
        buttons.pack_end(close_button, False, False, 0)
        box.pack_start(buttons, False, False, 0)

        self.closed = False
        self.window.connect("destroy", lambda _w: setattr(self, "closed", True))
        self.window.show_all()
        self.window.present()
        layout = fact(layout_text)
        threading.Thread(target=self._gather, args=(layout,), name="problem-report",
                         daemon=True).start()

    def _gather(self, layout):
        from . import problem_report
        report = problem_report.build(layout)
        GLib.idle_add(self._show_report, report)

    def _show_report(self, report):
        if self.closed:
            return False
        self.view.get_buffer().set_text(report.text)
        self.warning.set_visible(report.words_left_out)
        for button in (self.issue_button, self.copy_button, self.save_button):
            button.set_sensitive(True)
        return False

    def _text(self):
        buf = self.view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def _copy(self):
        """Copy the report, in a way that outlives this window (clipboard.py)."""
        from .clipboard import copy_text
        return copy_text(self._text())

    def _on_copy(self, _button):
        if not self._copy():
            self.status.set_text("Couldn't copy it. Save it to a file instead.")
            return
        self.status.set_text("Copied. Paste it into a forum post, an email or a GitHub issue.")

    def _on_issue(self, _button):
        if not self._copy():
            self.status.set_text("Couldn't copy the report. Save it to a file, then attach "
                                 "it to the issue.")
            return
        url = issue_url()
        try:
            Gtk.show_uri_on_window(self.window, url, Gdk.CURRENT_TIME)
        except GLib.Error as e:
            logger.warning(f"Could not open the bug form ({e.message}); URL: {url}")
            self.status.set_text(f"The report is copied, but the browser didn't open. Go to {url}")
            return
        self.status.set_text("The report is copied. On the page that opened, say what went "
                             "wrong and paste the report into Log Output. GitHub asks you to "
                             "sign in first.")

    def _on_save(self, _button):
        """Save the report to a text file the user picks."""
        chooser = Gtk.FileChooserNative.new("Save the report", self.window,
                                            Gtk.FileChooserAction.SAVE, None, None)
        chooser.set_current_name(time.strftime("tuxtalk-report-%Y%m%d-%H%M.txt"))
        chooser.set_do_overwrite_confirmation(True)
        target = chooser.get_filename() if chooser.run() == Gtk.ResponseType.ACCEPT else None
        chooser.destroy()
        if not target:
            return
        from .config import write_text_atomic
        try:
            # Private and atomic: the log can name the computer's people and
            # files, and a failed save leaves an older file as it was.
            write_text_atomic(target, self._text() + "\n", keep_backup=False)
        except OSError as e:
            self.status.set_text(f"Couldn't save it: {e}")
            return
        self.status.set_text(f"Saved {os.path.basename(target)}.")
