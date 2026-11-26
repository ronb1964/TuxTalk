"""Voice editing's exact edits said in German, with voice commands in
German: "alles in Großbuchstaben" or "die Kommas entfernen" is done in code,
exactly, as "make this all caps" and "remove the commas" are, instead of
going to the AI."""
import pytest

from tuxtalk.voice_edit_rules import formatting, is_delete, mechanical_edit

TEXT = "eins, zwei Test\n    - drei"


@pytest.mark.parametrize("said, edited", [
    ("alles in Großbuchstaben", "EINS, ZWEI TEST\n    - DREI"),
    ("Schreib das in Großbuchstaben.", "EINS, ZWEI TEST\n    - DREI"),
    ("in Grossbuchstaben", "EINS, ZWEI TEST\n    - DREI"),
    ("alles in Kleinbuchstaben", "eins, zwei test\n    - drei"),
    ("schreib alles klein", "eins, zwei test\n    - drei"),
    ("jedes Wort großschreiben", "Eins, Zwei Test\n    - Drei"),
    ("schreib jedes Wort groß", "Eins, Zwei Test\n    - Drei"),
    ("das einrücken", "    eins, zwei Test\n        - drei"),
    ("rück das ein", "    eins, zwei Test\n        - drei"),
    ("das ausrücken", "eins, zwei Test\n- drei"),
    ("Einzug verkleinern", "eins, zwei Test\n- drei"),
    ("als Aufzählung", "- eins, zwei Test\n    - drei"),
    ("mach daraus eine Aufzählung", "- eins, zwei Test\n    - drei"),
    ("als nummerierte Liste", "1. eins, zwei Test\n    2. drei"),
    ("die Kommas entfernen", "eins zwei Test\n    - drei"),
    ("bitte entferne alle Kommas", "eins zwei Test\n    - drei"),
    ("das Wort Test entfernen", "eins, zwei\n    - drei"),
    ("lösche das Wort Test", "eins, zwei\n    - drei"),
    ("die Aufzählungszeichen entfernen", "eins, zwei Test\n    drei"),
    ("die Aufzählungspunkte entfernen", "eins, zwei Test\n    drei"),
    ("als Liste mit Aufzählungspunkten", "- eins, zwei Test\n    - drei"),
    ("bitte entferne das Wort Test bitte", "eins, zwei\n    - drei"),
    ("entferne bitte das Wort Test", "eins, zwei\n    - drei"),
])
def test_a_german_exact_edit_is_done_in_code(said, edited):
    assert mechanical_edit(TEXT, said, "de") == edited


@pytest.mark.parametrize("text, said, edited", [
    # "bitte" in the words to take out stays one of them.
    ("Bitte warten und warten", "das Wort bitte warten entfernen", "Und warten"),
    # English words in them are words, not where to take them from.
    ("Test from this und Test", "den Ausdruck Test from this entfernen", "Und Test"),
])
def test_the_words_to_take_out_are_taken_as_said(text, said, edited):
    assert mechanical_edit(text, said, "de") == edited


def test_a_numbered_list_that_starts_at_three_is_renumbered():
    assert mechanical_edit("3. Milch\n4. Brot", "als nummerierte Liste", "de") == "1. Milch\n2. Brot"


@pytest.mark.parametrize("said, edited", [
    ("als nummerierte Liste", "1. 3. Oktober Feier\n2. 24. Dezember Fest"),
    ("als Aufzählung", "- 3. Oktober Feier\n- 24. Dezember Fest"),
    ("die Aufzählungszeichen entfernen", "3. Oktober Feier\n24. Dezember Fest"),
])
def test_a_date_opening_a_line_keeps_its_day(said, edited):
    """"3. Oktober" opening a line is a date, not a list's number."""
    assert mechanical_edit("3. Oktober Feier\n24. Dezember Fest", said, "de") == edited


def test_a_numbered_list_is_still_renumbered():
    assert mechanical_edit("1. eins\n2. zwei", "als Aufzählung", "de") == "- eins\n- zwei"


def test_the_numbers_are_not_the_bullets():
    """"Nummern" are the numbers in the text: that's for the AI to judge."""
    assert mechanical_edit("1. eins\n2. zwei", "die Nummern entfernen", "de") is None


def test_every_word_gets_its_capital_umlauts_too():
    assert mechanical_edit("ärger über öl", "jedes Wort großschreiben", "de") == "Ärger Über Öl"
    assert mechanical_edit("élan vital", "make this title case") == "Élan Vital"


def test_german_quotes_go_with_the_other_quotes():
    assert mechanical_edit("Er sagte „Hallo“.", "die Anführungszeichen entfernen",
                           "de") == "Er sagte Hallo."


@pytest.mark.parametrize("text", ["Er sagte «Hallo».", "Er sagte ‹Hallo›.",
                                  "Er sagte ‚Hallo‘.", "Er sagte »Hallo«."])
def test_swiss_austrian_and_single_german_quotes_go_too(text):
    """German quotes are „“, or »« in print, «» and ‹› in Switzerland, and ‚‘
    when single: each kind is a quote to take out."""
    assert mechanical_edit(text, "die Anführungszeichen entfernen", "de") == "Er sagte Hallo."


