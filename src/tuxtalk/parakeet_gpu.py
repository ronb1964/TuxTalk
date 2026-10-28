"""
Parakeet on the graphics chip, through the Vulkan engine.

Used when the device is "Vulkan (any GPU)" and the model is Parakeet. On an
RTX 4070 Super it transcribed 66 s of speech in 0.17 s, against 2.15 s for the
processor engine (parakeet_engine.py), with no loss of accuracy.

whisper.cpp has no Parakeet server, so the model runs in a helper process of
TuxTalk's own (parakeet_gpu_worker.py) that the dictation service starts and
talks to over pipes. Like the other engines (engine_process.py) it dies with
the service.

If the helper fails twice in a row, transcription moves to the processor
engine for the rest of the session, when that model is downloaded. TuxTalk
keeps both downloads for exactly this.
"""
import json
import os
import select
import struct
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import engine_process
from .logger import setup_logger
from .parakeet_engine import PARAKEET_MODEL, load_parakeet_model

logger = setup_logger(__name__)

SAMPLE_RATE = 16000
START_TIMEOUT = 180          # the very first run on a chip compiles shaders (8 s on an RTX 4070 Super)
MIN_SAMPLES = SAMPLE_RATE    # shorter recordings are padded with silence to one second
REPLY_TIMEOUT = 60           # for one recording, plus a second per second of audio


def _steer(model, words):
    """Hand *model* Your Words if it takes them; never in the way of a dictation."""
    set_words = getattr(model, "set_words", None)
    if set_words is not None:
        try:
            set_words(words)
        except Exception as e:
            logger.warning(f"Couldn't hand Parakeet your words: {e}")


