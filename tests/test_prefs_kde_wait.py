"""While Preferences waits for KDE's verdict on a new hotkey, its buttons
must not work: the wait runs the main loop, so a second Apply, OK or Cancel
clicked then would run inside the first Apply (Cancel would destroy the window
it is still using)."""
from types import SimpleNamespace

from tuxtalk import kwin_hotkeys, service_launcher
from tuxtalk import prefs as P
from tuxtalk.prefs import PreferencesWindow
from tuxtalk.prefs_general import GeneralTab


def _tab(window):
    """The General tab of the stand-in *window*."""
    return SimpleNamespace(win=window, window=window.window)


def test_the_window_is_locked_while_waiting_and_unlocked_after(monkeypatch):
    states = []
    answers = iter([None, ["F8"]])
    monkeypatch.setattr(kwin_hotkeys, "should_claim", lambda: True)
    monkeypatch.setattr(kwin_hotkeys, "refused_since", lambda since: next(answers))
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: [123])
    monkeypatch.setattr(P.Gtk, "events_pending", lambda: False)
    d = SimpleNamespace(window=SimpleNamespace(set_sensitive=states.append))

    assert GeneralTab._kde_refused(_tab(d), 0.0) == ["F8"]
    assert states == [False, True]


def test_an_error_while_waiting_still_unlocks_it(monkeypatch):
    states = []

    def boom(since):
        raise RuntimeError("kwin went away")
    monkeypatch.setattr(kwin_hotkeys, "should_claim", lambda: True)
    monkeypatch.setattr(kwin_hotkeys, "refused_since", boom)
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: [123])
    d = SimpleNamespace(window=SimpleNamespace(set_sensitive=states.append))

    try:
        GeneralTab._kde_refused(_tab(d), 0.0)
    except RuntimeError:
        pass
    assert states == [False, True]


def test_the_title_bar_close_waits_too(monkeypatch):
    """The window manager's close button isn't covered by set_sensitive, and
    closing then would destroy the window the first Apply goes on to use."""
    closed = []
    seen = []
    answers = iter([None, ["F8"]])
    monkeypatch.setattr(kwin_hotkeys, "should_claim", lambda: True)
    monkeypatch.setattr(kwin_hotkeys, "refused_since", lambda since: next(answers))
    monkeypatch.setattr(service_launcher, "find_service_pids", lambda: [123])
    monkeypatch.setattr(P.Gtk, "main_quit", lambda: closed.append("quit"))
    d = SimpleNamespace(window=SimpleNamespace(set_sensitive=lambda v: None),
                        _waiting_for_kde=False,
                        _has_unapplied_changes=lambda: False,
                        _release=lambda: closed.extend(["mic", "pidfile"]))

    def close_now():
        seen.append(PreferencesWindow.on_window_close(d, None, None))
        return False
    monkeypatch.setattr(P.Gtk, "events_pending", close_now)

    assert GeneralTab._kde_refused(_tab(d), 0.0) == ["F8"]
    assert seen == [True], "the close went ahead during the wait"
    assert closed == []

    assert PreferencesWindow.on_window_close(d, None, None) is False
    assert closed == ["mic", "pidfile", "quit"]
