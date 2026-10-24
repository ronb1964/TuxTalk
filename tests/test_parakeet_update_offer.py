"""Someone still on the original Parakeet v3 is offered Parakeet Ultra once,
and their Parakeet keeps working whatever they answer."""
import os
import types

import pytest

from tuxtalk import parakeet_ultra as pu
from tuxtalk import tray


@pytest.fixture
def env(monkeypatch):
    cfg = types.SimpleNamespace(model="parakeet-v3", device="vulkan", parakeet_update_offered=False)
    asked, saved, restarts = [], [], []
    monkeypatch.setattr("tuxtalk.config.load_config", lambda: cfg)
    monkeypatch.setattr("tuxtalk.config.save_config", lambda c: saved.append(c.parakeet_update_offered))
    monkeypatch.setattr("tuxtalk.cuda_helper.is_first_run", lambda: False)
    monkeypatch.setattr("tuxtalk.service_launcher.find_service_pids", lambda: [4242])
    monkeypatch.setattr("tuxtalk.model_helper.parakeet_update_needed", lambda c: "vulkan")
    answer = {"value": True}
    monkeypatch.setattr(tray, "_offer_parakeet_update",
                        lambda device: asked.append(device) or answer["value"])
    return cfg, asked, saved, restarts, answer


def _run(restarts):
    return tray.maybe_offer_parakeet_update(restarts.append)


def test_a_yes_downloads_restarts_dictation_and_is_not_asked_again(env):
    cfg, asked, saved, restarts, _ = env
    assert _run(restarts) is False
    assert asked == ["vulkan"] and saved == [True] and restarts == [None]
    _run(restarts)
    assert len(asked) == 1


def test_not_now_is_remembered_and_nothing_restarts(env):
    cfg, asked, saved, restarts, answer = env
    answer["value"] = False
    _run(restarts)
    assert saved == [True] and restarts == []


def test_a_failed_download_is_offered_again_next_start(env):
    cfg, asked, saved, restarts, answer = env
    answer["value"] = None
    _run(restarts)
    assert saved == [] and restarts == []


def test_it_waits_for_dictation_and_never_shows_on_first_run(env, monkeypatch):
    cfg, asked, saved, restarts, _ = env
    monkeypatch.setattr("tuxtalk.service_launcher.find_service_pids", lambda: [])
    assert _run(restarts) is True and asked == []
    monkeypatch.setattr("tuxtalk.service_launcher.find_service_pids", lambda: [4242])
    monkeypatch.setattr("tuxtalk.cuda_helper.is_first_run", lambda: True)
    assert _run(restarts) is False and asked == []


def test_nothing_to_offer_asks_nothing(env, monkeypatch):
    cfg, asked, saved, restarts, _ = env
    monkeypatch.setattr("tuxtalk.model_helper.parakeet_update_needed", lambda c: None)
    _run(restarts)
    assert asked == [] and saved == []


@pytest.fixture
def disk(tmp_path, monkeypatch):
    """A data folder for Ultra and a stand-in for the original v3 download."""
    monkeypatch.setattr("tuxtalk.config.get_data_dir", lambda: str(tmp_path / "data"))
    old = tmp_path / "hf"
    old.mkdir()
    monkeypatch.setattr("huggingface_hub.hf_hub_download",
                        lambda repo, name, local_files_only: str(old / name))
    monkeypatch.setattr("tuxtalk.whisper_vulkan.is_engine_installed", lambda m=None: True)

    def give_ultra(device):
        os.makedirs(pu.folder(), exist_ok=True)
        for name in (pu.GPU_FILE,) if device == "vulkan" else pu.CPU_FILES:
            open(os.path.join(pu.folder(), name), "w").close()
    return give_ultra


def test_offered_only_to_parakeet_users_on_the_original(disk):
    from tuxtalk.model_helper import parakeet_update_needed
    cfg = types.SimpleNamespace(model="parakeet-v3", device="vulkan")
    assert parakeet_update_needed(cfg) == "vulkan"
    cfg.device = "cpu"
    assert parakeet_update_needed(cfg) == "cpu"
    disk("cpu")
    assert parakeet_update_needed(cfg) is None          # already on Ultra there
    cfg.model, cfg.device = "small", "vulkan"
    assert parakeet_update_needed(cfg) is None          # dictates with Whisper


