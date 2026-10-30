"""
The clipboard, for TuxTalk's windows and for pasting dictations.

copy_text is a deliberate Copy (the tray's Recent Dictations, Fix a Word).
The rest is the dictation service's paste mode: dictations offered so that
clipboard managers can skip them, on Wayland (wl-copy --sensitive) and X11
(offer_dictation_x11), ClipboardKeeper putting the user's clipboard back
afterwards, and the primary selection voice editing reads.
"""
import os
import shutil
import subprocess
import time

from .logger import setup_logger

logger = setup_logger(__name__)


def copy_text(text):
    """Copy *text*; returns True on success. Never raises.

    wl-copy first: a click in a KDE/GNOME tray menu is handled by the
    desktop's menu, not a GTK window of ours, and Wayland only lets a
    focused window set the clipboard through GTK. wl-copy has its own way
    in. GTK's clipboard covers X11.
    """
    try:
        if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
            subprocess.run(["wl-copy"], input=text.encode("utf-8"), timeout=5, check=True)
        else:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk, Gdk
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(text, -1)
            clipboard.store()
        return True
    except Exception as e:
        logger.error(f"Could not copy to the clipboard: {e}")
        return False


# --- Paste mode and the user's clipboard (see issue #7) -------------------------------
# Paste mode puts each dictation on the clipboard and sends Ctrl+V, which
# replaces what the user had copied, and clipboard managers such as KDE's
# Klipper would save every dictation to disk. Dictations are therefore offered
# with wl-copy --sensitive, the x-kde-passwordManagerHint=secret marker KeePassXC
# uses, which Klipper and other clipboard managers skip. With the
# restore_clipboard setting on, ClipboardKeeper also puts the old contents back
# once the app has had time to read the paste. It's off by default because some
# people re-paste a dictation that landed in the wrong window.

import functools
import threading

RESTORE_DELAY_S = 0.5           # apps read the clipboard asynchronously after Ctrl+V
_MAX_SAVED_BYTES = 20 * 1024 * 1024
_TEXT_TYPES = ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING", "STRING", "TEXT")
_NOTHING = object()             # nothing saved (setting off, or the clipboard couldn't be read)
_EMPTY = object()               # the clipboard was empty before the paste
_UNREADABLE = object()          # the primary selection couldn't be read: left as it is
# KeePassXC and friends mark a copied password with this type; Klipper skips it.
_PASSWORD_HINT = "x-kde-passwordManagerHint"


def offer_dictation_x11(text, timeout=1.0):
    """Put a dictation on the X11 clipboard for pasting, from the thread that
    runs Gtk.main(); True on success.

    For the dictation service on X11, which has no wl-copy to lean on. GTK may
    only be touched from its own thread, and the service's typing runs on
    another one, so the offer is queued there and waited for. The service keeps
    the X11 clipboard selection afterwards and its GTK loop answers the
    target app's paste request, so without a running loop nothing could be
    pasted. If the loop doesn't get to it within *timeout*, this returns
    False and the offer is called off: nothing gets pasted, so the dictation
    mustn't land on the clipboard later.
    """
    from . import gtk_loop
    if not gtk_loop.running():
        logger.error("X11 paste: GTK's loop isn't running to answer the paste")
        return False
    try:
        return bool(gtk_loop.call(_offer_sensitive_x11, text, timeout=timeout))
    except TimeoutError:
        return False


def wait_for_x11_paste() -> None:
    """Before the service exits: give the app a dictation was just pasted
    into time to read it. Only this process holds an X11 offer (no store()),
    so quitting right after the Ctrl+V left the paste empty."""
    left = _x11_offered_at + RESTORE_DELAY_S - time.monotonic()
    if left > 0:
        time.sleep(left)


_x11_owner = None           # the invisible window owning the X11 clipboard, made once
_x11_offered = ""           # what it hands an app that pastes
_x11_offered_at = float("-inf")     # time.monotonic() of the last offer


def _offer_sensitive_x11(text):
    """On GTK's thread: own the X11 clipboard, offering *text* as text and
    marked with _PASSWORD_HINT, as wl-copy --sensitive does on Wayland.
    Clipboard managers that honour the hint (Klipper, CopyQ 8 and later)
    don't keep the dictation; others (Xfce's Clipman, older CopyQ) still do.
    Not copy_text: its store() hands the text to the clipboard manager to
    keep. GTK's selection API, since PyGObject can't reach set_with_data."""
    global _x11_owner, _x11_offered, _x11_offered_at
    from gi.repository import Gdk, GdkX11, Gtk
    if _x11_owner is None:
        _x11_owner = Gtk.Invisible()
        _x11_owner.connect("selection-get", _answer_x11_paste)
        _x11_owner.connect("selection-clear-event", _x11_paste_taken_over)
    _x11_offered = text
    Gtk.selection_clear_targets(_x11_owner, Gdk.SELECTION_CLIPBOARD)
    for info, name in enumerate((*_TEXT_TYPES, _PASSWORD_HINT)):
        Gtk.selection_add_target(_x11_owner, Gdk.SELECTION_CLIPBOARD,
                                 Gdk.Atom.intern(name, False), info)
    # A real time, not CurrentTime, which ICCCM rules out for taking a
    # selection: apps compare it to tell the newest copy.
    now = GdkX11.x11_get_server_time(_x11_owner.get_window())
    owned = Gtk.selection_owner_set(_x11_owner, Gdk.SELECTION_CLIPBOARD, now)
    if owned:
        _x11_offered_at = time.monotonic()
    return owned


