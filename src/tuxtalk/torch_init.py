#!/usr/bin/env python3
"""
Make TuxTalk's downloaded CUDA libraries findable, before any engine loads.

Import this first. CTranslate2 (faster-whisper) opens libcublas by name, and
TuxTalk's own CUDA folder is not on LD_LIBRARY_PATH, so the libraries are
preloaded here with RTLD_GLOBAL. When no CUDA is
usable at all, the GPU is hidden so nothing tries to load it.
"""

import sys
import os
import ctypes
import warnings
from pathlib import Path

from .logger import setup_logger

logger = setup_logger(__name__)

# Decide at import time, before anything loads CUDA, whether the GPU stays visible:
#   1. TuxTalk's CUDA download exists: leave it visible.
#   2. Otherwise, if CTranslate2 already sees a GPU (system CUDA, or CUDA in a
#      dev venv on LD_LIBRARY_PATH): leave it visible.
#   3. Otherwise set CUDA_VISIBLE_DEVICES='' for CPU-only mode.
_appimage_cuda_path = Path.home() / ".local" / "share" / "TuxTalk" / "cuda" / "lib"
_dev_cuda_path = Path.home() / ".local" / "share" / "TuxTalk-dev" / "cuda" / "lib"

if not _appimage_cuda_path.exists() and not _dev_cuda_path.exists():
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

# Set once init_cuda_for_pytorch has run, so a second call logs nothing.
_initialized = False


def init_cuda_for_pytorch():
    """Preload the libraries from TuxTalk's CUDA download, once per process.

    cuda_helper unpacks each wheel to cuda/lib/<package>/lib/. Loading them with
    RTLD_GLOBAL lets a later open by name (CTranslate2's) find them. The nvidia
    symlink and sys.path entry recreate the layout PyTorch's own search expects.

    Returns False when there is nothing to load; a repeat call returns True.
    """
    global _initialized
    if _initialized:
        return True

    from .cuda_helper import get_appdir_cuda_path
    cuda_base = Path(get_appdir_cuda_path())
    lib_path = cuda_base / "lib"

    if not lib_path.exists():
        # The module-level check has already decided CPU or system CUDA.
        _initialized = True
        return False

    # PyTorch looks in {sys.path}/nvidia/<package>/lib/; cuda/nvidia -> cuda/lib
    # provides that layout.
    nvidia_link = cuda_base / "nvidia"
    if not nvidia_link.exists():
        try:
            nvidia_link.symlink_to(lib_path)
            logger.info("Created nvidia symlink for PyTorch")
        except OSError as e:
            logger.warning(f"Failed to create nvidia symlink: {e}")

    cuda_str = str(cuda_base)
    if cuda_str not in sys.path:
        sys.path.insert(0, cuda_str)
        logger.info(f"Added {cuda_str} to sys.path for PyTorch")

    # Globs, not fixed sonames, so any CUDA major version loads.
    import glob

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
        logger.info(f"Preloaded {loaded_count} CUDA libraries for PyTorch")
        _initialized = True
        return True

    _initialized = True
    return False


# Importing the module is enough to preload.
if __name__ != "__main__":
    init_cuda_for_pytorch()
