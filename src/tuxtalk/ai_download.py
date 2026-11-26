"""The local AI's files: llama.cpp's prebuilt llama-server (the Vulkan build,
which also carries every CPU variant: it uses the graphics card when there
is one and the processor otherwise) and the Qwen3.5-4B model (Apache 2.0,
Q4_K_M, Unsloth's build: Qwen publishes no GGUF of its own). What they are,
where they live, and their downloads, made only when the user turns on a
feature that needs them, so TuxTalk's package does not grow. ai_server runs
them.
"""
import os

from . import engine_process
from .logger import setup_logger

logger = setup_logger(__name__)

LLAMA_TAG = "b11200"
LLAMA_ASSET = f"llama-{LLAMA_TAG}-bin-ubuntu-vulkan-x64.tar.gz"
LLAMA_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/{LLAMA_ASSET}"
# From GitHub's own digest of the release asset; the download is refused if
# it does not match, so a tampered or truncated engine is never run.
LLAMA_SHA256 = "376b003154a33bf1139302f66865fcd3dd0ce19a9b5b956dcc8b52b0962515ea"
LLAMA_SIZE_MB = 30

MODEL_REPO = "unsloth/Qwen3.5-4B-GGUF"
MODEL_FILE = "Qwen3.5-4B-Q4_K_M.gguf"
MODEL_SIZE_MB = 2741
# The model before 0.26. Asked to change a sentence's tone, it changed what
# about 8 in 100 meant ("I was under a van" became "under the influence");
# the 4B model kept nearly all of them. Still used where it's downloaded and
# the new one isn't yet, so an update never turns the AI off, until the new
# one arrives and this is deleted (remove_old_model).
OLD_MODEL_REPO = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
OLD_MODEL_FILE = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
# In the config folder once the start-up question offering the current model
# to someone on the old one was asked (startup_notices): it's asked once.
OFFERED_FILE = "better-ai-model-offered"


def _size_text(mb):
    """*mb* megabytes as people read a download's size: "30 MB", "1.1 GB"."""
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb} MB"


LLAMA_SIZE_TEXT = _size_text(LLAMA_SIZE_MB)
MODEL_SIZE_TEXT = _size_text(MODEL_SIZE_MB)
# What the server holds while the AI is on, measured with a 4096-token
# context: 3.1 GB of memory on the processor, or 2.9 GB of the graphics
# card's and 0.8 GB of memory with one.
MEMORY_TEXT = "3 GB"
# What turning the AI on downloads, its engine included: the size every
# window and Help give.
DOWNLOAD_SIZE_TEXT = _size_text(MODEL_SIZE_MB + LLAMA_SIZE_MB)


# The folder llama.cpp's archive unpacks to, in _engine_dir().
_SERVER_FOLDER = f"llama-{LLAMA_TAG}"


def _engine_dir():
    from .config import get_data_dir
    return os.path.join(get_data_dir(), "ai-engine", LLAMA_TAG)


def server_binary():
    """Where llama-server is once downloaded."""
    return os.path.join(_engine_dir(), _SERVER_FOLDER, "llama-server")


def model_path():
    """Local path of the model to run: the current one, else the one before
    it while that is all there is (OLD_MODEL_REPO); None when neither is."""
    from .download_utils import cached_hub_file
    return (cached_hub_file(MODEL_REPO, MODEL_FILE)
            or cached_hub_file(OLD_MODEL_REPO, OLD_MODEL_FILE))


def better_model_waiting() -> bool:
    """Whether the AI runs on the model before 0.26 because the current one
    hasn't been downloaded: Preferences and the start-up question offer it."""
    from .download_utils import cached_hub_file
    return (cached_hub_file(MODEL_REPO, MODEL_FILE) is None
            and cached_hub_file(OLD_MODEL_REPO, OLD_MODEL_FILE) is not None)


def remove_old_model() -> None:
    """Delete the model before 0.26 once the current one is downloaded, so
    its 1.1 GB doesn't stay on the disk. The download cache is shared: when
    its folder holds anything else (another app's file of the same
    repository), it's all left alone."""
    from .download_utils import cached_hub_file, cached_repo_files, remove_cached_repo
    if (cached_hub_file(MODEL_REPO, MODEL_FILE) is None
            or cached_hub_file(OLD_MODEL_REPO, OLD_MODEL_FILE) is None):
        return
    if cached_repo_files(OLD_MODEL_REPO) - {OLD_MODEL_FILE}:
        logger.info("Kept the AI model before 0.26: its download folder holds other files too")
    elif remove_cached_repo(OLD_MODEL_REPO):
        logger.info("Removed the AI model before 0.26; the new one is downloaded")


def is_installed() -> bool:
    """Engine and model are both downloaded. Cheap: file checks only."""
    return os.access(server_binary(), os.X_OK) and model_path() is not None


def make_engine_download_func():
    """DownloadTask function: fetch, verify and unpack the llama.cpp engine."""
    def download(progress_callback, cancel_event):
        """Fetch, check and unpack llama.cpp's server into the engine folder,
        reporting progress; True once it's there. Another download finishing
        first is fine."""
        from .download_utils import (download_area, download_file, install_turn,
                                     recover_cut_off_swaps, replace_folder,
                                     restore_cut_off_swap, unpack_tar)
        engine_dir = _engine_dir()
        recover_cut_off_swaps(engine_dir, (_SERVER_FOLDER,), "ai-engine", "the AI engine")
        if os.access(server_binary(), os.X_OK):
            progress_callback("Already downloaded", 100)
            return True

        def hook(done, total):
            if total:
                progress_callback("Downloading AI engine...", min(95, int(done * 95 / total)))

        # Inside engine_dir: remove_other_versions clears every other folder
        # beside it, which would take a download under way with it.
        with download_area(engine_dir, LLAMA_TAG, old_names=(LLAMA_ASSET,)) as (archive, folder):
            if not download_file(LLAMA_URL, archive, timeout=60, cancel_event=cancel_event,
                                 progress_hook=hook, expected_sha256=LLAMA_SHA256,
                                 report=progress_callback):
                return False
            try:
                progress_callback("Unpacking...", 97)
                unpack_tar(archive, folder)
                with install_turn("ai-engine", "the AI engine"):
                    # A complete engine at every moment: removed first, a
                    # kill before the move left none.
                    for name in os.listdir(folder):
                        restore_cut_off_swap(os.path.join(engine_dir, name),
                                             os.path.join(engine_dir, f".{name}.old"))
                    if not os.access(server_binary(), os.X_OK):    # or another download did it
                        for name in os.listdir(folder):
                            replace_folder(os.path.join(folder, name),
                                           os.path.join(engine_dir, name),
                                           os.path.join(engine_dir, f".{name}.old"))
                    engine_process.remove_other_versions(os.path.dirname(engine_dir), LLAMA_TAG)
            except Exception as e:
                logger.error(f"Could not unpack the AI engine: {e}")
                progress_callback("Could not unpack the download", 0)
                return False
        progress_callback("Done", 100)
        return True
    return download


def make_model_download_func():
    """DownloadTask function: fetch the AI model into the Hugging Face cache,
    then delete the model before it."""
    from .model_helper import make_model_download_func as _hf_download
    fetch = _hf_download("ai-cleanup", repo_id=MODEL_REPO, only_files=(MODEL_FILE,))

    def download(progress_callback, cancel_event):
        """Fetch the model; once it's here, the one before it goes."""
        done = fetch(progress_callback, cancel_event)
        if done:
            remove_old_model()
        return done
    return download
