"""Paste mode and the clipboard (GitHub issue #7).

Dictations are always marked sensitive, so clipboard managers that honour the
mark (Klipper, CopyQ 8 and later) don't save them to their history, and on
Wayland and X11, with "Restore clipboard after pasting" on, whatever the user
had copied comes back after the paste.
"""
import os
import subprocess
import sys
import time

import pytest

from tuxtalk import clipboard as cb
from tuxtalk import config as C
from tuxtalk import live_settings as live
from tuxtalk import keyboard_output

_REAL_PRIMARY_TO_KEEP = cb._primary_to_keep


class FakeClipboard:
    """The Wayland clipboard as wl-paste / wl-copy see it."""

    def __init__(self, data=b"what I copied", mime="text/plain;charset=utf-8"):
        self.data, self.mime = data, mime
        self.password = False
        self.restores = []

    def types(self):
        if self.data is None:
            return []
        return ([self.mime] + (["TEXT"] if self.mime.startswith("text/") else [])
                + (["x-kde-passwordManagerHint"] if self.password else []))

    def read(self, mime):
        return self.data

    def write(self, data, mime, sensitive=False):
        self.data, self.mime, self.password = data, mime, sensitive
        self.restores.append((data, mime) + (("sensitive",) if sensitive else ()))

    def clear(self):
        self.data = None
        self.restores.append(None)


@pytest.fixture
def board(monkeypatch):
    fake = FakeClipboard()
    monkeypatch.setattr(cb, "_wl_paste_types", fake.types)
    monkeypatch.setattr(cb, "_wl_paste", fake.read)
    monkeypatch.setattr(cb, "_wl_copy_bytes", fake.write)
    monkeypatch.setattr(cb, "_wl_clear", fake.clear)
    return fake


@pytest.fixture
def keeper():
    pending = []
    k = cb.ClipboardKeeper(schedule=lambda delay, fn: pending.append(fn) or (lambda: pending.remove(fn)))
    k.pending = pending
    return k


def paste(keeper, board, text):
    """What _paste_text does around a paste: remember, paste, schedule restore."""
    keeper.before_paste()
    board.data, board.mime = text.encode(), "text/plain;charset=utf-8"
    keeper.after_paste(text)


def run_pending(keeper):
    while keeper.pending:
        keeper.pending.pop(0)()


def test_the_old_clipboard_comes_back_after_the_paste(keeper, board):
    paste(keeper, board, "dictated words")
    assert board.data == b"dictated words"          # the app can still read it...
    run_pending(keeper)
    assert board.data == b"what I copied"           # ...and then it's back


def test_something_copied_meanwhile_is_left_alone(keeper, board):
    paste(keeper, board, "dictated words")
    board.data = b"copied right after"
    run_pending(keeper)
    assert board.data == b"copied right after" and board.restores == []


def test_two_quick_dictations_restore_the_original_not_the_first(keeper, board):
    paste(keeper, board, "first")
    paste(keeper, board, "second")                  # before the first restore ran
    run_pending(keeper)
    assert board.data == b"what I copied" and len(board.restores) == 1


def test_something_copied_between_two_quick_dictations_is_what_comes_back(keeper, board):
    """Copied while the first dictation's restore was pending, it's the
    user's newest clipboard; the copy saved before the first is older."""
    paste(keeper, board, "first")
    board.data = b"copied in between"
    paste(keeper, board, "second")
    run_pending(keeper)
    assert board.data == b"copied in between"


def test_an_empty_clipboard_stays_empty(keeper, board):
    board.data = None
    paste(keeper, board, "dictated words")
    run_pending(keeper)
    assert board.data is None and board.restores == [None]


def test_an_image_on_the_clipboard_comes_back_as_an_image(keeper, board):
    board.data, board.mime = b"\x89PNG...", "image/png"
    paste(keeper, board, "dictated words")
    run_pending(keeper)
    assert (board.data, board.mime) == (b"\x89PNG...", "image/png")


def test_text_is_preferred_when_the_clipboard_offers_several_types(monkeypatch):
    monkeypatch.setattr(cb, "_wl_paste_types",
                        lambda: ["image/png", "text/html", "text/plain;charset=utf-8", "TEXT"])
    assert cb._best_type(cb._wl_paste_types()) == "text/plain;charset=utf-8"


