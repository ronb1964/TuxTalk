"""Paste mode and the clipboard (GitHub issue #7).

Dictations are always marked sensitive, so clipboard managers such as Klipper
don't save them to their history, and with "Restore clipboard after pasting"
on, whatever the user had copied comes back after the paste.
"""
import subprocess

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


def test_a_dictation_pasted_on_x11_is_marked_for_clipboard_managers_to_skip(monkeypatch):
    """On X11 the text went on the clipboard as plain text and was handed to
    the clipboard manager to store, so Klipper and the like kept every
    dictation pasted this way; Wayland's copy was already marked sensitive."""
    from gi.repository import Gdk, Gtk
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    monkeypatch.setattr(Gtk.Clipboard, "store", lambda self: pytest.fail("handed to the manager"))
    assert cb._offer_sensitive_x11("Привіт, café") is True
    board = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    found, targets = board.wait_for_targets()
    assert found and cb._PASSWORD_HINT in [t.name() for t in targets]
    assert board.wait_for_text() == "Привіт, café"
    hint = board.wait_for_contents(Gdk.Atom.intern(cb._PASSWORD_HINT, False))
    assert hint.get_data() == b"secret"
