"""The Device dropdown says what each choice really does.

On NVIDIA, CUDA is always listed under Device, with its download size until
it's installed, so NVIDIA owners find it there. With Parakeet it says that
Parakeet runs on the processor rather than "CUDA (GPU)".
"""
import pytest

from tuxtalk.prefs_speech import SpeechSection, device_label


def test_cuda_not_downloaded_names_the_download():
    assert device_label("cuda", "small", cuda_ready=False) == "CUDA (NVIDIA, 580 MB download)"


def test_cuda_installed_is_plain():
    assert device_label("cuda", "large-v3", cuda_ready=True) == "CUDA (NVIDIA)"


@pytest.mark.parametrize("ready", [True, False])
def test_cuda_with_parakeet_says_it_runs_on_the_processor(ready):
    assert "Parakeet runs on the processor" in device_label("cuda", "parakeet-v3", ready)


def test_cuda_with_parakeet_still_names_the_download():
    assert "580 MB" in device_label("cuda", "parakeet-v3", cuda_ready=False)
    assert "580 MB" not in device_label("cuda", "parakeet-v3", cuda_ready=True)


def test_other_devices_keep_their_names():
    from tuxtalk.whisper_vulkan import DEVICE_LABEL
    assert device_label("cpu", "parakeet-v3", True) == "CPU"
    assert device_label("vulkan", "parakeet-v3", True) == DEVICE_LABEL


def test_relabelling_the_cuda_entry_again_changes_nothing():
    """Ron, 2026-10-20: CUDA selected, a Whisper model picked. Rewriting the
    selected entry's label makes the shown dropdown fire "changed", whose
    handler relabels it again: unless an unchanged label is left alone, that
    never stops, and Preferences crashed (RecursionError)."""
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    if not Gtk.init_check()[0]:
        pytest.skip("no display")

    picks = []

    class Prefs:
        _cuda_ready = True
        device_combo = Gtk.ComboBoxText()

        def _on_device_changed(self, combo):
            picks.append(combo.get_active_id())
    prefs = Prefs()
    prefs.device_combo.append("cuda", device_label("cuda", "parakeet-v3", True))
    prefs.device_combo.set_active_id("cuda")
    prefs.device_combo.connect("changed", prefs._on_device_changed)
    writes = []
    prefs.device_combo.get_model().connect("row-changed", lambda *_: writes.append(1))
    SpeechSection._relabel_cuda_option(prefs, "small")
    SpeechSection._relabel_cuda_option(prefs, "small")
    assert prefs.device_combo.get_active_text() == "CUDA (NVIDIA)"
    assert len(writes) == 1
    # Nor does a new label count as picking CUDA (review of 0.17): the Device
    # handler is held back while it's written, whatever GTK signals.
    prefs.device_combo.get_model().connect(
        "row-changed", lambda *_: prefs.device_combo.emit("changed"))
    SpeechSection._relabel_cuda_option(prefs, "parakeet-v3")
    assert picks == []