class ParakeetGpuModel:
    """Parakeet running on graphics chip *device_index* in a helper process.

    Offers recognize(), like the processor's ParakeetModel, so the dictation
    code treats the two the same."""

    def __init__(self, device_index, threads=None, command=None):
        from . import whisper_vulkan as wv
        self.device_index = device_index
        threads = threads or max(1, (os.cpu_count() or 2) // 2)
        if command is None:
            model = wv.model_path(PARAKEET_MODEL)
            logger.info(f"Parakeet on the graphics card from {os.path.basename(model)}")
            command = [sys.executable, "-m", "tuxtalk.parakeet_gpu_worker",
                       wv._engine_dir(), model, str(device_index), str(threads)]
        self._command = command
        self._proc = None
        self._stderr = None
        self._unread = b""          # the helper's output past the last full line
        self._cpu = None
        self._words = ()
        self._sent_words = ()       # the list the running helper has (a new one has none)
        self._lock = threading.Lock()
        # The helper dies with the thread that started it (PR_SET_PDEATHSIG
        # is per thread), and a restart can happen on a short-lived dictation
        # thread (the Flatpak build makes one per dictation). So every start
        # goes through this one thread, which lives as long as the model.
        self._launcher = ThreadPoolExecutor(max_workers=1, thread_name_prefix="parakeet-gpu")
        self._start()

    # --- the helper process ---------------------------------------------------

    def _start(self):
        self._stop()
        # The engine's own messages go to a temporary file: kept out of the way,
        # but there to explain a helper that dies while starting.
        self._stderr = tempfile.TemporaryFile()
        self._proc = self._launcher.submit(
            subprocess.Popen, self._command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self._stderr, preexec_fn=engine_process.set_parent_death_signal).result()
        # The pipes are used through their descriptors, never the buffered
        # files, so a reply that stops halfway or a helper that stops reading
        # ends at the deadline instead of blocking the dictation worker.
        os.set_blocking(self._proc.stdin.fileno(), False)
        self._unread = b""
        logger.info(f"Parakeet graphics helper starting (pid {self._proc.pid}, "
                    f"device {self.device_index})")
        try:
            reply = self._read_reply(START_TIMEOUT)
        except BaseException:
            # A helper that never answered would otherwise keep running
            # (and holding graphics memory) for the rest of the session.
            self._stop()
            raise
        if not reply.get("ready"):
            self._stop()
            raise RuntimeError(reply.get("error") or "the helper did not start")
        self._sent_words = ()
        logger.info("Parakeet graphics helper ready")

    def _engine_messages(self):
        """The last lines the engine printed, to explain a failure."""
        try:
            self._stderr.seek(0)
            lines = self._stderr.read().decode(errors="replace").strip().splitlines()
            return " | ".join(lines[-3:])
        except Exception:
            return ""

    def _read_reply(self, timeout):
        """The helper's next JSON reply, waiting at most *timeout* seconds."""
        deadline = time.monotonic() + timeout
        fd = self._proc.stdout.fileno()
        while b"\n" not in self._unread:
            left = max(0.0, deadline - time.monotonic())
            if not select.select([fd], [], [], left)[0]:
                raise RuntimeError(f"the helper did not answer within {timeout:.0f} s")
            chunk = os.read(fd, 65536)
            if not chunk:
                why = self._engine_messages() or "no message"
                raise RuntimeError(f"the helper stopped ({why})")
            self._unread += chunk
        line, _, self._unread = self._unread.partition(b"\n")
        return json.loads(line)

    def _write(self, data, timeout):
        """Send *data* to the helper, giving up after *timeout* seconds."""
        deadline = time.monotonic() + timeout
        fd = self._proc.stdin.fileno()
        view = memoryview(data)
        while view:
            left = max(0.0, deadline - time.monotonic())
            if not select.select([], [fd], [], left)[1]:
                raise RuntimeError(f"the helper took no audio for {timeout:.0f} s")
            try:
                view = view[os.write(fd, view):]
            except BlockingIOError:
                continue

    def _stop(self):
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None

    def _send_words(self):
        """Give the helper Your Words when they changed since it last had them.
        A problem here costs only the steering, never the dictation."""
        if self._sent_words == self._words:
            return
        from .parakeet_gpu_worker import encode_words
        try:
            self._write(encode_words(self._words), 30)
            reply = self._read_reply(30)
        except Exception as e:
            logger.warning(f"Couldn't give Parakeet on the graphics chip your words: {e}")
            raise
        self._sent_words = self._words
        if "error" in reply:
            logger.warning(f"Parakeet on the graphics chip can't take your words: {reply['error']}")
        elif reply.get("words") is None:
            logger.info("This Vulkan engine can't steer Parakeet toward your words; "
                        "only the spelling pass applies")
        else:
            logger.info(f"Your Words for Parakeet on the graphics chip: {reply['words']} spellings "
                        f"of {len(self._words)} words")

    def _recognize_on_gpu(self, audio):
        """Send one recording to the helper (restarting it if it died) and return its text."""
        if self._proc is None or self._proc.poll() is not None:
            logger.warning("Parakeet graphics helper had stopped; starting it again")
            self._start()
        self._send_words()
        timeout = REPLY_TIMEOUT + len(audio) / SAMPLE_RATE
        self._write(struct.pack("<I", len(audio)) + audio.tobytes(), timeout)
        reply = self._read_reply(timeout)
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return reply.get("text", "").strip()

    # --- what the dictation service calls ------------------------------------

    def recognize(self, audio_f32) -> str:
        """Transcribe 16 kHz mono float32 audio and return the text."""
        audio = np.ascontiguousarray(audio_f32, dtype="<f4")
        if len(audio) < MIN_SAMPLES:
            audio = np.pad(audio, (0, MIN_SAMPLES - len(audio)))
        with self._lock:
            if self._cpu is not None:
                return self._cpu.recognize(audio)
            for attempt in (1, 2):
                try:
                    return self._recognize_on_gpu(audio)
                except Exception as e:
                    logger.warning(f"Parakeet on the graphics chip failed (try {attempt}): {e}")
                    self._stop()
            # Twice in a row: something is wrong with the chip or its driver.
            # Use the processor for the rest of this session.
            try:
                self._cpu = load_parakeet_model()
            except Exception as e:
                raise RuntimeError("Parakeet failed on the graphics chip, and its processor "
                                   f"version isn't available ({e})") from e
            logger.warning("Parakeet moved to the processor for the rest of this session")
            _steer(self._cpu, self._words)
            return self._cpu.recognize(audio)

    def set_words(self, words) -> None:
        """Your Words: sent to the helper before the next recording (a
        Vulkan engine with TuxTalk's patch steers Parakeet with them), and
        handed to the processor engine if this has fallen back to it."""
        self._words = tuple(words or ())
        if self._cpu is not None:
            _steer(self._cpu, self._words)

    def close(self):
        """Stop the helper for good."""
        with self._lock:
            self._stop()
        self._launcher.shutdown(wait=False)
