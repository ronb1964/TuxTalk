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


def _tips(monkeypatch, flatpak=False, **settings):
    """The Tips section's text, as Help shows it with *settings*."""
    from tuxtalk import config, help_text
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(**settings))
    if flatpak:
        monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    else:
        monkeypatch.delenv("FLATPAK_ID", raising=False)
    return next(text for title, text in help_text.tabs() if "Tips" in title)


def test_tips_name_your_own_keys(monkeypatch):
    """Help names keys from the settings: someone on F6 is never told F8."""
    tips = _tips(monkeypatch, hotkey="F6", toggle_hotkey="F7", paste_last_hotkey="Ctrl+Alt+V")
    assert "fix that word" in tips and "<b>F6</b>" in tips
    assert "double-tap <b>F7</b>" in tips and "<b>Ctrl+Alt+V</b> types your last" in tips
    assert "F8" not in tips and "F9" not in tips and "Ctrl+Alt+P" not in tips


def test_tips_say_what_helps_in_a_noisy_room(monkeypatch):
    tips = _tips(monkeypatch)
    for advice in ("headset", "noise filters", "EasyEffects", "Preferences → Audio",
                   "Preferences → Words", "Right Alt", "AltGr", "press enter", "press tab",
                   "Filling in a form", "Recent Dictations"):
        assert advice in tips, advice


def test_tips_leave_out_what_a_setting_turned_off_or_the_flatpak_lacks(monkeypatch):
    """No Paste Last Dictation key: no line for it. On the Flatpak the
    desktop assigns the keys, so Preferences → General doesn't pick them."""
    assert "types your last" not in _tips(monkeypatch, paste_last_hotkey="")
    tips = _tips(monkeypatch, flatpak=True)
    assert "Right Alt" not in tips and "System Settings → Keyboard → Shortcuts" in tips


def test_problems_explains_letters_that_come_out_wrong(help_pages):
    """Caps Lock swaps every case, and TuxTalk can't read its state; COSMIC
    doesn't say which of several layouts is on. Neither can be fixed from
    TuxTalk, so Problems says what to do."""
    problems = next(text for title, text in help_pages.items() if "Problems" in title)
    assert "Caps Lock is on" in problems
    assert "COSMIC" in problems and "keyboard layout" in problems