def _answer_x11_paste(_owner, selection_data, _info, _time):
    """An app (or a clipboard manager) asks for the offered dictation."""
    target = selection_data.get_target()
    if target.name() == _PASSWORD_HINT:
        selection_data.set(target, 8, b"secret")
    else:
        selection_data.set_text(_x11_offered, -1)


def _x11_paste_taken_over(_owner, _event):
    """Another app owns the clipboard now: drop the dictation kept for it."""
    global _x11_offered
    _x11_offered = ""
    return False


def _wl_copy_help():
    result = subprocess.run(["wl-copy", "--help"], capture_output=True, text=True,
                            errors="replace", timeout=3)
    return result.stdout + result.stderr


@functools.lru_cache(maxsize=1)
def _sensitive_supported():
    """wl-copy --sensitive arrived in wl-clipboard 2.3.0 (some distros back-port it)."""
    try:
        return "--sensitive" in _wl_copy_help()
    except (OSError, subprocess.SubprocessError):   # no wl-copy: nothing to mark
        return False


def wl_copy_command():
    """The wl-copy command for putting a dictation on the clipboard."""
    return ["wl-copy", "--sensitive"] if _sensitive_supported() else ["wl-copy"]


def _wl_paste_types():
    """The types the clipboard offers now; [] when it is empty. errors="replace":
    an app may name a type with any bytes, and only text types are looked for."""
    result = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True,
                            errors="replace", timeout=1)
    return [t for t in result.stdout.splitlines() if t.strip()] if result.returncode == 0 else []


def _wl_paste(mime):
    """The clipboard's contents as *mime*, in bytes. Raises OSError on failure."""
    result = subprocess.run(["wl-paste", "--no-newline", "--type", mime], capture_output=True, timeout=1)
    if result.returncode != 0:
        raise OSError(f"wl-paste exited {result.returncode}")
    return result.stdout


def _wl_copy_bytes(data, mime, sensitive=False):
    """Offer *data* as *mime*; *sensitive* keeps it out of clipboard managers,
    as it came (a password copied from KeePassXC)."""
    flag = ["--sensitive"] if sensitive and _sensitive_supported() else []
    subprocess.run(["wl-copy", *flag, "--type", mime], input=data, timeout=3, check=True)


def _wl_clear():
    subprocess.run(["wl-copy", "--clear"], timeout=3, check=True)


def _wl_copy_sensitive(text):
    """Offer *text* marked sensitive, so clipboard managers don't keep it."""
    subprocess.run(wl_copy_command(), input=text.encode("utf-8"), timeout=3, check=True)


def _read_clipboard():
    """(bytes, mime, sensitive) of what is on the clipboard now, _EMPTY, or
    _NOTHING when it can't be read or is too big to keep. *sensitive* is
    whether a password manager marked it, so it goes back marked too."""
    try:
        types = _wl_paste_types()
        if not types:
            return _EMPTY
        mime = _best_type(types)
        data = _wl_paste(mime)
        if len(data) > _MAX_SAVED_BYTES:
            logger.info("Clipboard contents too large to keep; not restoring them")
            return _NOTHING
        return data, mime, _PASSWORD_HINT in types
    except Exception as e:
        logger.info(f"Couldn't read the clipboard to restore it later: {e}")
        return _NOTHING


def _write_back(saved):
    """Put a _read_clipboard() result back. True if it was. Never raises."""
    if saved is _NOTHING:
        return False
    try:
        if saved is _EMPTY:
            _wl_clear()
        else:
            _wl_copy_bytes(*saved)
        return True
    except Exception as e:
        logger.warning(f"Couldn't restore the clipboard: {e}")
        return False


# Offered only by a file manager's copy (GNOME Files, Dolphin), never a browser's link.
_FILE_MANAGER_TYPES = ("x-special/gnome-copied-files", "application/x-kde4-urilist",
                       "application/x-kde-cutselection")


