"""After CUDA's download: switch for real, and never slow Parakeet down.

CUDA's files come two ways: picking CUDA in the Device dropdown, or large-v3
in the Model dropdown (CUDA's files and the model in one window). Both end
the same way: the model and device are saved at once and dictation restarts
onto them, while the window's other changes stay unsaved for Apply or
Cancel. The download itself never saves (that's Preferences' job, so the
restart isn't skipped). And Parakeet can't use CUDA, so switching it there
would move it from the graphics card to the processor: Parakeet users are
asked which Whisper model to use on CUDA instead. Every question, download
and restart is a stand-in.
"""
from types import SimpleNamespace

import pytest

from gtk_text import widgets


@pytest.fixture
def prefs():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "3.0")
    from tuxtalk import prefs
    return prefs


def _asks(monkeypatch, stub, answer):
    """The Whisper-model question answers *answer*, and is counted."""
    from tuxtalk import prefs_speech
    monkeypatch.setattr(prefs_speech, "ask_whisper_model_for_cuda",
                        lambda parent: stub.asked.append(True) or answer)


def _speech():
    from tuxtalk.prefs_speech import SpeechSection
    return SpeechSection


@pytest.fixture
def nvidia(prefs_window, monkeypatch):
    """Preferences on an NVIDIA computer without CUDA's files, the speech
    models already downloaded but those named in build.missing. build(model)
    returns its Speech section; build.happened lists the questions,
    downloads and restarts in order, build.answer() sets what the
    Whisper-model question answers, and build.download_both() fakes
    large-v3's window of two downloads."""
    from gi.repository import Gtk
    from tuxtalk import (cuda_helper, download_progress_dialog, model_helper, prefs,
                         prefs_speech, recommend)
    from tuxtalk.download_progress_dialog import CUDA_TASK, model_task
    happened, installed, missing = [], [], set()

    def download_model_files(parent, model):
        happened.append(f"{model} downloaded")
        missing.discard(model)
        return True, False

    def download_cuda(parent=None, on_success_callback=None):
        installed.append(True)
        happened.append("CUDA downloaded")
        on_success_callback()
        return True

    def download_both(model_arrives=True):
        def download(cuda, model, parent):
            installed.append(True)
            happened.append(f"CUDA and {model} downloaded")
            return {CUDA_TASK: {"success": True},
                    model_task(model): {"success": model_arrives}}
        monkeypatch.setattr(download_progress_dialog, "show_unified_download_dialog", download)

    def answer(model):
        monkeypatch.setattr(prefs_speech, "ask_whisper_model_for_cuda",
                            lambda parent: happened.append("which Whisper model?") or model)

    def build(model):
        win_cls = prefs.PreferencesWindow
        monkeypatch.setattr(win_cls, "nvidia_gpu", lambda self: "NVIDIA RTX")
        monkeypatch.setattr(win_cls, "cuda_files_present", lambda self: bool(installed))
        monkeypatch.setattr(win_cls, "vulkan_offered", lambda self: False)
        monkeypatch.setattr(recommend, "detect_hardware",
                            lambda cfg=None: recommend.Hardware("NVIDIA RTX", True))
        monkeypatch.setattr(win_cls, "restart_service",
                            lambda self, on_failed=None: happened.append("restart") or True)
        monkeypatch.setattr(prefs, "show_message", lambda *a, **k: happened.append(a[2]))
        monkeypatch.setattr(prefs_speech, "show_message",
                            lambda *a, **k: happened.append(a[2]) or Gtk.ResponseType.YES)
        monkeypatch.setattr(model_helper, "is_model_cached_fast",
                            lambda model: model not in missing)
        monkeypatch.setattr(model_helper, "download_model_files", download_model_files)
        monkeypatch.setattr(cuda_helper, "show_cuda_download_dialog", download_cuda)
        answer(None)
        return prefs_window(model=model, device="cpu").general_tab.speech

    build.happened, build.answer, build.download_both = happened, answer, download_both
    build.missing = missing
    return build


