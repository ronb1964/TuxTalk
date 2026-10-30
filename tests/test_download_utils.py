"""Tests for the shared download helper (uses file:// URLs — no network)."""
import hashlib
import os
import threading
import time

import pytest

from tuxtalk.download_utils import (
    DownloadError,
    download_file,
    fetch_file,
    parse_sha256sums,
    free_space_bytes,
)


def _make_source(tmp_path, content=b"hello talktype" * 1000):
    src = tmp_path / "source.bin"
    src.write_bytes(content)
    return src, content


def test_download_file_success(tmp_path):
    src, content = _make_source(tmp_path)
    dest = tmp_path / "out" / "dest.bin"
    assert download_file(src.as_uri(), str(dest)) is True
    assert dest.read_bytes() == content
    assert not os.path.exists(str(dest) + ".tmp")


def test_download_file_sha256_match(tmp_path):
    src, content = _make_source(tmp_path)
    dest = tmp_path / "dest.bin"
    good = hashlib.sha256(content).hexdigest()
    assert download_file(src.as_uri(), str(dest), expected_sha256=good.upper()) is True
    assert dest.exists()


def test_download_file_sha256_mismatch_rejects(tmp_path):
    """A corrupted/tampered download must never land at the destination."""
    src, _ = _make_source(tmp_path)
    dest = tmp_path / "dest.bin"
    assert download_file(src.as_uri(), str(dest), expected_sha256="0" * 64) is False
    assert not dest.exists()
    assert not os.path.exists(str(dest) + ".tmp")


def test_download_file_cancelled(tmp_path):
    src, _ = _make_source(tmp_path)
    dest = tmp_path / "dest.bin"
    cancel = threading.Event()
    cancel.set()
    assert download_file(src.as_uri(), str(dest), cancel_event=cancel) is False
    assert not dest.exists()


def test_download_file_progress_hook(tmp_path):
    src, content = _make_source(tmp_path)
    dest = tmp_path / "dest.bin"
    seen = []
    assert download_file(src.as_uri(), str(dest),
                         progress_hook=lambda done, total: seen.append((done, total))) is True
    assert seen, "progress hook never called"
    assert seen[-1][0] == len(content)


@pytest.mark.parametrize("problem, reason", [
    ("missing", "No such file or directory"),
    ("checksum", "the file didn't match its checksum"),
])
def test_a_failed_download_says_why(tmp_path, problem, reason):
    """The reason reaches the download window: Parakeet's said only
    "Download failed"."""
    src, _ = _make_source(tmp_path)
    url = (tmp_path / "missing.bin").as_uri() if problem == "missing" else src.as_uri()
    dest = tmp_path / "dest.bin"
    with pytest.raises(DownloadError, match=reason):
        fetch_file(url, str(dest), expected_sha256="0" * 64)
    assert not dest.exists() and os.listdir(tmp_path) == ["source.bin"]


def test_download_file_bad_url(tmp_path):
    dest = tmp_path / "dest.bin"
    assert download_file((tmp_path / "missing.bin").as_uri(), str(dest)) is False
    assert not dest.exists()


def test_parse_sha256sums():
    text = (
        "abc123  TalkType-v0.5.17-x86_64.AppImage\n"
        "def456 *talktype-gnome-extension.zip\n"
        "\n"
        "# a comment line is ignored\n"
    )
    sums = parse_sha256sums(text)
    assert sums["TalkType-v0.5.17-x86_64.AppImage"] == "abc123"
    assert sums["talktype-gnome-extension.zip"] == "def456"
    assert len(sums) == 2


def test_free_space_bytes(tmp_path):
    # Must return a positive number for an existing dir and not raise for
    # a not-yet-created subdirectory (checks the nearest existing parent).
    assert free_space_bytes(str(tmp_path)) > 0
    assert free_space_bytes(str(tmp_path / "not" / "yet" / "created")) > 0


