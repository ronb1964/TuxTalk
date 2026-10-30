"""
Parakeet Ultra: the Parakeet that TuxTalk downloads.

Moondream's post-trained Parakeet TDT 0.6B v3 (moondream/parakeet-ultra,
CC-BY-4.0): the same size and speed as NVIDIA's v3, with fewer mistakes in
every language measured. On 2,400 clips in English, German, Spanish and
Italian it made 8-21% fewer on the graphics card, and 20-30% fewer than the
processor copy TuxTalk used before, whose int8 rounding cost accuracy.

Both formats come from a GitHub pre-release of TuxTalk's own, so a download
doesn't depend on someone else's repository staying as it is, and every file
is checked against its SHA256 below before it lands:

- the processor's int8 ONNX files are Olicorne's export
  (Olicorne/parakeet-tdt-0.6b-v3-ultra-onnx, commit a1fe742), unchanged;
- the graphics card's file is the same checkpoint converted with whisper.cpp
  1.9.5's convert-parakeet-to-ggml.py --use-f32 and parakeet-quantize q8_0,
  the steps that turn NVIDIA's v3 into ggml-org's file byte for byte.

Someone who set Parakeet up with an earlier TuxTalk has the original v3 files.
Those keep working (parakeet_engine.cached_model_dir, whisper_vulkan.model_path)
until they take the one-time offer to update (update_offers.maybe_offer_parakeet_update).
"""
import os

from .logger import setup_logger

logger = setup_logger(__name__)

# A published file never changes: a new conversion gets a new tag, because
# released versions check these hashes.
RELEASE_TAG = "parakeet-ultra-1"
RELEASE_URL = f"https://github.com/ronb1964/TuxTalk/releases/download/{RELEASE_TAG}/"

# name: (bytes, sha256)
CPU_FILES = {
    "config.json": (97, "666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466"),
    "vocab.txt": (93939, "d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d"),
    "nemo128.onnx": (139764, "a9fde1486ebfcc08f328d75ad4610c67835fea58c73ba57e3209a6f6cf019e9f"),
    "decoder_joint-model.int8.onnx":
        (18203490, "667ffde2242a560923cba6bc7f69ef03d736f8a36ca9588f2e6f9fa818cf2fc1"),
    "encoder-model.int8.onnx":
        (649537325, "8a2b47169cf3f1b114010e12c0221bc6f07203289477a65a44847b9e3f1e01ed"),
}
GPU_FILE = "ggml-parakeet-ultra-q8_0.bin"
FILES = {**CPU_FILES,
         GPU_FILE: (668757119, "fa28ec8c6afd9893da9829ec9f2fa908a60bc0fbebd37fd60bbd2f0953076fb2")}

_TRIES = 3      # a single slow moment shouldn't sink a 670 MB download


def folder():
    from .config import get_data_dir
    return os.path.join(get_data_dir(), "parakeet-ultra")


def _path(name):
    return os.path.join(folder(), name)


def missing(device):
    """The files Parakeet Ultra still needs on *device*: "vulkan" for the
    graphics card, anything else for the processor. download_file puts a file
    in place only once its SHA256 matched, so a file that's there is whole."""
    names = (GPU_FILE,) if device == "vulkan" else tuple(CPU_FILES)
    return tuple(name for name in names if not os.path.isfile(_path(name)))


def cpu_dir():
    """The folder of the processor files, or None until all are downloaded."""
    return None if missing("cpu") else folder()


def gpu_file():
    """The graphics card's file, or None until it's downloaded."""
    return None if missing("vulkan") else _path(GPU_FILE)


def make_download_func(device):
    """DownloadTask function: fetch what *device* still needs (see missing()).
    A failure's reason is its last report, at 0 %."""
    def download(progress_callback, cancel_event):
        from .download_utils import DownloadError, free_space_bytes
        todo = missing(device)
        total = sum(FILES[name][0] for name in todo)
        free = free_space_bytes(folder())
        if free and free < total * 1.1:
            logger.error(f"Not enough disk space for Parakeet: {free} bytes free, {total} needed")
            progress_callback(f"Not enough disk space: about {total * 1.1 / 1e9:.1f} GB "
                              "needs to be free", 0)
            return False
        done = 0
        for name in todo:
            def hook(got, _total, before=done):
                progress_callback("Downloading Parakeet...", min(99, (before + got) * 99 // total))

            try:
                _fetch_with_retries(name, cancel_event, hook)
            except InterruptedError:
                return False
            except DownloadError as e:
                progress_callback(f"Download failed: {e}", 0)
                return False
            done += FILES[name][0]
        logger.info(f"Parakeet Ultra downloaded for {device or 'cpu'}")
        progress_callback("Done", 100)
        return True
    return download


def _fetch_with_retries(name, cancel_event, hook):
    """Fetch the release's file *name*, checked against its SHA256, trying
    again after a failure; the last try's DownloadError is raised, and a
    cancel (InterruptedError) at once."""
    from .download_utils import DownloadError, fetch_file
    for attempt in range(1, _TRIES + 1):
        try:
            fetch_file(RELEASE_URL + name, _path(name), timeout=60, cancel_event=cancel_event,
                       progress_hook=hook, expected_sha256=FILES[name][1])
            return
        except DownloadError as e:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Download cancelled") from e
            logger.warning(f"Download of {name} failed (try {attempt} of {_TRIES}): {e}")
            if attempt == _TRIES:
                raise
