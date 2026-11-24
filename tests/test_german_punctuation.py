"""Spoken punctuation in German, with voice commands said in German
(command_language "de"): "Komma", "Punkt", "neue Zeile" and the rest
become marks, while the same words used in an ordinary German sentence
("auf den Punkt", "Punkt 12 Uhr") stay words."""
import pytest

from tuxtalk.normalize import LINE_BREAK, normalize_text


@pytest.mark.parametrize("said, typed", [
    ("Hallo Komma wie geht es dir Fragezeichen", "Hallo, wie geht es dir?"),
    ("Das ist gut Punkt", "Das ist gut."),
    # The engine's own period before a spoken one isn't doubled.
    ("Das ist gut. Punkt.", "Das ist gut."),
    ("Hallo Ausrufezeichen", "Hallo!"),
    ("Ja Punkt nein Fragezeichen", "Ja. Nein?"),
    ("Bitte Doppelpunkt alles", "Bitte: alles."),
    ("Erstens Strichpunkt zweitens", "Erstens; zweitens."),
    ("Erstens Semikolon zweitens", "Erstens; zweitens."),
    ("Grüß Gott Beistrich wie geht's", "Grüß Gott, wie geht's."),
    ("Klammer auf siehe oben Klammer zu", "(Siehe oben)."),
    ("Liste eckige Klammer auf eins eckige Klammer zu", "Liste [eins]."),
    ("Max Bindestrich Moritz", "Max-Moritz."),
    ("Punkt Punkt Punkt das war's", "… Das war's."),
    # A capital after a full stop, umlauts included.
    ("gut Punkt über alles", "Gut. Über alles."),
    ("Liebe Anna Komma neue Zeile wie geht es dir", f"Liebe Anna,{LINE_BREAK}Wie geht es dir."),
    ("Erster Teil neuer Absatz zweiter Teil",
     f"Erster Teil.{LINE_BREAK}{LINE_BREAK}Zweiter Teil."),
    # English stays understood.
    ("Hallo comma wie geht es", "Hallo, wie geht es."),
])
def test_german_spoken_marks_become_marks(said, typed):
    assert normalize_text(said, lang="de") == typed


@pytest.mark.parametrize("said, typed", [
    ("Das bringt es auf den Punkt.", "Das bringt es auf den Punkt."),
    ("Komm endlich zum Punkt", "Komm endlich zum Punkt."),
    ("Das ist ein wichtiger Punkt", "Das ist ein wichtiger Punkt."),
    ("In diesem Punkt hast du recht", "In diesem Punkt hast du recht."),
    ("Punkt eins der Tagesordnung", "Punkt eins der Tagesordnung."),
    # The engine's small "punkt" gets the capital a German noun has.
    ("Um punkt 12 Uhr bin ich nach Hause gekommen.",
     "Um Punkt 12 Uhr bin ich nach Hause gekommen."),
    ("Wir sehen uns um Punkt zwölf", "Wir sehen uns um Punkt zwölf."),
    ("Wir brauchten drei Komma fünf Tage", "Wir brauchten drei Komma fünf Tage."),
    ("Eine Zahl ohne Komma", "Eine Zahl ohne Komma."),
    ("Da steht ein großes Fragezeichen dahinter", "Da steht ein großes Fragezeichen dahinter."),
    ("Ich brauche einen neuen Absatz im Text", "Ich brauche einen neuen Absatz im Text."),
    ("Punkt, Punkt, Komma, Strich, fertig ist das Mondgesicht.",
     "Punkt, Punkt, Komma, Strich, fertig ist das Mondgesicht."),
    # "wörtlich" or "das Wort" types the mark's word itself.
    ("Schreib wörtlich Komma bitte", "Schreib Komma bitte."),
    ("Schreib das Wort Fragezeichen", "Schreib Fragezeichen."),
    # Compound words are never marks.
    ("Der Treffpunkt ist am Bahnhof", "Der Treffpunkt ist am Bahnhof."),
])
def test_german_sentences_keep_mark_words_as_words(said, typed):
    assert normalize_text(said, lang="de") == typed


def test_german_mark_words_wait_for_german_commands():
    """With the command language English, German words are dictation."""
    assert normalize_text("Hallo Komma wie geht es") == "Hallo Komma wie geht es."


@pytest.mark.parametrize("said, cleaned", [
    ("Ich wollte äh morgen kommen.", "Ich wollte morgen kommen."),
    ("Ähm, das passt.", "Das passt."),
    ("Wir öhm sehen uns.", "Wir sehen uns."),
    # German's doubled article is meant, and stays.
    ("Die Frau die die Zeitung liest.", "Die Frau die die Zeitung liest."),
])
def test_german_fillers_go_and_meant_doubles_stay(said, cleaned):
    from tuxtalk.cleanup import clean_fillers_and_repeats
    assert clean_fillers_and_repeats(said) == cleaned
