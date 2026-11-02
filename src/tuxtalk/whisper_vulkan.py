"""
Whisper on any graphics card, through Vulkan.

TuxTalk's usual Whisper engine (faster-whisper / CTranslate2) can only use
NVIDIA graphics, through CUDA. This runs whisper.cpp built with its Vulkan
backend instead, which works on AMD, Intel and NVIDIA alike. For AMD and
Intel it's the only way to use the graphics chip. For NVIDIA it's the light
option: measured on an RTX 4070 Super it matched CUDA's speed (large-v3,
0.51 s vs 0.53 s for 11 s of speech) for a 22 MB download instead of 585 MB. whisper.cpp
publishes no Linux Vulkan build, so TuxTalk builds one (build-vulkan-engine.sh)
and hosts it as a GitHub pre-release.

Nothing here is downloaded unless the user picks "Vulkan (any GPU)" as the
device: the engine (~22 MB) and the model in whisper.cpp's own format, which
is a separate file from the faster-whisper one.

whisper.cpp 1.9.4 also runs Parakeet (libparakeet), so the same engine runs
Parakeet on the graphics chip: 0.17 s instead of 2.15 s for 66 s of speech on
an RTX 4070 Super. That runs as its own helper process,
parakeet_gpu.py, because whisper-server only serves Whisper models.

At run time it is a whisper-server process, started and watched the same way
as the AI engine (ai_server.py, engine_process.py): it dies with the dictation
service and leftovers from a crash are cleaned up. Each dictation is sent to
it over 127.0.0.1 as a WAV, with a key only this user can read.

Not every graphics chip is faster than the processor. A desktop Ryzen 7000's
built-in graphics has 2 compute units and measured no faster than its own
CPU, so speed_check() times both before the user switches.
"""
import glob
import io
import json
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.request
import uuid
import wave

import numpy as np

from . import engine_process
from .logger import setup_logger
from .parakeet_engine import PARAKEET_MODEL, is_parakeet

logger = setup_logger(__name__)

DEVICE_LABEL = "Vulkan (any GPU)"       # Preferences' Device dropdown

# --- The engine download ------------------------------------------------------

ENGINE_VERSION = "1.9.5"                           # whisper.cpp release it is built from
# 1.9.4 build 2 added Parakeet (libparakeet.so, parakeet-cli). 1.9.5 build 1
# adds TuxTalk's patch (vulkan-engine-patches/): Your Words for Parakeet on
# the graphics chip. Build 2: whisper-server answers only requests with the
# key it is started with, and has no /load route. Each build has its own
# asset and pre-release, because released versions download theirs and check
# its SHA256, so a published build must stay exactly as it is. The archive's
# folder is named after the whisper.cpp version alone; it unpacks into a
# folder per build (_build_folder). A new build takes over from the one in
# use, which stays until the build after it arrives.
ENGINE_BUILD = 2
ENGINE_NAME = f"talktype-whisper-vulkan-{ENGINE_VERSION}"
ENGINE_ASSET = f"{ENGINE_NAME}-b{ENGINE_BUILD}-x64.tar.gz"
ENGINE_TAG = f"whisper-vulkan-{ENGINE_VERSION}-b{ENGINE_BUILD}"     # TuxTalk GitHub pre-release
ENGINE_URL = f"https://github.com/ronb1964/TuxTalk/releases/download/{ENGINE_TAG}/{ENGINE_ASSET}"
# From build-vulkan-engine.sh's .sha256; the download is refused if it doesn't
# match, so a tampered or truncated engine is never run.
ENGINE_SHA256 = "eb4afebecb3119d84ca66022b76e099ec59cf5e34fe3d31bdd2d7a42f7f8a72a"
ENGINE_SIZE_TEXT = "22 MB"

# --- Models, in whisper.cpp's format -------------------------------------------
# Quantized: about half the download, and less graphics memory, which matters
# on built-in graphics that share the computer's RAM. q8_0 for the small models
# (no accuracy cost worth measuring), q5_0 for the big ones.

