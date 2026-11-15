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
    assert help_search.search(entries, "hold f8") == []


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
