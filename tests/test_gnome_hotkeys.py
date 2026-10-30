"""GNOME: the extension holds TuxTalk's hotkeys back from the focused app.

Same leak as KDE (test_kwin_hotkeys): the service reads keys below the desktop,
so GNOME Shell also hands F8 to the app and a terminal prints "~". The service
sends its hotkeys to the tray in accelerator syntax, the tray relays them, and
the extension grabs them while the service runs. These pin the Python side;
test_gnome_extension runs the extension's grabs.
"""
import types

import pytest

from tuxtalk import gnome_hotkeys as gh


def _cfg(hotkey="F8", toggle="F9", vc="Ctrl+Alt+V"):
    return types.SimpleNamespace(hotkey=hotkey, toggle_hotkey=toggle,
                                 voice_commands_hotkey=vc)


class TestAccelerators:
    @pytest.mark.parametrize("qt,accel", [
        ("F8", "F8"), ("F12", "F12"),
        ("Ctrl+Alt+V", "<Control><Alt>v"),
        ("Ctrl+Shift+H", "<Control><Shift>h"),
        ("Meta+F5", "<Super>F5"),
    ])
    def test_converts(self, qt, accel):
        assert gh.accelerator(qt) == accel

    def test_the_configured_hotkeys(self):
        assert gh.accelerators(_cfg()) == ["F8", "F9", "<Control><Alt>v"]

    def test_same_rule_as_kde(self):
        """A bare letter would stop the user typing it while dictation runs."""
        assert gh.accelerators(_cfg("A", "", "")) == []

    def test_nothing_configured(self):
        assert gh.accelerators(_cfg("", "", "")) == []


class TestTheTrayRelaysThem:
    @pytest.fixture
    def svc(self):
        from tuxtalk.dbus_service import TuxTalkDBusService
        s = TuxTalkDBusService.__new__(TuxTalkDBusService)  # no real bus
        s.claimed_hotkeys = []
        s.sent = []
        s.HotkeysChanged = lambda accels: s.sent.append(list(accels))
        return s

    def test_notify_stores_and_signals(self, svc):
        svc.NotifyClaimedHotkeys(["F8", "<Control><Alt>v"])
        assert svc.claimed_hotkeys == ["F8", "<Control><Alt>v"]
        assert svc.sent == [["F8", "<Control><Alt>v"]]

    def test_a_stopped_service_clears_them(self, svc):
        svc.NotifyClaimedHotkeys(["F8"])
        svc.clear_claimed_hotkeys()
        assert svc.claimed_hotkeys == []
        assert svc.sent[-1] == []

    def test_clearing_nothing_sends_nothing(self, svc):
        svc.clear_claimed_hotkeys()
        assert svc.sent == []
