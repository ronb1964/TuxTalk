"""The download sizes shown are the real ones.

A model's size in millions of parameters (tiny 39M, small 244M) looks like
megabytes but is about half the real download, so the sizes shown are the
measured downloads.

Real sizes, read from Hugging Face on 2026-10-06 (decimal MB, whole repo for
the processor models, since TuxTalk downloads all of it):
  Systran/faster-whisper-*: tiny 78, base 148, small 486, medium 1531, large-v3 3091
  istupakov parakeet int8 files: 670
  ggerganov/whisper.cpp: tiny-q8 44, base-q8 82, small-q8 264, medium-q5 539, large-v3-q5 1081
  ggml-org/parakeet-GGUF q8: 669;  Vulkan engine: 24
"""
import pathlib
import re

from tuxtalk import recommend as r
from tuxtalk.model_helper import MODEL_DISPLAY_SIZES
from tuxtalk.whisper_vulkan import MODEL_FILES

ROOT = pathlib.Path(__file__).resolve().parent.parent

PROCESSOR = {"tiny": "78 MB", "base": "148 MB", "small": "486 MB",
             "medium": "1.5 GB", "large-v3": "3.1 GB", "parakeet-v3": "670 MB"}
GRAPHICS = {"tiny": "44 MB", "base": "82 MB", "small": "264 MB",
            "medium": "539 MB", "large-v3": "1.1 GB", "parakeet-v3": "669 MB"}

NVIDIA = r.Hardware("NVIDIA GeForce RTX 4070 SUPER", True)


def test_processor_sizes():
    assert MODEL_DISPLAY_SIZES == PROCESSOR


def test_graphics_sizes():
    assert {m: f[1] for m, f in MODEL_FILES.items()} == GRAPHICS


def test_first_run_card_download_lines():
    # Graphics card: the model plus the 24 MB engine.
    assert r.recommend("en", NVIDIA).download_text == "About 700 MB, downloaded once."
    assert r.recommend("en", r.Hardware(None)).download_text == "About 670 MB, downloaded once."
    assert r.recommend("ja", NVIDIA).download_text == "About 1.1 GB, downloaded once."
    assert r.recommend("ja", r.Hardware(None)).download_text == "About 490 MB, downloaded once."
    assert r.recommend("ja", NVIDIA, use_gpu=False).download_text == "About 490 MB, downloaded once."


def test_option_rows_follow_the_graphics_card_choice():
    on = {o.model: o.size for o in r.option_states("ja", NVIDIA, use_gpu=True)}
    off = {o.model: o.size for o in r.option_states("ja", NVIDIA, use_gpu=False)}
    assert on["small"] == "about 290 MB" and off["small"] == "about 490 MB"
    assert on["large-v3"] == "about 1.1 GB"
    assert on["parakeet-v3"] == off["parakeet-v3"] == "about 670 MB"


def test_help_readme_and_preferences_show_the_real_sizes(help_pages, prefs_window):
    from gtk_text import tree_text
    window = prefs_window()
    models = [row[1] for row in window.model_combo.get_model()]
    texts = {"Help": "\n".join(help_pages.values()),
             "README": (ROOT / "README.md").read_text(),
             "Preferences": "\n".join(tree_text(window.window) + models)}
    for name, text in texts.items():
        for old in ("39 MB", "74 MB", "244 MB", "769 MB"):
            assert not re.search(rf"\b{old}\b", text), (name, old)
