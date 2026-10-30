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
import urllib.error
import urllib.request

from .logger import FailureNotes  # noqa: E402

logger = logging.getLogger(__name__)
# Damaged downloads, logged once each: the presence checks run on every OK.
_damaged = FailureNotes()

# Read in 64 KiB chunks — large enough for throughput, small enough that
# cancellation and progress updates stay responsive.
_CHUNK_SIZE = 65536

# A temp file nobody has written to for this long belongs to a download the
# process never finished (killed, logged out, rebooted). A live download writes
# every chunk, and a stalled one gives up after its socket timeout.
STALE_PARTIAL_S = 10 * 60


class DownloadError(Exception):
    """Why a download failed, short enough for a download window's status line."""


def download_file(
    url: str,
    dest_path: str,
    timeout: int = 60,
    cancel_event=None,
    progress_hook=None,
    expected_sha256: str | None = None,
    user_agent: str = "TuxTalk",
    report=None,
) -> bool:
    """fetch_file() for callers that only need to know whether the file
    landed: True when it did; a failure is logged with its reason, and told
    to *report* (a download window's progress callback) at 0 %, as
    failure_reason reads it. A cancel is only logged."""
    try:
        fetch_file(url, dest_path, timeout=timeout, cancel_event=cancel_event,
                   progress_hook=progress_hook, expected_sha256=expected_sha256,
                   user_agent=user_agent)
        return True
    except InterruptedError:
        logger.info(f"Download cancelled: {url}")
    except DownloadError as e:
        logger.error(f"Download failed for {url}: {e}")
        if report is not None:
            report(f"Download failed: {e}", 0)
    return False


