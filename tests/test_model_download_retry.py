"""A slow moment must not sink a whole model download (download_utils.with_retries,
shared by the Whisper models' download and Parakeet Ultra's).

Two first runs in the VM tests (2026-10-13) lost Parakeet to one read timeout:
the file wasn't retried, the log still said "All files downloaded", and
setup quietly fell back to Whisper Small.
"""
import types

import pytest

from tuxtalk import download_utils


def test_a_file_that_fails_once_is_fetched_again():
    calls = []

    def fetch():
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("The read operation timed out")
        return "/path"

    assert download_utils.with_retries(fetch, "vocab.txt", sleep=lambda s: None) == "/path"
    assert len(calls) == 2


def test_it_gives_up_after_three_tries_with_the_last_error():
    calls = []

    def fetch():
        calls.append(1)
        raise TimeoutError(f"try {len(calls)}")

    with pytest.raises(TimeoutError, match="try 3"):
        download_utils.with_retries(fetch, "vocab.txt", sleep=lambda s: None)
    assert len(calls) == 3


def test_a_cancel_is_never_retried():
    calls = []

    def fetch():
        calls.append(1)
        raise InterruptedError("Download cancelled")

    with pytest.raises(InterruptedError):
        download_utils.with_retries(fetch, "vocab.txt", sleep=lambda s: None)
    assert len(calls) == 1


def test_waits_grow_between_tries():
    waits = []

    def fetch():
        raise OSError("network")

    with pytest.raises(OSError):
        download_utils.with_retries(fetch, "x", sleep=waits.append)
    assert waits == sorted(waits) and len(waits) == 2 and waits[0] > 0


def test_a_failure_after_cancel_is_a_cancel_not_another_try():
    import threading
    cancel, calls = threading.Event(), []

    def fetch():
        calls.append(1)
        cancel.set()
        raise OSError("connection reset")

    with pytest.raises(InterruptedError):
        download_utils.with_retries(fetch, "x", cancel, sleep=lambda s: pytest.fail("waited"))
    assert len(calls) == 1


def test_a_file_the_server_says_isnt_there_is_not_asked_for_again():
    calls = []

    class NotThere(Exception):
        pass

    def fetch():
        calls.append(1)
        raise NotThere("404")

    with pytest.raises(NotThere):
        download_utils.with_retries(fetch, "x", give_up_on=(NotThere,), sleep=lambda s: None)
    assert len(calls) == 1


def test_parakeet_ultra_retries_a_dropped_connection(monkeypatch, tmp_path):
    """It had a retry loop of its own, with no wait between tries."""
    import threading
    from tuxtalk import parakeet_ultra
    tries, waits = [], []
    monkeypatch.setattr(parakeet_ultra, "folder", lambda: str(tmp_path))
    monkeypatch.setattr(parakeet_ultra, "missing", lambda device: (parakeet_ultra.GPU_FILE,))
    monkeypatch.setattr(download_utils, "time", types.SimpleNamespace(sleep=waits.append))

    def fetch_file(url, path, **kwargs):
        tries.append(url)
        if len(tries) == 1:
            raise download_utils.DownloadError("connection dropped")
    monkeypatch.setattr(download_utils, "fetch_file", fetch_file)
    assert parakeet_ultra.make_download_func("vulkan")(lambda *a: None, threading.Event())
    assert len(tries) == 2 and waits == [download_utils.RETRY_WAITS[0]]
