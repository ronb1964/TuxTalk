"""Every voice command Help and the Voice Commands window list really works.

Both screens show one table (commands_reference). Before, each had its own
list, and they had drifted: each knew phrases the other didn't. Here every
phrase in the table goes through the code that recognises it.
"""
import re

import pytest

from tuxtalk import (ai_cleanup, commands_reference, help_text, key_commands, normalize, undo,
                     voice_commands, voice_edit_rules, voice_navigation)
from tuxtalk.voice_commands_dialog import _build_markup

PHRASES = [(c.kind, phrase) for s in commands_reference.SECTIONS for c in s.commands
           for phrase in c.said]


def _plain(markup):
    return re.sub(r"<[^>]+>", "", markup)


@pytest.mark.parametrize("kind,phrase", PHRASES)
def test_each_listed_phrase_is_recognised(kind, phrase):
    if kind == "inline":
        # Said mid-sentence, the words turn into punctuation or formatting.
        typed = normalize.normalize_text(f"apple {phrase} banana {phrase} cherry")
        assert phrase not in typed.lower()
    elif kind == "key":
        key = "TAB" if "tab" in phrase else "ENTER"
        assert key_commands.split_key_command(f"see you at noon, {phrase}")[1] == key
    elif kind == "undo":
        assert undo.detect_undo_command(phrase) is not None
    elif kind == "fix":
        assert phrase in voice_commands._FIX_WORD_PHRASES
    elif kind == "edit":
        assert voice_edit_rules.spoken_edit(phrase)
    elif kind == "select":
        assert voice_navigation.parse(phrase) is not None
    elif kind == "correction":
        assert ai_cleanup._RE_CORRECTION.search(f"meet at 3, {phrase}, 4")
    elif kind == "literal":
        word = re.sub(r"^(literal|the word) ", "", phrase)
        assert normalize.normalize_text(f"apple {phrase} banana") == f"Apple {word} banana."
    else:
        # Any wording works, the AI reads it; the exact jobs are done in code.
        assert kind == "instruction"
        done_in_code = {"make this all caps": "ONE, TWO", "remove the commas": "one two"}
        assert voice_edit_rules.mechanical_edit("one, two", phrase) == done_in_code.get(phrase)


def test_a_symbol_comes_out_as_the_symbol_listed():
    for section in commands_reference.SECTIONS:
        for command in section.commands:
            if command.kind == "inline" and len(command.result) == 1:
                for phrase in command.said:
                    assert command.result in normalize.normalize_text(f"apple {phrase} banana")


def test_formatting_comes_out_as_listed():
    """Not only gone from the text: each makes what the table says."""
    made = {"line break": normalize.LINE_BREAK, "double line break": normalize.LINE_BREAK * 2,
            "three spaces": "apple   banana", "tab character": "apple\tbanana"}
    checked = 0
    for section in commands_reference.SECTIONS:
        for command in section.commands:
            result = command.result.split(" (")[0]
            if command.kind != "inline" or result not in made:
                continue
            for phrase in command.said:
                typed = normalize.normalize_text(f"apple {phrase} banana")
                assert made[result].lower() in typed.lower(), phrase
                assert normalize.LINE_BREAK * 3 not in typed, phrase
                if result == "line break":
                    assert normalize.LINE_BREAK * 2 not in typed, phrase
                checked += 1
    assert checked == 9, "a formatting command was added: give it a line in *made*"


def test_both_screens_show_every_phrase():
    help_page = _plain(help_text._commands())
    short = _plain(_build_markup())
    for _kind, phrase in PHRASES:
        assert phrase in help_page and phrase in short, phrase


def test_the_short_list_keeps_each_note():
    """It said that "press tab" at the end presses Tab, and which keys
    clearing the box presses; a note only Help showed was lost there."""
    short = _plain(_build_markup())
    for section in commands_reference.SECTIONS:
        for command in section.commands:
            if command.note and not section.pairs:
                assert _plain(command.note) in short, command.note


def test_every_way_to_clear_and_to_fix_a_word_is_listed():
    listed = {phrase for _kind, phrase in PHRASES}
    assert undo.EVERYTHING_PATTERNS <= listed
    assert {"fix that", "fix that word"} <= listed


def test_a_labs_heading_puts_its_colon_after_the_brackets():
    help_page = help_text._commands()
    assert "<b>Editing by Voice</b> (TuxTalk Labs: turn on in Preferences → Advanced):" in help_page
    assert "Editing by Voice:" not in _plain(help_page)


def test_both_screens_show_the_one_literal_words_list():
    """Help and the Voice Commands window each had their own, worded
    differently."""
    for page in (_plain(help_text._commands()), _plain(_build_markup())):
        assert page.count("Literal Words") == 1
        assert "It works with: period, comma, press tab" in page
