"""After the welcome card, setup downloads exactly what it showed."""
import threading
import types

import pytest

from tuxtalk import config
from tuxtalk import first_run_setup as fr


@pytest.fixture
def env(monkeypatch):
    cfg = types.SimpleNamespace(model="parakeet-v3", device="cpu", dictation_language="",
                                recommend_notice_shown=False)
    calls = []
    monkeypatch.setattr("tuxtalk.config.load_config", lambda: cfg)
    monkeypatch.setattr("tuxtalk.config.save_config", lambda c: calls.append(("save", c.model, c.device)))
    monkeypatch.setattr(fr, "_download_vulkan_model_first_run",
                        lambda m: calls.append(("vk", m)) or (True, False))
    monkeypatch.setattr(fr, "_vulkan_speed_check_first_run", lambda m: calls.append(("speed", m)) or True)
    monkeypatch.setattr("tuxtalk.model_helper.is_model_cached_fast", lambda m: False)
    monkeypatch.setattr(fr, "_download_processor_model",
                        lambda m: calls.append(("cpu", m)) or (True, False))
    monkeypatch.setattr(fr, "_ask_retry_download", lambda m: False)
    monkeypatch.setattr(fr, "_tell_using_smaller_model", lambda chosen, used: None)
    return cfg, calls


def test_graphics_card_setup(env):
    cfg, calls = env
    assert fr._apply_first_run_setup({"model": "parakeet-v3", "device": "vulkan",
                                      "dictation_language": "en"})
    assert ("vk", "parakeet-v3") in calls and ("speed", "parakeet-v3") in calls
    assert (cfg.model, cfg.device, cfg.dictation_language) == ("parakeet-v3", "vulkan", "en")
    assert cfg.recommend_notice_shown is True
    assert not any(c[0] == "cpu" for c in calls)


def test_slower_graphics_falls_back_to_the_processor_model(env, monkeypatch):
    cfg, calls = env
    monkeypatch.setattr(fr, "_vulkan_speed_check_first_run", lambda m: False)
    fr._apply_first_run_setup({"model": "parakeet-v3", "device": "vulkan", "dictation_language": "en"})
    assert cfg.device == "cpu" and ("cpu", "parakeet-v3") in calls


def test_processor_setup(env):
    cfg, calls = env
    fr._apply_first_run_setup({"model": "small", "device": "cpu", "dictation_language": "ja"})
    assert ("cpu", "small") in calls and not any(c[0] == "vk" for c in calls)
    assert (cfg.model, cfg.device, cfg.dictation_language) == ("small", "cpu", "ja")


def test_no_first_run_screen_picks_a_model_after_the_welcome(welcome_window, setup_complete):
    """The welcome card chooses the model; nothing later asks again."""
    from gtk_text import tree_text
    shown = tree_text(welcome_window.dialog) + setup_complete()
    assert not [text for text in shown if "Starting Model" in text]


@pytest.fixture
def settings(monkeypatch):
    """Like the real config: every load is a fresh copy and every save writes
    all of it back, so a lost write shows (the shared-object fixture above
    would hide one)."""
    saved = {"model": "parakeet-v3", "device": "cpu", "dictation_language": "",
             "recommend_notice_shown": False, "vulkan_slower": False}
    monkeypatch.setattr("tuxtalk.config.load_config", lambda: types.SimpleNamespace(**saved))
    monkeypatch.setattr("tuxtalk.config.save_config", lambda c: saved.update(vars(c)))
    return saved


@pytest.fixture
def store(settings, monkeypatch):
    """The settings, and the processor downloads asked for, with every
    download and question faked."""
    saved, calls = settings, []
    monkeypatch.setattr(fr, "_download_vulkan_model_first_run", lambda m: (True, False))
    monkeypatch.setattr("tuxtalk.model_helper.is_model_cached_fast", lambda m: False)
    monkeypatch.setattr(fr, "_download_processor_model",
                        lambda m: calls.append(m) or (True, False))
    monkeypatch.setattr(fr, "_ask_retry_download", lambda m: False)
    notes = []
    monkeypatch.setattr(fr, "_tell_using_smaller_model", lambda chosen, used: notes.append((chosen, used)))
    saved["_notes"] = notes
    return saved, calls


def _processor_wins(monkeypatch):
    """The real speed check records the loss in its own load/save."""
    from tuxtalk.config import load_config, save_config

    def check(model):
        cfg = load_config()
        cfg.vulkan_slower = True
        save_config(cfg)
        return False
    monkeypatch.setattr(fr, "_vulkan_speed_check_first_run", check)


