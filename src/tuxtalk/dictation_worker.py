"""The one thread that transcribes and types, in the order the recordings
were made: whole dictations, Type at pause's phrases, voice edits, and the
few keys the hotkey loop would otherwise type itself (Paste Last Dictation,
the space a held send owes).

Off the hotkey loop, so a dictation key pressed while text is still being
typed is read at once and starts the next recording; and off the audio
thread, which only hands a phrase cut at a pause over. One per service
process, so nothing it types can overtake, or mix its keys into, what an
earlier recording is still typing.
"""
import queue
import threading
import time

from .logger import setup_logger

logger = setup_logger(__name__)


class DictationWorker:
    """Runs the jobs submitted to it one at a time, in order, on a thread of
    its own. A job that fails is logged, and the next one runs."""

    def __init__(self):
        self._queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True, name="tt-dictation")
        self.thread.start()

    def submit(self, job) -> None:
        self._queue.put(job)

    def idle(self) -> bool:
        """Whether nothing is running or waiting to."""
        return not self._queue.unfinished_tasks

    def drain(self, timeout=None) -> bool:
        """Wait until every submitted job has finished. False on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._queue.all_tasks_done.wait(remaining)
        return True

    def _run(self):
        while True:
            job = self._queue.get()
            try:
                job()
            except Exception:
                logger.exception("A dictation job failed; carrying on with the next")
            finally:
                self._queue.task_done()


_instance = None
_instance_lock = threading.Lock()


def worker() -> DictationWorker:
    """This service's worker, started on first use (type_at_pause.arm_chunker
    starts it before the audio thread could need it)."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = DictationWorker()
        return _instance


def submit(job) -> None:
    """Run *job* on the worker, after everything submitted before it."""
    worker().submit(job)


def call(job):
    """*job*() on the worker, and what it returns: now when on the worker,
    within what it's doing; from any other thread, after what the worker
    has been given (submit), waiting for it. A job that fails there is
    logged by the worker, and raises JobFailed here."""
    if _instance is not None and threading.current_thread() is _instance.thread:
        return job()
    done, answer = threading.Event(), {}

    def run():
        try:
            answer["value"] = job()
        finally:
            done.set()
    submit(run)
    done.wait()
    if "value" not in answer:
        raise JobFailed("a job on the dictation worker failed (logged there)")
    return answer["value"]


class JobFailed(RuntimeError):
    """A job run with call() raised on the worker."""


def drain(timeout) -> bool:
    """Wait up to *timeout* seconds for everything submitted to finish;
    False when it didn't."""
    return _instance is None or _instance.drain(timeout)


def idle() -> bool:
    """Whether no dictation is being transcribed or typed, or waiting to be."""
    return _instance is None or _instance.idle()
