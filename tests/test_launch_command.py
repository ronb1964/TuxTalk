"""Autostart must launch through a wrapper that sets up the bundled environment.

A packaged install ships python at /opt/talktype/usr/bin/python3, but that
interpreter has no PYTHONPATH/LD_LIBRARY_PATH/GI_TYPELIB_PATH of its own — the
/usr/bin/talktype wrapper (via AppRun) sets those before launching. Writing the
raw `python3 -m tuxtalk.tray` into the autostart .desktop therefore produces a
command that dies silently at login while a terminal `talktype` works.

The launcher must resolve to the wrapper the package actually installed. It is
looked up by KNOWN ABSOLUTE PATH, not `which`, so a stray ~/.local/bin/talktype
earlier in PATH can never be written into autostart in place of ours.
"""

import types

import pytest


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
    monkeypatch.setattr(config_mod, "DEV_MODE", dev_mode, raising=False)
    monkeypatch.setattr(mod.sys, "executable", executable, raising=False)
    if appimage is None:
        monkeypatch.delenv("APPIMAGE", raising=False)
    else:
        monkeypatch.setenv("APPIMAGE", appimage)

    exists = set(exists)
    which_map = which_map or {}

    def fake_which(argv, capture_output=True, text=True, timeout=5):
        path = which_map.get(argv[1])
        return types.SimpleNamespace(
            returncode=0 if path else 1,
            stdout=f"{path}\n" if path else "",
        )

    monkeypatch.setattr(mod.subprocess, "run", fake_which)
    monkeypatch.setattr(mod.os.path, "isfile", lambda p: p in exists)
    monkeypatch.setattr(mod.os, "access", lambda p, m: p in exists)

    return mod


def test_prefers_usr_bin_talktype(monkeypatch):
    mod = _make_prefs(monkeypatch, exists={"/usr/bin/talktype", "/usr/bin/dictate-tray"})
    cmd = mod.get_launch_command()
    assert cmd == "/usr/bin/talktype"
    assert "python3 -m tuxtalk.tray" not in cmd


def test_talktype_wins_over_dictate_tray(monkeypatch):
    mod = _make_prefs(monkeypatch, exists={"/usr/bin/dictate-tray", "/usr/bin/talktype"})
    assert mod.get_launch_command() == "/usr/bin/talktype"


def test_falls_back_to_dictate_tray_when_no_talktype(monkeypatch):
    mod = _make_prefs(monkeypatch, exists={"/usr/bin/dictate-tray"})
    assert mod.get_launch_command() == "/usr/bin/dictate-tray"


def test_poisoned_path_does_not_win_over_packaged_launcher(monkeypatch):
    """A stray ~/.local/bin/talktype earlier in PATH must NOT be chosen.

    This is the whole reason for looking up an absolute path instead of `which`:
    autostart would otherwise launch an unrelated binary at login.
    """
    mod = _make_prefs(
        monkeypatch,
        exists={"/usr/bin/talktype", "/home/ron/.local/bin/talktype"},
        which_map={"talktype": "/home/ron/.local/bin/talktype"},
    )
    assert mod.get_launch_command() == "/usr/bin/talktype"


def test_which_fallback_only_for_nonstandard_layout(monkeypatch):
    """No launcher at /usr/bin, but one on PATH — the fallback still finds it."""
    mod = _make_prefs(
        monkeypatch,
        exists={"/opt/custom/talktype"},
        which_map={"talktype": "/opt/custom/talktype"},
    )
    assert mod.get_launch_command() == "/opt/custom/talktype"


def test_appimage_env_takes_precedence_over_usr_bin(monkeypatch):
    """The AppImage branch runs before the packaged-launcher lookup; keep it so.

    Guards against the absolute-path check being reordered above the AppImage
    case, which would make an installed .deb hijack an AppImage run.
    """
    app = "/home/ron/AppImages/TalkType-v0.7.0-x86_64.AppImage"
    mod = _make_prefs(monkeypatch, exists={app, "/usr/bin/talktype"}, appimage=app)
    assert mod.get_launch_command() == app


def test_raw_python_only_as_last_resort(monkeypatch):
    """No launcher anywhere — the bare interpreter is the last resort."""
    mod = _make_prefs(monkeypatch, exists=set())
    assert mod.get_launch_command() == "/opt/talktype/usr/bin/python3 -m tuxtalk.tray"


DOWNLOAD = "/home/ron/Downloads/TalkType-v0.10.0-x86_64.AppImage"


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
