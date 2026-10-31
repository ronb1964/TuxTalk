"""
The dictation service's X11 clipboard: offering a dictation for pasting,
marked so clipboard managers can skip it (offer_dictation), and putting the
user's copy back afterwards (X11Board, for clipboard.ClipboardKeeper).

X11 has no wl-copy to lean on: the service owns the clipboard itself, through
an invisible GTK window on its GTK loop's thread (gtk_loop), and answers the
app's paste from there. Everything that touches GTK runs on that thread, and
the clipboard's state (what is offered) is only changed there; the typing
thread that offers a dictation keeps the Event its read sets
(_dictation_read), which only it replaces and clears.
"""
import collections
import threading
import time

from .clipboard import (EMPTY, FILE_MANAGER_TYPES, MAX_SAVED_BYTES, NOTHING, PASSWORD_HINT,
                        RESTORE_DELAY_S, TEXT_TYPES, best_type)
from .logger import setup_logger

logger = setup_logger(__name__)

# What the clipboard offers: *text* in every text type, or else *data*, a
# dict of type to bytes; *sensitive* marks it for clipboard managers to skip.
# A dictation has *read*, an Event set when an app reads it; the user's own
# copy put back has None.
_Offer = collections.namedtuple("_Offer", "text data sensitive read")

_owner = None                       # the invisible window owning the clipboard, made once
_offer = None                       # what it offers now, or None
_owned_at = None                    # the X server's time when it took the clipboard
_dictation_read = threading.Event()     # set when an app reads the latest dictation
_dictation_at = float("-inf")       # time.monotonic() of the latest dictation offered

# The owner of the user's copy is another app, answering through the X
# server; GTK waits up to 30 s for one that doesn't. So the copy is asked for
# without holding GTK's thread, and each step given up on after this: up to
# about two seconds in all (getting onto GTK's thread, then the answer), as
# Wayland's two wl-paste calls of a second each.
READ_WAIT_S = 1.0
# The restore waits for the app to read the dictation, at least
# RESTORE_DELAY_S, and at most this, for an app that never asks. Only a
# read after the paste key counts (reads_from_now): a clipboard manager
# reads each new copy at once.
RESTORE_CAP_S = 2.0
# Quitting waits at most this for a clipboard manager to take the user's
# copy.
QUIT_STORE_WAIT_S = 1.0
# What every X11 owner offers about its copy rather than the copy itself.
_META_TYPES = ("TARGETS", "TIMESTAMP", "MULTIPLE", "SAVE_TARGETS", "DELETE",
               "INSERT_PROPERTY", "INSERT_SELECTION", PASSWORD_HINT)


def offer_dictation(text, timeout=1.0):
    """Put a dictation on the clipboard for pasting, from any thread but
    GTK's; True on success.

    The service keeps the clipboard afterwards and its GTK loop answers the
    target app's paste request, so without a running loop nothing could be
    pasted. If the loop doesn't get to it within *timeout*, this returns
    False and the offer is called off: nothing gets pasted, so the dictation
    mustn't land on the clipboard later.
    """
    global _dictation_read
    from . import gtk_loop
    if not gtk_loop.running():
        logger.error("X11 paste: GTK's loop isn't running to answer the paste")
        return False
    read = threading.Event()
    try:
        offered = bool(gtk_loop.call(_offer_dictation, text, read, timeout=timeout))
    except TimeoutError:
        return False
    if offered:
        _dictation_read = read
    return offered


def reads_from_now() -> None:
    """Right before the paste key goes down, after every check and wait:
    forget reads of the dictation so far, a clipboard manager's (Clipman,
    CopyQ before 8), so the restore waits for the app's own. Called by the
    thread that offered it."""
    _dictation_read.clear()


def wait_for_paste() -> None:
    """Before the service exits: give the app a dictation was just pasted
    into time to read it. Only this process holds the offer (no store()), so
    quitting right after the Ctrl+V left the paste empty."""
    left = _dictation_at + RESTORE_DELAY_S - time.monotonic()
    if left > 0:
        time.sleep(left)


def keep_copy_at_quit(wait=QUIT_STORE_WAIT_S) -> None:
    """Before the service exits: hand the user's copy, put back after a
    paste, to the clipboard manager (Cinnamon's, MATE's, Xfce's, Klipper),
    which keeps it once TuxTalk is gone. Only text, and never a dictation or
    a password: those go with TuxTalk, as KeePassXC means a password to."""
    from . import gtk_loop
    if not gtk_loop.running():
        return
    stored = threading.Event()
    try:
        gtk_loop.call(_store_copy, stored, timeout=wait)
    except TimeoutError:
        return
    if not stored.wait(wait):
        logger.info("The clipboard manager didn't take the clipboard before quitting")


def _offer_dictation(text, read):
    """On GTK's thread: offer *text*, marked with PASSWORD_HINT as wl-copy
    --sensitive does on Wayland, and set *read* when an app reads it.
    Clipboard managers that honour the hint (Klipper, CopyQ 8 and later)
    don't keep the dictation; others (Xfce's Clipman, older CopyQ) still do.
    Not clipboard.copy_text: its store() hands the text to the clipboard
    manager to keep."""
    global _dictation_at
    owned = _own(_Offer(text, None, True, read))
    if owned:
        _dictation_at = time.monotonic()
    return owned


