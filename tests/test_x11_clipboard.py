"""The dictation service's X11 clipboard (x11_clipboard): a dictation
offered for pasting, marked for clipboard managers to skip, and with
"Restore clipboard after pasting" on, the user's copy put back afterwards.

These run on xvfb-run's display (conftest refuses any other), with other
processes as the apps that copy and paste; this process's GTK loop answers
them as the service's would.
"""
import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from tuxtalk import clipboard as cb
from tuxtalk import gtk_loop
from tuxtalk import keyboard_output
from tuxtalk import live_settings as live
from tuxtalk import x11_clipboard as x11

# An app pasting now: prints (text, the password hint) as it reads them.
_READ_CLIPBOARD = """
import sys, time
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk
time.sleep(float(sys.argv[1]))
board = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
hint = board.wait_for_contents(Gdk.Atom.intern("x-kde-passwordManagerHint", False))
print(repr((board.wait_for_text(), hint and hint.get_data())), flush=True)
"""
# An app that offers argv[1] (a JSON object of type: text) until its input closes.
_OFFER_AND_STAY = """
import json, sys
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk
offer = json.loads(sys.argv[1])
owner = Gtk.Invisible()
for info, name in enumerate(offer):
    Gtk.selection_add_target(owner, Gdk.SELECTION_CLIPBOARD, Gdk.Atom.intern(name, False), info)
owner.connect("selection-get", lambda _w, data, _i, _t: data.set(
    data.get_target(), 8, offer[data.get_target().name()].encode()))
Gtk.selection_owner_set(owner, Gdk.SELECTION_CLIPBOARD,
                        GdkX11.x11_get_server_time(owner.get_window()))
GLib.io_add_watch(sys.stdin, GLib.IO_IN | GLib.IO_HUP, lambda *a: Gtk.main_quit())
GLib.timeout_add_seconds(20, Gtk.main_quit)
GLib.idle_add(lambda: print("ready", flush=True))
Gtk.main()
"""
# X11: GTK would otherwise find the user's own Wayland desktop (conftest).
_X11_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "GDK_BACKEND": "x11"}
_TEXT = {"UTF8_STRING": "Grüße, what I copied",
         "text/plain;charset=utf-8": "Grüße, what I copied"}


def _pump_until(done, seconds=10):
    """Run this process's GTK loop, which answers the clipboard as the
    service's would, until done() or *seconds*."""
    from gi.repository import Gtk
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        Gtk.main_iteration_do(False)
        time.sleep(0.005)


def _in_the_service(fn):
    """fn() on a thread of its own, its GTK work queued to this thread's
    loop as the service's typing thread queues it to the GTK thread; what
    it returns."""
    got = []
    worker = threading.Thread(target=lambda: got.append(fn()))
    worker.start()
    _pump_until(lambda: not worker.is_alive())
    assert got, "it didn't finish"
    return got[0]


def _start_reading(after_s=0.0):
    """An app that pastes *after_s* from now."""
    return subprocess.Popen([sys.executable, "-c", _READ_CLIPBOARD, str(after_s)],
                            stdout=subprocess.PIPE, env=_X11_ENV, encoding="utf-8")


def _what_it_read(reader):
    """What a _start_reading() app pasted, answering it meanwhile."""
    try:
        _pump_until(lambda: reader.poll() is not None)
        return reader.communicate(timeout=1)[0].strip()
    finally:
        if reader.poll() is None:
            reader.kill()
            reader.wait()


@contextlib.contextmanager
def _app_offering(offer):
    """Another app that has copied *offer* (type: text) and keeps it."""
    app = subprocess.Popen([sys.executable, "-c", _OFFER_AND_STAY, json.dumps(offer)],
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=_X11_ENV,
                           encoding="utf-8")
    try:
        assert app.stdout.readline().strip() == "ready"
        yield app
    finally:
        app.send_signal(signal.SIGCONT)
        app.stdin.close()
        app.wait(timeout=10)


