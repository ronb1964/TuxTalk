"""Report a Problem: one place that gathers what helps find a problem, shown
in full before the person shares it anywhere. TuxTalk sends nothing itself.
"""
import ast
import os
import pathlib
import stat
import threading
import time
import urllib.parse

import pytest
from gi.repository import GLib, Gtk

from tuxtalk import help_dialog, problem_report, problem_report_window
from tuxtalk.config import Settings

HOME = "/home/archer"


@pytest.fixture
def report(monkeypatch, tmp_path):
    """build() on a made-up computer: settings, a log, and facts that answer
    without asking the real system."""
    from tuxtalk import config, desktop_detect, keyboard_layout, recommend, secure_input
    from tuxtalk import installation, uinput_helper
    monkeypatch.setattr(config, "load_config", lambda: Settings(model="parakeet-v3"))
    monkeypatch.setenv("TUXTALK_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "LXQt")
    monkeypatch.setattr(problem_report, "os_name", lambda: "Fedora Linux 44 (LXQt)")
    monkeypatch.setattr(desktop_detect, "get_desktop_environment", lambda: "unknown")
    monkeypatch.setattr(keyboard_layout, "active_layout", lambda: ("us", "", ""))
    monkeypatch.setattr(recommend, "detect_hardware",
                        lambda cfg: recommend.Hardware("Intel graphics"))
    monkeypatch.setattr(secure_input, "is_installed", lambda: True)
    monkeypatch.setattr(secure_input, "running_version", lambda: "1.4")
    monkeypatch.setattr(uinput_helper, "check_uinput_exists", lambda: True)
    monkeypatch.setattr(uinput_helper, "typing_blocked_reason", lambda: None)
    monkeypatch.setattr(installation, "get_install_type", lambda: "appimage")
    log = tmp_path / "tuxtalk.log"
    log.write_text("".join(f"line {n} {HOME}/AppImages/TuxTalk.AppImage\n" for n in range(400)))
    return lambda user="archer": problem_report.build(
        problem_report.fact(problem_report.layout_text), home=HOME, user=user)


def test_the_report_says_what_helps_find_a_problem(report):
    text = report().text
    for wanted in ("Version: ", "Installed as: appimage", "Hotkey helper: running, version 1.4",
                   "Typing: can type", "System: Fedora Linux 44 (LXQt)", "LXQt", "x11",
                   "Keyboard layout: us", "Graphics: Intel graphics", "model = 'parakeet-v3'"):
        assert wanted in text


def test_the_report_names_the_keyboard_options(report, monkeypatch):
    """They decide what Right Alt does, so a typing problem can hang on them."""
    from tuxtalk import keyboard_layout
    monkeypatch.setattr(keyboard_layout, "active_layout",
                        lambda: ("de", "nodeadkeys", "compose:ralt"))
    assert "Keyboard layout: de (nodeadkeys), options compose:ralt" in report().text


def test_it_ends_with_the_last_lines_of_the_log(report):
    text = report().text
    assert f"line {400 - problem_report.LOG_LINES} " in text
    assert f"line {399 - problem_report.LOG_LINES} " not in text


def test_the_home_folder_and_user_name_are_taken_out(report):
    text = report().text
    assert HOME not in text and "archer" not in text
    assert "~/AppImages/TuxTalk.AppImage" in text


@pytest.mark.parametrize("text, scrubbed", [
    ("environment ron", "environment <user>"),           # not inside a word
    ("Ron's AirPods", "<user>'s AirPods"),                # a headset named after them
    ("/home/ron/x and /home/ronald/data", "~/x and /home/ronald/data"),
    ("ron_usb_input", "<user>_usb_input"),
])
def test_the_home_folder_and_user_name_go_and_nothing_else(text, scrubbed):
    assert problem_report.scrub(text, "/home/ron", "ron") == scrubbed


def test_a_fact_that_cant_be_read_doesnt_stop_the_rest(report, monkeypatch):
    from tuxtalk import keyboard_layout

    def broken():
        raise OSError("no layout")
    monkeypatch.setattr(keyboard_layout, "active_layout", broken)
    text = report().text
    assert "Keyboard layout: couldn't tell (OSError: no layout)" in text
    assert "Graphics: Intel graphics" in text


def test_no_log_yet_says_so(report, tmp_path):
    (tmp_path / "tuxtalk.log").unlink()
    assert "(no log yet)" in report().text


def test_the_bug_form_opens_with_what_tuxtalk_knows(monkeypatch):
    from tuxtalk import desktop_detect
    monkeypatch.setattr(desktop_detect, "get_desktop_environment", lambda: "kde")
    monkeypatch.setattr(problem_report, "os_name", lambda: "Nobara Linux 44")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    url = problem_report_window.issue_url()
    fields = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert url.startswith("https://github.com/ronb1964/TuxTalk/issues/new?")
    assert fields["template"] == "bug_report.yml"
    assert (fields["desktop"], fields["display"], fields["distro"]) == (
        "KDE Plasma", "Wayland", "Nobara Linux 44")


# --- the window -------------------------------------------------------------------------

@pytest.fixture
def window(monkeypatch):
    """Report windows kept off screen, whose report is made up: any one
    opened, from Help's button too, gathers it on a thread, and the real one
    asks the graphics card, the keyboard layout and the hotkey helper."""
    from tuxtalk import config
    monkeypatch.setattr(Gtk.Window, "show_all", lambda self: None)   # never on screen
    monkeypatch.setattr(config, "load_config", lambda: Settings())
    monkeypatch.setattr(problem_report, "layout_text", lambda: "us")
    real_build = problem_report.build
    monkeypatch.setattr(problem_report, "build",
                        lambda layout: problem_report.Report("the report", False))

    def make(report="the report", words_left_out=False):
        """None: the real build(), for a test whose report fixture has
        already made the computer up."""
        if report is None:
            monkeypatch.setattr(problem_report, "build", real_build)
        else:
            report = problem_report.Report(report, words_left_out)
            monkeypatch.setattr(problem_report, "build", lambda layout: report)
        win = problem_report_window.ReportWindow()
        for _ in range(200):    # the report is gathered on a thread
            GLib.MainContext.default().iteration(False)
            if win.copy_button.get_sensitive():
                break
            time.sleep(0.01)
        made.append(win)
        return win
    made = []
    yield make
    for win in made:
        win.window.destroy()
    # Before the patches go: a report still being gathered would look up
    # the real build() once they had.
    for thread in threading.enumerate():
        if thread.name == "problem-report":
            thread.join(5)
            assert not thread.is_alive()


def _labels(widget):
    if isinstance(widget, Gtk.Label):
        yield widget.get_label()
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from _labels(child)


def test_the_window_shows_the_report_before_anything_is_shared(window):
    win = window()
    assert win._text() == "the report"
    assert win.copy_button.get_sensitive() and win.issue_button.get_sensitive()


def test_it_says_when_dictated_words_were_left_out(window):
    assert not window().warning.get_visible()
    shown = window(words_left_out=True)
    assert shown.warning.get_visible()
    assert "left out of this report" in shown.warning.get_label()


def test_closing_it_before_the_report_is_in_is_fine(window):
    win = window()
    win.window.destroy()
    assert win._show_report("late") is False


def test_opening_an_issue_copies_the_report_and_opens_the_form(window, monkeypatch):
    win = window()
    copied, opened = [], []
    monkeypatch.setattr(win, "_copy", lambda: copied.append(win._text()) or True)
    monkeypatch.setattr(Gtk, "show_uri_on_window", lambda w, uri, t: opened.append(uri))
    win._on_issue(None)
    assert copied == ["the report"]
    assert opened and opened[0].startswith(problem_report_window.ISSUE_URL)
    assert "Log Output" in win.status.get_text()


def test_help_problems_opens_it(monkeypatch):
    shown, opened = [], []
    monkeypatch.setattr(Gtk.Window, "show_all", lambda self: shown.append(self))
    monkeypatch.setattr(Gtk.Window, "present", lambda self: None)
    monkeypatch.setattr(problem_report_window, "ReportWindow", lambda: opened.append(True))
    help_dialog.show_help_dialog()
    dialog = shown[0]
    try:
        def labels(widget):
            if isinstance(widget, Gtk.Label):
                yield widget
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    yield from labels(child)
        problems = next(l for l in labels(dialog) if help_dialog.REPORT_LINK in l.get_label())
        assert problems.emit("activate-link", help_dialog.REPORT_LINK) is True
        assert opened == [True]
    finally:
        dialog.destroy()


def test_the_problems_tab_opens_with_a_report_button(monkeypatch):
    """Report a Problem is a button at the top of Problems, not only a link
    near the bottom of its text, and it opens the report over Help."""
    from gtk_text import widgets
    shown, opened = [], []
    monkeypatch.setattr(Gtk.Window, "show_all", lambda self: shown.append(self))
    monkeypatch.setattr(Gtk.Window, "present", lambda self: None)
    monkeypatch.setattr(problem_report_window, "ReportWindow", lambda: opened.append(True))
    help_dialog.show_help_dialog(tab="problems")
    dialog = shown[0]
    try:
        notebook = next(w for w in widgets(dialog) if isinstance(w, Gtk.Notebook))
        page = notebook.get_nth_page(notebook.get_current_page())
        first = next(w for w in widgets(page) if isinstance(w, Gtk.Button))
        assert first.get_label() == "Report a Problem…"
        first.clicked()
        assert opened == [True]
    finally:
        dialog.destroy()


def test_help_is_a_plain_window_so_the_report_opens_in_front_of_it(window):
    """KWin keeps a Gtk.Dialog with no parent above every other window of
    its app, so Report a Problem, opened from Help, came up behind it
    (seen in KWin's stacking order, 2026-10-25). Escape still closes Help."""
    dialog = help_dialog.show_help_dialog(tab="problems")
    try:
        assert type(dialog) is Gtk.Window
    finally:
        dialog.destroy()


def test_escape_closes_help(window):
    from gi.repository import Gdk
    dialog = help_dialog.show_help_dialog()
    closed = []
    dialog.connect("destroy", lambda _w: closed.append(True))
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval = Gdk.KEY_Escape
    dialog.emit("key-press-event", event)
    assert closed == [True]


def test_help_opens_the_report_as_a_window_of_its_own(window, monkeypatch):
    """Closing it as a window transient for Help made KWin 6.7.5 eat memory
    until the desktop froze; as a window of its own it doesn't. Opened from
    Help's button, it has no transient parent."""
    from gtk_text import widgets
    monkeypatch.setattr(Gtk.Window, "show_all", lambda self: None)
    monkeypatch.setattr(Gtk.Window, "present", lambda self: None)
    # Not the real gathering: its thread would outlive this test's stand-ins
    # and could ask the real hotkey helper.
    monkeypatch.setattr(problem_report, "build",
                        lambda layout: problem_report.Report("", False))
    dialog = help_dialog.show_help_dialog(tab="problems")
    try:
        button = next(w for w in widgets(dialog) if isinstance(w, Gtk.Button)
                      and w.get_label() == "Report a Problem\u2026")
        button.clicked()
        report = next(w for w in Gtk.Window.list_toplevels()
                      if w.get_title() == "Report a Problem")
        assert report.get_transient_for() is None
        report.destroy()
    finally:
        dialog.destroy()


# --- no dictated words in it -----------------------------------------------------------

# With "Log transcribed text" on, the log has the words as Python wrote them:
# in double quotes when they have an apostrophe.
SAID = ["I'm sending the contract, don't forward it", "my secret is swordfish",
        "both ' and \" in it"]


def _logged(text):
    from tuxtalk.logger import redact_text
    return redact_text(text, reveal=True)


_LEVELS = {"debug", "info", "warning", "error", "exception", "critical", "log"}


def _is_loggable(node):
    return (isinstance(node, ast.Call)
            and getattr(node.func, "attr", getattr(node.func, "id", "")) == "loggable")


def _holds_loggable(node):
    return any(_is_loggable(n) for n in ast.walk(node))


def _loggable_calls(source, name="<test>"):
    """(where, call) for each logging call in *source*, at any level, with
    a live.loggable anywhere in its arguments."""
    for call in ast.walk(ast.parse(source)):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr in _LEVELS
                and any(_holds_loggable(a) for a in [*call.args,
                                                     *(k.value for k in call.keywords)])):
            yield f"{name}:{call.lineno}", call