def test_a_clipboard_that_cannot_be_read_is_not_touched(keeper, board, monkeypatch):
    def broken(mime):
        raise OSError("wl-paste timed out")
    monkeypatch.setattr(cb, "_wl_paste", broken)
    paste(keeper, board, "dictated words")
    run_pending(keeper)
    assert board.restores == []


@pytest.fixture
def primary(monkeypatch):
    """The primary selection (what was last highlighted), as wl-paste sees it."""
    sel = {"text": "my highlighted words", "writes": []}

    def write(text):
        sel["text"] = text
        sel["writes"].append(text)
    monkeypatch.setattr(cb, "read_primary", lambda: sel["text"])
    monkeypatch.setattr(cb, "_primary_to_keep", lambda: sel["text"])
    monkeypatch.setattr(cb, "_wl_copy_primary", write)
    return sel


def paste_with_primary(keeper, board, primary, text):
    """A paste into an unknown window, which puts the dictation on the
    primary selection as well."""
    keeper.before_paste()
    keeper.before_primary()
    board.data, board.mime = text.encode(), "text/plain;charset=utf-8"
    primary["text"] = text
    keeper.after_paste(text)


def test_the_primary_selection_comes_back_too(keeper, board, primary):
    """A middle-click after dictating pastes what the user highlighted, not
    the dictation."""
    paste_with_primary(keeper, board, primary, "dictated words")
    run_pending(keeper)
    assert primary["text"] == "my highlighted words"
    assert board.data == b"what I copied"


def test_two_quick_dictations_restore_the_original_primary(keeper, board, primary):
    paste_with_primary(keeper, board, primary, "first")
    paste_with_primary(keeper, board, primary, "second")
    run_pending(keeper)
    assert primary["text"] == "my highlighted words" and primary["writes"] == [
        "my highlighted words"]


def test_a_highlight_between_two_quick_dictations_is_what_comes_back(keeper, board, primary):
    paste_with_primary(keeper, board, primary, "first")
    primary["text"] = "highlighted in between"
    paste_with_primary(keeper, board, primary, "second")
    run_pending(keeper)
    assert primary["text"] == "highlighted in between"


def test_a_newer_highlight_is_left_alone(keeper, board, primary):
    paste_with_primary(keeper, board, primary, "dictated words")
    primary["text"] = "highlighted right after"
    run_pending(keeper)
    assert primary["text"] == "highlighted right after" and primary["writes"] == []


def test_an_empty_primary_selection_is_cleared_again(keeper, board, primary):
    primary["text"] = None
    paste_with_primary(keeper, board, primary, "dictated words")
    run_pending(keeper)
    assert primary["writes"] == [None]