MODEL_REPO = "ggerganov/whisper.cpp"
MODEL_FILES = {
    "tiny": ("ggml-tiny-q8_0.bin", "44 MB"),
    "base": ("ggml-base-q8_0.bin", "82 MB"),
    "small": ("ggml-small-q8_0.bin", "264 MB"),
    "medium": ("ggml-medium-q5_0.bin", "539 MB"),
    "large-v3": ("ggml-large-v3-q5_0.bin", "1.1 GB"),
    # The original v3, which earlier TuxTalks downloaded: still used where
    # it's on disk and Parakeet Ultra isn't (parakeet_ultra.py, same size).
    PARAKEET_MODEL: ("ggml-parakeet-tdt-0.6b-v3-q8_0.bin", "669 MB"),
}
# Models not hosted in whisper.cpp's own repo.
MODEL_REPOS = {PARAKEET_MODEL: "ggml-org/parakeet-GGUF"}


def supports_model(model_name) -> bool:
    """Whether *model_name* can run on the graphics chip through Vulkan."""
    return model_name in MODEL_FILES


def model_repo(model_name):
    """Hugging Face repo holding *model_name*'s whisper.cpp file."""
    return MODEL_REPOS.get(model_name, MODEL_REPO)


# --- Is there a graphics chip? -------------------------------------------------

AMD_VENDOR, INTEL_VENDOR, NVIDIA_VENDOR = "0x1002", "0x8086", "0x10de"


def graphics_vendors(drm_root="/sys/class/drm"):
    """PCI vendor ids of the graphics chips the kernel drives."""
    vendors = set()
    for vendor_file in glob.glob(os.path.join(drm_root, "card*", "device", "vendor")):
        try:
            with open(vendor_file) as f:
                vendors.add(f.read().strip().lower())
        except OSError:
            continue
    return vendors


def vulkan_available() -> bool:
    """Whether the system has the Vulkan loader (part of every desktop's
    graphics drivers; the engine can't run without it)."""
    import ctypes
    try:
        ctypes.CDLL("libvulkan.so.1")
        return True
    except OSError:
        return False


def is_offered() -> bool:
    """Show "Vulkan (any GPU)" as a device choice? Any AMD, Intel or NVIDIA
    chip with a Vulkan driver (NVIDIA's own driver includes one)."""
    vendors = graphics_vendors()
    return bool(vendors & {AMD_VENDOR, INTEL_VENDOR, NVIDIA_VENDOR}) and vulkan_available()


def has_nvidia() -> bool:
    """Whether the kernel drives an NVIDIA graphics chip."""
    return NVIDIA_VENDOR in graphics_vendors()


# --- Where things live ---------------------------------------------------------

def _build_folder(build=None):
    """The folder *build* (default: this one) unpacks into. With the build in
    its name, an installed older build doesn't count as this one, which is
    then downloaded beside it and takes over once it's there. Build 1 keeps
    the version's name alone, the folder earlier versions installed it in,
    so it isn't downloaded again."""
    build = build or ENGINE_BUILD
    return ENGINE_VERSION if build == 1 else f"{ENGINE_VERSION}-b{build}"


def _engines_root():
    from .config import get_data_dir
    return os.path.join(get_data_dir(), "whisper-vulkan")


def _engine_dir():
    """Where this build is, once downloaded."""
    return os.path.join(_engines_root(), _build_folder(), ENGINE_NAME)


def _earlier_builds():
    """Folders of earlier builds of this whisper.cpp version on disk, newest
    first. Only the same version: TuxTalk calls libparakeet's functions as
    that version declares them (parakeet_gpu_worker.py)."""
    pattern = re.compile(re.escape(ENGINE_VERSION) + r"(?:-b(\d+))?")
    try:
        names = os.listdir(_engines_root())
    except OSError:
        return []
    builds = []
    for name in names:
        match = pattern.fullmatch(name)
        if match and int(match.group(1) or 1) < ENGINE_BUILD:
            builds.append((int(match.group(1) or 1), name))
    return [os.path.join(_engines_root(), name, ENGINE_NAME) for _, name in sorted(builds)[::-1]]