def test_a_lost_speed_check_stays_remembered(store, monkeypatch):
    saved, calls = store
    _processor_wins(monkeypatch)
    fr._apply_first_run_setup({"model": "parakeet-v3", "device": "vulkan", "dictation_language": "en"})
    assert saved["vulkan_slower"] is True
    assert (saved["model"], saved["device"]) == ("parakeet-v3", "cpu")


def test_large_v3_becomes_small_when_it_ends_up_on_the_processor(store, monkeypatch):
    """Large-v3 on the processor is the setup the card greys out ("Needs a
    graphics card"), and first run must not fetch 3 GB of it unasked."""
    saved, calls = store
    _processor_wins(monkeypatch)
    fr._apply_first_run_setup({"model": "large-v3", "device": "vulkan", "dictation_language": "ja"})
    assert (saved["model"], saved["device"]) == ("small", "cpu")
    assert calls == ["small"]


def test_failed_graphics_download_also_falls_back_to_small(store, monkeypatch):
    saved, calls = store
    monkeypatch.setattr(fr, "_download_vulkan_model_first_run", lambda m: (False, False))
    monkeypatch.setattr(fr, "_vulkan_speed_check_first_run", lambda m: True)
    fr._apply_first_run_setup({"model": "large-v3", "device": "vulkan", "dictation_language": "ja"})
    assert (saved["model"], saved["device"]) == ("small", "cpu")
    assert calls == ["small"]


def test_a_cancelled_first_run_download_starts_no_other(store, monkeypatch):
    """One Cancel is enough: it must not go on to Whisper Small, then Tiny."""
    saved, calls = store

    monkeypatch.setattr(fr, "_download_processor_model",
                        lambda m: calls.append(m) or (False, True))
    assert fr._apply_first_run_setup({"model": "parakeet-v3", "device": "cpu",
                                      "dictation_language": "en"}) is False
    assert calls == ["parakeet-v3"]
    assert saved["model"] == "parakeet-v3"     # offered again when the service starts


def test_a_cancelled_graphics_download_starts_no_processor_download(store, monkeypatch):
    """Cancel in the graphics download window must not go straight on to a
    processor download of the same model."""
    saved, calls = store
    speed = []

    monkeypatch.setattr(fr, "_download_vulkan_model_first_run", lambda m: (False, True))
    monkeypatch.setattr(fr, "_vulkan_speed_check_first_run", lambda m: speed.append(m) or True)
    fr._apply_first_run_setup({"model": "parakeet-v3", "device": "vulkan", "dictation_language": "en"})
    assert calls == [] and speed == []
    assert (saved["model"], saved["device"]) == ("parakeet-v3", "cpu")   # offered when the service starts


def test_a_failed_first_run_download_tries_one_smaller_model(store, monkeypatch):
    """And says no model is ready when that fails too, so Setup Complete
    doesn't say "ready to use"."""
    saved, calls = store
    monkeypatch.setattr(fr, "_download_processor_model",
                        lambda m: calls.append(m) or (False, False))
    assert fr._apply_first_run_setup({"model": "parakeet-v3", "device": "cpu",
                                      "dictation_language": "en"}) is False
    assert calls == ["parakeet-v3", "small"]   # not a third error box for tiny


def test_a_failed_download_uses_an_already_downloaded_smaller_model(store, monkeypatch):
    saved, calls = store
    monkeypatch.setattr("tuxtalk.model_helper.is_model_cached_fast", lambda m: m == "tiny")
    monkeypatch.setattr(fr, "_download_processor_model",
                        lambda m: calls.append(m) or (False, False))
    fr._apply_first_run_setup({"model": "parakeet-v3", "device": "cpu", "dictation_language": "en"})
    assert calls == ["parakeet-v3"] and saved["model"] == "tiny"


def test_the_emergency_fallback_also_moves_to_the_processor(store, monkeypatch):
    """whisper.cpp has no copy of Tiny, so vulkan + tiny would not run."""
    saved, calls = store

    def broken(m, **k):
        raise RuntimeError("engine check failed")

    monkeypatch.setattr(fr, "_download_vulkan_model_first_run", broken)
    monkeypatch.setattr("tuxtalk.model_helper.is_model_cached_fast", lambda m: m == "tiny")
    fr._apply_first_run_setup({"model": "parakeet-v3", "device": "vulkan", "dictation_language": "en"})
    assert (saved["model"], saved["device"]) == ("tiny", "cpu")


