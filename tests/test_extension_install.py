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
    assert sorted(os.listdir(ext_dir.parent)) == ["extensions"]          # nothing left over


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