def engine_dir():
    """The engine to run: this build, or, until it has downloaded
    (update_in_background), an earlier build of the same whisper.cpp
    version, so dictation stays on the graphics card meanwhile."""
    for folder in (_engine_dir(), *_earlier_builds()):
        if _can_run(folder):
            return folder
    return _engine_dir()


def _binary(name):
    return os.path.join(engine_dir(), name)


def _can_run(folder, model_name=None) -> bool:
    """The engine in *folder* is there, and can run *model_name* if one is
    given. whisper.cpp 1.9.4's first build, in a folder of its own that an
    update removes, had no Parakeet in it."""
    if not os.access(os.path.join(folder, "whisper-server"), os.X_OK):
        return False
    return not is_parakeet(model_name) or os.access(os.path.join(folder, "parakeet-cli"),
                                                    os.X_OK)


def is_engine_installed(model_name=None) -> bool:
    """An engine is downloaded, this build or an earlier one, and can run
    *model_name* if one is given."""
    return _can_run(engine_dir(), model_name)


def model_path(model_name):
    """Local path of the whisper.cpp model for *model_name*, or None. For
    Parakeet, Parakeet Ultra once it's downloaded, else the original v3."""
    if not supports_model(model_name):
        return None
    if is_parakeet(model_name):
        from . import parakeet_ultra
        ultra = parakeet_ultra.gpu_file()
        if ultra is not None:
            return ultra
    from .download_utils import cached_hub_file
    return cached_hub_file(model_repo(model_name), MODEL_FILES[model_name][0])


def is_installed(model_name) -> bool:
    """Engine and this model's whisper.cpp file are both downloaded."""
    return is_engine_installed(model_name) and model_path(model_name) is not None


def make_engine_download_func(model_name=None):
    """DownloadTask function: fetch, verify and unpack the engine, unless the
    one already there can run *model_name*."""
    def download(progress_callback, cancel_event):
        """Fetch, check and unpack the Vulkan engine into its folder, reporting
        progress; True once it's there. Another download finishing first is
        fine."""
        from .download_utils import (download_area, download_file, install_turn,
                                     recover_cut_off_swaps, replace_folder,
                                     restore_cut_off_swap, unpack_tar)
        parent = os.path.dirname(_engine_dir())
        recover_cut_off_swaps(parent, (ENGINE_NAME,), "vulkan-engine",
                              "the graphics engine")
        if _can_run(_engine_dir(), model_name):
            progress_callback("Already downloaded", 100)
            return True

        def hook(done, total):
            if total:
                progress_callback("Downloading graphics engine...", min(95, int(done * 95 / total)))

        # Before 0.19 the archive was ENGINE_ASSET itself, left behind when unpacking failed.
        with download_area(parent, ENGINE_NAME, old_names=(ENGINE_ASSET,)) as (archive, folder):
            if not download_file(ENGINE_URL, archive, timeout=60, cancel_event=cancel_event,
                                 progress_hook=hook, expected_sha256=ENGINE_SHA256,
                                 report=progress_callback):
                return False
            try:
                progress_callback("Unpacking...", 97)
                unpack_tar(archive, folder)
                with install_turn("vulkan-engine", "the graphics engine"):
                    # A complete engine at every moment: removed first, a
                    # kill before the move left none.
                    target, old = _engine_dir(), os.path.join(parent, f".{ENGINE_NAME}.old")
                    restore_cut_off_swap(target, old)
                    # The build in use until now stays, until the next
                    # build replaces this one: another TuxTalk process may
                    # have chosen it (engine_dir) and not started it yet.
                    previous = os.path.basename(os.path.dirname(engine_dir()))
                    if not _can_run(target, model_name):    # or another download did it
                        replace_folder(os.path.join(folder, ENGINE_NAME), target, old)
                    engine_process.remove_other_versions(os.path.dirname(parent),
                                                         _build_folder(), previous)
            except Exception as e:
                logger.error(f"Could not unpack the graphics engine: {e}")
                progress_callback("Could not unpack the download", 0)
                return False
        progress_callback("Done", 100)
        return True
    return download


