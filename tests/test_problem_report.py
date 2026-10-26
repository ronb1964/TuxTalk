"""Report a Problem: one place that gathers what helps find a problem, shown
in full before the person shares it anywhere. TuxTalk sends nothing itself.
"""
import time
import urllib.parse

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from tuxtalk import help_dialog, problem_report, problem_report_window  # noqa: E402
from tuxtalk.config import Settings  # noqa: E402

HOME = "/home/archer"


@pytest.fixture
def report(monkeypatch, tmp_path):
    """build() on a made-up computer: settings, a log, and facts that answer
    without asking the real system."""
    from tuxtalk import config, desktop_detect, keyboard_layout, recommend, secure_input
    from tuxtalk import uinput_helper, update_checker
    monkeypatch.setattr(config, "load_config", lambda: Settings(model="parakeet-v3"))
    monkeypatch.setenv("TUXTALK_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "LXQt")
    monkeypatch.setattr(problem_report, "os_name", lambda: "Fedora Linux 44 (LXQt)")
    monkeypatch.setattr(desktop_detect, "get_desktop_environment", lambda: "unknown")
    monkeypatch.setattr(keyboard_layout, "active_layout", lambda: ("us", ""))
    monkeypatch.setattr(recommend, "detect_hardware", lambda cfg: recommend.Hardware("Intel graphics"))
    monkeypatch.setattr(secure_input, "is_installed", lambda: True)
    monkeypatch.setattr(secure_input, "running_version", lambda: "1.4")
    monkeypatch.setattr(uinput_helper, "typing_blocked_reason", lambda: None)
    monkeypatch.setattr(update_checker, "get_install_type", lambda: "appimage")
    log = tmp_path / "tuxtalk.log"
    log.write_text("".join(f"line {n} {HOME}/AppImages/TuxTalk.AppImage\n" for n in range(400)))
    return lambda: problem_report.build(problem_report.fact(problem_report.layout_text),
                                        home=HOME, user="archer")


def test_the_report_says_what_helps_find_a_problem(report):
    text = report()
    for wanted in ("Version: ", "Installed as: appimage", "Hotkey helper: running, version 1.4",
                   "Typing: can type", "System: Fedora Linux 44 (LXQt)", "LXQt", "x11",
                   "Keyboard layout: us", "Graphics: Intel graphics", "model = 'parakeet-v3'"):
        assert wanted in text


def test_it_ends_with_the_last_lines_of_the_log(report):
    text = report()
    assert f"line {400 - problem_report.LOG_LINES} " in text
    assert f"line {399 - problem_report.LOG_LINES} " not in text


def test_the_home_folder_and_user_name_are_taken_out(report):
    text = report()
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
    text = report()
    assert "Keyboard layout: couldn't tell (OSError: no layout)" in text
    assert "Graphics: Intel graphics" in text


def test_no_log_yet_says_so(report, tmp_path):
    (tmp_path / "tuxtalk.log").unlink()
    assert "(no log yet)" in report()


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
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    from tuxtalk import config
    monkeypatch.setattr(Gtk.Window, "show_all", lambda self: None)   # never on screen
    monkeypatch.setattr(config, "load_config", lambda: Settings())
    monkeypatch.setattr(problem_report, "layout_text", lambda: "us")

    def make(report="the report"):
        monkeypatch.setattr(problem_report, "build", lambda layout: report)
        win = problem_report_window.ReportWindow()
        for _ in range(200):    # the report is gathered on a thread
            GLib.MainContext.default().iteration(False)
            if win._text() == report:
                break
            time.sleep(0.01)
        made.append(win)
        return win
    made = []
    yield make
    for win in made:
        win.window.destroy()


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


def test_it_warns_when_the_log_holds_what_was_dictated(window):
    """Also when logging dictations is off now: the old lines are still there."""
    hidden = window("Raw transcription (Parakeet): [hidden, 25 chars]")
    shown = window("Raw transcription (Parakeet): 'my secret is swordfish'")
    assert not hidden.warning.get_visible()
    assert shown.warning.get_visible()


def test_closing_it_before_the_report_is_in_is_fine(window):
    win = window()
    win.window.destroy()
    assert win._show_report("late") is False


def test_opening_an_issue_copies_the_report_and_opens_the_form(window, monkeypatch):
    win = window()
    copied, opened = [], []
    monkeypatch.setattr(win, "_copy", lambda: copied.append(win._text()))
    monkeypatch.setattr(Gtk, "show_uri_on_window", lambda w, uri, t: opened.append(uri))
    win._on_issue(None)
    assert copied == ["the report"]
    assert opened and opened[0].startswith(problem_report_window.ISSUE_URL)
    assert "Log Output" in win.status.get_text()


def test_help_problems_opens_it(monkeypatch):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    shown, opened = [], []
    monkeypatch.setattr(Gtk.Dialog, "show_all", lambda self: shown.append(self))
    monkeypatch.setattr(Gtk.Dialog, "present", lambda self: None)
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
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    from gtk_text import widgets
    shown, opened = [], []
    monkeypatch.setattr(Gtk.Dialog, "show_all", lambda self: shown.append(self))
    monkeypatch.setattr(Gtk.Dialog, "present", lambda self: None)
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


def test_the_report_opens_in_front_of_help(window, monkeypatch):
    """Both kept above other windows, KDE put the new Report window behind
    Help, where it couldn't be seen. Help steps down while it's open, and
    back up once it's closed."""
    from gtk_text import widgets
    kept_above = []
    monkeypatch.setattr(Gtk.Dialog, "show_all", lambda self: None)
    monkeypatch.setattr(Gtk.Dialog, "present", lambda self: None)
    monkeypatch.setattr(Gtk.Dialog, "set_keep_above", lambda self, above: kept_above.append(above))
    dialog = help_dialog.show_help_dialog(tab="problems")
    try:
        button = next(w for w in widgets(dialog) if isinstance(w, Gtk.Button)
                      and w.get_label() == "Report a Problem\u2026")
        button.clicked()
        assert kept_above == [True, False]
        report = next(w for w in Gtk.Window.list_toplevels()
                      if w.get_title() == "Report a Problem")
        report.destroy()
        assert kept_above == [True, False, True]
    finally:
        dialog.destroy()


def test_help_opens_the_report_as_a_window_of_its_own(window, monkeypatch):
    """Closing it as a window transient for Help made KWin 6.7.5 eat memory
    until the desktop froze; as a window of its own it doesn't. Opened from
    Help's button, it has no transient parent."""
    from gtk_text import widgets
    monkeypatch.setattr(Gtk.Dialog, "show_all", lambda self: None)
    monkeypatch.setattr(Gtk.Dialog, "present", lambda self: None)
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