def test_a_highlight_that_could_not_be_read_is_not_cleared(keeper, board, primary,
                                                            monkeypatch):
    """A wl-paste that times out isn't an empty selection: clearing it
    after the paste would lose what the user had highlighted."""
    def wl_paste_hangs(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(cb, "_primary_to_keep", _REAL_PRIMARY_TO_KEEP)
    monkeypatch.setattr(cb.subprocess, "run", wl_paste_hangs)
    paste_with_primary(keeper, board, primary, "dictated words")
    paste_with_primary(keeper, board, primary, "more words")
    run_pending(keeper)
    assert primary["writes"] == []


def test_the_primary_comes_back_even_when_the_clipboard_cannot(keeper, board, primary,
                                                                 monkeypatch):
    monkeypatch.setattr(cb, "_read_clipboard", lambda: cb._NOTHING)
    paste_with_primary(keeper, board, primary, "dictated words")
    run_pending(keeper)
    assert primary["text"] == "my highlighted words"
    assert board.restores == []


def test_a_paste_that_leaves_the_primary_alone_does_not_touch_it(keeper, board, primary):
    paste(keeper, board, "dictated words")
    run_pending(keeper)
    assert primary["writes"] == []


def test_the_unknown_window_paste_saves_the_primary_before_setting_it(monkeypatch):
    """The dictation goes on the primary selection too when the window is
    unknown; what the user had highlighted is kept first, or it's lost."""
    import types
    done = []

    class Keeper:
        def before_paste(self):
            done.append("keeps the clipboard")

        def before_primary(self):
            done.append("keeps the primary")

        def after_paste(self, text):
            done.append("puts them back")

    wl_copy = types.SimpleNamespace(stdin=types.SimpleNamespace(write=len, close=lambda: None),
                                    wait=lambda timeout: 0, poll=lambda: 0, stderr=None)
    monkeypatch.setattr(keyboard_output, "is_wayland_session", lambda: True)
    monkeypatch.setattr(keyboard_output, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(live, "restore_clipboard", True)
    monkeypatch.setattr(keyboard_output, "_clipboard_keeper", Keeper())
    monkeypatch.setattr(keyboard_output.subprocess, "Popen", lambda *a, **k: wl_copy)
    monkeypatch.setattr(cb, "_sensitive_supported", lambda: False)     # it would run the fake
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: None)
    monkeypatch.setattr(keyboard_output, "copy_to_primary", lambda text: done.append("sets the primary") or True)
    monkeypatch.setattr(keyboard_output, "send_paste_keystroke", lambda cls, primary: done.append("pastes") or True)
    assert keyboard_output.paste_text("dictated words") is True
    assert done == ["keeps the clipboard", "keeps the primary", "sets the primary", "pastes",
                    "puts them back"]


# --- marking dictations sensitive --------------------------------------------------

def test_dictations_are_marked_sensitive_when_wl_copy_can(monkeypatch):
    monkeypatch.setattr(cb, "_wl_copy_help", lambda: "  --sensitive  Hint that the content is sensitive.")
    cb._sensitive_supported.cache_clear()
    assert cb.wl_copy_command() == ["wl-copy", "--sensitive"]


def test_an_older_wl_copy_still_pastes_without_the_hint(monkeypatch):
    monkeypatch.setattr(cb, "_wl_copy_help", lambda: "  -t, --type mime/type  Override the MIME type")
    cb._sensitive_supported.cache_clear()
    assert cb.wl_copy_command() == ["wl-copy"]


# --- the setting -----------------------------------------------------------------------

def test_restoring_is_off_unless_asked_for():
    """A dictation that landed in the wrong window can be re-pasted from the
    clipboard; restoring by default would take that away."""
    assert C.Settings().restore_clipboard is False


def test_the_setting_applies_without_a_restart():
    assert "restore_clipboard" in C.LIVE_APPLIED_KEYS
    assert not C.needs_service_restart({"restore_clipboard"})


def test_paste_uses_the_keeper_only_when_the_setting_is_on(monkeypatch):
    events = []

    class Keeper:
        def before_paste(self): events.append("save")
        def after_paste(self, text): events.append(("restore", text))

    class Pipe:
        def write(self, data): pass
        def close(self): pass

    class Proc:
        returncode = 0
        stdin = Pipe()
        stderr = None
        def poll(self): return 0
        def communicate(self, *a, **k): return b"", b""
        def wait(self, *a, **k): return 0
        def terminate(self): pass
        def kill(self): pass

    commands = []
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(keyboard_output, "which", lambda name: True)
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "kwrite")
    monkeypatch.setattr(keyboard_output.subprocess, "Popen", lambda cmd, **kw: commands.append(cmd) or Proc())
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(cb, "wl_copy_command", lambda: ["wl-copy", "--sensitive"])
    monkeypatch.setattr(keyboard_output, "_clipboard_keeper", Keeper())

    monkeypatch.setattr(live, "restore_clipboard", False)
    assert keyboard_output.paste_text("hello") is True
    assert events == [] and commands[-1] == ["wl-copy", "--sensitive"]

    monkeypatch.setattr(live, "restore_clipboard", True)
    assert keyboard_output.paste_text("hello") is True
    assert events == ["save", ("restore", "hello")]


# --- a copied password stays a password ---------------------------------------------------

def test_a_password_comes_back_marked_for_clipboard_managers_to_skip(keeper, board):
    """Restored as plain text, Klipper would save it in its history."""
    board.data, board.password = b"hunter2", True
    paste(keeper, board, "dictated words")
    board.password = False
    run_pending(keeper)
    assert board.restores == [(b"hunter2", "text/plain;charset=utf-8", "sensitive")]


def test_the_sensitive_restore_uses_wl_copy_sensitive(monkeypatch):
    runs = []
    monkeypatch.setattr(cb, "_sensitive_supported", lambda: True)
    monkeypatch.setattr(cb.subprocess, "run", lambda cmd, **kw: runs.append(cmd))
    cb._wl_copy_bytes(b"hunter2", "text/plain", sensitive=True)
    cb._wl_copy_bytes(b"words", "text/plain")
    assert runs == [["wl-copy", "--sensitive", "--type", "text/plain"],
                    ["wl-copy", "--type", "text/plain"]]


