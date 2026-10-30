"""Tests for cuda_helper module"""
import os

import pytest

from unittest.mock import patch, Mock
from tuxtalk.cuda_helper import (
    detect_nvidia_gpu,
    has_cuda_libraries,
    get_appdir_cuda_path,
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


@patch('subprocess.run', side_effect=PermissionError)
def test_an_nvidia_smi_that_cannot_run_means_no_nvidia_card(mock_run):
    """A broken driver install leaves nvidia-smi without its execute bit:
    no NVIDIA card, rather than an error out of every caller."""
    assert detect_nvidia_gpu() is False


@patch('subprocess.run')
def test_detect_nvidia_gpu_timeout(mock_run):
    """Test GPU detection handles timeout gracefully"""
    import subprocess
    mock_run.side_effect = subprocess.TimeoutExpired(cmd='nvidia-smi', timeout=5)
    result = detect_nvidia_gpu()
    assert result is False


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
def test_has_cuda_libraries_os_error(mock_listdir, mock_exists, mock_run, _cuda_path, caplog):
    """A folder that can't be read is skipped, and the log says which and why."""
    # Mock ldconfig to fail (first check in has_cuda_libraries)
    import subprocess
    mock_run.side_effect = subprocess.TimeoutExpired(cmd='ldconfig', timeout=2)

    # Mock directory checks to raise OSError
    mock_exists.return_value = True
    mock_listdir.side_effect = OSError("Disk error")
    caplog.set_level("INFO")

    # Should not crash, should return False
    result = has_cuda_libraries()
    assert result is False
    assert "Couldn't look in /usr/lib64 for CUDA's libraries: Disk error" in caplog.text


LDCONFIG_HEADER = "1234 libs found in cache `/etc/ld.so.cache'\n"


def _ldconfig(*names):
    return Mock(returncode=0, stdout=LDCONFIG_HEADER + "".join(
        f"\t{n} (libc6,x86-64) => /usr/lib64/{n}\n" for n in names))


def _no_other_cuda(monkeypatch):
    from tuxtalk import cuda_helper
    monkeypatch.setattr(cuda_helper, "get_appdir_cuda_path", lambda: "/nonexistent/cuda")
    monkeypatch.setattr(cuda_helper.os.path, "exists", lambda p: False)


def test_system_cuda_without_cublas_is_not_enough(monkeypatch):
    """With a distro CUDA runtime but no cuBLAS, the model would load on CUDA
    and every dictation would fail."""
    _no_other_cuda(monkeypatch)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _ldconfig("libcudart.so.12"))
    assert has_cuda_libraries() is False


@pytest.mark.parametrize("major", ["11", "13"])
def test_system_cuda_of_another_major_version_is_not_enough(monkeypatch, major):
    """The speech engine opens CUDA 12's libraries by name."""
    _no_other_cuda(monkeypatch)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _ldconfig(
        f"libcudart.so.{major}", f"libcublas.so.{major}", f"libcublasLt.so.{major}"))
    assert has_cuda_libraries() is False


def test_system_cuda_without_cublaslt_is_not_enough(monkeypatch):
    _no_other_cuda(monkeypatch)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _ldconfig(
        "libcudart.so.12", "libcublas.so.12"))
    assert has_cuda_libraries() is False


def test_system_cuda_runtime_and_cublas_are_enough_without_cudnn(monkeypatch):
    _no_other_cuda(monkeypatch)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _ldconfig(
        "libcudart.so.12", "libcublas.so.12", "libcublasLt.so.12"))
    assert has_cuda_libraries() is True


def test_a_runtime_alone_in_a_system_folder_is_not_enough(monkeypatch, tmp_path):
    from tuxtalk import cuda_helper
    monkeypatch.setattr(cuda_helper, "get_appdir_cuda_path", lambda: "/nonexistent/cuda")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _ldconfig())
    monkeypatch.setattr(cuda_helper.os.path, "exists", lambda p: p == "/usr/lib64")
    monkeypatch.setattr(cuda_helper.os, "listdir", lambda p: ["libcudart.so.12", "libc.so.6"])
    assert has_cuda_libraries() is False


def test_an_nvidia_card_name_in_another_encoding_is_still_a_card(tmp_path, monkeypatch):
    """nvidia-smi's answer is read as text; one odd byte in the card's name
    mustn't raise out of the welcome screen or the Performance menu."""
    from shell_stubs import stub
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    stub(tmp_path, "nvidia-smi", r"printf 'NVIDIA GeForce RTX 4070 \377\n'")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert "RTX 4070" in detect_nvidia_gpu()
