"""Preferences warns about model settings that don't do what they say.

Parakeet with Device CUDA quietly runs on the processor, and Parakeet
ignores a language it doesn't know. Both get a warning.
"""
import pytest

from tuxtalk.parakeet_engine import PARAKEET_LANGUAGES
from tuxtalk.prefs_speech import model_setting_warning


def test_parakeet_has_its_25_languages():
    assert len(PARAKEET_LANGUAGES) == 25
    assert {"en", "de", "uk", "ru", "mt"} <= PARAKEET_LANGUAGES
    assert "ja" not in PARAKEET_LANGUAGES


def test_parakeet_on_cuda_points_to_vulkan():
    msg = model_setting_warning("parakeet-v3", "cuda", "auto", "")
    assert "processor" in msg and "Vulkan" in msg


def test_parakeet_with_an_unknown_language_points_to_whisper():
    msg = model_setting_warning("parakeet-v3", "cpu", "manual", "ja", "Japanese")
    assert "Japanese" in msg and "Whisper" in msg


def test_both_problems_are_listed():
    msg = model_setting_warning("parakeet-v3", "cuda", "manual", "ja", "Japanese")
    assert "Vulkan" in msg and "Japanese" in msg


@pytest.mark.parametrize("args", [
    ("parakeet-v3", "vulkan", "auto", ""),         # the good setup
    ("parakeet-v3", "cpu", "manual", "uk", "Ukrainian"),
    ("parakeet-v3", "cpu", "auto", "ja"),          # auto mode never uses the language
    ("large-v3", "cuda", "manual", "ja", "Japanese"),
    ("small", "cpu", "manual", "ja", "Japanese"),
])
def test_fine_combinations_say_nothing(args):
    assert model_setting_warning(*args) == ""
