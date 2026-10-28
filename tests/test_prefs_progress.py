"""A download or install in Preferences runs off the window's thread with a
progress window, and the window always closes, even when the work fails."""
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from tuxtalk import progress_window  # noqa: E402


class SameThread:
    """threading.Thread, run at start() on this thread."""

    def __init__(self, target, daemon=None, name=None):
        self.target = target

    def start(self):
        self.target()


@pytest.fixture
def run(monkeypatch):
    """run_with_progress with its thread and idle callbacks run in place;
    returns (run it, the windows it opened, what finish got)."""
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    windows, finished = [], []
    # Only progress_window's threads: the sound thread must stay a real one.
    monkeypatch.setattr(progress_window, "threading", SimpleNamespace(Thread=SameThread))
    monkeypatch.setattr(progress_window.GLib, "idle_add", lambda fn, *a: fn(*a))
    real = progress_window._progress_window

    def recorded(*args):
        made = real(*args)
        windows.append(made[0])
        return made
    monkeypatch.setattr(progress_window, "_progress_window", recorded)

    def go(work):
        progress_window.run_with_progress(None, "Installing", "Starting...", work, finished.append)
    return go, windows, finished


def test_the_result_and_the_progress_reach_the_window(run):
    go, windows, finished = run
    seen = []

    def work(progress):
        progress("Half way", 50)
        seen.append(windows[0].get_content_area().get_children()[1].get_fraction())
        return "done"
    go(work)
    assert seen == [0.5]
    assert finished == ["done"]
    assert not windows[0].get_visible()      # closed


def test_work_that_fails_still_closes_the_window_and_says_so(run):
    go, windows, finished = run

    def work(progress):
        raise OSError("disk full")
    go(work)
    assert finished == [None]
    assert not windows[0].get_visible()


def test_the_work_runs_off_the_windows_thread_and_its_updates_come_back_to_it(monkeypatch):
    """With a real worker: GTK may only be touched from its own thread, so
    the progress line and finish must come back to it, and the work must not
    run on it (the window would freeze)."""
    import threading
    import time
    from gi.repository import GLib
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    here, seen = threading.current_thread(), {}
    real = progress_window._progress_window

    class Line:
        """The status line, noting the thread each update came on."""
        def set_text(self, text):
            seen.setdefault("updates", []).append(threading.current_thread() is here)

    def window(*args):
        dialog, _line, bar = real(*args)
        return dialog, Line(), bar
    monkeypatch.setattr(progress_window, "_progress_window", window)

    def work(progress):
        seen["work off it"] = threading.current_thread() is not here
        progress("Halfway", 50)
        return "done"

    def finish(result):
        seen["finish"] = (threading.current_thread() is here, result)
    progress_window.run_with_progress(None, "Testing", "Starting", work, finish)
    deadline = time.monotonic() + 5
    while "finish" not in seen and time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
    assert seen == {"work off it": True, "updates": [True], "finish": (True, "done")}
