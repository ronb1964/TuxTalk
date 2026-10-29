"""The tray's Performance menu: three outcome presets from recommend.py.

Recommended for this computer / Lightest / Battery saver come from
recommend.presets(), so the menu, first run and the GNOME menu agree, and no
preset promises "GPU" while using the processor.
"""
import types

import pytest

from tuxtalk import recommend as r
from tuxtalk import tray as tray_mod
from tuxtalk import tray_menu, tray_presets

NVIDIA = r.Hardware("NVIDIA GeForce RTX 4070 SUPER", has_nvidia=True)


class FakeMenu:
    """The tray's menu, counting the times its dot is put back."""
    put_dot_back_soon = tray_menu.TrayMenu.put_dot_back_soon

    def __init__(self, tray):
        self.tray = tray

    def put_dot_back(self):
        self.tray.reverted += 1


class FakeTray:
    set_performance_preset = tray_mod.DictationTray.set_performance_preset
    _apply_preset = tray_mod.DictationTray._apply_preset
    _preset_files = tray_mod.DictationTray._preset_files
    _save_preset = tray_mod.DictationTray._save_preset

    def __init__(self):
        self.reverted = 0
        self.restarted = 0
        self._applying_preset = False
        self.menu = FakeMenu(self)

    def update_menu_display(self, is_running=None):
        pass

    def _emit_model_changed(self, model):
        pass

    def restart_service(self, _):
        self.restarted += 1


@pytest.fixture
def env(monkeypatch):
    cfg = types.SimpleNamespace(model="small", device="cpu", auto_timeout_enabled=True,
                                auto_timeout_minutes=5, dictation_language="en",
                                language_mode="auto", language="", vulkan_slower=False)
    saved = []
    monkeypatch.setattr("tuxtalk.config.load_config", lambda: cfg)
    monkeypatch.setattr("tuxtalk.config.save_config", lambda c: saved.append((c.model, c.device)))
    monkeypatch.setattr(r, "detect_hardware", lambda cfg=None: NVIDIA)
    monkeypatch.setattr("tuxtalk.model_helper.is_model_cached_fast", lambda m: True)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda t, b: None)
    monkeypatch.setattr("tuxtalk.whisper_vulkan.is_offered", lambda: True)
    return cfg, saved


def test_three_presets_in_order(env):
    assert [p.id for p in tray_presets.current_presets()] == ["recommended", "lightest", "battery"]


def test_recommended_runs_the_vulkan_setup_then_saves(env, monkeypatch):
    cfg, saved = env
    calls = []
    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.ensure_files",
                        lambda p, m, confirm: calls.append(("files", m)) or True)
    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.run_speed_check",
                        lambda p, m: calls.append(("speed", m)) or True)
    t = FakeTray()
    t.set_performance_preset("recommended")
    assert calls == [("files", "parakeet-v3"), ("speed", "parakeet-v3")]
    assert saved == [("parakeet-v3", "vulkan")] and t.restarted == 1


def test_recommended_falls_back_to_the_processor_when_it_is_faster(env, monkeypatch):
    cfg, saved = env
    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.ensure_files", lambda p, m, confirm: True)
    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.run_speed_check", lambda p, m: False)
    FakeTray().set_performance_preset("recommended")
    assert saved == [("parakeet-v3", "cpu")]


def test_large_v3_becomes_small_when_the_processor_is_faster(env, monkeypatch):
    """Japanese with a graphics card: Recommended is Large-v3 on it. Losing the
    speed check must not leave Large-v3 on the processor, the setup first run
    greys out ("Needs a graphics card")."""
    cfg, saved = env
    cfg.dictation_language = "ja"
    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.ensure_files", lambda p, m, confirm: True)
    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.run_speed_check", lambda p, m: False)
    FakeTray().set_performance_preset("recommended")
    assert saved == [("small", "cpu")]


def test_cancelling_the_download_changes_nothing(env, monkeypatch):
    """A cancelled download leaves the setup as it was."""
    cfg, saved = env
    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.ensure_files", lambda p, m, confirm: False)
    t = FakeTray()
    t.set_performance_preset("recommended")
    assert saved == [] and t.reverted == 1 and t.restarted == 0


