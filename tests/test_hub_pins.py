"""Models from Hugging Face come from one commit of each repository and are
checked against their SHA256 as they arrive, and a model already downloaded
at that commit is found without downloading it again."""
import threading

import huggingface_hub
import huggingface_hub.constants
import pytest

from tuxtalk import ai_download, hub_pins, model_helper as M, whisper_vulkan

_COMMIT = "f" * 40
_REPO = "org/model"
_LOOK_IN_CACHE = huggingface_hub.hf_hub_download


def test_every_repository_tuxtalk_downloads_from_is_held_to_a_commit():
    """A repository left at "main" takes whatever its owner put there last."""
    repos = {*M.MODEL_REPOS.values(), whisper_vulkan.MODEL_REPO, ai_download.MODEL_REPO}
    for repo in repos:
        commit = hub_pins.revision(repo)
        assert commit and len(commit) == 40 and int(commit, 16) >= 0, repo


def test_every_model_file_tuxtalk_downloads_has_a_checksum():
    weights = {(repo, "model.bin") for repo in M.MODEL_REPOS.values()}
    weights |= {(whisper_vulkan.model_repo(m), name)
                for m, (name, _size) in whisper_vulkan.MODEL_FILES.items()
                if whisper_vulkan.model_repo(m) == whisper_vulkan.MODEL_REPO}
    weights.add((ai_download.MODEL_REPO, ai_download.MODEL_FILE))
    assert weights <= set(hub_pins.SHA256)
    assert all(len(sha) == 64 for sha in hub_pins.SHA256.values())


def _snapshot(cache, commit, filename, content=b"weights"):
    """*filename* in the cache as huggingface_hub keeps it: a blob, and a link
    to it in the folder of the commit it came from."""
    folder = cache / ("models--" + _REPO.replace("/", "--"))
    blob = folder / "blobs" / ("b" * 64)
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(content)
    link = folder / "snapshots" / commit / filename
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(blob)
    return folder, link


@pytest.fixture
def pinned(monkeypatch, tmp_path):
    """An empty Hugging Face cache, and _REPO held to _COMMIT."""
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setitem(hub_pins.REVISIONS, _REPO, _COMMIT)
    return tmp_path


@pytest.mark.parametrize("main", [None, "a" * 40], ids=["no refs/main", "main moved on"])
def test_a_model_downloaded_at_its_commit_is_found_offline(pinned, main):
    """A download held to a commit writes no refs/main, and the repository's
    main may have moved on since: the model is still there, found without
    the network, so nothing is downloaded again."""
    from tuxtalk import download_utils
    folder, link = _snapshot(pinned, _COMMIT, "model.gguf")
    if main:
        (folder / "refs").mkdir()
        (folder / "refs" / "main").write_text(main)
    assert download_utils.cached_hub_file(_REPO, "model.gguf") == str(link)
    assert download_utils.cached_hub_snapshot(_REPO) == str(link.parent)


def test_a_copy_from_another_commit_isnt_taken_for_the_model(pinned):
    from tuxtalk import download_utils
    folder, _link = _snapshot(pinned, "a" * 40, "model.gguf")
    (folder / "refs").mkdir()
    (folder / "refs" / "main").write_text("a" * 40)
    assert download_utils.cached_hub_file(_REPO, "model.gguf") is None


def _fake_hub(cache, content, asked):
    """hf_hub_download that 'downloads' *content*, reporting progress as the
    Hub does, and records the revision it was asked for."""
    def download(repo_id, filename, tqdm_class, force_download, revision):
        asked.append(revision)
        bar = tqdm_class(total=len(content), desc=filename)
        bar.update(len(content))
        return str(_snapshot(cache, revision, filename, content)[1])
    return download


@pytest.mark.parametrize("arrived, ok", [(b"weights", True), (b"tampered", False)])
def test_a_model_file_is_checked_against_its_checksum(pinned, monkeypatch, arrived, ok):
    """huggingface_hub checks only the size. A file that isn't the one its
    commit names is refused and taken out of the cache, so the engine never
    parses it and the next try downloads it again."""
    import hashlib
    monkeypatch.setitem(hub_pins.SHA256, (_REPO, "model.gguf"),
                        hashlib.sha256(b"weights").hexdigest())
    asked, reports = [], []
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _fake_hub(pinned, arrived, asked))
    done = M._fetch_files(_REPO, "test", [("model.gguf", len(arrived))], True,
                          lambda text, percent: reports.append(text), threading.Event())
    assert asked == [_COMMIT]
    assert done is ok
    link = pinned / ("models--" + _REPO.replace("/", "--")) / "snapshots" / _COMMIT / "model.gguf"
    assert link.exists() is ok and (pinned / "models--org--model" / "blobs").exists()
    if not ok:
        assert not list((pinned / "models--org--model" / "blobs").iterdir())
        assert "damaged" in reports[-1]


