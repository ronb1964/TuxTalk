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

from tuxtalk import desktop_launcher
from tuxtalk import installation, update_install
from tuxtalk import update_checker as uc


@pytest.fixture(autouse=True)
def _never_exec(monkeypatch):
    """Every install and restart here, from the tray, Preferences or D-Bus,
    fails the test if it ends in os.execv (the module docstring says why)."""
    monkeypatch.setattr(os, "execv", lambda *a: pytest.fail("must not exec"))


@pytest.fixture
def appimages(tmp_path, monkeypatch):
    """Point ~/AppImages, and the lock copies into it take turns on, at a temp folder."""
    monkeypatch.setattr("tuxtalk.single_instance.runtime_dir", lambda: str(tmp_path))
    folder = tmp_path / "AppImages"
    monkeypatch.setattr(installation, "APPIMAGE_DIR", str(folder))
    monkeypatch.setattr(installation, "APPIMAGE_PATH", str(folder / "TuxTalk.AppImage"))
    return folder


def _download(tmp_path, data=b"new version"):
    updates = tmp_path / "updates"
    updates.mkdir()
    path = updates / "TuxTalk-v9.9.9-x86_64.AppImage"
    path.write_bytes(data)
    return str(path)


# ---- installing ---------------------------------------------------------

def test_an_update_waits_for_the_first_run_copy_to_finish(tmp_path, appimages, monkeypatch):
    """The first-run copy (the version first run started from) finished after
    the update had gone in and replaced it: the restart started the old
    version again."""
    import shutil
    import threading
    copying, may_finish = threading.Event(), threading.Event()
    real_copy = shutil.copy2

    def slow_first_copy(src, dst, **kw):
        if "first-run" in str(src):
            copying.set()
            assert may_finish.wait(10)
        return real_copy(src, dst, **kw)
    monkeypatch.setattr(shutil, "copy2", slow_first_copy)
    first = tmp_path / "first-run.AppImage"
    first.write_bytes(b"old version")
    dest = str(appimages / "TuxTalk.AppImage")
    copy = threading.Thread(target=update_install._install_appimage_file, args=(str(first), dest))
    copy.start()
    assert copying.wait(10)
    update = threading.Thread(target=update_install._install_appimage_file,
                              args=(_download(tmp_path), dest))
    update.start()
    update.join(0.5)
    assert update.is_alive()                # waits its turn
    may_finish.set()
    for t in (copy, update):
        t.join(10)
    assert (appimages / "TuxTalk.AppImage").read_bytes() == b"new version"


def test_install_puts_the_update_in_place_without_restarting(tmp_path, appimages):
    dest = appimages / "TuxTalk.AppImage"
    appimages.mkdir()
    dest.write_bytes(b"old version")

    downloaded = _download(tmp_path)
    other = tmp_path / "updates" / "TuxTalk-v9.9.9-x86_64.AppImage.part"
    other.write_bytes(b"the other window's download, still going")

    ok, _ = update_install.install_appimage_update(downloaded)

    assert ok
    assert dest.read_bytes() == b"new version"
    assert os.access(dest, os.X_OK)
    assert (appimages / ".tuxtalk_just_updated").exists()
    assert sorted(p.name for p in appimages.iterdir()) == [
        ".tuxtalk_just_updated", "TuxTalk.AppImage"]
    assert not os.path.exists(downloaded), "the download was left behind"
    assert other.exists(), "a download going on in the same folder was deleted"


def test_a_failed_copy_keeps_the_working_appimage(tmp_path, appimages, monkeypatch):
    dest = appimages / "TuxTalk.AppImage"
    appimages.mkdir()
    dest.write_bytes(b"old version")

    def half_copy(src, dst):
        pathlib.Path(dst).write_bytes(b"new ver")
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(shutil, "copy2", half_copy)

    ok, message = update_install.install_appimage_update(_download(tmp_path))

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

    ok, _ = update_install.install_appimage_update(_download(tmp_path))

    assert ok
    assert not stale.exists()
    assert in_progress.exists(), "a copy still being written was deleted"


