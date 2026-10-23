"""Installing an update and restarting into it.

Installing only installs, and one restart path in the tray stops the service,
quits and starts the new copy; Preferences asks the tray over D-Bus. Finishing
with os.execv on the new AppImage fails both ways:
- from Preferences (its own process), the new tray finds the old one still
  holding the single-instance lock and exits, so nothing restarts;
- from the tray, the tray restarts but keeps the old dictation service.

The first-run copy into ~/AppImages goes through a staging file: writing
straight onto the file the launcher and autostart point at would leave a
truncated AppImage there after a quit or reboot mid-copy.
"""
import os
import pathlib
import shutil
import types

import pytest

from tuxtalk import update_checker as uc

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "tuxtalk"


@pytest.fixture
def appimages(tmp_path, monkeypatch):
    """Point ~/AppImages at a temp folder."""
    folder = tmp_path / "AppImages"
    monkeypatch.setattr(uc, "APPIMAGE_DIR", str(folder))
    monkeypatch.setattr(uc, "APPIMAGE_PATH", str(folder / "TuxTalk.AppImage"))
    monkeypatch.setattr(os, "execv", lambda *a: pytest.fail("must not exec"))
    return folder


def _download(tmp_path, data=b"new version"):
    updates = tmp_path / "updates"
    updates.mkdir()
    path = updates / "TuxTalk-v9.9.9-x86_64.AppImage"
    path.write_bytes(data)
    return str(path)


# ---- installing ---------------------------------------------------------

def test_install_puts_the_update_in_place_without_restarting(tmp_path, appimages):
    dest = appimages / "TuxTalk.AppImage"
    appimages.mkdir()
    dest.write_bytes(b"old version")

    ok, _ = uc.install_appimage_update(_download(tmp_path))

    assert ok
    assert dest.read_bytes() == b"new version"
    assert os.access(dest, os.X_OK)
    assert (appimages / ".tuxtalk_just_updated").exists()
    assert sorted(p.name for p in appimages.iterdir()) == [
        ".tuxtalk_just_updated", "TuxTalk.AppImage"]
    assert not (tmp_path / "updates").exists(), "the download was left behind"


def test_a_failed_copy_keeps_the_working_appimage(tmp_path, appimages, monkeypatch):
    dest = appimages / "TuxTalk.AppImage"
    appimages.mkdir()
    dest.write_bytes(b"old version")

    def half_copy(src, dst):
        pathlib.Path(dst).write_bytes(b"new ver")
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(shutil, "copy2", half_copy)

    ok, message = uc.install_appimage_update(_download(tmp_path))

    assert not ok and "space" in message
    assert dest.read_bytes() == b"old version"
    assert [p.name for p in appimages.iterdir()] == ["TuxTalk.AppImage"], \
        "a partial copy was left in ~/AppImages"


def test_a_copy_cut_off_by_a_kill_is_swept_next_time(tmp_path, appimages):
    """A kill or reboot mid-copy skips the cleanup, and each staging name is
    random, so the next install removes old leftovers itself."""
    appimages.mkdir()
    stale = appimages / ".TuxTalk.AppImage.k1ll3d.part"
    stale.write_bytes(b"half a copy")
    hours_ago = os.path.getmtime(stale) - 3600
    os.utime(stale, (hours_ago, hours_ago))
    in_progress = appimages / ".TuxTalk.AppImage.busy01.part"
    in_progress.write_bytes(b"another copy still running")

    ok, _ = uc.install_appimage_update(_download(tmp_path))

    assert ok
    assert not stale.exists()
    assert in_progress.exists(), "a copy still being written was deleted"


def test_install_refuses_when_the_disk_is_too_full(tmp_path, appimages, monkeypatch):
    monkeypatch.setattr("tuxtalk.download_utils.free_space_bytes", lambda p: 1024)
    ok, message = uc.install_appimage_update(_download(tmp_path))
    assert not ok and "disk space" in message


