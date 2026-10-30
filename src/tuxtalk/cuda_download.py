"""Getting NVIDIA's CUDA libraries into TuxTalk's data folder: find the
wheels on PyPI, download them (each checked against PyPI's SHA256), unpack
their libraries into a folder of their own, check that every library is
there, and only then put it in place of the old one. A download that fails
at any point leaves a working CUDA as it was.

The download window (show_cuda_download_dialog) is here too; cuda_helper
says whether CUDA is there at all.
"""
import os
import shutil
import tempfile

from . import cuda_helper
from .logger import setup_logger

logger = setup_logger(__name__)

# The wheels come straight from PyPI's JSON API; no pip is needed.
CUDA_PACKAGES = ('nvidia-cuda-runtime-cu12', 'nvidia-cublas-cu12')
# Room kept free beyond what the download or the libraries take.
_SPARE_BYTES = 100 * 1024 * 1024
# The progress bar: the downloads share 0-70 %, unpacking 70-95 %.
_DOWNLOAD_SHARE = 70
_UNPACK_SHARE = 25


class _Stop(Exception):
    """The download ends here; the message, if any, is shown as the reason."""


def _pypi_wheel(pkg_name):
    """(pkg_name, url, filename, sha256, size) of *pkg_name*'s Linux x86_64
    wheel on PyPI. Raises LookupError when PyPI has none, and OSError or
    ValueError when it can't be read."""
    import json
    import urllib.request
    request = urllib.request.Request(f"https://pypi.org/pypi/{pkg_name}/json",
                                     headers={"User-Agent": "TuxTalk"})
    # Without a timeout a stalled PyPI connection hangs this thread.
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read())
    for file_info in data['urls']:
        if (file_info['packagetype'] == 'bdist_wheel' and 'x86_64' in file_info['filename']
                and 'manylinux' in file_info['filename']):
            # PyPI's published sha256, so a corrupted or tampered download is
            # never installed.
            return (pkg_name, file_info['url'], file_info['filename'],
                    file_info.get('digests', {}).get('sha256'),
                    int(file_info.get('size', 0) or 0))
    raise LookupError(f"PyPI has no {pkg_name} for this computer")


def _check_cancel(cancel_event, when=""):
    if cancel_event and cancel_event.is_set():
        logger.info(f"Download cancelled by user{when}")
        raise _Stop()


def _resolve_wheels(report, cancel_event):
    """Each package's wheel as _pypi_wheel gives it."""
    # From 0, so the bar never jumps back when the first download reports 0 %.
    report("Looking up CUDA packages...", 0)
    wheels = []
    for pkg_name in CUDA_PACKAGES:
        _check_cancel(cancel_event)
        try:
            wheels.append(_pypi_wheel(pkg_name))
        except Exception as e:
            logger.error(f"Failed to look up {pkg_name} on PyPI: {e}")
            raise _Stop(str(e) if isinstance(e, LookupError)
                        else "Could not reach PyPI — check your internet connection") from e
    return wheels


def _check_space(wheels, temp_dir, cuda_path):
    """Room for the download before it starts. The wheels go to *temp_dir*
    (often /tmp, a tmpfs in RAM on Fedora) and the libraries under
    *cuda_path*: often two filesystems, so both are checked, the second for
    at least the wheels' size (the unpacked size is checked once they're
    here, in _extract_wheels)."""
    from .download_utils import free_space_bytes
    total = sum(wheel[4] for wheel in wheels)
    need = total + _SPARE_BYTES
    for path, label in ((temp_dir, "download (temp)"), (cuda_path, "install")):
        free = free_space_bytes(path)
        if total and free and free < need:
            msg = (f"Not enough disk space for {label}: need ~{need / 1024 ** 3:.1f} GB "
                   f"free, only {free / 1024 ** 3:.1f} GB available")
            logger.error(f"{msg} (path: {path})")
            raise _Stop(msg)