def _paste_keeping(*texts, schedule=None):
    """Paste each of *texts* as the service does with restore_clipboard on,
    one right after the other; the restore then runs at once, or, with
    *schedule*, when it says. Returns the keeper."""
    pending = []
    keeper = cb.ClipboardKeeper(x11.X11Board, schedule=schedule or (
        lambda delay, fn: pending.append(fn) or (lambda: pending.remove(fn))))

    def paste():
        for text in texts:
            keeper.before_paste()
            assert x11.offer_dictation(text)
            keeper.after_paste(text)
        return True
    _in_the_service(paste)
    if schedule is None:
        _in_the_service(lambda: [fn() for fn in pending])
    return keeper


@pytest.fixture
def board(monkeypatch):
    """The service's X11 clipboard state, fresh, its GTK loop "elsewhere"
    (this thread's, pumped by _pump_until), and the display's clipboard
    ownerless to start with."""
    from gi.repository import Gdk, Gtk
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    for name, value in (("_owner", None), ("_offer", None),
                        ("_dictation_read", threading.Event()),
                        ("_dictation_at", float("-inf"))):
        monkeypatch.setattr(x11, name, value)
    monkeypatch.setattr(gtk_loop, "_thread", threading.Thread())
    Gtk.selection_owner_set(None, Gdk.SELECTION_CLIPBOARD, Gdk.CURRENT_TIME)
    yield
    if x11._owner is not None:
        x11._owner.destroy()


# --- The dictation, offered --------------------------------------------------------

def test_a_copy_that_timed_out_never_lands_later(monkeypatch):
    """After the wait gives up, nothing was pasted; the copy still queued
    on a stalled GTK loop must not put that dictation on the clipboard
    when the loop catches up."""
    from gi.repository import GLib
    copied = []
    monkeypatch.setattr(x11, "_offer_dictation", lambda text, read: copied.append(text) or True)
    monkeypatch.setattr(gtk_loop, "_thread", threading.Thread())   # running, stalled
    assert x11.offer_dictation("never pasted", timeout=0.05) is False
    context = GLib.MainContext.default()
    while context.iteration(False):
        pass
    assert copied == []


def test_an_app_pastes_a_dictation_marked_for_managers_to_skip(board, monkeypatch):
    """A dictation reaches the app that pastes it, another process, with the
    hint clipboard managers skip, and is never handed to the clipboard
    manager to keep."""
    from gi.repository import Gtk
    monkeypatch.setattr(Gtk.Clipboard, "store", lambda self: pytest.fail("handed to the manager"))
    assert _in_the_service(lambda: x11.offer_dictation("Привіт, café"))
    assert _what_it_read(_start_reading()) == repr(("Привіт, café", b"secret"))


def test_no_offer_without_the_loop_that_answers_pastes(monkeypatch):
    """With GTK's loop not running nothing would answer the app's paste:
    reported as not pasted rather than typed as nothing."""
    monkeypatch.setattr(gtk_loop, "running", lambda: False)
    monkeypatch.setattr(x11, "_offer_dictation", lambda text, read: pytest.fail("offered"))
    assert x11.offer_dictation("hello") is False


def test_quitting_right_after_a_paste_waits_for_the_app_to_read_it(monkeypatch):
    """Only the service holds the offer: gone at once, the app found
    nothing to paste."""
    slept = []
    monkeypatch.setattr(x11.time, "sleep", slept.append)
    monkeypatch.setattr(x11, "_dictation_at", time.monotonic())
    x11.wait_for_paste()
    assert slept and 0.4 < slept[0] <= cb.RESTORE_DELAY_S
    slept.clear()
    monkeypatch.setattr(x11, "_dictation_at", time.monotonic() - 5)
    x11.wait_for_paste()
    assert slept == []


# --- The user's copy, put back -----------------------------------------------------