def test_install_refuses_when_the_disk_is_too_full(tmp_path, appimages, monkeypatch):
    monkeypatch.setattr("tuxtalk.download_utils.free_space_bytes", lambda p: 1024)
    ok, message = update_install.install_appimage_update(_download(tmp_path))
    assert not ok and "disk space" in message


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

    ok, _ = update_install.copy_appimage_to_standard_location()

    assert not ok
    assert list(appimages.iterdir()) == [], \
        "a truncated AppImage was left where the launcher points"


def test_first_run_copy_succeeds(tmp_path, appimages, monkeypatch):
    running = tmp_path / "TuxTalk-v9.9.9-x86_64.AppImage"
    running.write_bytes(b"x" * 1000)
    monkeypatch.setenv("APPIMAGE", str(running))

    ok, _ = update_install.copy_appimage_to_standard_location()

    dest = appimages / "TuxTalk.AppImage"
    assert ok and dest.read_bytes() == b"x" * 1000 and os.access(dest, os.X_OK)


def test_restart_now_waits_for_the_first_run_copy(monkeypatch):
    """Restart Now reboots only once the copy into ~/AppImages has finished,
    and waits through GLib timeouts instead of freezing the window."""
    import threading
    from tuxtalk import appimage_copy
    release = threading.Event()
    thread = threading.Thread(target=lambda: release.wait(5))
    thread.start()
    monkeypatch.setattr(appimage_copy, "_thread", thread)
    polls = []
    monkeypatch.setattr(appimage_copy.GLib, "timeout_add", lambda ms, fn: polls.append(fn))
    rebooted = []

    appimage_copy.after(lambda: rebooted.append(True), lambda: rebooted.append(False))
    assert rebooted == [] and len(polls) == 1
    assert polls[0]() is True              # still copying: keep waiting
    release.set()
    thread.join(5)
    assert polls[0]() is False
    assert rebooted == [True]


def test_restart_now_goes_straight_on_without_a_copy(monkeypatch):
    from tuxtalk import appimage_copy
    monkeypatch.setattr(appimage_copy, "_thread", None)
    rebooted = []
    appimage_copy.after(lambda: rebooted.append(True), lambda: rebooted.append(False))
    assert rebooted == [True]



def test_a_copy_still_going_after_the_wait_doesnt_reboot(monkeypatch):
    """A very slow disk: after two minutes it rebooted anyway, and the menu
    launcher pointed at a half-copied AppImage."""
    import threading
    from tuxtalk import appimage_copy
    release = threading.Event()
    thread = threading.Thread(target=lambda: release.wait(5))
    thread.start()
    monkeypatch.setattr(appimage_copy, "_thread", thread)
    polls, outcome = [], []
    monkeypatch.setattr(appimage_copy.GLib, "timeout_add", lambda ms, fn: polls.append(fn))
    appimage_copy.after(lambda: outcome.append("reboot"), lambda: outcome.append("told"),
                            timeout=0)
    assert polls[0]() is False and outcome == ["told"]
    release.set()
    thread.join(5)


def test_a_failed_copy_repoints_the_launchers_before_the_copy_counts_as_done(monkeypatch):
    """The repair went to GTK's queue and the copy's thread ended: Restart
    computer now saw it done and could reboot first, and the launchers
    pointed at an AppImage that wasn't there."""
    from tuxtalk import appimage_copy, update_install
    repaired = []
    monkeypatch.setattr(update_install, "copy_appimage_to_standard_location",
                        lambda: (False, "No space left on device"))
    monkeypatch.setattr(desktop_launcher, "point_at_running_appimage", repaired.append)
    monkeypatch.setattr(appimage_copy, "_thread", None)
    appimage_copy.start("/home/u/Downloads/TuxTalk.AppImage")
    appimage_copy._thread.join(5)
    assert not appimage_copy.copying()
    assert repaired == ["/home/u/Downloads/TuxTalk.AppImage"]

def test_first_run_copy_thread_is_not_a_daemon(first_run):
    """Quitting TuxTalk waits for the copy into ~/AppImages instead of
    cutting it off, and Restart computer now waits for it (below)."""
    from tuxtalk import appimage_copy
    copies = [step[1] for step in first_run() if step[0] == "copies the AppImage"]
    assert len(copies) == 1 and copies[0].daemon is False
    assert appimage_copy._thread is copies[0]



