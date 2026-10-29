"""A model download must fail on missing weights, not on a missing README.

The downloader walks every file in the HuggingFace repo — including
.gitattributes, README.md and other files faster-whisper never loads — so
treating any single failure as total failure would let a flaky fetch of a text
file throw away a completed multi-gigabyte model download.

What faster-whisper actually needs is already spelled out in this module:
model.bin, config.json, tokenizer.json.
"""

import pytest

from tuxtalk.model_helper import ESSENTIAL_MODEL_FILES, _download_is_usable


def test_a_missing_readme_does_not_fail_the_download():
    assert _download_is_usable(["README.md"]) is True


def test_a_missing_gitattributes_does_not_fail_the_download():
    assert _download_is_usable([".gitattributes", "README.md"]) is True


def test_a_missing_model_binary_fails():
    assert _download_is_usable(["model.bin"]) is False


def test_a_missing_config_fails():
    assert _download_is_usable(["config.json"]) is False


def test_a_missing_tokenizer_fails():
    assert _download_is_usable(["tokenizer.json"]) is False


def test_nothing_failed_is_usable():
    assert _download_is_usable([]) is True


def test_an_essential_file_in_a_subdirectory_still_counts():
    """list_repo_tree is recursive, so paths can be nested."""
    assert _download_is_usable(["snapshots/abc/model.bin"]) is False


def test_the_essential_list_matches_what_the_cache_check_requires():
    """Both places must agree on what a usable model is."""
    assert set(ESSENTIAL_MODEL_FILES) == {"model.bin", "config.json", "tokenizer.json"}


def test_a_missing_vocabulary_fails():
    """ctranslate2 can't load a Whisper model without it (.txt or .json)."""
    assert _download_is_usable(["vocabulary.json"]) is False
    assert _download_is_usable(["vocabulary.txt"]) is False


def test_a_file_asked_for_by_name_is_never_optional(monkeypatch):
    """The AI model is fetched as one named file, which isn't on the Whisper
    list; a failed download of it must still count as a failure."""
    import huggingface_hub
    from tuxtalk import model_helper as M

    def fails(**kwargs):
        raise OSError("connection reset")
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fails)
    monkeypatch.setattr(M, "_RETRY_WAITS", (0, 0))
    monkeypatch.setattr(M, "_files_to_download", lambda repo: [("model.gguf", 10)])
    download = M.make_model_download_func("AI model", repo_id="some/repo",
                                          only_files=["model.gguf"])
    assert download(lambda *a: None, type("E", (), {"is_set": lambda self: False})()) is False


def test_the_disk_space_check_looks_where_the_files_go(monkeypatch):
    """HF_HOME / XDG_CACHE_HOME (the Flatpak sets the latter) move the cache;
    measuring ~/.cache/huggingface would check the wrong disk."""
    import huggingface_hub.constants
    from tuxtalk import download_utils
    from tuxtalk import model_helper as M

    asked = []
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", "/elsewhere/hub")
    monkeypatch.setattr(download_utils, "free_space_bytes",
                        lambda path: asked.append(path) or 1)
    monkeypatch.setattr(M, "_files_to_download", lambda repo: [("model.bin", 10 ** 9)])
    messages = []
    download = M.make_model_download_func("small")
    assert download(lambda msg, pct: messages.append(msg),
                    type("E", (), {"is_set": lambda self: False})()) is False
    assert asked == ["/elsewhere/hub"]
    assert "Not enough disk space" in messages[-1]


def test_one_dropped_connection_doesnt_sink_the_download(monkeypatch):
    """Preferences and the first-run window fetch through this function, and
    one read timeout in a 3 GB Large-v3 failed the whole download."""
    import huggingface_hub
    from tuxtalk import download_utils
    from tuxtalk import model_helper as M
    tries = []

    def flaky(**kwargs):
        tries.append(kwargs["filename"])
        if len(tries) == 1:
            raise OSError("Read timed out")
        return "/cache/model.bin"
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", flaky)
    monkeypatch.setattr(M, "_files_to_download", lambda repo: [("model.bin", 10)])
    monkeypatch.setattr(M, "_RETRY_WAITS", (0, 0))
    monkeypatch.setattr(download_utils, "free_space_bytes", lambda path: 10 ** 12)
    download = M.make_model_download_func("small")
    assert download(lambda *a: None, type("E", (), {"is_set": lambda self: False})()) is True
    assert tries == ["model.bin", "model.bin"]