def _in_cache(monkeypatch):
    """Whether model.gguf counts as downloaded, asked of the real cache."""
    from tuxtalk import download_utils
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _LOOK_IN_CACHE)
    return download_utils.cached_hub_file(_REPO, "model.gguf") is not None


def _cached_hub(cache, content, checked):
    """hf_hub_download that finds the file in the cache (or resumes it with
    every byte already there): no progress reported."""
    def download(repo_id, filename, tqdm_class, force_download, revision):
        checked.append(filename)
        link = cache / "models--org--model" / "snapshots" / revision / filename
        return str(link if link.exists() else _snapshot(cache, revision, filename, content)[1])
    return download


class _CancelAtTheCheck(threading.Event):
    """A cancel event set when the download says it's checking a file."""

    def report(self, text, _percent):
        if text.startswith("Checking"):
            self.set()


def test_a_check_cut_short_leaves_the_file_unused_until_it_is_checked(pinned, monkeypatch):
    """Cancel while a 3 GB file is being checked, or TuxTalk stopping then:
    the file is in the cache but unchecked, so it doesn't count as
    downloaded. The next try checks it without downloading it again, and a
    file already in the cache with no check owed isn't read again."""
    import hashlib
    monkeypatch.setitem(hub_pins.SHA256, (_REPO, "model.gguf"),
                        hashlib.sha256(b"weights").hexdigest())
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _fake_hub(pinned, b"weights", []))
    cancel = _CancelAtTheCheck()
    assert M._fetch_files(_REPO, "test", [("model.gguf", 7)], True, cancel.report,
                          cancel) is False
    assert not _in_cache(monkeypatch)

    fetched = []
    monkeypatch.setattr(huggingface_hub, "hf_hub_download",
                        _cached_hub(pinned, b"weights", fetched))
    assert M._fetch_files(_REPO, "test", [("model.gguf", 7)], True, lambda *a: None,
                          threading.Event()) is True
    assert fetched == ["model.gguf"]
    assert _in_cache(monkeypatch)

    reports = []
    monkeypatch.setattr(huggingface_hub, "hf_hub_download",
                        _cached_hub(pinned, b"weights", fetched))
    assert M._fetch_files(_REPO, "test", [("model.gguf", 7)], True,
                          lambda text, percent: reports.append(text), threading.Event()) is True
    assert not any(text.startswith("Checking") for text in reports)


def test_a_resumed_download_that_brings_no_new_bytes_is_still_checked(pinned, monkeypatch):
    """Cancelled with every byte in: picked up again, Hugging Face finishes
    the file without reporting any progress."""
    import hashlib
    monkeypatch.setitem(hub_pins.SHA256, (_REPO, "model.gguf"),
                        hashlib.sha256(b"weights").hexdigest())

    def cancelled(repo_id, filename, tqdm_class, force_download, revision):
        raise InterruptedError("Download cancelled")
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", cancelled)
    assert M._fetch_files(_REPO, "test", [("model.gguf", 8)], True, lambda *a: None,
                          threading.Event()) is False
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _cached_hub(pinned, b"tampered", []))
    reports = []
    assert M._fetch_files(_REPO, "test", [("model.gguf", 8)], True,
                          lambda text, percent: reports.append(text), threading.Event()) is False
    assert "damaged" in reports[-1]
    assert not _in_cache(monkeypatch)


def test_a_damaged_file_in_a_cache_without_links_is_removed_without_a_warning(
        pinned, monkeypatch, caplog):
    """With HF_HUB_DISABLE_SYMLINKS the snapshot holds the file itself."""
    import logging
    monkeypatch.setitem(hub_pins.SHA256, (_REPO, "model.gguf"), "0" * 64)
    damaged = pinned / "model.gguf"
    damaged.write_bytes(b"tampered")
    with caplog.at_level(logging.WARNING), pytest.raises(M.ChecksumMismatch):
        M._check_download(_REPO, "model.gguf", str(damaged), threading.Event())
    assert not damaged.exists() and "Couldn't remove" not in caplog.text


def test_a_whisper_model_with_its_check_owed_is_not_downloaded(pinned, monkeypatch):
    """What the service checks at start, before it loads a model."""
    from tuxtalk import download_utils
    monkeypatch.setitem(M.MODEL_REPOS, "small", _REPO)
    for name in (*M.ESSENTIAL_MODEL_FILES, "vocabulary.txt"):
        _snapshot(pinned, _COMMIT, name)
    assert M.is_model_cached_fast("small")
    download_utils.set_check_owed(_REPO, "model.bin", True)
    assert not M.is_model_cached_fast("small")


def test_whisper_loads_from_the_commit_it_was_downloaded_at(monkeypatch):
    asked = {}
    monkeypatch.setattr("faster_whisper.WhisperModel",
                        lambda name, **options: asked.update(options, name=name))
    M.load_model("small")
    assert asked["name"] == "small"
    assert asked["revision"] == hub_pins.revision(M.MODEL_REPOS["small"])
