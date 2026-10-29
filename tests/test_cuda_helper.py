"""Tests for cuda_helper module"""
import os
from tuxtalk import cuda_helper
from unittest.mock import patch, Mock
from tuxtalk.cuda_helper import (
    detect_nvidia_gpu,
    has_cuda_libraries,
    get_appdir_cuda_path,
    is_first_run,
    mark_first_run_complete
)


def test_get_appdir_cuda_path():
    """The CUDA download goes in TuxTalk's data folder (where that folder is
    on the computer: test_flatpak_private_paths)."""
    from tuxtalk import config
    assert get_appdir_cuda_path() == os.path.join(config.get_data_dir(), "cuda")


@patch('subprocess.run')
def test_detect_nvidia_gpu_found(mock_run):
    """Test GPU detection when nvidia-smi succeeds"""
    mock_run.return_value = Mock(returncode=0, stdout="NVIDIA GeForce RTX 3080\n")
    result = detect_nvidia_gpu()
    assert result == "NVIDIA GeForce RTX 3080"  # .strip() removes the newline


@patch('subprocess.run')
def test_detect_nvidia_gpu_not_found(mock_run):
    """Test GPU detection when nvidia-smi fails"""
    mock_run.return_value = Mock(returncode=1, stdout="")
    result = detect_nvidia_gpu()
    assert result is False


@patch('subprocess.run', side_effect=FileNotFoundError)
def test_detect_nvidia_gpu_no_nvidia_smi(mock_run):
    """Test GPU detection when nvidia-smi not installed"""
    result = detect_nvidia_gpu()
    assert result is False


@patch('subprocess.run')
def test_detect_nvidia_gpu_timeout(mock_run):
    """Test GPU detection handles timeout gracefully"""
    import subprocess
    mock_run.side_effect = subprocess.TimeoutExpired(cmd='nvidia-smi', timeout=5)
    result = detect_nvidia_gpu()
    assert result is False


def test_first_run_flag(tmp_path, monkeypatch):
    """First-run setup shows until it is marked done, then never again."""
    monkeypatch.setattr("tuxtalk.config.get_data_dir", lambda: str(tmp_path / "TuxTalk"))
    assert is_first_run() is True
    mark_first_run_complete()
    assert is_first_run() is False


@patch('tuxtalk.cuda_helper.get_appdir_cuda_path', return_value='/nonexistent/cuda')
@patch('subprocess.run')
@patch('os.path.exists')
@patch('os.listdir')
def test_has_cuda_libraries_permission_error(mock_listdir, mock_exists, mock_run, _cuda_path):
    """Test has_cuda_libraries handles permission errors gracefully"""
    # Mock ldconfig to fail (first check in has_cuda_libraries)
    import subprocess
    mock_run.side_effect = subprocess.TimeoutExpired(cmd='ldconfig', timeout=2)

    # Mock directory checks to raise PermissionError
    mock_exists.return_value = True
    mock_listdir.side_effect = PermissionError("Access denied")

    # Should not crash, should return False
    result = has_cuda_libraries()
    assert result is False


@patch('tuxtalk.cuda_helper.get_appdir_cuda_path', return_value='/nonexistent/cuda')
@patch('subprocess.run')
@patch('os.path.exists')
@patch('os.listdir')
def test_has_cuda_libraries_os_error(mock_listdir, mock_exists, mock_run, _cuda_path):
    """Test has_cuda_libraries handles OS errors gracefully"""
    # Mock ldconfig to fail (first check in has_cuda_libraries)
    import subprocess
    mock_run.side_effect = subprocess.TimeoutExpired(cmd='ldconfig', timeout=2)

    # Mock directory checks to raise OSError
    mock_exists.return_value = True
    mock_listdir.side_effect = OSError("Disk error")

    # Should not crash, should return False
    result = has_cuda_libraries()
    assert result is False


LDCONFIG_HEADER = "1234 libs found in cache `/etc/ld.so.cache'\n"


def _ldconfig(*names):
    return Mock(returncode=0, stdout=LDCONFIG_HEADER + "".join(
        f"\t{n} (libc6,x86-64) => /usr/lib64/{n}\n" for n in names))


def _no_other_cuda(monkeypatch):
    from tuxtalk import cuda_helper
    monkeypatch.setattr(cuda_helper, "get_appdir_cuda_path", lambda: "/nonexistent/cuda")
    monkeypatch.setattr(cuda_helper.os.path, "exists", lambda p: False)


