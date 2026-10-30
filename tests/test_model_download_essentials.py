"""A model download must fail on missing weights, not on a missing README.

The downloader walks every file in the HuggingFace repo — including
.gitattributes, README.md and other files faster-whisper never loads — so
treating any single failure as total failure would let a flaky fetch of a text
file throw away a completed multi-gigabyte model download.

What faster-whisper actually needs is already spelled out in this module:
model.bin, config.json, tokenizer.json.
"""

import threading
import types

import pytest

from tuxtalk.model_helper import ESSENTIAL_MODEL_FILES, _download_is_usable

# What a listed repo holds: the essentials and its one vocabulary file.
_LISTED = (*ESSENTIAL_MODEL_FILES, "vocabulary.txt", "README.md", ".gitattributes")


def test_large_v3_without_its_preprocessor_file_is_not_downloaded():
    """It loaded with 80 mel bins where it needs 128, and every dictation failed."""
    listed = (*_LISTED, "preprocessor_config.json")
    assert _download_is_usable(["preprocessor_config.json"], listed) is False


def test_a_model_without_a_preprocessor_file_is_still_downloaded():
    """Asked for by name (no file list), the smaller models haven't got one."""
    assert _download_is_usable(["preprocessor_config.json"], _LISTED,
                               absent=["preprocessor_config.json"]) is True


def test_only_the_preprocessor_file_may_be_missing_from_the_model():
    """Every file the Hub says a model hasn't got dropped out of the
    required list, model.bin too."""
    for name in ESSENTIAL_MODEL_FILES:
        assert _download_is_usable([name], _LISTED, absent=[name]) is False


def test_a_missing_readme_does_not_fail_the_download():
    assert _download_is_usable(["README.md"], _LISTED) is True


def test_a_missing_gitattributes_does_not_fail_the_download():
    assert _download_is_usable([".gitattributes", "README.md"], _LISTED) is True


def test_a_missing_model_binary_fails():
    assert _download_is_usable(["model.bin"], _LISTED) is False


def test_a_missing_config_fails():
    assert _download_is_usable(["config.json"], _LISTED) is False


def test_a_missing_tokenizer_fails():
    assert _download_is_usable(["tokenizer.json"], _LISTED) is False


def test_nothing_failed_is_usable():
    assert _download_is_usable([], _LISTED) is True


def test_an_essential_file_in_a_subdirectory_still_counts():
    """list_repo_tree is recursive, so paths can be nested."""
    assert _download_is_usable(["snapshots/abc/model.bin"], _LISTED) is False


def test_the_essential_list_matches_what_the_cache_check_requires():
    """Both places must agree on what a usable model is."""
    assert set(ESSENTIAL_MODEL_FILES) == {"model.bin", "config.json", "tokenizer.json"}


def test_a_missing_vocabulary_fails():
    """ctranslate2 can't load a Whisper model without it (.txt or .json)."""
    assert _download_is_usable(["vocabulary.txt"], _LISTED) is False
    both = (*ESSENTIAL_MODEL_FILES, "vocabulary.txt", "vocabulary.json")
    assert _download_is_usable(["vocabulary.txt", "vocabulary.json"], both) is False


def test_asked_for_both_vocabularies_by_name_one_is_enough():
    """With no file list both are fetched by name; a model has only one, so
    the other always fails, and the download is still whole."""
    both = (*ESSENTIAL_MODEL_FILES, "vocabulary.txt", "vocabulary.json")
    assert _download_is_usable(["vocabulary.json"], both) is True
    assert _download_is_usable(["vocabulary.txt"], both) is True


def test_a_file_asked_for_by_name_is_never_optional(monkeypatch):
    """The AI model is fetched as one named file, which isn't on the Whisper
    list; a failed download of it must still count as a failure."""
    import huggingface_hub
    from tuxtalk import model_helper as M

    def fails(**kwargs):
        raise OSError("connection reset")
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fails)
    monkeypatch.setattr("tuxtalk.download_utils.RETRY_WAITS", (0, 0))
    monkeypatch.setattr(M, "_files_to_download", lambda repo: [("model.gguf", 10)])
    download = M.make_model_download_func("AI model", repo_id="some/repo",
                                          only_files=["model.gguf"])
    assert download(lambda *a: None, threading.Event()) is False


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
                    threading.Event()) is False
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
    monkeypatch.setattr("tuxtalk.download_utils.RETRY_WAITS", (0, 0))
    monkeypatch.setattr(download_utils, "free_space_bytes", lambda path: 10 ** 12)
    download = M.make_model_download_func("small")
    assert download(lambda *a: None, threading.Event()) is True
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
def hub(monkeypatch, tmp_path):
    """A fake Hugging Face for Whisper Small, its cache in *tmp_path*:
    (files fetched, files cached)."""
    import huggingface_hub
    import huggingface_hub.constants
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
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", str(tmp_path))
    # Each file's current ETag, which its blob is named after.
    monkeypatch.setattr(huggingface_hub, "get_hf_file_metadata", lambda url, **k: (
        types.SimpleNamespace(etag="etag-" + url.rsplit("/", 1)[1])))
    monkeypatch.setattr(download_utils, "free_space_bytes", lambda path: 100 * MB)
    monkeypatch.setattr("tuxtalk.download_utils.RETRY_WAITS", (0.0, 0.0))
    return fetched, cached


