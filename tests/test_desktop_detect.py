"""Which desktop TuxTalk is on (desktop_detect.py), from XDG_CURRENT_DESKTOP
and DESKTOP_SESSION as each desktop really sets them."""
import pytest

from tuxtalk import desktop_detect, x11_hotkeys


@pytest.mark.parametrize("current, session, desktop", [
    ("GNOME", "gnome", "gnome"),
    ("ubuntu:GNOME", "ubuntu", "gnome"),
    ("pop:GNOME", "pop", "gnome"),
    ("zorin:GNOME", "zorin", "gnome"),
    ("KDE", "plasma", "kde"),
    ("X-Cinnamon", "cinnamon", "cinnamon"),
    # They name GNOME too, but run no GNOME Shell: no extension can load,
    # and no extension holds F8 back from a terminal on X11.
    ("Budgie:GNOME", "budgie-desktop", "unknown"),
    ("GNOME-Flashback:GNOME", "gnome-flashback-metacity", "unknown"),
])
def test_each_desktop_is_told_apart(monkeypatch, current, session, desktop):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", current)
    monkeypatch.setenv("DESKTOP_SESSION", session)
    assert desktop_detect.get_desktop_environment() == desktop


def test_budgie_on_x11_gets_tuxtalks_own_key_grab(monkeypatch):
    """Taken for GNOME, F8 and F9 left "^[[19~" in a terminal there."""
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "Budgie:GNOME")
    monkeypatch.setenv("DESKTOP_SESSION", "budgie-desktop")
    monkeypatch.setattr(desktop_detect, "is_x11_session", lambda env: True)
    assert x11_hotkeys.should_claim() is True


@pytest.mark.parametrize("env, x11", [
    ({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}, True),
    ({"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}, False),
    # startx sets no session type: X11 by its display.
    ({"DISPLAY": ":0"}, True),
    ({"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}, False),
    # A Wayland compositor nested in an X11 session: the keyboard and the
    # hotkeys are still the X server's.
    ({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-1"}, True),
    ({"XDG_SESSION_TYPE": "tty"}, False),
])
def test_one_rule_says_whether_the_session_is_x11(env, x11):
    assert desktop_detect.is_x11_session(env) is x11
