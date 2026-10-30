"""CUDA's download (cuda_download): the wheels from PyPI, each checked against
PyPI's SHA256, their libraries unpacked into a folder of their own and put in
place only when every library is there, so a failed, cancelled or odd
download, or a TuxTalk killed part-way, never leaves a working CUDA broken."""
import contextlib
import io
import json
import os
import zipfile

import pytest

from tuxtalk import cuda_download, cuda_helper, download_utils

LIBRARIES = ("cuda_runtime/lib/libcudart.so.12", "cublas/lib/libcublas.so.12",
             "cublas/lib/libcublasLt.so.12")


def _wheel(path, size, names=("cublas/lib/libcublas.so.12",)):
    """A CUDA wheel holding *names* of *size* bytes each, which compress to
    almost nothing, as the real ones shrink by half or more."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as wheel:
        for name in names:
            wheel.writestr(f"nvidia/{name}", b"\0" * size)
        wheel.writestr("nvidia_cublas_cu12-12.4.dist-info/METADATA", "Name: x")
    return str(path)


@pytest.fixture
def pypi(tmp_path, monkeypatch):
    """A PyPI and a network that serve *pypi.libraries* in one wheel per
    package; *pypi.sha256s* records the hash each download was checked with."""
    cuda = tmp_path / "cuda"
    monkeypatch.setattr(cuda_helper, "get_appdir_cuda_path", lambda: str(cuda))
    monkeypatch.setattr(download_utils, "free_space_bytes", lambda path: 10 ** 12)
    monkeypatch.setattr(cuda_download, "_pypi_wheel",
                        lambda pkg: (pkg, f"https://x/{pkg}.whl", f"{pkg}.whl", "ab" * 32, 1000))
    state = type("PyPI", (), {"libraries": LIBRARIES, "sha256s": [], "cuda": cuda})()

    def download_file(url, path, expected_sha256=None, progress_hook=None, **kwargs):
        state.sha256s.append(expected_sha256)
        mine = [name for name in state.libraries
                if name.startswith("cuda_runtime") == ("runtime" in url)]
        _wheel(path, 10, mine)
        if progress_hook:
            for done in (0, 500, 1000):
                progress_hook(done, 1000)
        return True
    monkeypatch.setattr(download_utils, "download_file", download_file)
    return state


def _install_complete_cuda(cuda):
    for name in LIBRARIES:
        (cuda / "lib" / name).parent.mkdir(parents=True, exist_ok=True)
        (cuda / "lib" / name).write_text("the working one")


def test_a_download_installs_every_library(pypi):
    assert cuda_download.download_cuda_libraries()
    assert cuda_helper.has_talktype_cuda_libraries()
    assert sorted(os.listdir(pypi.cuda)) == ["lib"], "the unpacking folder was left behind"


def test_each_wheel_is_checked_against_pypis_sha256(pypi):
    assert cuda_download.download_cuda_libraries()
    assert pypi.sha256s == ["ab" * 32] * len(cuda_download.CUDA_PACKAGES)


def test_a_download_missing_a_library_is_not_installed(pypi):
    """A wheel laid out differently upstream read "Downloaded!", and then
    CUDA's files were missing."""
    pypi.libraries = LIBRARIES[:2]
    reports = []
    assert not cuda_download.download_cuda_libraries(lambda m, p: reports.append((m, p)))
    assert download_utils.failure_reason(*reports[-1]) == (
        "The CUDA download didn't hold every library it needs")
    assert os.listdir(pypi.cuda) == []


def test_a_failed_re_download_leaves_the_working_cuda_as_it_was(pypi, monkeypatch):
    """Unpacking went straight into the working install, so a failure
    part-way left old and new files mixed, and the cleanup kept them."""
    _install_complete_cuda(pypi.cuda)

    def disk_full(wheel, so_files, report, done, of):
        target = so_files[0][1]
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as half:
            half.write("half")
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(cuda_download, "_unpack_wheel", disk_full)

    assert not cuda_download.download_cuda_libraries()
    for name in LIBRARIES:
        assert (pypi.cuda / "lib" / name).read_text() == "the working one"
    assert sorted(os.listdir(pypi.cuda)) == ["lib"]


def test_a_re_download_replaces_the_old_libraries(pypi):
    _install_complete_cuda(pypi.cuda)
    (pypi.cuda / "lib" / "cudnn").mkdir()
    assert cuda_download.download_cuda_libraries()
    assert (pypi.cuda / "lib" / LIBRARIES[0]).read_bytes() == b"\0" * 10
    assert not (pypi.cuda / "lib" / "cudnn").exists()