# A try that didn't happen or failed waits this long before the next one, at
# a later start of the engine: a Power Saving wake shouldn't fetch it again
# every time, offline too, but a login before the network was up shouldn't
# leave the whole session on the earlier build either.
UPDATE_RETRY_S = 600
_update_lock = threading.Lock()
_update_running = False
_update_failed_at = None        # time.monotonic() of the last try that didn't update


def update_in_background():
    """While an earlier build is what runs (engine_dir), download this one
    on a thread of its own, which the next start of the engine uses: the
    user chose this engine, and an update changes nothing they see. Never
    two at once, and not when the user turned off checking for updates at
    start (Preferences → Updates & Backup): the earlier build keeps
    working. The thread, or None when nothing was started."""
    global _update_running, _update_failed_at
    with _update_lock:
        waiting = (_update_failed_at is not None
                   and time.monotonic() - _update_failed_at < UPDATE_RETRY_S)
        if _update_running or waiting or engine_dir() == _engine_dir():
            return None
        from .config import load_config
        if not load_config().auto_check_updates:
            _update_failed_at = time.monotonic()
            logger.info(f"Graphics engine build {ENGINE_BUILD} not downloaded: checking for "
                        "updates at start is off")
            return None
        _update_running = True
    thread = threading.Thread(target=_update, daemon=True, name="vulkan-engine-update")
    thread.start()
    return thread


def _update():
    """The update's download, with how it ended in the log."""
    global _update_running, _update_failed_at
    done = False
    try:
        done = _download_update()
    finally:
        with _update_lock:
            _update_running = False
            _update_failed_at = None if done else time.monotonic()


def _download_update() -> bool:
    """Download this build; True once it's in place. Whatever went wrong is
    logged."""
    from .download_utils import failure_reason
    reports = []
    logger.info(f"Updating the graphics engine to build {ENGINE_BUILD}")
    try:
        done = make_engine_download_func()(lambda *report: reports.append(report), None)
    except OSError as e:        # the download folder couldn't be made, say
        done, reports = False, [(str(e), 0)]
    except Exception as e:      # a bug: in the log, not only on the thread's stderr
        logger.warning(f"The graphics engine update failed: {type(e).__name__}: {e}",
                       exc_info=True)
        done = False
    if done:
        logger.info("Graphics engine updated; it's used from its next start")
        return True
    why = failure_reason(*reports[-1]) if reports else None
    logger.warning(f"The graphics engine update didn't finish ({why or 'stopped'}); "
                   f"trying again at a start of the engine {UPDATE_RETRY_S // 60} minutes "
                   "from now or later")
    return False


def make_model_download_func(model_name):
    """DownloadTask function: fetch *model_name*'s whisper.cpp file."""
    if is_parakeet(model_name):
        from . import parakeet_ultra
        return parakeet_ultra.make_download_func("vulkan")
    from .model_helper import make_model_download_func as _hf_download
    return _hf_download(f"whisper-vulkan-{model_name}", repo_id=model_repo(model_name),
                        only_files=(MODEL_FILES[model_name][0],))


# --- Which graphics chip -------------------------------------------------------

_DEVICE_LINE = re.compile(r"ggml_vulkan: (\d+) = (.+?) \| uma: (\d)")


def parse_vulkan_devices(output):
    """[(index, name, is_nvidia, is_integrated)] from the device list the
    engine prints when it starts."""
    devices = []
    for index, name, uma in _DEVICE_LINE.findall(output):
        devices.append((int(index), name.strip(), "nvidia" in name.lower(), uma == "1"))
    return devices


def choose_device(devices):
    """The graphics chip to use: a separate graphics card (any brand) before
    one built into the processor, which is far smaller. On a machine with an
    NVIDIA card and a Ryzen's built-in Radeon, that's the NVIDIA card: 16x
    faster than the processor, where the built-in chip was no faster at all.
    None if there is no graphics chip."""
    if not devices:
        return None
    return sorted(devices, key=lambda d: d[3])[0][0]     # discrete (uma 0) first, stable


