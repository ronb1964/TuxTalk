""""bearbeite das, ..." with voice commands in German: voice editing
without its key, as "edit that, ..." is in English."""
import pytest

from tuxtalk.voice_edit_rules import spoken_edit


@pytest.mark.parametrize("said, instruction", [
    ("Bearbeite das, mach es kürzer.", "mach es kürzer."),
    ("Das bearbeiten. Übersetze es ins Englische", "Übersetze es ins Englische"),
    ("Ändere das", ""),
    ("bearbeite das mach es formeller", "mach es formeller"),
    # English stays understood.
    ("Edit that, make it shorter", "make it shorter"),
])
def test_a_german_edit_instruction_is_found(said, instruction):
    assert spoken_edit(said, "de") == instruction


@pytest.mark.parametrize("said", [
    "Bearbeite das Dokument bis morgen.",
    "Ändere das Passwort regelmäßig.",
])
def test_a_german_sentence_starting_the_same_way_stays_text(said):
    assert spoken_edit(said, "de") is None


def test_german_waits_for_german_commands():
    assert spoken_edit("Bearbeite das, mach es kürzer.") is None
