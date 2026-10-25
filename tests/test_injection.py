"""Tests for text injection reporting its real outcome.

Injection is the last step of every dictation. When it silently claims success
it causes the two worst failure modes this app has:

  * the wrong text lands in the document (a stale clipboard gets pasted)
  * the undo buffer records text the document never received, so a later
    "undo that" backspaces over the user's own writing

Every path here must return whether text actually reached the focused app.
"""

import subprocess

import pytest

from tuxtalk import focused_window
from tuxtalk import delivery
from tuxtalk import voice_commands
from tuxtalk import keyboard_output
from tuxtalk import recording_state as rs


class FakeCompleted:
    """Stands in for subprocess.CompletedProcess."""

    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = stderr


class FakePopen:
    """Stands in for a wl-copy process.

    wl-copy forks by default: the process we spawn exits 0 once the clipboard
    is set, and non-zero when it cannot reach the compositor. returncode=None
    is one that never gets that far.
    """

    def __init__(self, returncode=None):
        self._returncode = returncode
        self.stdin = _FakeStdin()
        self.stderr = _FakeStderr()
        self.terminated = False

    def poll(self):
        return self._returncode

    def terminate(self):
        self.terminated = True
        if self._returncode is None:
            self._returncode = -15

    def wait(self, timeout=None):
        if self._returncode is None:
            raise subprocess.TimeoutExpired(cmd="wl-copy", timeout=timeout)
        return self._returncode


class _FakeStderr:
    def read(self):
        return b"Failed to connect to a Wayland server"

    def close(self):
        pass


class _FakeStdin:
    def __init__(self):
        self.written = b""

    def write(self, data):
        self.written += data

    def close(self):
        pass


@pytest.fixture
def wayland(monkeypatch):
    """A working Wayland session with both tools present."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(keyboard_output, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: None)
    monkeypatch.setattr("time.sleep", lambda s: None)


# --- the paste path must report its real outcome ---------------------------

@pytest.fixture
def no_keyboard(monkeypatch):
    """TuxTalk couldn't make its virtual keyboard (no access to /dev/uinput)."""
    from tuxtalk import virtual_keyboard
    monkeypatch.setattr(virtual_keyboard, "_open", lambda: None)


def test_paste_reports_failure_without_a_keyboard_to_press_ctrl_v(monkeypatch, wayland, no_keyboard):
    """Reporting a paste that never happened as success would make the undo
    buffer record text the document never got."""
    monkeypatch.setattr(keyboard_output.subprocess, "Popen", lambda *a, **k: FakePopen(returncode=0))
    monkeypatch.setattr(keyboard_output.subprocess, "run", lambda *a, **k: FakeCompleted(returncode=0))

    assert keyboard_output.paste_text("hello") is False


def test_paste_reports_failure_when_the_clipboard_tool_dies(monkeypatch, wayland):
    """If wl-copy can't reach the compositor the clipboard was never set —
    pressing Ctrl+V then pastes whatever the user had copied earlier."""
    monkeypatch.setattr(keyboard_output.subprocess, "Popen", lambda *a, **k: FakePopen(returncode=1))
    monkeypatch.setattr(keyboard_output.subprocess, "run", lambda *a, **k: FakeCompleted(returncode=0))

    assert keyboard_output.paste_text("hello") is False


def test_paste_reports_success_when_the_clipboard_is_set_and_ctrl_v_sent(monkeypatch, wayland, keyboard):
    monkeypatch.setattr(keyboard_output.subprocess, "Popen", lambda *a, **k: FakePopen(returncode=0))
    monkeypatch.setattr(keyboard_output.subprocess, "run", lambda *a, **k: FakeCompleted(returncode=0))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: "org.kde.kwrite")

    assert keyboard_output.paste_text("hello") is True
    assert keyboard.events == [(29, 1), (47, 1), (47, 0), (29, 0)]     # Ctrl+V