def _engine_env(folder=None):
    """The environment the engine in *folder* (default: engine_dir) runs in."""
    return {**os.environ, "LD_LIBRARY_PATH": folder or engine_dir()}


def find_device():
    """Index of the graphics chip the engine should use, or None."""
    folder = engine_dir()       # once: the program and its libraries from one build
    command, env = engine_process.parent_death_command(
        [os.path.join(folder, "whisper-server"), "--help"], _engine_env(folder))
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors="replace",
                                timeout=30, env=env)
        out = result.stderr
        why = engine_process.launch_failure(result.returncode)
        if why:
            logger.warning(f"Could not list graphics devices{why}")
    except Exception as e:
        logger.warning(f"Could not list graphics devices: {e}")
        return None
    devices = parse_vulkan_devices(out)
    logger.info(f"Vulkan devices: {devices}")
    return choose_device(devices)


# --- The speed check -----------------------------------------------------------

_TOTAL_TIME = re.compile(r"total time =\s+([\d.]+) ms")
_LOAD_TIME = re.compile(r"load time =\s+([\d.]+) ms")


def _run_timed(cmd, cancel=None, timeout=300):
    """(exit code, stderr) of *cmd*, killed when *cancel* is set (Stop in the
    speed check's window) or after *timeout* seconds: a hung graphics driver
    would otherwise keep the chip and a processor core busy for minutes. A
    run Stop came before isn't started: on a hung driver it may not die."""
    if cancel is not None and cancel.is_set():
        raise RuntimeError("speed check stopped")
    deadline = time.monotonic() + timeout
    # Through the launcher like the engine itself: the window running the
    # check, killed mid-run, takes the run (a graphics chip and a core) with it.
    command, env = engine_process.parent_death_command(cmd, _engine_env())
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          text=True, errors="replace", env=env) as proc:
        while True:
            try:
                stderr = proc.communicate(timeout=0.5)[1]
                return proc.returncode, stderr
            except subprocess.TimeoutExpired:
                stopped = cancel is not None and cancel.is_set()
                if stopped or time.monotonic() > deadline:
                    proc.kill()
                    proc.communicate()
                    raise RuntimeError("speed check stopped" if stopped
                                       else "speed check run took too long") from None


def _time_one(model, extra_args, threads, cli="whisper-cli", cancel=None):
    """Seconds *cli* takes to transcribe the bundled speed-check sample."""
    returncode, stderr = _run_timed(
        [_binary(cli), "-m", model, "-f", _binary("speed-check.wav"),
         "-t", str(threads), *extra_args], cancel)
    found = _TOTAL_TIME.search(stderr)
    if returncode != 0 or not found:
        raise RuntimeError(f"speed check run failed (exit {returncode})"
                           + engine_process.launch_failure(returncode))
    seconds = float(found.group(1)) / 1000
    if cli == "parakeet-cli":
        # Parakeet transcribes the sample in a few hundredths of a second on a
        # graphics card, so loading the model (0.2 s there, 0.08 s on the
        # processor) would swamp the comparison. The service loads it once.
        load = _LOAD_TIME.search(stderr)
        if load:
            seconds -= float(load.group(1)) / 1000
    return seconds