def _own(offer, at=None):
    """On GTK's thread: own the clipboard, offering *offer*; True if it
    does. With *at*, the X server time it took the clipboard before: the
    server then refuses if another app has copied since, even if this
    process hasn't heard of it yet. GTK's selection API, since PyGObject
    can't reach set_with_data."""
    global _owner, _offer, _owned_at
    from gi.repository import Gdk, GdkX11, Gtk
    if _owner is None:
        _owner = Gtk.Invisible()
        _owner.connect("selection-get", _answer_paste)
        _owner.connect("selection-clear-event", _taken_over)
    _offer = offer
    types = TEXT_TYPES if offer.text is not None else tuple(offer.data)
    Gtk.selection_clear_targets(_owner, Gdk.SELECTION_CLIPBOARD)
    for info, name in enumerate((*types, PASSWORD_HINT) if offer.sensitive else types):
        Gtk.selection_add_target(_owner, Gdk.SELECTION_CLIPBOARD,
                                 Gdk.Atom.intern(name, False), info)
    # A real time, not CurrentTime, which ICCCM rules out for taking a
    # selection: apps compare it to tell the newest copy.
    now = at or GdkX11.x11_get_server_time(_owner.get_window())
    if not Gtk.selection_owner_set(_owner, Gdk.SELECTION_CLIPBOARD, now):
        _offer = None
        return False
    _owned_at = now
    return True


def _answer_paste(_owner, selection_data, _info, _time):
    """An app (or a clipboard manager) asks for what is offered: the
    dictation, or the user's copy put back."""
    target = selection_data.get_target()
    name = target.name()
    if name == PASSWORD_HINT:
        selection_data.set(target, 8, b"secret")
    elif _offer.text is not None:
        selection_data.set_text(_offer.text, -1)
        if _offer.read is not None:
            _offer.read.set()
    else:
        selection_data.set(target, 8, _offer.data[name])


def _taken_over(_owner, _event):
    """Another app owns the clipboard now: drop what was offered."""
    global _offer
    _offer = None
    return False


class X11Board:
    """The X11 clipboard, for clipboard.ClipboardKeeper. Kept on X11, the
    user's copy is the service's to offer until something else is copied:
    quitting TuxTalk hands text to the clipboard manager (keep_copy_at_quit);
    a password, a picture or files go with it."""

    @staticmethod
    def read():
        """What it holds: (bytes, type, sensitive, more), EMPTY or NOTHING;
        *more* is the other (type, bytes) pairs a file manager's copy offers,
        which say "copy" or "cut" to it."""
        from . import gtk_loop
        if not gtk_loop.running():
            return NOTHING
        answered, got = threading.Event(), []

        def done(saved):
            got.append(saved)
            answered.set()
        try:
            gtk_loop.call(_ask_owner, done, timeout=READ_WAIT_S)
        except TimeoutError:
            logger.warning("Couldn't read the clipboard to restore it later: GTK's thread was busy")
            return NOTHING
        if not answered.wait(READ_WAIT_S):
            logger.info("The app that owns the clipboard didn't answer; not restoring it")
            return NOTHING
        return got[0]

    @staticmethod
    def put_back(saved, over):
        """Put a read() result back, but only over the dictation *over*:
        something copied since stays. True if it was put back."""
        from . import gtk_loop
        try:
            return bool(gtk_loop.call(_put_back_over, saved, over, timeout=READ_WAIT_S))
        except TimeoutError:
            logger.warning("Couldn't restore the clipboard: GTK's thread was busy")
            return False

    @staticmethod
    def schedule(delay, fn):
        """Run *fn* on a thread of its own once an app has read the latest
        dictation and *delay* has passed, or after RESTORE_CAP_S; returns
        its cancel(). The service answers the paste itself, so a slow app
        isn't handed the user's copy instead of the dictation."""
        read, cancelled, pasted_at = _dictation_read, threading.Event(), time.monotonic()

        def wait():
            if _wait_for_read(read, pasted_at, delay, cancelled):
                fn()
        threading.Thread(target=wait, daemon=True, name="tt-clipboard-restore").start()
        return cancelled.set

    @staticmethod
    def wait_for_read(pasted_at, delay):
        """Wait as schedule() would for a paste made at *pasted_at*."""
        _wait_for_read(_dictation_read, pasted_at, delay, threading.Event())


def _wait_for_read(read, pasted_at, delay, cancelled):
    """Wait until *delay* after *pasted_at* and *read* is set, or until
    RESTORE_CAP_S after it; False if *cancelled* meanwhile."""
    if cancelled.wait(max(0.0, pasted_at + delay - time.monotonic())):
        return False
    read.wait(max(0.0, pasted_at + RESTORE_CAP_S - time.monotonic()))
    return not cancelled.is_set()