def _best_type(types):
    """The type to save: copied files as files, plain text if offered,
    otherwise the first one (an image, say).

    Only one type is kept, because wl-copy offers only one: rich text comes
    back as plain text, and files cut in a file manager come back as copied
    (the cut marker is a second type)."""
    if "text/uri-list" in types and any(t in types for t in _FILE_MANAGER_TYPES):
        return "text/uri-list"      # files copied in a file manager, not a link
    for wanted in _TEXT_TYPES:
        if wanted in types:
            return wanted
    return types[0] if types else None


def _timer(delay, fn):
    """Run *fn* after *delay* seconds on a daemon thread; returns its cancel()."""
    t = threading.Timer(delay, fn)
    t.daemon = True
    t.start()
    return t.cancel


class ClipboardKeeper:
    """Puts the user's clipboard back after a paste.

    before_paste() saves it; after_paste(text) restores it RESTORE_DELAY_S
    later, but only if the clipboard still holds the dictation, so something
    the user copied in the meantime is left alone. A second paste before the
    restore keeps the original saved copy, so the user gets back what they
    copied, not the first dictation. Never raises: a clipboard that can't be
    read is simply not restored.

    before_primary() does the same for the primary selection, for a paste
    that puts the dictation there too, so a middle-click still pastes what
    the user last highlighted."""

    def __init__(self, schedule=_timer, delay=RESTORE_DELAY_S):
        self._schedule, self._delay = schedule, delay
        self._lock = threading.Lock()
        self._saved = _NOTHING
        self._primary = _NOTHING    # the user's primary selection: text, or None if empty
        self._last_text = None      # the dictation the pending restore waits on
        self._cancel = None
        self._generation = 0

    def _read(self):
        return _read_clipboard()

    def before_paste(self):
        """Save the user's clipboard before a dictation replaces it."""
        with self._lock:
            self._generation += 1
            if self._cancel is not None:
                # A restore is still pending. While the clipboard holds the
                # last dictation, the copy saved before it is the user's;
                # something they copied since is newer, and is kept instead.
                self._cancel()
                self._cancel = None
                current = self._read()
                if current is not _NOTHING and not _holds(current, self._last_text):
                    self._saved = current
                return
            self._saved = self._read()

    def before_primary(self):
        """Save the primary selection before the dictation goes on it too.
        With a restore still pending, the copy already saved is kept while
        the selection still holds the last dictation (or can't be read);
        something highlighted since is newer, and is kept instead."""
        with self._lock:
            current = _primary_to_keep()
            last = self._last_text
            pending = self._primary is not _NOTHING
            if (pending and (current is _UNREADABLE
                             or (last is not None and current in (last, last.rstrip("\n"))))):
                return
            self._primary = current

    def after_paste(self, text):
        """Schedule the restore, once the app has had time to read *text*."""
        with self._lock:
            if self._saved is _NOTHING and self._primary is _NOTHING:
                return
            generation, self._last_text = self._generation, text
            self._cancel = self._schedule(self._delay, lambda: self._restore(text, generation))

    def _restore(self, text, generation):
        # Under the lock throughout: a paste starting meanwhile waits (the
        # wl-paste / wl-copy calls are time-limited), so it can neither save
        # this dictation as the user's copy nor have its own dictation
        # overwritten by this restore before the app reads it.
        with self._lock:
            if generation != self._generation:
                return          # a newer paste took over; it will restore
            saved, self._saved, self._cancel = self._saved, _NOTHING, None
            primary, self._primary = self._primary, _NOTHING
            if primary not in (_NOTHING, _UNREADABLE):
                _restore_primary(primary, text)
            if saved is _NOTHING:
                return
            try:
                current = _wl_paste(_best_type(_wl_paste_types()) or "text/plain")
            except (OSError, subprocess.SubprocessError):
                current = None      # unreadable: not known to be the dictation, so left alone
            if current != text.encode("utf-8"):
                logger.info("Clipboard changed after the paste; leaving it as it is")
                return
            if _write_back(saved):
                logger.info("Clipboard restored after paste")


def _holds(saved, text):
    """Whether a _read_clipboard() result is the dictation *text*."""
    return isinstance(saved, tuple) and text is not None and saved[0] == text.encode("utf-8")


def _wl_copy_primary(text):
    """Put *text* on the primary selection, or clear it when *text* is None."""
    if text is None:
        subprocess.run(["wl-copy", "--primary", "--clear"], timeout=3, check=True)
    else:
        subprocess.run(wl_copy_command() + ["--primary"], input=text.encode("utf-8"),
                       timeout=3, check=True)


def _restore_primary(saved, text):
    """Put the user's primary selection back (clear it if *saved* is None),
    unless something other than the dictation *text* is on it now."""
    if read_primary() not in (text, text.rstrip("\n")):
        logger.info("Primary selection changed after the paste; leaving it as it is")
        return
    try:
        _wl_copy_primary(saved)
        logger.info("Primary selection restored after paste")
    except Exception as e:
        logger.warning(f"Couldn't restore the primary selection: {e}")


