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


def _package_tree(root):
    """A stand-in for /opt/talktype, where the .deb and .rpm run AppRun
    straight, without the AppImage runtime that sets APPDIR."""
    (root / "usr").mkdir(parents=True)
    (root / "AppRun").write_text("#!/bin/sh\n")
    return str(root)


def test_the_packages_python_and_libraries_are_gone(tmp_path):
    tree = _package_tree(tmp_path / "opt" / "talktype")
    env = clean({"PYTHONHOME": f"{tree}/usr", "LD_LIBRARY_PATH": f"{tree}/usr/lib:/opt/mine/lib",
                 "PATH": f"{tree}/usr/bin:/usr/bin"}, venv="")
    assert "PYTHONHOME" not in env
    assert env["LD_LIBRARY_PATH"] == "/opt/mine/lib" and env["PATH"] == "/usr/bin"


def test_a_pythonhome_of_the_users_own_keeps_their_path(tmp_path):
    """Only AppRun's folder is TuxTalk's: a PYTHONHOME of /usr/local must
    not take /usr/bin off PATH."""
    env = clean({"PYTHONHOME": "/usr/local", "PATH": "/usr/local/bin:/usr/bin"}, venv="")
    assert env["PATH"] == "/usr/local/bin:/usr/bin"


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


def test_dev_venv_libraries_and_variables_are_gone():
    venv = "/home/u/TuxTalk/.venv"
    env = clean({"VIRTUAL_ENV": venv, "VIRTUAL_ENV_PROMPT": "(.venv) ",
                 "LD_LIBRARY_PATH": f"{venv}/lib64/python3.14/site-packages/ctranslate2.libs"
                                    ":/opt/mine/lib",
                 "PATH": f"{venv}/bin:/usr/bin:/home/u/.venv-other/bin"}, venv=venv)
    assert "VIRTUAL_ENV" not in env and "VIRTUAL_ENV_PROMPT" not in env
    assert env["LD_LIBRARY_PATH"] == "/opt/mine/lib"
    assert env["PATH"] == "/usr/bin:/home/u/.venv-other/bin"


def test_outside_a_venv_nothing_extra_is_removed():
    env = clean({"LD_LIBRARY_PATH": "/home/u/TuxTalk/.venv/lib:/opt/mine/lib",
                 "PATH": "/home/u/TuxTalk/.venv/bin:/usr/bin"}, venv="")
    assert env["LD_LIBRARY_PATH"] == "/home/u/TuxTalk/.venv/lib:/opt/mine/lib"
    assert env["PATH"] == "/home/u/TuxTalk/.venv/bin:/usr/bin"


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