def _download(reports, force=False):
    import threading
    from tuxtalk import model_helper as M
    return M.make_model_download_func("small", force=force)(lambda text, percent: reports.append(
        (text, percent)), threading.Event())


def test_files_already_downloaded_need_no_more_disk_space(hub):
    """A download picked up again was refused for the room its finished
    files already take, and setup moved on to a smaller model."""
    fetched, cached = hub
    cached.add("model.bin")                    # 400 MB here already, 100 MB free
    assert _download([]) is True
    assert fetched == ["model.bin", "config.json", "tokenizer.json", "vocabulary.txt"]


def _partial(tmp_path, blob, size, link=False):
    """A cut-off download's part in the fake cache: sparse, so it takes no room."""
    import os
    blobs = tmp_path / "models--Systran--faster-whisper-small" / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    target = blobs / (blob + (".data" if link else ".incomplete"))
    with open(target, "wb") as part:
        os.truncate(part.fileno(), size)
    if link:
        (blobs / f"{blob}.incomplete").symlink_to(target)


def test_a_download_cut_off_partway_goes_on_when_the_rest_fits(hub, tmp_path):
    """350 of model.bin's 400 MB were in Hugging Face's ".incomplete" part,
    which it resumes, but the whole 400 MB was asked for and refused with
    100 MB free."""
    fetched = hub[0]
    _partial(tmp_path, "etag-model.bin", 350 * MB)
    assert _download([]) is True
    assert "model.bin" in fetched


@pytest.mark.parametrize("blob, link", [
    ("etag-of-an-older-model.bin", False),    # another version, or another model's file
    ("etag-config.json", False),              # another file's part: config.json is 2 bytes
    ("etag-model.bin", True),                 # a link, not a part Hugging Face wrote
])
def test_a_part_that_isnt_this_files_own_doesnt_count(hub, tmp_path, blob, link):
    """Any .incomplete in the repo's folder was subtracted, so a stale 350 MB
    part let a 400 MB download start with 100 MB free and fill the disk."""
    from tuxtalk.download_utils import failure_reason
    _partial(tmp_path, blob, 350 * MB, link)
    reports = []
    assert _download(reports) is False
    assert failure_reason(*reports[-1]).startswith("Not enough disk space")


def test_with_room_for_the_whole_download_the_hub_isnt_asked_about_parts(hub, tmp_path,
                                                                        monkeypatch):
    """Any leftover part held a start-up for a lookup per file, plenty of room or not."""
    import huggingface_hub
    from tuxtalk import download_utils
    _partial(tmp_path, "etag-model.bin", 350 * MB)
    monkeypatch.setattr(download_utils, "free_space_bytes", lambda path: 10 * 1024 * MB)
    monkeypatch.setattr(huggingface_hub, "get_hf_file_metadata",
                        lambda url, **k: pytest.fail("asked the Hub"))
    assert _download([]) is True


def test_the_part_lookups_stop_at_a_cancel_and_use_the_hubs_timeout(tmp_path, monkeypatch):
    import threading
    import huggingface_hub
    import huggingface_hub.constants
    from tuxtalk import model_helper as M
    cancel, asked = threading.Event(), []

    def metadata(url, timeout):
        asked.append(timeout)
        cancel.set()                    # Cancel pressed during the first lookup
        return types.SimpleNamespace(etag="etag-" + url.rsplit("/", 1)[1])
    monkeypatch.setattr(huggingface_hub, "get_hf_file_metadata", metadata)
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_ETAG_TIMEOUT", 3)
    _partial(tmp_path, "etag-model.bin", 300 * MB)
    _partial(tmp_path, "etag-tokenizer.json", 1 * MB)
    missing = [("model.bin", 400 * MB), ("tokenizer.json", 2 * MB)]
    assert M._partial_bytes(str(tmp_path), "Systran/faster-whisper-small", missing,
                            cancel) == 300 * MB
    assert asked == [3]


def test_fetching_a_broken_model_again_needs_room_for_a_whole_new_copy(hub):
    """A damaged copy is fetched afresh beside the old one. Counted as
    cached, it passed the check on a nearly full disk and then filled it."""
    from tuxtalk.download_utils import failure_reason
    fetched, cached = hub
    cached.update({"model.bin", "config.json", "tokenizer.json", "vocabulary.txt"})
    reports = []
    assert _download(reports, force=True) is False
    assert failure_reason(*reports[-1]).startswith("Not enough disk space")
    assert fetched == []


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

    def broken(repo, files, force=False, cancel_event=None):
        raise RuntimeError("the cache folder went away")
    monkeypatch.setattr(M, "_disk_space_problem", broken)
    reports = []
    assert _download(reports) is False
    assert failure_reason(*reports[-1]) == "Download failed: the cache folder went away"
