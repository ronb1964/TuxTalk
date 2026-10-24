"""Preferences' one Language dropdown stands for three settings.

Language Mode, Language and Only these languages were three controls; the
dropdown replaces them, but the settings keep their meaning so configs and
backups from before read the same.
"""
from tuxtalk.prefs_language import (SEVERAL_LANGUAGES, apply_language_choice, language_choice,
                                    language_summary, several_languages_label)


def test_existing_settings_show_as_the_matching_entry():
    assert language_choice({"language_mode": "auto", "auto_languages": ""}) == "auto"
    assert language_choice({"language_mode": "manual", "language": "de"}) == "de"
    assert language_choice({"language_mode": "auto", "auto_languages": "en,es"}) == SEVERAL_LANGUAGES
    # A manual choice wins over a list left over from before.
    assert language_choice({"language_mode": "manual", "language": "fr",
                            "auto_languages": "en,es"}) == "fr"


def test_a_config_without_language_settings_detects_the_language():
    assert language_choice({}) == "auto"


def test_picking_one_language_is_the_old_manual_mode():
    config = {"language_mode": "auto", "auto_languages": "", "language": ""}
    apply_language_choice(config, "ja")
    assert config["language_mode"] == "manual" and config["language"] == "ja"
    assert language_choice(config) == "ja"


def test_detect_automatically_drops_a_language_list():
    config = {"language_mode": "auto", "auto_languages": "en,es"}
    apply_language_choice(config, "auto")
    assert config == {"language_mode": "auto", "auto_languages": ""}
    assert language_choice(config) == "auto"


def test_the_several_entry_names_the_languages():
    assert several_languages_label("") == "Several languages…"
    assert several_languages_label("en,es") == "Several: English, Spanish…"


def test_the_setup_line_says_which_language():
    assert language_summary({"language_mode": "auto"}) == "detects the language"
    assert language_summary({"language_mode": "manual", "language": "es"}) == "Spanish"
    assert language_summary({"language_mode": "auto", "auto_languages": "en,de"}) == "English, German"