def _loggable_messages():
    """Every log call in src that can hold what was said."""
    src = pathlib.Path(problem_report.__file__).parent
    for path in sorted(src.rglob("*.py")):
        yield from _loggable_calls(path.read_text(), path.name)


def _render(node, said, where):
    """What *node* writes, with *said* where the words go. A form this can't
    render fails the test rather than being passed over."""
    if _is_loggable(node):
        return _logged(said)
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.JoinedStr):
        return "".join(_render(part, said, where) for part in node.values)
    if isinstance(node, ast.FormattedValue):
        return _render(node.value, said, where)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _render(node.left, said, where) + _render(node.right, said, where)
    if isinstance(node, ast.IfExp):
        return _render(node.body, said, where)
    if _holds_loggable(node):
        pytest.fail(f"{where}: can't tell how this log line shows the words")
    return "x"


def _message(call, said, where):
    """The message *call* logs, %-arguments filled in as logging does."""
    args = call.args[1:] if call.func.attr == "log" else call.args
    if call.keywords and any(_holds_loggable(k.value) for k in call.keywords):
        pytest.fail(f"{where}: the words are passed by keyword")
    text = _render(args[0], said, where)
    if len(args) > 1:
        # Numbers stand in for the other arguments: a %d or %.1f takes them.
        text = text % tuple(_logged(said) if _holds_loggable(a) else 0 for a in args[1:])
    return text