def test_a_paste_waits_for_a_restore_that_is_under_way(keeper, board, monkeypatch):
    """The restore holds the lock while it reads and writes the clipboard, so
    a new paste can't start in between and have its dictation overwritten."""
    held = []
    write = board.write
    monkeypatch.setattr(cb, "_wl_copy_bytes",
                        lambda *a, **k: held.append(keeper._lock.locked()) or write(*a, **k))
    paste(keeper, board, "dictated words")
    run_pending(keeper)
    assert held == [True] and board.data == b"what I copied"


def test_an_x11_copy_that_timed_out_never_lands_later(monkeypatch):
    """After the wait gives up, nothing was pasted; the copy still queued
    on a stalled GTK loop must not put that dictation on the clipboard
    when the loop catches up."""
    import threading
    from gi.repository import GLib
    from tuxtalk import gtk_loop
    copied = []
    monkeypatch.setattr(cb, "_offer_sensitive_x11", lambda text: copied.append(text) or True)
    monkeypatch.setattr(gtk_loop, "_thread", threading.Thread())   # running, stalled
    assert cb.offer_dictation_x11("never pasted", timeout=0.05) is False
    context = GLib.MainContext.default()
    while context.iteration(False):
        pass
    assert copied == []


@pytest.mark.parametrize("said", [b"No selection\n",            # wl-clipboard 2.0, 2.1
                                  b"Nothing is copied\n"])      # 2.2 on
def test_nothing_highlighted_is_empty_in_every_wl_paste(monkeypatch, said):
    """Ubuntu 22.04 and Debian 12 ship a wl-paste that says it differently;
    read as "couldn't read", the dictation stayed highlighted after the paste."""
    import types
    monkeypatch.setattr(cb.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=1, stdout=b"",
                                                                stderr=said))
    assert _REAL_PRIMARY_TO_KEEP() is None


# Another process reading the clipboard, as the app a dictation is pasted into.
_READ_CLIPBOARD = """
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk
board = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
hint = board.wait_for_contents(Gdk.Atom.intern("x-kde-passwordManagerHint", False))
print(repr((board.wait_for_text(), hint and hint.get_data())))
"""


def test_an_app_pastes_an_x11_dictation_marked_for_managers_to_skip(monkeypatch):
    """A dictation pasted on X11 reaches the app that pastes it, another
    process, with the hint clipboard managers skip, and is never handed to
    the clipboard manager to keep."""
    from gi.repository import Gtk
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    monkeypatch.setattr(Gtk.Clipboard, "store", lambda self: pytest.fail("handed to the manager"))
    monkeypatch.setattr(cb, "_x11_owner", None)
    reader = None
    try:
        assert cb._offer_sensitive_x11("Привіт, café") is True
        reader = subprocess.Popen(
            [sys.executable, "-c", _READ_CLIPBOARD], stdout=subprocess.PIPE,
            # X11: GTK would otherwise find the user's own Wayland desktop
            # (conftest) and read its clipboard.
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "GDK_BACKEND": "x11"},
            encoding="utf-8")
        # The paste request comes through the X server: answer it as the
        # service's GTK loop would.
        deadline = time.monotonic() + 10
        while reader.poll() is None and time.monotonic() < deadline:
            Gtk.main_iteration_do(False)
            time.sleep(0.005)
        said = reader.communicate(timeout=1)[0]
    finally:
        if reader is not None and reader.poll() is None:
            reader.kill()
            reader.wait()
        if cb._x11_owner is not None:
            cb._x11_owner.destroy()
    assert said.strip() == repr(("Привіт, café", b"secret"))


def test_no_x11_offer_without_the_loop_that_answers_pastes(monkeypatch):
    """With GTK's loop not running nothing would answer the app's paste:
    reported as not pasted rather than typed as nothing."""
    from tuxtalk import gtk_loop
    monkeypatch.setattr(gtk_loop, "running", lambda: False)
    monkeypatch.setattr(cb, "_offer_sensitive_x11", lambda text: pytest.fail("offered"))
    assert cb.offer_dictation_x11("hello") is False


def test_quitting_right_after_an_x11_paste_waits_for_the_app_to_read_it(monkeypatch):
    """Only the service holds an X11 offer: gone at once, the app found
    nothing to paste."""
    slept = []
    monkeypatch.setattr(cb.time, "sleep", slept.append)
    monkeypatch.setattr(cb, "_x11_offered_at", cb.time.monotonic())
    cb.wait_for_x11_paste()
    assert slept and 0.4 < slept[0] <= cb.RESTORE_DELAY_S
    slept.clear()
    monkeypatch.setattr(cb, "_x11_offered_at", cb.time.monotonic() - 5)
    cb.wait_for_x11_paste()
    assert slept == []