def test_a_paste_keeps_the_clipboard_only_when_the_setting_is_on(monkeypatch):
    """Off by default, as on Wayland; on, the user's copy is saved before the
    dictation is offered and put back after. An offer that failed left the
    clipboard as it was: only an earlier dictation's restore is left to do."""
    events = []

    class Keeper:
        def __init__(self, board):
            events.append(("made for", board))

        def before_paste(self):
            events.append("save")

        def after_paste(self, text):
            events.append(("restore", text))

        def paste_failed(self):
            events.append("failed")
    monkeypatch.setattr(cb, "ClipboardKeeper", Keeper)
    monkeypatch.setattr(keyboard_output, "_clipboard_keeper", None)
    monkeypatch.setattr(x11, "offer_dictation", lambda text: events.append(("offer", text)) or True)
    monkeypatch.setattr(keyboard_output, "send_paste_keystroke", lambda *a, **k: True)
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "xed")
    monkeypatch.setattr("time.sleep", lambda s: None)

    monkeypatch.setattr(live, "restore_clipboard", False)
    assert keyboard_output.paste_text_x11("hello") is True
    assert events == [("offer", "hello")]

    events.clear()
    monkeypatch.setattr(live, "restore_clipboard", True)
    assert keyboard_output.paste_text_x11("hello") is True
    monkeypatch.setattr(x11, "offer_dictation", lambda text: False)
    assert keyboard_output.paste_text_x11("again") is False
    assert events == [("made for", x11.X11Board), "save", ("offer", "hello"),
                      ("restore", "hello"), "save", "failed"]


def test_something_copied_after_the_paste_is_not_replaced(board):
    """Another app has copied since the paste, and this process has heard
    of it: the restore leaves its copy alone."""
    with _app_offering(_TEXT):
        _paste_keeping("dictated words", schedule=lambda delay, fn: lambda: None)
    with _app_offering({"UTF8_STRING": "copied just now"}):
        _pump_until(lambda: x11._offer is None)
        assert _in_the_service(lambda: x11.X11Board.put_back(
            (b"what I copied", "UTF8_STRING", False, ()), "dictated words")) is False
        assert _what_it_read(_start_reading()) == repr(("copied just now", None))


def test_a_copy_the_service_hasnt_heard_of_yet_is_not_replaced(board):
    """Another app copies right before the restore, and the news of it
    hasn't reached the service: the X server refuses the restore, as it is
    asked with the time the dictation was offered."""
    with _app_offering(_TEXT):
        _paste_keeping("dictated words", schedule=lambda delay, fn: lambda: None)
    with _app_offering({"UTF8_STRING": "copied just now"}):
        # Not pumped: the service hasn't heard that it lost the clipboard.
        assert x11._offer.text == "dictated words"
        assert x11._put_back_over((b"what I copied", "UTF8_STRING", False, ()),
                                  "dictated words") is False
        assert _what_it_read(_start_reading()) == repr(("copied just now", None))


def test_a_copy_made_while_it_is_read_is_not_saved_as_the_first(board, monkeypatch):
    """The types and the copy are two questions. A password manager copying
    in between had its password saved as the plain text before it, put back
    without the mark, and handed to the clipboard manager at quit."""
    from gi.repository import Gtk
    request_text = Gtk.Clipboard.request_text
    password_manager = []

    def password_copied_first(self, callback):
        password_manager.append(contextlib.ExitStack())
        password_manager[0].enter_context(_app_offering(
            {"UTF8_STRING": "hunter2", "x-kde-passwordManagerHint": "secret"}))
        request_text(self, callback)
    monkeypatch.setattr(Gtk.Clipboard, "request_text", password_copied_first)
    with _app_offering(_TEXT):
        try:
            assert _in_the_service(x11.X11Board.read) is cb.NOTHING
        finally:
            password_manager[0].close()


@pytest.mark.parametrize("dictations", [["dictated words"], ["first", "second"]],
                         ids=["one dictation", "two before the restore"])
def test_what_another_app_copied_comes_back_after_the_paste(board, dictations):
    """Two dictations in quick succession bring back what the user copied,
    not the first dictation. Not marked as a password: it wasn't one."""
    with _app_offering(_TEXT):
        _paste_keeping(*dictations)
    assert _what_it_read(_start_reading()) == repr(("Grüße, what I copied", None))


def test_an_empty_clipboard_is_empty_again_after_the_paste(board):
    _paste_keeping("dictated words")
    assert _what_it_read(_start_reading()) == repr((None, None))


