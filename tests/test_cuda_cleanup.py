"""What is in TuxTalk's CUDA folder: when an install counts as complete, and
what earlier versions left there (the unused cuDNN, PyTorch's link) going
at start. A failed or cut-off download leaving the CUDA you had is
test_cuda_download's."""

import os

import pytest

from tuxtalk import cuda_download, cuda_helper


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


def test_the_real_nested_layout_counts_as_complete(cuda_dir):
    """lib/ itself holds only folders, so the check must look inside them, or
    a real install never counts and a failed re-download deletes it."""
    _install_complete_cuda(cuda_dir)
    assert cuda_helper.cuda_install_looks_complete(str(cuda_dir))
    assert cuda_helper.has_talktype_cuda_libraries()


def test_an_install_without_cublas_is_not_complete(cuda_dir):
    _install_complete_cuda(cuda_dir)
    (cuda_dir / "lib" / "cublas" / "lib" / "libcublas.so.12").unlink()
    assert not cuda_helper.cuda_install_looks_complete(str(cuda_dir))


def test_the_cudnn_earlier_versions_downloaded_is_removed(cuda_dir):
    """Nearly 1 GB nothing uses any more; the rest of CUDA stays."""
    kept = _install_complete_cuda(cuda_dir)
    (cuda_dir / "lib" / "cudnn" / "lib").mkdir(parents=True)
    (cuda_dir / "lib" / "cudnn" / "lib" / "libcudnn.so.9").write_text("x")
    cuda_helper.remove_unused_cudnn()
    assert sorted(os.listdir(cuda_dir / "lib")) == kept
    assert cuda_helper.cuda_install_looks_complete(str(cuda_dir))


def test_no_cudnn_folder_is_nothing_to_do(cuda_dir):
    kept = _install_complete_cuda(cuda_dir)
    cuda_helper.remove_unused_cudnn()
    assert sorted(os.listdir(cuda_dir / "lib")) == kept


def test_the_link_earlier_versions_made_for_pytorch_is_removed(cuda_dir):
    kept = _install_complete_cuda(cuda_dir)
    (cuda_dir / "nvidia").symlink_to(cuda_dir / "lib")
    cuda_helper.remove_pytorch_link()
    assert sorted(os.listdir(cuda_dir)) == ["lib"]
    assert sorted(os.listdir(cuda_dir / "lib")) == kept, "the link was followed"


def test_a_real_nvidia_folder_is_left_alone(cuda_dir):
    (cuda_dir / "nvidia").mkdir(parents=True)
    (cuda_dir / "nvidia" / "kept").write_text("x")
    cuda_helper.remove_pytorch_link()
    assert (cuda_dir / "nvidia" / "kept").exists()


def test_cudnn_is_not_downloaded():
    assert not any("cudnn" in name for name in cuda_download.CUDA_PACKAGES)