def _found_everywhere(where, call):
    for said in SAID:
        line = f"2026-10-31 12:00:00 - tuxtalk.x - INFO - {_message(call, said, where)}"
        hidden, left_out = problem_report.hide_dictations(line)
        if said in hidden or not left_out or f"[hidden, {len(said)} chars]" not in hidden:
            return False
    return True


def test_every_kind_of_log_line_with_dictated_words_is_found():
    """A new live.loggable line the report doesn't know would put words in it."""
    calls = list(_loggable_messages())
    assert len(calls) >= 10
    for where, call in calls:
        assert _found_everywhere(where, call), where


@pytest.mark.parametrize("source", [
    'logger.info("New words: %s", live.loggable(text))',
    'logger.exception(f"New words: {live.loggable(text)}")',
    'logger.log(20, "New words: %s (%d)", live.loggable(text), n)',
])
def test_the_line_check_sees_every_way_of_logging(source):
    """A line the report doesn't know is found and caught however it's
    written: deferred %-arguments, any level."""
    [(where, call)] = _loggable_calls(source)
    assert not _found_everywhere(where, call)


def test_lines_without_dictated_words_stay_as_they_are():
    log = ("Paste: Ctrl+V (class='org.kde.kwrite')\n"
           "Raw transcription (Parakeet): [hidden, 25 chars]\n"
           "Voice edit: no formatting shortcuts in 'org.kde.kwrite'\n"
           "Voice edit: the AI answered 'None.'; not using it\n"
           "Chunk (last) was only noise; nothing typed for it")
    assert problem_report.hide_dictations(log) == (log, False)


