"""The "Transcribe a File" window (Tray → Transcribe a File...).

Pick or drop an audio or video file and its words come up in the window, to
copy, or to save as text or as subtitles (.srt). The work is done by
file_transcribe.py; this is only the window around it.

It runs as its own process, like Preferences, with its own copy of the
speech model, so dictation keeps working while a long file is transcribed.
The model stays loaded while the window is open, so a second file starts at
once, and goes away with the window. One window at a time: opening it again
brings the open one to the front.
"""
import os
import signal
import sys
import threading
import time

# Before any engine loads, as in the dictation service: makes TuxTalk's CUDA
# download findable for a Whisper model on CUDA.
from .torch_init import init_cuda_for_pytorch
init_cuda_for_pytorch()

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import file_transcribe as ft  # noqa: E402
from .logger import setup_logger  # noqa: E402

logger = setup_logger(__name__)

TITLE = "Transcribe a File"
FRONT = signal.SIGUSR1


def pidfile_path():
    base = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return os.path.join(base, "tuxtalk-transcribe.pid")


def _running_pid():
    """The pid of an open Transcribe a File window, or None."""
    try:
        with open(pidfile_path()) as f:
            pid = int(f.read().strip())
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            if b"tuxtalk.transcribe_window" in f.read() and pid != os.getpid():
                return pid
    except (OSError, ValueError):
        pass
    return None


def _when(stamp):
    """"Oct 15 at 7:40 PM" for a time.time() stamp; "earlier" without one."""
    if not stamp:
        return "earlier"
    return time.strftime("%b %-d at %-I:%M %p", time.localtime(stamp))


def _duration(seconds):
    """3:20, or 1:02:05."""
    return ft.clock(seconds)


