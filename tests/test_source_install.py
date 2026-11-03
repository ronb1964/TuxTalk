"""What a source install tells its user to run: the command that starts it and
the one that installs a newer release into the venv it runs from."""
import sys

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