def test_a_copied_password_is_read_as_one_and_comes_back_marked(board):
    """KeePassXC marks a copied password; put back unmarked, Klipper would
    keep it in its history."""
    with _app_offering({"UTF8_STRING": "hunter2", "x-kde-passwordManagerHint": "secret"}):
        assert _in_the_service(x11.X11Board.read) == (b"hunter2", cb.TEXT_TYPES[0], True, ())
        _paste_keeping("dictated words")
    assert _what_it_read(_start_reading()) == repr(("hunter2", b"secret"))


def test_a_copied_picture_comes_back_as_a_picture(board):
    """Gwenview and Okular list Qt's own type first; PNG is what other apps
    can paste."""
    from gi.repository import Gdk, Gtk
    with _app_offering({"application/x-qt-image": "qt", "image/png": "\x89PNG not really"}):
        _paste_keeping("dictated words")
    got = []
    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).request_contents(
        Gdk.Atom.intern("image/png", False), lambda _board, data: got.append(data.get_data()))
    _pump_until(lambda: got)
    assert got == ["\x89PNG not really".encode()]


@pytest.mark.parametrize("how", ["copy", "cut"])
def test_files_copied_in_a_file_manager_come_back_for_it_to_paste(board, how):
    """Nemo and Caja paste only x-special/gnome-copied-files, which also says
    whether the files were cut; put back as their paths in text, the files
    pasted only into a text editor. The paths still paste there as text."""
    from gi.repository import Gdk, Gtk
    uris = "file:///home/me/photo.jpg"
    offer = {"x-special/gnome-copied-files": f"{how}\n{uris}", "text/uri-list": uris,
             "UTF8_STRING": "/home/me/photo.jpg"}
    with _app_offering(offer):
        _paste_keeping("dictated words")
    got = {}
    for name in ("text/uri-list", "x-special/gnome-copied-files"):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).request_contents(
            Gdk.Atom.intern(name, False),
            lambda _board, data, name=name: got.__setitem__(name, data.get_data()))
    _pump_until(lambda: len(got) == 2)
    assert got == {"text/uri-list": uris.encode(),
                   "x-special/gnome-copied-files": f"{how}\n{uris}".encode()}
    assert _what_it_read(_start_reading()) == repr(("/home/me/photo.jpg", None))


def test_a_slow_app_gets_the_dictation_not_the_copy_put_back(board):
    """The restore waits for the app to read the dictation: one that asks a
    second after the Ctrl+V still gets it (with the fixed half
    second, it got the user's copy), and the copy comes back after."""
    with _app_offering(_TEXT):
        reader = []

        def paste():
            keeper = cb.ClipboardKeeper(x11.X11Board)
            keeper.before_paste()
            assert x11.offer_dictation("dictated words")
            reader.append(_start_reading(after_s=1.0))
            keeper.after_paste("dictated words")
            return True
        _in_the_service(paste)
        assert _what_it_read(reader[0]) == repr(("dictated words", b"secret"))
        _pump_until(lambda: x11._offer is not None and x11._offer.read is None, seconds=3)
    assert _what_it_read(_start_reading()) == repr(("Grüße, what I copied", None))


@pytest.fixture
def pasting(monkeypatch):
    """keyboard_output's X11 paste with restore_clipboard on and a fresh
    keeper, its keys on the suite's recording keyboard; the window it finds
    is *looked_up*'s, which a test may replace."""
    monkeypatch.setattr(keyboard_output, "_clipboard_keeper", None)
    monkeypatch.setattr(live, "restore_clipboard", True)
    window = {"looked_up": lambda: "xed"}
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: window["looked_up"]())
    return window


def test_a_clipboard_manager_reading_first_doesnt_hurry_the_restore(board, pasting,
                                                                     monkeypatch):
    """Clipman and CopyQ before 8 read each new copy at once; that read used
    to count as the app's, and an app pasting a second later got the user's
    copy. A manager reading as late as the window lookup before the paste
    key doesn't count either."""
    app = []

    def manager_reads_then_window():
        manager = _start_reading()
        manager.wait(timeout=10)
        app.append(_start_reading(after_s=1.0))
        return "xed"
    pasting["looked_up"] = manager_reads_then_window
    with _app_offering(_TEXT):
        assert _in_the_service(lambda: keyboard_output.paste_text_x11("dictated words"))
        assert _what_it_read(app[0]) == repr(("dictated words", b"secret"))


