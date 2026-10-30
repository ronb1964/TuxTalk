"""A failed CUDA re-download must not delete the CUDA the user already has.

download_cuda_libraries() cleans up on failure by rmtree'ing the whole CUDA
directory. That is right when the directory is a half-finished download this
run created. It is wrong when a complete installation was already
there: an early failure (no network, pip missing, a cancelled dialog) would
destroy a working GPU setup the user has to download all over again.
"""

import os

import pytest

from tuxtalk import cuda_helper


@pytest.fixture
def cuda_dir(tmp_path, monkeypatch):
    path = tmp_path / "cuda"
    monkeypatch.setattr(cuda_helper, "get_appdir_cuda_path", lambda: str(path))
    return path


def _install_complete_cuda(path):
    """Look like a real, complete CUDA install: the download unpacks each
    wheel into its own folder, lib/<package>/lib/<library>."""
    lib = path / "lib"
    for folder, name in (("cuda_runtime", "libcudart.so.12"), ("cublas", "libcublas.so.12"),
                         ("cublas", "libcublasLt.so.12")):
        (lib / folder / "lib").mkdir(parents=True, exist_ok=True)
        (lib / folder / "lib" / name).write_text("x" * 1000)
    return sorted(os.listdir(lib))


def test_a_pre_existing_install_survives_a_failed_download(cuda_dir):
    before = _install_complete_cuda(cuda_dir)

    cleanup = cuda_helper._make_cuda_cleanup(str(cuda_dir))
    cleanup()

    assert cuda_dir.exists(), "deleted the user's working CUDA installation"
    assert sorted(os.listdir(cuda_dir / "lib")) == before


def test_a_partial_download_is_cleaned_up(cuda_dir):
    """Nothing usable was there before, so removing the debris is correct."""
    lib = cuda_dir / "lib"
    lib.mkdir(parents=True)
    (lib / "libcudart.so.partial").write_text("half a file")

    cleanup = cuda_helper._make_cuda_cleanup(str(cuda_dir))
    cleanup()

    assert not cuda_dir.exists(), "left a broken half-download in place"


def test_the_real_nested_layout_counts_as_complete(cuda_dir):
    """lib/ itself holds only folders, so the check must look inside them, or
    a real install never counts and a failed re-download deletes it."""
    _install_complete_cuda(cuda_dir)
    assert cuda_helper._cuda_install_looks_complete(str(cuda_dir))
    assert cuda_helper.has_talktype_cuda_libraries()


def test_an_install_without_cublas_is_not_complete(cuda_dir):
    _install_complete_cuda(cuda_dir)
    (cuda_dir / "lib" / "cublas" / "lib" / "libcublas.so.12").unlink()
    assert not cuda_helper._cuda_install_looks_complete(str(cuda_dir))


def test_the_cudnn_earlier_versions_downloaded_is_removed(cuda_dir):
    """Nearly 1 GB nothing uses any more; the rest of CUDA stays."""
    kept = _install_complete_cuda(cuda_dir)
    (cuda_dir / "lib" / "cudnn" / "lib").mkdir(parents=True)
    (cuda_dir / "lib" / "cudnn" / "lib" / "libcudnn.so.9").write_text("x")
    cuda_helper.remove_unused_cudnn()
    assert sorted(os.listdir(cuda_dir / "lib")) == kept
    assert cuda_helper._cuda_install_looks_complete(str(cuda_dir))


def test_no_cudnn_folder_is_nothing_to_do(cuda_dir):
    kept = _install_complete_cuda(cuda_dir)
    cuda_helper.remove_unused_cudnn()
    assert sorted(os.listdir(cuda_dir / "lib")) == kept


def test_cudnn_is_not_downloaded():
    assert not any("cudnn" in name for name in cuda_helper.CUDA_PACKAGES)
