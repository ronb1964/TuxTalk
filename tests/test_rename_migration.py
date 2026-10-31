"""Moving a TalkType install's folders to their TuxTalk names (0.15.0).

Nothing may ever be lost: settings and multi-GB models move by rename, the old
name is left as a symlink so an old copy still installed keeps working, and
when both names exist or the folder is on another disk nothing is overwritten.
"""
import os

from tuxtalk import rename_migration as rm


def _mk(p, f="x"):
    os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, f), "w") as fh:
        fh.write("1")


def test_moves_old_folders_and_leaves_symlinks(tmp_path):
    h = str(tmp_path)
    _mk(f"{h}/.config/talktype", "config.toml")
    _mk(f"{h}/.local/share/TalkType/models", "m.bin")
    rm.migrate(home=h)
    assert os.path.isfile(f"{h}/.config/tuxtalk/config.toml")
    assert os.path.isfile(f"{h}/.local/share/TuxTalk/models/m.bin")
    assert os.path.islink(f"{h}/.config/talktype")
    assert os.path.realpath(f"{h}/.config/talktype") == os.path.realpath(f"{h}/.config/tuxtalk")


def test_dev_uses_dev_folders_only(tmp_path):
    h = str(tmp_path)
    _mk(f"{h}/.config/talktype-dev")
    _mk(f"{h}/.config/talktype")
    rm.migrate(home=h, dev=True)
    assert os.path.isdir(f"{h}/.config/tuxtalk-dev")
    assert not os.path.exists(f"{h}/.config/tuxtalk")      # production untouched


def test_both_exist_nothing_is_overwritten(tmp_path):
    h = str(tmp_path)
    _mk(f"{h}/.config/talktype", "old.toml")
    _mk(f"{h}/.config/tuxtalk", "new.toml")
    lines = rm.migrate(home=h)
    assert os.path.isfile(f"{h}/.config/talktype/old.toml")   # left alone
    assert os.path.isfile(f"{h}/.config/tuxtalk/new.toml")
    assert any("both exist" in line for line in lines)


def test_cross_device_falls_back_to_reverse_symlink(tmp_path, monkeypatch):
    h = str(tmp_path)
    _mk(f"{h}/.local/share/TalkType", "m.bin")

    def exdev(a, b):
        raise OSError(18, "Invalid cross-device link")
    monkeypatch.setattr(rm.os, "rename", exdev)
    rm.migrate(home=h)
    assert os.path.islink(f"{h}/.local/share/TuxTalk")
    assert os.path.isfile(f"{h}/.local/share/TuxTalk/m.bin")   # reached through the link
    assert os.path.isfile(f"{h}/.local/share/TalkType/m.bin")  # never copied, never moved


def test_a_run_after_the_other_disk_fallback_does_nothing(tmp_path, monkeypatch):
    """The new name linking to the old folder is the fallback working, not
    "both exist", so later starts leave it alone."""
    h = str(tmp_path)
    _mk(f"{h}/.local/share/TalkType", "m.bin")

    def exdev(a, b):
        raise OSError(18, "Invalid cross-device link")
    monkeypatch.setattr(rm.os, "rename", exdev)
    rm.migrate(home=h)
    assert rm.migrate(home=h) == []


def test_second_run_does_nothing(tmp_path):
    h = str(tmp_path)
    _mk(f"{h}/.config/talktype")
    rm.migrate(home=h)
    assert rm.migrate(home=h) == []


def test_nothing_old_nothing_done(tmp_path):
    assert rm.migrate(home=str(tmp_path)) == []


def test_new_folder_holding_only_our_log_still_takes_the_old_one(tmp_path):
    # The logger creates ~/.config/tuxtalk to write tuxtalk.log the moment any
    # module loads. That must not count as "both exist", or settings look lost.
    h = str(tmp_path)
    _mk(f"{h}/.config/talktype", "config.toml")
    _mk(f"{h}/.config/tuxtalk", "tuxtalk.log")
    rm.migrate(home=h)
    assert os.path.isfile(f"{h}/.config/tuxtalk/config.toml")
    assert os.path.isfile(f"{h}/.config/tuxtalk/tuxtalk.log")
    assert os.path.islink(f"{h}/.config/talktype")


