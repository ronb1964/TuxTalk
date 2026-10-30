"""Parakeet engine wiring.

Parakeet is offered as one more model name ("parakeet-v3") so that every model
picker, the config validator and the download dialogs handle it without their
own special cases. These tests pin down the places where it genuinely differs
from Whisper: how "is it downloaded?" is answered and how dictation audio
reaches it. Where its files come from is tests/test_parakeet_ultra.py.
"""
from types import SimpleNamespace

import numpy as np
from tuxtalk import engine
from tuxtalk import pipeline
from tuxtalk.beeps import CANCEL_BEEP


def test_parakeet_is_offered_valid_and_downloadable():
    from tuxtalk.config import VALID_MODELS
    from tuxtalk.model_helper import MODEL_DISPLAY_SIZES, OFFERED_MODELS
    from tuxtalk.parakeet_engine import PARAKEET_MODEL

    assert PARAKEET_MODEL in OFFERED_MODELS
    assert PARAKEET_MODEL in VALID_MODELS
    assert PARAKEET_MODEL in MODEL_DISPLAY_SIZES


def test_every_offered_model_has_a_label_on_every_picker(prefs_window):
    """The picker indexes a label dict by model id; a missing key crashes the
    screen as it opens. (First run's card has no full picker.)"""
    from tuxtalk.model_helper import OFFERED_MODELS
    rows = {row[0]: row[1] for row in prefs_window().general_tab.speech.model_combo.get_model()}
    assert set(rows) == set(OFFERED_MODELS)
    assert all(label.strip() for label in rows.values()), rows


def test_whisper_downloads_are_not_filtered(monkeypatch):
    import huggingface_hub
    from tuxtalk import model_helper

    monkeypatch.setattr(
        huggingface_hub, "list_repo_tree",
        lambda repo_id, recursive=True: [SimpleNamespace(path="model.bin", size=10),
                                         SimpleNamespace(path="README.md", size=1)],
    )
    files = model_helper._files_to_download("Systran/faster-whisper-small")
    assert {p for p, _ in files} == {"model.bin", "README.md"}


def test_cache_checks_use_parakeet_files(monkeypatch):
    from tuxtalk import model_helper

    monkeypatch.setattr(model_helper, "_parakeet_cached_dir", lambda: None)
    assert model_helper.is_model_cached_fast("parakeet-v3") is False

    monkeypatch.setattr(model_helper, "_parakeet_cached_dir", lambda: "/some/dir")
    assert model_helper.is_model_cached_fast("parakeet-v3") is True


def test_dictation_audio_goes_to_parakeet(monkeypatch):
    from tuxtalk.parakeet_engine import ParakeetModel

    received = []
    fake = ParakeetModel.__new__(ParakeetModel)  # skip loading the real model
    fake.recognize = lambda audio: received.append(audio) or "Hello there."
    monkeypatch.setattr(engine, "model", fake, raising=False)

    audio = np.zeros(16000, dtype=np.float32)
    assert engine.transcribe(audio, language="de") == "Hello there."
    assert received and received[0] is audio


def test_parakeet_silence_returns_none(monkeypatch):
    from tuxtalk.parakeet_engine import ParakeetModel

    fake = ParakeetModel.__new__(ParakeetModel)
    fake.recognize = lambda audio: ""
    monkeypatch.setattr(engine, "model", fake, raising=False)

    assert engine.transcribe(np.zeros(8000, dtype=np.float32), language=None) is None


def test_the_download_question_names_the_model_as_the_menus_do(monkeypatch, message_boxes):
    """It said "Download Small Model?" where the menus said "Whisper Small"
    (review of 0.17)."""
    from tuxtalk import model_helper
    message_boxes.answer = model_helper.Gtk.ResponseType.CANCEL
    monkeypatch.setattr(model_helper, "is_model_cached_fast", lambda m: False)
    assert model_helper.download_model_with_progress("small") is None
    assert [box.title for box in message_boxes.shown] == ["Download Whisper Small Model?"]


