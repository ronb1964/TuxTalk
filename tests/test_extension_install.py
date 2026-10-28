"""Installing or updating the GNOME extension never leaves GNOME without it."""
import os

import pytest

from tuxtalk import desktop_detect
from tuxtalk import extension_helper as eh


@pytest.fixture
def extensions(tmp_path, monkeypatch):
    """A gnome-shell/extensions folder of its own, and an extension to install."""
    ext_dir = tmp_path / "gnome-shell" / "extensions"
    monkeypatch.setattr(desktop_detect, "get_extension_dir", lambda: str(ext_dir))
    runtime = tmp_path / "run"                  # the install lock, never the real one
    runtime.mkdir()
    monkeypatch.setattr("tuxtalk.single_instance.runtime_dir", lambda: str(runtime))
    source = tmp_path / "source"
    source.mkdir()
    (source / "metadata.json").write_text('{"version": 2}')
    (source / "extension.js").write_text("// new")
    return ext_dir, source


def test_an_install_puts_the_extension_in_place(extensions):
    ext_dir, source = extensions
    assert eh.install_extension_from_local(str(source), auto_enable=False)
    installed = ext_dir / eh.EXTENSION_UUID
    assert (installed / "extension.js").read_text() == "// new"
    assert os.listdir(ext_dir) == [eh.EXTENSION_UUID]                    # nothing left over


def test_an_update_that_fails_partway_keeps_the_old_extension(extensions, monkeypatch):
    """The old folder was removed before copying: a full disk partway left
    GNOME with no TuxTalk menu at all."""
    ext_dir, source = extensions
    installed = ext_dir / eh.EXTENSION_UUID
    installed.mkdir(parents=True)
    (installed / "extension.js").write_text("// old")

    def full_disk(src, dst):
        os.makedirs(dst)
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(eh.shutil, "copytree", full_disk)
    assert eh.install_extension_from_local(str(source), auto_enable=False) is False
    assert (installed / "extension.js").read_text() == "// old"


def test_a_failed_swap_puts_the_old_extension_back(extensions, monkeypatch):
    ext_dir, source = extensions
    installed = ext_dir / eh.EXTENSION_UUID
    installed.mkdir(parents=True)
    (installed / "extension.js").write_text("// old")
    rename = os.rename

    def second_rename_fails(src, dst):
        if str(src).endswith(".new"):
            raise OSError(5, "Input/output error")
        rename(src, dst)
    monkeypatch.setattr(eh.os, "rename", second_rename_fails)
    assert eh.install_extension_from_local(str(source), auto_enable=False) is False
    assert (installed / "extension.js").read_text() == "// old"


def test_an_install_cut_off_between_the_renames_is_recovered(extensions):
    """The old copy moved aside, then a crash: the next install deleted it
    before copying, and if that copy failed there was no extension left."""
    ext_dir, source = extensions
    aside = ext_dir / f".{eh.EXTENSION_UUID}.old"
    aside.mkdir(parents=True)
    (aside / "extension.js").write_text("// old")
    assert eh.install_extension_from_local(str(source), auto_enable=False)
    assert (ext_dir / eh.EXTENSION_UUID / "extension.js").read_text() == "// new"
    assert os.listdir(ext_dir) == [eh.EXTENSION_UUID]


def test_a_linked_extensions_folder_is_staged_where_it_really_is(tmp_path, extensions,
                                                                  monkeypatch):
    """Staged beside a linked extensions folder, the rename crossed
    filesystems and every update failed."""
    ext_dir, source = extensions
    real = tmp_path / "elsewhere" / "extensions"
    real.mkdir(parents=True)
    ext_dir.parent.mkdir(parents=True, exist_ok=True)
    ext_dir.symlink_to(real)
    renames = []
    rename = os.rename
    monkeypatch.setattr(eh.os, "rename", lambda a, b: renames.append((a, b)) or rename(a, b))
    assert eh.install_extension_from_local(str(source), auto_enable=False)
    assert all(os.path.dirname(a) == str(real) for a, b in renames)


def test_two_installs_at_once_take_turns(extensions, monkeypatch):
    """Preferences and first run installing at once: each cleared the
    other's folders, and one failed with the extension moved aside. The
    first is held inside its copy while the second tries to start."""
    import shutil
    import threading
    ext_dir, source = extensions
    copying, may_finish, copies = threading.Event(), threading.Event(), []
    real_copytree = shutil.copytree

    def held_copy(src, dst, **kw):
        copies.append(dst)
        if len(copies) == 1:
            copying.set()
            assert may_finish.wait(10)
        return real_copytree(src, dst, **kw)
    monkeypatch.setattr(eh.shutil, "copytree", held_copy)
    results = []

    def install():
        results.append(eh.install_extension_from_local(str(source), auto_enable=False))
    first = threading.Thread(target=install)
    first.start()
    assert copying.wait(10)
    second = threading.Thread(target=install)
    second.start()
    second.join(0.5)
    assert len(copies) == 1             # the second waits its turn
    may_finish.set()
    for t in (first, second):
        t.join(30)
    assert results == [True, True]
    assert os.listdir(ext_dir) == [eh.EXTENSION_UUID]
