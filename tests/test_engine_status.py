"""The tray and GNOME menu say what really runs, not just what's configured.

After a fallback the settings no longer match what runs, so the Device line
could say "GPU (Vulkan)" while the processor does the work. The dictation
service reports the model and device it actually loaded (NotifyRunningEngine).
"""
import types

import pytest

from tuxtalk.dbus_service import TuxTalkDBusService
from tuxtalk.recommend import engine_status
from tuxtalk.tray import preset_notice


@pytest.mark.parametrize("cfg_model,cfg_device,running,expected", [
    ("parakeet-v3", "vulkan", ("parakeet-v3", "vulkan"), ("parakeet-v3", "GPU (Vulkan)")),
    ("parakeet-v3", "vulkan", ("parakeet-v3", "cpu"), ("parakeet-v3", "CPU (Vulkan didn't start)")),
    ("large-v3", "cuda", ("large-v3", "cpu"), ("large-v3", "CPU (CUDA didn't start)")),
    ("parakeet-v3", "cuda", None, ("parakeet-v3", "CPU (Parakeet can't use CUDA)")),
    ("parakeet-v3", "cuda", ("parakeet-v3", "cpu"), ("parakeet-v3", "CPU (Parakeet can't use CUDA)")),
    ("small", "cpu", None, ("small", "CPU")),
    ("large-v3", "vulkan", None, ("large-v3", "GPU (Vulkan)")),   # not reported yet: the setting
    ("medium", "cuda", ("small", "cuda"), ("small", "GPU (CUDA)")),  # model fallback shows
])
def test_status_lines(cfg_model, cfg_device, running, expected):
    assert engine_status(cfg_model, cfg_device, running) == expected


def test_parakeet_preset_on_a_gpu_machine_says_how_to_use_it():
    text = preset_notice("parakeet-v3", "cpu", gpu_offered=True)
    assert "on the processor" in text and "Vulkan" in text


def test_no_gpu_hint_without_a_usable_graphics_chip():
    assert "Vulkan" not in preset_notice("small", "cpu", gpu_offered=False)


def test_gpu_preset_says_which_engine():
    assert "graphics card (CUDA)" in preset_notice("large-v3", "cuda", True)


@pytest.fixture
def svc(monkeypatch):
    # Parakeet's Vulkan files "downloaded", so the setting alone says vulkan.
    monkeypatch.setattr("tuxtalk.whisper_vulkan.is_installed", lambda m: True)
    s = TuxTalkDBusService.__new__(TuxTalkDBusService)    # no real bus
    s.app = types.SimpleNamespace(config=types.SimpleNamespace(model="parakeet-v3", device="vulkan"))
    s.running_engine = None
    s.claimed_hotkeys = []
    s.changed = []
    s.ModelChanged = lambda m: s.changed.append(m)
    return s


def test_the_service_report_wins_over_the_settings(svc):
    assert svc.GetDeviceType() == "vulkan"
    svc.NotifyRunningEngine("parakeet-v3", "cpu")
    assert svc.GetDeviceType() == "cpu" and svc.GetCurrentModel() == "parakeet-v3"
    assert svc.changed == ["parakeet-v3"], "the GNOME extension refreshes on ModelChanged"


def test_a_stopped_service_falls_back_to_the_settings(svc):
    svc.NotifyRunningEngine("small", "cpu")
    svc.clear_running_engine()
    assert svc.GetCurrentModel() == "parakeet-v3" and svc.GetDeviceType() == "vulkan"


