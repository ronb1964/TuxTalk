"""A custom command must fire whichever way Whisper spelled the number.

Whisper decides for itself whether a spoken number becomes "one" or "1", and it
is not consistent. Measured across 1,073 real dictations: a number followed by a
unit came out as a digit 20 times against 3 spelled out, and counting sequences
30 against 4 — but the same spoken phrase produced both forms on different days.
Commands match the raw transcription, so without this a trigger containing a
number would fire only when Whisper happened to pick the form the user typed.

The equivalence is applied to MATCHING only. Dictated text is untouched: what
gets typed still comes out exactly as Whisper and the normalizer produced it.
"""

import pytest

from tuxtalk import app
from tuxtalk import live_settings as live


@pytest.fixture
def commands(monkeypatch):
    def _install(mapping):
        monkeypatch.setattr(live, "commands", mapping, raising=False)
    return _install


def _run(text):
    result, _protected = app._apply_custom_commands(text)
    return result


class TestEitherFormFires:
    def test_a_word_trigger_matches_a_digit_transcription(self, commands):
        """Typed 'one', Whisper wrote '1'."""
        commands({"test phrase one": "IT WORKED"})
        assert _run("Test Phrase 1") == "IT WORKED"

    def test_a_digit_trigger_matches_a_word_transcription(self, commands):
        """And the reverse, so neither spelling is the privileged one."""
        commands({"test phrase 1": "IT WORKED"})
        assert _run("Test phrase one") == "IT WORKED"

    def test_the_matching_form_still_works(self, commands):
        commands({"test phrase one": "IT WORKED"})
        assert _run("test phrase one") == "IT WORKED"

    def test_it_works_mid_sentence(self, commands):
        commands({"route two": "Route 2 North"})
        assert _run("take route 2 tomorrow") == "take Route 2 North tomorrow"

    @pytest.mark.parametrize("word,digit", [
        ("zero", "0"), ("one", "1"), ("two", "2"), ("three", "3"),
        ("nine", "9"), ("ten", "10"), ("twelve", "12"), ("twenty", "20"),
        ("fifty", "50"),
    ])
    def test_each_supported_number(self, commands, word, digit):
        commands({f"level {word}": "OK"})
        assert _run(f"level {digit}") == "OK"


class TestItStaysNarrow:
    def test_a_number_inside_a_longer_word_is_not_a_match(self, commands):
        """'one' in 'money' must not turn the phrase into a number token."""
        commands({"money talks": "CASH"})
        assert _run("money talks") == "CASH"
        assert _run("m1ney talks") == "m1ney talks"

    def test_an_unrelated_number_does_not_fire_the_command(self, commands):
        commands({"test phrase one": "IT WORKED"})
        assert _run("test phrase two") == "test phrase two"
        assert _run("test phrase 2") == "test phrase 2"

    def test_a_command_without_numbers_is_unaffected(self, commands):
        commands({"app image": "AppImage"})
        assert _run("the app image build") == "the AppImage build"

    def test_dictation_with_no_command_is_returned_unchanged(self, commands):
        commands({"test phrase one": "IT WORKED"})
        text = "I recorded 5 minutes of audio and one of them failed"
        assert _run(text) == text

    def test_the_longest_phrase_still_wins(self, commands):
        """Longest-first ordering must survive the number handling."""
        commands({"route two": "SHORT", "route two north": "LONG"})
        assert _run("take route 2 north") == "take LONG"


class TestQuotedReplacementsStillBypassNormalization:
    def test_a_quoted_replacement_is_protected(self, commands):
        commands({"my tag one": '"/btw "'})
        result, protected = app._apply_custom_commands("my tag 1")
        assert result != "my tag 1"
        assert list(protected.values()) == ["/btw "]