def test_first_run_writes_start_at_login_before_the_copy_begins(first_run):
    """A copy that failed at once (no room) pointed start-at-login back at
    the running AppImage, and first run, writing it afterwards, pointed it
    at the copy that never arrived."""
    steps = [step if isinstance(step, str) else step[0] for step in first_run()]
    assert steps.index("starts at login") < steps.index("copies the AppImage")

def test_the_restart_button_waits_for_the_first_run_copy(monkeypatch):
    """Setup Complete's Restart computer now goes through the wait above."""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from gtk_text import find
    from tuxtalk import appimage_copy
    from tuxtalk import first_run_setup
    from tuxtalk import setup_complete_dialog
    copying = types.SimpleNamespace(alive=True)
    copying.is_alive = lambda: copying.alive
    monkeypatch.setattr(appimage_copy, "_thread", copying)
    monkeypatch.setattr(first_run_setup, "mark_first_run_complete", lambda: None)
    rebooted, polls = [], []
    monkeypatch.setattr(appimage_copy, "reboot_system", lambda: rebooted.append(True) or True)
    monkeypatch.setattr(appimage_copy.GLib, "timeout_add", lambda ms, fn, *a: polls.append(fn))

    def run(dialog):
        if not isinstance(dialog, Gtk.MessageDialog):       # Setup Complete itself
            find(dialog, Gtk.Button, "Restart computer now").clicked()
        return Gtk.ResponseType.OK                          # and "Restart now?"
    monkeypatch.setattr(Gtk.Dialog, "run", run)
    setup_complete_dialog.show_setup_complete_dialog(needs_restart=True)
    assert rebooted == []
    copying.alive = False
    while polls and polls[0]():
        pass
    assert rebooted == [True]


def test_a_failed_copy_points_login_and_the_menu_back_at_the_running_appimage(
        monkeypatch, tmp_path):
    """A full disk stopped the first-run copy into ~/AppImages: the menu's
    launcher still started that file, and opened nothing."""
    from types import SimpleNamespace
    from tuxtalk import autostart, config
    calls = []
    # The setting says off (its save failed after the file was written):
    # the file is what starts TuxTalk at login.
    monkeypatch.setattr(config, "load_config", lambda: SimpleNamespace(launch_at_login=False))
    entry = tmp_path / "autostart" / "tuxtalk.desktop"
    entry.parent.mkdir()
    entry.write_text("[Desktop Entry]\nType=Application\nExec=/gone/TuxTalk.AppImage\n")
    monkeypatch.setattr(autostart, "_autostart_desktop_path", lambda: str(entry))
    monkeypatch.setattr(autostart, "set_autostart",
                        lambda enable, prefer_installed_appimage=False, command=None:
                        calls.append(command) or True)
    monkeypatch.setattr(desktop_launcher, "DESKTOP_FILE_PATH",
                        str(tmp_path / "apps" / "tuxtalk.desktop"))
    monkeypatch.setattr(desktop_launcher, "install_app_icon", lambda: "tuxtalk")
    monkeypatch.setattr(desktop_launcher.menu_cache, "refresh_menu_cache", lambda: None)
    running = str(tmp_path / "Downloads" / "TuxTalk-v0.19.0-x86_64.AppImage")
    desktop_launcher.point_at_running_appimage(running)
    assert calls == [running]           # by name: the installed copy may be an older one
    assert f"Exec={running}\n" in (tmp_path / "apps" / "tuxtalk.desktop").read_text()


def test_a_launcher_for_a_path_with_a_space_still_opens(monkeypatch, tmp_path):
    """A second download is "TuxTalk (1).AppImage": the menu entry and its
    right-click shortcuts were cut at the space and opened nothing."""
    monkeypatch.setattr(desktop_launcher, "DESKTOP_FILE_PATH",
                        str(tmp_path / "apps" / "tuxtalk.desktop"))
    monkeypatch.setattr(desktop_launcher, "install_app_icon", lambda: "tuxtalk")
    monkeypatch.setattr(desktop_launcher.menu_cache, "refresh_menu_cache", lambda: None)
    running = str(tmp_path / "Downloads" / "TuxTalk (1).AppImage")
    assert desktop_launcher.create_desktop_launcher(running)[0]
    lines = (tmp_path / "apps" / "tuxtalk.desktop").read_text().splitlines()
    execs = [line for line in lines if line.startswith("Exec=")]
    assert execs and all(line.startswith(f'Exec="{running}"') for line in execs)