def test_paste_waits_for_wl_copy_to_set_the_clipboard(monkeypatch, wayland, keyboard):
    """The wl-copy we start exits once the clipboard is set. One still running
    has not got that far, so Ctrl+V would paste the old clipboard contents,
    and killing it afterwards would stop it before it could serve the new one."""
    proc = FakePopen(returncode=None)
    monkeypatch.setattr(keyboard_output.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(keyboard_output.subprocess, "run", lambda *a, **k: FakeCompleted(returncode=0))

    assert keyboard_output.paste_text("hello") is False
    assert not keyboard.events, "pasted before the clipboard held the dictation"
    assert proc.terminated, "a stuck wl-copy was left running"


def test_a_wl_copy_that_handed_off_is_not_killed(monkeypatch, wayland):
    proc = FakePopen(returncode=0)
    monkeypatch.setattr(keyboard_output.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(keyboard_output.subprocess, "run", lambda *a, **k: FakeCompleted(returncode=0))

    assert keyboard_output.paste_text("hello") is True
    assert not proc.terminated


def test_paste_is_refused_outside_a_wayland_session(monkeypatch, keyboard):
    """On an X11 login the clipboard tool fails instantly, and sending Ctrl+V
    anyway would paste the user's previous clipboard contents into their document."""
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(keyboard_output, "which", lambda name: f"/usr/bin/{name}")

    assert keyboard_output.paste_text("hello") is False
    assert not keyboard.events, "sent a paste keystroke on a session where the clipboard was never set"


# --- backspaces must report whether they landed ----------------------------

def test_send_backspaces_reports_failure_without_a_keyboard(wayland, no_keyboard):
    assert keyboard_output.send_backspaces(5) is False


def test_send_backspaces_presses_backspace_that_many_times(wayland, keyboard):
    assert keyboard_output.send_backspaces(5) is True
    assert keyboard.typed_keys() == [14] * 5


# --- partial injection must not re-send what already landed ----------------

def test_failed_chunk_only_retypes_the_remainder(monkeypatch, wayland):
    """A multi-line dictation whose third chunk fails must not fall back to
    re-injecting the *entire* text, which leaves the document with the first
    chunks followed by a complete second copy of everything."""
    typed = []
    pasted = []

    def fake_paste(part, *args, **kwargs):
        pasted.append(part)
        return part != "third"  # third chunk fails

    monkeypatch.setattr(keyboard_output, "paste_text", fake_paste)
    monkeypatch.setattr(keyboard_output, "type_text", lambda t: typed.append(t) or True)
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: True)
    monkeypatch.setattr(delivery, "determine_injection_method", lambda m: ("paste", "test"))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: None)

    marker = "\xa7SHIFT_ENTER\xa7"
    text = marker.join(["first", "second", "third", "fourth"])
    delivery.inject_text(text, "paste")

    assert typed, "nothing was retyped after the failure"
    retyped = "".join(typed)
    assert "first" not in retyped, f"re-injected a chunk that already landed: {retyped!r}"
    assert "second" not in retyped, f"re-injected a chunk that already landed: {retyped!r}"
    assert "third" in retyped, "the failed chunk was dropped"
    assert "fourth" in retyped, "the remaining chunk was dropped"


def test_undo_buffer_records_only_what_actually_landed(monkeypatch, wayland):
    """The buffer must match the document, or the next undo deletes the user's
    own writing. A failed typing run may have typed part of the text before it
    stopped, so neither this dictation nor the previous one is a safe length
    to delete: the buffer is emptied, and undo then deletes nothing."""
    monkeypatch.setattr(keyboard_output, "paste_text", lambda *a, **k: False)
    monkeypatch.setattr(keyboard_output, "type_text", lambda t: False)
    monkeypatch.setattr(delivery, "determine_injection_method", lambda m: ("paste", "test"))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: None)
    rs.state.last_inserted_text = "previous dictation"

    delivery.inject_text("brand new text", "paste")

    assert rs.state.last_inserted_text == "", \
        "undo buffer kept a length that no longer matches the document"


