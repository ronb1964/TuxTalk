"""Tests for install-type detection (installation) and the update path it picks.

The updater must know HOW TuxTalk was installed so it never tries to download
and run an AppImage on a .deb/.rpm/AUR/Flatpak install (that fails, e.g. missing
libfuse.so.2 on Fedora). get_install_type() is fully injectable so these tests
never touch the real environment.
"""
import subprocess

from tuxtalk import installation
from tuxtalk.installation import get_install_type
from tuxtalk.update_install import get_package_asset, package_install_argv
from tuxtalk.update_ui import get_update_guidance


def test_flatpak_detected_from_env():
    assert get_install_type(flatpak_env="io.github.ronb1964.TalkType",
                            appimage_env="", module_path="/app/lib/x.py") == "flatpak"


def test_aur_is_appimage_under_opt():
    # AUR ships the AppImage to /opt/talktype/TalkType.AppImage (pacman-managed)
    assert get_install_type(appimage_env="/opt/talktype/TalkType.AppImage",
                            module_path="/tmp/.mount_abc/usr/src/tuxtalk/u.py") == "aur"


def test_self_managed_appimage_under_home():
    assert get_install_type(appimage_env="/home/ron/AppImages/TalkType.AppImage",
                            module_path="/tmp/.mount_abc/usr/src/tuxtalk/u.py") == "appimage"


def test_an_appimage_variable_inherited_from_another_app_is_ignored():
    """A .deb install started from a terminal or editor that is itself an
    AppImage inherits that app's APPIMAGE. Taking it for TuxTalk's own made
    Uninstall delete the other app's file (review of 0.17)."""
    assert get_install_type(appimage_env="/home/ron/Apps/VSCodium.AppImage",
                            appdir_env="/tmp/.mount_VSCodiXYZ", flatpak_env="",
                            module_path="/opt/talktype/usr/src/tuxtalk/installation.py",
                            pkg_query=lambda: "deb") == "deb"


def test_an_extracted_appimage_counts_by_its_appdir():
    """--appimage-extract-and-run: no /.mount_ path, but the code is in APPDIR."""
    assert get_install_type(appimage_env="/home/ron/AppImages/TuxTalk.AppImage",
                            appdir_env="/tmp/appimage_extracted_abc", flatpak_env="",
                            module_path="/tmp/appimage_extracted_abc/usr/src/tuxtalk/u.py"
                            ) == "appimage"


def test_rpm_package_from_opt_tree():
    # .deb/.rpm ship the EXTRACTED tree; APPIMAGE is unset, source lives in /opt/talktype
    assert get_install_type(appimage_env="", flatpak_env="",
                            module_path="/opt/talktype/usr/src/tuxtalk/installation.py",
                            pkg_query=lambda: "rpm") == "rpm"


def test_deb_package_from_opt_tree():
    assert get_install_type(appimage_env="", flatpak_env="",
                            module_path="/opt/talktype/usr/src/tuxtalk/installation.py",
                            pkg_query=lambda: "deb") == "deb"


def test_generic_package_when_manager_unknown():
    assert get_install_type(appimage_env="", flatpak_env="",
                            module_path="/opt/talktype/usr/src/tuxtalk/installation.py",
                            pkg_query=lambda: None) == "package"


def test_dev_checkout():
    assert get_install_type(appimage_env="", flatpak_env="",
                            module_path="/home/ron/Projects/TalkType/src/tuxtalk/installation.py",
                            pkg_query=lambda: None) == "dev"


def test_guidance_appimage_swaps_in_place():
    g = get_update_guidance("appimage", "0.8.0")
    assert g["update_method"] == "appimage_swap"
    assert g["can_auto_update"] is True


def test_guidance_rpm_uses_pkexec_package():
    g = get_update_guidance("rpm", "0.8.0")
    assert g["update_method"] == "pkexec_package"
    assert g["can_auto_update"] is True
    assert "0.8.0" in g["message"]


def test_guidance_deb_uses_pkexec_package():
    g = get_update_guidance("deb", "0.8.0")
    assert g["update_method"] == "pkexec_package"
    assert g["can_auto_update"] is True


def test_guidance_aur_is_manual_with_helper_command():
    g = get_update_guidance("aur", "0.8.0")
    assert g["update_method"] == "manual"
    assert g["can_auto_update"] is False
    assert "tuxtalk-appimage" in g["command"]


def test_guidance_flatpak_is_manual_with_flatpak_command():
    g = get_update_guidance("flatpak", "0.8.0")
    assert g["update_method"] == "manual"
    assert "flatpak update" in g["command"]