def test_a_clipboard_type_name_in_another_encoding_does_not_stop_the_restore(
        tmp_path, monkeypatch):
    """wl-paste --list-types and wl-copy --help are read as text: an app that
    offers a type with an odd byte in its name mustn't make the clipboard
    restore or the voice-edit selection probe fail."""
    from shell_stubs import stub
    stub(tmp_path, "wl-paste", r"printf 'text/plain\napplication/x-caf\351\n'")
    stub(tmp_path, "wl-copy", r"printf 'Usage: wl-copy [--sensitive] caf\351\n'")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert "text/plain" in cb._wl_paste_types()
    assert "--sensitive" in cb._wl_copy_help()


# --- Restoring on X11 -----------------------------------------------------------------
# The service owns the X11 clipboard through GTK while it offers a dictation;
# X11Board reads the user's copy before and offers it again after. These run
# on xvfb-run's display (conftest), with other processes as the apps.

def test_an_x11_paste_keeps_the_clipboard_only_when_the_setting_is_on(monkeypatch):
    """Off by default, as on Wayland; on, the user's copy is saved before the
    dictation is offered and put back after, even when the paste failed."""
    events = []

    class Keeper:
        def __init__(self, board):
            events.append(("made for", board))

        def before_paste(self):
            events.append("save")

        def after_paste(self, text):
            events.append(("restore", text))
    monkeypatch.setattr(cb, "ClipboardKeeper", Keeper)
    monkeypatch.setattr(keyboard_output, "_clipboard_keeper", None)
    monkeypatch.setattr(cb, "offer_dictation_x11",
                        lambda text: events.append(("offer", text)) or True)
    monkeypatch.setattr(keyboard_output, "send_paste_keystroke", lambda *a: True)
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "xed")
    monkeypatch.setattr("time.sleep", lambda s: None)

    monkeypatch.setattr(live, "restore_clipboard", False)
    assert keyboard_output.paste_text_x11("hello") is True
    assert events == [("offer", "hello")]

    events.clear()
    monkeypatch.setattr(live, "restore_clipboard", True)
    assert keyboard_output.paste_text_x11("hello") is True
    monkeypatch.setattr(cb, "offer_dictation_x11", lambda text: False)
    assert keyboard_output.paste_text_x11("again") is False
    assert events == [("made for", cb.X11Board), "save", ("offer", "hello"),
                      ("restore", "hello"), "save", ("restore", "again")]


def test_something_copied_after_an_x11_paste_is_not_replaced(monkeypatch):
    """Another app owning the clipboard now: the dictation is no longer what
    it holds, so the restore leaves it alone."""
    monkeypatch.setattr(cb, "_x11_offered", "dictated words")
    monkeypatch.setattr(cb, "_x11_offered_data", None)
    assert cb.X11Board.holds("dictated words") is True
    cb._x11_paste_taken_over(None, None)
    assert cb.X11Board.holds("dictated words") is False


# An app that has copied argv[1] and keeps it on offer until its input closes.
_COPY_AND_STAY = """
import sys
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk
Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(sys.argv[1], -1)
GLib.io_add_watch(sys.stdin, GLib.IO_IN | GLib.IO_HUP, lambda *a: Gtk.main_quit())
GLib.timeout_add_seconds(20, Gtk.main_quit)
GLib.idle_add(lambda: print("ready", flush=True))
Gtk.main()
"""
_X11_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "GDK_BACKEND": "x11"}


def _pump_until(done, seconds=10):
    """Run this process's GTK loop, which answers the clipboard as the
    service's would, until done() or *seconds*."""
    from gi.repository import Gtk
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        Gtk.main_iteration_do(False)
        time.sleep(0.005)


def _read_in_another_app():
    """(text, password hint) as an app pasting now reads them."""
    reader = subprocess.Popen([sys.executable, "-c", _READ_CLIPBOARD], stdout=subprocess.PIPE,
                              env=_X11_ENV, encoding="utf-8")
    try:
        _pump_until(lambda: reader.poll() is not None)
        return reader.communicate(timeout=1)[0].strip()
    finally:
        if reader.poll() is None:
            reader.kill()
            reader.wait()