def test_no_update_path_execs_the_appimage():
    """execv from Preferences or a worker thread breaks the restart."""
    for path in SRC.rglob("*.py"):
        assert "os.execv(" not in path.read_text(), path.name
    assert not hasattr(uc, "install_update_and_restart")


# ---- first-run copy -----------------------------------------------------

def test_first_run_copy_is_atomic(tmp_path, appimages, monkeypatch):
    running = tmp_path / "Downloads" / "TuxTalk-v9.9.9-x86_64.AppImage"
    running.parent.mkdir()
    running.write_bytes(b"x" * 1000)
    monkeypatch.setenv("APPIMAGE", str(running))

    def half_copy(src, dst):
        pathlib.Path(dst).write_bytes(b"x" * 10)
        raise OSError("interrupted")
    monkeypatch.setattr(shutil, "copy2", half_copy)

    ok, _ = uc.copy_appimage_to_standard_location()

    assert not ok
    assert list(appimages.iterdir()) == [], \
        "a truncated AppImage was left where the launcher points"


def test_first_run_copy_succeeds(tmp_path, appimages, monkeypatch):
    running = tmp_path / "TuxTalk-v9.9.9-x86_64.AppImage"
    running.write_bytes(b"x" * 1000)
    monkeypatch.setenv("APPIMAGE", str(running))

    ok, _ = uc.copy_appimage_to_standard_location()

    dest = appimages / "TuxTalk.AppImage"
    assert ok and dest.read_bytes() == b"x" * 1000 and os.access(dest, os.X_OK)


def test_restart_now_waits_for_the_first_run_copy(monkeypatch):
    """Restart Now reboots only once the copy into ~/AppImages has finished,
    and waits through GLib timeouts instead of freezing the window."""
    import threading
    from tuxtalk import welcome_dialog as wd
    release = threading.Event()
    thread = threading.Thread(target=lambda: release.wait(5))
    thread.start()
    monkeypatch.setattr(wd, "_appimage_copy_thread", thread)
    polls = []
    monkeypatch.setattr(wd.GLib, "timeout_add", lambda ms, fn: polls.append(fn))
    rebooted = []

    wd._after_appimage_copy(lambda: rebooted.append(True))
    assert rebooted == [] and len(polls) == 1
    assert polls[0]() is True              # still copying: keep waiting
    release.set()
    thread.join(5)
    assert polls[0]() is False
    assert rebooted == [True]


def test_restart_now_goes_straight_on_without_a_copy(monkeypatch):
    from tuxtalk import welcome_dialog as wd
    monkeypatch.setattr(wd, "_appimage_copy_thread", None)
    rebooted = []
    wd._after_appimage_copy(lambda: rebooted.append(True))
    assert rebooted == [True]


def test_first_run_copy_thread_is_not_a_daemon(first_run):
    """Quitting TuxTalk waits for the copy into ~/AppImages instead of
    cutting it off, and Restart computer now waits for it (below)."""
    from tuxtalk import welcome_dialog as wd
    copies = [step[1] for step in first_run() if step[0] == "copies the AppImage"]
    assert len(copies) == 1 and copies[0].daemon is False
    assert wd._appimage_copy_thread is copies[0]


def test_the_restart_button_waits_for_the_first_run_copy(monkeypatch):
    """Setup Complete's Restart computer now goes through the wait above."""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from gtk_text import find
    from tuxtalk import cuda_helper, uinput_helper
    from tuxtalk import welcome_dialog as wd
    copying = types.SimpleNamespace(alive=True)
    copying.is_alive = lambda: copying.alive
    monkeypatch.setattr(wd, "_appimage_copy_thread", copying)
    monkeypatch.setattr(cuda_helper, "mark_first_run_complete", lambda: None)
    rebooted, polls = [], []
    monkeypatch.setattr(uinput_helper, "reboot_system", lambda: rebooted.append(True) or True)
    monkeypatch.setattr(wd.GLib, "timeout_add", lambda ms, fn, *a: polls.append(fn))

    def run(dialog):
        if not isinstance(dialog, Gtk.MessageDialog):       # Setup Complete itself
            find(dialog, Gtk.Button, "Restart computer now").clicked()
        return Gtk.ResponseType.OK                          # and "Restart now?"
    monkeypatch.setattr(Gtk.Dialog, "run", run)
    wd.show_setup_complete_dialog(needs_restart=True)
    assert rebooted == []
    copying.alive = False
    while polls and polls[0]():
        pass
    assert rebooted == [True]


