"""Preferences → General → Speech Recognition: what picking a model or a
device does when it needs something set up first, and what the dropdowns
show afterwards. Every question and download is answered by a stand-in, so
no real dialog opens and nothing is downloaded."""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from tuxtalk import (download_progress_dialog, model_helper, prefs,  # noqa: E402
                     prefs_speech, vulkan_setup_dialogs)


@pytest.fixture
def speech(prefs_window, monkeypatch):
    """The Speech section of a Preferences window on a computer with
    *card* ("nvidia", "amd" or None), CUDA's files missing, on the processor
    with Small. Returns (build, what the stand-ins were asked)."""
    asked = []

    def build(card, **settings):
        monkeypatch.setattr(prefs.PreferencesWindow, "nvidia_gpu",
                            lambda self: "NVIDIA RTX" if card == "nvidia" else False)
        monkeypatch.setattr(prefs.PreferencesWindow, "cuda_files_present", lambda self: False)
        monkeypatch.setattr(prefs.PreferencesWindow, "vulkan_offered",
                            lambda self: card is not None)
        window = prefs_window(**{"model": "small", "device": "cpu", **settings})
        return window.general_tab.speech

    def answer(response):
        """show_message answers *response* and records each title."""
        monkeypatch.setattr(prefs_speech, "show_message",
                            lambda parent, kind, title, text="", buttons=None, keep_above=False:
                            asked.append(title) or response)

    build.answer = answer
    build.asked = asked
    return build


def _shown(section):
    return section.model_combo.get_active_id(), section.config["model"], section.config["device"]


def test_large_v3_on_nvidia_through_vulkan_picks_both(speech, monkeypatch):
    section = speech("nvidia")
    monkeypatch.setattr(vulkan_setup_dialogs, "choose_light_or_full",
                        lambda parent, model: vulkan_setup_dialogs.LIGHT)
    monkeypatch.setattr(vulkan_setup_dialogs, "set_up", lambda parent, model, confirm: True)
    section.model_combo.set_active_id("large-v3")
    assert _shown(section) == ("large-v3", "large-v3", "vulkan")


def test_cuda_downloaded_but_large_v3_not_keeps_the_old_model(speech, monkeypatch):
    """CUDA's files are there now, so large-v3 loses its download note; the
    model itself didn't arrive, so it isn't selected."""
    section = speech("nvidia")
    monkeypatch.setattr(vulkan_setup_dialogs, "choose_light_or_full",
                        lambda parent, model: vulkan_setup_dialogs.FULL)
    monkeypatch.setattr(download_progress_dialog, "show_unified_download_dialog",
                        lambda **k: {"CUDA Libraries": {"success": True},
                                     "large-v3 AI Model": {"success": False}})
    section.model_combo.set_active_id("large-v3")
    assert _shown(section) == ("small", "small", "cpu")
    labels = {row[0]: row[1] for row in section.model_store}
    assert labels["large-v3"] == prefs_speech.MODEL_TEXT["large-v3"]


def test_cuda_and_large_v3_downloaded_pick_both(speech, monkeypatch):
    section = speech("nvidia")
    installed = []
    monkeypatch.setattr(prefs.PreferencesWindow, "cuda_files_present",
                        lambda self: bool(installed))
    monkeypatch.setattr(vulkan_setup_dialogs, "choose_light_or_full",
                        lambda parent, model: vulkan_setup_dialogs.FULL)
    monkeypatch.setattr(download_progress_dialog, "show_unified_download_dialog",
                        lambda **k: installed.append(1) or {
                            "CUDA Libraries": {"success": True},
                            "large-v3 AI Model": {"success": True}})
    section.model_combo.set_active_id("large-v3")
    assert _shown(section) == ("large-v3", "large-v3", "cuda")


@pytest.mark.parametrize("response, kept", [(Gtk.ResponseType.OK, True),
                                            (Gtk.ResponseType.CANCEL, False)])