# --- Voice editing: the text selected in the focused app -----------------------------

COPY_WAIT_S = 0.6               # how long an app may take to put its selection on the clipboard
_COPY_POLL_S = 0.05


def _primary_to_keep():
    """The primary selection (the highlighted text) as text, None when
    nothing is highlighted, or _UNREADABLE when wl-paste failed: a selection
    that couldn't be read mustn't be cleared as if it were empty."""
    try:
        result = subprocess.run(["wl-paste", "--primary", "--no-newline"],
                                capture_output=True, timeout=1)
    except (OSError, subprocess.SubprocessError) as e:
        logger.info(f"Couldn't read the primary selection: {e}")
        return _UNREADABLE
    if result.returncode == 0:
        return result.stdout.decode("utf-8", "replace")
    # wl-clipboard 2.2 says "Nothing is copied", 2.0 and 2.1 "No selection".
    if b"Nothing is copied" in result.stderr or b"No selection" in result.stderr:
        return None
    logger.info(f"Couldn't read the primary selection (wl-paste exit {result.returncode})")
    return _UNREADABLE


def read_primary():
    """The primary selection (the highlighted text) as text, or None."""
    text = _primary_to_keep()
    return None if text is _UNREADABLE else text


def _read_text():
    """The clipboard as text, or None when it holds no text."""
    try:
        types = _wl_paste_types()
        mime = next((t for t in _TEXT_TYPES if t in types), None)
        return _wl_paste(mime).decode("utf-8", "replace") if mime else None
    except (OSError, subprocess.SubprocessError):
        return None     # callers leave a clipboard they can't read alone


class ClipboardUnavailable(Exception):
    """The clipboard couldn't be used to read the selection (wl-copy failed,
    or is missing). Not the same as nothing being selected."""


def copy_selection(send_copy, wait=COPY_WAIT_S, sleep=None):
    """The text selected in the focused app, and put_back() to restore what
    the user had on the clipboard. The text is None when nothing is selected.

    The app copies its own selection: *send_copy* presses Ctrl+C (Ctrl+Shift+C
    in a terminal) and returns whether the keystroke went out. A unique marker
    goes on the clipboard first: an app with nothing selected leaves the
    clipboard alone, so the marker still being there means no selection.
    With nothing selected, the clipboard is put back before returning.
    Raises ClipboardUnavailable when the marker can't be put on the clipboard."""
    import secrets
    sleep = sleep or time.sleep
    saved = _read_clipboard()
    marker = f"tuxtalk-selection-probe-{secrets.token_hex(8)}"
    text = None

    def put_back(also=()):
        """Restore the user's clipboard, unless something newer is on it:
        only the marker, the selection or *also* (a pasted rewrite, or what
        the app copied instead of a selection) may be replaced, or nothing at
        all. A password goes back marked sensitive, as it came."""
        current = _read_text()
        if current is None:
            # No text: empty is safe to fill, but an image the user just
            # copied, or a clipboard that can't be read, is left alone.
            try:
                replaceable = not _wl_paste_types()
            except (OSError, subprocess.SubprocessError):
                replaceable = False
        else:
            replaceable = current in {s for s in (marker, text, *also) if s is not None}
        if not replaceable:
            logger.info("Clipboard changed during the voice edit; leaving it as it is")
            return
        if saved is _NOTHING:
            # The user's clipboard couldn't be kept; at least the probe
            # marker mustn't be what their next Ctrl+V pastes.
            if current == marker:
                try:
                    _wl_clear()
                except Exception as e:
                    logger.warning(f"Couldn't clear the clipboard: {e}")
            return
        _write_back(saved)

    try:
        _wl_copy_sensitive(marker)
    except Exception as e:
        logger.warning(f"Couldn't use the clipboard to read the selection: {e}")
        raise ClipboardUnavailable(str(e)) from e
    copied = None
    if send_copy():
        for _ in range(int(wait / _COPY_POLL_S) + 1):
            current = _read_text()
            if current is not None and current != marker:
                copied = current
                break
            sleep(_COPY_POLL_S)
    # Code editors (VS Code, JetBrains, Sublime) copy the whole current line,
    # newline and all, when nothing is selected. A real selection is also the
    # highlighted text (the primary selection); an app without one gets the
    # benefit of the doubt.
    primary = read_primary() if copied and copied.endswith("\n") else None
    rejected = copied
    if primary and primary != copied:
        logger.info("Ctrl+C copied a whole line with nothing selected; not a selection")
        copied = None
    if not copied:
        # What the app copied instead of a selection (that whole line, or
        # nothing) is on the clipboard now, and may be replaced.
        put_back(also=(rejected,))
        return None, put_back
    text = copied
    return text, put_back