def test_a_failed_copy_points_login_back_at_the_running_appimage(monkeypatch):
    from types import SimpleNamespace
    from tuxtalk import autostart, config
    from tuxtalk import welcome_dialog as wd
    calls = []
    monkeypatch.setattr(config, "load_config", lambda: SimpleNamespace(launch_at_login=True))
    monkeypatch.setattr(autostart, "set_autostart",
                        lambda enable, prefer_installed_appimage=False:
                        calls.append(prefer_installed_appimage) or True)
    assert wd._autostart_running_appimage() is False
    assert calls == [False]


# ---- restarting ---------------------------------------------------------

def test_restart_starts_the_appimage_after_this_process_exits(tmp_path, monkeypatch):
    import subprocess
    appimage = tmp_path / "TuxTalk.AppImage"
    appimage.write_bytes(b"")
    appimage.chmod(0o755)
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: started.append((args, k)))

    ok, _ = uc.restart_via_launcher(str(appimage))

    assert ok
    args, kwargs = started[0]
    script = args[-1]
    assert f"kill -0 {os.getpid()}" in script
    assert script.endswith(f"exec {appimage}")
    assert kwargs["start_new_session"] is True


def test_restart_refuses_a_missing_appimage(tmp_path):
    ok, _ = uc.restart_via_launcher(str(tmp_path / "gone.AppImage"))
    assert not ok


class FakeTray:
    from tuxtalk import tray as _tray
    restart_app = _tray.DictationTray.restart_app

    def __init__(self):
        self.quit = 0

    def quit_app(self, _):
        self.quit += 1


def test_tray_restart_hands_off_to_the_new_appimage(monkeypatch):
    targets = []
    monkeypatch.setattr(uc, "get_install_type", lambda: "appimage")
    monkeypatch.setattr(uc, "restart_via_launcher",
                        lambda target=None: targets.append(target) or (True, "restarting"))
    tray = FakeTray()

    assert tray.restart_app() is False  # safe to schedule with GLib.idle_add
    assert targets == [uc.APPIMAGE_PATH]
    assert tray.quit == 1, "quit_app is what stops the old dictation service"


def test_tray_restart_uses_the_package_launcher(monkeypatch):
    targets = []
    monkeypatch.setattr(uc, "get_install_type", lambda: "rpm")
    monkeypatch.setattr(uc, "restart_via_launcher",
                        lambda target=None: targets.append(target) or (True, "restarting"))
    FakeTray().restart_app()
    assert targets == [None]


def test_tray_keeps_running_when_it_cannot_restart(monkeypatch):
    from tuxtalk import tray as tray_mod
    shown = []
    monkeypatch.setattr(uc, "get_install_type", lambda: "appimage")
    monkeypatch.setattr(uc, "restart_via_launcher", lambda target=None: (False, "nope"))
    monkeypatch.setattr(tray_mod, "message_dialog", lambda **k: shown.append(k) or types.SimpleNamespace(
        format_secondary_text=lambda t: None, set_position=lambda p: None,
        set_keep_above=lambda b: None, connect=lambda *a: None, show_all=lambda: None))
    tray = FakeTray()

    tray.restart_app()

    assert tray.quit == 0
    assert shown, "the user was not told to reopen TuxTalk"