def _repoint_fixture(monkeypatch, tmp_path, entry_text):
    """point_at_running_appimage with a real start-at-login file; its path."""
    from tuxtalk import autostart
    entry = tmp_path / "autostart" / "tuxtalk.desktop"
    entry.parent.mkdir()
    entry.write_text(entry_text)
    monkeypatch.setattr(autostart, "_autostart_desktop_path", lambda: str(entry))
    monkeypatch.setattr(desktop_launcher, "create_desktop_launcher", lambda running: (True, ""))
    return entry


def test_a_failed_copy_leaves_start_at_login_off_when_it_was_turned_off(monkeypatch, tmp_path):
    """Turned off in Preferences while the first-run copy ran, then the
    copy failed: the repair saw the file and turned it on again."""
    stub = "[Desktop Entry]\nType=Application\nName=TuxTalk\nHidden=true\n"
    entry = _repoint_fixture(monkeypatch, tmp_path, stub)
    desktop_launcher.point_at_running_appimage(str(tmp_path / "TuxTalk-v0.19.0-x86_64.AppImage"))
    assert entry.read_text() == stub


def test_a_repair_that_could_not_write_says_so(monkeypatch, tmp_path, caplog):
    from tuxtalk import autostart
    _repoint_fixture(monkeypatch, tmp_path, "[Desktop Entry]\nExec=/gone/TuxTalk.AppImage\n")
    monkeypatch.setattr(autostart, "set_autostart", lambda *a, **k: False)
    caplog.set_level("INFO")
    desktop_launcher.point_at_running_appimage(str(tmp_path / "TuxTalk-v0.19.0-x86_64.AppImage"))
    assert "Could not repoint launch at login" in caplog.text
    assert "Launch at login points at" not in caplog.text


# ---- restarting ---------------------------------------------------------

def test_the_new_copy_starts_only_once_the_old_tray_has_gone(tmp_path):
    """The old tray waits for its service to finish typing (up to 40 s); the
    script started the new copy after 10 s regardless, which found the old
    lock, exited, and left nothing running. Run for real, against a stand-in
    old tray."""
    import subprocess
    old = subprocess.Popen(["sleep", "11"])     # past the old script's 10 s
    marker = tmp_path / "new-copy-started"
    script = subprocess.Popen(["/bin/sh", "-c",
                               update_install.handover_script(old.pid, f"touch {marker}")])
    with pytest.raises(subprocess.TimeoutExpired):
        script.wait(timeout=10.5)               # still waiting for the old tray
    assert not marker.exists() and old.poll() is None
    old.wait()
    assert script.wait(timeout=5) == 0 and marker.exists()


def test_restart_starts_the_appimage_after_this_process_exits(tmp_path, monkeypatch):
    import subprocess
    appimage = tmp_path / "TuxTalk.AppImage"
    appimage.write_bytes(b"")
    appimage.chmod(0o755)
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: started.append((args, k)))

    ok, _ = update_install.restart_via_launcher(str(appimage))

    assert ok
    args, kwargs = started[0]
    script = args[-1]
    assert f"kill -0 {os.getpid()}" in script
    assert script.endswith(f"exec {appimage}")
    assert kwargs["start_new_session"] is True


def test_restart_refuses_a_missing_appimage(tmp_path):
    ok, _ = update_install.restart_via_launcher(str(tmp_path / "gone.AppImage"))
    assert not ok


class FakeTray:
    from tuxtalk import tray as _tray
    restart_app = _tray.DictationTray.restart_app

    def __init__(self):
        self.quit = 0
        self._quitting = False

    def quit_app(self, _):
        self.quit += 1