def test_the_offer_names_its_real_size(disk, monkeypatch):
    shown = []

    class Dialog:
        def __init__(self, **kwargs):
            shown.append(kwargs["text"])

        def format_secondary_text(self, text):
            shown.append(text)

        def add_button(self, label, response):
            shown.append(label)

        def set_default_response(self, r): pass
        def set_keep_above(self, on): pass
        def destroy(self): pass

        def run(self):
            return tray.Gtk.ResponseType.CANCEL

    monkeypatch.setattr("tuxtalk.ui_style.message_dialog", lambda **k: Dialog(**k))
    assert tray._offer_parakeet_update("vulkan") is False
    assert shown[0] == "Parakeet got more accurate"
    assert "Download (669 MB)" in shown and "Not Now" in shown


def test_enter_on_the_offer_means_not_now(disk, monkeypatch):
    """The offer opens by itself about 20 s after start; someone typing
    elsewhere who presses Enter as it comes up must not start a download."""
    class Dialog:
        def __init__(self, **kwargs): pass
        def format_secondary_text(self, text): pass
        def add_button(self, label, response): pass
        def set_keep_above(self, on): pass
        def destroy(self): pass

        def set_default_response(self, response):
            self.default = response

        def run(self):
            return self.default         # what Enter picks

    monkeypatch.setattr("tuxtalk.ui_style.message_dialog", lambda **k: Dialog())
    monkeypatch.setattr("tuxtalk.model_helper.download_parakeet_update",
                        lambda device: pytest.fail("Enter started the download"))
    assert tray._offer_parakeet_update("vulkan") is False


@pytest.fixture
def prefs_row(monkeypatch):
    """Preferences' Update Parakeet row with only what it needs."""
    from tuxtalk.prefs import PreferencesWindow
    w = PreferencesWindow.__new__(PreferencesWindow)
    w.window = None
    w.device_combo = tray.Gtk.ComboBoxText()
    w.device_combo.append("vulkan", "Vulkan")
    w.device_combo.set_active_id("vulkan")
    w.model_combo = tray.Gtk.ComboBoxText()
    w.model_combo.append("parakeet-v3", "Parakeet")
    w.model_combo.set_active_id("parakeet-v3")
    w.parakeet_update_row = tray.Gtk.Box()
    w.parakeet_update_btn = tray.Gtk.Button()
    need = {"device": "vulkan"}
    monkeypatch.setattr("tuxtalk.model_helper.parakeet_update_needed", lambda cfg: need["device"])
    monkeypatch.setattr("tuxtalk.model_helper.parakeet_update_size", lambda device: "669 MB")
    w.restarts, w.messages = [], []
    w.restart_service = lambda: w.restarts.append(True)
    monkeypatch.setattr("tuxtalk.prefs.show_message",
                        lambda parent, kind, title, text: w.messages.append(title))
    return w, need


def test_preferences_shows_update_parakeet_only_to_those_on_the_original(prefs_row):
    w, need = prefs_row
    w._update_parakeet_row("parakeet-v3")
    assert w.parakeet_update_row.get_visible()
    assert w.parakeet_update_btn.get_label() == "Update Parakeet (669 MB)"
    need["device"] = None
    w._update_parakeet_row("parakeet-v3")
    assert not w.parakeet_update_row.get_visible()


def test_update_parakeet_downloads_then_restarts_dictation(prefs_row, monkeypatch):
    w, need = prefs_row
    def download(device, parent):
        need["device"] = None           # Ultra is there now
        return True
    monkeypatch.setattr("tuxtalk.model_helper.download_parakeet_update", download)
    w._on_update_parakeet_clicked(None)
    assert w.restarts == [True] and not w.parakeet_update_row.get_visible()


def test_a_failed_update_says_so_and_changes_nothing(prefs_row, monkeypatch):
    w, need = prefs_row
    monkeypatch.setattr("tuxtalk.model_helper.download_parakeet_update", lambda d, p: None)
    w._update_parakeet_row("parakeet-v3")
    w._on_update_parakeet_clicked(None)
    assert w.messages == ["The download did not finish"] and w.restarts == []
    assert w.parakeet_update_row.get_visible()
