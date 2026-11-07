"""Help and the README describe the app as it is.

Each test keeps out a description that no longer fits: the old first run (an
NVIDIA-only CUDA offer), Whisper Small as the recommended model, a red
recording indicator (it's a cyan orb by default), Fix a Word under a
"Voice Commands" tab (it's "Commands"), tray items that no longer exist, a
"Fast & Accurate" preset, and setting names that were never real.
"""
import re
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
    assert "open quote, begin quote\n“" in help_text
    assert "close quote, end quote, unquote\n”" in help_text


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


def test_help_says_how_a_source_install_and_the_aur_package_update():
    """Read from Help's markup (tabs()), where the bold and the command show."""
    from tuxtalk import help_text
    markup = "\n".join(text for _title, text in help_text.tabs())
    assert "<b>From source:</b>" in markup
    assert "<tt>yay -S tuxtalk-appimage</tt>" in markup
    assert "<tt>yay -S tuxtalk</tt>" in markup


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


@pytest.mark.parametrize("flatpak, says", [(None, True), ("io.github.ronb1964.TuxTalk", False)])
def test_help_tells_what_esc_does_only_where_esc_is_heard(monkeypatch, flatpak, says):
    """The Flatpak's shortcuts portal never hears Esc."""
    from tuxtalk import help_text
    if flatpak:
        monkeypatch.setenv("FLATPAK_ID", flatpak)
    else:
        monkeypatch.delenv("FLATPAK_ID", raising=False)
    mistakes = dict(help_text.tabs())["Mistakes"]
    assert ("Changed your mind as you let go" in mistakes) is says
    assert "delete everything" not in mistakes


def test_help_says_a_fast_computer_can_beat_esc(monkeypatch):
    """On a fast graphics card a short dictation is in before Esc can be
    reached; without saying so, Esc looked broken."""
    from tuxtalk import help_text
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    mistakes = " ".join(dict(help_text.tabs())["Mistakes"].split())
    assert "Esc can only stop what hasn't happened yet" in mistakes
    assert "delete last sentence" in mistakes


def test_help_says_auto_send_leaves_terminals_alone(monkeypatch):
    from tuxtalk import help_text
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    pages = dict(help_text.tabs())
    chatting = " ".join(pages["Chatting"].split())
    terminals = " ".join(pages["Terminals"].split())
    assert "In a terminal it never presses Enter either" in chatting
    assert "presses Enter in a terminal too" not in terminals
    assert "doesn't press Enter in a terminal" in terminals


def _writing(monkeypatch, **settings):
    """The Writing section's text, as Help shows it with *settings*."""
    from tuxtalk import config, help_text
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(**settings))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    return next(text for title, text in help_text.tabs() if title == "Writing")


def test_help_spells_out_the_desktop_commands_with_your_own_hold_key(monkeypatch):
    writing = _writing(monkeypatch, hotkey="F6")
    section = writing.split("Apps, Windows and Keys by Voice", 1)[1].split("Indicator Follows", 1)[0]
    assert "Hold <b>F6</b>, say <b>snap left</b>, let go" in section
    for said in ("show desktop", "switch to desktop two", "move window to the other screen",
                 "volume up", "pause music", "lock screen", "take a screenshot", "what can I say",
                 "COSMIC"):
        assert said in section, said
    assert "F8" not in section


def test_help_spells_out_opening_apps_with_your_own_hold_key(monkeypatch):
    """Opening apps is a Labs feature: Help says where to turn it on, the
    steps with the key from the settings, and what it can't do."""
    writing = _writing(monkeypatch, hotkey="F6")
    opening = writing.split("Apps, Windows and Keys by Voice", 1)[1].split("Indicator Follows", 1)[0]
    assert "Preferences → Advanced → TuxTalk Labs" in opening
    assert "Hold <b>F6</b>, say <b>open Firefox</b>, let go" in opening
    assert "F8" not in opening
    for said in ("open new Firefox", "open the browser", "literal open Firefox"):
        assert said in opening, said
    assert "menu entry" in opening and "English" in opening
    assert "fits no app is typed as usual" in opening


