"""A download or install in Preferences runs off the window's thread with a
progress window, and the window always closes, even when the work fails."""
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from tuxtalk import prefs_tab  # noqa: E402


class SameThread:
    """threading.Thread, run at start() on this thread."""

    def __init__(self, target, daemon=None):
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
    # Only prefs_tab's threads: the sound thread must stay a real one.
    monkeypatch.setattr(prefs_tab, "threading", SimpleNamespace(Thread=SameThread))
    monkeypatch.setattr(prefs_tab.GLib, "idle_add", lambda fn, *a: fn(*a))
    real = prefs_tab._progress_window

    def recorded(*args):
        made = real(*args)
        windows.append(made[0])
        return made
    monkeypatch.setattr(prefs_tab, "_progress_window", recorded)

    def go(work):
        prefs_tab.run_with_progress(None, "Installing", "Starting...", work, finished.append)
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
