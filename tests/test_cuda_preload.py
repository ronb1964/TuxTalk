"""TuxTalk's CUDA download is loaded before any engine, so CTranslate2 finds
cuBLAS by name: the runtime and both cuBLAS libraries, from this copy's own
data folder, once."""
import ctypes

import pytest

from tuxtalk import cuda_preload


@pytest.fixture
def loaded(tmp_path, monkeypatch):
    """The libraries preload_cuda_libraries() opens, by file name, with the
    data folder in tmp_path and no real library loaded."""
    opened = []
    monkeypatch.setattr(cuda_preload, "get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cuda_preload, "_initialized", False)
    monkeypatch.setattr(ctypes, "CDLL",
                        lambda path, mode: opened.append((path.rsplit("/", 1)[-1], mode)))
    return opened


def _download(data_dir, *files):
    for folder, name in files:
        lib = data_dir / "cuda" / "lib" / folder / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        (lib / name).write_text("x")


def test_the_runtime_and_cublas_are_loaded_for_everyone_to_find(tmp_path, loaded):
    _download(tmp_path, ("cuda_runtime", "libcudart.so.12"), ("cublas", "libcublas.so.12"),
              ("cublas", "libcublasLt.so.12"), ("cudnn", "libcudnn.so.9"))
    cuda_preload.preload_cuda_libraries()
    assert loaded == [("libcudart.so.12", ctypes.RTLD_GLOBAL),
                      ("libcublas.so.12", ctypes.RTLD_GLOBAL),
                      ("libcublasLt.so.12", ctypes.RTLD_GLOBAL)]


def test_they_are_loaded_once_per_process(tmp_path, loaded):
    _download(tmp_path, ("cuda_runtime", "libcudart.so.12"))
    cuda_preload.preload_cuda_libraries()
    cuda_preload.preload_cuda_libraries()
    assert [name for name, _mode in loaded] == ["libcudart.so.12"]


def test_without_the_download_nothing_is_loaded(loaded):
    cuda_preload.preload_cuda_libraries()
    assert loaded == []
