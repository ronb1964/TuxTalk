"""Downloading and loading the speech models (Whisper and Parakeet), with progress UI."""
import os

# Must be set before huggingface_hub is imported anywhere: XET downloads bypass
# tqdm_class, so the progress bars would never move.
os.environ["HF_HUB_DISABLE_XET"] = "1"

import threading
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
from .ui_style import message_dialog
from .logger import setup_logger
from .recommend import model_title
from .parakeet_engine import (
    PARAKEET_MODEL, cached_model_dir as _parakeet_cached_dir,
    is_parakeet, load_parakeet_model,
)

logger = setup_logger(__name__)

# The files faster-whisper actually loads. Everything else in a model repo —
# README.md, .gitattributes, alternative formats — is downloaded for
# completeness but is not needed to run the model, so failing to fetch one
# must not discard an otherwise finished multi-gigabyte download.
ESSENTIAL_MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")
# A Whisper model has one of these (.json for large-v3) and won't load without it.
VOCABULARY_FILES = ("vocabulary.txt", "vocabulary.json")


def _download_is_usable(failed_files, asked_for) -> bool:
    """True if a Whisper model can still be loaded despite *failed_files*,
    of the files *asked_for*: no essential file failed, and of the
    vocabulary files asked for, one came. A model has only one, so asked
    for both by name (no file list), the other one always fails.

    Paths may be nested (list_repo_tree is recursive), so match on the file
    name rather than the whole path.
    """
    failed = {os.path.basename(p) for p in failed_files}
    if failed & set(ESSENTIAL_MODEL_FILES):
        return False
    vocabulary = {os.path.basename(p) for p in asked_for} & set(VOCABULARY_FILES)
    return not vocabulary or bool(vocabulary - failed)


def _files_to_download(repo_id):
    """(path, size) pairs to fetch: every file in the Hugging Face repo *repo_id*.
    Raises if the file list cannot be fetched (callers fall back)."""
    from huggingface_hub import list_repo_tree
    files_info = list(list_repo_tree(repo_id, recursive=True))
    return [(f.path, f.size) for f in files_info
            if getattr(f, 'size', None) is not None]


def load_model(model_name, device="cpu", compute_type="int8"):
    """Load an already-downloaded model, Whisper or Parakeet.

    Parakeet always runs on the CPU (see parakeet_engine), so *device* and
    *compute_type* only apply to Whisper.
    """
    if is_parakeet(model_name):
        return load_parakeet_model()
    from faster_whisper import WhisperModel
    return WhisperModel(
        model_name,
        device=device,
        compute_type=compute_type,
        cpu_threads=os.cpu_count() or 4,
    )


# Hugging Face repository for each Whisper model. Parakeet comes from
# TuxTalk's own release instead (parakeet_ultra.py).
MODEL_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
}

# The models offered in the UI, smallest first. Every screen with a model picker
# builds from this so their lists can't drift apart. A model is only offerable
# if it can be downloaded (tests/test_model_choices.py).
#
# Parakeet comes last: it is not part of Whisper's size ladder, it is a
# different engine (see parakeet_engine.py).
OFFERED_MODELS = ("tiny", "base", "small", "medium", "large-v3", PARAKEET_MODEL)

# What each model really downloads for the processor (and CUDA), in decimal
# MB as Hugging Face lists it, not its parameter count (tests/test_model_sizes.py).
MODEL_DISPLAY_SIZES = {
    "tiny": "78 MB",
    "base": "148 MB",
    "small": "486 MB",
    "medium": "1.5 GB",
    "large-v3": "3.1 GB",
    PARAKEET_MODEL: "670 MB",
}


def _loads_on_cpu(model_name):
    """Whether a cached Whisper model loads on the processor: after a load has
    failed, this tells broken files from a device or driver problem."""
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(model_name, device="cpu", compute_type="int8",
                             local_files_only=True)
        del model
        return True
    except Exception:
        return False