def test_battery_saver_sets_its_timeout(env):
    cfg, saved = env
    FakeTray().set_performance_preset("battery")
    assert saved == [("tiny", "cpu")]
    assert (cfg.auto_timeout_enabled, cfg.auto_timeout_minutes) == (True, 2)


@pytest.mark.parametrize("preset_id", ["recommended", "lightest"])
def test_leaving_battery_saver_puts_the_timeout_back_to_5_minutes(env, monkeypatch, preset_id):
    """After Battery saver, the other presets must not keep its 2-minute
    auto-stop: back to this computer's default (on only with a battery)."""
    monkeypatch.setattr("tuxtalk.config.has_battery", lambda: True)
    cfg, saved = env
    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.ensure_files", lambda p, m, confirm: True)
    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.run_speed_check", lambda p, m: True)
    FakeTray().set_performance_preset("battery")
    FakeTray().set_performance_preset(preset_id)
    assert (cfg.auto_timeout_enabled, cfg.auto_timeout_minutes) == (True, 5)


def test_a_timeout_set_in_preferences_is_kept(env, monkeypatch):
    cfg, saved = env
    cfg.auto_timeout_minutes = 10
    FakeTray().set_performance_preset("lightest")
    assert cfg.auto_timeout_minutes == 10


@pytest.mark.parametrize("old_id", ["balanced", "accurate", "parakeet", "fastest", "custom"])
def test_old_or_unknown_ids_are_ignored(env, old_id):
    """An older GNOME extension sends the old ids."""
    cfg, saved = env
    t = FakeTray()
    t.set_performance_preset(old_id)
    assert saved == [] and t.restarted == 0


def test_the_tray_never_loads_a_model_on_its_main_loop(env, monkeypatch):
    """The tray owns the D-Bus name the dictation service calls into, from its
    input thread while it holds a grab on every keyboard. A model loaded on
    the tray's main loop (seconds and gigabytes for large-v3, just to ask
    "is it downloaded?") would freeze the user's keyboard system-wide. A
    downloaded model is recognised by its files."""
    from tuxtalk import model_helper

    def loads(*a, **k):
        pytest.fail("loaded a speech model on the main loop")
    monkeypatch.setattr("faster_whisper.WhisperModel", loads)
    monkeypatch.setattr(model_helper, "load_parakeet_model", loads)
    cfg, saved = env
    tray_menu.TrayMenu(tray_mod.DictationTray.__new__(tray_mod.DictationTray)).widget.destroy()
    t = FakeTray()
    t.set_performance_preset("lightest")
    assert saved == [("base", "cpu")] and t.restarted == 1


def test_current_preset_uses_match_preset(env):
    cfg, _ = env
    cfg.model, cfg.device = "parakeet-v3", "vulkan"
    assert tray_presets.preset_in_use() == "recommended"
    cfg.model, cfg.device = "medium", "cuda"
    assert tray_presets.preset_in_use() == "custom"


def test_dbus_exposes_presets_and_the_active_one():
    from tuxtalk.dbus_service import TuxTalkDBusService
    svc = TuxTalkDBusService.__new__(TuxTalkDBusService)
    svc.running_engine = None
    svc.claimed_hotkeys = []
    svc.app = types.SimpleNamespace(
        get_presets=lambda: [("recommended", "Recommended for this computer", "x")],
        current_preset=lambda: "recommended",
        config=types.SimpleNamespace(model="parakeet-v3", device="vulkan",
                                     auto_timeout_enabled=True, auto_timeout_minutes=5),
        is_recording=False, service_running=True)
    svc.IsRecording = lambda: False
    svc.IsServiceRunning = lambda: True
    assert list(svc.GetPresets()) == [("recommended", "Recommended for this computer", "x")]
    assert svc.GetStatus()["preset"] == "recommended"


def test_the_menu_offers_the_presets_of_this_computer(tray_menu):
    """The Performance submenu is built from recommend's presets, not a table
    of its own: Recommended, Lightest and Battery saver, then Custom."""
    performance = next(item for item in tray_menu.get_children()
                       if item.get_label() == "Performance")
    labels = [item.get_label() for item in performance.get_submenu().get_children()]
    starts = ("Recommended", "Lightest", "Battery saver", "", "Custom (opens Preferences)")
    assert len(labels) == len(starts)
    assert all(label.startswith(start) for label, start in zip(labels, starts)), labels