def test_menus_show_the_device_parakeet_really_uses():
    """Picking Parakeet in Preferences can leave device = "cuda" in the config;
    both the tray and the GNOME extension (via GetStatus) must say CPU."""
    from tuxtalk.parakeet_engine import effective_device
    assert effective_device("parakeet-v3", "cuda") == "cpu"
    assert effective_device("large-v3", "cuda") == "cuda"
    assert effective_device("small", "cpu") == "cpu"


def test_parakeet_is_on_the_graphics_chip_only_once_its_vulkan_files_are_there(monkeypatch):
    from tuxtalk import whisper_vulkan as wv
    from tuxtalk.parakeet_engine import effective_device
    monkeypatch.setattr(wv, "is_installed", lambda m: False)
    assert effective_device("parakeet-v3", "vulkan") == "cpu"
    monkeypatch.setattr(wv, "is_installed", lambda m: True)
    assert effective_device("parakeet-v3", "vulkan") == "vulkan"
    assert effective_device("parakeet-v3", "cuda") == "cpu"     # CUDA has no Parakeet

    from tuxtalk.dbus_service import TuxTalkDBusService
    svc = TuxTalkDBusService.__new__(TuxTalkDBusService)
    svc.app = SimpleNamespace(config=SimpleNamespace(model="parakeet-v3", device="cuda"))
    svc.running_engine, svc.claimed_hotkeys = None, []
    assert svc.GetStatus()["device"] == "cpu"


def _parakeet_saying(monkeypatch, text, nobody_talked):
    from tuxtalk import silence
    from tuxtalk.parakeet_engine import ParakeetModel
    fake = ParakeetModel.__new__(ParakeetModel)
    fake.recognize = lambda audio: text
    monkeypatch.setattr(engine, "model", fake, raising=False)
    monkeypatch.setattr(silence, "has_no_speech", lambda a, sr: nobody_talked)
    return engine.transcribe(np.zeros(16000, dtype=np.float32), language=None)


def test_parakeet_inventing_mm_hmm_in_silence_types_nothing(monkeypatch):
    """0.15.0 testing: an F9 tap with nothing said typed "Mm-hmm." into Konsole."""
    assert _parakeet_saying(monkeypatch, "Mm-hmm.", nobody_talked=True) is None


def test_parakeet_keeps_a_real_mm_hmm(monkeypatch):
    assert _parakeet_saying(monkeypatch, "Mm-hmm.", nobody_talked=False) == "Mm-hmm."


def test_parakeet_keeps_real_words_even_in_a_quiet_recording(monkeypatch):
    """Only stock fillers are judged, never a real sentence."""
    assert _parakeet_saying(monkeypatch, "Hello there.", nobody_talked=True) == "Hello there."


def test_nothing_recognized_plays_the_cancel_beep(monkeypatch):
    """A recording that types nothing ends with the cancel beep, not silence."""
    from tuxtalk import silence
    beeps = []
    monkeypatch.setattr("tuxtalk.beeps.beep", lambda on, *tone: beeps.append(tone))
    monkeypatch.setattr(silence, "is_speechless_tap", lambda a, sr: False)
    monkeypatch.setattr(engine, "transcribe", lambda *a: None)
    pipeline.transcribe_and_inject([np.ones(16000, dtype=np.int16).tobytes()], 16000, True, False, False)
    assert beeps == [CANCEL_BEEP]


def test_the_log_says_your_words_steered_parakeet_but_not_which_word(caplog):
    """Which word Parakeet wrote is something the user said: the log keeps
    none of that unless log_transcripts is on."""
    import logging
    from tuxtalk import parakeet_engine
    caplog.set_level(logging.INFO, logger=parakeet_engine.logger.name)
    parakeet_engine._log_word("ClaudeClaw", 2.5, kept=True)
    logged = " ".join(r.message for r in caplog.records)
    assert "one of your words" in logged
    assert "ClaudeClaw" not in logged
