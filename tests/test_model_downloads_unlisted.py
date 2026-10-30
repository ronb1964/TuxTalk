"""Without Hugging Face's file list, a Whisper model's files are asked for by
name, some of which a model hasn't got. Only the Hub saying so lets a file
be missing, and never one the model can't load without."""
import threading

import pytest

from tuxtalk import model_helper


def _not_on_the_hub(filename):
    """The Hub's own answer for a file this model hasn't got."""
    import httpx
    from huggingface_hub.errors import RemoteEntryNotFoundError
    request = httpx.Request("GET", f"https://huggingface.co/x/resolve/main/{filename}")
    return RemoteEntryNotFoundError(f"404: {filename}", response=httpx.Response(404,
                                                                                request=request))


@pytest.fixture
def hub(monkeypatch):
    """A Hub without the file list: {file name: what fetching it raises};
    the files fetched."""
    import huggingface_hub
    raises, fetched = {}, []

    def no_list(repo):
        raise OSError("rate limited")

    def hf_hub_download(repo_id, filename, tqdm_class=None, force_download=False):
        if filename in raises:
            raise raises[filename]
        fetched.append(filename)
        return f"/cache/{filename}"
    monkeypatch.setattr(model_helper, "_files_to_download", no_list)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", hf_hub_download)
    monkeypatch.setattr(model_helper, "_RETRY_WAITS", (0.0, 0.0))
    return raises, fetched


def _download(model):
    return model_helper.make_model_download_func(model)(lambda text, percent: None,
                                                        threading.Event())


def test_without_the_file_list_a_model_with_one_vocabulary_file_downloads(hub):
    """Both vocabulary files are asked for by name, and every model has only
    one: the other's "not found" must not fail a download that's whole."""
    raises, fetched = hub
    for name in ("vocabulary.json", "preprocessor_config.json"):
        raises[name] = _not_on_the_hub(name)
    assert _download("small") is True
    assert sorted(fetched) == ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]


def test_a_dropped_connection_on_the_preprocessor_file_fails_the_download(hub):
    """Taken for "this model hasn't got one", large-v3 was reported
    downloaded and then ran with 80 mel bins, and every dictation failed."""
    from huggingface_hub.errors import LocalEntryNotFoundError
    raises, _fetched = hub
    raises["vocabulary.txt"] = _not_on_the_hub("vocabulary.txt")
    raises["preprocessor_config.json"] = LocalEntryNotFoundError("connection error, not cached")
    assert _download("large-v3") is False


def test_a_model_file_the_hub_says_is_missing_still_fails_the_download(hub):
    raises, _fetched = hub
    raises["preprocessor_config.json"] = _not_on_the_hub("preprocessor_config.json")
    raises["model.bin"] = _not_on_the_hub("model.bin")
    assert _download("small") is False
