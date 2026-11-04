#!/usr/bin/env python3
"""Whether this computer has an NVIDIA graphics card and CUDA libraries to run
the Whisper models on it: TuxTalk's own download (cuda_download) or the
system's, and whether the installed speech library was built to use them.
Also tidies what earlier versions left in the download's folder."""

import importlib.util
import os
import subprocess
import sys
from typing import Optional
from . import installation
from .logger import setup_logger

logger = setup_logger(__name__)

# Timeout constants (in seconds)
NVIDIA_SMI_TIMEOUT = 5
SUBPROCESS_TIMEOUT = 2
# The CTranslate2 check imports the speech library in a new Python, which
# takes a few seconds when nothing of it is cached yet.
CTRANSLATE2_CHECK_TIMEOUT = 10

# The libraries a CUDA install needs here: CTranslate2 opens CUDA 12's cuBLAS
# and cuBLASLt by these names, so another major version (11, 13) doesn't do;
# the runtime comes with the same download and stays a sign of a whole one.
# Without them the model loads "on cuda" and every dictation fails inside the
# speech library (libcublas.so.12 not found). cuDNN isn't among them:
# CTranslate2's Linux wheels are built without it since 4.7 (TuxTalk 0.16).
REQUIRED_CUDA_LIBS = ("libcudart.so.12", "libcublas.so.12", "libcublasLt.so.12")

# Run in a separate Python: importing CTranslate2 here would load it into
# Preferences. A build without CUDA refuses the device name with this
# ValueError (str_to_device in its devices.cc); a build with CUDA but no card
# usable now fails otherwise, and still counts.
_CUDA_PROBE = (
    "import ctranslate2\n"
    "try:\n    ctranslate2.get_supported_compute_types('cuda')\n"
    "except ValueError as e:\n    print('no-cuda' if 'compiled with CUDA' in str(e) else 'cuda')\n"
    "except Exception:\n    print('cuda')\n"
    "else:\n    print('cuda')\n")

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

def probe_ctranslate2_cuda(run=subprocess.run, python: Optional[str] = None) -> Optional[bool]:
    """Whether the CTranslate2 that *python* (this one by default) imports was
    built with CUDA. Hidden from every graphics card, so the check never wakes
    one. None when there's no clean answer (a hang, a crash, no CTranslate2).

    Not isolated (-I): TuxTalk itself finds a CTranslate2 the user installed
    with pip --user or through PYTHONPATH, so the check must find the same
    one. It runs in / instead, so the current folder that -c puts first on
    the path can't offer a stray ctranslate2.py."""
    argv = [python or sys.executable, "-c", _CUDA_PROBE]
    try:
        result = run(argv, env={**os.environ, "CUDA_VISIBLE_DEVICES": ""}, cwd="/",
                     capture_output=True, text=True, errors="replace",
                     timeout=CTRANSLATE2_CHECK_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        why = str(e)
    else:
        answer = result.stdout.strip()
        if result.returncode == 0 and answer == "cuda":
            return True
        if result.returncode == 0 and answer == "no-cuda":
            logger.info("The installed CTranslate2 was built without CUDA; not offering CUDA")
            return False
        why = f"exit {result.returncode}, {answer or result.stderr.strip()[-200:]!r}"
    logger.info(f"CTranslate2's CUDA check didn't answer ({why}); not offering CUDA")
    return None


def _ctranslate2_outside(prefix: str) -> bool:
    """Whether the CTranslate2 this Python finds lies outside *prefix*. Found,
    not imported: importing it would load the speech library into Preferences.
    None found counts as outside, so the check runs and says there is none."""
    spec = importlib.util.find_spec("ctranslate2")
    if spec is None or not spec.origin:
        return True
    return not os.path.realpath(spec.origin).startswith(os.path.realpath(prefix) + os.sep)


# ctranslate2_has_cuda's answer, once there is one to keep, and how many of
# its checks didn't answer. A check that hangs waits up to
# CTRANSLATE2_CHECK_TIMEOUT, on GTK's thread in Preferences, so after
# CHECK_TRIES of them no CUDA is the answer for the process.
_cuda_build: Optional[bool] = None
_unanswered = 0
CHECK_TRIES = 2


def ctranslate2_has_cuda() -> bool:
    """Whether the speech library can run on CUDA here. Asked of a distro
    package's TuxTalk (install type "system"), which may run a CTranslate2
    the distro built without CUDA, and of a source install whose CTranslate2
    isn't its own: a venv made with --system-site-packages, as the README's
    is, keeps the distro's when its version meets the pin. Every other
    install runs PyPI's, which has CUDA. A clean answer is kept: it can't
    change while TuxTalk runs. A check with none (a hang at a busy login, a
    crash) counts as no CUDA that time and is tried once more on the next
    question, so one slow start doesn't hide a working CUDA for the whole
    session; a second without one is kept as no CUDA."""
    global _cuda_build, _unanswered
    if _cuda_build is None:
        install_type = installation.get_install_type()
        checked = install_type == "system" or (install_type == "source"
                                               and _ctranslate2_outside(sys.prefix))
        answer = probe_ctranslate2_cuda() if checked else True
        if answer is None:
            _unanswered += 1
            if _unanswered < CHECK_TRIES:
                return False
            answer = False
        _cuda_build = answer
    return _cuda_build


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
            except OSError as e:       # unreadable: look in the next folder
                logger.info(f"Couldn't look in {lib_dir} for CUDA's libraries: {e}")

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