def _download_wheels(wheels, temp_dir, report, cancel_event):
    """The wheels' paths in *temp_dir*, each checked against its SHA256."""
    from .download_utils import download_file
    paths = []
    for index, (pkg_name, url, filename, sha256, _size) in enumerate(wheels):
        _check_cancel(cancel_event)
        report(f"Downloading {pkg_name}...", _DOWNLOAD_SHARE * index // len(wheels))
        logger.info(f"Downloading {pkg_name}...")

        def progress_hook(done, total, _pkg=pkg_name, _index=index):
            if total > 0:
                percent = min(100, done / total * 100)
                report(f"Downloading {_pkg}... {int(percent)}%",
                       int(_DOWNLOAD_SHARE * (_index + percent / 100) / len(wheels)))

        path = os.path.join(temp_dir, filename)
        if not download_file(url, path, timeout=60, cancel_event=cancel_event,
                             progress_hook=progress_hook, expected_sha256=sha256):
            logger.error(f"Failed to download {pkg_name}")
            cancelled = cancel_event and cancel_event.is_set()
            raise _Stop(None if cancelled else f"Download of {pkg_name} failed or was corrupted")
        logger.info(f"Downloaded and verified {filename}")
        paths.append(path)
    return sorted(paths)


def _inside(folder, rel_path):
    """*rel_path* under *folder*, or None when it would land anywhere else
    (an absolute path, or ".." in it): a wheel's file names are only data."""
    target = os.path.realpath(os.path.join(folder, rel_path))
    return target if target.startswith(os.path.realpath(folder) + os.sep) else None


def _wheel_libraries(names, lib_path):
    """(member, where it goes) for each shared library under nvidia/ in a
    wheel's *names*, the nvidia/ prefix dropped; one whose name points
    outside *lib_path* is skipped and logged."""
    wanted = []
    for member in names:
        if not member.startswith("nvidia/") or ".so" not in member:
            continue
        target = _inside(lib_path, member[len("nvidia/"):])
        if target is None:
            logger.warning(f"Skipped {member!r}: it points outside {lib_path}")
        else:
            wanted.append((member, target))
    return wanted


def _unpack_wheel(wheel, so_files, report, done, of):
    """Copy one wheel's libraries out, the bar moving through its share."""
    wheel_file = os.path.basename(wheel.filename)
    logger.info(f"Extracting {wheel_file}...")
    for j, (member, target) in enumerate(so_files):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with wheel.open(member) as source, open(target, 'wb') as dest:
            shutil.copyfileobj(source, dest)
        os.chmod(target, 0o755)
        share = (done + (j + 1) / len(so_files)) / of
        report(f"Extracting {wheel_file}... ({j+1}/{len(so_files)} files)",
               _DOWNLOAD_SHARE + int(share * _UNPACK_SHARE))
    report(f"Extracted {wheel_file} ({done+1}/{of})",
           _DOWNLOAD_SHARE + int((done + 1) / of * _UNPACK_SHARE))


def _extract_wheels(wheel_paths, lib_path, report, cancel_event):
    """Unpack the shared libraries of each downloaded wheel into *lib_path*,
    each in lib/<package>/lib/. Stops, having said why, when there isn't
    room for them (checked first: the wheels are compressed, so the check on
    their download size passed and a full disk mid-way gave only a generic
    error) or the user cancelled."""
    import contextlib
    import zipfile
    from .download_utils import free_space_bytes
    with contextlib.ExitStack() as stack:
        wheels = []
        for path in wheel_paths:
            wheel = stack.enter_context(zipfile.ZipFile(path))
            wheels.append((wheel, _wheel_libraries(wheel.namelist(), lib_path)))
        need = sum(wheel.getinfo(member).file_size
                   for wheel, so_files in wheels for member, _target in so_files)
        need += _SPARE_BYTES
        free = free_space_bytes(lib_path)
        if free and free < need:
            msg = (f"Not enough disk space to install: need ~{need / 1024 ** 3:.1f} GB "
                   f"free, only {free / 1024 ** 3:.1f} GB available")
            logger.error(f"{msg} (path: {lib_path})")
            raise _Stop(msg)
        report("Extracting CUDA libraries...", _DOWNLOAD_SHARE)
        for i, (wheel, so_files) in enumerate(wheels):
            _check_cancel(cancel_event, " during extraction")
            _unpack_wheel(wheel, so_files, report, i, len(wheels))
    logger.info(f"CUDA libraries installed: {sum(len(f) for _w, f in wheels)} "
                f"library files in {lib_path}")


def _swap_in(staging, lib_path):
    """*staging* becomes *lib_path*; the old libraries go only once it has."""
    old = lib_path + ".old"
    shutil.rmtree(old, ignore_errors=True)      # what a killed swap left
    if os.path.lexists(lib_path):
        os.rename(lib_path, old)
    try:
        os.rename(staging, lib_path)
    except OSError:
        if os.path.lexists(old):
            os.rename(old, lib_path)
        raise
    shutil.rmtree(old, ignore_errors=True)


def _install(wheel_paths, cuda_path, report, cancel_event):
    """The wheels' libraries in place as *cuda_path*/lib. They are unpacked
    into a folder of their own and moved in only when every library CUDA
    needs is among them, so a failure part-way, or a wheel laid out
    differently upstream, never mixes files into a working install."""
    from .download_utils import download_area, install_turn
    with download_area(cuda_path, "lib") as (_archive, staging):
        _extract_wheels(wheel_paths, staging, report, cancel_event)
        if not cuda_helper.holds_required_libs(staging):
            logger.error(f"The CUDA download lacks one of {cuda_helper.REQUIRED_CUDA_LIBS}")
            raise _Stop("The CUDA download didn't hold every library it needs")
        with install_turn("cuda"):
            _swap_in(staging, os.path.join(cuda_path, "lib"))


def _make_cuda_cleanup(cuda_path: str):
    """Build the failure cleanup for a CUDA download attempt.

    Snapshots whether a complete installation is already present *before* the
    download starts. If one is, a failed or cancelled attempt leaves it alone
    rather than deleting a working setup.
    """
    pre_existing = cuda_helper.cuda_install_looks_complete(cuda_path)

    def cleanup_cuda_dir():
        if pre_existing:
            logger.warning(
                f"CUDA download failed, but {cuda_path} already held a complete "
                f"installation before this attempt — leaving it in place"
            )
            return
        try:
            if os.path.exists(cuda_path):
                shutil.rmtree(cuda_path)
                logger.info(f"Cleaned up incomplete CUDA installation at {cuda_path}")
        except Exception as e:
            logger.warning(f"Could not clean up CUDA directory: {e}")

    return cleanup_cuda_dir


def download_cuda_libraries(progress_callback=None, cancel_event=None):
    """Download and install CUDA's libraries; True when they are in place.

    *progress_callback(message, percent)* hears how it goes, a failure as a
    message at 0 % (download_utils.failure_reason); *cancel_event* (a
    threading.Event) stops it. On a failure or a cancel, what this attempt
    left is removed.
    """
    report = progress_callback or (lambda message, percent: None)
    cuda_path = cuda_helper.get_appdir_cuda_path()
    cleanup_cuda_dir = _make_cuda_cleanup(cuda_path)
    try:
        os.makedirs(cuda_path, exist_ok=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            wheels = _resolve_wheels(report, cancel_event)
            _check_space(wheels, temp_dir, cuda_path)
            wheel_paths = _download_wheels(wheels, temp_dir, report, cancel_event)
            _install(wheel_paths, cuda_path, report, cancel_event)
    except _Stop as stop:
        if stop.args and stop.args[0]:
            report(stop.args[0], 0)
        cleanup_cuda_dir()
        return False
    except Exception as e:
        logger.error(f"Error downloading CUDA libraries: {e}", exc_info=True)
        report(f"Couldn't install CUDA's libraries: {e}", 0)
        cleanup_cuda_dir()
        return False
    report("CUDA libraries installed successfully!", 100)
    return True


def show_cuda_download_dialog(parent=None, on_success_callback=None):
    """Download TuxTalk's CUDA libraries in the download window (Preferences'
    Device choice), then say how it went.

    on_success_callback runs after the "Downloaded!" message, for example to
    refresh Preferences. Returns True on success, False on failure and None
    if the user cancelled.
    """
    from .download_progress_dialog import CUDA_TASK, DownloadTask, run_downloads
    from .download_utils import failure_reason
    task = DownloadTask(CUDA_TASK, f"~{cuda_helper.DOWNLOAD_SIZE_TEXT}", download_cuda_libraries)
    result = run_downloads(parent, "Downloading CUDA Libraries",
                           "This may take several minutes depending on your connection.",
                           [task])
    if result is False:
        # The download notices the Cancel and cleans up by itself.
        logger.info("User cancelled CUDA download")
        return None
    if result is None and failure_reason(*task.last_report):
        # The window showed why and waited for Close: not twice.
        logger.error("CUDA libraries download failed")
        return False
    _say_how_it_went(parent, bool(result), on_success_callback)
    return bool(result)


def _say_how_it_went(parent, success, on_success_callback):
    """"CUDA Libraries Downloaded!", then *on_success_callback*, or "Download
    Failed" for a failure the download window couldn't explain. Switching the device
    is the caller's job (Preferences applies it, so the service restarts
    onto it), after the message so its question doesn't come before
    "Downloaded!"."""
    from gi.repository import Gtk
    from .ui_style import show_message
    if not success:
        show_message(parent, Gtk.MessageType.ERROR, "Download Failed",
                     "Could not download CUDA libraries.\n\nYou can try again later: choose "
                     "CUDA under Preferences → General → Change model, device or language → "
                     "Device.")
        logger.error("CUDA libraries download failed")
        return
    show_message(parent, Gtk.MessageType.INFO, "CUDA Libraries Downloaded!",
                 "Your NVIDIA graphics card can now run the Whisper models with CUDA.")
    if on_success_callback:
        try:
            on_success_callback()
        except Exception as e:
            logger.warning(f"Error in success callback: {e}", exc_info=True)
    logger.info("CUDA libraries downloaded successfully")
