"""Terminal detection must not hinge on how a compositor capitalises a class.

The focused-window class arrives from two different sources — the GNOME
Shell extension and, on KDE, the KWin script — and they are under no obligation
to agree on case. The set already carries hand-maintained variants ("xterm",
"XTerm", "UXTerm"), which is the shape of a bug waiting for the next terminal
whose class is spelled differently than someone expected.

Real values captured from a live Plasma 6.7.4 Wayland session: Konsole reports
"org.kde.konsole" and kitty reports "kitty".
"""

import pytest

from tuxtalk import window_classes


class TestKnownTerminalsAreRecognized:
    @pytest.mark.parametrize("wm_class", [
        "org.kde.konsole",   # measured on Plasma 6.7.4 Wayland
        "kitty",             # measured on Plasma 6.7.4 Wayland
        "org.gnome.Terminal",
        "gnome-terminal-server",   # measured on Zorin OS 18.1 (GNOME 46), from the extension
        "Alacritty",
        "foot",
        "org.kde.yakuake",
        "yakuake",
    ])
    def test_it_is_a_terminal(self, wm_class):
        assert window_classes.is_terminal_class(wm_class) is True


class TestCaseDoesNotMatter:
    @pytest.mark.parametrize("wm_class", [
        "Konsole", "KONSOLE", "org.KDE.Konsole",
        "Kitty", "ALACRITTY", "Foot",
    ])
    def test_a_differently_cased_terminal_is_still_a_terminal(self, wm_class):
        assert window_classes.is_terminal_class(wm_class) is True


class TestItStaysNarrow:
    @pytest.mark.parametrize("wm_class", [
        "brave-browser",            # measured live
        "com.anthropic.Claude",     # measured live
        "zenity",                   # measured live
        "org.gnome.Loupe",
        "",
    ])
    def test_a_non_terminal_is_not_a_terminal(self, wm_class):
        assert window_classes.is_terminal_class(wm_class) is False

    def test_unknown_focus_is_not_a_terminal(self):
        """None means 'nobody told us' — it must not be read as a terminal."""
        assert window_classes.is_terminal_class(None) is False

    def test_a_terminal_name_inside_another_app_does_not_match(self):
        """Substring matching would make 'kitty-photo-viewer' a terminal."""
        assert window_classes.is_terminal_class("kitty-photo-viewer") is False
