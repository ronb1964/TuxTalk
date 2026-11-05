"""Starting an app by voice: the menu's way, with no trace of TuxTalk's own
environment, and the app in a systemd scope of its own. No app is ever
started and the real systemd is never asked: both are stand-ins."""
import logging

import pytest
from gi.repository import GLib

from tuxtalk import app_launcher
from tuxtalk.app_finder import AppEntry
from tuxtalk.app_launcher import launch, own_scope

FIREFOX = AppEntry("firefox.desktop", "Firefox", "Firefox", "Web Browser", "firefox", "")


class FakeAppInfo:
    """Stands in for Gio.DesktopAppInfo; keeps what the app would have been given."""

    def __init__(self, pid, fail, calls):
        self.pid, self.fail, self.calls = pid, fail, calls

    def launch_uris_as_manager(self, uris, context, flags, setup, setup_data,
                               pid_callback, callback_data):
        if self.fail:
            raise GLib.Error("no such program")
        self.calls.append({"uris": uris, "env": dict(
            e.split("=", 1) for e in context.get_environment())})
        if self.pid is not None:
            pid_callback(self, self.pid, callback_data)
        return True


class FakeGio:
    pid = 4321

    def __init__(self):
        self.fail = False
        self.reports_pid = True
        self.launched = []
        self.asked_for = []

    def app_info(self, app_id):
        self.asked_for.append(app_id)
        return FakeAppInfo(self.pid if self.reports_pid else None, self.fail, self.launched)


class FakeSystemd:
    def __init__(self):
        self.units = []     # (unit name, pids)
        self.fail = False

    def start_scope(self, name, pids):
        if self.fail:
            raise RuntimeError("Access denied")
        self.units.append((name, pids))


@pytest.fixture
def fake_gio(monkeypatch):
    gio = FakeGio()
    monkeypatch.setattr(app_launcher, "_app_info", gio.app_info)
    return gio


@pytest.fixture
def fake_systemd(monkeypatch):
    systemd = FakeSystemd()
    monkeypatch.setattr(app_launcher, "_systemd", lambda: systemd)
    return systemd


def test_launch_uses_the_clean_environment(fake_gio, fake_systemd, monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "/tmp/.mount_X/usr")
    monkeypatch.setenv("TUXTALK_USERS_GDK_BACKEND", "wayland")
    monkeypatch.setenv("GDK_BACKEND", "x11")
    assert launch(FIREFOX)
    env = fake_gio.launched[0]["env"]
    assert "PYTHONHOME" not in env and env["GDK_BACKEND"] == "wayland"
    assert fake_gio.asked_for == ["firefox.desktop"] and fake_gio.launched[0]["uris"] == []


def test_each_launched_app_gets_its_own_scope(fake_gio, fake_systemd):
    assert launch(FIREFOX)
    name, pids = fake_systemd.units[0]
    assert name == "app-tuxtalk-firefox.desktop-4321.scope" and pids == [fake_gio.pid]


def test_an_app_id_systemd_would_refuse_is_escaped(fake_systemd):
    own_scope(7, "org.gnome.Calculator Plus/x-y.desktop")
    assert fake_systemd.units[0][0] == (
        r"app-tuxtalk-org.gnome.Calculator\x20Plus\x2fx\x2dy.desktop-7.scope")


def test_no_systemd_still_launches(fake_gio, monkeypatch):
    monkeypatch.setattr(app_launcher, "_systemd", lambda: None)
    assert launch(FIREFOX)


def test_an_app_that_reports_no_process_is_still_launched(fake_gio, fake_systemd):
    fake_gio.reports_pid = False
    assert launch(FIREFOX) and fake_systemd.units == []


def test_a_refused_scope_does_not_fail_the_launch(fake_gio, fake_systemd, caplog):
    fake_systemd.fail = True
    with caplog.at_level(logging.INFO):
        assert launch(FIREFOX)
    assert "own scope" in caplog.text


def test_a_failed_launch_is_false_and_logged(fake_gio, fake_systemd, caplog):
    fake_gio.fail = True
    with caplog.at_level(logging.INFO):
        assert not launch(FIREFOX)
    assert "Couldn't open firefox.desktop" in caplog.text


def test_an_app_removed_since_the_list_was_read_is_false(monkeypatch, caplog):
    monkeypatch.setattr(app_launcher, "_app_info", lambda app_id: None)
    with caplog.at_level(logging.INFO):
        assert not launch(FIREFOX)
    assert "Couldn't open firefox.desktop" in caplog.text


class FakeSessionBus:
    def __init__(self, systemd_running):
        self.systemd_running = systemd_running
        self.calls = []

    def name_has_owner(self, name):
        return self.systemd_running and name == "org.freedesktop.systemd1"

    def call_blocking(self, *args, **kwargs):
        self.calls.append(args)


def test_a_desktop_without_systemd_has_no_scope_to_ask(monkeypatch):
    import dbus
    monkeypatch.setattr(dbus, "SessionBus", lambda: FakeSessionBus(False))
    assert app_launcher._systemd() is None


def test_the_scope_is_asked_for_in_the_app_slice(monkeypatch):
    import dbus
    bus = FakeSessionBus(True)
    monkeypatch.setattr(dbus, "SessionBus", lambda: bus)
    own_scope(99, "firefox.desktop")
    service, path, interface, method, signature, args = bus.calls[0]
    assert (service, path, method) == (
        "org.freedesktop.systemd1", "/org/freedesktop/systemd1", "StartTransientUnit")
    name, mode, properties, aux = args
    assert name == "app-tuxtalk-firefox.desktop-99.scope" and mode == "fail" and aux == []
    assert dict(properties) == {"PIDs": [99], "Slice": "app.slice"}
