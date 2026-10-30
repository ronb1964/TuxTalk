"""Where the single-instance and install locks go. A session started
without a runtime folder (a desktop started by hand, without logind or
elogind) ran with no locks at all: two services typed every dictation
twice, and every GNOME extension install failed."""
import os
import stat

import pytest

from tuxtalk import single_instance


@pytest.fixture
def no_runtime_folder(tmp_path, monkeypatch):
    """No XDG_RUNTIME_DIR folder; the fallback's place is the test's."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "missing"))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(single_instance, "FALLBACK_ROOT", str(tmp_path))
    return tmp_path / f"tuxtalk-{os.getuid()}"


def test_without_a_runtime_folder_locks_go_to_a_private_one(no_runtime_folder):
    """Shared by every copy (dev and installed), as the runtime folder is."""
    lock = single_instance.open_lock("talktype.lock")
    try:
        assert single_instance.claim(lock)
        assert single_instance.held_elsewhere("talktype.lock") is True
    finally:
        lock.close()
    assert stat.S_IMODE(os.stat(no_runtime_folder).st_mode) == 0o700


def test_a_linked_fallback_folder_is_refused(no_runtime_folder, tmp_path):
    """A link could put the lock where another user holds or swaps it."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    no_runtime_folder.symlink_to(elsewhere)
    with pytest.raises(OSError):
        single_instance.open_lock("talktype.lock")
    assert os.listdir(elsewhere) == []


def test_a_fallback_folder_others_can_open_is_refused(no_runtime_folder):
    no_runtime_folder.mkdir(mode=0o755)
    no_runtime_folder.chmod(0o755)
    with pytest.raises(OSError):
        single_instance.open_lock("talktype.lock")


def test_a_linked_lock_file_is_refused(tmp_path, monkeypatch):
    """Claiming truncates the file: through a link, someone else's."""
    monkeypatch.setattr(single_instance, "runtime_dir", lambda: str(tmp_path))
    target = tmp_path / "precious"
    target.write_text("keep")
    (tmp_path / "talktype.lock").symlink_to(target)
    with pytest.raises(OSError):
        single_instance.open_lock("talktype.lock")
    assert target.read_text() == "keep"


def test_the_runtime_folder_is_used_when_there_is_one(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert single_instance.runtime_dir() == str(tmp_path)


def test_a_runtime_folder_others_can_open_is_not_used(no_runtime_folder, tmp_path, monkeypatch):
    """A misconfigured, shared runtime folder: another user could swap the lock."""
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(shared))
    assert single_instance.runtime_dir() == str(no_runtime_folder)


def test_an_empty_runtime_folder_setting_counts_as_none(monkeypatch):
    """The desktop's sockets (Hyprland's, a Wayland display) are looked for
    in /run/user/<uid>, not in the current folder."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", "")
    assert single_instance.session_runtime_dir() == f"/run/user/{os.getuid()}"
    assert single_instance.session_runtime_dir({"XDG_RUNTIME_DIR": "/run/x"}) == "/run/x"


def _private_folder(path):
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def test_a_linked_runtime_folder_is_not_used_for_dictated_text(tmp_path, monkeypatch):
    """A link can lead through a folder another user may change, and be
    pointed elsewhere after the check."""
    real = _private_folder(tmp_path / "real")
    link = tmp_path / "run"
    link.symlink_to(real)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(link))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert single_instance.private_session_dir() is None


def test_a_runtime_folder_in_a_folder_anyone_can_change_is_not_used(tmp_path, monkeypatch):
    """Anyone could rename it away and put their own in its place."""
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    run = _private_folder(shared / "run")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert single_instance.private_session_dir() is None
    shared.chmod(0o1777)        # sticky, as /tmp: only its owner may rename it
    assert single_instance.private_session_dir() == str(run)


def test_a_private_folder_in_the_users_own_folder_is_used(tmp_path, monkeypatch):
    parent = _private_folder(tmp_path / "user")
    run = _private_folder(parent / "1000")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert single_instance.private_session_dir() == str(run)


@pytest.mark.skipif(not os.path.isdir(f"/run/user/{os.getuid()}"),
                    reason="no /run/user/<uid> on this computer")
def test_the_usual_runtime_folder_is_used(monkeypatch):
    """/run/user/<uid>, in root's /run/user, as logind makes it."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert single_instance.private_session_dir() == f"/run/user/{os.getuid()}"


@pytest.mark.parametrize("layout", ["open parent", "link"])
def test_the_locks_stay_where_they_were_when_the_strict_check_refuses(tmp_path, monkeypatch,
                                                                      layout):
    """Moved by the stricter check, a new copy's lock would miss the one an
    older copy still holds in the old folder, and both would run."""
    if layout == "open parent":
        shared = tmp_path / "shared"
        shared.mkdir()
        shared.chmod(0o777)
        run = _private_folder(shared / "run")
    else:
        run = tmp_path / "run"
        run.symlink_to(_private_folder(tmp_path / "real"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(single_instance, "FALLBACK_ROOT", str(tmp_path / "fallback"))
    assert single_instance.private_session_dir() is None
    assert single_instance.runtime_dir() == str(run)


def test_tests_never_use_the_users_own_runtime_folder():
    """conftest gives each test a runtime folder of its own: a test that
    took the user's real cuda-install.lock could hold up a live TuxTalk."""
    import conftest
    for folder in (single_instance.runtime_dir(), single_instance.session_runtime_dir()):
        assert not os.path.realpath(folder).startswith(
            os.path.realpath(conftest._REAL_RUNTIME_DIR))