def is_model_cached_fast(model_name):
    """Whether a model is downloaded, judged from its files alone: loading it
    to find out takes seconds and gigabytes for large-v3. A download that was
    cancelled partway reports False. Safe to call on every Apply/OK click."""
    if is_parakeet(model_name):
        return _parakeet_cached_dir() is not None
    try:
        from huggingface_hub import snapshot_download
        repo_id = MODEL_REPOS.get(model_name)
        if not repo_id:
            return False
        snapshot_path = snapshot_download(repo_id, local_files_only=True)
        # Offline, snapshot_download does not check completeness: it returns a
        # snapshot holding only config.json just as happily. hf_hub_download
        # links a file into the snapshot only once it has fully downloaded, so
        # the presence of the files faster-whisper loads means they are complete.
        def has(name):
            return os.path.isfile(os.path.join(snapshot_path, name))
        return all(map(has, ESSENTIAL_MODEL_FILES)) and any(map(has, VOCABULARY_FILES))
    except Exception:
        return False


def _failure_text(error):
    """A download error as the download window's status line: its first
    line, cut short (the whole error is in the log)."""
    text = str(error).strip()
    first = text.splitlines()[0] if text else type(error).__name__
    return f"Download failed: {first[:89] + '…' if len(first) > 90 else first}"


def _listed_files(repo, model_name, only_files):
    """(path, size) of the files to fetch from *repo*: every file, or just
    *only_files*. Empty when Hugging Face's file list can't be fetched,
    except that files asked for by name are fetched without their sizes."""
    try:
        files = _files_to_download(repo)
    except Exception as e:
        logger.warning(f"Could not list model files: {e}")
        files = []
    if only_files:
        files = [(p, s) for p, s in files if p in only_files] or [(n, 0) for n in only_files]
    total = sum(size for _, size in files)
    logger.info(f"Model {model_name}: {len(files)} files, {total / 1024 / 1024:.1f} MB total")
    return files


def _disk_space_problem(repo, files):
    """Why the files still to fetch won't fit on the cache's disk, or None.
    Checked up front, so a full disk gives a clear message rather than a
    per-file error halfway through. Files already in the cache need no more
    room, so a download picked up again isn't refused for them. HF_HUB_CACHE
    follows HF_HOME, HF_HUB_CACHE and XDG_CACHE_HOME (set inside the
    Flatpak), so this measures the disk the files land on."""
    from huggingface_hub import try_to_load_from_cache
    from huggingface_hub.constants import HF_HUB_CACHE
    from .download_utils import free_space_bytes
    needed = sum(size for name, size in files
                 if not isinstance(try_to_load_from_cache(repo, name), str)) * 1.1
    free = free_space_bytes(HF_HUB_CACHE)
    if not (needed and free and free < needed):
        return None
    return (f"Not enough disk space: model needs ~{needed / 1024 ** 3:.1f} GB free, "
            f"only {free / 1024 ** 3:.1f} GB available")


def _fetch_unlisted(repo, model_name, progress_callback, cancel_event):
    """Without a file list, fetch the files faster-whisper loads by name,
    one at a time like the listed ones, so Cancel stops it; without byte
    progress, since their sizes aren't known. Never through WhisperModel(),
    which would load the model as well: its caller loads it when it needs it."""
    progress_callback("Downloading model files...", 5)
    names = (*ESSENTIAL_MODEL_FILES, *VOCABULARY_FILES, "preprocessor_config.json")
    return _fetch_files(repo, model_name, [(name, 0) for name in names], False,
                        progress_callback, cancel_event)