def test_startup_run_respects_the_off_switches(tmp_path):
    h = str(tmp_path)
    _mk(f"{h}/.config/talktype")
    assert rm.run_at_startup({"TUXTALK_NO_MIGRATE": "1"}, home=h) == []
    assert rm.run_at_startup({"FLATPAK_ID": "io.github.ronb1964.TuxTalk"}, home=h) == []
    assert os.path.isdir(f"{h}/.config/talktype") and not os.path.islink(f"{h}/.config/talktype")
    assert rm.run_at_startup({}, home=h) == ["moved .config/talktype to .config/tuxtalk"]


def test_startup_run_picks_dev_folders_from_dev_mode(tmp_path):
    h = str(tmp_path)
    _mk(f"{h}/.config/talktype-dev")
    rm.run_at_startup({"DEV_MODE": "1"}, home=h)
    assert os.path.isdir(f"{h}/.config/tuxtalk-dev")


def test_package_import_runs_the_migration_before_anything_else(tmp_path):
    """Importing any TuxTalk module moves the old folders first, so the
    logger (or anything else) can't create the new ones ahead of the move."""
    import subprocess
    import sys
    h = str(tmp_path)
    _mk(f"{h}/.config/talktype", "config.toml")
    env = {k: v for k, v in os.environ.items()
           if k not in ("TUXTALK_NO_MIGRATE", "TUXTALK_LOG_DIR", "DEV_MODE")
           and not k.startswith("XDG_")}
    env["HOME"] = h
    subprocess.run([sys.executable, "-c",
                    "from tuxtalk.logger import setup_logger; setup_logger('tuxtalk.t').info('up')"],
                   env=env, check=True, timeout=60)
    assert os.path.isfile(f"{h}/.config/tuxtalk/config.toml")
    assert os.path.islink(f"{h}/.config/talktype")


def test_importing_the_migration_makes_no_settings_folder(tmp_path):
    """It opened the log file at import, making ~/.config/tuxtalk before it
    moved the old folder there."""
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items()
           if k not in ("TUXTALK_LOG_DIR", "DEV_MODE") and not k.startswith("XDG_")}
    env.update(HOME=str(tmp_path), TUXTALK_NO_MIGRATE="1")
    subprocess.run([sys.executable, "-c", "import tuxtalk.rename_migration"],
                   env=env, check=True, timeout=60)
    assert not os.path.lexists(f"{tmp_path}/.config/tuxtalk")


def test_settings_on_another_disk_are_found_after_a_start_wrote_its_log(tmp_path, monkeypatch):
    """~/.config/talktype on another disk, and a log already in the new
    folder: moving the log across raised, the move stopped, and TuxTalk
    started on default settings at every start."""
    import errno
    h = str(tmp_path)
    old, new = f"{h}/.config/talktype", f"{h}/.config/tuxtalk"
    _mk(old, "config.toml")
    _mk(new, "tuxtalk.log")
    real_rename = os.rename

    def rename(src, dst):
        if src.startswith(old + "/") != dst.startswith(old + "/") or src == old:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        real_rename(src, dst)
    monkeypatch.setattr(rm.os, "rename", rename)
    rm.migrate(home=h)
    assert os.path.islink(new) and os.path.realpath(new) == os.path.realpath(old)
    assert {"config.toml", "tuxtalk.log"} <= set(os.listdir(new))


# --- the one-time "TalkType is now TuxTalk" notice ----------------------------
# Shown once by the tray when this start converted an old TalkType install, on
# whichever TuxTalk version that happens to be (users skip releases).

def test_converting_the_settings_asks_for_the_notice(tmp_path):
    h = str(tmp_path)
    _mk(f"{h}/.config/talktype", "config.toml")
    rm.migrate(home=h)
    assert rm.notice_pending(f"{h}/.config/tuxtalk")


