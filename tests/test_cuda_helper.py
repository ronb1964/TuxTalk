"""Tests for cuda_helper module"""
import os
from unittest.mock import patch, Mock
from tuxtalk.cuda_helper import (
    detect_nvidia_gpu,
    has_cuda_libraries,
    get_appdir_cuda_path,
    is_first_run,
    mark_first_run_complete
)


def test_get_appdir_cuda_path():
    """Test CUDA path returns expected location"""
    path = get_appdir_cuda_path()
    assert path.endswith(".local/share/TuxTalk/cuda")
    assert os.path.expanduser("~") in path


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
