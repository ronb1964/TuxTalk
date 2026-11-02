"""Two rules that share a theme: a slow operation must not act on stale state
or wait forever.

1. The daily update check spends up to ~30s on a network call, so it records
   its timestamp alone rather than writing back a Settings snapshot taken
   before the call, which would silently revert anything the user changed in
   Preferences meanwhile. Atomic writing prevents corruption, not clobbering by
   a stale snapshot.

2. Installing a .deb/.rpm through pkexec has a bounded wait, since a package
   lock (routine on Ubuntu with unattended-upgrades) would hang the update
   dialog forever. The child runs as root, a user process cannot kill it, and
   killing a package manager mid-transaction is far worse than waiting, so the
   process is left alone to finish.
"""

import io
import subprocess

import pytest

from tuxtalk import config as config_module


class TestRecordingTheUpdateCheck:
    def test_it_does_not_carry_other_settings_along(self, monkeypatch):
        """A setting changed during the network call must not be reverted."""
        saved = {}

        # The tray held model="small" before its ~30s network call; this is
        # what the user changed to in Preferences while it was running.
        on_disk = config_module.Settings(model="large-v3", auto_period=False)

        monkeypatch.setattr(config_module, "load_config", lambda: on_disk)
        monkeypatch.setattr(config_module, "save_config",
                            lambda s: saved.update(model=s.model,
                                                   auto_period=s.auto_period,
                                                   last_update_check=s.last_update_check))

        config_module.record_update_check("2026-09-05")

        assert saved["model"] == "large-v3", "the user's model choice was reverted"
        assert saved["auto_period"] is False
        assert saved["last_update_check"] == "2026-09-05"

    def test_a_failed_read_does_not_write(self, monkeypatch):
        """Losing a timestamp beats overwriting real settings with defaults."""
        def unreadable():
            raise OSError("config unreadable")

        monkeypatch.setattr(config_module, "load_config", unreadable)
        monkeypatch.setattr(config_module, "save_config",
                            lambda s: pytest.fail("must not save after a failed read"))

        config_module.record_update_check("2026-09-05")  # must not raise


class TestPackageInstallIsBounded:
    def _checker(self):
        from tuxtalk import update_install
        return update_install

    def test_a_hung_package_manager_returns_instead_of_blocking(self, monkeypatch, tmp_path):
        uc = self._checker()

        class HungProc:
            stdin = io.BytesIO()     # takes the root install script
            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired(cmd="pkexec", timeout=timeout)
            def kill(self):
                pytest.fail("a package manager must never be killed mid-transaction")

        monkeypatch.setattr(uc, "package_install_argv",
                            lambda t: ["apt", "install", "-y"])
        monkeypatch.setattr(uc.subprocess, "Popen", lambda *a, **k: HungProc())

        (tmp_path / "x.deb").write_bytes(b"package")
        download = uc.Downloaded(str(tmp_path / "x.deb"), "a" * 64, 7)
        ok, message = uc.install_package_update(download, "deb")

        assert ok is False
        assert "still" in message.lower() or "taking" in message.lower()

    def test_a_normal_install_still_succeeds(self, monkeypatch, tmp_path):
        uc = self._checker()

        class GoodProc:
            stdin = io.BytesIO()     # takes the root install script
            returncode = 0
            def wait(self, timeout=None):
                return self.returncode

        monkeypatch.setattr(uc, "package_install_argv",
                            lambda t: ["apt", "install", "-y"])
        monkeypatch.setattr(uc.subprocess, "Popen", lambda *a, **k: GoodProc())

        (tmp_path / "x.deb").write_bytes(b"package")
        download = uc.Downloaded(str(tmp_path / "x.deb"), "a" * 64, 7)
        ok, _ = uc.install_package_update(download, "deb")

        assert ok is True

    def test_a_cancelled_authorization_is_still_reported(self, monkeypatch, tmp_path):
        uc = self._checker()

        class CancelledProc:
            stdin = io.BytesIO()     # takes the root install script
            returncode = 126
            def wait(self, timeout=None):
                return self.returncode

        monkeypatch.setattr(uc, "package_install_argv",
                            lambda t: ["apt", "install", "-y"])
        monkeypatch.setattr(uc.subprocess, "Popen", lambda *a, **k: CancelledProc())

        (tmp_path / "x.deb").write_bytes(b"package")
        download = uc.Downloaded(str(tmp_path / "x.deb"), "a" * 64, 7)
        ok, message = uc.install_package_update(download, "deb")

        assert ok is False
        assert "cancel" in message.lower()


def test_the_daily_check_records_its_time_on_gtks_thread(monkeypatch):
    """Loaded and saved on the check's own thread, a setting the tray saved
    in the same moment (on GTK's thread) could be lost; on GTK's thread the
    two take turns."""
    import threading
    import time
    from tuxtalk import config, tray, update_checker, update_ui
    queued, recorded = [], []
    monkeypatch.setattr(config, "load_config",
                        lambda: config.Settings(auto_check_updates=True, last_update_check=""))
    monkeypatch.setattr(update_checker, "check_for_updates", lambda: {"success": True})
    monkeypatch.setattr(config, "record_update_check",
                        lambda ts: recorded.append(threading.current_thread()))
    monkeypatch.setattr(update_ui.GLib, "idle_add", lambda fn, *a: queued.append((fn, a)))
    t = tray.DictationTray.__new__(tray.DictationTray)
    t._quitting = False
    t.auto_check_for_updates()
    deadline = time.monotonic() + 5
    while not queued and time.monotonic() < deadline:
        time.sleep(0.01)
    assert recorded == []                       # not on the check's thread
    for fn, args in queued:                     # GTK's loop, here
        fn(*args)
    assert recorded == [threading.current_thread()]


def test_a_daily_check_that_failed_isnt_todays_and_tries_again_later(monkeypatch):
    """At login the Wi-Fi often isn't up yet: the failed check counted as
    the day's, at every login, and the update window never opened."""
    import threading
    from tuxtalk import config, update_checker, update_ui
    recorded, timers, checks = [], [], []
    monkeypatch.setattr(config, "load_config",
                        lambda: config.Settings(auto_check_updates=True, last_update_check=""))
    monkeypatch.setattr(config, "record_update_check", recorded.append)
    monkeypatch.setattr(update_checker, "check_for_updates", lambda: checks.append(1) or {
        "success": False, "error": "Could not connect to GitHub."})
    monkeypatch.setattr(update_ui.GLib, "timeout_add_seconds",
                        lambda seconds, fn: timers.append((seconds, fn)))

    class Now(threading.Thread):
        """The check's thread, run at once."""
        def start(self):
            self.run()
    monkeypatch.setattr(update_ui.threading, "Thread", lambda target, daemon: Now(target=target))
    update_ui.auto_check_for_updates(lambda result: None)
    while timers:
        seconds, again = timers.pop()
        assert seconds == update_ui.RETRY_S
        again()
    assert recorded == []
    assert len(checks) == 1 + update_ui.RETRIES


def test_a_screen_reader_can_name_the_update_command():
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from tuxtalk import update_ui
    box = Gtk.Box()
    update_ui._how_to_update(box, {"message": "Update with dnf:",
                                   "command": "sudo dnf upgrade tuxtalk"})
    entry = next(child for child in box.get_children() if isinstance(child, Gtk.Entry))
    assert entry.get_accessible().get_name() == "Command to update TuxTalk"