def test_each_download_has_its_own_temp_file(tmp_path):
    """Two downloads of one file at once must not share a temp file: one could
    overwrite the bytes the other already hash-checked."""
    src, content = _make_source(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    dest = out / "dest.bin"
    others = out / "dest.bin.tmp"
    others.write_bytes(b"another download in progress")

    assert download_file(src.as_uri(), str(dest)) is True

    assert dest.read_bytes() == content
    assert others.read_bytes() == b"another download in progress"
    assert sorted(p.name for p in out.iterdir()) == ["dest.bin", "dest.bin.tmp"]
    assert (dest.stat().st_mode & 0o777) == 0o644


def test_a_failed_download_leaves_no_temp_file(tmp_path):
    src, _ = _make_source(tmp_path)
    out = tmp_path / "out"
    assert download_file(src.as_uri(), str(out / "dest.bin"), expected_sha256="0" * 64) is False
    assert list(out.iterdir()) == []


def test_a_temp_file_left_by_a_killed_download_is_swept(tmp_path):
    """A kill skips the cleanup and temp names are random, so the next
    download of the same file removes old leftovers."""
    src, content = _make_source(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    stale = out / "dest.bin.k1ll3d.tmp"
    stale.write_bytes(b"half")
    hours_ago = os.path.getmtime(stale) - 3600
    os.utime(stale, (hours_ago, hours_ago))
    live = out / "dest.bin.busy01.tmp"
    live.write_bytes(b"another download")
    other = out / "other.bin.old999.tmp"
    other.write_bytes(b"not ours")
    os.utime(other, (hours_ago, hours_ago))

    assert download_file(src.as_uri(), str(out / "dest.bin")) is True

    assert not stale.exists()
    assert live.exists(), "a download still being written was deleted"
    assert other.exists(), "another file's download was touched"


# --- an engine's archive and the folder it unpacks into ---------------------------------

def test_two_downloads_of_one_engine_get_names_of_their_own(tmp_path):
    """The tray and Preferences may fetch the same engine at once; shared
    names let one unpack into, or delete, the other's archive."""
    from tuxtalk.download_utils import download_area
    with download_area(str(tmp_path), "engine") as first, \
            download_area(str(tmp_path), "engine") as second:
        assert set(first).isdisjoint(second)


def test_a_failed_unpack_leaves_neither_archive_nor_folder(tmp_path):
    from tuxtalk.download_utils import download_area, unpack_tar
    try:
        with download_area(str(tmp_path), "engine") as (archive, folder):
            open(archive, "wb").write(b"not a tar file")
            unpack_tar(archive, folder)
    except Exception:
        pass
    assert list(tmp_path.iterdir()) == []


def test_what_a_killed_download_left_is_swept_next_time(tmp_path):
    from tuxtalk.download_utils import STALE_PARTIAL_S, download_area
    old = time.time() - STALE_PARTIAL_S - 60
    archive, folder = tmp_path / ".engine.dead.tar", tmp_path / ".engine.dead.d"
    archive.write_bytes(b"half")
    folder.mkdir()
    (folder / "bin").write_bytes(b"x")
    for path in (archive, folder):
        os.utime(path, (old, old))
    with download_area(str(tmp_path), "engine"):
        pass
    assert list(tmp_path.iterdir()) == []


def test_an_archive_older_versions_left_is_removed(tmp_path):
    """Before 0.19 a failed unpack left the archive under its own name."""
    from tuxtalk.download_utils import download_area
    (tmp_path / "engine-b1.tar.gz").write_bytes(b"22 MB once")
    with download_area(str(tmp_path), "engine", old_names=("engine-b1.tar.gz",)):
        pass
    assert list(tmp_path.iterdir()) == []


def test_a_plain_sweep_leaves_folders_alone(tmp_path):
    """Only download_area's own leftovers are folders; the updater's sweep
    of its folder mustn't start deleting folders."""
    from tuxtalk.download_utils import remove_stale_files
    folder = tmp_path / "keep"
    folder.mkdir()
    os.utime(folder, (0, 0))
    remove_stale_files(str(tmp_path), "*", 60)
    assert folder.exists()


def _tar(path, *members):
    import io
    import tarfile
    with tarfile.open(path, "w") as tar:
        for name, link in members:
            info = tarfile.TarInfo(name)
            if link:
                info.type, info.linkname = tarfile.SYMTYPE, link
                tar.addfile(info)
            else:
                info.size = 6
                tar.addfile(info, io.BytesIO(b"engine"))


def test_unpacking_keeps_files_inside_the_folder(tmp_path):
    from tuxtalk.download_utils import unpack_tar
    _tar(tmp_path / "a.tar", ("engine/bin", None))
    unpack_tar(str(tmp_path / "a.tar"), str(tmp_path / "out"))
    assert (tmp_path / "out" / "engine" / "bin").read_bytes() == b"engine"


@pytest.mark.parametrize("member", [("../outside", None),
                                    ("engine/link", "/etc/passwd")])
def test_unpacking_refuses_what_would_land_outside_the_folder(tmp_path, member):
    import tarfile
    from tuxtalk.download_utils import unpack_tar
    if not hasattr(tarfile, "data_filter"):
        pytest.skip("this Python has no tar data filter")
    _tar(tmp_path / "a.tar", member)
    with pytest.raises(tarfile.FilterError):
        unpack_tar(str(tmp_path / "a.tar"), str(tmp_path / "out"))
    assert not (tmp_path / "outside").exists()


def test_an_http_error_keeps_its_status(tmp_path, monkeypatch):
    """"Not Found" alone didn't say it was the server's answer."""
    import urllib.error
    import urllib.request

    def not_found(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", not_found)
    with pytest.raises(DownloadError, match="^HTTP 404 Not Found$"):
        fetch_file("https://example.invalid/x", str(tmp_path / "x"))


def test_a_download_window_is_told_why_and_a_cancel_says_nothing(tmp_path):
    """The graphics and AI engines' windows said only "Download failed"."""
    src, _ = _make_source(tmp_path)
    reports = []
    assert download_file((tmp_path / "missing.bin").as_uri(), str(tmp_path / "a"),
                         report=lambda *r: reports.append(r)) is False
    assert reports[-1][1] == 0 and reports[-1][0].startswith("Download failed: ")
    assert "No such file" in reports[-1][0]
    cancel = threading.Event()
    cancel.set()
    reports.clear()
    assert download_file(src.as_uri(), str(tmp_path / "b"), cancel_event=cancel,
                         report=lambda *r: reports.append(r)) is False
    assert reports == []