def test_tray_restart_hands_off_to_the_new_appimage(monkeypatch):
    targets = []
    monkeypatch.setattr(installation, "get_install_type", lambda: "appimage")
    monkeypatch.setattr(update_install, "restart_via_launcher",
                        lambda target=None: targets.append(target) or (True, "restarting"))
    tray = FakeTray()

    assert tray.restart_app() is False  # safe to schedule with GLib.idle_add
    assert targets == [installation.APPIMAGE_PATH]
    assert tray.quit == 1, "quit_app is what stops the old dictation service"


def test_tray_restart_uses_the_package_launcher(monkeypatch):
    targets = []
    monkeypatch.setattr(installation, "get_install_type", lambda: "rpm")
    monkeypatch.setattr(update_install, "restart_via_launcher",
                        lambda target=None: targets.append(target) or (True, "restarting"))
    FakeTray().restart_app()
    assert targets == [None]


def test_tray_keeps_running_when_it_cannot_restart(monkeypatch, message_boxes):
    monkeypatch.setattr(installation, "get_install_type", lambda: "appimage")
    monkeypatch.setattr(update_install, "restart_via_launcher",
                        lambda target=None: (False, "nope"))
    tray = FakeTray()

    tray.restart_app()

    assert tray.quit == 0
    assert [box.title for box in message_boxes.shown] == ["Update installed"], \
        "the user was not told to reopen TuxTalk"


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
    """The install (a copy of the whole AppImage) runs off the GTK thread:
    on it, the update's window and the tray menu froze. Not a daemon thread:
    a Quit's fallback exit waits for it."""
    import gi
    import threading
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from tuxtalk import tray
    steps = []
    monkeypatch.setattr(update_install, "download_update",
                        lambda *a, **k: "/updates/TuxTalk.AppImage")
    monkeypatch.setattr(update_install, "install_appimage_update",
                        lambda path, progress: steps.append(
        ("install", path, threading.current_thread() is threading.main_thread()))
        or (installed, "disk full"))
    monkeypatch.setattr(Gtk.Dialog, "run", lambda d: steps.append("error shown"))
    stub = types.SimpleNamespace(restart_app=lambda: steps.append("restart"), _quitting=False)
    stub._install_not_cut_off = (
        lambda install: tray.DictationTray._install_not_cut_off(stub, install))
    tray.DictationTray._download_update(stub, {"appimage_url": "https://x/TuxTalk.AppImage"})
    idle_queue(lambda: len(steps) >= 2)
    install = ("install", "/updates/TuxTalk.AppImage", False)
    assert steps == [install, "restart" if installed else "error shown"]


def test_the_tray_says_why_an_appimage_update_didnt_go_in(monkeypatch, idle_queue):
    """A download that fails says why (download_update tells only its progress
    line), and an install that raises says something went wrong, where the
    window used to close with no word."""
    from tuxtalk import tray, update_ui
    told = []
    monkeypatch.setattr(update_ui, "show_message",
                        lambda parent, kind, title, text, **k: told.append(text))
    stub = types.SimpleNamespace(restart_app=lambda: pytest.fail("restarted"), _quitting=False)
    stub._install_not_cut_off = (
        lambda install: tray.DictationTray._install_not_cut_off(stub, install))
    release = {"appimage_url": "https://x/TuxTalk.AppImage"}

    def checksum_mismatch(url, name, progress, **k):
        progress("Could not verify the update's integrity", 0)
    monkeypatch.setattr(update_install, "download_update", checksum_mismatch)
    tray.DictationTray._download_update(stub, release)
    idle_queue(lambda: told)
    assert told.pop().startswith("Could not verify the update's integrity.")

    def raises(path, progress):
        raise OSError("disk full")
    monkeypatch.setattr(update_install, "download_update",
                        lambda *a, **k: "/updates/TuxTalk.AppImage")
    monkeypatch.setattr(update_install, "install_appimage_update", raises)
    tray.DictationTray._download_update(stub, release)
    idle_queue(lambda: told)
    assert told.pop().startswith(update_ui.UNEXPECTED)


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
    stub = types.SimpleNamespace(restart_app=restarted.append, _quitting=False,
                                 _start_kwin_focus_provider=lambda: None)
    tray.DictationTray._init_dbus_service(stub)
    registered[0].restart_app()
    assert restarted == [None]


