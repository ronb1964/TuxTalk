"""The splash screen shows TuxTalk's own icon wherever it was started from,
and its fade-in stops for a window that was closed."""
import os

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from tuxtalk import splash_screen as ss  # noqa: E402


def test_the_splash_finds_the_projects_own_icon_from_any_folder(monkeypatch, tmp_path):
    """It looked in src/icons, which doesn't exist; only paths relative to the
    working folder found the icon, and only when started from the project."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APPDIR", raising=False)
    candidates = ss._icon_candidates()
    assert all(os.path.isabs(path) for path in candidates)
    assert os.path.isfile(candidates[0])
    assert candidates[0].endswith(os.path.join("icons", "OFFICIAL_ICON_DO_NOT_CHANGE.svg"))


def test_a_window_closed_while_it_fades_in_stops_the_fade(monkeypatch):
    """The welcome screen closed in its first half second kept getting
    opacity changes, and its "then" ran for a window that was gone."""
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    steps, done = [], []
    monkeypatch.setattr(ss.GLib, "timeout_add", lambda ms, fn: steps.append(fn))
    window = Gtk.Window()
    window.show()
    ss.fade_in(window, lambda: done.append(True))
    window.destroy()
    while steps:
        steps.pop()()
    assert done == []
