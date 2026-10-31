"""Parakeet Ultra: every Parakeet download fetches it from TuxTalk's own
release, each file checked against its SHA256, and someone with the original
v3 from an earlier TuxTalk keeps dictating on it until they take the update."""
import os

import pytest

from tuxtalk import parakeet_ultra as pu
from tuxtalk.parakeet_engine import PARAKEET_MODEL


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("tuxtalk.config.get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr("tuxtalk.download_utils.free_space_bytes", lambda path: 10 ** 12)
    return tmp_path


@pytest.fixture
def fetched(data_dir, monkeypatch):
    """A fake fetch_file: records each call and writes the file."""
    calls = []

    def fetch_file(url, dest, timeout, cancel_event, progress_hook, expected_sha256):
        calls.append((url, os.path.basename(dest), expected_sha256))
        progress_hook(10, 10)
        os.makedirs(os.path.dirname(dest), exist_ok=True)      # as fetch_file does
        with open(dest, "w") as f:
            f.write("x")

    monkeypatch.setattr("tuxtalk.download_utils.fetch_file", fetch_file)
    return calls


def _no_original_v3(monkeypatch):
    def not_cached(*_a, **_k):
        raise FileNotFoundError
    monkeypatch.setattr("huggingface_hub.hf_hub_download", not_cached)


def test_the_processor_download_fetches_ultra_from_the_release_checked(fetched):
    assert pu.make_download_func("cpu")(lambda *a: None, None) is True
    assert {name for _url, name, _sha in fetched} == set(pu.CPU_FILES)
    for url, name, sha in fetched:
        assert url == pu.RELEASE_URL + name and sha == pu.FILES[name][1]
    assert url.startswith("https://github.com/ronb1964/TuxTalk/releases/download/")


def test_the_graphics_card_download_fetches_only_its_file(fetched):
    assert pu.make_download_func("vulkan")(lambda *a: None, None) is True
    assert [name for _url, name, _sha in fetched] == [pu.GPU_FILE]


def test_a_second_download_fetches_only_what_is_missing(fetched, data_dir):
    os.makedirs(pu.folder())
    open(os.path.join(pu.folder(), "vocab.txt"), "w").close()
    pu.make_download_func("cpu")(lambda *a: None, None)
    assert "vocab.txt" not in {name for _url, name, _sha in fetched}


def test_every_parakeet_download_route_fetches_ultra(fetched):
    """First run, Preferences and the startup check download the processor
    copy through model_helper; the graphics setup through whisper_vulkan."""
    from tuxtalk import model_helper, whisper_vulkan
    model_helper.make_model_download_func(PARAKEET_MODEL)(lambda *a: None, None)
    whisper_vulkan.make_model_download_func(PARAKEET_MODEL)(lambda *a: None, None)
    assert {name for _url, name, _sha in fetched} == set(pu.FILES)


def _failing_fetch(monkeypatch, reason, tries):
    from tuxtalk.download_utils import DownloadError

    def fetch_file(url, *a, **k):
        tries.append(url)
        raise DownloadError(reason)
    monkeypatch.setattr("tuxtalk.download_utils.fetch_file", fetch_file)
    monkeypatch.setattr("tuxtalk.download_utils.RETRY_WAITS", (0.0, 0.0))


def test_a_failed_file_is_tried_three_times_then_reported_with_its_reason(
        data_dir, monkeypatch):
    """The download window said only "Download failed", for the default model."""
    from tuxtalk.download_utils import failure_reason
    tries, reports = [], []
    _failing_fetch(monkeypatch, "Name or service not known", tries)
    assert pu.make_download_func("vulkan")(lambda *r: reports.append(r), None) is False
    assert len(tries) == 3
    assert failure_reason(*reports[-1]) == "Download failed: Name or service not known"
    assert pu.gpu_file() is None


def test_a_cancel_is_not_retried(data_dir, monkeypatch):
    import threading
    cancel = threading.Event()
    cancel.set()
    tries, reports = [], []
    _failing_fetch(monkeypatch, "timed out", tries)
    assert pu.make_download_func("vulkan")(lambda *r: reports.append(r), cancel) is False
    assert len(tries) == 1 and reports == []


def test_a_full_disk_downloads_nothing(data_dir, fetched, monkeypatch):
    monkeypatch.setattr("tuxtalk.download_utils.free_space_bytes", lambda path: 10 ** 6)
    messages = []
    assert pu.make_download_func("cpu")(lambda text, pct: messages.append(text), None) is False
    assert fetched == [] and "disk space" in messages[-1]


def test_dictation_uses_ultra_once_downloaded(fetched, monkeypatch):
    from tuxtalk import parakeet_engine, whisper_vulkan
    _no_original_v3(monkeypatch)
    assert parakeet_engine.cached_model_dir() is None
    assert whisper_vulkan.model_path(PARAKEET_MODEL) is None
    pu.make_download_func("cpu")(lambda *a: None, None)
    pu.make_download_func("vulkan")(lambda *a: None, None)
    assert parakeet_engine.cached_model_dir() == pu.folder()
    assert whisper_vulkan.model_path(PARAKEET_MODEL) == os.path.join(pu.folder(), pu.GPU_FILE)


def test_the_original_v3_keeps_working_until_the_update(data_dir, tmp_path, monkeypatch):
    from tuxtalk import parakeet_engine, whisper_vulkan
    old = tmp_path / "hf"
    old.mkdir()
    monkeypatch.setattr("huggingface_hub.hf_hub_download",
                        lambda repo, name, local_files_only, **k: str(old / name))
    assert parakeet_engine.cached_model_dir() == str(old)
    assert whisper_vulkan.model_path(PARAKEET_MODEL) == str(old / whisper_vulkan.MODEL_FILES[PARAKEET_MODEL][0])
