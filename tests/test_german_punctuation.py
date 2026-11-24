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


@pytest.mark.parametrize("said, typed", [
    ("Er sagte Anführungszeichen unten Hallo Anführungszeichen oben und ging",
     "Er sagte „Hallo“ und ging."),
    ("Er sagte, Anführungszeichen auf, Hallo, Anführungszeichen zu.", "Er sagte, „Hallo“."),
    ("Gänsefüßchen auf Test Punkt Gänsefüßchen zu dann weiter", "„Test.“ dann weiter."),
    # Talked about, the word stays.
    ("Setz die Anführungszeichen richtig", "Setz die Anführungszeichen richtig."),
])
def test_german_quotes_are_typed_the_german_way(said, typed):
    assert normalize_text(said, lang="de") == typed


def test_english_quotes_stay_english_with_german_commands_on():
    assert normalize_text("He said open quote hello close quote", lang="de") == "He said “hello.”"


def test_without_smart_quotes_german_quotes_are_plain(monkeypatch):
    from tuxtalk import dictation_text, live_settings
    monkeypatch.setattr(live_settings, "command_language", "de")
    text = dictation_text.prepare_text(
        "Er sagte Anführungszeichen unten Hallo Anführungszeichen oben", smart_quotes=False,
        auto_period=True, auto_space=False, ai=False, settings=live_settings)
    assert text == 'Er sagte "Hallo".'


@pytest.mark.parametrize("said, typed", [
    # A mark said after a noun or a pronoun is a mark.
    ("Wie war die Woche Fragezeichen", "Wie war die Woche?"),
    ("Was machst du am Wochenende Fragezeichen", "Was machst du am Wochenende?"),
    ("Ich trinke das Wasser Punkt", "Ich trinke das Wasser."),
    ("Kommst du mit Fragezeichen", "Kommst du mit?"),
    ("Was ist das Fragezeichen", "Was ist das?"),
    ("Das kann nicht wahr sein Punkt", "Das kann nicht wahr sein."),
    # Two marks with the engine's pause between them are both marks.
    ("Liebe Anna Komma, neue Zeile, wie geht es dir",
     f"Liebe Anna,{LINE_BREAK}Wie geht es dir."),
    ("Max Gedankenstrich Moritz", "Max – Moritz."),
    ("Gänsefüsschen auf Test Gänsefüsschen zu", "„Test“."),
])
def test_more_german_marks_said_as_commands(said, typed):
    assert normalize_text(said, lang="de") == typed


# Ordinary German sentences: every mark word in them is a word.
@pytest.mark.parametrize("said", [
    "Das Fragezeichen ist falsch.",
    "Das sind unsere Fragezeichen.",
    "Seine Fragezeichen sind berechtigt.",
    "Ein ganz wichtiger Punkt.",
    "Punkt für Punkt gehen wir vor.",
    "Ohne Auslassungspunkte geht es auch.",
    "Um Punkt zwanzig Uhr gehen wir.",
    "Punkt dreizehn Uhr treffen wir uns.",
    "Wir brauchten 12 Komma 5 Liter.",
    "Er setzte einen Doppelpunkt und machte weiter.",
    "Hier fehlt ein Bindestrich zwischen den Wörtern.",
    "Ich mag die neue Zeile im Gedicht.",
    "Setz einen neuen Absatz nach dem Titel.",
    "Mit diesem Semikolon bin ich nicht einverstanden.",
])
def test_ordinary_german_sentences_stay_text(said):
    assert normalize_text(said, lang="de") == said


def test_a_german_quote_the_engine_wrote_keeps_the_space_after_it():
    assert normalize_text("Er sagte „Hallo“ und ging") == "Er sagte „Hallo“ und ging."


@pytest.mark.parametrize("said, cleaned", [
    ("ob sie sie kennt", "ob sie sie kennt"),
    ("Die Nummer ist zwei zwei fünf fünf", "Die Nummer ist zwei zwei fünf fünf"),
])
def test_german_words_meant_twice_stay(said, cleaned):
    from tuxtalk.cleanup import clean_fillers_and_repeats
    assert clean_fillers_and_repeats(said) == cleaned
