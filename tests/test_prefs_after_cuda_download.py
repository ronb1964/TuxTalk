"""After the CUDA download: switch for real, and never slow Parakeet down.

The download must not save device = "cuda" by itself: that also moves the
baseline Preferences diffs against, so OK finds no change and never restarts
the service. And Parakeet can't use CUDA, so switching it there would move it
from the graphics card to the processor. Parakeet users are asked which Whisper
model to use on CUDA instead, and any switch goes through Apply.
"""
from types import SimpleNamespace

import pytest


@pytest.fixture
def prefs():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "3.0")
    from tuxtalk import prefs
    return prefs


def _stub(prefs, model, answer, monkeypatch):
    stub = SimpleNamespace(config={"model": model, "device": "vulkan"}, window=None,
                           applied=[], asked=[])
    stub._ask_whisper_model_for_cuda = lambda: stub.asked.append(True) or answer
    stub.on_apply = lambda button: stub.applied.append(dict(stub.config))
    stub._set_choice = lambda key, value: stub.config.__setitem__(key, value)
    return stub


def test_a_whisper_model_switches_to_cuda_through_apply(prefs, monkeypatch):
    stub = _stub(prefs, "small", "unused", monkeypatch)
    prefs.PreferencesWindow._after_cuda_download(stub)
    assert stub.asked == []
    assert stub.applied == [{"model": "small", "device": "cuda"}]


@pytest.mark.parametrize("picked", ["small", "large-v3", "tiny"])
def test_parakeet_is_asked_and_moves_to_the_model_picked(prefs, monkeypatch, picked):
    """Any Whisper model can be picked, not only Small, so wanting another
    one doesn't mean downloading Small first."""
    stub = _stub(prefs, "parakeet-v3", picked, monkeypatch)
    prefs.PreferencesWindow._after_cuda_download(stub)
    assert len(stub.asked) == 1
    assert stub.applied == [{"model": picked, "device": "cuda"}]


def test_parakeet_can_stay_put(prefs, monkeypatch):
    stub = _stub(prefs, "parakeet-v3", None, monkeypatch)
    prefs.PreferencesWindow._after_cuda_download(stub)
    assert stub.applied == []
    assert stub.config == {"model": "parakeet-v3", "device": "vulkan"}


def test_the_download_no_longer_writes_the_config_itself():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "src/tuxtalk/cuda_helper.py").read_text()
    assert "Automatically enable GPU mode" not in src


def test_the_question_offers_every_whisper_model_with_large_v3_picked(prefs, monkeypatch):
    if not prefs.Gtk.init_check()[0]:
        pytest.skip("no display")
    shown = {}

    def run_and_record(box):
        # On a short screen the content sits in a scroller (ui_style), so
        # look through all of it.
        def widgets(w):
            yield w
            if isinstance(w, prefs.Gtk.Container):
                for child in w.get_children():
                    yield from widgets(child)
        radios = [w for w in widgets(box.get_content_area())
                  if isinstance(w, prefs.Gtk.RadioButton)]
        shown["labels"] = [r.get_label() for r in radios]
        shown["active"] = [r.get_label() for r in radios if r.get_active()]
        return prefs.Gtk.ResponseType.YES

    monkeypatch.setattr(prefs.Gtk.Dialog, "run", run_and_record)
    stub = SimpleNamespace(window=None)
    chosen = prefs.PreferencesWindow._ask_whisper_model_for_cuda(stub)
    assert chosen == "large-v3"
    assert [label.split(",")[0] for label in shown["labels"]] == \
        ["Tiny", "Base", "Small", "Medium", "Large-v3"]
    assert shown["active"][0].startswith("Large-v3") and "3.1 GB" in shown["active"][0]


# --- CUDA already downloaded, picked in Device with Parakeet -----------------

def _picker(prefs, answer):
    """Device → CUDA with the files already there. Switching straight away
    would leave Parakeet running on the processor under "CUDA"."""
    stub = SimpleNamespace(config={"model": "parakeet-v3", "device": "vulkan"},
                           _device_before="vulkan", asked=[])
    stub._ask_whisper_model_for_cuda = lambda: stub.asked.append(True) or answer
    stub._set_choice = lambda key, value: stub.config.__setitem__(key, value)
    return stub


def test_picking_cuda_on_parakeet_asks_for_a_whisper_model(prefs):
    stub = _picker(prefs, "large-v3")
    prefs.PreferencesWindow._finish_cuda_for_parakeet(stub, None)
    assert stub.asked == [True]
    # Chosen, not applied yet: OK or Apply downloads the model and restarts.
    assert stub.config == {"model": "large-v3", "device": "cuda"}


def test_keep_parakeet_puts_the_device_back(prefs):
    stub = _picker(prefs, None)
    prefs.PreferencesWindow._finish_cuda_for_parakeet(stub, None)
    assert stub.config == {"model": "parakeet-v3", "device": "vulkan"}