def _fetch_files(repo, model_name, files, all_needed, progress_callback, cancel_event):
    """Fetch *files* from *repo* into the cache, with byte progress. True
    when everything needed is there: every file when *all_needed*, else the
    ones faster-whisper loads (see _download_is_usable)."""
    from huggingface_hub import hf_hub_download
    import tqdm as tqdm_lib
    total_bytes = sum(size for _, size in files)
    downloaded_bytes = [0]  # across all files

    def report(text):
        if total_bytes > 0:
            progress_callback(text, min(99, int((downloaded_bytes[0] / total_bytes) * 99)))

    class ProgressTqdm(tqdm_lib.tqdm):
        """Tqdm subclass that updates the unified download dialog progress bar."""

        def __init__(self, *args, **kwargs):
            import io
            self._current_file = kwargs.get('desc', 'file')
            # huggingface_hub passes 'name', which tqdm rejects.
            kwargs.pop('name', None)
            # tqdm disables itself without a TTY; this one feeds a
            # progress bar, so keep it on and send its text nowhere.
            kwargs['disable'] = False
            kwargs['file'] = io.StringIO()
            super().__init__(*args, **kwargs)

        def update(self, n=1):
            if cancel_event.is_set():
                raise InterruptedError("Download cancelled")
            super().update(n)
            downloaded_bytes[0] += n
            report(f"Downloading {self._current_file}...")

    failed_files, first_error = [], None
    for filename, file_size in files:
        if cancel_event.is_set():
            return False
        bytes_before = downloaded_bytes[0]

        def fetch(filename=filename, bytes_before=bytes_before):
            # A retried file counts its bytes once.
            downloaded_bytes[0] = bytes_before
            return hf_hub_download(repo_id=repo, filename=filename, tqdm_class=ProgressTqdm)

        try:
            _fetch_with_retry(fetch, filename)
            # No progress means the file was already cached; count it anyway.
            if downloaded_bytes[0] == bytes_before:
                downloaded_bytes[0] += file_size
                report(f"Loaded from cache: {filename}")
        except InterruptedError:
            return False
        except Exception as e:
            logger.warning(f"Error downloading {filename}: {e}")
            # Keep the progress bar moving, but remember the failure:
            # a model missing essential files must not be reported as
            # downloaded (the service would stall loading it).
            failed_files.append(filename)
            first_error = first_error or e
            downloaded_bytes[0] += file_size

    if failed_files and (all_needed or not _download_is_usable(
            failed_files, [name for name, _ in files])):
        logger.error(f"Model {model_name} download incomplete: "
                     f"{len(failed_files)} file(s) failed: {failed_files[:3]}")
        # At 0 %, so the window shows the reason (download_utils.failure_reason).
        progress_callback(f"{_failure_text(first_error)} ({len(failed_files)} file(s))", 0)
        return False
    if failed_files:
        # Only non-essential files (README, .gitattributes, alternative
        # formats) failed: not worth discarding a finished
        # multi-gigabyte download over.
        logger.warning(f"Model {model_name}: {len(failed_files)} non-essential file(s) "
                       f"could not be downloaded, model is still usable: {failed_files[:3]}")
    progress_callback("All files downloaded!", 100)
    return True


def make_model_download_func(model_name, repo_id=None, only_files=None):
    """
    Build a DownloadTask function that fetches model files into the Hugging Face
    cache without loading them, for UnifiedDownloadDialog.

    Args:
        model_name: Model size (e.g., "large-v3"), or a label for logs when
            *repo_id* is given.
        repo_id: Download from this Hugging Face repo instead of MODEL_REPOS
            (used for the AI cleanup model, which is not a speech model).
        only_files: With *repo_id*, fetch just these files from the repo.

    Returns:
        Callable: Function with signature (progress_callback, cancel_event) -> bool
                  progress_callback takes (message: str, percent: int); a
                  failure's reason is its last report, at 0 %.
    """
    if is_parakeet(model_name) and repo_id is None:
        from . import parakeet_ultra
        return parakeet_ultra.make_download_func("cpu")

    def download_func(progress_callback, cancel_event):
        """Download model files to HF cache only. Returns True on success."""
        repo = repo_id or MODEL_REPOS.get(model_name)
        if not repo:
            logger.error(f"Unknown model: {model_name}")
            progress_callback(f"Unknown model: {model_name}", 0)
            return False
        try:
            from huggingface_hub.utils import disable_progress_bars
            disable_progress_bars()
            progress_callback("Getting file list...", 1)
            files = _listed_files(repo, model_name, only_files)
            if not files:
                return _fetch_unlisted(repo, model_name, progress_callback, cancel_event)
            problem = _disk_space_problem(repo, files)
            if problem:
                logger.error(problem)
                progress_callback(problem, 0)
                return False
            return _fetch_files(repo, model_name, files, bool(only_files),
                                progress_callback, cancel_event)
        except Exception as e:
            logger.error(f"Model download failed for {model_name}: {e}")
            progress_callback(_failure_text(e), 0)
            return False

    return download_func


