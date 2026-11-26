"""TuxTalk's AI moved to a bigger model in 0.26 (ai_download.MODEL_REPO): the
1.5B one before it changed what about 8 in 100 tone rewrites meant. Someone
updating with the old one downloaded keeps a working AI until they fetch the
new one, is asked once at start-up, and loses the old 1.1 GB once the new one
is here."""
import pytest

from tuxtalk import ai_download, download_utils

NEW = (ai_download.MODEL_REPO, ai_download.MODEL_FILE)
OLD = (ai_download.OLD_MODEL_REPO, ai_download.OLD_MODEL_FILE)


@pytest.fixture
def cache(monkeypatch):
    """The download cache, as the set of (repo, file) it holds; removed repos go."""
    held = set()
    monkeypatch.setattr(download_utils, "cached_hub_file",
                        lambda repo, name: f"/cache/{name}" if (repo, name) in held else None)
    monkeypatch.setattr(download_utils, "cached_repo_files",
                        lambda repo: sorted(name for r, name in held if r == repo))
    monkeypatch.setattr(download_utils, "remove_cached_repo",
                        lambda repo: held.difference_update({k for k in held if k[0] == repo})
                        or True)
    return held


def test_the_new_model_runs_when_it_is_downloaded(cache):
    cache.update({NEW, OLD})
    assert ai_download.model_path() == f"/cache/{ai_download.MODEL_FILE}"
    assert not ai_download.better_model_waiting()


def test_after_an_update_the_old_model_keeps_the_ai_working(cache):
    cache.add(OLD)
    assert ai_download.model_path() == f"/cache/{ai_download.OLD_MODEL_FILE}"
    assert ai_download.better_model_waiting()


def test_with_neither_the_ai_isnt_installed(cache):
    assert ai_download.model_path() is None
    assert not ai_download.better_model_waiting()


def test_the_old_model_is_deleted_only_once_the_new_one_is_here(cache):
    cache.add(OLD)
    ai_download.remove_old_model()
    assert cache == {OLD}
    cache.add(NEW)
    ai_download.remove_old_model()
    assert cache == {NEW}


def test_another_apps_files_in_the_old_models_folder_are_left_alone(tmp_path, monkeypatch):
    """The download cache is shared: a folder holding another file of the
    same repository (another app's) isn't deleted with TuxTalk's (release
    review, Codex). With only TuxTalk's file in it, it goes."""
    from huggingface_hub import constants
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setattr(download_utils, "cached_hub_file", lambda repo, name: "/cache/" + name)
    folder = tmp_path / "models--Qwen--Qwen2.5-1.5B-Instruct-GGUF"
    snapshot = folder / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / ai_download.OLD_MODEL_FILE).write_text("ours")
    (snapshot / "qwen2.5-1.5b-instruct-q8_0.gguf").write_text("another app's")
    ai_download.remove_old_model()
    assert folder.exists()
    (snapshot / "qwen2.5-1.5b-instruct-q8_0.gguf").unlink()
    other_revision = folder / "snapshots" / "def"
    other_revision.mkdir()
    (other_revision / ai_download.OLD_MODEL_FILE).write_text("another app's revision")
    ai_download.remove_old_model()
    assert folder.exists()
    (other_revision / ai_download.OLD_MODEL_FILE).unlink()
    ai_download.remove_old_model()
    assert not folder.exists()


def test_the_new_models_download_deletes_the_old_one(cache, monkeypatch):
    from tuxtalk import model_helper

    def fetch(name, repo_id=None, only_files=None):
        def download(progress, cancel):
            cache.add(NEW)
            return True
        return download
    monkeypatch.setattr(model_helper, "make_model_download_func", fetch)
    cache.add(OLD)
    assert ai_download.make_model_download_func()(lambda *a: None, None)
    assert cache == {NEW}


def test_the_new_model_is_pinned_and_checked_like_every_other():
    from tuxtalk import hub_pins
    assert len(hub_pins.REVISIONS[ai_download.MODEL_REPO]) == 40
    assert len(hub_pins.SHA256[NEW]) == 64


# --- the question at start-up -------------------------------------------------------------

@pytest.fixture
def offer(cache, monkeypatch, tmp_path):
    """The start-up offer with the AI on, only the old model, and a fresh config
    folder; returns (questions asked, downloads run, restarts)."""
    from types import SimpleNamespace

    from tuxtalk import config, download_progress_dialog, startup_notices
    from tuxtalk.startup_notices import Gtk
    cache.add(OLD)
    asked, downloads, restarts = [], [], []
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.toml"))
    monkeypatch.setattr(config, "load_config",
                        lambda: SimpleNamespace(ai_corrections=True, voice_editing=False))
    answer = {"button": Gtk.ResponseType.OK}
    monkeypatch.setattr(startup_notices, "show_message",
                        lambda *a, **k: asked.append(k) or answer["button"])

    def download(parent=None):
        downloads.append(parent)
        cache.discard(OLD)
        cache.add(NEW)
        return True
    monkeypatch.setattr(download_progress_dialog, "download_better_ai_model", download)
    return SimpleNamespace(asked=asked, downloads=downloads, restarts=restarts, answer=answer,
                           run=lambda: startup_notices._offer_better_ai_model_once(
                               lambda: restarts.append("restart")))


def test_download_fetches_it_and_restarts_the_service_onto_it(offer):
    offer.run()
    assert len(offer.asked) == 1 and len(offer.downloads) == 1
    assert offer.restarts == ["restart"]


def test_it_is_asked_once_whatever_the_answer(offer):
    from tuxtalk.startup_notices import Gtk
    offer.answer["button"] = Gtk.ResponseType.CANCEL
    offer.run()
    offer.run()
    assert len(offer.asked) == 1
    assert offer.downloads == [] and offer.restarts == []


def test_the_question_leaves_the_keyboard_where_it_is(offer):
    """It can come up mid-dictation: Enter or a typed space must not answer it."""
    from tuxtalk.startup_notices import Gtk
    offer.run()
    assert offer.asked[0]["take_focus"] is False
    assert offer.asked[0]["default"] == Gtk.ResponseType.CANCEL


def test_not_asked_with_the_ai_off(offer, monkeypatch):
    from types import SimpleNamespace

    from tuxtalk import config
    monkeypatch.setattr(config, "load_config",
                        lambda: SimpleNamespace(ai_corrections=False, voice_editing=False))
    offer.run()
    assert offer.asked == []


def test_not_asked_once_the_new_model_is_here(offer, cache):
    cache.add(NEW)
    offer.run()
    assert offer.asked == []