def test_a_model_that_looked_downloaded_but_wont_load_is_fetched_afresh(monkeypatch):
    """Damaged files in the cache: the download "again" found them cached,
    fetched nothing, and the model failed to load again."""
    import huggingface_hub
    from tuxtalk import model_helper as M
    fetched, loads = [], []

    def load(name, device="cpu", compute_type="int8"):
        loads.append(name)
        if len(loads) == 1:
            raise RuntimeError("Unable to open file 'model.bin' in model")
        return "model"
    monkeypatch.setattr(M, "is_model_cached_fast", lambda name: True)
    monkeypatch.setattr(M, "_loads_on_cpu", lambda name: False)
    monkeypatch.setattr(M, "load_model", load)
    monkeypatch.setattr(M, "_files_to_download", lambda repo: [("model.bin", 10)])
    monkeypatch.setattr(huggingface_hub, "hf_hub_download",
                        lambda **kwargs: fetched.append(kwargs) or "/cache/model.bin")
    assert M.download_model_with_progress("small", show_confirmation=False) == "model"
    assert fetched and all(kwargs.get("force_download") for kwargs in fetched)


MB = 1024 * 1024


@pytest.fixture
def hub(monkeypatch):
    """A fake Hugging Face for Whisper Small: (files fetched, files cached)."""
    import huggingface_hub
    from tuxtalk import download_utils
    from tuxtalk import model_helper as M
    fetched, cached = [], set()
    monkeypatch.setattr(M, "_files_to_download",
                        lambda repo: [("model.bin", 400 * MB), ("config.json", 2),
                                      ("tokenizer.json", 2), ("vocabulary.txt", 1)])
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache",
                        lambda repo, name: f"/cache/{name}" if name in cached else None)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download",
                        lambda repo_id, filename, **k: fetched.append(filename))
    monkeypatch.setattr(download_utils, "free_space_bytes", lambda path: 100 * MB)
    monkeypatch.setattr(M, "_RETRY_WAITS", (0.0, 0.0))
    return fetched, cached


def _download(reports):
    import threading
    from tuxtalk import model_helper as M
    return M.make_model_download_func("small")(lambda text, percent: reports.append(
        (text, percent)), threading.Event())


def test_files_already_downloaded_need_no_more_disk_space(hub):
    """A download picked up again was refused for the room its finished
    files already take, and setup moved on to a smaller model."""
    fetched, cached = hub
    cached.add("model.bin")                    # 400 MB here already, 100 MB free
    assert _download([]) is True
    assert fetched == ["model.bin", "config.json", "tokenizer.json", "vocabulary.txt"]


def test_too_little_room_for_the_files_still_to_come_is_said(hub):
    from tuxtalk.download_utils import failure_reason
    reports = []
    assert _download(reports) is False
    assert failure_reason(*reports[-1]).startswith("Not enough disk space")


def test_a_failed_download_says_why_in_its_window(hub, monkeypatch):
    """Reported at 100 %, the reason never reached the window, which said
    only "Failed"."""
    import huggingface_hub
    from tuxtalk.download_utils import failure_reason
    cached = hub[1]
    cached.add("model.bin")

    def refused(repo_id, filename, **k):
        raise OSError("Connection refused\nand more")
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", refused)
    reports = []
    assert _download(reports) is False
    assert failure_reason(*reports[-1]) == "Download failed: Connection refused (4 file(s))"


def test_an_unexpected_error_says_why_in_its_window(hub, monkeypatch):
    from tuxtalk import model_helper as M
    from tuxtalk.download_utils import failure_reason

    def broken(repo, files):
        raise RuntimeError("the cache folder went away")
    monkeypatch.setattr(M, "_disk_space_problem", broken)
    reports = []
    assert _download(reports) is False
    assert failure_reason(*reports[-1]) == "Download failed: the cache folder went away"