class _DownloadWindow:
    """UnifiedDownloadDialog without a window: run() runs each download
    at once, or cancels the *cancelled* ones without running them."""
    cancelled = ()

    def __init__(self, **kwargs):
        self.tasks = []

    def add_task(self, task):
        self.tasks.append(task)

    def run(self):
        for t in self.tasks:
            t.cancelled = t.name in self.cancelled
            t.success = not t.cancelled and t.download_func(lambda *a: None, threading.Event())
            t.completed = not t.cancelled
        return {t.name: {"success": t.success, "cancelled": t.cancelled,
                         "completed": t.completed} for t in self.tasks}


_WHISPER_SMALL = [("model.bin", 400), ("config.json", 2), ("tokenizer.json", 2),
                  ("vocabulary.txt", 1), ("README.md", 1)]


@pytest.fixture
def hugging_face(monkeypatch):
    """Whisper Small's files on a fake Hugging Face, through the real
    download code: the files fetched, by name. Nothing may load the model."""
    import huggingface_hub
    from tuxtalk import download_progress_dialog, download_utils, model_helper
    fetched = []

    def loaded(*args, **kwargs):
        pytest.fail("first run loaded the speech model")

    def snapshot(repo, allow_patterns=None, **kwargs):
        fetched.extend(name for name, _size in _WHISPER_SMALL if name in allow_patterns)

    monkeypatch.setattr(download_progress_dialog, "UnifiedDownloadDialog", _DownloadWindow)
    monkeypatch.setattr(model_helper, "_files_to_download", lambda repo: list(_WHISPER_SMALL))
    monkeypatch.setattr(huggingface_hub, "hf_hub_download",
                        lambda repo_id, filename, **k: fetched.append(filename))
    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda repo, name: None)
    monkeypatch.setattr(download_utils, "free_space_bytes", lambda path: 10 ** 12)
    monkeypatch.setattr(model_helper, "is_model_cached_fast",
                        lambda m: {"model.bin", "config.json", "tokenizer.json"} <= set(fetched))
    monkeypatch.setattr(model_helper, "load_model", loaded)
    monkeypatch.setattr(model_helper, "download_model_with_progress", loaded)
    monkeypatch.setattr("faster_whisper.WhisperModel", loaded)
    return fetched


def test_first_run_on_the_processor_downloads_the_model_without_loading_it(settings,
                                                                          hugging_face):
    """The dictation service loads the model when it starts. Loaded at first
    run too, it kept the window at "Loading model..." for seconds and the
    tray holding 0.7 to 3 GB, only to drop it."""
    assert fr._apply_first_run_setup({"model": "small", "device": "cpu",
                                      "dictation_language": "en"})
    assert hugging_face == [name for name, _size in _WHISPER_SMALL]
    assert (settings["model"], settings["device"]) == ("small", "cpu")


def test_without_hugging_faces_file_list_the_model_is_still_not_loaded(settings, hugging_face,
                                                                      monkeypatch):
    """Without the list, the files came through WhisperModel(), which loads
    the model too (and not as int8)."""
    from tuxtalk import model_helper

    def no_list(repo):
        raise OSError("rate limited")
    monkeypatch.setattr(model_helper, "_files_to_download", no_list)
    assert fr._apply_first_run_setup({"model": "small", "device": "cpu",
                                      "dictation_language": "en"})
    # By name, the optional ones too (a model has only one vocabulary file).
    assert set(hugging_face) == {"model.bin", "config.json", "tokenizer.json", "vocabulary.txt",
                                 "vocabulary.json", "preprocessor_config.json"}


def test_cancel_stops_a_download_without_the_file_list(hugging_face, monkeypatch):
    """The window closes at once on Cancel; the download fetched by name
    behind it must stop too, not run on unseen for gigabytes."""
    import threading
    from tuxtalk import model_helper

    def no_list(repo):
        raise OSError("rate limited")
    monkeypatch.setattr(model_helper, "_files_to_download", no_list)
    cancel = threading.Event()
    cancel.set()
    download = model_helper.make_model_download_func("small")
    assert download(lambda text, percent: None, cancel) is False
    assert hugging_face == []


def test_a_cancelled_processor_download_says_so(hugging_face, monkeypatch):
    """So that one Cancel starts no smaller download after it."""
    monkeypatch.setattr(_DownloadWindow, "cancelled", ("Whisper Small",))
    assert fr._download_processor_model("small") == (False, True)
    assert hugging_face == []


