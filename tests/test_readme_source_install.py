"""The README's "From source" steps install this version the way the app expects.

Check for Updates shows `source_install.update_command`, and Uninstall removes
the venv the README makes, so the README has to say the same thing. Step 11 of
CLAUDE.md has the version in the command move with each release.
"""
import re
from pathlib import Path

from tuxtalk import source_install

ROOT = Path(__file__).resolve().parent.parent
VERSION = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(),
                  re.M).group(1)


def _readme():
    """The README with its `\\` line ends joined, so a split command reads as one."""
    return (ROOT / "README.md").read_text().replace("\\\n", " ")


def test_the_readme_installs_this_version_with_the_command_check_for_updates_shows():
    home = "/home/ann"
    cmd = source_install.update_command(
        VERSION, prefix=f"{home}/.local/share/TuxTalk/venv", home=home)
    assert " ".join(cmd.split()) in " ".join(_readme().split())


def test_the_readme_makes_the_venv_the_uninstaller_removes():
    assert ("python3 -m venv --system-site-packages ~/.local/share/TuxTalk/"
            + source_install.VENV_NAME) in _readme()


def test_the_installation_section_has_the_from_source_part_and_points_developers_on():
    text = _readme()
    assert "### From source (any distro)" in text and "DEV_SETUP.md" in text


def test_the_readme_offers_the_aur_source_package_and_keeps_the_appimage_one_recommended():
    arch = _readme().split("### Arch Linux (AUR)")[1].split("### Debian")[0]
    assert "yay -S tuxtalk-appimage" in arch
    assert "yay -S tuxtalk\n" in arch or "yay -S tuxtalk " in arch
    assert "from source" in arch and "python-onnxruntime-cpu" in arch
    assert arch.index("tuxtalk-appimage") < arch.index("python-onnxruntime-cpu")
