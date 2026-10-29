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
        assert key_commands.split_key_command(f"see you at noon, {phrase}")[1] == "ENTER"
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
    else:
        assert kind == "instruction"        # anything works: the AI reads it


def test_a_symbol_comes_out_as_the_symbol_listed():
    for section in commands_reference.SECTIONS:
        for command in section.commands:
            if command.kind == "inline" and len(command.result) == 1:
                for phrase in command.said:
                    assert command.result in normalize.normalize_text(f"apple {phrase} banana")


def test_both_screens_show_every_phrase():
    help_page = _plain(help_text._commands())
    short = _plain(_build_markup())
    for _kind, phrase in PHRASES:
        assert phrase in help_page and phrase in short, phrase


def test_every_way_to_clear_and_to_fix_a_word_is_listed():
    listed = {phrase for _kind, phrase in PHRASES}
    assert undo.EVERYTHING_PATTERNS <= listed
    assert {"fix that", "fix that word"} <= listed