@pytest.fixture
def idle_queue(monkeypatch):
    """GLib.idle_add callbacks from an update's download thread, run on this
    thread by run_until(), as the main loop would."""
    import queue
    from gi.repository import GLib
    waiting = queue.Queue()
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *args: waiting.put((fn, args)))

    def run_until(done):
        while not done():
            fn, args = waiting.get(timeout=5)
            fn(*args)
    return run_until


@pytest.mark.parametrize("installed", [True, False])
def test_tray_download_install_restarts_through_restart_app(monkeypatch, idle_queue, installed):
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from tuxtalk import tray
    steps = []
    monkeypatch.setattr(uc, "download_update", lambda *a, **k: "/updates/TuxTalk.AppImage")
    monkeypatch.setattr(uc, "install_appimage_update",
                        lambda path: steps.append(("install", path)) or (installed, "disk full"))
    monkeypatch.setattr(Gtk.Dialog, "run", lambda d: steps.append("error shown"))
    stub = types.SimpleNamespace(restart_app=lambda: steps.append("restart"))
    tray.DictationTray._download_update(stub, {"appimage_url": "https://x/TuxTalk.AppImage"})
    idle_queue(lambda: len(steps) >= 2)
    if installed:
        assert steps == [("install", "/updates/TuxTalk.AppImage"), "restart"]
    else:
        assert steps == [("install", "/updates/TuxTalk.AppImage"), "error shown"]


# ---- Preferences asks the tray ------------------------------------------

def test_dbus_restart_app_reaches_the_tray():
    from tuxtalk.dbus_service import TuxTalkDBusService
    calls = []
    fake = types.SimpleNamespace(_dispatch=lambda name, *a: calls.append(name))
    TuxTalkDBusService.RestartApp(fake)
    assert calls == ["restart_app"]



def test_the_tray_answers_restart_app(monkeypatch):
    from tuxtalk import tray
    registered, restarted = [], []
    monkeypatch.setattr(tray, "DBUS_AVAILABLE", True)
    monkeypatch.setattr(tray, "TuxTalkDBusService", registered.append)
    monkeypatch.setattr(tray.GLib, "idle_add", lambda fn, *args: fn(*args))
    stub = types.SimpleNamespace(restart_app=restarted.append,
                                 _start_kwin_focus_provider=lambda: None)
    tray.DictationTray._init_dbus_service(stub)
    registered[0].restart_app()
    assert restarted == [None]


def test_preferences_asks_the_tray_to_restart(monkeypatch):
    from tuxtalk import update_ui
    asked = []
    iface = types.SimpleNamespace(RestartApp=lambda: asked.append(True))
    monkeypatch.setattr(update_ui, "_info", lambda *a: pytest.fail("no fallback needed"))

    assert update_ui.restart_via_tray(lambda: iface) is True
    assert asked == [True]


def test_preferences_explains_when_the_tray_is_unreachable(monkeypatch):
    from tuxtalk import update_ui
    shown = []
    monkeypatch.setattr(update_ui, "_info", lambda title, msg: shown.append(msg))

    def unreachable():
        raise RuntimeError("org.freedesktop.DBus.Error.ServiceUnknown")

    assert update_ui.restart_via_tray(unreachable) is False
    assert shown and "open it" in shown[0]


@pytest.fixture
def prefs_update(prefs_window, monkeypatch):
    """Preferences with an update found, its dialogs answered Yes, and the
    tray's restart recorded."""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from tuxtalk import update_ui
    asked = []
    monkeypatch.setattr(Gtk.Dialog, "run", lambda d: Gtk.ResponseType.YES)
    monkeypatch.setattr(update_ui, "restart_via_tray", lambda: asked.append("restart"))
    window = prefs_window()
    window._current_release = {"appimage_url": "https://x/TuxTalk.AppImage",
                               "html_url": "https://x/release"}
    window.asked = asked
    return window