def _confirm_download(parent, model_name):
    """Ask before downloading *model_name*, naming its size: True to go on."""
    size_str = MODEL_DISPLAY_SIZES.get(model_name, "unknown size")
    confirm_dialog = message_dialog(
        parent=parent,
        message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.OK_CANCEL,
        text=f"Download {model_title(model_name)} Model?"
    )
    confirm_dialog.format_secondary_text(
        f"TuxTalk needs to download the {model_title(model_name)} AI model ({size_str}) "
        "for speech recognition.\n\n"
        "This is a one-time download that will be cached for future use.\n\n"
        "Continue with download?"
    )
    confirm_dialog.set_position(Gtk.WindowPosition.CENTER)
    response = confirm_dialog.run()
    confirm_dialog.destroy()
    if response != Gtk.ResponseType.OK:
        logger.info(f"User declined model download: {model_name}")
        return False
    return True


def download_model_files(parent, model, confirm=False):
    """Download *model*'s files for the processor or CUDA in the download
    window: (here now, cancelled). The caller checks is_model_cached_fast
    first. The model isn't loaded; what dictates with it loads it.
    *confirm* asks first. Success is told only once every file is there."""
    from .download_progress_dialog import DownloadTask, UnifiedDownloadDialog
    if confirm and not _confirm_download(parent, model):
        return False, True
    logger.info(f"Model {model} not found, downloading...")
    dialog = UnifiedDownloadDialog(
        parent=parent, title="Downloading the speech model",
        description="This may take a few minutes depending on your connection.")
    task = DownloadTask(model_title(model), MODEL_DISPLAY_SIZES.get(model, "unknown size"),
                        make_model_download_func(model))
    dialog.add_task(task)
    dialog.run()
    # The downloader's word is trusted only once the cache holds every file.
    ok = task.success and not task.cancelled and is_model_cached_fast(model)
    if ok:
        logger.info(f"Model {model} downloaded successfully")
    return ok, task.cancelled


def parakeet_update_needed(cfg):
    """Where ("vulkan" or "cpu") *cfg* dictates with the original Parakeet v3
    an earlier TuxTalk downloaded, now that Parakeet Ultra is out; None when
    it doesn't use Parakeet or already has Ultra there."""
    from . import parakeet_ultra, whisper_vulkan
    from .parakeet_engine import effective_device
    if not is_parakeet(cfg.model):
        return None
    device = effective_device(cfg.model, cfg.device)
    if not parakeet_ultra.missing(device):
        return None
    on_disk = whisper_vulkan.model_path(cfg.model) if device == "vulkan" else _parakeet_cached_dir()
    return device if on_disk else None


def parakeet_update_size(device):
    """What updating *device* to Parakeet Ultra downloads, as "669 MB"."""
    from . import parakeet_ultra
    return f"{sum(parakeet_ultra.FILES[n][0] for n in parakeet_ultra.missing(device)) / 1e6:.0f} MB"


