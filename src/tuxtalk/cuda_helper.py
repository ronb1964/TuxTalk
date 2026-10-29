#!/usr/bin/env python3
"""NVIDIA graphics card detection and TuxTalk's own CUDA library download."""

import os
import subprocess
from .logger import setup_logger

# NVIDIA's CUDA libraries, downloaded once when CUDA is picked: the size
# Preferences, System check, the setup choices and Help give.
DOWNLOAD_SIZE_TEXT = "1.4 GB"

logger = setup_logger(__name__)

# Timeout constants (in seconds)
NVIDIA_SMI_TIMEOUT = 5
SUBPROCESS_TIMEOUT = 2

# The libraries CTranslate2 needs to run on CUDA: the runtime, cuBLAS and
# cuDNN. Without cuDNN the model loads "on cuda" and the first dictation crashes.
_REQUIRED_CUDA_LIBS = ("libcudart.so", "libcublas.so", "libcudnn.so")

def detect_nvidia_gpu():
    """Check if an NVIDIA GPU is present.

    The host asks nvidia-smi. A Flatpak sandbox has no nvidia-smi, so there the
    driver's device nodes (/dev/nvidiactl, /dev/nvidia0, exposed by the
    manifest's --device=dri) are checked instead, which also works when
    torch_init has cleared CUDA_VISIBLE_DEVICES.
    """
    if os.environ.get("FLATPAK_ID"):
        return os.path.exists("/dev/nvidiactl") or os.path.exists("/dev/nvidia0")
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                              capture_output=True, text=True, timeout=NVIDIA_SMI_TIMEOUT)
        return result.returncode == 0 and result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

def _has_required_libs(names) -> bool:
    """True if *names* (library file names) include every required library."""
    names = list(names)
    return all(any(name.startswith(lib) for name in names)
               for lib in _REQUIRED_CUDA_LIBS)


def _cuda_install_looks_complete(cuda_path: str) -> bool:
    """True if *cuda_path* holds every required library of a usable install.

    The download unpacks each wheel into its own folder (lib/cudnn/lib/...,
    lib/cublas/lib/..., lib/cuda_runtime/lib/...), so the whole tree is
    searched. A directory holding only the debris of an interrupted download
    does not read as complete.
    """
    lib_path = os.path.join(cuda_path, "lib")
    if not os.path.isdir(lib_path):
        return False
    names = []
    for _root, _dirs, files in os.walk(lib_path):
        names.extend(files)
    return _has_required_libs(names)


def _make_cuda_cleanup(cuda_path: str):
    """Build the failure cleanup for a CUDA download attempt.

    Snapshots whether a complete installation is already present *before* the
    download starts. If one is, a failed or cancelled attempt leaves it alone
    rather than deleting a working 1.4 GB setup.
    """
    import shutil

    pre_existing = _cuda_install_looks_complete(cuda_path)

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


