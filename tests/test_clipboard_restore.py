"""Paste mode and the clipboard (GitHub issue #7).

Dictations are always marked sensitive, so clipboard managers such as Klipper
don't save them to their history, and with "Restore clipboard after pasting"
on, whatever the user had copied comes back after the paste.
"""
import pytest

from tuxtalk import clipboard as cb
from tuxtalk import config as C
from tuxtalk import live_settings as live
from tuxtalk import keyboard_output


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
    monkeypatch.setattr(cb, "_read_primary", lambda: sel["text"])
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
    from tuxtalk import app
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
    monkeypatch.setattr(app.time, "sleep", lambda s: None)
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
