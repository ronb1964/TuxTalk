"""Rename edge cases: the menu launcher, a pre-rename service, and folders and
AppImages that need care when they are moved."""
import os

from tuxtalk import update_checker as uc


def test_menu_launcher_points_at_the_installed_appimage(tmp_path, monkeypatch):
    # First run copies the AppImage to APPIMAGE_PATH (~/AppImages/TuxTalk.AppImage);
    # the launcher it writes must start that file, not the old TalkType name.
    monkeypatch.setattr(uc, "DESKTOP_FILE_PATH", str(tmp_path / "apps" / "tuxtalk.desktop"))
    monkeypatch.setattr(uc, "APPIMAGE_PATH", str(tmp_path / "AppImages" / "TuxTalk.AppImage"))
    monkeypatch.setattr(uc, "_install_app_icon", lambda: "tuxtalk")
    monkeypatch.setattr(uc, "_refresh_desktop_menu_cache", lambda: None)
    ok, _ = uc.create_desktop_launcher()
    assert ok
    text = (tmp_path / "apps" / "tuxtalk.desktop").read_text()
    assert f"Exec={tmp_path}/AppImages/TuxTalk.AppImage\n" in text


def test_a_talktype_service_left_running_by_the_update_is_recognised():
    # 0.14.x's in-app update execs the new tray without stopping the service,
    # which keeps running as "python3 -m talktype.app" and holds the lock.
    from tuxtalk.service_launcher import is_service_argv, is_pre_rename_service_argv
    assert is_service_argv(["/tmp/.mount_x/usr/bin/python3", "-m", "talktype.app"])
    assert is_pre_rename_service_argv(["python3", "-m", "talktype.app"])
    assert not is_pre_rename_service_argv(["python3", "-m", "tuxtalk.app"])
    assert not is_service_argv(["python3", "-m", "talktype.tray"])


# --- rename_migration: linked folders, mount points and the AppImage ---------
import errno  # noqa: E402

from tuxtalk import rename_migration as rm  # noqa: E402


def _mk(p, f="x"):
    os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, f), "w") as fh:
        fh.write("1")


def test_a_user_link_to_another_disk_is_followed_not_skipped(tmp_path):
    # Models moved to a bigger disk with a symlink is common. Skipping it as
    # "already migrated" would give an empty new install and re-download
    # everything.
    h = str(tmp_path / "home")
    _mk(str(tmp_path / "bigdisk" / "TalkType" / "models"), "m.bin")
    os.makedirs(f"{h}/.local/share")
    os.symlink(str(tmp_path / "bigdisk" / "TalkType"), f"{h}/.local/share/TalkType")
    rm.migrate(home=h)
    assert os.path.isfile(f"{h}/.local/share/TuxTalk/models/m.bin")
    assert os.path.isfile(f"{h}/.local/share/TalkType/models/m.bin")    # old path still works
    assert os.path.isfile(str(tmp_path / "bigdisk" / "TalkType" / "models" / "m.bin"))  # never moved


def test_a_mount_point_at_the_old_path_is_linked_not_lost(tmp_path, monkeypatch):
    h = str(tmp_path)
    _mk(f"{h}/.local/share/TalkType", "m.bin")

    def busy(a, b):
        raise OSError(errno.EBUSY, "Device or resource busy")
    monkeypatch.setattr(rm.os, "rename", busy)
    rm.migrate(home=h)
    assert os.path.isfile(f"{h}/.local/share/TuxTalk/m.bin")


def _old_entry(h, exec_path, enabled="true"):
    os.makedirs(f"{h}/.config/autostart", exist_ok=True)
    with open(f"{h}/.config/autostart/talktype.desktop", "w") as f:
        f.write(f"[Desktop Entry]\nName=TalkType\nExec={exec_path}\nX-GNOME-Autostart-enabled={enabled}\n")


def test_only_the_running_appimage_is_renamed(tmp_path):
    # Running a downloaded TuxTalk while 0.14.6 is installed must not turn the
    # old 0.14.6 file into "the installed TuxTalk".
    h = str(tmp_path)
    _mk(f"{h}/AppImages")
    with open(f"{h}/AppImages/TalkType.AppImage", "w") as f:
        f.write("0.14.6")
    _old_entry(h, f"{h}/AppImages/TalkType.AppImage")
    rm.adopt_old_install(home=h, running_appimage=f"{h}/Downloads/TuxTalk-v0.15.0-x86_64.AppImage")
    assert open(f"{h}/AppImages/TalkType.AppImage").read() == "0.14.6"
    assert not os.path.exists(f"{h}/AppImages/TuxTalk.AppImage")
    # ...and the login entry keeps starting a file that exists.
    assert f"Exec={h}/AppImages/TalkType.AppImage\n" in open(f"{h}/.config/autostart/tuxtalk.desktop").read()


def test_the_updated_appimage_renames_itself(tmp_path):
    h = str(tmp_path)
    _mk(f"{h}/AppImages")
    with open(f"{h}/AppImages/TalkType.AppImage", "w") as f:
        f.write("0.15.0")
    _old_entry(h, f"{h}/AppImages/TalkType.AppImage")
    rm.adopt_old_install(home=h, running_appimage=f"{h}/AppImages/TalkType.AppImage")
    assert open(f"{h}/AppImages/TuxTalk.AppImage").read() == "0.15.0"
    assert f"Exec={h}/AppImages/TuxTalk.AppImage\n" in open(f"{h}/.config/autostart/tuxtalk.desktop").read()


def test_an_aur_login_entry_points_at_the_new_opt_path(tmp_path, monkeypatch):
    h = str(tmp_path)
    _old_entry(h, "/opt/talktype/TalkType.AppImage")
    real_exists = os.path.exists
    monkeypatch.setattr(rm.os.path, "exists",
                        lambda p: p == "/opt/tuxtalk/TuxTalk.AppImage" or real_exists(p))
    rm.adopt_old_install(home=h, running_appimage="/opt/tuxtalk/TuxTalk.AppImage")
    assert "Exec=/opt/tuxtalk/TuxTalk.AppImage\n" in open(f"{h}/.config/autostart/tuxtalk.desktop").read()
