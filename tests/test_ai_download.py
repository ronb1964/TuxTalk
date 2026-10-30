"""The local AI's download (ai_download): the engine checked against its pin,
a failure that says why, and an update that doesn't leave the old version."""
import io
import os
import types

from tuxtalk import ai_download as ai


def test_the_engine_download_is_checked_against_its_pinned_sha256(tmp_path, monkeypatch):
    """The pin only protects if the downloader gets it, and an engine that
    fails the check is never unpacked."""
    from tuxtalk import download_utils
    monkeypatch.setattr("tuxtalk.config.get_data_dir", lambda: str(tmp_path))
    checked, unpacked = [], []

    def tampered(url, dest, expected_sha256=None, **kwargs):
        checked.append((url, expected_sha256))
        raise download_utils.DownloadError("the file didn't match its checksum")
    monkeypatch.setattr(download_utils, "fetch_file", tampered)
    monkeypatch.setattr(download_utils, "unpack_tar", lambda *a: unpacked.append(a))
    assert ai.make_engine_download_func()(lambda *r: None, None) is False
    assert len(ai.LLAMA_SHA256) == 64 and ai.LLAMA_TAG in ai.LLAMA_URL
    assert checked == [(ai.LLAMA_URL, ai.LLAMA_SHA256)]
    assert unpacked == [] and not os.path.exists(ai.server_binary())


# --- an engine update doesn't leave the old version behind -----------------------------------

def test_unpacking_a_new_engine_removes_older_versions(tmp_path, monkeypatch):
    """Each version unpacks into its own folder, so the old ones must be removed."""
    import tarfile
    monkeypatch.setattr("tuxtalk.config.get_data_dir", lambda: str(tmp_path))
    old = tmp_path / "ai-engine" / "b100" / "llama-b100"
    old.mkdir(parents=True)
    (old / "llama-server").write_text("old")

    def download_file(url, dest, **kw):
        with tarfile.open(dest, "w:gz") as tar:
            data = b"#!/bin/sh\n"
            info = tarfile.TarInfo(f"llama-{ai.LLAMA_TAG}/llama-server")
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
        return True
    monkeypatch.setattr("tuxtalk.download_utils.download_file", download_file)
    assert ai.make_engine_download_func()(lambda *a: None, None) is True
    assert sorted(p.name for p in (tmp_path / "ai-engine").iterdir()) == [ai.LLAMA_TAG]
    assert os.access(ai.server_binary(), os.X_OK)


def test_a_failed_engine_download_says_why(tmp_path, monkeypatch):
    """Its window said only "Download failed"."""
    from tuxtalk import download_utils
    monkeypatch.setattr("tuxtalk.config.get_data_dir", lambda: str(tmp_path))

    def offline(url, dest, **kwargs):
        raise download_utils.DownloadError("HTTP 503 Service Unavailable")
    monkeypatch.setattr(download_utils, "fetch_file", offline)
    reports = []
    assert ai.make_engine_download_func()(lambda *r: reports.append(r), None) is False
    assert download_utils.failure_reason(*reports[-1]) == (
        "Download failed: HTTP 503 Service Unavailable")


def test_only_folders_other_than_the_current_version_go(tmp_path):
    from tuxtalk import engine_process
    for name in ("1.9.3", "1.9.4"):
        (tmp_path / name).mkdir()
    (tmp_path / "notes.txt").write_text("kept")
    engine_process.remove_other_versions(str(tmp_path), "1.9.4")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["1.9.4", "notes.txt"]
    engine_process.remove_other_versions(str(tmp_path / "missing"), "1.9.4")    # never raises


def test_an_old_engine_version_that_couldnt_be_removed_isnt_logged_as_removed(
        tmp_path, monkeypatch):
    from tuxtalk import engine_process
    (tmp_path / "1.9.3").mkdir()
    (tmp_path / "1.9.4").mkdir()
    said = []
    monkeypatch.setattr(engine_process, "logger", types.SimpleNamespace(
        info=lambda m: said.append(("info", m)), warning=lambda m: said.append(("warning", m))))
    monkeypatch.setattr(engine_process.shutil, "rmtree", lambda path, ignore_errors: None)
    engine_process.remove_other_versions(str(tmp_path), "1.9.4")
    assert said == [("warning", f"Couldn't remove an older engine version: {tmp_path / '1.9.3'}")]