def _paste_on_x11_keeping(*texts):
    """Paste each of *texts* as the service does with restore_clipboard on,
    one right after the other, its GTK work queued to this thread's loop,
    and then let the restore run."""
    import threading
    from tuxtalk import gtk_loop
    pending = []
    keeper = cb.ClipboardKeeper(
        cb.X11Board, schedule=lambda delay, fn: pending.append(fn) or (lambda: pending.remove(fn)))
    offered = []

    def paste():
        for text in texts:
            keeper.before_paste()
            offered.append(cb.offer_dictation_x11(text))
            keeper.after_paste(text)
    gtk_loop._thread = threading.Thread()       # the loop is "elsewhere": this one
    worker = threading.Thread(target=paste)
    worker.start()
    _pump_until(lambda: not worker.is_alive())
    assert offered == [True] * len(texts)
    restore = threading.Thread(target=lambda: [fn() for fn in pending])
    restore.start()
    _pump_until(lambda: not restore.is_alive())


@pytest.fixture
def x11_board(monkeypatch):
    """The service's X11 clipboard state, fresh, and the display's clipboard
    ownerless to start with."""
    from gi.repository import Gdk, Gtk
    from tuxtalk import gtk_loop
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    for name, value in (("_x11_owner", None), ("_x11_offered", ""),
                        ("_x11_offered_data", None), ("_x11_offered_at", float("-inf"))):
        monkeypatch.setattr(cb, name, value)
    monkeypatch.setattr(gtk_loop, "_thread", None)
    Gtk.selection_owner_set(None, Gdk.SELECTION_CLIPBOARD, Gdk.CURRENT_TIME)
    yield
    if cb._x11_owner is not None:
        cb._x11_owner.destroy()


@pytest.mark.parametrize("dictations", [["dictated words"], ["first", "second"]],
                         ids=["one dictation", "two before the restore"])
def test_on_x11_what_another_app_copied_comes_back_after_the_paste(x11_board, dictations):
    """Two dictations in quick succession bring back what the user copied,
    not the first dictation."""
    owner = subprocess.Popen([sys.executable, "-c", _COPY_AND_STAY, "Grüße, what I copied"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=_X11_ENV,
                             encoding="utf-8")
    try:
        assert owner.stdout.readline().strip() == "ready"
        _paste_on_x11_keeping(*dictations)
    finally:
        owner.stdin.close()
        owner.wait(timeout=10)
    # Offered by the service now, as the app that copied it has gone, and
    # not marked as a password: it wasn't one.
    assert _read_in_another_app() == repr(("Grüße, what I copied", None))


def test_on_x11_an_empty_clipboard_is_empty_again_after_the_paste(x11_board):
    _paste_on_x11_keeping("dictated words")
    assert _read_in_another_app() == repr((None, None))


def test_on_x11_a_copied_image_comes_back_as_an_image_and_a_password_marked(x11_board):
    """What isn't text is offered again in its own type; a password manager's
    copy is marked again for clipboard managers to skip."""
    from gi.repository import Gdk, Gtk
    board = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    assert cb._put_back_x11((b"\x89PNG not really", "image/png", False))
    got = []
    board.request_contents(Gdk.Atom.intern("image/png", False),
                           lambda _board, data: got.append(data.get_data()))
    _pump_until(lambda: got)
    assert got == [b"\x89PNG not really"]
    assert cb._put_back_x11((b"hunter2", "UTF8_STRING", True))
    assert _read_in_another_app() == repr(("hunter2", b"secret"))


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


def test_on_x11_files_copied_in_a_file_manager_come_back_as_files(x11_board):
    """Nemo and Caja offer copied files as their paths in text as well;
    restored as that text, pasting in the file manager would paste no files."""
    import json
    from gi.repository import Gdk, Gtk
    uris = "file:///home/me/photo.jpg"
    offer = {"x-special/gnome-copied-files": "copy\n" + uris, "text/uri-list": uris,
             "UTF8_STRING": "/home/me/photo.jpg"}
    owner = subprocess.Popen([sys.executable, "-c", _OFFER_AND_STAY, json.dumps(offer)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=_X11_ENV,
                             encoding="utf-8")
    try:
        assert owner.stdout.readline().strip() == "ready"
        _paste_on_x11_keeping("dictated words")
    finally:
        owner.stdin.close()
        owner.wait(timeout=10)
    got = []
    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).request_contents(
        Gdk.Atom.intern("text/uri-list", False), lambda _board, data: got.append(data.get_data()))
    _pump_until(lambda: got)
    assert got == [uris.encode()]