def test_preferences_asks_the_tray_to_restart(monkeypatch):
    from tuxtalk import update_ui
    asked = []
    iface = types.SimpleNamespace(RestartApp=lambda: asked.append(True))
    monkeypatch.setattr(update_ui, "show_message",
                        lambda *a, **k: pytest.fail("no fallback needed"))

    assert update_ui.restart_via_tray(lambda: iface) is True
    assert asked == [True]


def test_preferences_explains_when_the_tray_is_unreachable(monkeypatch):
    from tuxtalk import update_ui
    shown = []
    monkeypatch.setattr(update_ui, "show_message", lambda parent, kind, title, msg, **k:
                        shown.append(msg))

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
    tab = prefs_window().updates_tab
    tab._current_release = {"appimage_url": "https://x/TuxTalk.AppImage",
                            "html_url": "https://x/release"}
    tab.asked = asked
    return tab


def test_preferences_restarts_an_installed_appimage_through_the_tray(
        prefs_update, monkeypatch, idle_queue):
    monkeypatch.setattr(installation, "get_install_type", lambda: "appimage")
    monkeypatch.setattr(update_install, "download_update",
                        lambda *a, **k: "/updates/TuxTalk.AppImage")
    monkeypatch.setattr(update_install, "install_appimage_update",
                        lambda path, progress: (True, ""))
    prefs_update._on_download_update_clicked(None)
    idle_queue(lambda: prefs_update.asked)
    assert prefs_update.asked == ["restart"]


def test_preferences_says_why_its_appimage_update_failed(prefs_update, monkeypatch, idle_queue):
    """Preferences' Download & Install is the tray's flow too: a download that
    fails says why in Update Status and in a message, and nothing restarts."""
    from tuxtalk import update_ui
    told = []
    monkeypatch.setattr(installation, "get_install_type", lambda: "appimage")

    def checksum_mismatch(url, name, progress, **k):
        progress("Could not verify the update's integrity", 0)
    monkeypatch.setattr(update_install, "download_update", checksum_mismatch)
    monkeypatch.setattr(update_ui, "show_message",
                        lambda parent, kind, title, text, **k: told.append((title, text)))
    prefs_update._on_download_update_clicked(None)
    idle_queue(lambda: told)
    (title, text), = told
    assert title == "Update Failed" and text.startswith("Could not verify the update's integrity.")
    assert "Could not verify" in prefs_update.update_status_label.get_label()
    assert prefs_update.asked == []


def test_preferences_package_update_restarts_through_the_tray(prefs_update, monkeypatch):
    from tuxtalk import update_ui
    ran = []
    monkeypatch.setattr(installation, "get_install_type", lambda: "deb")
    monkeypatch.setattr(update_ui, "run_package_update",
                        lambda release, kind, restart: ran.append((kind, restart)))
    prefs_update._on_download_update_clicked(None)
    kind, restart = ran[0]
    assert kind == "deb"
    restart()
    assert prefs_update.asked == ["restart"]


@pytest.mark.parametrize("saved", [True, False])
def test_a_package_update_saves_what_isnt_saved_before_it_restarts(prefs_update, monkeypatch,
                                                                    saved):
    """The AppImage's Download & Install saves first; a .deb or .rpm install
    restarted with unsaved settings and snippets, and they were lost. A
    save that fails (it says why) keeps TuxTalk from restarting."""
    from tuxtalk import update_ui
    ran = []
    monkeypatch.setattr(installation, "get_install_type", lambda: "rpm")
    monkeypatch.setattr(update_ui, "run_package_update",
                        lambda release, kind, restart: ran.append(restart))
    monkeypatch.setattr(prefs_update.win, "save_before_restart",
                        lambda: prefs_update.asked.append("save") or saved)
    prefs_update._on_download_update_clicked(None)
    assert prefs_update.asked == []                 # nothing saved before "Restart Now"
    ran[0]()
    assert prefs_update.asked == (["save", "restart"] if saved else ["save"])


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

    monkeypatch.setattr(update_install, "package_install_argv",
                        lambda t, p: ["pkexec", "dpkg", "-i", p])
    monkeypatch.setattr(uc.subprocess, "Popen", lambda *a, **k: Proc())

    update_install.install_package_update(str(package), "deb")

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

    monkeypatch.setattr(update_install, "package_install_argv",
                        lambda t, p: ["pkexec", "dpkg", "-i", p])
    monkeypatch.setattr(uc.subprocess, "Popen", lambda *a, **k: SlowProc())

    ok, _ = update_install.install_package_update(str(package), "deb")

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

    monkeypatch.setattr(update_install, "package_install_argv",
                        lambda t, p: ["pkexec", "dpkg", "-i", p])
    monkeypatch.setattr(uc.subprocess, "Popen", popen)

    update_install.install_package_update(str(package), "deb")

    assert seen["stdout"] not in (subprocess.PIPE, None)
    assert seen["stderr"] == subprocess.STDOUT


