"""large-v3 is only swapped for medium when it was meant to run on CUDA.

Checking only for TuxTalk's CUDA libraries would give an AMD or Intel user who
set up large-v3 on their graphics card (Vulkan) the medium model while the tray
said "Large".
"""
import types

import pytest

from tuxtalk import engine


def _cfg(device):
    return types.SimpleNamespace(model="large-v3", device=device)


@pytest.fixture
def no_cuda(monkeypatch):
    monkeypatch.setattr("tuxtalk.cuda_helper.has_cuda_libraries", lambda: False)
    monkeypatch.setattr("tuxtalk.notify.notify", lambda *a, **k: None)


def test_vulkan_keeps_large_v3(no_cuda):
    cfg = _cfg("vulkan")
    engine.guard_large_v3_without_cuda(cfg)
    assert cfg.model == "large-v3"


@pytest.mark.parametrize("device", ["cuda", "cpu"])
def test_without_cuda_libraries_it_still_falls_back(no_cuda, device):
    cfg = _cfg(device)
    engine.guard_large_v3_without_cuda(cfg)
    assert cfg.model == "medium"


def test_with_cuda_libraries_nothing_changes(monkeypatch):
    monkeypatch.setattr("tuxtalk.cuda_helper.has_cuda_libraries", lambda: True)
    cfg = _cfg("cuda")
    engine.guard_large_v3_without_cuda(cfg)
    assert cfg.model == "large-v3"


def test_other_models_are_untouched(no_cuda):
    cfg = types.SimpleNamespace(model="parakeet-v3", device="cpu")
    engine.guard_large_v3_without_cuda(cfg)
    assert cfg.model == "parakeet-v3"


def test_a_system_wide_cuda_install_keeps_large_v3(monkeypatch):
    """Preferences offers large-v3 when CUDA is installed system-wide (common
    on Arch). Checking only TuxTalk's own download would silently give those
    users medium, and could ask them to download it."""
    monkeypatch.setattr("tuxtalk.cuda_helper.has_talktype_cuda_libraries", lambda: False)
    monkeypatch.setattr("tuxtalk.cuda_helper.has_cuda_libraries", lambda: True)
    cfg = _cfg("cuda")
    engine.guard_large_v3_without_cuda(cfg)
    assert cfg.model == "large-v3"


def test_the_swap_is_told_to_the_user(monkeypatch):
    monkeypatch.setattr("tuxtalk.cuda_helper.has_cuda_libraries", lambda: False)
    notes = []
    monkeypatch.setattr("tuxtalk.notify.notify", lambda title, body: notes.append(f"{title}\n{body}"))
    engine.guard_large_v3_without_cuda(_cfg("cuda"))
    assert notes and "Medium" in notes[0]
    # The CUDA download is offered in General, Device; Advanced only shows status.
    assert "General, Device" in notes[0]
