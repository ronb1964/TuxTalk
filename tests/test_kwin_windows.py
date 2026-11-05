"""Close, minimize and maximize windows by voice on KDE (kwin_windows.py):
a KWin script acts on the app's windows or the active one, and says whether
there was one. The KWin it runs in is a stand-in (desktop_fakes.py).
"""
import json

import pytest

from tuxtalk import kwin_windows

KONSOLE = 'makeWindow("org.kde.konsole", "{k}", 0, 0, 800, 600)'
FIREFOX = 'makeWindow("firefox", "{f}", 0, 0, 800, 600)'


def test_kde_closes_the_apps_window_politely(kde, fake_kwin):
    fake_kwin.windows = [KONSOLE, FIREFOX]
    assert kwin_windows.act("close", "firefox", {"firefox"}, False, 1.0) is True
    assert fake_kwin.closed == [False, True]
    script = fake_kwin.scripts[-1]
    assert "closeWindow" in script and "kill" not in script.lower()


def test_kde_close_all_closes_every_window_of_the_app(kde, fake_kwin):
    fake_kwin.windows = [FIREFOX, KONSOLE, FIREFOX.replace("{f}", "{g}")]
    assert kwin_windows.act("close", "firefox", {"firefox"}, True, 1.0) is True
    assert fake_kwin.closed == [True, False, True]


def test_kde_close_one_window_takes_only_the_topmost(kde, fake_kwin):
    fake_kwin.windows = [FIREFOX, FIREFOX.replace("{f}", "{g}")]
    assert kwin_windows.act("close", "firefox", {"firefox"}, False, 1.0) is True
    assert fake_kwin.closed == [False, True]


def test_kde_close_window_closes_the_active_one(kde, fake_kwin):
    """Without an app named, the window the user is in, and no other."""
    fake_kwin.windows = [KONSOLE, FIREFOX]
    fake_kwin.active = 0
    assert kwin_windows.act("close", "", set(), False, 1.0) is True
    assert fake_kwin.closed == [True, False]


def test_kde_close_window_closes_a_focused_dialog(kde, fake_kwin):
    """A "Save changes?" question is the window the user is in."""
    fake_kwin.windows = [KONSOLE, 'Object.assign(makeWindow("org.kde.kwrite", "{d}", 0, 0, 9, 9),'
                                  ' {normalWindow: false, dialog: true})']
    fake_kwin.active = 1
    assert kwin_windows.act("close", "", set(), False, 1.0) is True
    assert fake_kwin.closed == [False, True]


def test_kde_close_window_never_closes_the_desktop(kde, fake_kwin):
    fake_kwin.windows = ['Object.assign(makeWindow("plasmashell", "{desk}", 0, 0, 9, 9),'
                         ' {normalWindow: false, desktopWindow: true})']
    fake_kwin.active = 0
    assert kwin_windows.act("close", "", set(), False, 1.0) is False
    assert fake_kwin.closed == [False]


def test_a_script_error_is_told_apart_from_no_window(kde, fake_kwin):
    """KWin failing to act isn't "Firefox has no open window"."""
    fake_kwin.windows = ['Object.assign(makeWindow("firefox", "{f}", 0, 0, 9, 9),'
                         ' {closeWindow() { throw new Error("refused"); }})']
    with pytest.raises(RuntimeError, match="refused"):
        kwin_windows.act("close", "firefox", {"firefox"}, False, 1.0)


def test_kde_minimizes_and_maximizes_the_active_window(kde, fake_kwin):
    fake_kwin.windows = [KONSOLE, FIREFOX]
    fake_kwin.active = 1
    assert kwin_windows.act("minimize", "", set(), False, 1.0) is True
    assert fake_kwin.minimized == [False, True]
    assert kwin_windows.act("maximize", "", set(), False, 1.0) is True
    assert fake_kwin.maximized == [[False, False], [True, True]]


def test_kde_restore_window_unmaximizes_it(kde, fake_kwin):
    fake_kwin.windows = ['Object.assign(makeWindow("firefox", "{f}", 0, 0, 9, 9),'
                         ' {maximized: [true, true]})']
    fake_kwin.active = 0
    assert kwin_windows.act("unmaximize", "", set(), False, 1.0) is True
    assert fake_kwin.maximized == [[False, False]]


def test_kde_restore_brings_back_a_minimized_maximized_window(kde, fake_kwin):
    fake_kwin.windows = [KONSOLE, 'Object.assign(makeWindow("firefox", "{f}", 0, 0, 9, 9),'
                                  ' {minimized: true, maximized: [true, true]})']
    assert kwin_windows.act("restore", "firefox", {"firefox"}, False, 1.0) is True
    assert fake_kwin.minimized == [False, False]
    assert fake_kwin.maximized == [[False, False], [False, False]]
    assert fake_kwin.activated == "{f}"


def test_kde_maximize_by_name_brings_it_forward(kde, fake_kwin):
    fake_kwin.windows = [FIREFOX, KONSOLE]
    assert kwin_windows.act("maximize", "firefox", {"firefox"}, False, 1.0) is True
    assert fake_kwin.maximized == [[True, True], [False, False]]
    assert fake_kwin.activated == "{f}"


def test_kde_minimize_by_name_leaves_the_focus_alone(kde, fake_kwin):
    fake_kwin.windows = [FIREFOX, KONSOLE]
    fake_kwin.active = 1
    assert kwin_windows.act("minimize", "firefox", {"firefox"}, False, 1.0) is True
    assert fake_kwin.activated == "{k}"


def test_kde_never_closes_a_popup_of_the_app(kde, fake_kwin):
    fake_kwin.windows = ['Object.assign(makeWindow("firefox", "{tip}", 0, 0, 9, 9),'
                         ' {normalWindow: false})']
    assert kwin_windows.act("close", "firefox", {"firefox"}, True, 1.0) is False
    assert fake_kwin.closed == [False]


def test_kde_without_the_apps_window_says_so(kde, fake_kwin):
    fake_kwin.windows = [KONSOLE]
    assert kwin_windows.act("close", "firefox", {"firefox"}, False, 1.0) is False
    assert fake_kwin.closed == [False]


def test_kde_with_no_active_window_acts_on_nothing(kde, fake_kwin):
    fake_kwin.windows = [KONSOLE]
    assert kwin_windows.act("close", "", set(), False, 1.0) is False
    assert fake_kwin.closed == [False]


def test_an_app_name_is_never_run_as_script_code(kde, fake_kwin):
    """A menu entry's name comes from a file anyone can install."""
    evil = '");workspace.stackingOrder.forEach(w => w.closeWindow());("'
    fake_kwin.windows = [KONSOLE]
    assert kwin_windows.act("close", evil, {evil}, True, 1.0) is False
    assert fake_kwin.closed == [False]
    assert json.dumps(evil) in fake_kwin.scripts[-1]


def test_the_window_script_is_taken_out_of_kwin_afterwards(kde, fake_kwin):
    fake_kwin.windows = [FIREFOX]
    kwin_windows.act("close", "firefox", {"firefox"}, False, 1.0)
    assert fake_kwin.methods == ["unloadScript", "loadScript", "start", "unloadScript"]


def test_kde_find_says_whether_the_app_has_a_window_and_does_nothing(kde, fake_kwin):
    fake_kwin.windows = [KONSOLE, FIREFOX]
    assert kwin_windows.act("find", "firefox", {"firefox"}, False, 1.0) is True
    assert kwin_windows.act("find", "org.kde.kwrite", {"kwrite"}, False, 1.0) is False
    assert fake_kwin.closed == [False, False] and fake_kwin.minimized == [False, False]