def download_parakeet_update(device, parent=None):
    """Download Parakeet Ultra for *device* behind a progress window, for the
    tray's one-time offer and Preferences' Update Parakeet. True when it
    arrived, False when cancelled, None when the download failed."""
    from . import parakeet_ultra
    from .download_progress_dialog import DownloadTask, UnifiedDownloadDialog
    dialog = UnifiedDownloadDialog(parent=parent, title="Updating Parakeet",
                                   description="One-time download. Everything runs on this computer.")
    dialog.add_task(DownloadTask("Parakeet Ultra", parakeet_update_size(device),
                                 parakeet_ultra.make_download_func(device)))
    results = dialog.run()
    if all(r.get("success") for r in results.values()) and not parakeet_ultra.missing(device):
        return True
    if any(r.get("cancelled") for r in results.values()):
        return False
    return None


class BrokenModelFiles(Exception):
    """A downloaded model won't load, and the caller can't show the download
    dialogs to fetch it again."""


# Pauses before the second and third try of a model file.
_RETRY_WAITS = (2.0, 5.0)


def _fetch_with_retry(fetch, filename, sleep=None):
    """fetch() (one file's download), tried again after a short wait if it
    fails: a single slow moment shouldn't sink a 670 MB download. A cancel
    is never retried; after the last try its error is raised."""
    import time
    from huggingface_hub.utils import EntryNotFoundError
    sleep = sleep or time.sleep
    for attempt, wait in enumerate((*_RETRY_WAITS, None), start=1):
        try:
            return fetch()
        except (InterruptedError, EntryNotFoundError):
            raise                       # a cancel, or a file the model hasn't
        except Exception as e:
            if wait is None:
                raise
            logger.warning(f"Download of {filename} failed (try {attempt}): {e}; trying again")
            sleep(wait)


