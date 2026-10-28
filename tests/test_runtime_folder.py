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
