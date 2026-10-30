"""
The voice commands, in one table that Help's Commands tab and the Voice
Commands window both show, each in its own layout.

Two hand-written lists had drifted apart: each knew phrases the other didn't.
tests/test_commands_reference.py checks every phrase here against the code
that recognises it, so a command can't be listed that doesn't work.

Each command is checked by its *kind*: "inline" (normalize_text turns it into
punctuation or formatting mid-sentence), "key" (a closing "press enter",
split_key_command), "undo" (detect_undo_command), "fix" (Fix a Word's
phrases), "edit" ("edit that, ..."), "select" (voice_navigation),
"correction" (a self-correction the AI acts on, found by its pattern),
"instruction" (an example of what to tell the AI; any wording works), or
"literal" (normalize_text types the word after "literal" or "the word").
"""
from typing import NamedTuple

from .normalize import LITERAL_REPLACEMENTS

_LABS = "TuxTalk Labs: turn on in Preferences → Advanced"


class Command(NamedTuple):
    """The ways to say one command, what it does, and Help's longer note."""
    said: tuple
    result: str
    kind: str
    note: str = ""


class Section(NamedTuple):
    """A heading's commands; *pairs* puts two on a line in the short list."""
    title: str
    commands: tuple
    labs: bool = False
    pairs: bool = False
    note: str = ""


def _inline(*said, result, note=""):
    return Command(said, result, "inline", note)


def _literal(word, result, *said):
    """The command that types *word* itself; *said* adds other ways to say it."""
    return Command((f"literal {word}", *said), f'"{word}" (not {result})', "literal")


SECTIONS = (
    Section("Punctuation", pairs=True, commands=(
        _inline("comma", result=","),
        _inline("period", "full stop", result="."),
        _inline("question mark", result="?"),
        _inline("exclamation point", "exclamation mark", result="!"),
        _inline("semicolon", result=";"),
        _inline("colon", result=":"),
        _inline("apostrophe", result="'"),
        _inline("quote", result="quotes around what's between: quote hi quote → “hi”",
                note='A lone one is a plain "'),
        _inline("open quote", "open quotes", result="“"),
        _inline("close quote", "close quotes", result="”"),
        _inline("hyphen", "dash", result="-"),
        _inline("em dash", result="—"),
        _inline("dot dot dot", "ellipsis", result="…"),
    )),
    Section("Brackets", pairs=True, commands=(
        _inline("open parenthesis", result="("),
        _inline("close parenthesis", result=")"),
        _inline("open bracket", result="["),
        _inline("close bracket", result="]"),
        _inline("open brace", result="{"),
        _inline("close brace", result="}"),
    )),
    Section("Formatting", commands=(
        _inline("new line", "newline", "return", "line break", result="line break"),
        _inline("new paragraph", "paragraph break", result="double line break"),
        _inline("press tab", result="tab character (just <b>tab</b> is the word)"),
        Command(("press tab", "press the tab key"),
                "at the end: presses the Tab key, to the next box in a form (in Claude "
                "Desktop, four spaces)", "key",
                "Help → Writing → Filling in a form has the steps"),
        _inline("soft break", "soft line", result="three spaces"),
        Command(("press enter", "hit enter"), "at the end: types it, then presses Enter",
                "key", "It sends a chat message or runs a command. <b>literal press enter</b> "
                       "types the words"),
    )),
    Section("Undo", commands=(
        Command(("delete last word", "undo last word", "remove last word"),
                "delete the last word you dictated", "undo"),
        Command(("delete last sentence", "undo last sentence"),
                "delete back to the previous sentence", "undo"),
        Command(("delete last paragraph", "undo last paragraph"),
                "delete back to the last line break", "undo"),
        Command(("delete last 3 words", "undo last two sentences"),
                "counted undo (1 to 10, in words or digits)", "undo"),
    )),
    Section("Clear the Whole Box", commands=(
        Command(("delete everything", "delete all", "clear everything", "clear all",
                 "undo everything", "undo all"),
                "empty the whole box, including text TuxTalk didn't type; in a terminal, "
                "the line you're typing", "undo",
                "It presses Ctrl+A then Backspace; in a terminal, Ctrl+E then Ctrl+U"),
    )),
    Section("Fixing a Word", commands=(
        Command(("fix that word", "fix that"),
                "open Fix a Word on what you just said, to teach TuxTalk the right spelling "
                "for next time", "fix"),
    )),
    Section("Editing by Voice", labs=True, commands=(
        Command(("make this more formal", "fix the grammar", "make this all caps",
                 "remove the commas"),
                "changes the selected text: hold Shift with your dictation key as you "
                "say it", "instruction", "The Writing section has the details"),
        Command(("edit that, make it shorter",), "right after dictating, no keys: changes it",
                "edit"),
    )),
    Section("Select and Move", labs=True, commands=(
        Command(("select that", "select the last sentence", "select the last 3 words"),
                "select what you just said", "select"),
        Command(("select all", "select the line", "select the next word",
                 "select to the end of the line"), "select on the screen", "select"),
        Command(("go to the end", "go to the start of the line", "go back 3 words",
                 "go up 2 lines"), "move the cursor", "select"),
        Command(("unselect",), "drop the selection", "select"),
    )),
    Section("Changing Your Mind", note="Needs <b>Fix self-corrections with AI</b> turned on "
                                       "in Preferences → Advanced (a one-time download of "
                                       "about {ai_size})", commands=(
        Command(("no wait", "I mean", "make that", "scratch that", "or rather"),
                'correct yourself mid-sentence: "meet at 3, no wait, 4" → "meet at 4"',
                "correction"),
    )),
    Section("Literal Words",
            note="Say <b>literal</b> or <b>the word</b> before a punctuation or line-break "
                 "word to type the word itself. It works with: "
                 + ", ".join(LITERAL_REPLACEMENTS) + ".",
            commands=(
                _literal("period", ".", "the word period"),
                _literal("comma", ",", "the word comma"),
                _literal("press tab", "a tab character"),
                _literal("new line", "a line break", "the word new line"),
                _literal("question mark", "?"),
                _literal("exclamation point", "!"),
            )),
)


def _said(command):
    return " / ".join(f"<b>{phrase}</b>" for phrase in command.said)


def _title(section, colon=""):
    """The section's heading; a Labs one says so in brackets before the colon."""
    if section.labs:
        return f"<b>{section.title}</b> ({_LABS}){colon}"
    return f"<b>{section.title}{colon}</b>"


def _note(section):
    from .ai_cleanup import DOWNLOAD_SIZE_TEXT
    return section.note.format(ai_size=DOWNLOAD_SIZE_TEXT)


def help_markup():
    """Help's layout: a bullet per command, with its longer note."""
    blocks = []
    for section in SECTIONS:
        lines = [_title(section, ":")]
        for command in section.commands:
            lines.append(f"• {_said(command)} → {command.result}")
            if command.note:
                lines.append(f"  {command.note}")
        if section.note:
            lines.append(_note(section))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def short_markup():
    """The Voice Commands window's layout: an indented line per command, two
    to a line where they're short (without their notes, for the room), and
    each other note under its command."""
    blocks = []
    for section in SECTIONS:
        if section.pairs:
            entries = [f"{_said(c)} → {c.result}" for c in section.commands]
            lines = ["    ".join(entries[i:i + 2]) for i in range(0, len(entries), 2)]
        else:
            lines = []
            for command in section.commands:
                lines.append(f"{_said(command)} → {command.result}")
                if command.note:
                    lines.append(f"  {command.note}")
        lines = [_title(section)] + [f"  {line}" for line in lines]
        if section.note:
            lines.append(_note(section))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