def test_english_single_quotes_stay_whole_with_the_apostrophe():
    """‘ is German's closing single quote, but in English it opens one whose
    closing ’ is the apostrophe too: taking ‘ alone would leave half a pair."""
    assert mechanical_edit("He said ‘hi’ and “bye”.", "remove the quotes") == "He said ‘hi’ and bye."


def test_a_german_date_keeps_its_period_when_the_periods_go():
    """A German day's number ends in a period before its month ("am 3.
    Oktober"): part of the date, not a sentence's end."""
    assert mechanical_edit("Wir treffen uns am 3. Oktober. Bis dann.", "die Punkte entfernen",
                           "de") == "Wir treffen uns am 3. Oktober Bis dann"


@pytest.mark.parametrize("said", [
    "mach das rot", "in Blau", "die Schrift größer machen", "die Schrift grösser machen",
    "die Schriftart ändern", "die Farbe ändern", "die Größe ändern", "die Grösse ändern",
    "das zentrieren", "das durchstreichen", "durchstreiche das", "streich das durch",
    "eine andere Schriftart wählen", "ändere die Schriftfarbe in Rot",
])
def test_german_formatting_no_shortcut_does_is_said_to_be_impossible(said):
    """Font, size and colour went to the AI, which can't change how text
    looks: German heard "no change" where English hears it can't be done."""
    assert formatting(said, "de") == "cant_format"


@pytest.mark.parametrize("said", ["das löschen", "Lösch das.", "alles löschen",
                                  "die Auswahl entfernen"])
def test_german_delete_this_deletes_the_selection(said):
    assert is_delete(said, "de")


@pytest.mark.parametrize("said, keys", [
    ("fett machen", ["bold"]),
    ("mach das fett", ["bold"]),
    ("kursiv", ["italic"]),
    ("unterstreiche das", ["underline"]),
    ("fett und kursiv", ["bold", "italic"]),
])
def test_german_bold_italic_and_underline_press_the_shortcut(said, keys):
    assert formatting(said, "de") == keys


@pytest.mark.parametrize("said", [
    "mach es förmlicher",
    "korrigiere die Grammatik",
    # Longer than an exact edit: the AI decides.
    "die Kommas entfernen und mach es kürzer",
    "lösche das Wort Test und schreib es freundlicher",
])
def test_other_german_instructions_go_to_the_ai(said):
    assert mechanical_edit(TEXT, said, "de") is None
    assert not is_delete(said, "de") and formatting(said, "de") is None


def test_german_edits_wait_for_german_commands():
    assert mechanical_edit(TEXT, "alles in Großbuchstaben") is None
    assert not is_delete("das löschen") and formatting("fett machen") is None


def test_english_edits_still_work_with_german_commands_on():
    assert mechanical_edit(TEXT, "make this all caps", "de") == "EINS, ZWEI TEST\n    - DREI"
    assert is_delete("delete this", "de")


def test_every_german_edit_help_names_is_done_in_code():
    """Help's Writing page promises these are done exactly."""
    import re

    from tuxtalk import help_text
    writing = next(text for title, text in help_text.tabs() if title == "Writing")
    block = writing.split("With voice commands in German, the German ones")[1]
    block = block.split("Any other German instruction")[0]
    named = [p for p in re.findall(r"<b>([^<]+)</b>", block) if "..." not in p]
    assert "alles in Großbuchstaben" in named
    for said in named:
        assert (mechanical_edit(TEXT, said, "de") or is_delete(said, "de")
                or formatting(said, "de")), said


@pytest.mark.parametrize("said", ["den Text kleiner machen", "mach das größer", "das hervorheben",
                                  "ändere rot zu blau", "ersetze rot durch blau",
                                  "das Wort rot entfernen", "die Farbe in Rot ändern"])
def test_german_requests_that_may_be_about_the_words_go_to_the_ai(said):
    """Smaller may mean shorter, and bringing out a point is the wording's job:
    only the lettering's size, a colour or the font is refused."""
    assert formatting(said, "de") is None


@pytest.mark.parametrize("lang, said", [("en", "remove the commas"), ("de", "die Punkte entfernen")])
def test_taking_marks_out_changes_nothing_else(lang, said):
    """Text the edit doesn't touch comes back as it was, odd characters too."""
    assert mechanical_edit("A\x00B, c. D", said, lang).startswith("A\x00B")


@pytest.mark.parametrize("text, said, edited", [
    ("am 3.Oktober. Gut.", "die Punkte entfernen", "am 3.Oktober Gut"),
    ("am 3. Oktober, gut.", "die Satzzeichen entfernen", "am 3. Oktober gut"),
])
def test_a_german_date_keeps_its_period_written_close_or_with_the_other_marks(
        text, said, edited):
    assert mechanical_edit(text, said, "de") == edited


@pytest.mark.parametrize("text", ["Kennung 123.Mai.", "abc3.Mai.", "am 32. Mai."])
def test_a_number_that_cant_be_a_day_loses_its_period(text):
    """Only a day, 1 to 31 and a word of its own, keeps its period."""
    assert "." not in mechanical_edit(text, "die Punkte entfernen", "de")