def test_undo_buffer_updates_on_a_successful_injection(monkeypatch, wayland):
    monkeypatch.setattr(keyboard_output, "paste_text", lambda *a, **k: True)
    monkeypatch.setattr(delivery, "determine_injection_method", lambda m: ("paste", "test"))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: None)
    rs.state.last_inserted_text = "previous"

    delivery.inject_text("brand new text", "paste")

    assert rs.state.last_inserted_text == "brand new text"


# --- undo must confirm the deletion before shrinking its buffer ------------

@pytest.fixture
def undo_env(monkeypatch):
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda *a, **k: None)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: None)
    rs.state.last_inserted_text = "hello world"
    rs.state.continue_mid_sentence = False
    yield
    rs.state.last_inserted_text = ""


def test_undo_leaves_its_buffer_alone_when_the_backspaces_fail(monkeypatch, undo_env):
    """Otherwise TuxTalk reports a successful undo, nothing is deleted, and the
    next undo deletes that many characters of the user's own writing."""
    monkeypatch.setattr(keyboard_output, "send_backspaces", lambda count: False)

    voice_commands.handle_undo("undo last word", beeps_on=False, notify_on=False)

    assert rs.state.last_inserted_text == "hello world"


def test_undo_shrinks_its_buffer_when_the_backspaces_land(monkeypatch, undo_env):
    monkeypatch.setattr(keyboard_output, "send_backspaces", lambda count: True)

    voice_commands.handle_undo("undo last word", beeps_on=False, notify_on=False)

    assert rs.state.last_inserted_text == "hello "


# --- line breaks are part of the text, and part of the report ---------------

def test_type_text_reports_failure_when_the_shift_enter_key_fails(monkeypatch):
    """A dictation containing "new line" is typed in parts with Shift+Enter
    between them, so _type_text must check what the key returns as well as
    the text parts. Otherwise the undo buffer counts a line break the
    document never got, and "undo that" backspaces into the user's own text."""
    monkeypatch.setattr(keyboard_output, "type_text_raw", lambda t: True)
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: False)

    assert keyboard_output.type_text("hello§SHIFT_ENTER§world") is False


def test_type_text_reports_success_when_every_part_lands(monkeypatch):
    monkeypatch.setattr(keyboard_output, "type_text_raw", lambda t: True)
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: True)

    assert keyboard_output.type_text("hello§SHIFT_ENTER§world") is True


def test_type_text_still_reports_failure_when_a_text_part_fails(monkeypatch):
    monkeypatch.setattr(keyboard_output, "type_text_raw", lambda t: False)
    monkeypatch.setattr(keyboard_output, "send_shift_enter", lambda: True)

    assert keyboard_output.type_text("hello§SHIFT_ENTER§world") is False


def test_injection_method_forces_type_in_flatpak(monkeypatch):
    """Inside a Flatpak there is no wl-copy, and libei types directly — so the
    method must be 'type', never 'paste', regardless of the configured mode."""
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
    for mode in ("auto", "paste", "type"):
        actual, _reason = delivery.determine_injection_method(mode)
        assert actual == "type", mode


def test_injection_method_respects_user_choice_on_host(monkeypatch):
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert delivery.determine_injection_method("paste")[0] == "paste"
    assert delivery.determine_injection_method("type")[0] == "type"


def test_auto_mode_pastes_without_scanning_processes(monkeypatch):
    """Auto always pastes, so the answer must not cost a pgrep over every
    process on each dictation."""
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")

    def no_subprocess(*a, **k):
        raise AssertionError("auto mode must not start a process")
    monkeypatch.setattr(keyboard_output.subprocess, "run", no_subprocess)

    assert delivery.determine_injection_method("auto")[0] == "paste"


# --- a typed line break must never run a terminal command --------------------
# Mint (X11) test, 2026-10-13: gnome-terminal treats Shift+Enter as Enter, so
# "first line new line second line" ran "First line." as a command.

MARK = "\xa7SHIFT_ENTER\xa7"


