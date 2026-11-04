"""Uninstalling a source install (pip into a venv) and a system install (a
distro package's files under /usr).

Every test works in a made-up home folder; nothing here may touch the real
one, and conftest.py fails any test that reaches pkexec.
"""
import shutil

import pytest

from tuxtalk import secure_input, uinput_helper, uninstall

VENV = ".local/share/TuxTalk/venv"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home folder with a source install in the README's venv."""
    home = tmp_path / "home"
    command = home / VENV / "bin/tuxtalk"
    command.parent.mkdir(parents=True)
    command.write_text("#!/usr/bin/python3\n")
    # The models cache and runtime folder are looked for in the made-up home only.
    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    # This machine's own helper, extension folder and groups stay out of it.
    for name in ("UNIT_PATH", "HELPER_PATH", "UINPUT_RULE_PATH"):
        monkeypatch.setattr(secure_input, name, str(tmp_path / f"etc-{name}"))
    monkeypatch.setattr(uinput_helper, "UDEV_RULE_PATH", str(tmp_path / "etc-99-rule"))
    monkeypatch.setattr(uninstall.gnome_extensions, "get_extension_dir",
                        lambda: str(home / ".local/share/gnome-shell/extensions"))
    return home


def _source(home, prefix=None):
    """The plan for a source install running from *prefix* (the README's venv)."""
    return uninstall.find(home, "source", prefix=str(prefix or home / VENV), base_prefix="/usr")


def _done(plan, **result):
    return uninstall.done_text(plan, False, False, uninstall.Result(**result))


def test_a_source_installs_venv_and_its_command_link_go(home):
    venv = home / VENV
    link = home / ".local/bin/tuxtalk"
    link.parent.mkdir(parents=True)
    link.symlink_to(venv / "bin/tuxtalk")
    plan = _source(home)
    assert venv in plan.downloads and link in plan.leftovers and plan.venv_elsewhere is None
    uninstall.remove_files(plan, delete_personal=False, delete_shared=False)
    assert not venv.exists() and not link.is_symlink()


def test_a_tuxtalk_command_of_the_users_own_stays(home):
    own = home / ".local/bin/tuxtalk"
    own.parent.mkdir(parents=True)
    own.write_text("#!/bin/sh\n")
    plan = _source(home)
    assert own not in plan.leftovers
    uninstall.remove_files(plan, delete_personal=False, delete_shared=False)
    assert own.read_text() == "#!/bin/sh\n"


def test_a_tuxtalk_link_to_another_program_stays(home, tmp_path):
    other = tmp_path / "elsewhere/tuxtalk"
    other.parent.mkdir()
    other.write_text("#!/bin/sh\n")
    link = home / ".local/bin/tuxtalk"
    link.parent.mkdir(parents=True)
    link.symlink_to(other)
    assert link not in _source(home).leftovers


def test_a_source_installs_menu_entry_icons_and_start_at_login_go(home):
    made = [home / path for path in (
        ".config/autostart/tuxtalk.desktop", ".local/share/applications/tuxtalk.desktop",
        *(f".local/share/icons/hicolor/{size}/apps/tuxtalk.png" for size in uninstall.ICON_SIZES))]
    for path in made:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    plan = _source(home)
    assert "starting at login and its menu entry" in uninstall.confirm_text(plan, 0)
    assert uninstall.remove_files(plan, delete_personal=False, delete_shared=False) == []
    assert not any(path.exists() for path in made)


def test_a_venv_elsewhere_is_named_and_never_deleted(home, tmp_path):
    venv = tmp_path / "envs/tt"
    (venv / "bin").mkdir(parents=True)
    plan = _source(home, venv)
    assert plan.venv_elsewhere == venv and venv not in plan.downloads
    # pip takes TuxTalk out; the folder may hold other programs.
    assert (f"TuxTalk was installed in <tt>{venv}</tt>. To finish, remove it with "
            f"<tt>{venv}/bin/pip uninstall tuxtalk</tt>, or delete that folder if nothing "
            "else is in it.") in _done(plan)
    # The question doesn't promise to remove a program it leaves.
    assert "TuxTalk itself" not in uninstall.confirm_text(plan, 0)
    uninstall.remove_files(plan, delete_personal=False, delete_shared=False)
    assert (venv / "bin").is_dir()


def test_a_venv_elsewhere_in_the_home_folder_is_shown_with_a_tilde(home):
    text = _done(_source(home, home / "envs/tuxtalk"))
    assert "installed in <tt>~/envs/tuxtalk</tt>" in text
    assert "<tt>~/envs/tuxtalk/bin/pip uninstall tuxtalk</tt>" in text


def test_a_venv_path_that_needs_quoting_is_quoted_whole_for_the_shell(home):
    venv = home / "R&D envs/tt"
    text = _done(_source(home, venv))
    # Quoted, so a pasted command works; escaped, so the message shows it
    # (Pango shows &apos; as ').
    assert f"<tt>&apos;{home}/R&amp;D envs/tt/bin/pip&apos; uninstall tuxtalk</tt>" in text


def test_a_readme_venv_that_is_a_link_names_the_venv_it_leads_to(home, tmp_path):
    real = tmp_path / "envs/tt"
    (real / "bin").mkdir(parents=True)
    readme = home / VENV
    shutil.rmtree(readme)
    readme.symlink_to(real)
    plan = _source(home)
    assert plan.venv_elsewhere == real
    assert "TuxTalk itself" not in uninstall.confirm_text(plan, 0)
    assert f"<tt>{real}/bin/pip uninstall tuxtalk</tt>" in _done(plan)
    uninstall.remove_files(plan, delete_personal=False, delete_shared=False)
    assert not readme.is_symlink() and (real / "bin").is_dir()