def test_preferences_restarts_an_installed_appimage_through_the_tray(
        prefs_update, monkeypatch, idle_queue):
    monkeypatch.setattr(uc, "get_install_type", lambda: "appimage")
    monkeypatch.setattr(uc, "download_update", lambda *a, **k: "/updates/TuxTalk.AppImage")
    monkeypatch.setattr(uc, "install_appimage_update", lambda path, progress: (True, ""))
    prefs_update._on_download_update_clicked(None)
    idle_queue(lambda: prefs_update.asked)
    assert prefs_update.asked == ["restart"]


def test_preferences_package_update_restarts_through_the_tray(prefs_update, monkeypatch):
    from tuxtalk import update_ui
    ran = []
    monkeypatch.setattr(uc, "get_install_type", lambda: "deb")
    monkeypatch.setattr(update_ui, "run_package_update",
                        lambda release, kind, restart_callback: ran.append((kind, restart_callback)))
    prefs_update._on_download_update_clicked(None)
    kind, restart = ran[0]
    assert kind == "deb"
    restart()
    assert prefs_update.asked == ["restart"]


# ---- package updates ----------------------------------------------------

def _package(tmp_path):
    path = tmp_path / "tuxtalk_9.9.9_amd64.deb"
    path.write_bytes(b"package")
    return path


@pytest.mark.parametrize("returncode", [0, 1, 126])
def test_the_package_is_deleted_once_installed_or_refused(tmp_path, monkeypatch, returncode):
    package = _package(tmp_path)

    class Proc:
        def __init__(self):
            self.returncode = returncode

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(uc, "package_install_argv", lambda t, p: ["pkexec", "dpkg", "-i", p])
    monkeypatch.setattr(uc.subprocess, "Popen", lambda *a, **k: Proc())

    uc.install_package_update(str(package), "deb")

    assert not package.exists(), "a package hundreds of MB big was left behind"


def test_a_slow_installer_keeps_its_package_until_it_finishes(tmp_path, monkeypatch):
    import subprocess
    import threading
    package = _package(tmp_path)
    finish = threading.Event()
    finished = threading.Event()

    class SlowProc:
        returncode = None

        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="pkexec", timeout=timeout)
            finish.wait(5)
            self.returncode = 0
            finished.set()
            return 0

        def kill(self):
            pytest.fail("a package manager must never be killed mid-transaction")

    monkeypatch.setattr(uc, "package_install_argv", lambda t, p: ["pkexec", "dpkg", "-i", p])
    monkeypatch.setattr(uc.subprocess, "Popen", lambda *a, **k: SlowProc())

    ok, _ = uc.install_package_update(str(package), "deb")

    assert not ok
    assert package.exists(), "the installer may still be reading the package"
    finish.set()
    assert finished.wait(5), "nothing waited for the installer"
    for _ in range(100):
        if not package.exists():
            break
        threading.Event().wait(0.02)
    assert not package.exists()


def test_the_installer_output_does_not_go_through_a_pipe(tmp_path, monkeypatch):
    """A pipe closes when TuxTalk exits, and the installer's next write would
    kill it mid-transaction. Its output goes to a file instead."""
    import subprocess
    package = _package(tmp_path)
    seen = {}

    class Proc:
        returncode = 0

        def wait(self, timeout=None):
            return 0

    def popen(argv, **kwargs):
        seen.update(kwargs)
        return Proc()

    monkeypatch.setattr(uc, "package_install_argv", lambda t, p: ["pkexec", "dpkg", "-i", p])
    monkeypatch.setattr(uc.subprocess, "Popen", popen)

    uc.install_package_update(str(package), "deb")

    assert seen["stdout"] not in (subprocess.PIPE, None)
    assert seen["stderr"] == subprocess.STDOUT


def test_a_failed_install_reports_the_installer_last_lines(tmp_path, monkeypatch):
    package = _package(tmp_path)
    monkeypatch.setattr(uc, "package_install_argv", lambda t, p: [
        "sh", "-c", "for i in $(seq 1 30); do echo progress $i; done; "
                    "echo 'E: broken dependency' >&2; exit 100"])

    ok, message = uc.install_package_update(str(package), "deb")

    assert not ok
    assert "E: broken dependency" in message
    assert "progress 1\n" not in message, "pages of progress buried the error"
    assert not package.exists()