def _typing_into(monkeypatch, window_class, mode="type"):
    typed = []
    monkeypatch.setattr(keyboard_output, "type_text", lambda t: typed.append(t) or True)
    monkeypatch.setattr(delivery, "determine_injection_method", lambda m: (mode, "test"))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: window_class)
    monkeypatch.setattr(focused_window, "look", lambda: (window_class, None))
    return typed


@pytest.mark.parametrize("window", ["Gnome-terminal", "org.kde.konsole", "xfce4-terminal"])
def test_typed_line_breaks_become_spaces_in_a_terminal(monkeypatch, window):
    typed = _typing_into(monkeypatch, window)
    assert delivery.inject_text(f"First line.{MARK}Second line.", "type") == "ok"
    assert typed == ["First line. Second line."]
    assert rs.state.last_inserted_text == "First line. Second line."


def test_a_snippet_newline_becomes_a_space_in_a_terminal(monkeypatch):
    typed = _typing_into(monkeypatch, "Gnome-terminal")
    delivery.inject_text("Best,\nRon", "type")
    assert typed == ["Best, Ron"]


def test_typed_line_breaks_stay_line_breaks_in_other_apps(monkeypatch):
    typed = _typing_into(monkeypatch, "xed")
    delivery.inject_text(f"First line.{MARK}Second line.", "type")
    assert typed == [f"First line.{MARK}Second line."]


def test_the_typed_remainder_after_a_failed_paste_has_no_breaks_in_a_terminal(monkeypatch, wayland):
    typed = _typing_into(monkeypatch, "org.kde.konsole", mode="paste")
    monkeypatch.setattr(keyboard_output, "paste_text", lambda *a, **k: False)
    delivery.inject_text(f"First line.{MARK}Second line.", "paste")
    assert typed and all(MARK not in t and "\n" not in t for t in typed)


# --- the remembered window is the one the text landed in -------------------

def _focus_moves_on_paste(monkeypatch, before, after):
    """The focus report says *before* until the paste lands, then *after*.
    On KDE, clicking the desktop reports plasmashell, then wl-copy's brief
    clipboard window hands the focus back to the last real window, so the
    text lands there (Ron, 2026-10-15)."""
    focus = {"now": before}

    def fake_paste(*a, **k):
        focus["now"] = after
        return True

    monkeypatch.setattr(keyboard_output, "paste_text", fake_paste)
    monkeypatch.setattr(delivery, "determine_injection_method", lambda m: ("paste", "test"))
    monkeypatch.setattr("tuxtalk.focused_window.window_class", lambda: focus["now"])
    monkeypatch.setattr(focused_window, "look", lambda: (focus["now"], None))


def test_the_window_the_text_landed_in_is_remembered(monkeypatch, wayland):
    """Ron clicked the desktop, dictated, and the text landed in Claude. Undo
    then refused to delete it there, saying it went to plasmashell."""
    _focus_moves_on_paste(monkeypatch, "plasmashell", "com.anthropic.Claude")
    assert delivery.inject_text("Testing 1, 2, 3. ", "paste") == "ok"
    assert rs.state.last_inserted_window == "com.anthropic.Claude"


def test_an_unknown_window_after_typing_keeps_the_one_before(monkeypatch, wayland):
    _focus_moves_on_paste(monkeypatch, "org.kde.kwrite", None)
    delivery.inject_text("Hello. ", "paste")
    assert rs.state.last_inserted_window == "org.kde.kwrite"


@pytest.mark.parametrize("mode", ["type", "auto"])
def test_a_tab_said_mid_sentence_is_four_spaces_in_claude_desktop_however_it_types(
        monkeypatch, mode):
    """With Keystroke Typing too: a real Tab there jumps to the Send button,
    and the next space typed sends the message halfway through."""
    typed = _typing_into(monkeypatch, "com.anthropic.Claude", mode="type")
    monkeypatch.setattr(keyboard_output, "type_text_raw", lambda t: typed.append(t) or True)
    assert delivery.inject_text("Name\taddress. ", mode) == "ok"
    assert typed == ["Name    address. "]
    assert rs.state.last_inserted_text == "Name    address. "