def test_large_v3_on_amd_asks_about_vulkan(speech, monkeypatch, response, kept):
    section = speech("amd")
    speech.answer(response)
    setups = []
    monkeypatch.setattr(vulkan_setup_dialogs, "set_up",
                        lambda parent, model, confirm: setups.append(confirm) or True)
    section.model_combo.set_active_id("large-v3")
    assert speech.asked == ["Large-v3 needs your graphics card"]
    if kept:
        assert setups == [False]        # already asked: no second question
        assert _shown(section) == ("large-v3", "large-v3", "vulkan")
    else:
        assert setups == []
        assert _shown(section) == ("small", "small", "cpu")


def test_large_v3_without_a_graphics_card_says_why_and_changes_nothing(speech):
    section = speech(None)
    speech.answer(Gtk.ResponseType.OK)
    section.model_combo.set_active_id("large-v3")
    assert speech.asked == ["Graphics Card Required"]
    assert _shown(section) == ("small", "small", "cpu")


@pytest.mark.parametrize("response, kept", [(Gtk.ResponseType.OK, True),
                                            (Gtk.ResponseType.CANCEL, False),
                                            (Gtk.ResponseType.DELETE_EVENT, False)])
def test_large_v3_not_downloaded_yet_is_confirmed_first(speech, monkeypatch, response, kept):
    """With CUDA there, large-v3's 3 GB is announced; Escape or the title
    bar's close is a no, like Cancel."""
    section = speech("nvidia")
    monkeypatch.setattr(prefs.PreferencesWindow, "cuda_files_present", lambda self: True)
    monkeypatch.setattr(model_helper, "is_model_cached_fast", lambda model: False)
    speech.answer(response)
    section.model_combo.set_active_id("large-v3")
    assert speech.asked == ["Large Model Selected"]
    assert section.config["model"] == ("large-v3" if kept else "small")
    assert section.model_combo.get_active_id() == section.config["model"]


def test_a_refused_vulkan_setup_puts_the_device_back(speech, monkeypatch):
    section = speech("amd")
    monkeypatch.setattr(vulkan_setup_dialogs, "set_up", lambda parent, model, confirm: False)
    # The setup runs once the dropdown has closed (an idle callback): run it
    # here, so it isn't left for a later test's main loop.
    queued = []
    monkeypatch.setattr(prefs_speech.GLib, "idle_add", lambda fn, *a: queued.append((fn, a)))
    section.device_combo.set_active_id("vulkan")
    fn, args = queued.pop()
    fn(*args)
    assert queued == []
    assert section.device_combo.get_active_id() == "cpu"
    assert section.config["device"] == "cpu"


def test_use_recommended_falls_back_to_the_processor_when_it_is_faster(speech, monkeypatch):
    """The speed check found the graphics chip slower: the processor
    version of the recommended setup is picked, and the row stops offering
    the graphics card."""
    section = speech("amd")
    monkeypatch.setattr(vulkan_setup_dialogs, "ensure_files",
                        lambda parent, model, confirm: True)
    monkeypatch.setattr(vulkan_setup_dialogs, "run_speed_check", lambda parent, model: False)
    section.use_recommended_btn.clicked()
    assert section.config["device"] == "cpu"
    assert section._shown_language().vulkan_slower is True
    assert section.model_combo.get_active_id() == section.config["model"]


def test_a_cancelled_download_on_apply_says_nothing(monkeypatch):
    """Cancel in the download window: Apply stops quietly, with no
    "Model download failed!" box."""
    shown = []
    monkeypatch.setattr(prefs_speech, "download_model", lambda parent, model: (False, True))
    monkeypatch.setattr(prefs_speech, "show_message",
                        lambda *a, **k: shown.append(a[2]))
    from types import SimpleNamespace
    stub = SimpleNamespace(config={"model": "small", "device": "cpu"}, window=None)
    assert prefs_speech.SpeechSection.download_selected_model(stub) is False
    assert shown == []


def test_a_failed_download_on_apply_says_so(monkeypatch):
    shown = []
    monkeypatch.setattr(prefs_speech, "download_model", lambda parent, model: (False, False))
    monkeypatch.setattr(prefs_speech, "show_message",
                        lambda *a, **k: shown.append(a[2]))
    from types import SimpleNamespace
    stub = SimpleNamespace(config={"model": "small", "device": "cpu"}, window=None)
    assert prefs_speech.SpeechSection.download_selected_model(stub) is False
    assert shown == ["Model download failed!"]
