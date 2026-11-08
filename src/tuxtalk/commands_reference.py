"""
The voice commands, in one table that Help's Commands tab and the Voice
Commands window both show, as cards (commands_cards).

Two hand-written lists had drifted apart: each knew phrases the other didn't.
tests/test_commands_reference.py checks every phrase here against the code
that recognises it, and the other way round each fixed rule of that code (a
spoken mark, a key or desktop action, a window verb, an exact edit, a
top-level alternative of a fixed phrase) against the phrases here. Another
wording of an action already listed isn't required; counts and app names are
shown by an example each, and what to tell the AI can be worded any way.

Each command is checked by its *kind*: "inline" (normalize_text turns it into
punctuation or formatting mid-sentence), "key" (a closing "press enter",
split_key_command), "undo" (detect_undo_command), "fix" (Fix a Word's
phrases), "edit" ("edit that, ..."), "select" (voice_navigation),
"correction" (a self-correction the AI acts on, found by its pattern),
"instruction" (an example of what to tell the AI; any wording works),
"exact" (an edit done in code, voice_edit_rules), "format" (a word
processor's own shortcut),
"action" (command_phrases.parse: the Labs apps, windows, desktops and sound),
"mouse" (mouse_phrases: while a grid shows, or for the clicks at any time), or
"literal" (normalize_text types the word after "literal" or "the word").
"""
from typing import NamedTuple

from .normalize import LITERAL_REPLACEMENTS

# The TuxTalk Labs tickboxes (Preferences → Advanced) that turn commands on.
LABS_EDIT = "Voice editing"
LABS_SELECT = "Select and move"
LABS_ACTIONS = "Apps, windows, keys and the mouse by voice"


class Command(NamedTuple):
    """The ways to say one command (plain text), what it does, and a note
    under that (both Pango markup: <b>tab</b>)."""
    said: tuple
    result: str
    kind: str
    note: str = ""


class Section(NamedTuple):
    """A heading's commands, shown in *group* (a tab of the Voice Commands
    window, a run of cards in Help); *labs* names the TuxTalk Labs tickbox
    they need, if any."""
    title: str
    group: str
    commands: tuple
    labs: str = ""
    note: str = ""


# The groups, in the order the window's tabs and Help's cards show them.
DICTATION, FIXING, EDITING = "Dictation", "Fixing", "Editing"
APPS, KEYS, SOUND = "Apps and Windows", "Keys and Web", "Sound and System"
GROUPS = (DICTATION, FIXING, EDITING, APPS, KEYS, SOUND)


def _inline(*said, result, note=""):
    return Command(said, result, "inline", note)


def _literal(word, result, *said):
    """The command that types *word* itself; *said* adds other ways to say it."""
    return Command((f"literal {word}", *said), f'"{word}" (not {result})', "literal")


