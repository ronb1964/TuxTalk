"""Every voice command Help and the Voice Commands window list really works.

Both screens show one table (commands_reference). Before, each had its own
list, and they had drifted: each knew phrases the other didn't. Here every
phrase in the table goes through the code that recognises it.
"""
import re

import pytest

from tuxtalk import (ai_cleanup, command_phrases, commands_cards, commands_reference, explain,
                     help_text,
                     key_commands, normalize, spoken_quotes, undo, voice_commands,
                     voice_edit_rules, voice_navigation)

PHRASES = [(c.kind, phrase) for s in commands_reference.SECTIONS for c in s.commands
           for phrase in c.said]


def _plain(markup):
    return re.sub(r"<[^>]+>", "", markup)


def _opened(phrase):
    """What to say before *phrase*: a closing quote closes one that's open
    ("unquote" with none open is the word)."""
    return "open quote " if re.fullmatch(spoken_quotes.CLOSING, phrase) else ""


@pytest.mark.parametrize("kind,phrase", PHRASES)
def test_each_listed_phrase_is_recognised(kind, phrase, monkeypatch):
    if kind == "inline":
        # Said mid-sentence, the words turn into punctuation or formatting.
        typed = normalize.normalize_text(
            f"{_opened(phrase)}apple {phrase} banana {_opened(phrase)}{phrase} cherry")
        assert phrase not in typed.lower()
    elif kind == "key":
        key = "TAB" if "tab" in phrase else "ENTER"
        assert key_commands.split_key_command(f"see you at noon, {phrase}")[1] == key
    elif kind == "undo":
        assert undo.detect_undo_command(phrase) is not None
    elif kind == "fix":
        opened = []
        monkeypatch.setattr(explain, "start_helper_window", lambda *args: opened.append(args))
        # As the speech model writes it: a capital and a period.
        assert voice_commands.handle_fix_word(f"{phrase.capitalize()}.", False)
        assert opened[0][0] == "tuxtalk.fix_word_dialog"
    elif kind == "edit":
        assert voice_edit_rules.spoken_edit(phrase)
    elif kind == "select":
        assert voice_navigation.parse(phrase) is not None
    elif kind == "action":
        assert command_phrases.parse(phrase, navigation=False, actions=True) is not None
    elif kind == "correction":
        assert ai_cleanup.has_correction(f"Meet at 3, {phrase}, 4.")
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
                    said = f"{_opened(phrase)}apple {phrase} banana"
                    assert command.result in normalize.normalize_text(said)


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


@pytest.fixture
def window_text():
    """Every tab of the Voice Commands window, as one text."""
    from gtk_text import tree_text
    from tuxtalk import voice_commands_dialog
    dialog = voice_commands_dialog.show_voice_commands_dialog()
    try:
        return "\n".join(tree_text(dialog))
    finally:
        dialog.destroy()


def test_both_screens_show_every_phrase(help_pages, window_text):
    for _kind, phrase in PHRASES:
        assert phrase in help_pages["Commands"] and phrase in window_text, phrase


def test_the_voice_commands_window_keeps_each_note(window_text):
    """It said that "press tab" at the end presses Tab, and which keys
    clearing the box presses; a note only Help showed was lost there."""
    for section in commands_reference.SECTIONS:
        for command in section.commands:
            if command.note:
                assert _plain(command.note) in window_text, command.note


def test_both_screens_say_where_labs_commands_are_turned_on(help_pages, window_text):
    labs = _plain(commands_cards.LABS_LINE)
    assert labs in window_text
    assert any(s.labs for s in commands_reference.SECTIONS)


def test_both_screens_show_the_one_literal_words_list(help_pages, window_text):
    """Help and the Voice Commands window each had their own, worded
    differently."""
    for page in (help_pages["Commands"], window_text):
        assert page.count("Literal Words") == 1
        assert "It works with: period, comma, press tab" in page


def test_help_shows_commands_right_after_start_here_and_problems_last(help_pages):
    titles = list(help_pages)
    assert titles[:2] == ["Start Here", "Commands"]
    assert titles[-2:] == ["Tips", "Problems"]


def test_every_command_phrase_help_spells_out_works(monkeypatch):
    """Help can't list a desktop command the parser doesn't know."""
    from tuxtalk import config
    monkeypatch.setattr(config, "load_config", lambda: config.Settings(hotkey="F6"))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    writing = next(text for title, text in help_text.tabs() if title == "Writing")
    part = writing.split("Snapping and moving windows", 1)[1].split("<b>Keys</b>", 1)[0]
    body = re.sub(r"(?m)^<b>[^<]+</b>$", "", part)         # the part headings
    phrases = [p for p in re.findall(r"<b>([^<]+)</b>", body) if p != "F6"]
    assert len(phrases) > 20
    for phrase in phrases:
        assert command_phrases.parse(phrase, navigation=False, actions=True), phrase


# Every command TuxTalk understands is listed: each rule of the parsers is
# shown by at least one phrase here, so a command added without a line in
# Help and the Voice Commands window fails these.

def _listed(kind):
    return [p for s in commands_reference.SECTIONS for c in s.commands if c.kind == kind
            for p in c.said]


def _requests():
    return [command_phrases.parse(p, navigation=False, actions=True) for p in _listed("action")]


def test_every_select_and_move_command_is_listed():
    listed = _listed("select")
    missing = [pattern.pattern for pattern, _build in voice_navigation._COMPILED
               if not any(pattern.match(phrase) for phrase in listed)]
    assert missing == []


def test_every_key_command_is_listed():
    keys = {r.name for r in _requests() if isinstance(r, command_phrases.KeyRequest)}
    assert keys == set(command_phrases.KEY_PHRASES.values()) | {"scroll up", "scroll down"}


def test_every_desktop_command_is_listed():
    actions = {r.action for r in _requests() if isinstance(r, command_phrases.DesktopRequest)}
    assert actions == command_phrases.DESKTOP_ACTIONS


def test_every_sound_music_and_system_command_is_listed():
    made = {(type(r).__name__, r.action) for r in _requests()
            if isinstance(r, (command_phrases.SoundRequest, command_phrases.MusicRequest,
                              command_phrases.SystemRequest))}
    assert made == {("SoundRequest", a) for a in ("volume up", "volume down", "mute", "unmute")} \
        | {("MusicRequest", a) for a in ("pause", "play", "next", "previous")} \
        | {("SystemRequest", a) for a in ("lock", "screenshot", "window screenshot", "commands")}


def test_every_fixed_command_phrasing_is_listed():
    """Each way the parser takes a fixed phrase ("turn down the volume")."""
    listed = [p.lower() for p in _listed("action")]
    missing = [pattern.pattern for pattern, _build in command_phrases._FIXED
               if not any(pattern.fullmatch(phrase) for phrase in listed)]
    assert missing == []


def test_every_window_command_is_listed():
    windows = {(r.action, r.app is not None, r.every) for r in _requests()
               if isinstance(r, command_phrases.WindowRequest)}
    assert windows == {("close", False, False), ("minimize", False, False),
                       ("maximize", False, False), ("unmaximize", False, False),
                       ("close", True, False), ("close", True, True),
                       ("minimize", True, False), ("maximize", True, False),
                       ("restore", True, False), ("switch", True, False)}


def test_opening_an_address_and_a_web_search_are_listed():
    kinds = {type(r).__name__ for r in _requests()}
    assert {"OpenRequest", "AddressRequest", "SearchRequest"} <= kinds


def test_each_section_is_in_one_of_the_groups():
    assert {s.group for s in commands_reference.SECTIONS} == set(commands_reference.GROUPS)