def test_package_asset_picks_rpm():
    rel = {"rpm_url": "http://x/talktype-0.8.0-1.x86_64.rpm", "rpm_name": "talktype.rpm",
           "deb_url": "http://x/t.deb"}
    url, name = get_package_asset(rel, "rpm")
    assert url.endswith(".rpm") and name == "talktype.rpm"


def test_package_asset_picks_deb():
    rel = {"rpm_url": "http://x/t.rpm", "deb_url": "http://x/talktype_0.8.0_amd64.deb"}
    url, _ = get_package_asset(rel, "deb")
    assert url.endswith(".deb")


def test_install_argv_rpm_prefers_dnf():
    argv = package_install_argv("rpm", which=lambda c: "/usr/bin/" + c)
    assert argv == ["dnf", "install", "-y"]


def test_install_argv_rpm_falls_back_to_zypper():
    which = lambda c: "/usr/bin/zypper" if c == "zypper" else None
    argv = package_install_argv("rpm", which=which)
    assert argv == ["env", "ZYPP_LOCK_TIMEOUT=120", "zypper", "--non-interactive", "install",
                    "--allow-unsigned-rpm"]


def test_install_argv_deb_prefers_apt():
    argv = package_install_argv("deb", which=lambda c: "/usr/bin/" + c)
    assert argv == ["apt", "-o", "DPkg::Lock::Timeout=120", "install", "-y"]


def test_install_argv_deb_falls_back_to_dpkg():
    which = lambda c: "/usr/bin/dpkg" if c == "dpkg" else None
    argv = package_install_argv("deb", which=which)
    assert argv == ["dpkg", "-i"]


def test_install_argv_unsupported_returns_none():
    assert package_install_argv("aur", which=lambda c: None) is None
    assert package_install_argv("rpm", which=lambda c: None) is None


def test_apt_and_zypper_wait_for_the_desktops_own_updater():
    """Right after login PackageKit held zypper's lock in the openSUSE VM, and
    the update failed at once ("System management is locked"); dnf waits by
    itself."""
    everything = lambda command: "/usr/bin/" + command
    assert "DPkg::Lock::Timeout=120" in package_install_argv("deb", which=everything)
    only_zypper = lambda command: "/usr/bin/zypper" if command == "zypper" else None
    assert "ZYPP_LOCK_TIMEOUT=120" in package_install_argv("rpm", which=only_zypper)


def _type(path, **kw):
    return get_install_type(appimage_env="", flatpak_env="", appdir_env="", module_path=path, **kw)


def test_a_venv_in_the_home_folder_is_a_source_install():
    assert _type("/home/ann/.local/share/TuxTalk/venv/lib/python3.14/site-packages/"
                 "tuxtalk/installation.py") == "source"


def test_a_debian_style_venv_is_a_source_install_too():
    assert _type("/home/ann/env/lib/python3/dist-packages/tuxtalk/installation.py") == "source"


def test_tuxtalk_in_the_systems_site_packages_is_a_system_install():
    assert _type("/usr/lib/python3.14/site-packages/tuxtalk/installation.py") == "system"
    assert _type("/usr/lib/python3/dist-packages/tuxtalk/installation.py") == "system"


def test_pip_into_usr_local_is_not_taken_for_a_package():
    assert _type("/usr/local/lib/python3.14/site-packages/tuxtalk/installation.py") == "source"


def test_a_git_checkout_is_still_the_development_version():
    assert _type("/home/ron/Projects/TuxTalk/src/tuxtalk/installation.py") == "dev"


def test_an_appimage_and_a_flatpak_still_win_over_their_site_packages():
    assert get_install_type(
        appimage_env="/home/a/AppImages/TuxTalk.AppImage", appdir_env="", flatpak_env="",
        module_path="/tmp/.mount_x/usr/lib/python3.10/site-packages/tuxtalk/i.py") == "appimage"
    assert get_install_type(
        flatpak_env="io.github.ronb1964.TuxTalk", appimage_env="",
        module_path="/app/lib/python3.12/site-packages/tuxtalk/i.py") == "flatpak"


def test_pacman_names_the_package_that_owns_a_system_install():
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="tuxtalk\n", stderr="")
    path = "/usr/lib/python3.14/site-packages/tuxtalk/installation.py"
    assert installation._owning_package(
        path, run=run, which=lambda n: "/usr/bin/pacman") == "tuxtalk"
    assert calls[0][:2] == ["pacman", "-Qqo"] and calls[0][-1] == path


def test_without_pacman_or_its_ownership_there_is_no_known_owner():
    path = "/usr/lib/python3.14/site-packages/tuxtalk/installation.py"
    assert installation._owning_package(path, which=lambda n: None) is None

    def not_owned(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
    assert installation._owning_package(
        path, run=not_owned, which=lambda n: "/usr/bin/pacman") is None