class TranscribeWindow:
    def __init__(self):
        self.engine = None            # loaded on the first file, kept for the next
        self.engine_key = None        # the settings it was loaded with (engine_settings)
        self.pieces = []
        self.path = None
        self.busy = False
        # The text was saved to a file, so closing or a new file loses
        # nothing. A copy doesn't count: the next copy replaces it.
        self.kept = False
        self.copied = False
        # The transcript is kept under Earlier transcripts (file_transcribe.
        # remember), so another file or closing the window loses nothing.
        self.remembered = False
        self._cancel = threading.Event()

        self.window = Gtk.Window(title=TITLE)
        self.window.set_default_size(680, 560)
        self.window.connect("delete-event", self._on_close)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(16)
        self.window.add(box)

        top = Gtk.Box(spacing=12)
        intro = Gtk.Label(xalign=0)
        intro.set_line_wrap(True)
        intro.set_markup("Turn a recording or a video into text. Choose a file, or drop one "
                         "here. <small>It stays on this computer.</small>")
        top.pack_start(intro, True, True, 0)
        self.choose_btn = Gtk.Button(label="Choose a File…")
        self.choose_btn.connect("clicked", self._on_choose)
        top.pack_end(self.choose_btn, False, False, 0)
        self.earlier_btn = Gtk.MenuButton()
        self.earlier_btn.set_label("Earlier Transcripts")
        self.earlier_btn.set_tooltip_text(
            f"Your last {ft.MAX_RECENT} transcripts, to open again and save another way")
        self.earlier_menu = Gtk.Menu()
        self.earlier_btn.set_popup(self.earlier_menu)
        top.pack_end(self.earlier_btn, False, False, 0)
        box.pack_start(top, False, False, 0)

        self.file_label = Gtk.Label(xalign=0)
        self.file_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        box.pack_start(self.file_label, False, False, 0)

        progress_row = Gtk.Box(spacing=10)
        self.progress = Gtk.ProgressBar()
        self.progress.set_valign(Gtk.Align.CENTER)
        progress_row.pack_start(self.progress, True, True, 0)
        self.cancel_btn = Gtk.Button(label="Stop")
        self.cancel_btn.connect("clicked", lambda _b: self._cancel.set())
        progress_row.pack_end(self.cancel_btn, False, False, 0)
        box.pack_start(progress_row, False, False, 0)
        self.status = Gtk.Label(xalign=0)
        self.status.set_line_wrap(True)
        box.pack_start(self.status, False, False, 0)

        # Read-only: the subtitles are built from what was heard, so an edit
        # here wouldn't reach them. Copy or save it and edit it anywhere.
        self.view = Gtk.TextView()
        self.view.set_editable(False)
        self.view.set_cursor_visible(False)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        for side in ("left", "right", "top", "bottom"):
            getattr(self.view, f"set_{side}_margin")(8)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.add(self.view)
        # A read-only text view turns down every drop, and it's most of the
        # window, so it takes files itself (_on_view_drag_motion).
        self.view.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        self.view.drag_dest_add_uri_targets()
        self.view.connect("drag-motion", self._on_view_drag_motion)
        self.view.connect("drag-leave", lambda w, *_a: w.drag_unhighlight())
        self.view.connect("drag-drop", self._on_view_drag_drop)
        self.view.connect("drag-data-received", self._on_view_drop)
        # Where to drop, while there's nothing to show yet.
        self.drop_hint = Gtk.Label(justify=Gtk.Justification.CENTER)
        self.drop_hint.set_markup(
            "<span size='large'>Drop an audio or video file here</span>\n"
            "<small>or use Choose a File…</small>")
        self.drop_hint.set_opacity(0.6)
        self.drop_hint.set_halign(Gtk.Align.CENTER)
        self.drop_hint.set_valign(Gtk.Align.CENTER)
        overlay = Gtk.Overlay()
        overlay.add(scrolled)
        overlay.add_overlay(self.drop_hint)
        overlay.set_overlay_pass_through(self.drop_hint, True)
        box.pack_start(overlay, True, True, 0)

        bottom = Gtk.Box(spacing=8)
        self.times_check = Gtk.CheckButton(label="Show times")
        self.times_check.set_tooltip_text("Start each paragraph with where it is in the file, like [1:05]")
        self.times_check.connect("toggled", lambda _c: self._show_text())
        bottom.pack_start(self.times_check, False, False, 0)
        self.clean_check = Gtk.CheckButton(label="Remove ums and repeats")
        self.clean_check.set_tooltip_text(
            "Takes out um, uh and words said twice, like \u201cthe the\u201d.\n"
            "Untick it to see it exactly as it was heard. Copy and the saves use what's shown.")
        self.clean_check.connect("toggled", lambda _c: self._show_text())
        bottom.pack_start(self.clean_check, False, False, 0)
        self.srt_btn = Gtk.Button(label="Save as Subtitles…")
        self.srt_btn.set_tooltip_text(
            "Captions for a video: the words show up on screen while it plays.\n"
            "Saved as an .srt file next to the video; players like VLC pick it up.")
        self.srt_btn.connect("clicked", lambda _b: self._save("srt"))
        bottom.pack_end(self.srt_btn, False, False, 0)
        self.txt_btn = Gtk.Button(label="Save as Text…")
        self.txt_btn.connect("clicked", lambda _b: self._save("txt"))
        bottom.pack_end(self.txt_btn, False, False, 0)
        self.copy_btn = Gtk.Button(label="Copy All")
        self.copy_btn.connect("clicked", self._on_copy)
        bottom.pack_end(self.copy_btn, False, False, 0)
        box.pack_start(bottom, False, False, 0)

        # Dropping a file from the file manager starts it too.
        self.window.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        self.window.drag_dest_add_uri_targets()
        self.window.connect("drag-data-received", self._on_drop)

        self.window.show_all()
        self._set_busy(False)
        self._refresh_earlier()
        self.progress.hide()
        self.cancel_btn.hide()

    # --- states -----------------------------------------------------------------------

    def _set_busy(self, busy):
        self.busy = busy
        self.choose_btn.set_sensitive(not busy)
        self.earlier_btn.set_sensitive(not busy and bool(self.earlier_menu.get_children()))
        have = bool(self.pieces) and not busy
        for widget in (self.copy_btn, self.txt_btn, self.srt_btn, self.times_check,
                       self.clean_check):
            widget.set_sensitive(have)
        self.cancel_btn.set_visible(busy)
        self.progress.set_visible(busy)
        self.drop_hint.set_visible(not self.pieces and not busy)

    def _shown(self):
        """The pieces as they're shown: cleaned up when that's ticked."""
        return ft.cleaned(self.pieces) if self.clean_check.get_active() else self.pieces

    def _show_text(self):
        self.view.get_buffer().set_text(ft.to_text(self._shown(), times=self.times_check.get_active()))

    def present(self):
        self.window.present()
        return True

    # --- choosing a file -----------------------------------------------------------------

    def _on_choose(self, _button):
        if not self._may_discard("new"):
            return
        chooser = Gtk.FileChooserNative.new("Choose a recording or video", self.window,
                                            Gtk.FileChooserAction.OPEN, None, None)
        media = Gtk.FileFilter()
        media.set_name("Audio and video")
        for ext in ft.AUDIO_EXTENSIONS + ft.VIDEO_EXTENSIONS:
            media.add_pattern(f"*.{ext}")
            media.add_pattern(f"*.{ext.upper()}")
        chooser.add_filter(media)
        anything = Gtk.FileFilter()
        anything.set_name("All files")
        anything.add_pattern("*")
        chooser.add_filter(anything)
        if self.path:
            chooser.set_current_folder(os.path.dirname(self.path))
        path = chooser.get_filename() if chooser.run() == Gtk.ResponseType.ACCEPT else None
        chooser.destroy()
        if path:
            self.start(path)

    def _on_view_drag_motion(self, view, context, _x, _y, time_):
        """Take a file anywhere over the text, as over the rest of the
        window; not while a file is running."""
        if self.busy:
            Gdk.drag_status(context, 0, time_)
            view.drag_unhighlight()
        else:
            Gdk.drag_status(context, Gdk.DragAction.COPY, time_)
            view.drag_highlight()
        return True

    def _on_view_drag_drop(self, _view, _context, _x, _y, _time):
        """GTK already asks for the file's address (Gtk.DestDefaults.DROP);
        handled here so the text view doesn't ask a second time."""
        return True

    def _on_view_drop(self, view, context, x, y, data, info, time_):
        view.drag_unhighlight()
        # The text view's own handler would only turn the file down.
        view.stop_emission_by_name("drag-data-received")
        self._on_drop(view, context, x, y, data, info, time_)

    def _on_drop(self, _widget, _context, _x, _y, data, _info, _time):
        uris = data.get_uris() or []
        if uris and not self.busy and self._may_discard("new"):
            path, _host = GLib.filename_from_uri(uris[0])
            self.start(path)

    # --- the work, on its own thread ---------------------------------------------------------

    def start(self, path):
        if self.busy:
            return
        self.path = path
        self.pieces = []
        self.view.get_buffer().set_text("")
        self.file_label.set_markup(f"<b>{GLib.markup_escape_text(os.path.basename(path))}</b>")
        self._cancel.clear()
        self._set_busy(True)
        self.status.set_text("Reading the file…")
        self.progress.set_fraction(0.0)
        threading.Thread(target=self._work, args=(path,), daemon=True).start()

    def _work(self, path):
        started = time.time()
        try:
            audio = ft.decode(path)
            total = len(audio) / ft.SAMPLE_RATE
            if self._cancel.is_set():
                GLib.idle_add(self._on_done, [], total, time.time() - started, True)
                return
            # Settings are read for every file: a model or language changed in
            # Preferences since the last one loads afresh.
            from .config import load_config
            cfg = load_config()
            if self.engine is None or self.engine_key != ft.engine_settings(cfg):
                if self.engine is not None:
                    self.engine.close()
                    self.engine = None
                GLib.idle_add(self.status.set_text, "Loading the speech model…")
                self.engine = ft.load_engine(cfg)
                self.engine_key = ft.engine_settings(cfg)
            self.engine.reload_words()
            if self._cancel.is_set():
                GLib.idle_add(self._on_done, [], total, time.time() - started, True)
                return
            describe = self.engine.describe
            GLib.idle_add(self.status.set_text, f"Transcribing {_duration(total)} with {describe}…")

            def progress(done, whole):
                GLib.idle_add(self._on_progress, done, whole, describe)

            pieces = ft.transcribe(audio, self.engine, progress, self._cancel.is_set)
            GLib.idle_add(self._on_done, pieces, total, time.time() - started, self._cancel.is_set())
        except (ft.NotDownloaded, ft.NoSound) as e:
            GLib.idle_add(self._on_failed, str(e) + (
                " Choose a model in Preferences → General, or dictate once, and it downloads."
                if isinstance(e, ft.NotDownloaded) else ""))
        except Exception as e:
            logger.exception("File transcription failed")
            GLib.idle_add(self._on_failed, f"Something went wrong: {e}")

    def _on_progress(self, done, whole, describe):
        self.progress.set_fraction(min(1.0, done / whole) if whole else 1.0)
        self.status.set_text(f"Transcribing with {describe}: {_duration(done)} of {_duration(whole)}")
        return False

    def _on_done(self, pieces, total, seconds, stopped):
        self.pieces = pieces
        self.kept = self.copied = False
        self.remembered = bool(pieces) and ft.remember(self.path, pieces, total) is not None
        self._refresh_earlier()
        self._set_busy(False)
        self._show_text()
        logger.info(f"Transcribed a file: {total:.0f} s of sound in {seconds:.1f} s"
                    f"{' (stopped)' if stopped else ''}, {len(pieces)} pieces with speech")
        if stopped and not pieces:
            self.status.set_text("Stopped.")
        elif stopped:
            self.status.set_text(f"Stopped at about {_duration(pieces[-1].end)}. "
                                 "What was done so far is below.")
        elif not pieces:
            self.status.set_text("Nobody seems to talk in this file.")
        else:
            self.status.set_text(f"Done: {_duration(total)} of sound in {_duration(seconds)}."
                                 + (" It's kept under Earlier Transcripts." if self.remembered
                                    else ""))
        return False

    def recall(self):
        """On opening: show the newest transcript kept, with every button."""
        self._refresh_earlier()
        entries = ft.recent()
        if entries:
            self.show_kept(entries[0])

    def show_kept(self, entry):
        """Show a kept transcript as it was, ready to save another way."""
        if self.busy:
            return
        self.path, self.pieces = entry.path, entry.pieces
        self.kept = self.copied = False
        self.remembered = True
        self.file_label.set_markup(f"<b>{GLib.markup_escape_text(entry.name)}</b>")
        self.status.set_text(f"Transcribed {_when(entry.when)}. It's kept under Earlier Transcripts.")
        self._set_busy(False)
        self._show_text()

    def _open_kept(self, entry):
        """Earlier Transcripts: *entry* in place of the text shown, asking
        first if that one would be lost (it couldn't be kept in the list)."""
        if self._may_discard("new"):
            self.show_kept(entry)

    def _refresh_earlier(self):
        """The Earlier Transcripts menu, as the list is now."""
        menu = self.earlier_menu
        for child in menu.get_children():
            menu.remove(child)
        entries = ft.recent()
        for entry in entries:
            item = Gtk.MenuItem(label=f"{entry.name}  ·  {_when(entry.when)}")
            item.connect("activate", lambda _i, e=entry: self._open_kept(e))
            menu.append(item)
        if entries:
            menu.append(Gtk.SeparatorMenuItem())
            clear = Gtk.MenuItem(label="Clear This List…")
            clear.connect("activate", lambda _i: self._on_clear_earlier())
            menu.append(clear)
        menu.show_all()
        self.earlier_btn.set_sensitive(not self.busy and bool(entries))

    def _on_clear_earlier(self):
        if self._ask("Clear earlier transcripts?",
                     f"The last {ft.MAX_RECENT} transcripts kept here are deleted. "
                     "Files you saved aren't touched.",
                     [("Cancel", Gtk.ResponseType.CANCEL),
                      ("Clear", Gtk.ResponseType.ACCEPT)]) != Gtk.ResponseType.ACCEPT:
            return
        if ft.forget_all():
            self.remembered = False     # what's shown is now only here
            self.status.set_text("Earlier transcripts cleared.")
        self._refresh_earlier()

    def _on_failed(self, message):
        self._set_busy(False)
        self.status.set_text(message)
        return False

    # --- copy and save --------------------------------------------------------------------

    def _on_copy(self, _button):
        buf = self.view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
        self.copied = True
        self.status.set_text("Copied. Paste it wherever you like.")

    def _save(self, kind):
        shown = self._shown()
        content = (ft.to_srt(shown) if kind == "srt"
                   else ft.to_text(shown, times=self.times_check.get_active()) + "\n")
        chooser = Gtk.FileChooserNative.new("Save as subtitles" if kind == "srt" else "Save as text",
                                            self.window, Gtk.FileChooserAction.SAVE, None, None)
        stem = os.path.splitext(os.path.basename(self.path or "transcript"))[0]
        chooser.set_current_name(f"{stem}.{kind}")
        if self.path:
            chooser.set_current_folder(os.path.dirname(self.path))
        chooser.set_do_overwrite_confirmation(True)
        target = chooser.get_filename() if chooser.run() == Gtk.ResponseType.ACCEPT else None
        chooser.destroy()
        if not target:
            return
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            self.kept = True
            self.status.set_text(f"Saved {os.path.basename(target)}.")
        except OSError as e:
            self.status.set_text(f"Couldn't save it: {e}")

    # --- closing -------------------------------------------------------------------------------

    def _ask(self, title, text, buttons):
        """A question over the window; the response of the button clicked.
        *buttons*: (label, response) pairs, the last one the default."""
        from .ui_style import message_dialog
        dialog = message_dialog(transient_for=self.window, modal=True,
                                message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE, text=title)
        dialog.format_secondary_text(text)
        for label, response in buttons:
            dialog.add_button(label, response)
        dialog.set_default_response(buttons[-1][1])
        answer = dialog.run()
        dialog.destroy()
        return answer

    def _may_discard(self, why):
        """Whether the text shown may go, asking first when it would be lost:
        *why* is "close" (the window is closing) or "new" (another file)."""
        if self.busy:
            if why != "close":
                return False
            return self._ask(
                "Stop transcribing?",
                "The file isn't finished yet. Closing now loses what's been done so far.",
                [("Keep Going", Gtk.ResponseType.CANCEL),
                 ("Stop and Close", Gtk.ResponseType.ACCEPT)]) == Gtk.ResponseType.ACCEPT
        # Closing asks even when the text is kept (Ron: "a little bit of
        # insurance"), and says it's kept, so it isn't a scare. Another file
        # loses nothing then: the text stays under Earlier Transcripts.
        if not self.pieces or self.kept or (why == "new" and self.remembered):
            return True
        if self.remembered:
            loses = "It's kept under Earlier Transcripts, so you can open it again."
        elif why == "close":
            loses = "Closing the window loses it."
        else:
            loses = "Opening another file replaces it."
        where = ("You copied it, but it isn't saved to a file." if self.copied
                 else "It isn't saved or copied anywhere yet.")
        answer = self._ask(
            "Save the text first?",
            f"{where} {loses}",
            [("Cancel", Gtk.ResponseType.CANCEL),
             ("Don't Save", Gtk.ResponseType.REJECT),
             ("Save as Text…", Gtk.ResponseType.ACCEPT)])
        if answer == Gtk.ResponseType.ACCEPT:
            self._save("txt")
            return self.kept            # not if the save was cancelled or failed
        return answer == Gtk.ResponseType.REJECT

    def _on_close(self, *_args):
        """Stop and quit at once, once nothing unsaved would be lost. The
        engine is closed in main() after the window is gone: closing it here
        could wait for the piece being transcribed, with the window frozen
        meanwhile."""
        if not self._may_discard("close"):
            return True                 # the window stays open
        self._cancel.set()
        self.window.hide()
        Gtk.main_quit()
        return False

    def close_engine(self):
        if self.engine is not None:
            self.engine.close()


def main(argv=None):
    """Open the window, or bring the open one to the front. A file given on
    the command line starts at once."""
    argv = sys.argv[1:] if argv is None else argv
    other = _running_pid()
    if other:
        try:
            os.kill(other, FRONT)
            return 0
        except OSError:
            pass
    try:
        from . import desktop_identity, ui_style
        desktop_identity.apply()        # TuxTalk's icon on the window
        ui_style.prepare_windows()   # emoji that show, buttons clear of panels and docks
    except Exception as e:
        logger.warning(f"Couldn't set up the window's icon, emoji or size: {e}")
    window = TranscribeWindow()
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, FRONT, window.present)
    try:
        with open(pidfile_path(), "w") as f:
            f.write(str(os.getpid()))
    except OSError as e:
        logger.debug(f"Transcribe a File: no pidfile ({e})")
    if argv and os.path.isfile(argv[0]):
        window.start(os.path.abspath(argv[0]))
    else:
        window.recall()
    try:
        Gtk.main()
    finally:
        try:
            if _read_pid() == os.getpid():
                os.remove(pidfile_path())
        except OSError:
            pass
        window.close_engine()
    return 0


def _read_pid():
    try:
        with open(pidfile_path()) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    sys.exit(main())
