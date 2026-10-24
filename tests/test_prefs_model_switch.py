"""Switching the model in Preferences, General, must not crash it.

Ron, 2026-10-20 (0.17 test): Parakeet to small crashed Preferences. Going
between Parakeet and Whisper refills the Language dropdown, whose "changed"
came back to the handler that started the refill, over and over; with CUDA
selected, so did the CUDA entry's new label.
"""
from dataclasses import asdict

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from tuxtalk import prefs, prefs_speech, prefs_words  # noqa: E402
from tuxtalk.config import Settings  # noqa: E402


def _build(monkeypatch, model, device):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    monkeypatch.setattr(prefs.PreferencesWindow, "load_config",
                        lambda self: {**asdict(Settings()), "model": model, "device": device})
    monkeypatch.setattr(prefs.PreferencesWindow, "cuda_files_present", lambda self: True)
    monkeypatch.setattr(prefs_words, "load_custom_commands", lambda: {})
    monkeypatch.setattr(prefs_words.WordsTab, "_watch_custom_commands_file",
                        lambda self: None)
    monkeypatch.setattr(prefs.PreferencesWindow, "_show_window", lambda self: None)
    return prefs.PreferencesWindow()


def _speech(w):
    return w.general_tab.speech


def _pick_model(w, model):
    combo = _speech(w).model_combo
    for i, row in enumerate(combo.get_model()):
        if row[0] == model:
            combo.set_active(i)
            return
    raise AssertionError(f"no {model} in the Model dropdown")


@pytest.mark.parametrize("start, to, device", [
    ("parakeet-v3", "small", "vulkan"),
    ("parakeet-v3", "small", "cuda"),
    ("small", "parakeet-v3", "cuda"),
])
def test_switching_between_parakeet_and_whisper_settles(monkeypatch, start, to, device):
    w = _build(monkeypatch, start, device)
    hints = []      # the calls that do the work, not the ones a refill turns away
    original = prefs_speech.SpeechSection.update_hint

    def counted(self):
        if not self.language.filling:
            hints.append(1)
        if len(hints) < 50:
            original(self)
    monkeypatch.setattr(prefs_speech.SpeechSection, "update_hint", counted)
    _pick_model(w, to)
    assert len(hints) < 10
    assert _speech(w).language.filled_for == to
    w.window.destroy()


def test_a_new_several_languages_label_doesnt_reopen_the_picker(monkeypatch):
    """Relabelling the selected "Several: ..." entry fires "changed"; while it
    does, the Language handler must see a refill, not a new pick (Codex
    review of 0.17, the same loop as the CUDA label)."""
    w = _build(monkeypatch, "small", "cpu")
    monkeypatch.setitem(w.config, "auto_languages", "en,es")
    monkeypatch.setattr(Gtk.Dialog, "run", lambda self: Gtk.ResponseType.OK)
    seen = []
    language = _speech(w).language
    language.combo.get_model().connect("row-changed", lambda *_: seen.append(language.filling))
    assert language.pick_several() is True
    assert seen and all(seen)
    w.window.destroy()
