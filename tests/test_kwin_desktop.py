"""Snapping, centring, desktops and screens on KDE (kwin_desktop.py), against
stand-ins for kglobalaccel and KWin (desktop_fakes.py): nothing here touches
the real desktop."""
import json
import re

import pytest

from tuxtalk import kwin_desktop

# Read from Plasma 6.7.5 (shortcutNames of /component/kwin); a renamed shortcut does nothing.
KWIN_SHORTCUTS = {
    "Window Quick Tile Left", "Window Quick Tile Right", "Window Quick Tile Top",
    "Window Quick Tile Bottom", "Window Quick Tile Top Left", "Window Quick Tile Top Right",
    "Window Quick Tile Bottom Left", "Window Quick Tile Bottom Right", "Window Move Center",
    "Window to Next Desktop", "Window to Previous Desktop", "Window to Next Screen",
    "Overview", *(f"Window to Desktop {n}" for n in range(1, 10))}


def _invoked(kde):
    return [c for c in kde.calls if c[0] == "invokeShortcut"]


def test_every_shortcut_name_is_one_kwin_has():
    assert set(kwin_desktop.SHORTCUTS.values()) <= KWIN_SHORTCUTS


def test_snap_left_invokes_kwins_own_quick_tile(fake_kde):
    assert kwin_desktop.act("snap left", None, 1.0) == "done"
    assert fake_kde.calls == [("invokeShortcut", "Window Quick Tile Left")]


def test_centre_uses_window_move_center(fake_kde):
    assert kwin_desktop.act("center", None, 1.0) == "done"
    assert fake_kde.calls == [("invokeShortcut", "Window Move Center")]


def test_show_desktop_toggles(fake_kde):
    kwin_desktop.act("show desktop", None, 1.0)
    fake_kde.showing = True
    kwin_desktop.act("show desktop", None, 1.0)
    assert [c for c in fake_kde.calls if c[0] == "showDesktop"] == [
        ("showDesktop", True), ("showDesktop", False)]


def test_switch_to_desktop_two(fake_kde):
    assert kwin_desktop.act("desktop", 2, 1.0) == "done"
    assert ("setCurrentDesktop", 2) in fake_kde.calls and fake_kde.current == 2


def test_a_desktop_that_isnt_there_says_so(fake_kde):
    assert kwin_desktop.act("desktop", 5, 1.0) == "no_desktop"
    assert kwin_desktop.act("to desktop", 5, 1.0) == "no_desktop"
    assert ("invokeShortcut", "Window to Desktop 5") not in fake_kde.calls


def test_one_desktop_says_so(fake_kde):
    fake_kde.desktops = 1
    for action in ("next desktop", "previous desktop", "to next desktop",
                   "to previous desktop"):
        assert kwin_desktop.act(action, None, 1.0) == "one_desktop"
    assert not _invoked(fake_kde)
    assert not [c for c in fake_kde.calls if c[0] in ("nextDesktop", "previousDesktop")]


def test_next_desktop_goes_on_and_at_the_last_says_edge(fake_kde):
    assert kwin_desktop.act("next desktop", None, 1.0) == "done"
    assert fake_kde.current == 2
    assert kwin_desktop.act("next desktop", None, 1.0) == "edge"
    assert kwin_desktop.act("previous desktop", None, 1.0) == "done"
    assert fake_kde.current == 1


def test_move_window_to_desktop_two_invokes_its_shortcut(fake_kde):
    assert kwin_desktop.act("to desktop", 2, 1.0) == "done"
    assert fake_kde.calls == [("invokeShortcut", "Window to Desktop 2")]


def test_one_screen_moves_nothing(fake_kde, fake_kwin):
    assert kwin_desktop.act("to next screen", None, 1.0) == "one_screen"
    assert not _invoked(fake_kde)


def test_two_screens_move_the_window(fake_kde, fake_kwin):
    fake_kwin.screens = 2
    assert kwin_desktop.act("to next screen", None, 1.0) == "done"
    assert fake_kde.calls[-1] == ("invokeShortcut", "Window to Next Screen")


def test_the_screens_script_is_taken_out_of_kwin_afterwards(fake_kde, fake_kwin):
    kwin_desktop.act("to next screen", None, 1.0)
    assert fake_kwin.methods[-1] == "unloadScript"


def test_an_unknown_action_is_never_sent(fake_kde):
    assert kwin_desktop.act("kill", None, 1.0) == "unknown"
    assert fake_kde.calls == []


def test_a_silent_kwin_is_not_taken_for_one_screen(fake_kde, fake_kwin):
    fake_kwin.silent = True
    with pytest.raises(TimeoutError):
        kwin_desktop.act("to next screen", None, 0.3)
    assert not _invoked(fake_kde)


def test_a_lost_screens_answer_is_not_taken_for_one_screen(fake_kde, fake_kwin, monkeypatch):
    """KWin missed the question once and answers the next script: the
    user with two screens must not hear there is one."""
    real = fake_kwin.call

    def first_one_lost(method, *args):
        answer = real(method, *args)
        if method == "start":
            fake_kwin.silent = False
        return answer
    fake_kwin.silent = True
    monkeypatch.setattr(fake_kwin, "call", first_one_lost)
    with pytest.raises(TimeoutError):
        kwin_desktop.act("to next screen", None, 0.3)
    assert not _invoked(fake_kde)


def test_one_screen_is_known_from_one_question(fake_kde, fake_kwin):
    assert kwin_desktop.act("to next screen", None, 1.0) == "one_screen"
    assert len(fake_kwin.scripts) == 1


def test_the_screens_script_carries_the_reply_name_as_json(fake_kde, fake_kwin):
    kwin_desktop.act("to next screen", None, 1.0)
    reply = re.search(r'callDBus\((".*?"), ', fake_kwin.scripts[0]).group(1)
    assert json.loads(reply).startswith(":")
    assert 'callDBus("' + json.loads(reply) + '"' in fake_kwin.scripts[0]


def test_a_screens_script_that_fails_raises(fake_kde, fake_kwin, monkeypatch):
    build = kwin_desktop._screens_script
    monkeypatch.setattr(kwin_desktop, "_screens_script", lambda _expression: build("nothing.here"))
    with pytest.raises(RuntimeError):
        kwin_desktop.act("to next screen", None, 1.0)


def test_a_desktop_above_nine_is_never_sent(fake_kde):
    fake_kde.desktops = 12
    assert kwin_desktop.act("to desktop", 10, 1.0) == "no_desktop"
    assert not _invoked(fake_kde)


def test_a_window_move_no_longer_wanted_after_asking_kwin_is_never_sent(fake_kde):
    """The desktop count is asked first; by then the user may have clicked
    into another window, and the shortcut would move that one."""
    asked = []
    assert kwin_desktop.act("to next desktop", None, 1.0,
                            still_wanted=lambda: asked.append(len(fake_kde.calls)) or False
                            ) == "stopped"
    assert asked == [0] and _invoked(fake_kde) == []


def test_a_window_move_still_wanted_is_sent(fake_kde):
    assert kwin_desktop.act("to desktop", 2, 1.0, still_wanted=lambda: True) == "done"
    assert _invoked(fake_kde) == [("invokeShortcut", "Window to Desktop 2")]