def speed_check(model_name, device_index, threads=None, cancel=None):
    """Seconds to transcribe the 11-second sample on the graphics chip and on
    the processor, as (graphics, processor). The graphics run is done twice
    and the second one counted: the first pays one-time shader setup.
    Setting *cancel* (a threading.Event) stops it with a RuntimeError."""
    threads = threads or max(1, (os.cpu_count() or 2) // 2)
    model = model_path(model_name)
    cli = "parakeet-cli" if is_parakeet(model_name) else "whisper-cli"
    gpu_args = ["-dev", str(device_index)]
    _time_one(model, gpu_args, threads, cli, cancel)
    gpu = _time_one(model, gpu_args, threads, cli, cancel)
    cpu = _time_one(model, ["-ng"], threads, cli, cancel)
    logger.info(f"Speed check ({model_name}): graphics {gpu:.2f}s, processor {cpu:.2f}s")
    return gpu, cpu


def graphics_is_worth_it(gpu_seconds, cpu_seconds) -> bool:
    """Use the graphics chip only when it is clearly faster: at least 20%,
    so a near tie doesn't swap the processor for an extra background engine."""
    return gpu_seconds * 1.2 <= cpu_seconds


# --- Talking to the engine -----------------------------------------------------

def wav_bytes(audio_f32, sample_rate=16000):
    """16-bit mono WAV of float32 audio in [-1, 1], in memory."""
    pcm = (np.clip(audio_f32, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def multipart(fields, file_bytes):
    """(body, content type) of a multipart form with *fields* and one file."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                     f'{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                 f'filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
                 + file_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# whisper.cpp writes non-speech as bracketed markers: silence or room noise
# with a language set comes back as the text "[BLANK_AUDIO]", which must not be
# typed. Any all-caps bracketed tag is such a marker, never dictation.
_MARKER = re.compile(r"\[[A-Z][A-Z _]*\]")


def parse_result(reply):
    """The text of a verbose_json reply, with whisper.cpp's non-speech
    markers removed.

    Its segments' no-speech probability is left out: on the same audio it
    reads 0.90 for clear speech with the language set and 0.00 for pure
    silence, so the voice detector's verdict on the recording stands in
    for it (engine._transcribe_vulkan)."""
    segments = reply.get("segments") or []
    text = " ".join((s.get("text") or "").strip() for s in segments).strip()
    if not segments:
        text = (reply.get("text") or "").strip()
    return " ".join(_MARKER.sub(" ", text).split())


def request_fields(language=None, prompt=None, scores=False):
    """The form fields of one /inference request. whisper-server reads
    "prompt" as Whisper's initial prompt for that request alone.

    *scores* asks for every language's probability (language_scores). The
    server works them out with one more pass of the model, about a fifth of
    the time of a short dictation on built-in graphics, so they're only
    asked for when auto-detect is held to the user's languages."""
    fields = {"response_format": "verbose_json", "temperature": "0.0",
              "beam_size": "5", "language": language or "auto",
              "no_language_probabilities": "false" if scores else "true"}
    if prompt:
        fields["prompt"] = prompt
    return fields


def language_scores(reply):
    """{language code: probability} from a reply asked for with scores=True,
    or {} when it has none."""
    scores = reply.get("language_probabilities") or {}
    return {code: float(p) for code, p in scores.items()}


def _kill_orphaned_servers():
    """Kill whisper-servers an earlier run left behind, from any build of
    this version: an orphan of an earlier build keeps its folder's path
    after an update removed the folder."""
    for build in range(1, ENGINE_BUILD + 1):
        server = os.path.join(_engines_root(), _build_folder(build), ENGINE_NAME, "whisper-server")
        engine_process.kill_orphans(engine_process.processes_of(server),
                                    engine_process.is_talktype_process, "graphics engine")


class VulkanWhisperModel:
    """A whisper-server process running *model_name* on graphics chip
    *device_index*, kept running for the life of the dictation service."""

    def __init__(self, model_name, device_index, threads=None):
        """Start whisper-server for *model_name* on graphics card
        *device_index*, warmed up. Raises when it doesn't start."""
        self.model_name = model_name
        self.device_index = device_index
        self.threads = threads or max(1, (os.cpu_count() or 2) // 2)
        self._proc = None
        self._port = None
        self._path = ""
        self._key = ""
        # Re-entrant: a restart inside transcribe() runs a warm-up transcribe().
        self._lock = threading.RLock()
        self._launcher = engine_process.Launcher("vulkan-whisper")
        try:
            self._start()
        except BaseException:
            self._launcher.close()
            raise

    def _start(self):
        """Start whisper-server on a free local port, wait up to two minutes
        for it to answer, and warm it up with a practice run. Raises
        RuntimeError with the reason when it doesn't come up, with the
        server stopped."""
        folder = engine_dir()       # once: the program and its libraries from one build
        server = os.path.join(folder, "whisper-server")
        _kill_orphaned_servers()
        self._port = engine_process.free_port()
        # A random key, so other users' programs, which can read the port and
        # path on the command line, and web pages can't send it audio
        # (vulkan-engine-patches/server-api-key.patch). Like the AI engine's
        # (ai_server.py) it goes in the environment, which only this user
        # can read. Programs running as this user could read it, as they
        # could read anything else of TuxTalk's. The random path before every
        # route is for an earlier build still running until this one has
        # downloaded (update_in_background): it takes no key and has a /load
        # route that loads any model file it is given the path of, and the
        # path keeps web pages, which can post a form to 127.0.0.1 and scan
        # the ports, away from it.
        self._key = secrets.token_hex(16)
        self._path = "/" + secrets.token_hex(16)
        command, env = engine_process.parent_death_command(
            [server, "-m", model_path(self.model_name),
             "--host", "127.0.0.1", "--port", str(self._port),
             "--request-path", self._path,
             "-t", str(self.threads), "-dev", str(self.device_index)],
            {**_engine_env(folder), "WHISPER_API_KEY": self._key})
        self._proc = self._launcher.popen(command, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE, env=env)
        output = engine_process.OutputTail(self._proc.stderr)
        logger.info(f"Graphics engine starting (pid {self._proc.pid}, device {self.device_index})")
        try:
            deadline = time.time() + 120
            while time.time() < deadline:
                if self._proc.poll() is not None:
                    raise RuntimeError("the graphics engine stopped while starting"
                                       + engine_process.launch_failure(self._proc.returncode,
                                                                       output))
                if engine_process.answers(self._port, self._proc.pid, self._path):
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("the graphics engine did not start in time")
            # One short practice run: the first request on a graphics chip pays
            # one-time shader setup, which would otherwise land on the first dictation.
            started = time.time()
            self.transcribe(np.zeros(16000, dtype=np.float32), "en")
        except BaseException:
            # A server that never got ready would otherwise keep the model in
            # graphics memory for the rest of the session.
            self._stop()
            raise
        logger.info(f"Graphics engine ready (warm-up took {time.time() - started:.1f}s)")

    def transcribe(self, audio_f32, language=None, timeout=120, prompt=None, limit_to=()):
        """The text of *audio_f32*. *prompt*
        is Your Words (custom_words.prompt), to steer Whisper's spelling.

        *limit_to* holds auto-detect to those languages (language_limit.py):
        a dictation Whisper hears in another language is run again in the
        likeliest one on the list."""
        wav = wav_bytes(audio_f32)
        limited = bool(limit_to) and not language
        reply = self._inference(request_fields(language, prompt, scores=limited), wav, timeout)
        if limited:
            from . import language_limit
            scores = language_scores(reply)
            heard = max(scores, key=scores.get) if scores else None
            better = language_limit.redirect(heard, scores, limit_to) if scores else None
            if better:
                logger.info(f"Language: Whisper heard {heard}, which isn't on your list; "
                            f"using {better}")
                reply = self._inference(request_fields(better, prompt), wav, timeout)
        return parse_result(reply)

    def _inference(self, fields, wav, timeout):
        """One /inference request; the server's JSON reply."""
        body, content_type = multipart(fields, wav)
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                logger.warning("Graphics engine had stopped; starting it again")
                self._start()
            request = urllib.request.Request(
                f"http://127.0.0.1:{self._port}{self._path}/inference", data=body,
                headers={"Content-Type": content_type,
                         "Authorization": f"Bearer {self._key}"})
            with engine_process.open_local(request, timeout=timeout) as response:
                return json.load(response)

    def _stop(self):
        proc, self._proc = self._proc, None
        engine_process.stop(proc)

    def close(self):
        """Stop the server for good."""
        self._stop()
        self._launcher.close()