@pytest.mark.parametrize("install_type", ["appimage", "deb"])
def test_another_installs_uninstall_names_the_source_install_it_takes(home, install_type):
    plan = uninstall.find(home, install_type)
    assert home / VENV in plan.downloads
    assert ("•  TuxTalk installed from its source code, in ~/.local/share/TuxTalk/venv"
            in uninstall.confirm_text(plan, 0))


def test_an_uninstall_from_another_venv_names_the_readme_venv_it_takes(home, tmp_path):
    """Both installed: the README's venv goes with the downloads, so the
    question has to say so; the venv running stays and isn't promised."""
    venv = tmp_path / "envs/tt"
    (venv / "bin").mkdir(parents=True)
    plan = _source(home, venv)
    assert home / VENV in plan.downloads
    assert ("•  TuxTalk installed from its source code, in ~/.local/share/TuxTalk/venv"
            in uninstall.confirm_text(plan, 0))
    uninstall.remove_files(plan, delete_personal=False, delete_shared=False)
    assert not (home / VENV).exists() and (venv / "bin").is_dir()


def test_an_uninstall_outside_a_venv_names_the_readme_venv_it_takes(home):
    plan = uninstall.find(home, "source", prefix="/usr", base_prefix="/usr")
    assert ("•  TuxTalk installed from its source code, in ~/.local/share/TuxTalk/venv"
            in uninstall.confirm_text(plan, 0))


def test_a_source_installs_own_question_doesnt_name_its_venv_twice(home):
    assert "in ~/.local/share/TuxTalk/venv" not in uninstall.confirm_text(_source(home), 0)


def test_the_readmes_venv_reached_through_a_link_is_not_called_elsewhere(home, tmp_path):
    link = tmp_path / "venv-link"
    link.symlink_to(home / VENV)
    plan = _source(home, link)
    assert plan.venv_elsewhere is None
    assert "to finish" not in _done(plan)


def test_an_install_outside_a_venv_is_never_told_to_delete_its_prefix(home):
    plan = uninstall.find(home, "source", prefix="/usr", base_prefix="/usr")
    text = _done(plan)
    assert plan.venv_elsewhere is None and "/usr" not in text
    assert "Remove TuxTalk the way you installed it to finish." in text
    assert "TuxTalk itself" not in uninstall.confirm_text(plan, 0)


def test_a_source_install_in_the_readmes_venv_needs_nothing_more(home):
    text = _done(_source(home))
    assert "to finish" not in text


def test_a_pacman_owned_system_install_is_removed_with_pacman(home):
    plan = uninstall.find(home, "system", owner=lambda: "tuxtalk")
    assert plan.package == "tuxtalk" and plan.needs_root
    assert "pacman -R --noconfirm tuxtalk" in uninstall.root_script(plan, False)


def test_a_package_name_from_pacman_is_quoted_for_the_shell(home):
    plan = uninstall.find(home, "system", owner=lambda: "tux talk;rm")
    script = uninstall.root_script(plan, False)
    assert "pacman -R --noconfirm 'tux talk;rm'" in script
    assert "<tt>sudo pacman -R &apos;tux talk;rm&apos;</tt>" in _done(plan, package_failed=True)


def test_a_failed_pacman_removal_names_the_command(home):
    plan = uninstall.find(home, "system", owner=lambda: "tuxtalk")
    assert "<tt>sudo pacman -R tuxtalk</tt>" in _done(plan, package_failed=True)


def test_a_system_install_pacman_doesnt_own_is_left_to_its_package_manager(home):
    plan = uninstall.find(home, "system", owner=lambda: None)
    assert plan.package is None and not plan.needs_root
    assert "Remove TuxTalk with the package manager you installed it with to finish." in \
        _done(plan)
    assert "TuxTalk itself" not in uninstall.confirm_text(plan, 0)


def test_the_question_names_the_program_for_the_new_install_types(home):
    assert "•  TuxTalk itself (installed from its source code)" in \
        uninstall.confirm_text(_source(home), 0)
    plan = uninstall.find(home, "system", owner=lambda: "tuxtalk")
    assert "•  TuxTalk itself (the tuxtalk package)" in uninstall.confirm_text(plan, 0)


def test_the_question_names_the_program_for_the_package_installs(home):
    """The packages' question lines are as they were before system installs came in."""
    for install_type, line in (("deb", "TuxTalk itself (the tuxtalk package)"),
                               ("rpm", "TuxTalk itself (the tuxtalk package)"),
                               ("aur", "TuxTalk itself (the tuxtalk-appimage package)"),
                               ("appimage", "TuxTalk itself (its AppImage)")):
        assert f"•  {line}" in uninstall.confirm_text(uninstall.find(home, install_type), 0)
    assert "TuxTalk itself" not in uninstall.confirm_text(uninstall.find(home, "package"), 0)


def test_the_package_installs_keep_their_words(home):
    """The packages' closing lines are as they were before system installs came in."""
    for install_type, command in (("deb", "sudo apt remove tuxtalk"),
                                  ("rpm", "sudo dnf remove tuxtalk"),
                                  ("aur", "sudo pacman -R tuxtalk-appimage")):
        plan = uninstall.find(home, install_type)
        assert f"<tt>{command}</tt>" in _done(plan, package_failed=True)
    assert "Remove the tuxtalk package with your package manager to finish." in \
        _done(uninstall.find(home, "package"))
