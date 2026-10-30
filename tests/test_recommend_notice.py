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
    return cfg, notes, saved


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
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TalkType")
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
