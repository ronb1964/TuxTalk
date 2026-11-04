"""The environment of programs TuxTalk starts: no trace of TuxTalk's own."""
import subprocess

from tuxtalk import update_checker
from tuxtalk.launch_env import clean


def test_appimage_python_and_libraries_are_gone():
    env = clean({"APPDIR": "/tmp/.mount_X", "PYTHONHOME": "/tmp/.mount_X/usr",
                 "PYTHONPATH": "/x", "GI_TYPELIB_PATH": "/x",
                 "LD_LIBRARY_PATH": "/tmp/.mount_X/usr/lib:/opt/mine/lib",
                 "PATH": "/tmp/.mount_X/usr/bin:/usr/bin", "APPIMAGE": "/a", "ARGV0": "a",
                 "OWD": "/"})
    assert "PYTHONHOME" not in env and "PYTHONPATH" not in env and "APPDIR" not in env
    assert "GI_TYPELIB_PATH" not in env and "APPIMAGE" not in env
    assert "ARGV0" not in env and "OWD" not in env
    assert env["LD_LIBRARY_PATH"] == "/opt/mine/lib" and env["PATH"] == "/usr/bin"


def test_the_users_own_path_entries_keep_their_order():
    env = clean({"APPDIR": "/mnt/x", "PATH": "/b:/mnt/x/usr/bin:/a:/mnt/x/usr/sbin:/c"})
    assert env["PATH"] == "/b:/a:/c"


def test_an_emptied_library_path_is_removed_not_left_blank():
    env = clean({"APPDIR": "/mnt/x", "LD_LIBRARY_PATH": "/mnt/x/usr/lib"})
    assert "LD_LIBRARY_PATH" not in env


def test_library_path_outside_an_appimage_is_untouched():
    assert clean({"LD_LIBRARY_PATH": "/opt/mine/lib"})["LD_LIBRARY_PATH"] == "/opt/mine/lib"


def test_users_gdk_backend_comes_back():
    assert "GDK_BACKEND" not in clean({"GDK_BACKEND": "x11", "TUXTALK_USERS_GDK_BACKEND": ""})
    env = clean({"GDK_BACKEND": "x11", "TUXTALK_USERS_GDK_BACKEND": "wayland"})
    assert env["GDK_BACKEND"] == "wayland"
    assert "TUXTALK_USERS_GDK_BACKEND" not in env


def test_cuda_hidden_by_tuxtalk_only():
    env = clean({"CUDA_VISIBLE_DEVICES": "", "TUXTALK_SET_CUDA_VISIBLE_DEVICES": "1"})
    assert "CUDA_VISIBLE_DEVICES" not in env
    assert "TUXTALK_SET_CUDA_VISIBLE_DEVICES" not in env
    assert clean({"CUDA_VISIBLE_DEVICES": "1"})["CUDA_VISIBLE_DEVICES"] == "1"


def test_tuxtalks_own_variables_go():
    env = clean({"HF_HUB_DISABLE_XET": "1", "DEV_MODE": "1", "TUXTALK_LOG_DIR": "/x",
                 "HOME": "/home/u"})
    assert env == {"HOME": "/home/u"}


def test_the_callers_environment_is_not_changed():
    original = {"PYTHONPATH": "/x", "HOME": "/home/u"}
    clean(original)
    assert original == {"PYTHONPATH": "/x", "HOME": "/home/u"}


def test_the_release_page_opens_with_the_clean_environment(monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "/tmp/.mount_X/usr")
    started = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: started.append((cmd, kw)))
    update_checker.open_release_page("https://example.org/r")
    cmd, kw = started[0]
    assert cmd == ["xdg-open", "https://example.org/r"]
    assert "PYTHONHOME" not in kw["env"]