def download_model_with_progress(model_name, device="cpu", compute_type="int8", parent=None,
                                 show_confirmation=True, dialogs=True):
    """
    Load a model, first downloading it behind a progress dialog if needed.

    Args:
        model_name: Model size (e.g., "tiny", "small", "medium", "large-v3")
        device: Device to use ("cpu" or "cuda")
        compute_type: Compute type ("int8", "float16", etc.)
        parent: Optional parent window for dialog
        show_confirmation: Whether to show confirmation dialog before download (default True)
        dialogs: False when this thread must not open GTK dialogs (another
            thread runs Gtk.main()). A downloaded model that won't load then
            raises BrokenModelFiles instead of downloading again, and one
            that isn't downloaded returns None instead of downloading.

    Returns:
        WhisperModel (or ParakeetModel) instance, or None if cancelled/failed
    """
    cached = is_model_cached_fast(model_name)
    logger.info(f"Model cache check: {model_name} cached={cached}")

    if cached:
        logger.info(f"Model {model_name} already cached, loading directly (no download window)")
        print(f"Model {model_name} already cached - loading without download")
        try:
            return load_model(model_name, device=device, compute_type=compute_type)
        except Exception as e:
            # Broken files are downloaded again; a device problem (the files
            # load on the processor) is the caller's to report.
            if is_parakeet(model_name) or _loads_on_cpu(model_name):
                raise
            if not dialogs:
                raise BrokenModelFiles(
                    f"{model_name} looked downloaded but won't load: {e}") from e
            # Afresh (force_download below): the cache's copy is what won't load.
            logger.warning(f"Model {model_name} looked downloaded but won't load "
                           f"({e}); downloading it again")

    if not dialogs:
        logger.warning(f"Model {model_name} isn't downloaded, and this thread can't show "
                       "the download")
        return None

    logger.info(f"Model {model_name} NOT cached - showing download progress dialog")
    print(f"Model {model_name} NOT cached - will download and show progress")
    settings = Gtk.Settings.get_default()
    if settings:        # for this window and the ones after it
        settings.set_property("gtk-application-prefer-dark-theme", True)

    size_str = MODEL_DISPLAY_SIZES.get(model_name, "unknown size")

    if show_confirmation and not _confirm_download(parent, model_name):
        return None

    progress_dialog = Gtk.Dialog(title=f"Downloading Model: {model_title(model_name)}")
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
    title_label.set_markup(f'<span size="large"><b>Downloading {model_title(model_name)} Model</b></span>')
    content.pack_start(title_label, False, False, 0)

    status_label = Gtk.Label()
    status_label.set_markup(f'<span>Downloading... ({size_str})</span>')
    status_label.set_line_wrap(True)
    content.pack_start(status_label, False, False, 0)

    progress_bar = Gtk.ProgressBar()
    progress_bar.set_show_text(True)
    progress_bar.set_text("0%")
    content.pack_start(progress_bar, False, False, 0)

    info_label = Gtk.Label()
    info_label.set_markup('<span size="small"><i>This may take several minutes on first download...</i></span>')
    info_label.set_opacity(0.7)
    content.pack_start(info_label, False, False, 0)

    cancel_button = Gtk.Button(label="Cancel")
    cancel_button.set_can_default(True)
    cancel_button.show()
    progress_dialog.add_action_widget(cancel_button, Gtk.ResponseType.CANCEL)

    progress_dialog.show_all()

    cancel_event = threading.Event()
    download_result = [None]
    download_error = [None]

    # Progress tracking state (shared between threads)
    progress_state = {
        'total_bytes': 0,
        'downloaded_bytes': 0,
        'last_percent': -1
    }

    def update_ui_progress(percent, file_info=""):
        """Update the progress bar on the GTK thread, only when the whole percent changes."""
        if int(percent) == progress_state['last_percent']:
            return
        progress_state['last_percent'] = int(percent)

        def do_update():
            if not cancel_event.is_set():       # else the dialog is already gone
                progress_bar.set_fraction(percent / 100.0)
                progress_bar.set_text(f"{int(percent)}%")
                if file_info:
                    status_label.set_text(file_info)    # a file name can hold & or <
            return False
        GLib.idle_add(do_update)

    def close_dialog():
        if cancel_event.is_set():
            return False       # the dialog is already gone
        progress_bar.set_fraction(1.0)
        progress_bar.set_text("100%")
        # Unless a Cancel in these 200 ms closed the window already.
        GLib.timeout_add(200, lambda: not cancel_event.is_set()
                         and progress_dialog.response(Gtk.ResponseType.OK))
        return False

    def do_download():
        """Download model files with byte-level progress tracking"""
        try:
            if cancel_event.is_set():
                return

            if is_parakeet(model_name):
                from . import parakeet_ultra
                if not parakeet_ultra.make_download_func("cpu")(
                        lambda text, percent: update_ui_progress(percent * 0.95, text),
                        cancel_event):
                    if not cancel_event.is_set():
                        download_error[0] = RuntimeError("the files could not be downloaded")
                    return
                update_ui_progress(95, "Loading model...")
                model = load_model(model_name)
                if not cancel_event.is_set():
                    download_result[0] = model
                return

            logger.info(f"Downloading model {model_name} using huggingface_hub")

            from huggingface_hub import hf_hub_download
            from huggingface_hub.utils import disable_progress_bars
            import tqdm

            disable_progress_bars()

            repo_id = MODEL_REPOS.get(model_name)
            if not repo_id:
                raise ValueError(f"Unknown model: {model_name}")

            try:
                files_with_sizes = _files_to_download(repo_id)
                total_bytes = sum(size for _, size in files_with_sizes)
                progress_state['total_bytes'] = total_bytes
                logger.info(f"Model {model_name}: {len(files_with_sizes)} files, {total_bytes / 1024 / 1024:.1f} MB total")
            except Exception as e:
                logger.warning(f"Could not get file sizes: {e}")
                files_with_sizes = []
                total_bytes = 0

            if not files_with_sizes:
                # Without a file list, let WhisperModel download it (no byte progress).
                from faster_whisper import WhisperModel
                update_ui_progress(50, "Downloading model...")
                model = WhisperModel(
                    model_name,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=os.cpu_count() or 4
                )
                download_result[0] = model
                return

            class GtkProgressTqdm(tqdm.tqdm):
                """Tqdm subclass that drives this dialog's GTK progress bar."""
                def __init__(self, *args, **kwargs):
                    import io
                    self._current_file = kwargs.get('desc', 'file')
                    # huggingface_hub passes 'name', which tqdm rejects.
                    kwargs.pop('name', None)
                    # tqdm disables itself without a TTY; this one feeds a
                    # progress bar, so keep it on and send its text nowhere.
                    kwargs['disable'] = False
                    kwargs['file'] = io.StringIO()
                    super().__init__(*args, **kwargs)

                def update(self, n=1):
                    if cancel_event.is_set():
                        raise InterruptedError("Download cancelled")
                    super().update(n)
                    progress_state['downloaded_bytes'] += n
                    if progress_state['total_bytes'] > 0:
                        percent = (progress_state['downloaded_bytes'] / progress_state['total_bytes']) * 95
                        percent = min(95, percent)  # the last 5% is loading the model
                        update_ui_progress(percent, f"Downloading {self._current_file}...")

            failed = []
            for filename, file_size in files_with_sizes:
                if cancel_event.is_set():
                    raise InterruptedError("Download cancelled")

                bytes_before = progress_state['downloaded_bytes']

                def fetch(filename=filename, bytes_before=bytes_before):
                    # A retried file counts its bytes once.
                    progress_state['downloaded_bytes'] = bytes_before
                    return hf_hub_download(repo_id=repo_id, filename=filename,
                                           tqdm_class=GtkProgressTqdm, force_download=cached)

                try:
                    _fetch_with_retry(fetch, filename)
                    # No progress means the file was already cached; count it anyway.
                    if progress_state['downloaded_bytes'] == bytes_before:
                        progress_state['downloaded_bytes'] += file_size
                        if progress_state['total_bytes'] > 0:
                            percent = (progress_state['downloaded_bytes'] / progress_state['total_bytes']) * 95
                            percent = min(95, percent)
                            update_ui_progress(percent, f"Loaded from cache: {filename}")
                except InterruptedError:
                    raise
                except Exception as e:
                    # Keep the bar moving; a missing essential file shows up
                    # when the model fails to load below.
                    logger.warning(f"Error downloading {filename}: {e}")
                    failed.append(filename)
                    progress_state['downloaded_bytes'] = bytes_before + file_size

            if cancel_event.is_set():
                return

            update_ui_progress(95, "Loading model...")
            if failed:
                logger.warning(f"{len(failed)} file(s) failed to download ({', '.join(failed)}); "
                               f"trying to load model {model_name} anyway")
            else:
                logger.info(f"All files downloaded, loading model {model_name}")

            model = load_model(model_name, device=device, compute_type=compute_type)

            if not cancel_event.is_set():
                download_result[0] = model
                logger.info(f"Model {model_name} loaded successfully")

        except InterruptedError:
            logger.info("Model download interrupted by user")
        except Exception as e:
            download_error[0] = e
            logger.error(f"Model download failed: {e}")
        finally:
            # Every way out closes the dialog, including the early return of
            # the fallback path; otherwise run() waits for a Cancel that then
            # throws the finished model away.
            GLib.idle_add(close_dialog)

    download_thread = threading.Thread(target=do_download)
    download_thread.daemon = True
    download_thread.start()

    response = progress_dialog.run()
    progress_dialog.destroy()
    cancel_event.set()      # the window is gone: nothing may update it any more

    # A Cancel after the download finished, in the 200 ms before the window
    # closes by itself, keeps the model.
    if (response in (Gtk.ResponseType.CANCEL, Gtk.ResponseType.DELETE_EVENT)
            and download_result[0] is None):
        logger.info(f"User cancelled model download: {model_name}")
        return None

    download_thread.join()

    if download_error[0]:
        msg = message_dialog(
            parent=parent,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Model Download Failed"
        )
        msg.format_secondary_text(
            f"Could not download {model_title(model_name)} model.\n\n"
            f"Error: {str(download_error[0])}\n\n"
            "Check your internet connection and try again."
        )
        msg.run()
        msg.destroy()
        return None

    return download_result[0]