def has_cuda_libraries():
    """
    Check if CUDA libraries are available for running GPU-accelerated models.
    Each place must hold all of _REQUIRED_CUDA_LIBS:
    1. Downloaded CUDA in TuxTalk data dir (respects DEV_MODE)
    2. The system's library cache (ldconfig)
    3. CUDA toolkit installation (/usr/local/cuda, /opt/cuda)
    4. Standard /usr library paths (distro-packaged CUDA)
    """
    # get_appdir_cuda_path() respects DEV_MODE, so the dev version checks
    # ~/.local/share/TuxTalk-dev/cuda and the AppImage ~/.local/share/TuxTalk/cuda.
    if _cuda_install_looks_complete(get_appdir_cuda_path()):
        return True

    # Check system-wide CUDA installation via ldconfig (most reliable)
    try:
        result = subprocess.run(['ldconfig', '-p'], capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
        # Lines look like "\tlibcudart.so.12 (libc6,x86-64) => /usr/lib64/libcudart.so.12".
        names = [line.split()[0] for line in result.stdout.splitlines()[1:] if line.strip()]
        if _has_required_libs(names):
            return True
    except (subprocess.TimeoutExpired, Exception):
        pass

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
    return _cuda_install_looks_complete(get_appdir_cuda_path())

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


def _inside(folder, rel_path):
    """*rel_path* under *folder*, or None when it would land anywhere else
    (an absolute path, or ".." in it): a wheel's file names are only data."""
    target = os.path.realpath(os.path.join(folder, rel_path))
    return target if target.startswith(os.path.realpath(folder) + os.sep) else None


def _extract_wheels(wheel_paths, lib_path, progress_callback, cancel_event):
    """Unpack the shared libraries of each downloaded wheel into *lib_path*,
    70 to 95 % of the progress bar. False, having said why, when there isn't
    room for them (checked first: the wheels are compressed, so the check on
    their download size passed and a full disk mid-way gave only a generic
    error) or the user cancelled."""
    import contextlib
    import shutil
    import zipfile
    from .download_utils import free_space_bytes
    with contextlib.ExitStack() as stack:
        wheels = []
        for path in wheel_paths:
            wheel = stack.enter_context(zipfile.ZipFile(path))
            wheels.append((path, wheel, _wheel_libraries(wheel.namelist(), lib_path)))
        need = sum(wheel.getinfo(member).file_size
                   for _path, wheel, so_files in wheels for member, _target in so_files)
        need += 100 * 1024 * 1024
        free = free_space_bytes(lib_path)
        if free and free < need:
            msg = (f"Not enough disk space to install: need ~{need / 1024 ** 3:.1f} GB "
                   f"free, only {free / 1024 ** 3:.1f} GB available")
            logger.error(f"{msg} (path: {lib_path})")
            if progress_callback:
                progress_callback(msg, 0)
            return False
        if progress_callback:
            progress_callback("Extracting CUDA libraries...", 70)
        for i, (path, wheel, so_files) in enumerate(wheels):
            if cancel_event and cancel_event.is_set():
                logger.info("Download cancelled by user during extraction")
                return False
            wheel_file = os.path.basename(path)
            logger.info(f"Extracting {wheel_file}...")
            # The nvidia/ prefix is dropped: each lands in lib/<package>/lib/.
            for j, (member, target) in enumerate(so_files):
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with wheel.open(member) as source, open(target, 'wb') as dest:
                    shutil.copyfileobj(source, dest)
                os.chmod(target, 0o755)
                if progress_callback:
                    wheel_progress = (i + (j + 1) / len(so_files)) / len(wheels)
                    progress_callback(f"Extracting {wheel_file}... ({j+1}/{len(so_files)} files)",
                                      70 + int(wheel_progress * 25))
            if progress_callback:
                progress_callback(f"Extracted {wheel_file} ({i+1}/{len(wheels)})",
                                  70 + int((i + 1) / len(wheels) * 25))
    logger.info(f"CUDA libraries installed: {sum(len(f) for _p, _w, f in wheels)} "
                f"library files in {lib_path}")
    return True


# The wheels come straight from PyPI's JSON API; no pip is needed.
CUDA_PACKAGES = ('nvidia-cuda-runtime-cu12', 'nvidia-cublas-cu12', 'nvidia-cudnn-cu12')


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


def download_cuda_libraries(progress_callback=None, cancel_event=None):
    """
    Download CUDA libraries for GPU acceleration.
    Downloads wheels directly from PyPI and extracts them.

    Args:
        progress_callback: Optional function(message, percent) for progress updates
        cancel_event: Optional threading.Event() to signal cancellation

    Returns:
        bool: True if successful, False otherwise
    """
    cuda_path = get_appdir_cuda_path()
    lib_path = os.path.join(cuda_path, "lib")

    os.makedirs(lib_path, exist_ok=True)

    # Clean up on failure/cancellation — but only what this run created.
    cleanup_cuda_dir = _make_cuda_cleanup(cuda_path)
    
    try:
        import tempfile
        from .download_utils import download_file, free_space_bytes

        with tempfile.TemporaryDirectory() as temp_dir:
            # --- Phase 1: resolve wheel URLs + sizes + PyPI sha256 digests ---
            if progress_callback:
                # Start at 0 so the bar never jumps backwards when the first
                # per-package download reports 0%.
                progress_callback("Looking up CUDA packages...", 0)

            wheels = []  # (pkg_name, url, filename, sha256, size_bytes)
            for pkg_name in CUDA_PACKAGES:
                if cancel_event and cancel_event.is_set():
                    logger.info("Download cancelled by user")
                    cleanup_cuda_dir()
                    return False
                try:
                    wheels.append(_pypi_wheel(pkg_name))
                except Exception as e:
                    logger.error(f"Failed to look up {pkg_name} on PyPI: {e}")
                    if progress_callback:
                        progress_callback(str(e) if isinstance(e, LookupError) else
                                          "Could not reach PyPI — check your internet connection",
                                          0)
                    cleanup_cuda_dir()
                    return False

            # --- Phase 2: disk-space check before committing to the download ---
            # The wheels download into temp_dir (often /tmp, which is tmpfs =
            # RAM on Fedora/Nobara) and the .so files extract into lib_path
            # (under ~/.local/share). These can be different filesystems, so
            # BOTH must be checked: temp_dir needs room for the wheels, and
            # lib_path at least as much (a first look: the unpacked size is
            # known, and checked, once the wheels are here, _extract_wheels).
            total_wheel_bytes = sum(w[4] for w in wheels)
            checks = [
                (temp_dir, total_wheel_bytes + 100 * 1024 * 1024, "download (temp)"),
                (lib_path, total_wheel_bytes + 100 * 1024 * 1024, "install"),
            ]
            for check_path, need_bytes, label in checks:
                free = free_space_bytes(check_path)
                if total_wheel_bytes and free and free < need_bytes:
                    need_gb = need_bytes / (1024 ** 3)
                    free_gb = free / (1024 ** 3)
                    msg = (f"Not enough disk space for {label}: need ~{need_gb:.1f} GB "
                           f"free, only {free_gb:.1f} GB available")
                    logger.error(f"{msg} (path: {check_path})")
                    if progress_callback:
                        progress_callback(msg, 0)
                    cleanup_cuda_dir()
                    return False

            # --- Phase 3: download each wheel (timeout + cancel + sha256) ---
            for pkg_idx, (pkg_name, wheel_url, wheel_filename, wheel_sha256, _size) in enumerate(wheels):
                if cancel_event and cancel_event.is_set():
                    logger.info("Download cancelled by user")
                    cleanup_cuda_dir()
                    return False

                if progress_callback:
                    percent = int((pkg_idx / len(wheels)) * 70)
                    progress_callback(f"Downloading {pkg_name}...", percent)

                logger.info(f"Downloading {pkg_name}...")
                wheel_path = os.path.join(temp_dir, wheel_filename)

                def progress_hook(downloaded, total, _pkg=pkg_name, _idx=pkg_idx):
                    if progress_callback and total > 0:
                        download_percent = min(100, (downloaded / total) * 100)
                        # Map to overall progress: each package gets ~23% of total
                        current_percent = _idx * 23 + int((download_percent / 100) * 23)
                        progress_callback(
                            f"Downloading {_pkg}... {int(download_percent)}%",
                            current_percent)

                if not download_file(
                    wheel_url, wheel_path,
                    timeout=60,
                    cancel_event=cancel_event,
                    progress_hook=progress_hook,
                    expected_sha256=wheel_sha256,
                ):
                    logger.error(f"Failed to download {pkg_name}")
                    if progress_callback and not (cancel_event and cancel_event.is_set()):
                        progress_callback(f"Download of {pkg_name} failed or was corrupted", 0)
                    cleanup_cuda_dir()
                    return False
                logger.info(f"Downloaded and verified {wheel_filename}")

            wheel_paths = sorted(os.path.join(temp_dir, f)
                                 for f in os.listdir(temp_dir) if f.endswith('.whl'))
            if not _extract_wheels(wheel_paths, lib_path, progress_callback, cancel_event):
                cleanup_cuda_dir()
                return False

        if progress_callback:
            progress_callback("CUDA libraries installed successfully!", 100)
        return True

    except Exception as e:
        logger.error(f"Error downloading CUDA libraries: {e}", exc_info=True)
        if progress_callback:
            progress_callback(f"Couldn't install CUDA's libraries: {e}", 0)
        cleanup_cuda_dir()
        return False

def show_cuda_download_dialog(parent=None, on_success_callback=None):
    """Download TuxTalk's CUDA libraries behind a progress dialog (Preferences' Device choice).

    on_success_callback runs after the "Downloaded!" message, for example to
    refresh Preferences. Returns True on success, False on failure and None
    if the user cancelled.
    """
    # GTK only here: recommend.py and the dictation service read this
    # module's sizes and detection without loading it.
    import threading
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import GLib, Gtk

    progress_dialog = Gtk.Dialog(title="Downloading CUDA Libraries")
    progress_dialog.set_default_size(500, 150)
    progress_dialog.set_modal(True)
    progress_dialog.set_position(Gtk.WindowPosition.CENTER)

    if parent:
        progress_dialog.set_transient_for(parent)

    content = progress_dialog.get_content_area()
    content.set_margin_top(20)
    content.set_margin_bottom(20)
    content.set_margin_start(30)
    content.set_margin_end(30)
    content.set_spacing(15)

    title_label = Gtk.Label()
    title_label.set_markup('<span size="large"><b>Downloading CUDA Libraries</b></span>')
    content.pack_start(title_label, False, False, 0)

    status_label = Gtk.Label()
    status_label.set_markup(f'<span>Initializing download... (~{DOWNLOAD_SIZE_TEXT})</span>')
    status_label.set_line_wrap(True)
    content.pack_start(status_label, False, False, 0)

    progress_bar = Gtk.ProgressBar()
    progress_bar.set_show_text(True)
    content.pack_start(progress_bar, False, False, 0)

    info_label = Gtk.Label()
    info_label.set_markup('<span size="small"><i>This may take several minutes depending on your connection...</i></span>')
    info_label.set_opacity(0.7)
    content.pack_start(info_label, False, False, 0)

    cancel_button = Gtk.Button(label="Cancel")
    progress_dialog.add_action_widget(cancel_button, Gtk.ResponseType.CANCEL)

    progress_dialog.show_all()

    # Cancellation flag and download result
    cancel_event = threading.Event()
    download_success = [False]

    last_report = ["", None]

    def update_progress(message, percent):
        """Update progress bar and status from callback"""
        last_report[:] = [message, percent]

        def update_ui():
            progress_bar.set_fraction(percent / 100.0)
            progress_bar.set_text(f"{percent}%")
            status_label.set_markup(f'<span>{GLib.markup_escape_text(message)}</span>')
            return False
        GLib.idle_add(update_ui)

    def do_download():
        """Run download in background thread"""
        download_success[0] = download_cuda_libraries(
            progress_callback=update_progress,
            cancel_event=cancel_event
        )
        def close_dialog():
            progress_dialog.response(Gtk.ResponseType.OK)
            return False
        GLib.idle_add(close_dialog)

    download_thread = threading.Thread(target=do_download)
    download_thread.daemon = True
    download_thread.start()

    response = progress_dialog.run()
    progress_dialog.destroy()

    if response == Gtk.ResponseType.CANCEL or response == Gtk.ResponseType.DELETE_EVENT:
        logger.info("User cancelled CUDA download")
        # The download thread notices the event and cleans up by itself.
        cancel_event.set()
        return None

    download_thread.join()
    from .download_utils import failure_reason
    _say_how_it_went(parent, download_success[0], on_success_callback,
                     failure_reason(*last_report))
    return download_success[0]


def _say_how_it_went(parent, success, on_success_callback, reason=None):
    """"CUDA Libraries Downloaded!", then *on_success_callback*, or "Download
    Failed" with *reason* when the download gave one. Switching the device
    is the caller's job (Preferences applies it, so the service restarts
    onto it), after the message so its question doesn't come before
    "Downloaded!"."""
    from gi.repository import Gtk
    from .ui_style import show_message
    if not success:
        show_message(parent, Gtk.MessageType.ERROR, "Download Failed",
                     f"{reason or 'Could not download CUDA libraries.'}"
                     "\n\nYou can try again later: choose "
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
            logger.warning(f"Error in success callback: {e}")
    logger.info("CUDA libraries downloaded successfully")
