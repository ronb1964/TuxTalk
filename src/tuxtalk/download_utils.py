"""
Shared download helpers: timeouts, cancellation, integrity verification.

Used for the CUDA libraries, the Vulkan engine, models, the GNOME extension
and updates, so every download has a timeout, can be cancelled, and is checked
for truncation (and against a sha256 when one is known).

Every download goes to its own ".tmp" file first and is atomically renamed
into place only after size and (when provided) sha256 checks pass — so an
interrupted download can never be mistaken for a complete file.
"""

import contextlib
import fnmatch
import glob
import hashlib
import logging
import os
import secrets
import shutil
import tarfile
import tempfile
import time
import urllib.request

logger = logging.getLogger(__name__)

# Read in 64 KiB chunks — large enough for throughput, small enough that
# cancellation and progress updates stay responsive.
_CHUNK_SIZE = 65536

# A temp file nobody has written to for this long belongs to a download the
# process never finished (killed, logged out, rebooted). A live download writes
# every chunk, and a stalled one gives up after its socket timeout.
STALE_PARTIAL_S = 10 * 60


def download_file(
    url: str,
    dest_path: str,
    timeout: int = 60,
    cancel_event=None,
    progress_hook=None,
    expected_sha256: str | None = None,
    user_agent: str = "TuxTalk",
) -> bool:
    """Download *url* to *dest_path* safely. Returns True on success.

    - *timeout* applies to each socket operation, so a stalled connection
      raises instead of hanging forever.
    - *cancel_event* (threading.Event) is checked between chunks; setting it
      aborts within one chunk read.
    - *progress_hook(downloaded_bytes, total_bytes)* is called per chunk
      (total_bytes is 0 when the server sends no Content-Length).
    - *expected_sha256* is verified before the file lands at dest_path.
    - Truncated transfers are rejected: HTTP read() returns b"" on a
      premature EOF without raising, so the byte count is compared against
      Content-Length explicitly.

    On any failure the partial ".tmp" file is removed and dest_path is left
    untouched.
    """
    tmp_path = None
    sha = hashlib.sha256()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length", 0) or 0)
            downloaded = 0
            parent = os.path.dirname(dest_path) or "."
            os.makedirs(parent, exist_ok=True)
            remove_stale_files(parent, glob.escape(os.path.basename(dest_path)) + ".*.tmp",
                               STALE_PARTIAL_S)
            # A temp name of its own: the tray and Preferences can download the
            # same file at once, and a shared name would let one overwrite the
            # bytes the other has already checked.
            fd, tmp_path = tempfile.mkstemp(
                dir=parent, prefix=os.path.basename(dest_path) + ".", suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("Download cancelled")
                    chunk = response.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    sha.update(chunk)
                    downloaded += len(chunk)
                    if progress_hook:
                        progress_hook(downloaded, total)

        if total and downloaded != total:
            logger.error(
                f"Download truncated: got {downloaded} of {total} bytes from {url}"
            )
            _remove_quiet(tmp_path)
            return False

        if expected_sha256:
            actual = sha.hexdigest()
            if actual.lower() != expected_sha256.strip().lower():
                logger.error(
                    f"Checksum mismatch for {url}: expected {expected_sha256}, got {actual}"
                )
                _remove_quiet(tmp_path)
                return False

        os.chmod(tmp_path, 0o644)  # mkstemp makes it owner-only
        os.replace(tmp_path, dest_path)
        return True

    except InterruptedError:
        logger.info(f"Download cancelled: {url}")
        _remove_quiet(tmp_path)
        return False
    except Exception as e:
        logger.error(f"Download failed for {url}: {e}")
        _remove_quiet(tmp_path)
        return False


def _remove_quiet(path: str | None) -> None:
    """Delete *path* if there is one, ignoring errors."""
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def remove_stale_files(directory: str, pattern: str, max_age_s: float) -> None:
    """Delete the files and folders in *directory* matching the glob
    *pattern* that have not been modified for *max_age_s* seconds. Temp
    names are random, so a copy cut off by a kill or a reboot is never
    overwritten by the next try; without this sweep each one stays on disk
    for good."""
    cutoff = time.time() - max_age_s
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return
    for entry in entries:
        try:
            if (fnmatch.fnmatchcase(entry.name, pattern)
                    and entry.stat(follow_symlinks=False).st_mtime < cutoff):
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path)
                else:
                    os.remove(entry.path)
                logger.info(f"Removed a leftover partial download: {entry.path}")
        except OSError:
            pass


@contextlib.contextmanager
def download_area(parent: str, name: str):
    """(archive path, folder path) in *parent* for one download of an engine
    to land and unpack in, both removed afterwards whether it worked or not.
    Names of its own each time: the tray and Preferences may fetch the same
    engine at once, and shared names let one unpack into, or delete, the
    other's archive. What a killed process left is swept here next time;
    neither is touched for long once the download ends."""
    os.makedirs(parent, exist_ok=True)
    remove_stale_files(parent, f".{glob.escape(name)}.*", STALE_PARTIAL_S)
    stem = os.path.join(parent, f".{name}.{secrets.token_hex(4)}")
    archive, folder = stem + ".tar", stem + ".d"
    try:
        yield archive, folder
    finally:
        _remove_quiet(archive)
        shutil.rmtree(folder, ignore_errors=True)


def unpack_tar(archive: str, folder: str) -> None:
    """Unpack the tar *archive* into *folder*. Where Python has the data
    filter (3.12, and back-ports), it refuses paths that would land outside
    the folder, links to them and device files."""
    with tarfile.open(archive) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(folder, filter="data")
        else:
            tar.extractall(folder)


def parse_sha256sums(text: str) -> dict:
    """Parse `sha256sum` output ("<hash>  <filename>") into {filename: hash}.

    Handles the optional '*' binary-mode marker before the filename and
    skips blank/comment lines.
    """
    sums = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, filename = parts
        sums[filename.lstrip("*").strip()] = digest
    return sums


def free_space_bytes(path: str) -> int:
    """Free disk space (bytes) on the filesystem holding *path*.

    Walks up to the nearest existing parent so it works for directories
    that haven't been created yet. Returns 0 if nothing can be determined
    (callers should treat 0 as 'unknown', not 'full').
    """
    p = os.path.abspath(path)
    while p and not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    try:
        return shutil.disk_usage(p).free
    except Exception:
        return 0