def test_system_cuda_without_cudnn_is_not_enough(monkeypatch):
    """With a distro CUDA runtime but no cuDNN, the model would load on CUDA
    and the first dictation would crash."""
    _no_other_cuda(monkeypatch)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _ldconfig(
        "libcudart.so.12", "libcublas.so.12", "libcublasLt.so.12"))
    assert has_cuda_libraries() is False


def test_system_cuda_with_cudnn_is_found(monkeypatch):
    _no_other_cuda(monkeypatch)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _ldconfig(
        "libcudart.so.12", "libcublas.so.12", "libcudnn.so.9"))
    assert has_cuda_libraries() is True


def test_a_runtime_alone_in_a_system_folder_is_not_enough(monkeypatch, tmp_path):
    from tuxtalk import cuda_helper
    monkeypatch.setattr(cuda_helper, "get_appdir_cuda_path", lambda: "/nonexistent/cuda")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _ldconfig())
    monkeypatch.setattr(cuda_helper.os.path, "exists", lambda p: p == "/usr/lib64")
    monkeypatch.setattr(cuda_helper.os, "listdir", lambda p: ["libcudart.so.12", "libc.so.6"])
    assert has_cuda_libraries() is False


def _wheel(path, size):
    """A CUDA wheel holding one library of *size* bytes, which compresses
    to almost nothing, as the real ones shrink by half or more."""
    import zipfile
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as wheel:
        wheel.writestr("nvidia/cublas/lib/libcublas.so.12", b"\0" * size)
        wheel.writestr("nvidia_cublas_cu12-12.4.dist-info/METADATA", "Name: x")
    return str(path)


def test_too_little_room_for_the_unpacked_libraries_says_so_before_unpacking(tmp_path,
                                                                            monkeypatch):
    """The download size passed the check, but unpacked the libraries need
    more: say how much, rather than fail half-way with a generic error."""
    from tuxtalk import download_utils
    wheel = _wheel(tmp_path / "cublas.whl", 300 * 1024 * 1024)
    lib = tmp_path / "lib"
    monkeypatch.setattr(download_utils, "free_space_bytes", lambda path: 200 * 1024 * 1024)
    said = []

    assert not cuda_helper._extract_wheels([wheel], str(lib),
                                           lambda text, percent: said.append(text), None)
    assert said == ["Not enough disk space to install: need ~0.4 GB free, "
                    "only 0.2 GB available"]
    assert not lib.exists()


def test_the_libraries_unpack_into_their_package_folders(tmp_path, monkeypatch):
    from tuxtalk import download_utils
    wheel = _wheel(tmp_path / "cublas.whl", 1000)
    lib = tmp_path / "lib"
    monkeypatch.setattr(download_utils, "free_space_bytes", lambda path: 10 * 1024 ** 3)

    assert cuda_helper._extract_wheels([wheel], str(lib), None, None)
    unpacked = lib / "cublas" / "lib" / "libcublas.so.12"
    assert unpacked.stat().st_size == 1000 and os.access(unpacked, os.X_OK)


def test_the_cuda_window_says_why_the_download_failed(monkeypatch):
    from tuxtalk import ui_style
    said = []
    monkeypatch.setattr(ui_style, "show_message",
                        lambda parent, kind, title, text, **k: said.append(text))
    cuda_helper._say_how_it_went(None, False, None, "Not enough disk space to install: "
                                                    "need ~2.9 GB free, only 0.2 GB available")
    assert said[0].startswith("Not enough disk space to install")


def test_no_wheel_on_pypi_is_the_reason_given(tmp_path, monkeypatch):
    """It ended on "Looking up CUDA packages...", shown as the reason."""
    from tuxtalk.download_utils import failure_reason
    monkeypatch.setattr(cuda_helper, "get_appdir_cuda_path", lambda: str(tmp_path / "cuda"))

    def none_for_us(pkg_name):
        raise LookupError(f"PyPI has no {pkg_name} for this computer")
    monkeypatch.setattr(cuda_helper, "_pypi_wheel", none_for_us)
    reports = []
    assert not cuda_helper.download_cuda_libraries(lambda m, p: reports.append((m, p)))
    assert failure_reason(*reports[-1]) == "PyPI has no nvidia-cuda-runtime-cu12 for this computer"