def _pick_cuda_device(section, monkeypatch):
    """Pick CUDA in the Device dropdown, and run the setup it leaves for
    the dropdown's closing (an idle callback) at once."""
    from tuxtalk import prefs_speech
    queued = []
    monkeypatch.setattr(prefs_speech.GLib, "idle_add", lambda fn, *a: queued.append((fn, a)))
    section.device_combo.set_active_id("cuda")
    fn, args = queued.pop()
    fn(*args)
    assert queued == []


def _end_state(section):
    """What the user sees and what was saved: (Model dropdown, Device
    dropdown, the model and device on disk)."""
    import toml     # not tomllib: Python 3.10 (Ubuntu 22.04, the AppImage's) lacks it
    from tuxtalk import prefs
    try:
        saved = toml.load(prefs.CONFIG_PATH)
    except FileNotFoundError:
        saved = {}
    return (section.model_combo.get_active_id(), section.device_combo.get_active_id(),
            saved.get("model"), saved.get("device"))


def _device_labels(section):
    return {row[1]: row[0] for row in section.device_combo.get_model()}


def _large_v3_label(section):
    return next(row[1] for row in section.model_store if row[0] == "large-v3")


def test_cuda_from_the_device_dropdown_switches_a_whisper_model_at_once(nvidia, monkeypatch):
    section = nvidia("small")
    _pick_cuda_device(section, monkeypatch)
    assert nvidia.happened == ["Download CUDA Libraries?", "CUDA downloaded", "restart"]
    assert _end_state(section) == ("small", "cuda", "small", "cuda")
    assert _device_labels(section)["cuda"] == "CUDA (NVIDIA)"     # no download size now


@pytest.mark.parametrize("picked", ["small", "large-v3", "tiny"])
def test_cuda_from_the_device_dropdown_moves_parakeet_to_the_model_picked(
        nvidia, monkeypatch, picked):
    """Any Whisper model can be picked, not only Small, so wanting another
    one doesn't mean downloading Small first."""
    section = nvidia("parakeet-v3")
    nvidia.answer(picked)
    _pick_cuda_device(section, monkeypatch)
    assert nvidia.happened == ["Download CUDA Libraries?", "CUDA downloaded",
                               "which Whisper model?", "restart"]
    assert _end_state(section) == (picked, "cuda", picked, "cuda")


def test_a_whisper_model_picked_after_the_download_is_downloaded_first(nvidia, monkeypatch):
    """Dictation can't restart onto a model that isn't there."""
    section = nvidia("parakeet-v3")
    nvidia.missing.add("medium")
    nvidia.answer("medium")
    _pick_cuda_device(section, monkeypatch)
    assert nvidia.happened == ["Download CUDA Libraries?", "CUDA downloaded",
                               "which Whisper model?", "medium downloaded",
                               "Model Downloaded Successfully!", "restart"]
    assert _end_state(section) == ("medium", "cuda", "medium", "cuda")


def test_a_failed_model_download_after_cuda_switches_nothing(nvidia, monkeypatch):
    from tuxtalk import model_helper
    section = nvidia("parakeet-v3")
    nvidia.missing.add("medium")
    nvidia.answer("medium")
    monkeypatch.setattr(model_helper, "download_model_files",
                        lambda parent, model: (False, False))
    _pick_cuda_device(section, monkeypatch)
    assert nvidia.happened[-1] == "Model download failed!"
    assert "restart" not in nvidia.happened
    assert _end_state(section) == ("parakeet-v3", "cpu", None, None)


