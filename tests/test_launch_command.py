"""Autostart must launch through a wrapper that sets up the bundled environment.

A packaged install ships python at /opt/talktype/usr/bin/python3, but that
interpreter has no PYTHONPATH/LD_LIBRARY_PATH/GI_TYPELIB_PATH of its own — the
/usr/bin/tuxtalk wrapper (via AppRun) sets those before launching. Writing the
raw `python3 -m tuxtalk.tray` into the autostart .desktop therefore produces a
command that dies silently at login while a terminal `tuxtalk` works.

The launcher must resolve to the wrapper the package actually installed. It is
looked up by KNOWN ABSOLUTE PATH, not `which`, so a stray ~/.local/bin/tuxtalk
earlier in PATH can never be written into autostart in place of ours.
"""

import types


def _make_prefs(
    monkeypatch,
    *,
    exists=(),
    which_map=None,
    executable="/opt/talktype/usr/bin/python3",
    appimage=None,
    dev_mode=False,
):
    """Return the tuxtalk.autostart module with a fake filesystem + which patched in.

    exists:    absolute paths that are present AND executable on this fake FS.
    which_map: name -> path the `which` fallback should resolve (must also be in
               `exists`, since the code re-checks isfile/access on the result).
    """
    from tuxtalk import autostart as mod
    from tuxtalk import config as config_mod

    # `from . import config` inside get_launch_command resolves to the real module.
    monkeypatch.setattr(config_mod, "DEV_MODE", dev_mode)
    monkeypatch.setattr(mod.sys, "executable", executable)
    if appimage is None:
        monkeypatch.delenv("APPIMAGE", raising=False)
    else:
        monkeypatch.setenv("APPIMAGE", appimage)

    exists = set(exists)
    which_map = which_map or {}

    def fake_which(argv, **kwargs):
        path = which_map.get(argv[1])
        return types.SimpleNamespace(
            returncode=0 if path else 1,
            stdout=f"{path}\n" if path else "",
        )

    monkeypatch.setattr(mod.subprocess, "run", fake_which)
    monkeypatch.setattr(mod.os.path, "isfile", lambda p: p in exists)
    monkeypatch.setattr(mod.os, "access", lambda p, m: p in exists)

    return mod


def test_a_deb_or_rpm_starts_through_usr_bin_tuxtalk(monkeypatch):
    """The packages ship /usr/bin/tuxtalk, and /usr/bin/talktype beside it
    for the command before the rename."""
    mod = _make_prefs(monkeypatch, exists={"/usr/bin/tuxtalk", "/usr/bin/talktype",
                                           "/usr/bin/dictate-tray"})
    assert mod.get_launch_command() == "/usr/bin/tuxtalk"


def test_a_package_from_before_the_rename_starts_through_talktype(monkeypatch):
    mod = _make_prefs(monkeypatch, exists={"/usr/bin/talktype", "/usr/bin/dictate-tray"})
    assert mod.get_launch_command() == "/usr/bin/talktype"


def test_falls_back_to_dictate_tray_when_no_talktype(monkeypatch):
    mod = _make_prefs(monkeypatch, exists={"/usr/bin/dictate-tray"})
    assert mod.get_launch_command() == "/usr/bin/dictate-tray"


def test_poisoned_path_does_not_win_over_packaged_launcher(monkeypatch):
    """A stray ~/.local/bin/tuxtalk earlier in PATH must NOT be chosen.

    This is the whole reason for looking up an absolute path instead of `which`:
    autostart would otherwise launch an unrelated binary at login.
    """
    mod = _make_prefs(
        monkeypatch,
        exists={"/usr/bin/tuxtalk", "/home/ron/.local/bin/tuxtalk"},
        which_map={"tuxtalk": "/home/ron/.local/bin/tuxtalk"},
    )
    assert mod.get_launch_command() == "/usr/bin/tuxtalk"


def test_which_fallback_only_for_nonstandard_layout(monkeypatch):
    """No launcher at /usr/bin, but one on PATH — the fallback still finds it."""
    mod = _make_prefs(
        monkeypatch,
        exists={"/opt/custom/tuxtalk"},
        which_map={"tuxtalk": "/opt/custom/tuxtalk"},
    )
    assert mod.get_launch_command() == "/opt/custom/tuxtalk"


def test_appimage_env_takes_precedence_over_usr_bin(monkeypatch):
    """The AppImage branch runs before the packaged-launcher lookup; keep it so.

    Guards against the absolute-path check being reordered above the AppImage
    case, which would make an installed .deb hijack an AppImage run.
    """
    app = "/home/ron/AppImages/TuxTalk-v0.19.0-x86_64.AppImage"
    mod = _make_prefs(monkeypatch, exists={app, "/usr/bin/tuxtalk"}, appimage=app)
    assert mod.get_launch_command() == app


def test_raw_python_only_as_last_resort(monkeypatch):
    """No launcher anywhere — the bare interpreter is the last resort."""
    mod = _make_prefs(monkeypatch, exists=set())
    assert mod.get_launch_command() == "/opt/talktype/usr/bin/python3 -m tuxtalk.tray"


DOWNLOAD = "/home/ron/Downloads/TuxTalk-v0.19.0-x86_64.AppImage"


def test_autostart_uses_the_installed_appimage_not_the_download(monkeypatch):
    """Setup installs the AppImage under ~/AppImages, so autostart must point
    there and not at ~/Downloads, or tidying Downloads silently stops TuxTalk
    starting at login."""
    mod = _make_prefs(monkeypatch, exists={DOWNLOAD}, appimage=DOWNLOAD)
    installed = mod.INSTALLED_APPIMAGE
    mod = _make_prefs(monkeypatch, exists={DOWNLOAD, installed}, appimage=DOWNLOAD)
    assert mod.get_launch_command() == installed


def test_setup_can_point_at_the_installed_copy_before_it_finishes_copying(monkeypatch):
    mod = _make_prefs(monkeypatch, exists={DOWNLOAD}, appimage=DOWNLOAD)
    assert mod.get_launch_command(prefer_installed_appimage=True) == mod.INSTALLED_APPIMAGE


def test_without_an_installed_copy_the_running_appimage_is_used(monkeypatch):
    mod = _make_prefs(monkeypatch, exists={DOWNLOAD}, appimage=DOWNLOAD)
    assert mod.get_launch_command() == DOWNLOAD


def test_onboarding_asks_for_the_installed_copy(first_run):
    assert ("starts at login", True) in first_run()


def test_an_appimage_in_a_folder_with_a_space_is_quoted_for_the_exec_line(monkeypatch):
    """An unquoted "/home/Jo Ann/..." makes the desktop look for a program
    called "/home/Jo", so TuxTalk silently never started at login."""
    spaced = "/home/Jo Ann/Downloads/TuxTalk-v0.19.0-x86_64.AppImage"
    mod = _make_prefs(monkeypatch, exists={spaced}, appimage=spaced)
    assert mod.get_launch_command() == f'"{spaced}"'