SECTIONS = (
    Section("Punctuation", DICTATION, commands=(
        _inline("comma", result=","),
        _inline("period", "full stop", result="."),
        _inline("question mark", result="?"),
        _inline("exclamation point", "exclamation mark", result="!"),
        _inline("semicolon", result=";"),
        _inline("colon", result=":"),
        _inline("apostrophe", result="'"),
        _inline("quote", result="quotes around what's between: quote hi quote → “hi”",
                note='Said as a word ("a quote for the job", "to quote him") it stays '
                     'the word; a lone one otherwise is a plain "'),
        _inline("open quote", "begin quote", result="“"),
        _inline("close quote", "end quote", "unquote", result="”",
                note='"unquote" and "end quote" with no quote open stay the words '
                     '("the end quote")'),
        _inline("quote unquote", result="“next word”",
                note="a quote unquote expert → a “expert”"),
        _inline("hyphen", "dash", result="-"),
        _inline("em dash", result="—"),
        _inline("dot dot dot", "ellipsis", result="…"),
    )),
    Section("Brackets", DICTATION, commands=(
        _inline("open parenthesis", result="("),
        _inline("close parenthesis", result=")"),
        _inline("open bracket", result="["),
        _inline("close bracket", result="]"),
        _inline("open brace", result="{"),
        _inline("close brace", result="}"),
    )),
    Section("Formatting", DICTATION, commands=(
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
    Section("Literal Words", DICTATION,
            note="Say <b>literal</b> or <b>the word</b> before a punctuation or line-break "
                 "word to type the word itself. It works with: "
                 + ", ".join(LITERAL_REPLACEMENTS) + ". <b>literal</b> before a command "
                 "from Select and Move, Apps and Windows, Keys and Web or Sound and "
                 "System types its words too (<b>literal open Firefox</b>).",
            commands=(
                _literal("period", ".", "the word period"),
                _literal("comma", ",", "the word comma"),
                _literal("press tab", "a tab character"),
                _literal("new line", "a line break", "the word new line"),
                _literal("question mark", "?"),
                _literal("exclamation point", "!"),
            )),
    Section("Undo", FIXING, commands=(
        Command(("delete last word", "undo last word", "remove last word"),
                "delete the last word you dictated", "undo"),
        Command(("delete last sentence", "undo last sentence"),
                "delete back to the previous sentence", "undo"),
        Command(("delete last paragraph", "undo last paragraph"),
                "delete back to the last line break", "undo"),
        Command(("delete last 3 words", "undo last two sentences"),
                "counted undo (1 to 10, in words or digits)", "undo"),
    )),
    Section("Clear the Whole Box", FIXING, commands=(
        Command(("delete everything", "delete all", "clear everything", "clear all",
                 "undo everything", "undo all"),
                "empty the whole box, including text TuxTalk didn't type; in a terminal, "
                "the line you're typing", "undo",
                "It presses Ctrl+A then Backspace; in a terminal, Ctrl+E then Ctrl+U"),
    )),
    Section("Fixing a Word", FIXING, commands=(
        Command(("fix that word", "fix that", "fix a word", "fix the word", "fix this word",
                 "fix the last word"),
                "open Fix a Word on what you just said, to teach TuxTalk the right spelling "
                "for next time", "fix"),
    )),
    Section("Changing Your Mind", FIXING,
            note="Needs <b>Fix self-corrections with AI</b> turned on in Preferences → "
                 "Advanced (a one-time download of about {ai_size})",
            commands=(
        Command(("no wait", "I mean", "make that", "scratch that", "or rather"),
                'correct yourself mid-sentence: "meet at 3, no wait, 4" → "meet at 4"',
                "correction"),
    )),
    Section("Editing by Voice", EDITING, labs=LABS_EDIT, commands=(
        Command(("make this more formal", "fix the grammar", "make it friendlier"),
                "the AI rewrites the selected text: hold Shift with your dictation key as "
                "you say it", "instruction", "Help → Writing has the details"),
        Command(("make this all caps", "make this lower case", "make this title case",
                 "indent this", "unindent this"), "the same way, done exactly, without the AI",
                "exact"),
        Command(("make this a bulleted list", "make this a numbered list"),
                "each line a point, without the AI", "exact",
                "One paragraph goes to the AI, which decides where the points are"),
        Command(("remove the commas", "remove the word testing", "remove the bullets",
                 "delete this"), "takes out just that, or the whole selection", "exact"),
        Command(("make this bold", "make this italic", "underline this"),
                "presses the app's own shortcut, in LibreOffice and other word processors",
                "format", "Font, size and colour can't be changed by voice"),
        Command(("edit that, make it shorter",), "right after dictating, no keys: changes it",
                "edit"),
    )),
    Section("Select and Move", EDITING, labs=LABS_SELECT, commands=(
        Command(("select that", "select the last sentence", "select the last 3 words",
                 "select the last paragraph"), "select what you just said", "select"),
        Command(("select all", "select the line", "select the next word",
                 "select the previous 2 words", "select the next line",
                 "select the previous line"), "select on the screen", "select"),
        Command(("select to the end of the line", "select to the start of the line",
                 "select to the end", "select to the top"),
                "select from the cursor to there", "select"),
        Command(("go to the end of the line", "go to the start of the line",
                 "go to the end", "go to the start"), "move the cursor there", "select"),
        Command(("go back 3 words", "go forward a word", "next word", "previous word",
                 "go back 2 letters", "go forward 4 characters", "go up 2 lines",
                 "go down a line"), "move the cursor", "select"),
        Command(("unselect", "clear the selection"), "drop the selection", "select"),
    )),
    Section("Apps", APPS, labs=LABS_ACTIONS, commands=(
        Command(("open Firefox", "launch Firefox", "start Firefox"),
                "opens it, or brings its window to the front", "action"),
        Command(("open another Firefox", "open new Firefox"), "starts a second one", "action"),
        Command(("open Firefox Flatpak", "open Firefox Snap", "open Firefox AppImage"),
                "that copy of it, when you have more than one", "action"),
        Command(("open the browser", "open email", "open files", "open the text editor",
                 "open the terminal"), "the app your desktop uses for that", "action"),
        Command(("switch to Firefox",), "brings its window to the front", "action"),
        Command(("close Firefox", "close all Firefox"),
                "closes its window (all of them); an app with unsaved work asks to save "
                "first", "action"),
        Command(("minimize Firefox", "maximize Firefox", "restore Firefox"),
                "its window, by name", "action"),
    ), note="Any app on the computer, by the name in its menu entry."),
    Section("The Window You're In", APPS, labs=LABS_ACTIONS, commands=(
        Command(("close window", "close this window"),
                "closes it; an app with unsaved work asks to save first", "action"),
        Command(("minimize window", "maximize window", "restore window"),
                "hides it, fills the screen, or puts it back", "action"),
        Command(("snap left", "snap right", "snap top", "snap bottom",
                 "move window to the left"), "fills that half of the screen", "action"),
        Command(("snap top left", "snap top right", "snap bottom left", "snap bottom right"),
                "fills that quarter", "action"),
        Command(("center window", "centre window", "move window to the center"),
                "moves it to the middle", "action"),
        Command(("move window to the other screen",),
                "moves it to your other screen (with two or more)", "action"),
    )),
    Section("The Mouse", APPS, labs=LABS_ACTIONS, commands=(
        Command(("mouse grid", "show grid"),
                "numbers each part of the screen (a second screen carries on from 10)",
                "mouse"),
        Command(("five", "number five"), "zooms into that part, with a new grid inside it",
                "mouse"),
        Command(("click", "double click", "right click", "middle click"),
                "clicks the middle of what the grid shows", "mouse"),
        Command(("click five", "right click 3"), "clicks that part straight away", "mouse"),
        Command(("move here", "move five"), "puts the pointer there without clicking",
                "mouse"),
        Command(("left", "right 3", "up", "down two"),
                "moves the click dot a little (a number moves it more)", "mouse"),
        Command(("cancel", "close grid", "hide grid"), "closes the grid", "mouse"),
        Command(("left click", "right click", "double click", "middle click"),
                "with no grid: clicks where the pointer is", "mouse"),
    ), note="Where the desktop's own zoom can be used (KDE, GNOME, Cinnamon) it zooms "
            "in about three times on the part you say, and goes back after the click. "
            "The pointer hides while the grid shows; a dot marks the spot. "
            "With no grid showing, a bare number or <b>click</b> is typed as a word. "
            "The grid closes by itself after a minute, a click, Esc, or anything else "
            "you dictate. If the pointer can't be put exactly there, nothing is clicked."),
    Section("Desktops", APPS, labs=LABS_ACTIONS, commands=(
        Command(("show desktop",), "hides the windows; say it again to bring them back",
                "action"),
        Command(("show all windows",), "every window at once, to pick one", "action"),
        Command(("next desktop", "previous desktop", "switch to desktop two",
                 "go to desktop three"), "changes the desktop (one to nine)", "action"),
        Command(("move window to desktop two", "move window to the next desktop",
                 "move window to the previous desktop"),
                "sends the window you're in there", "action"),
    )),
    Section("Tabs and Pages", KEYS, labs=LABS_ACTIONS, commands=(
        Command(("new tab", "close tab", "next tab", "previous tab", "reopen tab"),
                "in a browser or any app with tabs", "action"),
        Command(("go back a page", "go forward a page", "reload the page", "refresh the page"),
                "back, forward, reload", "action"),
        Command(("page up", "page down", "go to the top", "go to the bottom"),
                "a screen at a time, or all the way", "action"),
        Command(("scroll down", "scroll up", "scroll down 5"),
                "scrolls what's under the mouse pointer; a number scrolls more", "action"),
        Command(("find on page",), "opens the page's search", "action"),
    )),
    Section("Everyday Keys", KEYS, labs=LABS_ACTIONS, commands=(
        Command(("save the file", "select all", "copy the selection", "paste it",
                 "press escape"), "presses the key in the window you're in", "action"),
    ), note="One word alone (copy, paste, save) is typed: people say those as answers."),
    Section("Addresses and Search", KEYS, labs=LABS_ACTIONS, commands=(
        Command(("go to youtube dot com",), "opens the address; in a browser, in that tab",
                "action"),
        Command(("web search cheap tires",), "your browser's own search, in a new tab",
                "action"),
    )),
    Section("Sound and Music", SOUND, labs=LABS_ACTIONS, commands=(
        Command(("volume up", "volume down", "volume up 3", "turn down the volume"),
                "louder or quieter; a number for more steps", "action"),
        Command(("mute sound", "unmute sound"), "turns the sound off or on", "action"),
        Command(("pause music", "play music", "resume the music"),
                "the player that's playing, or the paused one", "action"),
        Command(("next track", "previous track", "next song"), "in that player", "action"),
    )),
    Section("Lock, Screenshots and Help", SOUND, labs=LABS_ACTIONS, commands=(
        Command(("lock screen", "lock the computer"),
                "your desktop's own lock; the ready beep means it's locked", "action"),
        Command(("take a screenshot",), "the whole screen, saved to your screenshots folder",
                "action"),
        Command(("take a screenshot of this window", "screenshot this window",
                 "screenshot the window", "take a window screenshot"),
                "just the window you're in (GNOME and Pop!_OS: pick it in the screenshot "
                "tool)", "action"),
        Command(("what can I say", "show the voice commands"), "opens this list", "action"),
    )),
)


def section_note(section):
    """*section*'s note, with the AI download's size filled in."""
    from .ai_download import DOWNLOAD_SIZE_TEXT
    return section.note.format(ai_size=DOWNLOAD_SIZE_TEXT)
