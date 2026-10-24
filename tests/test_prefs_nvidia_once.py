"""nvidia-smi runs at most once per Preferences window.

The General tab, the Device dropdown, the GPU status line and the large-v3
check all need its answer; running it for each on the GTK thread delays the
window on a card in a low-power state.
"""
from types import SimpleNamespace

from tuxtalk import cuda_helper
from tuxtalk.prefs import PreferencesWindow


def test_the_card_is_detected_once(monkeypatch):
    calls = []
    monkeypatch.setattr(cuda_helper, "detect_nvidia_gpu",
                        lambda: calls.append(1) or "NVIDIA GeForce RTX 3080")
    d = SimpleNamespace()
    d.nvidia_gpu = lambda: PreferencesWindow.nvidia_gpu(d)

    assert PreferencesWindow.has_nvidia(d) is True
    assert PreferencesWindow.has_nvidia(d) is True
    assert d.nvidia_gpu() == "NVIDIA GeForce RTX 3080"
    assert len(calls) == 1


def test_no_card_is_remembered_too(monkeypatch):
    calls = []
    monkeypatch.setattr(cuda_helper, "detect_nvidia_gpu", lambda: calls.append(1) or "")
    d = SimpleNamespace()
    d.nvidia_gpu = lambda: PreferencesWindow.nvidia_gpu(d)

    assert PreferencesWindow.has_nvidia(d) is False
    assert PreferencesWindow.has_nvidia(d) is False
    assert len(calls) == 1
