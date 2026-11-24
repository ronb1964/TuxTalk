"""Preferences' Voice commands in dropdown: the language voice commands are
said in (command_language), apart from the language dictated."""
import gi


def _row(window):
    return window.general_tab.speech.command_language


def test_the_dropdown_offers_english_and_german_and_shows_the_saved_one(prefs_window):
    row = _row(prefs_window(command_language="de"))
    assert [entry[0] for entry in row.combo.get_model()] == ["English", "Deutsch"]
    assert row.combo.get_active_id() == "de"


def test_picking_deutsch_sets_german_commands(prefs_window):
    window = prefs_window()
    _row(window).combo.set_active_id("de")
    assert window.config["command_language"] == "de"


def test_a_screen_reader_hears_the_dropdown_s_name(prefs_window):
    gi.require_version("Atk", "1.0")
    from gi.repository import Atk
    relations = _row(prefs_window()).combo.get_accessible().ref_relation_set()
    labelled_by = relations.get_relation_by_type(Atk.RelationType.LABELLED_BY)
    assert [label.get_name() for label in labelled_by.get_target()] == ["Voice commands in:"]


def test_the_last_setup_screen_tells_a_german_setup_in_german(setup_complete, monkeypatch):
    """The rest of TuxTalk is English: this line is how a German speaker
    learns the commands are German too."""
    from tuxtalk import config
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(command_language="de"))
    shown = " ".join(setup_complete())
    assert "Sprachbefehle gehen auch auf Deutsch" in shown and "Voice Commands" in shown
    assert 'Say "period"' not in shown


def test_the_last_setup_screen_stays_english_otherwise(setup_complete):
    shown = " ".join(setup_complete())
    assert 'Say "period", "comma" or "new line" as you talk' in shown
    assert "Deutsch" not in shown
