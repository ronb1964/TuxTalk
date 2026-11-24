"""Apps, windows, keys, desktops, sound, music and the mouse by voice in
German (command_language "de"): each German command does what the English
one it means does, and German sentences stay text."""
import pytest

from tuxtalk import command_phrases
from tuxtalk.german_commands import to_english


@pytest.mark.parametrize("german, english", [
    ("Firefox öffnen", "open firefox"),
    ("Öffne Firefox", "open firefox"),
    ("Firefox aufrufen", "open firefox"),
    ("Starte Firefox", "start firefox"),
    ("Weiteren Firefox öffnen", "open another firefox"),
    ("Noch einen Firefox öffnen", "open another firefox"),
    ("Neues Firefox-Fenster öffnen", "open another firefox"),
    ("Den Browser öffnen", "open the browser"),
    ("E-Mail öffnen", "open email"),
    ("Dateien öffnen", "open files"),
    ("Firefox schließen", "close firefox"),
    ("Alle Firefox schließen", "close all firefox"),
    ("Zu Firefox wechseln", "switch to firefox"),
    ("Wechsle zu Firefox", "switch to firefox"),
    ("Fenster schließen", "close window"),
    ("Fenster minimieren", "minimize window"),
    ("Fenster wiederherstellen", "restore window"),
    ("Fenster nach links", "snap left"),
    ("Fenster nach oben rechts", "snap top right"),
    ("Fenster zentrieren", "center window"),
    ("Fenster auf den anderen Bildschirm", "move window to the other screen"),
    ("Desktop anzeigen", "show desktop"),
    ("Alle Fenster anzeigen", "show all windows"),
    ("Nächster Desktop", "next desktop"),
    ("Zu Desktop zwei wechseln", "switch to desktop 2"),
    ("Fenster auf Desktop drei", "move window to desktop 3"),
    ("Neuer Tab", "new tab"),
    ("Tab schließen", "close tab"),
    ("Tab wieder öffnen", "reopen tab"),
    ("Eine Seite zurück", "go back a page"),
    ("Seite neu laden", "reload the page"),
    ("Bild runter", "page down"),
    ("Fünf nach unten scrollen", "scroll down 5"),
    ("Nach oben scrollen", "scroll up"),
    ("Auf der Seite suchen", "find on page"),
    ("Datei speichern", "save the file"),
    ("Auswahl kopieren", "copy the selection"),
    ("Escape drücken", "press escape"),
    ("Lauter", "volume up"),
    ("Lautstärke runter", "volume down"),
    ("Lautstärke hoch drei", "volume up 3"),
    ("Ton aus", "mute the sound"),
    ("Musik pausieren", "pause music"),
    ("Musik abspielen", "play music"),
    ("Nächster Titel", "next track"),
    ("Bildschirm sperren", "lock the screen"),
    ("Bildschirmfoto machen", "take a screenshot"),
    ("Bildschirmfoto von diesem Fenster", "screenshot this window"),
    ("Was kann ich sagen?", "what can i say"),
    ("Geh zu youtube Punkt com", "go to youtube dot com"),
    ("Websuche günstige Reifen", "web search günstige Reifen"),
    ("Im Web suchen: Wetter Berlin", "web search Wetter Berlin"),
    ("Fenster schliessen", "close window"),
    ("Eine Datei öffnen.", "open a datei"),
    ("geh zu example.com/ResetToken", "go to example.com/ResetToken"),
    # A spoken Punkt is a dot in the host only; an app's name keeps its ß.
    ("geh zu example.com/Punkt/Reset", "go to example.com/Punkt/Reset"),
    ("geh zu https://example Punkt com", "go to https://example dot com"),
    ("Maße schliessen", "close maße"),
    ("Mausraster", "mouse grid"),
    ("Fünf", "5"),
    ("Nummer zwölf", "12"),
    ("Fünfundzwanzig", "25"),
    ("Klick", "click"),
    ("Rechtsklick fünf", "right click 5"),
    ("Doppelklick", "double click"),
    ("Links zwei", "left 2"),
    ("Maus hierhin", "move here"),
    ("Zeiger hierhin bewegen", "move here"),
    ("Abbrechen", "cancel"),
    ("Raster schließen", "close grid"),
])
def test_a_german_command_means_its_english_one(german, english):
    assert to_english(german) == english


@pytest.mark.parametrize("said", [
    "Ich gehe nach Hause.",
    "Wir haben gestern Firefox geöffnet und dann lange gearbeitet.",
    "Das war ein schöner Tag.",
])
def test_german_sentences_are_no_command(said):
    assert to_english(said) is None
    assert command_phrases.parse(said, navigation=True, actions=True, lang="de") is None


def test_a_german_command_is_parsed_as_its_english_one():
    german = command_phrases.parse("Zu Desktop zwei wechseln", navigation=True, actions=True,
                                   lang="de")
    english = command_phrases.parse("switch to desktop two", navigation=True, actions=True)
    assert german == english


def test_german_commands_wait_for_german_and_for_the_labs_setting():
    assert command_phrases.parse("Neuer Tab", navigation=True, actions=True) is None
    assert command_phrases.parse("Neuer Tab", navigation=True, actions=False, lang="de") is None


def test_wortlich_types_a_german_command_as_words():
    assert command_phrases.literal_words("wörtlich neuer Tab", navigation=True, actions=True,
                                         lang="de") == "neuer Tab"