def test_quitting_right_after_a_paste_still_puts_the_copy_back_and_keeps_it(
        board, pasting, monkeypatch):
    """Quitting or restarting within two seconds of a paste ended the service
    before the restore ran, and the user's copy went with it. Now the
    restore runs first, and the clipboard manager keeps the copy."""
    from gi.repository import Gdk, Gtk
    from tuxtalk import service_tick
    stored = []
    monkeypatch.setattr(Gdk.Display, "supports_clipboard_persistence", lambda self: True)
    monkeypatch.setattr(Gtk.Clipboard, "store", lambda self: stored.append(self.wait_for_text()))
    with _app_offering(_TEXT):
        assert _in_the_service(lambda: keyboard_output.paste_text_x11("dictated words"))
    _in_the_service(lambda: service_tick._leave_the_clipboard() or True)
    assert stored == ["Grüße, what I copied"]


def test_a_failed_paste_still_lets_the_earlier_dictations_restore_run(board, pasting,
                                                                       monkeypatch):
    """A paste whose offer failed called off the restore of the dictation
    before it, which then stayed on the clipboard for good."""
    pending = []
    monkeypatch.setattr(keyboard_output, "_clipboard_keeper", cb.ClipboardKeeper(
        x11.X11Board, schedule=lambda delay, fn: pending.append(fn) or (
            lambda: pending.remove(fn))))
    with _app_offering(_TEXT):
        assert _in_the_service(lambda: keyboard_output.paste_text_x11("first"))
    monkeypatch.setattr(x11, "offer_dictation", lambda text: False)
    assert _in_the_service(lambda: keyboard_output.paste_text_x11("second")) is False
    _in_the_service(lambda: [fn() for fn in pending])
    assert _what_it_read(_start_reading()) == repr(("Grüße, what I copied", None))


def test_an_app_that_never_answers_is_given_up_on(board):
    """A frozen app owning the clipboard: the read gives up within about two
    seconds, and the paste goes ahead without a copy to put back."""
    with _app_offering(_TEXT) as app:
        app.send_signal(signal.SIGSTOP)
        started = time.monotonic()
        assert _in_the_service(x11.X11Board.read) is cb.NOTHING
        assert time.monotonic() - started < 2 * x11.READ_WAIT_S + 0.5


# --- Quitting ---------------------------------------------------------------------

@pytest.mark.parametrize("what, kept", [("my copy", True), ("a password", False),
                                        ("a dictation", False)])
def test_quitting_hands_the_copy_put_back_to_the_clipboard_manager(board, monkeypatch,
                                                                   what, kept):
    """Offered by the service, the user's copy would go when TuxTalk quits
    or restarts: the clipboard manager keeps it. Never a password, which
    KeePassXC means to go, nor a dictation."""
    from gi.repository import Gdk, Gtk
    stored = []
    monkeypatch.setattr(Gdk.Display, "supports_clipboard_persistence", lambda self: True)
    monkeypatch.setattr(Gtk.Clipboard, "store", lambda self: stored.append(self.wait_for_text()))
    if what == "a dictation":
        assert _in_the_service(lambda: x11.offer_dictation("dictated words"))
    else:
        assert x11._put_back((b"copied", "UTF8_STRING", what == "a password", ()))
    _in_the_service(x11.keep_copy_at_quit)
    assert stored == (["copied"] if kept else [])


def test_gtk_too_busy_to_read_or_restore_is_logged(board, monkeypatch, caplog):
    """A GTK thread that doesn't get to the read or the restore in time:
    the restore is lost, and the log says why."""
    monkeypatch.setattr(x11, "READ_WAIT_S", 0.05)      # nothing pumps this thread's loop
    with caplog.at_level("INFO", logger="tuxtalk.x11_clipboard"):
        assert x11.X11Board.read() is cb.NOTHING
        assert x11.X11Board.put_back(cb.EMPTY, "dictated words") is False
    assert caplog.text.count("GTK's thread was busy") == 2