def test_help_spells_out_windows_keys_and_addresses_with_your_own_hold_key(monkeypatch):
    writing = _writing(monkeypatch, hotkey="F6")
    section = writing.split("Apps, Windows and Keys by Voice", 1)[1].split(
        "Indicator Follows", 1)[0]
    assert "Hold <b>F6</b>, say <b>new tab</b>, let go" in section
    for said in ("close Firefox", "go to youtube.com", "web search", "terminal", "log out",
                 "copy the selection", "paste it", "save the file", "press escape"):
        assert said in section, said
    assert "<b>copy</b>," not in section
    assert "F8" not in section


def test_labs_intro_lists_opening_apps(monkeypatch):
    from tuxtalk import help_outline
    labs = next(s.body for part in help_outline.parse(_writing(monkeypatch)).parts
                for s in part.sections if s.title == "TuxTalk Labs")
    assert "opening apps" in labs


def _terminals(monkeypatch, hold, toggle="F9"):
    """The Terminals section's text, with *hold* and *toggle* as the saved keys."""
    from tuxtalk import help_text
    monkeypatch.setattr(help_text, "_keys", lambda _flatpak: (hold, toggle, ""))
    return next(text for title, text in help_text.tabs() if "Terminals" in title)


def test_tips_offer_left_ctrl_space(monkeypatch):
    """Everyone who finds F-keys need Fn is told Left Ctrl+Space needs none."""
    assert "Left Ctrl+Space" in _tips(monkeypatch)
    assert "Left Ctrl+Space" in _tips(monkeypatch, hotkey="F6")


def test_help_for_no_working_key_offers_left_ctrl_space():
    from tuxtalk import help_text
    problems = next(text for title, text in help_text.tabs() if "Problems" in title)
    assert "Scroll Lock or Left Ctrl+Space" in problems


def test_terminals_says_what_left_ctrl_space_costs_only_when_it_is_the_key(monkeypatch):
    """Ctrl+Space code completion waits while dictation is on, but only for
    someone who picked it (as hold key or as toggle key)."""
    for hold, toggle in (("Left Ctrl+Space", "F9"), ("F8", "Left Ctrl+Space")):
        page = _terminals(monkeypatch, hold, toggle)
        assert "code completion" in page and "Right Ctrl+Space" in page
        assert "Left Ctrl+Space" in page
    page = _terminals(monkeypatch, "F8")
    assert "code completion" not in page and "Right Ctrl+Space" not in page


def test_help_spells_out_the_mouse_grid_with_your_own_hold_key(monkeypatch):
    from tuxtalk import help_text
    writing = _writing(monkeypatch, hotkey="F6")
    card = writing.split("### The mouse", 1)[1].split("### Desktops", 1)[0]
    assert "Hold <b>F6</b>, say <b>mouse grid</b>, let go" in card
    for said in ("click five", "right click", "move here", "cancel"):
        assert said in card, said
    assert "F8" not in card
    tips = dict(help_text.tabs())["Tips"]
    assert "Writing → The mouse" in tips.split("Easier on the hands", 1)[1]


def test_every_literal_mouse_example_in_help_types_the_words(monkeypatch):
    """Help promises "literal" types these words: with no grid for the first
    two, while a grid shows for the other two."""
    from tuxtalk import command_phrases, help_text
    card = _writing(monkeypatch).split("### The mouse", 1)[1].split("### Desktops", 1)[0]
    examples = re.findall(r"<b>literal ([^<]+)</b>", card)
    assert examples == ["mouse grid", "left click", "five", "click"]
    for i, words in enumerate(examples):
        grid = i >= 2
        assert command_phrases.literal_words(
            f"literal {words}", navigation=False, actions=True, grid=grid) == words
