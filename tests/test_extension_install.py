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


@pytest.mark.parametrize("from_checkout", [True, False])
def test_the_install_enables_once_and_its_progress_only_goes_up(extensions, monkeypatch,
                                                                from_checkout):
    """It enabled the extension twice, and the download's bar showed 100%
    before "Enabling extension..." at 95%."""
    import zipfile
    ext_dir, source = extensions
    enabled, shown = [], []
    monkeypatch.setattr(eh, "is_extension_available", lambda: True)
    monkeypatch.setattr(desktop_detect, "enable_extension", lambda uuid: enabled.append(uuid) or True)
    monkeypatch.setattr(eh, "checkout_extension_path", lambda: str(source) if from_checkout else None)

    def download(url, dest, **kwargs):
        with zipfile.ZipFile(dest, "w") as z:
            for name in ("metadata.json", "extension.js"):
                z.write(source / name, f"{eh.EXTENSION_UUID}/{name}")
        return True
    monkeypatch.setattr(eh, "_fetch_extension_sha256", lambda: None)    # no checksums
    monkeypatch.setattr("tuxtalk.download_utils.download_file", download)
    assert eh.install_extension(lambda message, percent: shown.append(percent))
    assert enabled == [eh.EXTENSION_UUID]
    assert shown == sorted(shown) and shown[-1] == 100
    assert (ext_dir / eh.EXTENSION_UUID / "extension.js").read_text() == "// new"


def test_a_checkout_copy_that_fails_falls_back_without_the_bar_going_back(extensions,
                                                                       monkeypatch):
    """It showed 50% for the checkout's copy, then 10% for the download."""
    import zipfile
    ext_dir, source = extensions
    shown = []
    monkeypatch.setattr(eh, "is_extension_available", lambda: True)
    monkeypatch.setattr(desktop_detect, "enable_extension", lambda uuid: True)
    monkeypatch.setattr(eh, "checkout_extension_path", lambda: str(source))
    real_local = eh.install_extension_from_local
    tries = []

    def local(path, auto_enable=True):
        tries.append(path)
        return False if len(tries) == 1 else real_local(path, auto_enable)
    monkeypatch.setattr(eh, "install_extension_from_local", local)

    def download(url, dest, **kwargs):
        with zipfile.ZipFile(dest, "w") as z:
            for name in ("metadata.json", "extension.js"):
                z.write(source / name, f"{eh.EXTENSION_UUID}/{name}")
        return True
    monkeypatch.setattr(eh, "_fetch_extension_sha256", lambda: None)    # no checksums
    monkeypatch.setattr("tuxtalk.download_utils.download_file", download)
    assert eh.install_extension(lambda message, percent: shown.append(percent))
    assert shown == sorted(shown) and shown[-1] == 100


def test_the_dev_version_installs_the_extension_of_its_own_checkout():
    """The AppImage carries no copy: only a source checkout has one."""
    path = eh.checkout_extension_path()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # this checkout
    assert path == os.path.join(here, "gnome-extension", eh.EXTENSION_UUID)
    assert os.path.isfile(os.path.join(path, "extension.js"))


def test_installed_programs_have_no_checkout_copy(tmp_path, monkeypatch):
    """The AppImage, .deb and .rpm run from a folder with no gnome-extension
    beside it: the extension is downloaded."""
    monkeypatch.setattr(eh, "__file__", str(tmp_path / "usr" / "lib" / "tuxtalk" /
                                            "extension_helper.py"))
    assert eh.checkout_extension_path() is None


def test_an_extension_gnome_wont_turn_on_is_not_reported_installed(extensions, monkeypatch):
    """Without a tray its menu is how TuxTalk is used: "installed" would leave
    the user with nothing."""
    _ext_dir, source = extensions
    monkeypatch.setattr(desktop_detect, "enable_extension", lambda uuid: False)
    assert eh.install_extension_from_local(str(source)) is False


def test_a_checkout_copy_gnome_wont_turn_on_isnt_downloaded_again(extensions, monkeypatch):
    """The download would be refused the same way: only a failed copy is
    worth one."""
    _ext_dir, source = extensions
    monkeypatch.setattr(eh, "is_extension_available", lambda: True)
    monkeypatch.setattr(eh, "checkout_extension_path", lambda: str(source))
    monkeypatch.setattr(desktop_detect, "enable_extension", lambda uuid: False)
    downloaded = []
    monkeypatch.setattr(eh, "download_and_install_extension",
                        lambda callback=None: downloaded.append(True))
    shown = []
    assert eh.install_extension(lambda message, percent: shown.append(message)) is False
    assert downloaded == [] and shown[-1] == "GNOME wouldn't turn the extension on"
