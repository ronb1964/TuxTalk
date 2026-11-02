"""The splash screen shows TuxTalk's own icon wherever it was started from,
and its fade-in stops for a window that was closed."""
import os

import pytest

from gi.repository import Gtk

from tuxtalk import app_icon, splash_screen as ss


def test_the_splash_finds_the_projects_own_icon_from_any_folder(monkeypatch, tmp_path):
    """It looked in src/icons, which doesn't exist; only paths relative to the
    working folder found the icon, and only when started from the project."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APPDIR", raising=False)
    candidates = app_icon.icon_files()
    assert all(os.path.isabs(path) for path in candidates)
    assert os.path.isfile(candidates[0])
    assert candidates[0].endswith(os.path.join("icons", "OFFICIAL_ICON_DO_NOT_CHANGE.svg"))


def test_a_window_closed_while_it_fades_in_stops_the_fade(monkeypatch):
    """The welcome screen closed in its first half second kept getting
    opacity changes, and its "then" ran for a window that was gone."""
    steps, done = [], []
    monkeypatch.setattr(ss.GLib, "timeout_add", lambda ms, fn: steps.append(fn))
    window = Gtk.Window()
    window.show()
    ss.fade_in(window, lambda: done.append(True))
    window.destroy()
    while steps:
        steps.pop()()
    assert done == []


def test_the_fade_in_reaches_full_opacity_then_goes_on(monkeypatch):
    steps, done = [], []
    monkeypatch.setattr(ss.GLib, "timeout_add", lambda ms, fn: steps.append(fn))
    window = Gtk.Window()
    window.show()
    try:
        ss.fade_in(window, lambda: done.append(window.get_opacity()))
        while steps:
            steps.pop()()
        assert done == [pytest.approx(1.0, abs=0.01)]
    finally:
        window.destroy()
