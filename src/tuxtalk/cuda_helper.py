#!/usr/bin/env python3
"""Whether this computer has an NVIDIA graphics card and CUDA libraries to run
the Whisper models on it: TuxTalk's own download (cuda_download) or the
system's. Also tidies what earlier versions left in the download's folder."""

import os
import subprocess
from .logger import setup_logger

# NVIDIA's CUDA libraries, downloaded once when CUDA is picked: the size
# Preferences, System check, the setup choices and Help give.
DOWNLOAD_SIZE_TEXT = "580 MB"

logger = setup_logger(__name__)

# Timeout constants (in seconds)
NVIDIA_SMI_TIMEOUT = 5
SUBPROCESS_TIMEOUT = 2

# The libraries a CUDA install needs here: CTranslate2 opens CUDA 12's cuBLAS
# and cuBLASLt by these names, so another major version (11, 13) doesn't do;
# the runtime comes with the same download and stays a sign of a whole one.
# Without them the model loads "on cuda" and every dictation fails inside the
# speech library (libcublas.so.12 not found). cuDNN isn't among them:
# CTranslate2's Linux wheels are built without it since 4.7 (TuxTalk 0.16).
REQUIRED_CUDA_LIBS = ("libcudart.so.12", "libcublas.so.12", "libcublasLt.so.12")

def detect_nvidia_gpu():
    """Check if an NVIDIA GPU is present.

    The host asks nvidia-smi. A Flatpak sandbox has no nvidia-smi, so there the
    driver's device nodes (/dev/nvidiactl, /dev/nvidia0, exposed by the
    manifest's --device=dri) are checked instead, which also works when
    cuda_preload has cleared CUDA_VISIBLE_DEVICES.
    """
    if os.environ.get("FLATPAK_ID"):
        return os.path.exists("/dev/nvidiactl") or os.path.exists("/dev/nvidia0")
    try:
        # errors="replace": an odd byte in the card's name is still a card.
        result = subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                                capture_output=True, text=True, errors="replace",
                                timeout=NVIDIA_SMI_TIMEOUT)
        return result.returncode == 0 and result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):    # not installed, not runnable, or hung
        return False

def _has_required_libs(names) -> bool:
    """True if *names* (library file names) include every required library."""
    names = list(names)
    return all(any(name.startswith(lib) for name in names)
               for lib in REQUIRED_CUDA_LIBS)


def holds_required_libs(folder: str) -> bool:
    """True if *folder*, subfolders included, holds every required library.

    The download unpacks each wheel into its own folder (lib/cublas/lib/...,
    lib/cuda_runtime/lib/...), so the whole tree is searched.
    """
    names = []
    for _root, _dirs, files in os.walk(folder):
        names.extend(files)
    return _has_required_libs(names)


def cuda_install_looks_complete(cuda_path: str) -> bool:
    """True if *cuda_path* holds every required library of a usable install.
    A directory holding only the debris of an interrupted download does not
    read as complete."""
    lib_path = os.path.join(cuda_path, "lib")
    return os.path.isdir(lib_path) and holds_required_libs(lib_path)


def remove_unused_cudnn():
    """Delete the cuDNN an earlier TuxTalk downloaded with CUDA (lib/cudnn):
    nothing uses it, and it takes nearly 1 GB. Never raises."""
    import shutil
    cudnn = os.path.join(get_appdir_cuda_path(), "lib", "cudnn")
    # A link there isn't TuxTalk's download: leave it and what it points at.
    if not os.path.isdir(cudnn) or os.path.islink(cudnn):
        return
    try:
        shutil.rmtree(cudnn)
    except OSError as e:
        logger.warning(f"Couldn't remove the unused cuDNN in {cudnn}: {e}")
        return
    logger.info("Removed the cuDNN earlier versions downloaded with CUDA; nothing uses it")


def remove_pytorch_link():
    """Delete the cuda/nvidia link to cuda/lib that versions before 0.19 made
    for PyTorch, which TuxTalk doesn't use. Only a link: a real folder there
    isn't TuxTalk's. Never raises."""
    link = os.path.join(get_appdir_cuda_path(), "nvidia")
    if not os.path.islink(link):
        return
    try:
        os.unlink(link)
    except OSError as e:
        logger.warning(f"Couldn't remove the unused link {link}: {e}")
        return
    logger.info("Removed the cuda/nvidia link earlier versions made for PyTorch")


def has_cuda_libraries():
    """
    Check if CUDA libraries are available for running GPU-accelerated models.
    Each place must hold all of REQUIRED_CUDA_LIBS:
    1. Downloaded CUDA in TuxTalk data dir (respects DEV_MODE)
    2. The system's library cache (ldconfig)
    3. CUDA toolkit installation (/usr/local/cuda, /opt/cuda)
    4. Standard /usr library paths (distro-packaged CUDA)
    """
    # get_appdir_cuda_path() respects DEV_MODE, so the dev version checks
    # ~/.local/share/TuxTalk-dev/cuda and the AppImage ~/.local/share/TuxTalk/cuda.
    if cuda_install_looks_complete(get_appdir_cuda_path()):
        return True

    # Check system-wide CUDA installation via ldconfig (most reliable)
    try:
        result = subprocess.run(['ldconfig', '-p'], capture_output=True, text=True,
                                errors="replace", timeout=SUBPROCESS_TIMEOUT)
        # Lines look like "\tlibcudart.so.12 (libc6,x86-64) => /usr/lib64/libcudart.so.12".
        names = [line.split()[0] for line in result.stdout.splitlines()[1:] if line.strip()]
        if _has_required_libs(names):
            return True
    except (OSError, subprocess.SubprocessError) as e:
        logger.info(f"Couldn't ask ldconfig for CUDA's libraries: {e}")

    lib_dirs = [
        '/usr/local/cuda/lib64',
        '/usr/local/cuda/lib',
        '/opt/cuda/lib64',
        '/opt/cuda/lib',
        '/usr/lib/x86_64-linux-gnu',
        '/usr/lib64',
        '/usr/lib',
    ]
    for lib_dir in lib_dirs:
        if os.path.exists(lib_dir):
            try:
                if _has_required_libs(os.listdir(lib_dir)):
                    return True
            except (PermissionError, OSError):
                pass

    return False

def get_appdir_cuda_path():
    """Get the path where CUDA libraries should be stored."""
    from .config import get_data_dir
    return os.path.join(get_data_dir(), "cuda")

def has_talktype_cuda_libraries():
    """True if TuxTalk's own CUDA download is complete in its data dir.

    System CUDA is not considered, so this is for showing download status in
    the UI; has_cuda_libraries() answers whether CUDA can run.
    """
    return cuda_install_looks_complete(get_appdir_cuda_path())

def is_first_run():
    """Check if this is the first time the app is run."""
    from .config import get_data_dir
    flag_file = os.path.join(get_data_dir(), ".first_run_done")
    return not os.path.exists(flag_file)

def mark_first_run_complete():
    """Mark that the first run has been completed."""
    from .config import get_data_dir
    flag_file = os.path.join(get_data_dir(), ".first_run_done")
    os.makedirs(os.path.dirname(flag_file), exist_ok=True)
    with open(flag_file, 'w') as f:
        f.write('done')
