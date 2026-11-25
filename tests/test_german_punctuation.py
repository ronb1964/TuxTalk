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


@pytest.mark.parametrize("said, typed", [
    # Parakeet wrote these as two words for a real German speaker.
    ("Hallo, wie geht es dir? Frage Zeichen.", "Hallo, wie geht es dir?"),
    ("Hallo Komma wie geht es dir Frage Zeichen", "Hallo, wie geht es dir?"),
    ("Hallo Ausrufe Zeichen", "Hallo!"),
    ("Bitte Doppel Punkt alles", "Bitte: alles."),
    ("Max Binde Strich Moritz", "Max-Moritz."),
    ("Liebe Anna Komma Zeilen Umbruch wie geht es dir",
     f"Liebe Anna,{LINE_BREAK}Wie geht es dir."),
    ("Anführungs Zeichen unten Hallo Anführungs Zeichen oben", "\u201eHallo\u201c."),
    ("Warte Auslassungs Punkte gut", "Warte… Gut."),
])
def test_german_marks_the_engine_wrote_in_two_words_are_marks(said, typed):
    assert normalize_text(said, lang="de") == typed


@pytest.mark.parametrize("said, typed", [
    # "Doppel Punkt" ending the dictation: doubles, and a full stop.
    ("Wir spielen heute Doppel Punkt", "Wir spielen heute Doppel."),
    # Held as words when two-word too: "wörtlich", after an article, in a run.
    ("Schreib wörtlich Doppel Punkt", "Schreib Doppel Punkt."),
    ("den Doppel Punkt", "Den Doppel Punkt."),
    ("Punkt, Komma, Doppel Punkt", "Punkt, Komma, Doppel Punkt."),
    # Kept as words, both halves are nouns and keep their capitals.
    ("das frage zeichen ist falsch", "Das Frage Zeichen ist falsch."),
    ("Da steht ein großes Frage Zeichen dahinter", "Da steht ein großes Frage Zeichen dahinter."),
    # Only the compounds that aren't ordinary German as two words: a line
    # with a full stop, "high" with a comma, thoughts and a verb.
    ("Mach einen Strich Punkt", "Mach einen Strich."),
    ("Das ist hoch Komma aber gut", "Das ist hoch, aber gut."),
    ("In Gedanken strich sie über das Fell der Katze.",
     "In Gedanken strich sie über das Fell der Katze."),
    ("Diesen Gedanken strich er wieder.", "Diesen Gedanken strich er wieder."),
])
def test_two_words_that_are_ordinary_german_stay_words(said, typed):
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
    # The verb or adverb that ends the clause is no adjective.
    ("Kannst du das machen Fragezeichen", "Kannst du das machen?"),
    ("Hast du das gesehen Fragezeichen", "Hast du das gesehen?"),
    ("Ich muss das noch erledigen Punkt", "Ich muss das noch erledigen."),
    ("Ich will einen haben Punkt", "Ich will einen haben."),
    ("Wir machen das morgen Punkt", "Wir machen das morgen."),
    ("Ich mag das hier Punkt", "Ich mag das hier."),
    ("Er sagt das immer Punkt", "Er sagt das immer."),
    ("Ich weiß das alles Punkt", "Ich weiß das alles."),
    ("Ich finde das super Punkt", "Ich finde das super."),
    ("Ist das ganz sicher Fragezeichen", "Ist das ganz sicher?"),
    ("Der ist super Ausrufezeichen", "Der ist super!"),
    # "ein", "ohne" and "mit" end a clause too.
    ("Fällt dir nichts ein Fragezeichen", "Fällt dir nichts ein?"),
    ("Schalte das Licht ein Punkt", "Schalte das Licht ein."),
    ("Geht das auch ohne Fragezeichen", "Geht das auch ohne?"),
    ("Kommst du mit Komma oder bleibst du", "Kommst du mit, oder bleibst du."),
    # A conjunction after it is no more of a noun.
    ("Es könnte sein Komma dass er kommt", "Es könnte sein, dass er kommt."),
    ("Sag es ihr Komma bitte", "Sag es ihr, bitte."),
    ("Ich mache das gerne Punkt", "Ich mache das gerne."),
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
    "Das ist ein wichtiger Punkt.",
    "Da steht ein großes Fragezeichen.",
    "Der Komma-Fehler ist ärgerlich.",
    "Punkt-Abzug gibt es.",
    "Das schreibt man mit Bindestrich.",
    "In null Komma nichts war er da.",
    "Das ist der springende Punkt bei der Sache.",
    "Wir treffen uns um Punkt halb acht.",
    "Eine Zahl ohne Komma.",
    "Kein Komma hier.",
    "Punkt, Komma, Strich.",
])
def test_ordinary_german_sentences_stay_text(said):
    assert normalize_text(said, lang="de") == said


def test_a_german_quote_the_engine_wrote_keeps_the_space_after_it():
    assert normalize_text("Er sagte „Hallo“ und ging") == "Er sagte „Hallo“ und ging."