def test_a_cancelled_retry_starts_no_smaller_download(store, monkeypatch):
    saved, calls = store
    results = iter([(False, False), (False, True)])     # fails, then the retry is cancelled
    monkeypatch.setattr(fr, "_download_processor_model", lambda m: calls.append(m) or next(results))
    monkeypatch.setattr(fr, "_ask_retry_download", lambda m: True)
    assert fr._apply_first_run_setup({"model": "parakeet-v3", "device": "cpu",
                                      "dictation_language": "en"}) is False
    assert calls == ["parakeet-v3", "parakeet-v3"] and saved["_notes"] == []


def test_setup_complete_hears_that_no_model_came(first_run, monkeypatch):
    seen = {}
    monkeypatch.setattr(fr, "_apply_first_run_setup", lambda result: False)
    monkeypatch.setattr(fr, "show_setup_complete_dialog", lambda **k: seen.update(k))
    first_run()
    assert seen["model_ready"] is False


def test_without_a_model_setup_complete_does_not_say_ready(setup_complete):
    shown = setup_complete(model_ready=False)
    assert "TuxTalk is ready to use." not in shown
    assert any("The speech model isn't downloaded yet." in text for text in shown)
    assert "Start Using TuxTalk!" not in shown and "Continue" in shown
    ready = setup_complete()
    assert "TuxTalk is ready to use." in ready and "Start Using TuxTalk!" in ready


def test_the_ready_screen_names_the_key_the_user_chose(setup_complete, monkeypatch):
    """Not always F8: the hotkey test may have changed the key."""
    monkeypatch.setattr("tuxtalk.config.load_config",
                        lambda: types.SimpleNamespace(hotkey="F10"))
    assert config.saved_hold_key() == "F10"
    shown = setup_complete()
    assert "1. Hold F10" in shown
    assert not [text for text in shown if "or your hotkey" in text]
    monkeypatch.setattr("tuxtalk.config.load_config",
                        lambda: types.SimpleNamespace(hotkey=""))
    assert config.saved_hold_key() == "F8"


# --- a failed download asks before falling back (VM tests, 2026-10-13) --------

def test_a_failed_download_can_be_tried_again(store, monkeypatch):
    saved, calls = store
    results = iter([(False, False), (True, False)])     # fails, then works
    monkeypatch.setattr(fr, "_download_processor_model",
                        lambda m: calls.append(m) or next(results))
    monkeypatch.setattr(fr, "_ask_retry_download", lambda m: True)
    fr._apply_first_run_setup({"model": "parakeet-v3", "device": "cpu", "dictation_language": "en"})
    assert calls == ["parakeet-v3", "parakeet-v3"]
    assert saved["model"] == "parakeet-v3" and saved["_notes"] == []


def test_a_smaller_model_is_announced(store, monkeypatch):
    saved, calls = store
    monkeypatch.setattr(fr, "_download_processor_model",
                        lambda m: calls.append(m) or (m == "small", False))
    fr._apply_first_run_setup({"model": "parakeet-v3", "device": "cpu", "dictation_language": "en"})
    assert saved["model"] == "small"
    assert saved["_notes"] == [("parakeet-v3", "small")]


def test_retrying_is_offered_at_most_twice(store, monkeypatch):
    saved, calls = store
    monkeypatch.setattr(fr, "_download_processor_model",
                        lambda m: calls.append(m) or (False, False))
    monkeypatch.setattr(fr, "_ask_retry_download", lambda m: True)
    fr._apply_first_run_setup({"model": "parakeet-v3", "device": "cpu", "dictation_language": "en"})
    assert calls == ["parakeet-v3"] * 3 + ["small"]


@pytest.fixture
def kept_f10(monkeypatch):
    """A reinstall that kept a config whose hold key is F10."""
    from tuxtalk import config
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(hotkey="F10"))


def test_the_welcome_names_a_hold_key_kept_from_before(kept_f10, welcome_window):
    """Not F8: after a reinstall the settings may already name another key."""
    from gtk_text import tree_text
    shown = " ".join(tree_text(welcome_window.dialog))
    assert "F10" in shown and "F8" not in shown


@pytest.mark.parametrize("on", [True, False])
def test_setup_complete_promises_a_start_at_login_only_when_it_is_on(setup_complete,
                                                                    monkeypatch, on):
    """An autostart file that couldn't be written leaves launch_at_login off;
    the screen said TuxTalk would start at login all the same."""
    from tuxtalk import config
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(launch_at_login=on))
    shown = " ".join(setup_complete())
    assert ("start automatically when you log in" in shown) is on
