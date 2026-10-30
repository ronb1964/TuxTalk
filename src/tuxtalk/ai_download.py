"""The local AI's files: llama.cpp's prebuilt llama-server (the Vulkan build,
which also carries every CPU variant: it uses the graphics card when there
is one and the processor otherwise) and the Qwen2.5-1.5B-Instruct model
(Apache 2.0, Q4_K_M). What they are, where they live, and their downloads,
made only when the user turns on a feature that needs them, so TuxTalk's
package does not grow. ai_server runs them.
"""
import os
import shutil

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

MODEL_REPO = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
MODEL_FILE = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
MODEL_SIZE_MB = 1117


def _size_text(mb):
    """*mb* megabytes as people read a download's size: "30 MB", "1.1 GB"."""
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb} MB"


LLAMA_SIZE_TEXT = _size_text(LLAMA_SIZE_MB)
MODEL_SIZE_TEXT = _size_text(MODEL_SIZE_MB)
# What turning the AI on downloads, its engine included: the size every
# window and Help give.
DOWNLOAD_SIZE_TEXT = _size_text(MODEL_SIZE_MB + LLAMA_SIZE_MB)


def _engine_dir():
    from .config import get_data_dir
    return os.path.join(get_data_dir(), "ai-engine", LLAMA_TAG)


def server_binary():
    """Where llama-server is once downloaded."""
    return os.path.join(_engine_dir(), f"llama-{LLAMA_TAG}", "llama-server")


def model_path():
    """Local path of the downloaded model, or None."""
    from .download_utils import cached_hub_file
    return cached_hub_file(MODEL_REPO, MODEL_FILE)


def is_installed() -> bool:
    """Engine and model are both downloaded. Cheap: file checks only."""
    return os.access(server_binary(), os.X_OK) and model_path() is not None


def make_engine_download_func():
    """DownloadTask function: fetch, verify and unpack the llama.cpp engine."""
    def download(progress_callback, cancel_event):
        """Fetch, check and unpack llama.cpp's server into the engine folder,
        reporting progress; True once it's there. Another download finishing
        first is fine."""
        from .download_utils import download_area, download_file, install_turn, unpack_tar
        if os.access(server_binary(), os.X_OK):
            progress_callback("Already downloaded", 100)
            return True
        engine_dir = _engine_dir()

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
                with install_turn("ai-engine"):
                    if not os.access(server_binary(), os.X_OK):    # or another download did it
                        for name in os.listdir(folder):
                            target = os.path.join(engine_dir, name)
                            shutil.rmtree(target, ignore_errors=True)
                            os.replace(os.path.join(folder, name), target)
                    engine_process.remove_other_versions(os.path.dirname(engine_dir), LLAMA_TAG)
            except Exception as e:
                logger.error(f"Could not unpack the AI engine: {e}")
                progress_callback("Could not unpack the download", 0)
                return False
        progress_callback("Done", 100)
        return True
    return download


def make_model_download_func():
    """DownloadTask function: fetch the AI model into the Hugging Face cache."""
    from .model_helper import make_model_download_func as _hf_download
    return _hf_download("ai-cleanup", repo_id=MODEL_REPO, only_files=(MODEL_FILE,))
