"""Checking whether a model is downloaded must not load it.

Building a whole WhisperModel on the processor just to answer yes or no, before
the service loads the model again for real, costs an extra second or two and
gigabytes of memory on every start with large-v3, plus ctranslate2's "float16
... converted to float32" warning in the output. The file check answers the
same question, and a cached model that turns out broken is still downloaded
again.
"""

import pytest

from tuxtalk import model_helper as M


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    """A fake Hugging Face snapshot folder that is_model_cached_fast() finds."""
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download",
                        lambda repo_id, local_files_only=True: str(tmp_path))
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (tmp_path / name).write_text("x")
    return tmp_path


def test_whisper_needs_its_vocabulary_file(snapshot):
    """A download cancelled before the vocabulary arrived can't load."""
    assert M.is_model_cached_fast("small") is False


@pytest.mark.parametrize("vocab", ["vocabulary.txt", "vocabulary.json"])
def test_whisper_with_vocabulary_counts_as_downloaded(snapshot, vocab):
    (snapshot / vocab).write_text("x")
    assert M.is_model_cached_fast("small") is True


def test_a_cached_model_is_loaded_once(monkeypatch):
    loads = []
    monkeypatch.setattr(M, "is_model_cached_fast", lambda name: True)
    monkeypatch.setattr(M, "load_model", lambda *a, **k: loads.append(a) or "MODEL")
    monkeypatch.setattr(M, "_loads_on_cpu", lambda name: pytest.fail("no second load"))
    assert M.download_model_with_progress("large-v3", device="cuda",
                                          compute_type="float16") == "MODEL"
    assert len(loads) == 1


def test_a_broken_cached_model_is_downloaded_again(monkeypatch):
    """Files present but unloadable (say a truncated model.bin): fetch again."""
    monkeypatch.setattr(M, "is_model_cached_fast", lambda name: True)

    def broken(*a, **k):
        raise RuntimeError("Unable to open file 'model.bin'")
    monkeypatch.setattr(M, "load_model", broken)
    monkeypatch.setattr(M, "_loads_on_cpu", lambda name: False)

    reached_download = []

    class StopHere(Exception):
        pass

    def fake_dialog(*a, **k):
        reached_download.append(True)
        raise StopHere
    monkeypatch.setattr(M.Gtk, "Dialog", fake_dialog)

    with pytest.raises(StopHere):
        M.download_model_with_progress("small", show_confirmation=False)
    assert reached_download, "a broken cached model should go to the download"


def test_a_device_problem_is_not_mistaken_for_a_broken_download(monkeypatch):
    """The files load on the processor, so the failure is the graphics side
    (CUDA libraries, driver). Re-downloading would change nothing."""
    monkeypatch.setattr(M, "is_model_cached_fast", lambda name: True)

    def cuda_fails(*a, **k):
        raise RuntimeError("CUDA failed with error unknown error")
    monkeypatch.setattr(M, "load_model", cuda_fails)
    monkeypatch.setattr(M, "_loads_on_cpu", lambda name: True)
    with pytest.raises(RuntimeError, match="CUDA"):
        M.download_model_with_progress("small", device="cuda", compute_type="float16")


def test_models_never_downloaded_are_reported_missing_not_raised():
    """The test home's Hugging Face cache is empty: every lookup that asks
    it only for local files says "not downloaded" instead of failing."""
    from tuxtalk import ai_cleanup, parakeet_engine, parakeet_ultra, whisper_vulkan
    assert M.is_model_cached_fast("small") is False
    assert ai_cleanup._model_path() is None
    assert whisper_vulkan.model_path("small") is None
    if parakeet_ultra.cpu_dir() is None:
        assert parakeet_engine.cached_model_dir() is None


def test_a_damaged_download_record_counts_as_not_downloaded_and_is_logged(
        monkeypatch, tmp_path, caplog):
    """Hugging Face's record of which snapshot is current (refs/main) can
    be left as garbage by a crash: each presence check says "not
    downloaded", names the damage in the log, and nothing raises."""
    import logging

    import huggingface_hub.constants
    from tuxtalk import ai_cleanup, parakeet_engine, parakeet_ultra, whisper_vulkan
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", str(tmp_path))
    repos = {ai_cleanup.MODEL_REPO, whisper_vulkan.model_repo("small"),
             parakeet_engine.V3_REPO, M.MODEL_REPOS["small"]}
    for repo in repos:
        folder = tmp_path / ("models--" + repo.replace("/", "--"))
        (folder / "refs").mkdir(parents=True)
        (folder / "snapshots").mkdir()
        (folder / "refs" / "main").write_bytes(b"\xff\xfe garbage")
    with caplog.at_level(logging.WARNING):
        assert M.is_model_cached_fast("small") is False
        assert ai_cleanup._model_path() is None
        assert whisper_vulkan.model_path("small") is None
        if parakeet_ultra.cpu_dir() is None:
            assert parakeet_engine.cached_model_dir() is None
    assert "damaged" in caplog.text


def test_a_damaged_download_record_is_set_aside_so_the_download_mends_it(
        monkeypatch, tmp_path, caplog):
    """huggingface_hub reads refs/main before it replaces it, and so does the
    disk-space check: the damaged record is set aside (kept, renamed) when a
    download starts, the download goes through, and the model then counts
    as downloaded."""
    import logging
    import threading

    import huggingface_hub
    import huggingface_hub.constants
    from tuxtalk import download_utils
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setattr(download_utils, "free_space_bytes", lambda path: 10 ** 12)
    repo = M.MODEL_REPOS["small"]
    folder = tmp_path / ("models--" + repo.replace("/", "--"))
    (folder / "refs").mkdir(parents=True)
    (folder / "refs" / "main").write_bytes(b"\xff\xfe garbage")
    names = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
    monkeypatch.setattr(M, "_listed_files", lambda repo, model, only: [(n, 1) for n in names])
    commit = "a" * 40

    def hub_download(repo_id, filename, **kwargs):
        """Writes into the cache as the Hub does, after reading the old ref."""
        ref = folder / "refs" / "main"
        if not ref.exists() or ref.read_text() != commit:     # file_download.py does this
            ref.write_text(commit)
        snapshot = folder / "snapshots" / commit
        snapshot.mkdir(parents=True, exist_ok=True)
        (snapshot / filename).write_text("x")
        return str(snapshot / filename)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", hub_download)

    with caplog.at_level(logging.WARNING):
        done = M.make_model_download_func("small")(lambda text, percent: None,
                                                   threading.Event())
    assert done is True
    assert M.is_model_cached_fast("small") is True
    assert list((folder / "refs").glob("main.damaged-*")), "the damaged record wasn't kept"
    assert "Set aside a damaged" in caplog.text


def test_damage_that_comes_back_after_a_mended_download_is_logged_again(
        monkeypatch, tmp_path, caplog):
    import logging

    import huggingface_hub
    from tuxtalk import download_utils
    answers = iter([ValueError("Invalid pointer path"), str(tmp_path / "f"),
                    ValueError("Invalid pointer path")])

    def lookup(repo, filename, local_files_only):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", lookup)
    monkeypatch.setattr(download_utils, "_damaged", download_utils.FailureNotes())
    with caplog.at_level(logging.WARNING, logger=download_utils.logger.name):
        for _ in range(3):
            download_utils.cached_hub_file("org/again", "model.gguf")
    assert sum("damaged" in r.getMessage() for r in caplog.records) == 2
