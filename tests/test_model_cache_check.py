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
    from tuxtalk import ai_download, parakeet_engine, parakeet_ultra, whisper_vulkan
    assert M.is_model_cached_fast("small") is False
    assert ai_download.model_path() is None
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
    from tuxtalk import ai_download, parakeet_engine, parakeet_ultra, whisper_vulkan
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", str(tmp_path))
    repos = {ai_download.MODEL_REPO, whisper_vulkan.model_repo("small"),
             parakeet_engine.V3_REPO, M.MODEL_REPOS["small"]}
    for repo in repos:
        folder = tmp_path / ("models--" + repo.replace("/", "--"))
        (folder / "refs").mkdir(parents=True)
        (folder / "snapshots").mkdir()
        (folder / "refs" / "main").write_bytes(b"\xff\xfe garbage")
    with caplog.at_level(logging.WARNING):
        assert M.is_model_cached_fast("small") is False
        assert ai_download.model_path() is None
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
    assert list((folder / "damaged-refs").glob("main-*")), "the damaged record wasn't kept"
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


@pytest.fixture
def hub_cache(monkeypatch, tmp_path):
    """An empty Hugging Face cache; .ref(text) writes refs/main of org/model."""
    import types

    import huggingface_hub.constants
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", str(tmp_path))
    folder = tmp_path / "models--org--model"
    (folder / "refs" / "pr").mkdir(parents=True)
    (folder / "snapshots").mkdir()

    def ref(data, name="main"):
        (folder / "refs" / name).write_bytes(data)
    return types.SimpleNamespace(folder=folder, ref=ref, aside=folder / "damaged-refs")


def test_only_the_damaged_record_asked_for_is_set_aside(hub_cache):
    """A pull request's record, damaged too, is no business of a download
    of main; a record that reads fine is never touched."""
    from tuxtalk import download_utils
    hub_cache.ref(b"\xff\xfe garbage")
    hub_cache.ref(b"\xff\xfe", name="pr/1")
    download_utils.mend_damaged_ref("org/model")
    assert not (hub_cache.folder / "refs" / "main").exists()
    assert (hub_cache.folder / "refs" / "pr" / "1").read_bytes() == b"\xff\xfe"
    hub_cache.ref(b"A" * 40)                    # upper-case hex is a commit too
    download_utils.mend_damaged_ref("org/model")
    assert (hub_cache.folder / "refs" / "main").read_bytes() == b"A" * 40


def test_a_record_rewritten_since_it_failed_is_left_alone(hub_cache, monkeypatch):
    """Another TuxTalk downloading at the same time writes a fresh record
    between huggingface_hub's failed read and the set-aside: it is kept."""
    import huggingface_hub
    from tuxtalk import download_utils

    def read_fails_then_another_download_writes(repo, filename):
        hub_cache.ref(b"0123456789ABCDEF" * 2 + b"01234567")
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache",
                        read_fails_then_another_download_writes)
    download_utils.mend_damaged_ref("org/model")
    assert (hub_cache.folder / "refs" / "main").exists()
    assert not hub_cache.aside.exists()


def test_a_record_that_is_a_link_is_not_renamed_through(hub_cache, tmp_path):
    from tuxtalk import download_utils
    target = tmp_path / "elsewhere"
    target.write_bytes(b"\xff\xfe garbage")
    (hub_cache.folder / "refs" / "main").symlink_to(target)
    download_utils.mend_damaged_ref("org/model")
    assert (hub_cache.folder / "refs" / "main").is_symlink()
    assert target.read_bytes() == b"\xff\xfe garbage"


def test_set_aside_records_each_keep_their_own_copy_and_only_the_last_few(hub_cache):
    """Several in one second don't overwrite each other, and they don't pile up."""
    from tuxtalk import download_utils
    for n in range(5):
        hub_cache.ref(b"\xff garbage %d" % n)
        download_utils.mend_damaged_ref("org/model")
    kept = sorted(p.read_bytes() for p in hub_cache.aside.iterdir())
    assert kept == [b"\xff garbage 2", b"\xff garbage 3", b"\xff garbage 4"]


def test_the_copies_kept_are_the_last_set_aside_whatever_their_dates(hub_cache):
    """Pruning goes by when each record was set aside, not by the damaged
    file's own date, and the tenth copy sorts after the second."""
    import os
    from tuxtalk import download_utils
    ref = hub_cache.folder / "refs" / "main"
    for n in range(12):
        hub_cache.ref(b"\xff garbage %d" % n)
        os.utime(ref, ns=(10 ** 18 - n, 10 ** 18 - n))     # each one dated earlier
        download_utils.mend_damaged_ref("org/model")
    kept = sorted(p.read_bytes() for p in hub_cache.aside.iterdir())
    assert kept == [b"\xff garbage 10", b"\xff garbage 11", b"\xff garbage 9"]


def test_a_linked_damaged_refs_folder_is_not_used(hub_cache, tmp_path):
    """The copies' folder made a link elsewhere: nothing is moved or pruned
    through it, and the record stays where it is."""
    from tuxtalk import download_utils
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    for n in range(4):
        (elsewhere / f"main-{n}-1-{n}").write_text("someone's file")
    hub_cache.aside.symlink_to(elsewhere)
    hub_cache.ref(b"\xff garbage")
    download_utils.mend_damaged_ref("org/model")
    assert (hub_cache.folder / "refs" / "main").exists()
    assert len(list(elsewhere.iterdir())) == 4
