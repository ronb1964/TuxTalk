"""Preferences says when GNOME or COSMIC already uses a new dictation key
for a shortcut of its own (CODE_HEALTH 105): only KDE's verdict and the
Ctrl+Space list were said, so Alt+D picked on GNOME did both jobs unsaid."""
import pytest

from tuxtalk import cosmic_hotkeys, desktop_detect
from tuxtalk import desktop_shortcut_clashes as dsc

LISTING = """org.gnome.desktop.wm.keybindings panel-run-dialog ['<Alt>F2']
org.gnome.desktop.wm.keybindings switch-input-source ['<Super>space', 'XF86Keyboard']
org.gnome.settings-daemon.plugins.media-keys terminal ['<Primary><Alt>t']
org.gnome.shell.keybindings toggle-message-tray ['<Super>v', '<Super>m']
org.gnome.desktop.wm.keybindings minimize @as []"""


def test_gnomes_accelerators_are_read_as_modifiers_and_a_key():
    found = dsc.gnome_bindings(LISTING)
    assert found[(frozenset({"Ctrl", "Alt"}), "t")] == "terminal"
    assert found[(frozenset({"Super"}), "m")] == "toggle-message-tray"
    assert found[(frozenset({"Alt"}), "f2")] == "panel-run-dialog"


@pytest.fixture
def on(monkeypatch):
    def desktop(name):
        monkeypatch.setattr(desktop_detect, "get_desktop_environment", lambda: name)
    return desktop


def test_a_key_gnome_binds_is_named_with_its_setting(on, monkeypatch):
    on("gnome")
    monkeypatch.setattr(dsc, "gsettings", lambda *args: LISTING if args[1].endswith("wm.keybindings")
                        or args[1].endswith("media-keys") else "")
    found = dsc.clashes(["Ctrl+Alt+T", "Alt+D"])
    assert [(c.key, c.setting) for c in found] == [("Ctrl+Alt+T", "terminal")]
    assert "GNOME already uses Ctrl+Alt+T" in found[0].paragraph()


def test_a_key_cosmic_binds_is_named(on, monkeypatch):
    on("cosmic")
    monkeypatch.setattr(cosmic_hotkeys, "others_bindings",
                        lambda: {(frozenset({"Super"}), "d")})
    assert [c.title() for c in dsc.clashes(["Super+D", "F8"])] == ["COSMIC already uses Super+D"]


def test_elsewhere_nothing_is_looked_up(on, monkeypatch):
    on("kde")
    monkeypatch.setattr(dsc, "gsettings", lambda *args: pytest.fail("asked gsettings"))
    assert dsc.clashes(["Alt+D"]) == []
