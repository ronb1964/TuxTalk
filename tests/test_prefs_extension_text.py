"""The GNOME extension dialogs must give steps that work on today's GNOME.

None may tell the user to press Alt+F2 and type r, which restarts GNOME Shell
only in an X11 session; GNOME 49 turned that session off by default and
GNOME 50 removed it. The success dialog must not promise a model switcher the
extension's menu does not have.
"""
from tuxtalk import prefs_system_check as P


def test_wayland_is_told_to_log_out_only(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    steps = P.extension_reload_steps()
    assert "Log out" in steps
    assert "Alt+F2" not in steps


def test_x11_also_gets_the_quick_restart(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    steps = P.extension_reload_steps()
    assert "Log out" in steps and "Alt+F2" in steps


def test_the_feature_list_matches_the_extension_menu():
    from pathlib import Path
    menu = (Path(__file__).parent.parent / "gnome-extension"
            / "talktype@ronb1964.github.io" / "extension.js").read_text()
    assert "Switch Whisper models" not in P.EXTENSION_FEATURES
    for item in ("Recent Dictations", "Fix a Word", "Performance", "Preferences"):
        assert item in P.EXTENSION_FEATURES
        assert item in menu

