"""What a source install tells its user to run: the command that starts it and
the one that installs a newer release into the venv it runs from. A pip install
outside a venv starts from the `tuxtalk` script pip put beside it."""
import site
import sys
import sysconfig

import pytest

from tuxtalk import source_install


def test_the_update_command_installs_the_new_release_into_the_running_venv():
    assert source_install.update_command(
        "0.21.0", prefix="/home/ann/.local/share/TuxTalk/venv", home="/home/ann") == (
        "~/.local/share/TuxTalk/venv/bin/pip install -c "
        "https://raw.githubusercontent.com/ronb1964/TuxTalk/v0.21.0/packaging/"
        "source-install-constraints.txt "
        "https://github.com/ronb1964/TuxTalk/archive/refs/tags/v0.21.0.tar.gz")


def test_a_venv_somewhere_else_is_named_as_it_is():
    cmd = source_install.update_command("0.21.0", prefix="/opt/envs/tt", home="/home/ann")
    assert cmd.startswith("/opt/envs/tt/bin/pip install -c ")


def test_a_venv_path_with_a_space_is_quoted_in_full():
    cmd = source_install.update_command("0.21.0", prefix="/home/ann/My Apps/venv",
                                        home="/home/ann")
    assert cmd.startswith("'/home/ann/My Apps/venv/bin/pip' install -c ")


def test_the_tuxtalk_command_is_the_one_in_the_venv(monkeypatch):
    monkeypatch.setattr(sys, "prefix", "/home/ann/.local/share/TuxTalk/venv")
    assert source_install.command() == "/home/ann/.local/share/TuxTalk/venv/bin/tuxtalk"


@pytest.fixture
def outside_venv(tmp_path, monkeypatch):
    """TuxTalk pip-installed into a Python's own folders rather than a venv
    (sys.prefix is the system's /usr). Where pip puts the scripts of the
    prefix scheme (/usr/local/bin on Fedora and Debian) and of the user
    scheme (~/.local/bin) is made up under tmp_path. Call it with "user" or
    "prefix" for where TuxTalk's package is; it returns where pip put the
    `tuxtalk` script for that."""
    monkeypatch.setattr(sys, "prefix", "/usr")
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    user_site = tmp_path / "home/.local/lib/python3.14/site-packages"
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(user_site))
    scripts = {"posix_user": tmp_path / "home/.local/bin", None: tmp_path / "usr/local/bin"}
    real_get_path = sysconfig.get_path

    def get_path(name, scheme=None, *args, **kwargs):
        if name == "scripts":
            return str(scripts[scheme])
        return real_get_path(name, scheme, *args, **kwargs)
    monkeypatch.setattr(sysconfig, "get_path", get_path)

    def installed(where):
        site_packages = (user_site if where == "user"
                         else tmp_path / "usr/local/lib/python3.14/site-packages")
        monkeypatch.setattr(source_install, "__file__",
                            str(site_packages / "tuxtalk/source_install.py"))
        return scripts["posix_user" if where == "user" else None] / "tuxtalk"
    return installed


def _make(script):
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/usr/bin/python3\n")


def test_a_pip_install_user_starts_from_the_tuxtalk_pip_put_in_its_bin(outside_venv):
    script = outside_venv("user")
    _make(script)
    assert source_install.command() == str(script)


def test_a_pip_install_into_usr_local_starts_from_its_own_bin(outside_venv):
    script = outside_venv("prefix")
    _make(script)
    assert source_install.command() == str(script)


def test_outside_a_venv_without_its_tuxtalk_script_there_is_no_command(outside_venv):
    outside_venv("user")
    assert source_install.command() is None