def test_a_failed_install_reports_the_installer_last_lines(tmp_path, monkeypatch):
    package = _package(tmp_path)
    monkeypatch.setattr(update_install, "package_install_argv", lambda t, p: [
        "sh", "-c", "for i in $(seq 1 30); do echo progress $i; done; "
                    "echo 'E: broken dependency' >&2; exit 100"])

    ok, message = update_install.install_package_update(str(package), "deb")

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
    monkeypatch.setattr(update_install, "UPDATE_DIR", str(updates))
    monkeypatch.setattr("tuxtalk.download_utils.download_file", lambda *a, **k: False)

    update_install.download_update("https://example.invalid/x.deb", "tuxtalk_9.9.9_amd64.deb")

    assert not old.exists()
    assert recent.exists()


def test_download_progress_is_passed_on_once_per_percent(tmp_path, monkeypatch):
    monkeypatch.setattr(update_install, "UPDATE_DIR", str(tmp_path))
    total = 1000 * 65536

    def fake_download(url, dest, progress_hook=None, **k):
        for done in range(0, total + 1, 65536):
            progress_hook(done, total)
        pathlib.Path(dest).write_bytes(b"")
        return True
    monkeypatch.setattr("tuxtalk.download_utils.download_file", fake_download)
    seen = []

    update_install.download_update("https://example.invalid/x", "x.deb",
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

    monkeypatch.setattr(update_install, "package_install_argv",
                        lambda t, p: ["pkexec", "dpkg", "-i", p])
    monkeypatch.setattr(uc.subprocess, "Popen", lambda argv, stdout, stderr: Proc(stdout))

    ok, message = update_install.install_package_update(str(package), "deb")
    assert not ok
    assert expected in message


def test_an_unexpected_install_error_closes_the_update_window_and_says_why(monkeypatch):
    """The "Updating TuxTalk" window refuses to close while it works; an
    exception in the install left it on screen until TuxTalk quit."""
    pytest.importorskip("gi")
    from tuxtalk import update_ui

    monkeypatch.setattr(update_ui.update_install, "get_package_asset",
                        lambda release, kind: ("https://example.invalid/t.deb", "t.deb"))
    monkeypatch.setattr(update_ui.update_install, "download_update",
                        lambda *a, **k: "/tmp/t.deb")

    def broken_install(path, kind):
        raise OSError("no space left")
    monkeypatch.setattr(update_ui.update_install, "install_package_update", broken_install)
    _progress_in_place(monkeypatch)
    errors = []
    monkeypatch.setattr(update_ui, "_error", errors.append)

    update_ui.run_package_update({}, "deb")
    assert errors and "no space left" in errors[0]


def _progress_in_place(monkeypatch):
    """progress_window.run_with_progress with no window, its work run at
    once on this thread."""
    from tuxtalk import progress_window
    monkeypatch.setattr(progress_window, "_progress_window", lambda *a: (
        types.SimpleNamespace(destroy=lambda: None),
        types.SimpleNamespace(set_text=lambda t: None),
        types.SimpleNamespace(set_fraction=lambda f: None, set_text=lambda t: None)))
    monkeypatch.setattr(progress_window.GLib, "idle_add", lambda fn, *a: fn(*a))
    monkeypatch.setattr(progress_window, "threading", types.SimpleNamespace(
        Thread=lambda target, daemon=None, name=None: types.SimpleNamespace(start=target)))