def test_a_settings_folder_on_another_disk_asks_too(tmp_path, monkeypatch):
    h = str(tmp_path)
    _mk(f"{h}/.config/talktype", "config.toml")

    def exdev(a, b):
        raise OSError(18, "Invalid cross-device link")
    monkeypatch.setattr(rm.os, "rename", exdev)
    rm.migrate(home=h)
    assert rm.notice_pending(f"{h}/.config/tuxtalk")


def test_dev_settings_ask_in_the_dev_folder(tmp_path):
    h = str(tmp_path)
    _mk(f"{h}/.config/talktype-dev", "config.toml")
    rm.migrate(home=h, dev=True)
    assert rm.notice_pending(f"{h}/.config/tuxtalk-dev")


def test_a_new_install_never_sees_it(tmp_path):
    h = str(tmp_path)
    _mk(f"{h}/.config/tuxtalk", "config.toml")
    rm.migrate(home=h)
    assert not rm.notice_pending(f"{h}/.config/tuxtalk")


def test_only_models_moving_is_not_a_conversion(tmp_path):
    """Settings are what make it someone's install; a stray models folder alone
    is not worth a message."""
    h = str(tmp_path)
    _mk(f"{h}/.local/share/TalkType", "m.bin")
    rm.migrate(home=h)
    assert not rm.notice_pending(f"{h}/.config/tuxtalk")


def test_the_notice_is_shown_once(tmp_path):
    h = str(tmp_path)
    _mk(f"{h}/.config/talktype", "config.toml")
    rm.migrate(home=h)
    rm.notice_shown(f"{h}/.config/tuxtalk")
    assert not rm.notice_pending(f"{h}/.config/tuxtalk")
    rm.migrate(home=h)                                  # the next start
    assert not rm.notice_pending(f"{h}/.config/tuxtalk")


def test_the_tray_shows_it(tray_main, tmp_path, monkeypatch):
    """Shown from the tray's start, once: the marker goes after it is seen."""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from gtk_text import tree_text
    from tuxtalk import config
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.toml"))
    rm._ask_for_notice(str(tmp_path))
    shown = []
    monkeypatch.setattr(Gtk.Dialog, "run",
                        lambda dialog: shown.append(tree_text(dialog)) or Gtk.ResponseType.OK)
    tray_main().run_timers()
    assert len(shown) == 1 and "TalkType is now TuxTalk" in shown[0]
    assert not rm.notice_pending(str(tmp_path))


def test_the_plasma_script_uses_its_own_bus_connection(monkeypatch):
    """On KDE the tray's D-Bus service failed on every first start: this ran
    dbus.SessionBus() before the tray set the GLib main loop, so the shared
    connection had none and exporting the service failed (fedora44-KDE test,
    2026-10-13). A private connection, closed after use, leaves it alone."""
    import sys
    import types
    from tuxtalk import rename_migration as rm
    calls = []

    class Bus:
        def __init__(self, **kwargs):
            calls.append(("open", kwargs))

        def get_object(self, *a):
            return types.SimpleNamespace(evaluateScript=lambda script, dbus_interface: "1")

        def close(self):
            calls.append(("close", {}))

    monkeypatch.setitem(sys.modules, "dbus", types.SimpleNamespace(SessionBus=Bus))
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert rm._default_plasma_runner()("print(1)") == "1"
    assert calls == [("open", {"private": True}), ("close", {})]


def test_preferences_sets_the_dbus_main_loop_before_anything_else(prefs_main):
    """0.15.0 testing: Test Hotkeys crashed on opening ("D-Bus connections must
    be attached to a main loop"). Something in Preferences opened the shared
    connection first, and the dialog's own DBusGMainLoop call can't reach a
    connection that already exists. main() must set it before the window."""
    started = prefs_main()
    assert started.index("sets the D-Bus main loop") < started.index("opens the window")