@pytest.mark.parametrize("said, cleaned", [
    ("ob sie sie kennt", "ob sie sie kennt"),
    ("Die Nummer ist zwei zwei fünf fünf", "Die Nummer ist zwei zwei fünf fünf"),
    ("Die Zahlen sind dreizehn dreizehn.", "Die Zahlen sind dreizehn dreizehn."),
    ("Die Zahlen sind zwanzig zwanzig.", "Die Zahlen sind zwanzig zwanzig."),
    ("Die Zahlen sind dreiundzwanzig dreiundzwanzig.",
     "Die Zahlen sind dreiundzwanzig dreiundzwanzig."),
])
def test_german_words_meant_twice_stay(said, cleaned):
    from tuxtalk.cleanup import clean_fillers_and_repeats
    assert clean_fillers_and_repeats(said) == cleaned


# From the release review: clauses that end in a verb, and mark words joined by
# "und", come out the same way every time.
@pytest.mark.parametrize("said, typed", [
    ("ob ich das schaffe Fragezeichen", "Ob ich das schaffe?"),
    ("dass ich das mache Punkt", "Dass ich das mache."),
    ("ob ich die nehme Fragezeichen", "Ob ich die nehme?"),
    ("Ich sage es ihr später Punkt", "Ich sage es ihr später."),
    ("Wenn Sie das brauchen Komma melden Sie sich", "Wenn Sie das brauchen, melden Sie sich."),
    ("Wenn wir das machen Komma dann klappt es", "Wenn wir das machen, dann klappt es."),
    ("Das ist gut Punkt ein Freund kommt", "Das ist gut. Ein Freund kommt."),
    ("Ich komme mit Punkt", "Ich komme mit."),
])
def test_a_clause_ending_in_a_verb_takes_the_mark(said, typed):
    assert normalize_text(said, lang="de") == typed


@pytest.mark.parametrize("said", [
    "Ich habe ein Fragezeichen und ein Ausrufezeichen gesehen.",
    "Eine Zahl ohne Komma und ohne Punkt.",
    "Schreib das mit Bindestrich und ohne Leerzeichen.",
    "Das ist die richtige neue Zeile für dich.",
    "Punkt eins der Tagesordnung.",
    "Er hat den letzten Punkt vergessen.",
    "Ich habe einen wichtigen Punkt vergessen.",
    "Zum letzten Punkt komme ich gleich.",
    "Mit dem neuen Komma ist es besser.",
    "Sein letzter Punkt war gut.",
    "Ihr letzter Punkt war gut.",
    "Ich bin um Punkt ein Uhr da.",
])
def test_mark_words_as_nouns_mid_sentence_stay_words(said):
    assert normalize_text(said, lang="de") == said


def test_a_mark_word_ending_the_dictation_is_the_mark():
    """What rules can't tell from a command: the command wins, and
    "wörtlich" types the word."""
    assert normalize_text("Das ist der letzte Punkt", lang="de") == "Das ist der letzte."
    assert normalize_text("Das ist der letzte wörtlich Punkt", lang="de") == (
        "Das ist der letzte Punkt.")


@pytest.mark.parametrize("said, typed", [
    ("Max Geviertstrich Moritz", "Max — Moritz."),
    ("Max Geviert Strich Moritz", "Max — Moritz."),
    # A word processor's soft line break is Shift+Enter: TuxTalk's line break.
    ("Erste Zeile weicher Zeilenumbruch zweite Zeile", f"Erste Zeile.{LINE_BREAK}Zweite Zeile."),
    # "wörtlich" types the words themselves, the verb small as German writes it.
    ("Schreib wörtlich Tab drücken bitte", "Schreib Tab drücken bitte."),
    ("Schreib wörtlich Geviertstrich", "Schreib Geviertstrich."),
    ("Schreib das Wort neue Zeile", "Schreib neue Zeile."),
    # English's words for it too, as at the end of a dictation (key_commands).
    ("Schreib literal Tab drücken bitte", "Schreib Tab drücken bitte."),
])
def test_german_dash_and_line_break_said_as_commands(said, typed):
    assert normalize_text(said, lang="de") == typed


@pytest.mark.parametrize("said", [
    # Said about the key, as "press the tab key" is in English.
    "Du musst die Tab-Taste drücken, um weiterzukommen.",
    "Ein Geviertstrich ist länger als ein Bindestrich.",
    "Du musst Tab drücken, um weiterzukommen.",
    "Man kann einfach Tab drücken.",
    "Mit Alt Tab drücken wechselst du das Fenster.",
    "Du könntest Tab drücken, um weiterzukommen.",
    "Wenn du Tab drücken willst, geht es weiter.",
    "Er wollte einen weichen Zeilenumbruch.",
    "Hier wäre weicher Umbruch sinnvoll.",
    "Er wollte einen weichen Umbruch.",
])
def test_german_sentences_about_dashes_tabs_and_breaks_stay_text(said):
    assert normalize_text(said, lang="de") == said


def test_tab_and_dash_words_wait_for_german_commands():
    assert normalize_text("Name Geviertstrich") == "Name Geviertstrich."


def test_die_woerter_in_a_sentence_stays():
    """Only "wörtlich", "das Wort" and "literal" type the mark's word; "die
    Wörter" is no such command, and isn't lost."""
    assert normalize_text("Ich kenne die Wörter Punkt und Komma", lang="de").startswith(
        "Ich kenne die Wörter")