def test_moving_the_dot_always_clears_the_busy_flag(tray_menu_object, monkeypatch):
    """The flag is cleared in a finally: one failure must not leave every
    later preset click silently ignored."""
    menu, clicked = tray_menu_object

    def broken():
        raise RuntimeError("presets")
    monkeypatch.setattr(tray_presets, "preset_in_use", broken)
    with pytest.raises(RuntimeError):
        menu.put_dot_back()
    menu.preset_radios["lightest"].activate()
    assert clicked == ["lightest"]


def test_the_menu_moving_its_own_dot_applies_nothing(tray_menu_object, monkeypatch):
    """Putting the dot on the preset in use makes GTK send "activate" to the
    radio items; only a click applies a preset."""
    menu, clicked = tray_menu_object
    monkeypatch.setattr(tray_presets, "preset_in_use", lambda: "battery")
    menu.put_dot_back()
    assert menu.preset_radios["battery"].get_active() and clicked == []


@pytest.fixture
def tray_menu_object(env):
    """A real TrayMenu, recording the presets its radio items ask the tray for."""
    clicked = []
    tray = tray_mod.DictationTray.__new__(tray_mod.DictationTray)
    tray.set_performance_preset = clicked.append
    menu = tray_menu.TrayMenu(tray)
    yield menu, clicked
    menu.widget.destroy()


def test_choosing_the_active_preset_again_changes_nothing(env, monkeypatch):
    """The GNOME menu sends a click on the dotted item too, which must not
    rerun the download check and speed check, then restart the service."""
    cfg, saved = env
    cfg.model, cfg.device = "parakeet-v3", "vulkan"     # Recommended on NVIDIA
    checks = []
    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.ensure_files",
                        lambda p, m, confirm: checks.append(m) or True)
    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.run_speed_check",
                        lambda p, m: checks.append(("speed", m)) or True)
    t = FakeTray()
    t.set_performance_preset("recommended")
    assert checks == [] and saved == [] and t.restarted == 0


def test_dbus_presets_failure_is_an_empty_list_not_an_error(caplog):
    from tuxtalk.dbus_service import TuxTalkDBusService
    svc = TuxTalkDBusService.__new__(TuxTalkDBusService)

    def broken():
        raise RuntimeError("no presets")
    svc.app = types.SimpleNamespace(get_presets=broken)
    assert list(svc.GetPresets()) == []
    assert "no presets" in caplog.text


def test_leaving_battery_saver_on_a_desktop_turns_the_timeout_off(env, monkeypatch):
    monkeypatch.setattr("tuxtalk.config.has_battery", lambda: False)
    cfg, saved = env
    FakeTray().set_performance_preset("battery")
    FakeTray().set_performance_preset("lightest")
    assert (cfg.auto_timeout_enabled, cfg.auto_timeout_minutes) == (False, 5)


def test_a_second_preset_clicked_during_the_first_ones_download_waits(env, monkeypatch):
    """The download window runs a loop of its own and the menu still takes
    clicks: a second preset opened a second download window."""
    t = FakeTray()
    windows = []

    def ensure_files(parent, model, confirm):
        windows.append(model)
        t.set_performance_preset("lightest")   # clicked while this one downloads
        return True
    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.ensure_files", ensure_files)
    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.run_speed_check", lambda p, m: True)
    deferred = []                             # the dot goes back on a short timer
    monkeypatch.setattr(tray_mod.GLib, "timeout_add",
                        lambda ms, fn: deferred.append(ms) or fn())
    t.set_performance_preset("recommended")
    assert len(windows) == 1 and t.reverted == 1 and deferred == [150]
    t.set_performance_preset("lightest")        # once it's done, a click works again
    assert t.restarted == 2


def test_a_preset_that_fails_puts_the_dot_back(env, monkeypatch):
    """It failed after the dot had moved, and the dot stayed on the preset
    that wasn't applied."""
    t = FakeTray()
    monkeypatch.setattr(tray_mod.GLib, "timeout_add", lambda ms, fn: fn())

    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("tuxtalk.vulkan_setup_dialogs.ensure_files", broken)
    t.set_performance_preset("recommended")
    assert t.reverted == 1 and t.restarted == 0