def test_too_little_room_for_the_unpacked_libraries_says_so_before_unpacking(tmp_path,
                                                                            monkeypatch):
    """The download size passed the check, but unpacked the libraries need
    more: say how much, rather than fail half-way with a generic error."""
    wheel = _wheel(tmp_path / "cublas.whl", 300 * 1024 * 1024)
    lib = tmp_path / "lib"
    monkeypatch.setattr(download_utils, "free_space_bytes", lambda path: 200 * 1024 * 1024)

    with pytest.raises(cuda_download._Stop) as stop:
        cuda_download._extract_wheels([wheel], str(lib), lambda text, percent: None, None)
    assert stop.value.args[0] == ("Not enough disk space to install: need ~0.4 GB free, "
                                  "only 0.2 GB available")
    assert not lib.exists()


def test_the_libraries_unpack_into_their_package_folders(tmp_path, monkeypatch):
    wheel = _wheel(tmp_path / "cublas.whl", 1000)
    lib = tmp_path / "lib"
    monkeypatch.setattr(download_utils, "free_space_bytes", lambda path: 10 * 1024 ** 3)

    cuda_download._extract_wheels([wheel], str(lib), lambda text, percent: None, None)
    unpacked = lib / "cublas" / "lib" / "libcublas.so.12"
    assert unpacked.stat().st_size == 1000 and os.access(unpacked, os.X_OK)


def test_no_wheel_on_pypi_is_the_reason_given(tmp_path, monkeypatch):
    """It ended on "Looking up CUDA packages...", shown as the reason."""
    monkeypatch.setattr(cuda_helper, "get_appdir_cuda_path", lambda: str(tmp_path / "cuda"))

    def none_for_us(pkg_name):
        raise LookupError(f"PyPI has no {pkg_name} for this computer")
    monkeypatch.setattr(cuda_download, "_pypi_wheel", none_for_us)
    reports = []
    assert not cuda_download.download_cuda_libraries(lambda m, p: reports.append((m, p)))
    assert download_utils.failure_reason(*reports[-1]) == (
        "PyPI has no nvidia-cuda-runtime-cu12 for this computer")


def test_the_download_bar_never_goes_backwards(pypi):
    """With two packages it was laid out for three, so the second one's
    progress dropped the bar from 35 % to 23 %."""
    percents = []
    assert cuda_download.download_cuda_libraries(lambda message, percent: percents.append(percent))
    assert percents == sorted(percents) and percents[-1] == 100


def _cut_off_between_the_renames(cuda):
    """What a TuxTalk killed in the middle of the swap leaves: the working
    copy moved aside as lib.old and nothing at lib yet."""
    _install_complete_cuda(cuda)
    (cuda / "lib").rename(cuda / "lib.old")


def test_an_install_cut_off_between_its_renames_is_put_back_at_start(pypi):
    """CUDA read as not downloaded although the whole copy was in lib.old,
    and a later download could delete it."""
    _cut_off_between_the_renames(pypi.cuda)
    cuda_download.recover_cut_off_install()
    assert cuda_helper.has_talktype_cuda_libraries()
    assert (pypi.cuda / "lib" / LIBRARIES[0]).read_text() == "the working one"
    assert sorted(os.listdir(pypi.cuda)) == ["lib"]


def test_a_retry_that_fails_still_puts_a_cut_off_install_back(pypi, monkeypatch):
    _cut_off_between_the_renames(pypi.cuda)

    def offline(pkg):
        raise OSError("no network")
    monkeypatch.setattr(cuda_download, "_pypi_wheel", offline)
    assert not cuda_download.download_cuda_libraries()
    assert (pypi.cuda / "lib" / LIBRARIES[0]).read_text() == "the working one"


def test_an_unfinished_unpacking_folder_is_removed_however_recent(pypi):
    """A kill mid-unpack left up to 1.5 GB in a hidden folder, swept only
    when it was ten minutes old and only by another download."""
    _install_complete_cuda(pypi.cuda)
    (pypi.cuda / ".lib.0123abcd.d" / "cublas").mkdir(parents=True)
    cuda_download.recover_cut_off_install()
    assert sorted(os.listdir(pypi.cuda)) == ["lib"]
    (pypi.cuda / ".lib.4567cdef.d").mkdir()
    assert cuda_download.download_cuda_libraries()
    assert sorted(os.listdir(pypi.cuda)) == ["lib"]