def test_the_switch_saves_only_the_model_and_device(nvidia, monkeypatch):
    """A new hold key and another setting changed, not applied: the CUDA
    switch saves and restarts for the model and device alone, asks nothing
    about the hotkey, and leaves the rest for Apply or Cancel."""
    import toml
    from tuxtalk import prefs
    section = nvidia("small")
    win = section.win
    period = win.config["auto_period"]
    win.update_config("hotkey", "F6")
    win.update_config("auto_period", not period)
    monkeypatch.setattr(win.general_tab, "after_hotkey_change",
                        lambda *a: nvidia.happened.append("hotkey question"))
    _pick_cuda_device(section, monkeypatch)
    assert nvidia.happened == ["Download CUDA Libraries?", "CUDA downloaded", "restart"]
    assert _end_state(section) == ("small", "cuda", "small", "cuda")
    saved = toml.load(prefs.CONFIG_PATH)
    assert saved.get("hotkey") != "F6" and saved.get("auto_period") == period
    assert win._changed_since_open() == {"hotkey", "auto_period"}
    assert (win.config["hotkey"], win.config["auto_period"]) == ("F6", not period)


def test_a_switch_that_cant_be_saved_says_so(nvidia, monkeypatch):
    """A read-only settings file: nothing restarts, the user is told, and
    the model and device stay chosen for Apply to try again."""
    section = nvidia("small")
    win = section.win
    monkeypatch.setattr(win, "save_now", lambda settings: False)
    _pick_cuda_device(section, monkeypatch)
    assert nvidia.happened == ["Download CUDA Libraries?", "CUDA downloaded",
                               "Couldn't switch to CUDA"]
    assert win._changed_since_open() >= {"device"}


def test_rebuilding_the_device_dropdown_is_no_pick(nvidia, monkeypatch):
    """With CUDA chosen on Parakeet, a live "changed" would ask which
    Whisper model to use; the rebuild after a download must not."""
    from tuxtalk import prefs_speech
    section = nvidia("parakeet-v3")
    nvidia.download_both(model_arrives=False)
    section.model_combo.set_active_id("large-v3")       # CUDA's files are here now
    queued = []
    monkeypatch.setattr(prefs_speech.GLib, "idle_add", lambda fn, *a: queued.append(fn))
    section.config["device"] = "cuda"
    section._refill_device_options()
    assert queued == []
    assert section.device_combo.get_active_id() == "cuda"


def test_keep_parakeet_after_the_download_changes_nothing(nvidia, monkeypatch):
    """The Device dropdown goes back, nothing is saved or restarted, and
    the CUDA entry stops naming a download that's done."""
    section = nvidia("parakeet-v3")
    _pick_cuda_device(section, monkeypatch)
    assert "restart" not in nvidia.happened
    assert _end_state(section) == ("parakeet-v3", "cpu", None, None)
    assert section.config["device"] == "cpu"
    assert _device_labels(section)["cuda"] == "CUDA (Parakeet runs on the processor)"


def test_a_refused_cuda_download_puts_the_device_back(nvidia, monkeypatch):
    from gi.repository import Gtk
    from tuxtalk import prefs_speech
    section = nvidia("small")
    monkeypatch.setattr(prefs_speech, "show_message",
                        lambda *a, **k: nvidia.happened.append(a[2]) or Gtk.ResponseType.NO)
    _pick_cuda_device(section, monkeypatch)
    assert nvidia.happened == ["Download CUDA Libraries?"]
    assert _end_state(section) == ("small", "cpu", None, None)
    assert section.config["device"] == "cpu"


def test_large_v3_with_cuda_switches_at_once_like_the_device_dropdown(nvidia):
    """Picking large-v3 downloads CUDA's files and the model in one window,
    then ends as CUDA from the Device dropdown does: saved, restarted."""
    from tuxtalk.prefs_speech import MODEL_TEXT
    section = nvidia("small")
    nvidia.download_both()
    section.model_combo.set_active_id("large-v3")
    assert nvidia.happened == ["Downloads Required", "CUDA and large-v3 downloaded",
                               "restart"]
    assert _end_state(section) == ("large-v3", "cuda", "large-v3", "cuda")
    assert _device_labels(section)["cuda"] == "CUDA (NVIDIA)"
    assert _large_v3_label(section) == MODEL_TEXT["large-v3"]


