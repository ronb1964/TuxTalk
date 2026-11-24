"""Select and move by voice in German (command_language "de"): the verb
last as most say it ("den letzten Satz markieren") or first ("markiere den
letzten Satz"), and the same keys as the English command."""
import pytest

from tuxtalk import voice_navigation as nav


@pytest.mark.parametrize("german, english", [
    ("Das markieren.", "select that"),
    ("Markiere das", "select that"),
    ("Den letzten Satz markieren.", "select the last sentence"),
    ("markiere den letzten Satz", "select the last sentence"),
    ("Die letzten drei Wörter markieren", "select the last three words"),
    ("die letzten 3 Wörter markieren", "select the last 3 words"),
    ("Alles markieren", "select all"),
    ("Die Zeile markieren", "select the line"),
    ("die nächsten zwei Wörter markieren", "select the next two words"),
    ("Bis zum Zeilenende markieren", "select to the end of the line"),
    ("Geh zum Zeilenende.", "go to the end of the line"),
    ("zum Ende der Zeile", "go to the end of the line"),
    ("Zum Zeilenanfang", "go to the start of the line"),
    ("Geh zum Ende", "go to the end"),
    ("Nach ganz oben", "go to the top"),
    ("Drei Wörter zurück", "go back three words"),
    ("Geh zwei Wörter vor", "go forward two words"),
    ("Nächstes Wort", "next word"),
    ("Fünf Zeichen zurück", "go back five characters"),
    ("Zwei Zeilen nach oben", "go up two lines"),
    ("eine Zeile runter", "go down one line"),
    ("Markierung aufheben", "unselect"),
    ("Markierung löschen bitte", "unselect"),
])
def test_a_german_command_presses_what_the_english_one_does(german, english):
    said, meant = nav.parse(german, "de"), nav.parse(english)
    assert said is not None and meant is not None
    assert (said.said_unit, said.count, said.keys, said.first, said.step) == (
        meant.said_unit, meant.count, meant.keys, meant.first, meant.step)


@pytest.mark.parametrize("said", [
    "Ich habe den letzten Satz markiert.",
    "Das ist das Ende der Zeile.",
    "Wir gehen zum Ende.",
    "11 Wörter zurück",            # more than ten moves is never a command
])
def test_german_sentences_stay_text(said):
    assert nav.parse(said, "de") is None


def test_english_still_works_with_german_commands_on():
    assert nav.parse("select the last sentence", "de").said_unit == "sentence"


def test_german_waits_for_german_commands():
    assert nav.parse("den letzten Satz markieren") is None