def test_a_failed_attempt_never_removes_what_another_one_installed(pypi, monkeypatch):
    """Two attempts started with no CUDA; the one that failed deleted the
    whole folder, with the other's finished install in it."""
    fetch = download_utils.download_file
    attempts = []

    def other_attempt_finishes_meanwhile(url, path, **kwargs):
        if not attempts:
            attempts.append("other")
            assert cuda_download.download_cuda_libraries()
            return False                    # then this attempt's own download fails
        return fetch(url, path, **kwargs)
    monkeypatch.setattr(download_utils, "download_file", other_attempt_finishes_meanwhile)
    assert not cuda_download.download_cuda_libraries()
    assert cuda_helper.has_talktype_cuda_libraries()


def test_a_cancel_while_waiting_for_the_install_lock_installs_nothing(pypi, monkeypatch):
    """Cancel closes the window at once; an attempt still waiting for its
    turn went on to replace the libraries anyway."""
    import threading
    _install_complete_cuda(pypi.cuda)
    cancel, turns = threading.Event(), []

    @contextlib.contextmanager
    def turn(name, what, wait_s=120.0):
        turns.append(name)
        if len(turns) == 2:                 # the install's turn: Cancel came meanwhile
            cancel.set()
        yield
    monkeypatch.setattr(download_utils, "install_turn", turn)
    unpacked = []
    monkeypatch.setattr(cuda_download, "_extract_wheels", lambda *a: unpacked.append(a))
    assert not cuda_download.download_cuda_libraries(cancel_event=cancel)
    assert unpacked == [], "it unpacked after the Cancel"
    assert (pypi.cuda / "lib" / LIBRARIES[0]).read_text() == "the working one"
    assert sorted(os.listdir(pypi.cuda)) == ["lib"]


def test_a_cancel_during_the_last_wheels_unpack_installs_nothing(pypi, monkeypatch):
    """The window closes at the Cancel; the libraries mustn't go in after it."""
    import threading
    _install_complete_cuda(pypi.cuda)
    cancel = threading.Event()
    extract = cuda_download._extract_wheels

    def cancelled_as_it_ends(*args):
        extract(*args)
        cancel.set()
    monkeypatch.setattr(cuda_download, "_extract_wheels", cancelled_as_it_ends)
    assert not cuda_download.download_cuda_libraries(cancel_event=cancel)
    assert (pypi.cuda / "lib" / LIBRARIES[0]).read_text() == "the working one"
    assert sorted(os.listdir(pypi.cuda)) == ["lib"]


def test_a_tray_starting_during_the_unpack_leaves_the_unpacking_folder_alone(pypi, monkeypatch):
    """The sweep at start removes unpacking folders whatever their age. It
    is safe only because unpacking holds the install lock: a sweep that
    didn't wait for it took the folder away, and the install failed."""
    extract = cuda_download._extract_wheels
    seen = []

    def tray_starts_meanwhile(wheel_paths, staging, report, cancel_event):
        extract(wheel_paths, staging, report, cancel_event)
        cuda_download.recover_cut_off_install()
        seen.append(os.path.isdir(staging))
    monkeypatch.setattr(cuda_download, "_extract_wheels", tray_starts_meanwhile)
    assert cuda_download.download_cuda_libraries()
    assert seen == [True]
    assert cuda_helper.has_talktype_cuda_libraries()


def test_a_wheel_pypi_lists_without_a_checksum_is_not_downloaded(monkeypatch):
    """Without PyPI's SHA256 the download went ahead unchecked and the log
    said "Downloaded and verified"."""
    import urllib.request
    entry = {"packagetype": "bdist_wheel", "url": "https://x/w.whl", "size": 1,
             "filename": "nvidia_cublas_cu12-12.9-py3-none-manylinux_2_27_x86_64.whl"}
    for digests in ({}, {"sha256": ""}, {"sha256": "not-a-hash"}):
        body = json.dumps({"urls": [dict(entry, digests=digests)]}).encode()
        monkeypatch.setattr(urllib.request, "urlopen",
                            lambda request, timeout, body=body:
                            contextlib.nullcontext(io.BytesIO(body)))
        with pytest.raises(LookupError, match="no checksum"):
            cuda_download._pypi_wheel("nvidia-cublas-cu12")
