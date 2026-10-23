"""Help and the README describe the app as it is.

Each test keeps out a description that no longer fits: the old first run (an
NVIDIA-only CUDA offer), Whisper Small as the recommended model, a red
recording indicator (it's a cyan orb by default), Fix a Word under a
"Voice Commands" tab (it's "Commands"), tray items that no longer exist, a
"Fast & Accurate" preset, and setting names that were never real.
"""
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()


@pytest.fixture
def help_text(help_pages):
    """Every tab of Help, as one text."""
    return "\n".join(help_pages.values())


def test_first_run_is_the_recommended_setup(help_text):
    assert "Offers to use your NVIDIA graphics card" not in help_text
    assert "recommends a setup for your computer" in help_text
    assert "it asks which one you want" not in README


def test_parakeet_not_small_is_recommended(help_text):
    assert "General use (recommended)" not in help_text
    assert "| **small** | 244 MB | Balanced | Very Good | **Recommended** |" not in README
    assert 'Start with "small"' not in README


def test_no_red_indicator_or_dimmed_icon(help_text):
    assert "red microphone icon" not in help_text
    assert "Red recording indicator" not in help_text
    assert "dimmed = stopped" not in help_text
    assert "Preferences → Audio" in help_text


def test_fix_a_word_lives_on_the_words_tab(help_text):
    assert "Preferences → Voice Commands" not in help_text
    assert "Preferences → Words → Fix a Word" in help_text


def test_quote_lines_show_curly_quotes(help_text):
    assert 'Smart quotes (" " instead of " ")' not in help_text
    assert "open quotes for “" in help_text
    assert "close quotes for ”" in help_text


def test_no_menu_items_or_presets_that_do_not_exist(help_text):
    assert "Stop Service then Start Service" not in help_text
    assert "Fast &amp; Accurate" not in README and "Fast & Accurate" not in README
    assert "98 point 6" not in help_text          # a model's habit, not something TuxTalk does


def test_voice_commands_tab_mentions_english_and_self_corrections(help_pages):
    commands = next(text for title, text in help_pages.items() if "Commands" in title)
    assert "English words" in commands
    assert "no wait" in commands


def test_readme_config_example_uses_real_setting_names():
    from tuxtalk.config import Settings
    block = README.split("```toml", 1)[1].split("```", 1)[0]
    for line in block.splitlines():
        key = line.split("=", 1)[0].strip()
        if key and not key.startswith("#"):
            assert hasattr(Settings, key), key
    assert 'model = "parakeet-v3"' in block