@pytest.fixture
def said_log(report, tmp_path):
    """A log from logging the words, then logging them off: the old lines
    are still in it."""
    lines = [f"Raw transcription (Parakeet): {_logged(SAID[0])}",
             f"Normalized text: {_logged(SAID[1])}",
             f"Voice edit result: {_logged(SAID[2])} (took 1.20s)",
             "Raw transcription (Parakeet): [hidden, 11 chars]"]
    (tmp_path / "tuxtalk.log").write_text("\n".join(lines) + "\n")
    return report


def test_the_report_leaves_out_what_was_dictated(said_log):
    made = said_log()
    assert made.words_left_out
    for said in SAID:
        assert said not in made.text
    assert f"Raw transcription (Parakeet): [hidden, {len(SAID[0])} chars]" in made.text
    assert "Raw transcription (Parakeet): [hidden, 11 chars]" in made.text


def test_no_words_shown_copied_or_saved(said_log, window, monkeypatch, tmp_path):
    from tuxtalk import clipboard
    copied = []
    monkeypatch.setattr(clipboard, "copy_text", lambda text: copied.append(text) or True)
    win = window(None)          # the real report, from the log above
    target = tmp_path / "report.txt"
    _save_to(win, monkeypatch, target)
    win._on_copy(None)
    assert win.warning.get_visible()
    for text in (win._text(), copied[0], target.read_text()):
        assert "Normalized text: [hidden" in text
        for said in SAID:
            assert said not in text


