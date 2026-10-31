"""Existing users hear once that there's a better setup, only if there is."""
import types

import pytest

from tuxtalk import recommend as r
from tuxtalk import update_offers

NVIDIA = r.Hardware("NVIDIA GeForce RTX 4070 SUPER", True)


@pytest.fixture
def env(monkeypatch):
    cfg = types.SimpleNamespace(model="small", device="cpu", recommend_notice_shown=False,
                                dictation_language="en", language_mode="auto", language="",
                                vulkan_slower=False)
    notes, saved = [], []
    monkeypatch.setattr("tuxtalk.config.load_config", lambda: cfg)
    monkeypatch.setattr("tuxtalk.config.save_config", lambda c: saved.append(c.recommend_notice_shown))
    monkeypatch.setattr("tuxtalk.notify.notify", lambda t, b: notes.append(b))
    monkeypatch.setattr("tuxtalk.first_run_marker.is_first_run", lambda: False)
    monkeypatch.setattr("tuxtalk.service_launcher.find_service_pids", lambda: [4242])
    monkeypatch.setattr(r, "detect_hardware", lambda cfg=None: NVIDIA)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    # The hardware is looked at on a thread, and the notice given on GTK's:
    # both at once here.
    monkeypatch.setattr(update_offers, "threading", types.SimpleNamespace(Thread=_RunNow))
    monkeypatch.setattr(update_offers.GLib, "idle_add", lambda fn, *a: fn(*a))
    return cfg, notes, saved


class _RunNow:
    """A thread run at once, noting that it was one."""
    started = []

    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.started.append(self.target)
        self.target()


def test_shown_once_when_the_setup_differs(env):
    cfg, notes, saved = env
    update_offers.maybe_show_recommend_notice()
    assert len(notes) == 1 and "Parakeet on your NVIDIA GeForce RTX 4070 SUPER" in notes[0]
    assert "Performance" in notes[0] and saved == [True]
    cfg.recommend_notice_shown = True
    update_offers.maybe_show_recommend_notice()
    assert len(notes) == 1


def test_silent_when_already_on_the_recommendation(env):
    cfg, notes, saved = env
    cfg.model, cfg.device = "parakeet-v3", "vulkan"
    update_offers.maybe_show_recommend_notice()
    assert notes == [] and saved == [True]


def test_never_on_first_run_or_flatpak(env, monkeypatch):
    cfg, notes, saved = env
    monkeypatch.setattr("tuxtalk.first_run_marker.is_first_run", lambda: True)
    update_offers.maybe_show_recommend_notice()
    monkeypatch.setattr("tuxtalk.first_run_marker.is_first_run", lambda: False)
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    update_offers.maybe_show_recommend_notice()
    assert notes == []


def test_notice_wording():
    s = r.recommend("en", NVIDIA)
    assert update_offers.recommend_notice_text(s) == (
        "Parakeet on your NVIDIA GeForce RTX 4070 SUPER. Choose Performance → Recommended "
        "for this computer in the TuxTalk menu to switch.")


def test_waits_for_the_dictation_service(env, monkeypatch):
    """Spec: the notice comes once the service is up, not on a fixed timer."""
    cfg, notes, saved = env
    monkeypatch.setattr("tuxtalk.service_launcher.find_service_pids", lambda: [])
    assert update_offers.maybe_show_recommend_notice() is True       # ask again later
    assert notes == [] and saved == []
    monkeypatch.setattr("tuxtalk.service_launcher.find_service_pids", lambda: [4242])
    assert update_offers.maybe_show_recommend_notice() is False
    assert len(notes) == 1


def test_the_hardware_is_looked_at_off_gtks_thread(env, monkeypatch):
    """nvidia-smi can take its full 5 seconds: on GTK's thread, the tray
    froze while it ran."""
    _RunNow.started.clear()
    looked = []
    monkeypatch.setattr(r, "detect_hardware",
                        lambda cfg=None: looked.append(list(_RunNow.started)) or NVIDIA)
    update_offers.maybe_show_recommend_notice()
    assert looked and looked[0], "looked at the hardware before any thread started"