def fetch_file(
    url: str,
    dest_path: str,
    timeout: int = 60,
    cancel_event=None,
    progress_hook=None,
    expected_sha256: str | None = None,
    user_agent: str = "TuxTalk",
) -> None:
    """Download *url* to *dest_path* safely. Raises DownloadError saying why
    when it can't, and InterruptedError when cancelled.

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
    try:
        tmp_path = _fetch_to_temp(url, dest_path, timeout, cancel_event, progress_hook,
                                  expected_sha256, user_agent)
        os.chmod(tmp_path, 0o644)  # mkstemp makes it owner-only
        os.replace(tmp_path, dest_path)
    except (InterruptedError, DownloadError):
        _remove_quiet(tmp_path)
        raise
    except Exception as e:
        _remove_quiet(tmp_path)
        raise DownloadError(_reason(e)) from e


def _reason(error) -> str:
    """*error* in words for a download window. urllib's read "<urlopen error
    [Errno -2] Name or service not known>", whose reason alone is what the
    user can act on; an HTTP error keeps its status ("HTTP 404 Not Found")."""
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code} {error.reason}"
    return str(getattr(error, "reason", None) or error)


def _fetch_to_temp(url, dest_path, timeout, cancel_event, progress_hook,
                   expected_sha256, user_agent):
    """fetch_file's transfer: the checked bytes in a temp file beside
    *dest_path*, whose path is returned."""
    sha = hashlib.sha256()
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
        try:
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
                raise DownloadError(f"the download was cut short ({downloaded} of {total} bytes)")
            if expected_sha256 and sha.hexdigest() != expected_sha256.strip().lower():
                logger.error(f"Checksum mismatch for {url}: expected {expected_sha256}, "
                             f"got {sha.hexdigest()}")
                raise DownloadError("the file didn't match its checksum")
        except BaseException:
            _remove_quiet(tmp_path)
            raise
    return tmp_path


def _remove_quiet(path: str | None) -> None:
    """Delete *path* if there is one, ignoring errors."""
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def remove_stale_files(directory: str, pattern: str, max_age_s: float,
                       folders: bool = False) -> None:
    """Delete the files (and with *folders*, the folders) in *directory*
    matching the glob *pattern* that have not been modified for *max_age_s*
    seconds. Temp names are random, so a copy cut off by a kill or a reboot
    is never overwritten by the next try; without this sweep each one stays
    on disk for good."""
    cutoff = time.time() - max_age_s
    try:
        entries = list(os.scandir(directory))
    except FileNotFoundError:
        return
    except OSError as e:
        logger.info(f"Couldn't look for leftover downloads in {directory}: {e}")
        return
    for entry in entries:
        try:
            if (not fnmatch.fnmatchcase(entry.name, pattern)
                    or entry.stat(follow_symlinks=False).st_mtime >= cutoff):
                continue
            if entry.is_dir(follow_symlinks=False):
                if not folders:
                    continue
                shutil.rmtree(entry.path)
            else:
                os.remove(entry.path)
            logger.info(f"Removed a leftover partial download: {entry.path}")
        except FileNotFoundError:
            pass                # another sweep got there first
        except OSError as e:
            logger.info(f"Couldn't remove the leftover download {entry.path}: {e}")


@contextlib.contextmanager
def download_area(parent: str, name: str, old_names=()):
    """(archive path, folder path) in *parent* for one download of an engine
    to land and unpack in, both removed afterwards whether it worked or not.
    Names of its own each time: the tray and Preferences may fetch the same
    engine at once, and shared names let one unpack into, or delete, the
    other's archive. What a killed process left is swept here next time;
    neither is touched for long once the download ends. *old_names* are the
    fixed archive names older versions used, removed once seen."""
    os.makedirs(parent, exist_ok=True)
    remove_stale_files(parent, f".{glob.escape(name)}.*", STALE_PARTIAL_S, folders=True)
    for old in old_names:
        _remove_quiet(os.path.join(parent, old))
    stem = os.path.join(parent, f".{name}.{secrets.token_hex(4)}")
    archive, folder = stem + ".tar", stem + ".d"
    try:
        yield archive, folder
    finally:
        _remove_quiet(archive)
        shutil.rmtree(folder, ignore_errors=True)


@contextlib.contextmanager
def install_turn(name: str, wait_s: float = 120.0):
    """Hold *name*'s install lock while a downloaded engine is put in place:
    the tray and Preferences may finish the same download together, and one
    removing the engine folder while the other moved its copy in failed
    both. Whoever comes second should check whether it is there already.
    Raises OSError when the turn doesn't come within *wait_s*."""
    from . import single_instance
    with single_instance.open_lock(f"{name}-install.lock") as lock:
        if not single_instance.claim(lock, wait_s=wait_s):
            raise OSError(f"another {name} install didn't finish")
        yield


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


def failure_reason(message, percent):
    """What a download that failed said went wrong, from the last report it
    gave its progress callback: each reports its reason last, at 0 %. None
    when that last word was only progress."""
    return message if percent == 0 and message else None


def _cached(lookup, what):
    """*lookup*()'s answer from the Hugging Face cache, or None when *what*
    isn't downloaded or its cache entry is damaged."""
    try:
        return lookup()
    except OSError:     # not downloaded: LocalEntryNotFoundError is a FileNotFoundError
        return None
    except ValueError as e:
        # A damaged entry: refs/main that isn't text (UnicodeDecodeError) or
        # names a folder outside the snapshots ("Invalid pointer path").
        if _damaged.failed(what, e):
            logger.warning(f"The download of {what} is damaged; counted as missing: {e}")
        return None


def cached_hub_file(repo: str, filename: str):
    """The local path of *filename* from Hugging Face *repo*, or None when it
    isn't fully downloaded. Asks only the cache, never the network."""
    from huggingface_hub import hf_hub_download
    return _cached(lambda: hf_hub_download(repo, filename, local_files_only=True),
                   f"{repo}/{filename}")


def cached_hub_snapshot(repo: str):
    """The local snapshot folder of Hugging Face *repo*, or None when there is
    none. It may hold only some of the files (see is_model_cached_fast)."""
    from huggingface_hub import snapshot_download
    return _cached(lambda: snapshot_download(repo, local_files_only=True), repo)


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
    except OSError:     # unreadable, a broken mount: 0 means unknown
        return 0
