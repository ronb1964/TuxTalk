

def test_without_the_file_list_a_model_with_one_vocabulary_file_downloads(monkeypatch):
    """Both vocabulary files are asked for by name, and every model has only
    one: the other's "not found" must not fail a download that's whole."""
    import threading
    import huggingface_hub
    from huggingface_hub.utils import EntryNotFoundError
    from tuxtalk import model_helper
    fetched = []

    def no_list(repo):
        raise OSError("rate limited")

    def hf_hub_download(repo_id, filename, tqdm_class=None, force_download=False):
        if filename in ("vocabulary.json", "preprocessor_config.json"):
            raise EntryNotFoundError(f"{filename} not in {repo_id}")
        fetched.append(filename)
        return f"/cache/{filename}"
    monkeypatch.setattr(model_helper, "_files_to_download", no_list)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", hf_hub_download)
    download = model_helper.make_model_download_func("small")
    assert download(lambda text, percent: None, threading.Event()) is True
    assert sorted(fetched) == ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]