@pytest.fixture
def loads(monkeypatch):
    """The service's model loading with every engine faked; returns the
    engine the service then says is running."""
    from tuxtalk import app, config, cuda_helper, model_helper, parakeet_gpu, whisper_vulkan
    monkeypatch.setattr(app, "_running_engine", None)
    monkeypatch.setattr(app, "_notify", lambda *a: None)
    monkeypatch.setattr(whisper_vulkan, "is_installed", lambda m: True)
    monkeypatch.setattr(whisper_vulkan, "find_device", lambda: 0)
    monkeypatch.setattr(whisper_vulkan, "VulkanWhisperModel", lambda m, d: "whisper on vulkan")
    monkeypatch.setattr(parakeet_gpu, "ParakeetGpuModel", lambda d: "parakeet on vulkan")
    monkeypatch.setattr(cuda_helper, "has_cuda_libraries", lambda: True)
    monkeypatch.setattr(config, "load_config", config.Settings)
    monkeypatch.setattr(config, "save_config", lambda cfg: None)

    def load(model, device, cuda_fails=False):
        def download(name, device, **k):
            if device == "cuda" and cuda_fails:
                raise RuntimeError("CUDA driver missing")
            return f"{name} on {device}"
        monkeypatch.setattr(model_helper, "download_model_with_progress", download)
        assert app.build_model(config.Settings(model=model, device=device))
        return app._running_engine
    return load


@pytest.mark.parametrize("model,device,cuda_fails,running", [
    ("small", "vulkan", False, ("small", "vulkan")),
    ("parakeet-v3", "vulkan", False, ("parakeet-v3", "vulkan")),
    ("small", "cpu", False, ("small", "cpu")),
    ("parakeet-v3", "cuda", False, ("parakeet-v3", "cpu")),
    ("large-v3", "cuda", False, ("large-v3", "cuda")),
    ("large-v3", "cuda", True, ("large-v3", "cpu")),
])
def test_the_service_records_every_load_path(loads, model, device, cuda_fails, running):
    """Each way a model loads records what really runs."""
    assert loads(model, device, cuda_fails) == running


def test_the_service_tells_the_tray_once_the_model_is_loaded(service_main):
    started = service_main()
    assert started.index("loads the speech model") < started.index("tells the tray what runs")


def test_the_tray_is_told_what_was_loaded(monkeypatch):
    from tuxtalk import app
    told = []
    monkeypatch.setattr(app, "_running_engine", ("small", "cpu"))
    monkeypatch.setattr(app, "_get_tray_dbus_proxy", lambda: types.SimpleNamespace(
        NotifyRunningEngine=lambda *engine, **k: told.append(engine)))
    app._notify_tray_running_engine()
    assert told == [("small", "cpu")]


@pytest.mark.parametrize("cfg_model,cfg_device,running,expected", [
    ("parakeet-v3", "vulkan", ("parakeet-v3", "vulkan"), "Parakeet on the graphics card (Vulkan)"),
    ("parakeet-v3", "vulkan", ("parakeet-v3", "cpu"), "Parakeet on the processor (Vulkan didn't start)"),
    ("small", "cpu", None, "Whisper Small on the processor"),
    ("large-v3", "cuda", ("large-v3", "cuda"), "Whisper Large-v3 on the graphics card (CUDA)"),
])
def test_the_menus_say_what_runs_in_one_line(cfg_model, cfg_device, running, expected):
    """"Active Model" and "Device" were two lines; one sentence says both."""
    from tuxtalk.recommend import engine_line
    assert engine_line(cfg_model, cfg_device, running) == expected


def test_the_gnome_menu_gets_the_trays_line(svc):
    svc.app.current_preset = lambda: "custom"
    svc.NotifyRunningEngine("parakeet-v3", "cpu")
    assert svc.GetStatus()["engine"] == "Parakeet on the processor (Vulkan didn't start)"


def test_the_dictation_services_status_doesnt_load_the_tray():
    """The dictation service runs the D-Bus class too, when it stands in for
    a crashed tray: its status line must not import the whole tray (and its
    indicator library) into the service. Run apart, with a clean import list."""
    import subprocess
    import sys
    script = (
        "import sys, types\n"
        "from tuxtalk.dbus_service import TuxTalkDBusService\n"
        "s = TuxTalkDBusService.__new__(TuxTalkDBusService)\n"
        "s.app = types.SimpleNamespace(config=types.SimpleNamespace(model='small', device='cpu'),\n"
        "                              current_preset=lambda: 'custom')\n"
        "s.running_engine, s.claimed_hotkeys = None, []\n"
        "assert s.GetStatus()['engine'] == 'Whisper Small on the processor'\n"
        "assert 'tuxtalk.tray' not in sys.modules, 'the tray was imported'\n")
    ran = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr[-800:]