def test_cuda_downloaded_but_large_v3_not_keeps_the_old_model(nvidia):
    """CUDA's files are there now, so large-v3 and the CUDA entry lose their
    download notes; the model itself didn't arrive, so nothing switches."""
    from tuxtalk.prefs_speech import MODEL_TEXT
    section = nvidia("small")
    nvidia.download_both(model_arrives=False)
    section.model_combo.set_active_id("large-v3")
    assert "restart" not in nvidia.happened
    assert _end_state(section) == ("small", "cpu", None, None)
    assert (section.config["model"], section.config["device"]) == ("small", "cpu")
    assert _large_v3_label(section) == MODEL_TEXT["large-v3"]
    assert _device_labels(section)["cuda"] == "CUDA (NVIDIA)"


def test_the_download_no_longer_writes_the_config_itself(prefs, monkeypatch):
    """Switching to CUDA is Preferences' job, so the service restarts onto
    it: a download that saved device = "cuda" itself moved the baseline
    Apply compares against, and nothing restarted."""
    import threading
    from tuxtalk import config, cuda_helper, download_progress_dialog
    switched = []
    monkeypatch.setattr(cuda_helper, "download_cuda_libraries", lambda progress, cancel: True)
    monkeypatch.setattr(config, "save_config", lambda cfg: pytest.fail("the download saved settings"))
    monkeypatch.setattr(download_progress_dialog, "run_downloads", lambda parent, title, text,
                        tasks: all(t.download_func(lambda *a: None, threading.Event())
                                              for t in tasks))
    monkeypatch.setattr(cuda_helper, "_say_how_it_went",
                        lambda parent, ok, then, reason: ok and then())
    assert cuda_helper.show_cuda_download_dialog(on_success_callback=lambda: switched.append(1))
    assert switched == [1]


def test_the_question_offers_every_whisper_model_with_large_v3_picked(prefs, monkeypatch):
    if not prefs.Gtk.init_check()[0]:
        pytest.skip("no display")
    shown = {}

    def run_and_record(box):
        # On a short screen the content sits in a scroller (ui_style), so
        # look through all of it.
        radios = [w for w in widgets(box.get_content_area())
                  if isinstance(w, prefs.Gtk.RadioButton)]
        shown["labels"] = [r.get_label() for r in radios]
        shown["active"] = [r.get_label() for r in radios if r.get_active()]
        return prefs.Gtk.ResponseType.YES

    monkeypatch.setattr(prefs.Gtk.Dialog, "run", run_and_record)
    from tuxtalk.prefs_speech import ask_whisper_model_for_cuda
    chosen = ask_whisper_model_for_cuda(None)
    assert chosen == "large-v3"
    assert [label.split(",")[0] for label in shown["labels"]] == \
        ["Tiny", "Base", "Small", "Medium", "Large-v3"]
    assert shown["active"][0].startswith("Large-v3") and "3.1 GB" in shown["active"][0]


# --- CUDA already downloaded, picked in Device with Parakeet -----------------

def _picker(monkeypatch, answer):
    """Device → CUDA with the files already there. Switching straight away
    would leave Parakeet running on the processor under "CUDA"."""
    stub = SimpleNamespace(config={"model": "parakeet-v3", "device": "vulkan"},
                           _device_before="vulkan", asked=[], window=None)
    _asks(monkeypatch, stub, answer)
    stub.set_choice = lambda key, value: stub.config.__setitem__(key, value)
    stub._select_on_cuda = lambda model: _speech()._select_on_cuda(stub, model)
    return stub


def test_picking_cuda_on_parakeet_asks_for_a_whisper_model(prefs, monkeypatch):
    stub = _picker(monkeypatch, "large-v3")
    _speech()._finish_cuda_for_parakeet(stub, None)
    assert stub.asked == [True]
    # Chosen, not applied yet: OK or Apply downloads the model and restarts.
    assert stub.config == {"model": "large-v3", "device": "cuda"}


def test_keep_parakeet_puts_the_device_back(prefs, monkeypatch):
    stub = _picker(monkeypatch, None)
    _speech()._finish_cuda_for_parakeet(stub, None)
    assert stub.config == {"model": "parakeet-v3", "device": "vulkan"}
