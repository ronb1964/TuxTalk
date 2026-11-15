"""Searching Help: a feature known by its name is found whichever tab it's
on (Help is sorted by situation, so a Labs feature has no tab of its own),
voice commands included, in the words Help shows this person."""
import pytest

from tuxtalk import config, help_search, help_text


@pytest.fixture
def entries(monkeypatch):
    """Help's index as it reads with default settings."""
    monkeypatch.setattr(config, "load_config", config.Settings)
    return help_search.index(help_text.tabs())


def _titles(found):
    return [(e.tab, e.part, e.title) for e in found]


def test_a_features_name_finds_its_card(entries):
    found = help_search.search(entries, "live text")
    assert ("Hands-free", "Live Text", "How it works") in _titles(found)


def test_labs_finds_every_labs_card_and_command(entries):
    found = help_search.search(entries, "labs")
    labs = [e for e in entries if e.labs]
    assert labs and all(e in found for e in labs)
    assert any(e.tab == help_text.COMMANDS_TITLE for e in labs)


def test_case_and_spaces_dont_matter(entries):
    assert help_search.search(entries, "  Live   TEXT ") == help_search.search(entries, "live text")


def test_a_title_match_comes_before_a_text_match(entries):
    found = help_search.search(entries, "snippets")
    assert found[0].title == "Snippets"
    assert len(found) > 1


def test_a_word_only_in_a_headings_note_is_found(entries):
    found = help_search.search(entries, "tuxtalk menu → performance")
    assert ("Problems", "", "Performance presets") in _titles(found)


def test_cpu_finds_the_processor_cards(entries):
    found = help_search.search(entries, "cpu")
    assert found and all("processor" in (e.title + e.note + e.text).lower() for e in found)


def test_a_voice_command_is_found_on_the_commands_tab(entries):
    found = help_search.search(entries, "press tab")
    assert any(e.tab == help_text.COMMANDS_TITLE for e in found)


def test_help_is_searched_with_the_users_own_key(monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(hotkey="F6"))
    entries = help_search.index(help_text.tabs())
    assert ("Start Here", "", "How to dictate") in _titles(help_search.search(entries, "f6"))
    assert help_search.search(entries, "f8") == []


def test_a_blank_search_finds_nothing(entries):
    assert help_search.search(entries, "   ") == []


def test_the_match_is_marked_and_the_text_stays_safe_markup():
    assert help_search.highlight("Ctrl & <Alt>", "&") == "Ctrl <b>&amp;</b> &lt;Alt&gt;"
    assert help_search.highlight("Live Text", "live") == "<b>Live</b> Text"
    assert help_search.highlight("No match", "zzz") == "No match"


def _card(entries, tab, title):
    return next(e for e in entries if e.tab == tab and e.title == title)


def test_uninstall_finds_the_uninstalling_card(entries):
    found = help_search.search(entries, "uninstall")
    assert found[0].tab == "Problems" and found[0].title == "Uninstalling"
    assert "Updates & Backup" in found[0].text


def test_a_package_is_told_its_uninstall_command(monkeypatch):
    from tuxtalk import installation
    monkeypatch.setattr(config, "load_config", config.Settings)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(installation, "get_install_type", lambda: "rpm")
    monkeypatch.setattr(installation, "get_running_appimage_path", lambda: None)
    card = _card(help_search.index(help_text.tabs()), "Problems", "Uninstalling")
    assert "tuxtalk --uninstall" in card.text


def test_the_flatpak_is_told_its_own_uninstall_command(monkeypatch):
    monkeypatch.setattr(config, "load_config", config.Settings)
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    card = _card(help_search.index(help_text.tabs()), "Problems", "Uninstalling")
    assert "flatpak uninstall --delete-data io.github.ronb1964.TuxTalk" in card.text
    assert "--uninstall" not in card.text.replace("flatpak uninstall", "")


def test_start_here_says_help_can_be_searched(entries):
    found = help_search.search(entries, "search help")
    assert ("Start Here", "", "Where things are") in _titles(found)


# Words people are likely to type: each finds a card, so nobody is told
# "no results" for something Help covers in its own words.
LIKELY = [
    "labs", "live text", "type at pause", "voice editing", "mouse", "cuda", "vulkan", "cpu",
    "gpu", "processor", "graphics card", "device", "model", "parakeet", "whisper", "language",
    "microphone", "mic", "hotkey", "shortcut", "key", "password", "uninstall", "update",
    "backup", "settings", "start at login", "autostart", "terminal", "send", "enter", "tab",
    "punctuation", "comma", "period", "new paragraph", "numbers", "undo", "scratch that",
    "snippets", "your words", "music", "flatpak", "gnome", "kde", "log", "privacy", "stats",
    "recent dictations", "transcribe", "file", "ai", "performance", "slow", "indicator",
    # A few words, as people type them.
    "remove tuxtalk", "how to uninstall", "change hotkey", "mic not working", "gpu speed",
    "cpu slow", "start login", "live text labs", "gpus",
]


@pytest.mark.parametrize("word", LIKELY)
def test_likely_searches_find_something(entries, word):
    assert help_search.search(entries, word), f"nothing found for {word!r}"


def test_a_short_word_matches_words_not_the_middle_of_others(entries):
    import re
    found = help_search.search(entries, "ai")
    assert found
    assert all(re.search(r"(?<!\w)ai", f"{e.part} {e.title} {e.note} {e.text}", re.I)
               for e in found), "'ai' matched inside 'again' or 'explain'"


def test_autostart_finds_where_starting_at_login_is_set(entries):
    found = _titles(help_search.search(entries, "autostart"))
    assert ("Start Here", "", "Where things are") in found
    assert ("Problems", "", "Password fields") not in found


def test_hotkey_finds_the_dictation_key_not_every_key(entries):
    found = _titles(help_search.search(entries, "hotkey"))
    assert ("Problems", "", "No key works") in found
    assert len(found) < 10


def test_every_word_of_a_search_counts_not_only_the_whole_phrase(entries):
    found = _titles(help_search.search(entries, "how to uninstall"))
    assert found[0] == ("Problems", "", "Uninstalling")


def test_cards_with_all_the_words_come_before_cards_with_some(entries):
    found = help_search.search(entries, "microphone answering")
    assert found[0].title == '"Microphone isn\'t answering"'


def test_a_plural_finds_what_the_singular_does(entries):
    assert help_search.search(entries, "gpus") == help_search.search(entries, "gpu")


def test_the_match_is_found_where_a_word_starts_with_other_names_too():
    assert help_search.first_match("Not again: the processor", "cpu") == (15, 24)
    assert help_search.first_match("explain the AI", "ai") == (12, 14)
    assert help_search.first_match("nothing here", "cpu") is None


def test_uninstalling_says_the_shared_models_go_only_when_their_box_is_ticked(entries):
    # Uninstall leaves the speech and AI models in the download folder other
    # apps share unless that box is ticked; Help mustn't promise the space back.
    card = _card(entries, "Problems", "Uninstalling")
    assert "speech and AI models" in card.text
    assert "only if you tick that box" in card.text