def test_a_package_left_by_an_earlier_install_is_swept(tmp_path, monkeypatch):
    """An installer that outlives TuxTalk can't have its package deleted by
    it, so the next update download removes old leftovers."""
    updates = tmp_path / "updates"
    updates.mkdir()
    old = updates / "tuxtalk_9.9.8_amd64.deb"
    old.write_bytes(b"old package")
    hours_ago = os.path.getmtime(old) - 2 * 3600
    os.utime(old, (hours_ago, hours_ago))
    recent = updates / "tuxtalk_9.9.9_amd64.deb.abc.tmp"
    recent.write_bytes(b"another download in progress")
    monkeypatch.setattr(uc, "UPDATE_DIR", str(updates))
    monkeypatch.setattr("tuxtalk.download_utils.download_file", lambda *a, **k: False)

    uc.download_update("https://example.invalid/x.deb", "tuxtalk_9.9.9_amd64.deb")

    assert not old.exists()
    assert recent.exists()


def test_download_progress_is_passed_on_once_per_percent(tmp_path, monkeypatch):
    monkeypatch.setattr(uc, "UPDATE_DIR", str(tmp_path))
    total = 1000 * 65536

    def fake_download(url, dest, progress_hook=None, **k):
        for done in range(0, total + 1, 65536):
            progress_hook(done, total)
        pathlib.Path(dest).write_bytes(b"")
        return True
    monkeypatch.setattr("tuxtalk.download_utils.download_file", fake_download)
    seen = []

    uc.download_update("https://example.invalid/x", "x.deb",
                       lambda msg, pct: seen.append(pct))

    downloading = [p for p in seen if 0 <= p <= 100]
    assert len(downloading) <= 103, f"{len(seen)} progress callbacks for one download"
    assert 100 in seen


@pytest.mark.parametrize("returncode,said,expected", [
    (126, "", "cancelled"),
    # Several polkit agents report a cancelled prompt as a refusal.
    (127, "Error executing command as another user: Not authorized\n\n"
          "This incident has been reported.", "cancelled"),
    (127, "pkexec: cannot run program", "Could not start the installer"),
])
def test_a_cancelled_password_prompt_says_cancelled(tmp_path, monkeypatch, returncode, said,
                                                    expected):
    package = _package(tmp_path)

    class Proc:
        def __init__(self, stdout):
            stdout.write(said.encode())
            self.returncode = returncode

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(uc, "package_install_argv", lambda t, p: ["pkexec", "dpkg", "-i", p])
    monkeypatch.setattr(uc.subprocess, "Popen", lambda argv, stdout, stderr: Proc(stdout))

    ok, message = uc.install_package_update(str(package), "deb")
    assert not ok
    assert expected in message


def test_an_unexpected_install_error_closes_the_update_window_and_says_why(monkeypatch):
    """The "Updating TuxTalk" window refuses to close while it works; an
    exception in the install left it on screen until TuxTalk quit."""
    pytest.importorskip("gi")
    from tuxtalk import update_ui

    monkeypatch.setattr(update_ui.update_checker, "get_package_asset",
                        lambda release, kind: ("https://example.invalid/t.deb", "t.deb"))
    monkeypatch.setattr(update_ui.update_checker, "download_update",
                        lambda *a, **k: "/tmp/t.deb")

    def broken_install(path, kind):
        raise OSError("no space left")
    monkeypatch.setattr(update_ui.update_checker, "install_package_update", broken_install)
    monkeypatch.setattr(update_ui.GLib, "idle_add", lambda fn, *a: fn(*a))
    monkeypatch.setattr(update_ui.threading, "Thread",
                        lambda target, daemon: type("T", (), {"start": lambda self: target()})())
    errors = []
    monkeypatch.setattr(update_ui, "_error", errors.append)

    update_ui.run_package_update({}, "deb")
    assert errors and "no space left" in errors[0]
