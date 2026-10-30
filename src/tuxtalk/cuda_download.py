"""Getting NVIDIA's CUDA libraries into TuxTalk's data folder: find the
wheels on PyPI, download them (each checked against PyPI's SHA256), unpack
their libraries into a folder of their own, check that every library is
there, and only then put it in place of the old one. A download that fails
or is cancelled at any point, or a TuxTalk killed part-way, leaves a working
CUDA as it was.

cuda_helper says whether CUDA is there at all; its download window is
download_progress_dialog.show_cuda_download_dialog.
"""
import os
import re
import shutil
import tempfile

from . import cuda_helper, download_utils
from .logger import setup_logger

logger = setup_logger(__name__)

# The wheels come straight from PyPI's JSON API; no pip is needed.
CUDA_PACKAGES = ('nvidia-cuda-runtime-cu12', 'nvidia-cublas-cu12')
# Room kept free beyond what the download or the libraries take.
_SPARE_BYTES = 100 * 1024 * 1024
# The progress bar: the downloads share 0-70 %, unpacking 70-95 %.
_DOWNLOAD_SHARE = 70
_UNPACK_SHARE = 25
_SHA256 = re.compile(r"[0-9a-f]{64}")
# The start of an unpacking folder's name (download_utils.download_area's).
_UNPACKING = ".lib."


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
            # never installed; without one it isn't downloaded at all.
            sha256 = (file_info.get('digests') or {}).get('sha256') or ""
            if not _SHA256.fullmatch(sha256):
                raise LookupError(f"PyPI gave no checksum for {file_info['filename']}, "
                                  "so it isn't downloaded")
            return (pkg_name, file_info['url'], file_info['filename'], sha256,
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


def _remove_leftovers(cuda_path):
    """What an install killed part-way left, with the install lock held, so
    no other install is unpacking now: the libraries put back if the swap
    was cut off between its renames, then the old copy and every unpacking
    folder removed, however recent."""
    lib = os.path.join(cuda_path, "lib")
    download_utils.restore_cut_off_swap(lib, lib + ".old")
    download_utils.remove_tree(lib + ".old")
    try:
        entries = list(os.scandir(cuda_path))
    except FileNotFoundError:
        return
    for entry in entries:
        if entry.name.startswith(_UNPACKING):
            logger.info(f"Removing an unfinished CUDA install: {entry.name}")
            download_utils.remove_tree(entry.path)


def recover_cut_off_install():
    """At start, before anything loads CUDA: _remove_leftovers, unless
    another TuxTalk is installing CUDA now (that one tidies). Never raises."""
    cuda_path = cuda_helper.get_appdir_cuda_path()
    if not os.path.isdir(cuda_path):
        return
    try:
        with download_utils.install_turn("cuda", wait_s=0):
            _remove_leftovers(cuda_path)
    except OSError as e:        # the lock is busy or can't be made: next start
        logger.info(f"CUDA's folder wasn't checked this time: {e}")


def _install(wheel_paths, cuda_path, report, cancel_event):
    """The wheels' libraries in place as *cuda_path*/lib. They are unpacked
    into a folder of their own and moved in only when every library CUDA
    needs is among them, so a failure part-way, or a wheel laid out
    differently upstream, never mixes files into a working install. The
    install lock is held from the unpacking on: another install waits its
    turn instead of taking this one's folder for a leftover."""
    with download_utils.install_turn("cuda"):
        _check_cancel(cancel_event)         # cancelled while it waited its turn
        _remove_leftovers(cuda_path)
        with download_utils.download_area(cuda_path, "lib") as (_archive, staging):
            _extract_wheels(wheel_paths, staging, report, cancel_event)
            if not cuda_helper.holds_required_libs(staging):
                logger.error(f"The CUDA download lacks one of {cuda_helper.REQUIRED_CUDA_LIBS}")
                raise _Stop("The CUDA download didn't hold every library it needs")
            _check_cancel(cancel_event, " before the libraries went in")
            lib = os.path.join(cuda_path, "lib")
            download_utils.replace_folder(staging, lib, lib + ".old")


def download_cuda_libraries(progress_callback=None, cancel_event=None):
    """Download and install CUDA's libraries; True when they are in place.

    *progress_callback(message, percent)* hears how it goes, a failure as a
    message at 0 % (download_utils.failure_reason); *cancel_event* (a
    threading.Event) stops it. A failure or a cancel removes only this
    attempt's own files: what is installed, by an earlier attempt or one
    running beside it, stays.
    """
    report = progress_callback or (lambda message, percent: None)
    cuda_path = cuda_helper.get_appdir_cuda_path()
    try:
        os.makedirs(cuda_path, exist_ok=True)
        with download_utils.install_turn("cuda"):
            _remove_leftovers(cuda_path)    # a copy cut off last time comes back first
        with tempfile.TemporaryDirectory() as temp_dir:
            wheels = _resolve_wheels(report, cancel_event)
            _check_space(wheels, temp_dir, cuda_path)
            wheel_paths = _download_wheels(wheels, temp_dir, report, cancel_event)
            _install(wheel_paths, cuda_path, report, cancel_event)
    except _Stop as stop:
        if stop.args and stop.args[0]:
            report(stop.args[0], 0)
        return False
    except Exception as e:
        logger.error(f"Error downloading CUDA libraries: {e}", exc_info=True)
        report(f"Couldn't install CUDA's libraries: {e}", 0)
        return False
    report("CUDA libraries installed successfully!", 100)
    return True
