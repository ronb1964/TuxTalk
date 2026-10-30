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