# --- saving -------------------------------------------------------------------------------

def _save_to(win, monkeypatch, target):
    """Save to File…, with the file chooser answering *target*."""
    class Chooser:
        def set_current_name(self, _name):
            pass

        def set_do_overwrite_confirmation(self, _on):
            pass

        def run(self):
            return Gtk.ResponseType.ACCEPT

        def get_filename(self):
            return str(target)

        def destroy(self):
            pass
    monkeypatch.setattr(Gtk.FileChooserNative, "new", lambda *a: Chooser())
    win._on_save(None)


def test_a_saved_report_is_private(window, monkeypatch, tmp_path):
    """The log can name people and files: other users can't read it, also
    when it replaces a file anyone could read."""
    win = window()
    old = os.umask(0o022)
    try:
        new = tmp_path / "new.txt"
        _save_to(win, monkeypatch, new)
        replaced = tmp_path / "old.txt"
        replaced.write_text("before")
        replaced.chmod(0o644)
        _save_to(win, monkeypatch, replaced)
    finally:
        os.umask(old)
    for path in (new, replaced):
        assert path.read_text() == "the report\n"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.bak"))


def test_a_failed_save_leaves_the_file_it_would_replace(window, monkeypatch, tmp_path):
    win = window()
    target = tmp_path / "old.txt"
    target.write_text("keep me")

    def full_disk(_fd):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "fsync", full_disk)
    _save_to(win, monkeypatch, target)
    assert target.read_text() == "keep me"
    assert win.status.get_text().startswith("Couldn't save it")


# --- what it says -------------------------------------------------------------------------

def test_a_user_named_after_the_distro_leaves_the_distro_named(report, monkeypatch):
    """Linux Mint's live user is "mint": the report still says Linux Mint,
    and the name still goes from the settings."""
    from tuxtalk import config
    monkeypatch.setattr(problem_report, "os_name", lambda: "Linux Mint 22")
    monkeypatch.setattr(config, "load_config",
                        lambda: Settings(model="parakeet-v3", mic="mint's headset"))
    text = report(user="mint").text
    assert "System: Linux Mint 22" in text
    assert "<user>'s headset" in text and "mint's" not in text


def test_no_uinput_isnt_reported_as_can_type(report, monkeypatch):
    from tuxtalk import uinput_helper
    monkeypatch.setattr(uinput_helper, "check_uinput_exists", lambda: False)
    assert "Typing: can't: no /dev/uinput" in report().text


def test_the_flatpak_says_how_it_types_and_reads_keys(report, monkeypatch):
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    text = report().text
    assert "Typing: through the desktop's portal (Flatpak)" in text
    assert "Hotkey helper: not used (Flatpak" in text


def test_the_bug_form_opens_when_the_distro_cant_be_read(monkeypatch):
    """A broken /etc/os-release: the form still opens, saying why."""
    def unreadable():
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    monkeypatch.setattr(problem_report, "os_name", unreadable)
    fields = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(
        problem_report_window.issue_url()).query))
    assert fields["distro"].startswith("couldn't tell (UnicodeDecodeError")


def test_a_screen_reader_names_the_report(window):
    assert window().view.get_accessible().get_name() == "Report"


def test_escape_closes_the_report(window):
    from gi.repository import Gdk
    win = window()
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval = Gdk.KEY_Escape
    win.window.emit("key-press-event", event)
    assert win.closed
