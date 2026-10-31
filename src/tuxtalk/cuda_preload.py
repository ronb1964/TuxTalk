"""
Make TuxTalk's downloaded CUDA libraries findable, before any engine loads.

Import this and call preload_cuda_libraries() first. CTranslate2
(faster-whisper) opens libcublas by name, and TuxTalk's own CUDA folder is not
on LD_LIBRARY_PATH, so the libraries are preloaded here with RTLD_GLOBAL. When
no CUDA is usable at all, the GPU is hidden so nothing tries to load it.
"""

import ctypes
import glob
import os
import warnings
from pathlib import Path

from .config import get_data_dir
from .logger import setup_logger

logger = setup_logger(__name__)


def _cuda_lib_dir():
    """Where the CUDA download unpacks to (cuda_helper.get_appdir_cuda_path's
    lib): this copy's data folder, dev or not, the private one in a Flatpak."""
    return Path(get_data_dir()) / "cuda" / "lib"


# Decide at import time, before anything loads CUDA, whether the GPU stays visible:
#   1. TuxTalk's CUDA download exists: leave it visible.
#   2. Otherwise, if CTranslate2 already sees a GPU (system CUDA, or CUDA in a
#      dev venv on LD_LIBRARY_PATH): leave it visible.
#   3. Otherwise set CUDA_VISIBLE_DEVICES='' for CPU-only mode.
if not _cuda_lib_dir().exists():
    try:
        import ctranslate2 as _ct2
        _has_gpu = _ct2.get_cuda_device_count() > 0
        del _ct2
    except Exception as e:      # a broken CUDA driver: the processor it is
        logger.info(f"CTranslate2 couldn't count the CUDA devices: {e}")
        _has_gpu = False

    if not _has_gpu:
        os.environ['CUDA_VISIBLE_DEVICES'] = ''
        logger.info("No CUDA libraries found - forcing CPU-only mode")

    # Without TuxTalk's CUDA download, missing-CUDA-library warnings are expected noise.
    warnings.filterwarnings('ignore', message='Could not load CUDA library.*')
    warnings.filterwarnings('ignore', message='.*not found in the system path.*')

# Set once preload_cuda_libraries has run, so a second call logs nothing.
_initialized = False


def preload_cuda_libraries():
    """Preload the libraries from TuxTalk's CUDA download, once per process.

    cuda_download unpacks each wheel to cuda/lib/<package>/lib/. Loading them with
    RTLD_GLOBAL lets a later open by name (CTranslate2's) find them.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True
    # With no download nothing matches: the module-level check has already
    # decided between the processor and the system's CUDA.
    lib_path = _cuda_lib_dir()

    # Globs, not one soname each: a library may also come under its full
    # version (libcublas.so.12.4.5.8). Only CUDA 12's are of use, though
    # (cuda_helper.REQUIRED_CUDA_LIBS).
    lib_patterns = [
        ("cuda_runtime", "lib", "libcudart.so.*"),
        ("cublas", "lib", "libcublas.so.*"),
        ("cublas", "lib", "libcublasLt.so.*"),
    ]

    loaded_count = 0
    for lib_folder, lib_subdir, lib_pattern in lib_patterns:
        search_path = lib_path / lib_folder / lib_subdir / lib_pattern
        matches = glob.glob(str(search_path))

        if matches:
            lib_file = matches[0]
            try:
                ctypes.CDLL(lib_file, mode=ctypes.RTLD_GLOBAL)
                loaded_count += 1
            except OSError as e:
                logger.warning(f"Failed to preload {Path(lib_file).name}: {e}")

    if loaded_count > 0:
        logger.info(f"Preloaded {loaded_count} CUDA libraries")