def _put_back_over(saved, over):
    """On GTK's thread: X11Board.put_back(). Looked at and done in one go,
    so nothing this process hears of can come in between, and with the
    time the dictation was offered, so the X server refuses it over a copy
    made since that this process hasn't heard of yet."""
    if not (_offer is not None and _offer.read is not None and _offer.text == over
            and _put_back(saved, at=_owned_at)):
        logger.info("Clipboard changed after the paste; leaving it as it is")
        return False
    return True


def _ask_owner(done):
    """On GTK's thread: ask the clipboard's owner which types it offers, then
    for the best one, and hand done() the result as X11Board.read() gives
    it. Text is asked for as text, so GTK converts whichever text type the
    app offers; files, and what isn't text, in their own type (best_type).
    An owner that offers no types is an empty clipboard: every X11 app
    answers that question (ICCCM)."""
    from gi.repository import Gdk, Gtk
    board = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    # The types and the copy are separate questions: an app copying in
    # between (a password manager, or the same app copying again) would have
    # its copy saved as the first one's, say not marked as a password.
    changed = []
    watch = board.connect("owner-change", lambda *_args: changed.append(True))

    def done_reading(saved):
        board.disconnect(watch)
        if changed and saved is not NOTHING:
            logger.info("The clipboard changed while it was read; not restoring it")
            saved = NOTHING
        done(saved)

    def got_types(_board, answer):
        """The owner's types are in *answer*: ask for its copy in the best."""
        # Asked of no owner, get_targets() trips a GLib warning: nothing came.
        found, atoms = answer.get_targets() if answer.get_length() >= 0 else (False, [])
        names = [atom.name() for atom in atoms] if found else []
        types = [name for name in names if name not in _META_TYPES]
        if not types:
            done_reading(EMPTY)
            return
        sensitive = PASSWORD_HINT in names
        wanted = best_type(types)
        # Files copied in a file manager are offered as text too (their paths).
        if wanted != "text/uri-list" and Gtk.targets_include_text(atoms):
            board.request_text(lambda _board, text: done_reading(_saved(
                None if text is None else text.encode("utf-8"), TEXT_TYPES[0], sensitive)))
            return
        # With the file manager's own types, and the paths as text for an editor.
        files = (*FILE_MANAGER_TYPES, *TEXT_TYPES)
        more = [t for t in files if t in types] if wanted == "text/uri-list" else []
        _ask_for_types(board, [wanted, *more], {}, lambda got: done_reading(_saved(
            got.get(wanted), wanted, sensitive, tuple((t, got[t]) for t in more if t in got))))
    board.request_contents(Gdk.Atom.intern("TARGETS", False), got_types)


def _ask_for_types(board, types, got, finished):
    """On GTK's thread: ask *board*'s owner for each of *types* in turn,
    collecting the answers in *got* (type to bytes), then finished(got)."""
    from gi.repository import Gdk
    if not types:
        finished(got)
        return
    wanted, rest = types[0], types[1:]

    def answered(_board, data):
        if data.get_length() >= 0:
            got[wanted] = data.get_data()
        _ask_for_types(board, rest, got, finished)
    board.request_contents(Gdk.Atom.intern(wanted, False), answered)


def _saved(data, mime, sensitive, more=()):
    """*data* of type *mime* as a saved copy, or NOTHING when the owner
    didn't give it or it is too big to keep."""
    if data is None:
        logger.info("Couldn't read the clipboard to restore it later")
        return NOTHING
    if len(data) + sum(len(extra) for _type, extra in more) > MAX_SAVED_BYTES:
        logger.info("Clipboard contents too large to keep; not restoring them")
        return NOTHING
    return data, mime, sensitive, more


def _put_back(saved, at=None):
    """On GTK's thread: offer the user's copy *saved* again, as it came
    (text in every text type, files in each type they came in), or leave
    the clipboard empty if it was; *at* as for _own(). True if it was."""
    global _offer
    from gi.repository import Gdk, GdkX11, Gtk
    if saved is NOTHING:
        return False
    if saved is EMPTY:
        _offer = None
        now = at or GdkX11.x11_get_server_time(_owner.get_window())
        # False when the X server kept another app's copy: GDK only knows
        # this process's windows, so asking it who owns the clipboard
        # can't tell that from an empty one.
        return Gtk.selection_owner_set(None, Gdk.SELECTION_CLIPBOARD, now)
    data, mime, sensitive, more = saved
    if mime in TEXT_TYPES:
        return _own(_Offer(data.decode("utf-8", "replace"), None, sensitive, None), at)
    return _own(_Offer(None, {mime: data, **dict(more)}, sensitive, None), at)


def _store_copy(stored):
    """On GTK's thread: hand the user's copy put back to the clipboard
    manager, if it is text and no password, then set *stored*. GTK's
    store() runs once this returns, as it waits for the manager in a loop
    of its own."""
    from gi.repository import Gdk, GLib, Gtk
    offer = _offer
    if (offer is None or offer.read is not None or offer.sensitive or offer.text is None
            or not Gdk.Display.get_default().supports_clipboard_persistence()):
        stored.set()
        return
    board = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    board.set_text(offer.text, -1)
    board.set_can_store(None)

    def store():
        board.store()
        stored.set()
        return False
    GLib.idle_add(store)
