"""Every voice command Help and the Voice Commands window list really works.

Both screens show one table (commands_reference). Before, each had its own
list, and they had drifted: each knew phrases the other didn't. Here every
phrase in the table goes through the code that recognises it.
"""
import re

import pytest

from tuxtalk import (ai_cleanup, app_commands, app_finder, command_phrases, commands_cards,
                     commands_reference, explain, help_outline, help_text, key_commands,
                     mouse_phrases, normalize, spoken_quotes, undo, voice_commands,
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
    elif kind == "mouse":
        assert command_phrases.parse(phrase, navigation=False, actions=True, grid=True)
    elif kind == "correction":
        assert ai_cleanup.has_correction(f"Meet at 3, {phrase}, 4.")
    elif kind == "literal":
        word = re.sub(r"^(literal|the word) ", "", phrase)
        assert normalize.normalize_text(f"apple {phrase} banana") == f"Apple {word} banana."
    elif kind == "exact":
        assert _DONE_IN_CODE[phrase] == (
            "DELETE" if voice_edit_rules.is_delete(phrase)
            else voice_edit_rules.mechanical_edit(_EDITED, phrase))
    elif kind == "format":
        assert voice_edit_rules.formatting(phrase) in (["bold"], ["italic"], ["underline"])
    else:
        # Any wording works: the AI reads it, nothing in code takes it.
        assert kind == "instruction"
        assert not _done_in_code(phrase) and voice_edit_rules.formatting(phrase) is None


# What each exact edit listed makes of _EDITED: done in code, not by the AI.
_EDITED = "one, two Testing\n    - three"
_DONE_IN_CODE = {
    "make this all caps": "ONE, TWO TESTING\n    - THREE",
    "make this lower case": "one, two testing\n    - three",
    "make this title case": "One, Two Testing\n    - Three",
    "indent this": "    one, two Testing\n        - three",
    "unindent this": "one, two Testing\n- three",
    "make this a bulleted list": "- one, two Testing\n    - three",
    "make this a numbered list": "1. one, two Testing\n    2. three",
    "remove the commas": "one two Testing\n    - three",
    "remove the word testing": "one, two\n    - three",
    "remove the bullets": "one, two Testing\n    three",
    "delete this": "DELETE",
}


def _done_in_code(phrase):
    """An edit made in code, not by the AI: a change to the text, or Delete."""
    changed = voice_edit_rules.mechanical_edit(_EDITED, phrase)
    return changed is not None or voice_edit_rules.is_delete(phrase)


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


def _say_columns(root):
    """Each card row's "what to say" column, as its text."""
    from gi.repository import Gtk
    from gtk_text import widgets
    return [w.get_text() for w in widgets(root)
            if isinstance(w, Gtk.Label) and w.get_style_context().has_class("tt-say")]


def _labels(root):
    """The text of every label in *root*: what shows, without tooltips."""
    from gi.repository import Gtk
    from gtk_text import widgets
    return "\n".join(w.get_text() for w in widgets(root) if isinstance(w, Gtk.Label))


@pytest.fixture
def screens(monkeypatch):
    """Help's Commands page and the Voice Commands window, built, not shown."""
    from gi.repository import Gtk
    from gtk_text import widgets
    from tuxtalk import config, help_dialog, voice_commands_dialog
    monkeypatch.setattr(config, "load_config", config.Settings)
    monkeypatch.setattr(Gtk.Window, "show_all", lambda self: None)
    monkeypatch.setattr(Gtk.Window, "present", lambda self: None)
    help_window = help_dialog.show_help_dialog()
    window = voice_commands_dialog.show_voice_commands_dialog()
    notebook = next(w for w in widgets(help_window) if isinstance(w, Gtk.Notebook))
    commands = next(notebook.get_nth_page(n) for n in range(notebook.get_n_pages())
                    if notebook.get_tab_label_text(notebook.get_nth_page(n)) == "Commands")
    yield commands, window
    help_window.destroy()
    window.destroy()


def test_both_screens_show_every_command_on_its_own_row(screens):
    """Each command's phrases, on a row of their own, in both."""
    listed = [", ".join(c.said) for s in commands_reference.SECTIONS for c in s.commands]
    for screen in screens:
        assert _say_columns(screen) == listed


def test_the_voice_commands_window_keeps_each_note(window_text):
    """It said that "press tab" at the end presses Tab, and which keys
    clearing the box presses; a note only Help showed was lost there."""
    for section in commands_reference.SECTIONS:
        for command in section.commands:
            if command.note:
                assert _plain(command.note) in window_text, command.note


def test_both_screens_say_which_labs_tickbox_each_group_needs(screens):
    """In text that shows, not only a tooltip a keyboard never reaches."""
    for group in commands_reference.GROUPS:
        sections = [s for s in commands_reference.SECTIONS if s.group == group]
        line = _plain(commands_cards.labs_line(sections))
        for screen in screens:
            assert (line in _labels(screen)) if line else True, group
        for section in sections:
            if section.labs:
                assert section.labs in line
    boxes = {s.labs for s in commands_reference.SECTIONS} - {""}
    assert boxes == {commands_reference.LABS_EDIT, commands_reference.LABS_SELECT,
                     commands_reference.LABS_ACTIONS}


def test_the_labs_tickboxes_named_are_the_ones_in_preferences():
    import inspect
    from tuxtalk import prefs_advanced
    source = inspect.getsource(prefs_advanced)
    for box in (commands_reference.LABS_EDIT, commands_reference.LABS_SELECT,
                commands_reference.LABS_ACTIONS):
        assert f'label="{box}:' in source, box


def test_help_says_what_can_i_say_needs_labs(help_pages):
    """Said with the box unticked, "what can I say" is typed as words."""
    assert command_phrases.parse("what can I say", navigation=False, actions=False) is None
    intro = help_pages["Commands"].split("what can I say", 1)[0]
    assert commands_reference.LABS_ACTIONS in " ".join(intro.split())


def test_a_screen_reader_hears_each_card_by_its_heading_and_commands():
    """Its rows are labels no key reaches: the card that takes the focus
    carries them."""
    from gi.repository import Gtk
    for section in commands_reference.SECTIONS:
        card = commands_cards.section_card(section, Gtk.SizeGroup())
        accessible = card.get_accessible()
        assert card.get_can_focus()
        assert accessible.get_name().startswith(section.title)
        if section.labs:
            assert section.labs in accessible.get_name()
        else:
            assert "Labs" not in accessible.get_name()
        for command in section.commands:
            for phrase in command.said:
                assert phrase in accessible.get_description()
            assert _plain(command.result) in accessible.get_description()


def test_a_heading_with_an_ampersand_still_shows():
    from gi.repository import Gtk
    from gtk_text import widgets
    section = commands_reference.Section("Apps & Windows", commands_reference.APPS, ())
    card = commands_cards.section_card(section, Gtk.SizeGroup())
    texts = [w.get_text() for w in widgets(card) if isinstance(w, Gtk.Label)]
    assert "Apps & Windows" in texts


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
    shown = ("Snapping and moving windows", "Desktops", "Sound and music",
             "Lock, screenshot and help")
    body = "\n".join(s.body for part in help_outline.parse(writing).parts
                     for s in part.sections if s.title in shown)
    phrases = [p for p in re.findall(r"<b>([^<]+)</b>", body) if p != "F6"]
    assert len(phrases) > 20
    for phrase in phrases:
        assert command_phrases.parse(phrase, navigation=False, actions=True), phrase


# The other way round: each fixed rule of the parsers (a key or desktop
# action, a window verb, a top-level alternative of a fixed phrase, an exact
# edit, an undo unit, a spoken mark) is shown by a phrase here, so such a
# command added without a row fails these. A new wording of an action
# already listed, or an alternative nested inside a phrase, isn't caught.

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


def _alternatives(pattern):
    """*pattern*'s top-level alternatives ("a|b(?:c|d)" -> "a", "b(?:c|d)")."""
    parts, depth, start = [], 0, 0
    for i, char in enumerate(pattern):
        if char == "(" and pattern[i - 1:i] != "\\":
            depth += 1
        elif char == ")" and pattern[i - 1:i] != "\\":
            depth -= 1
        elif char == "|" and depth == 0:
            parts.append(pattern[start:i])
            start = i + 1
    return parts + [pattern[start:]]


def test_every_fixed_command_phrasing_is_listed():
    """Each way the parser takes a fixed phrase ("turn down the volume"),
    an alternative added to one ("show the voice commands") included."""
    listed = [p.lower() for p in _listed("action")]
    missing = [alternative for pattern, _build in command_phrases._FIXED
               for alternative in _alternatives(pattern.pattern)
               if not any(re.fullmatch(alternative, phrase) for phrase in listed)]
    assert missing == []


def test_every_window_command_is_listed():
    """Every window action, for the window you're in and for an app by name,
    taken from the parser's own tables: a new verb needs a row."""
    windows = {(r.action, r.app is not None, r.every) for r in _requests()
               if isinstance(r, command_phrases.WindowRequest)}
    this_window = {(action, False, False) for _pattern, action in command_phrases._WINDOW_ACTIONS}
    named = {(action, True, False) for action in command_phrases._NAMED_WINDOW_VERBS.values()}
    assert windows == this_window | named | {("close", True, True), ("switch", True, False)}


def test_every_way_to_open_an_app_is_listed():
    """Each verb, a second one, a copy (Flatpak, Snap, AppImage) and the
    desktop's own app for a job ("open the browser")."""
    opened = [r for r in _requests() if isinstance(r, command_phrases.OpenRequest)]
    verbs = {p.split()[0] for p in _listed("action")
             if isinstance(command_phrases.parse(p, navigation=False, actions=True),
                           command_phrases.OpenRequest)}
    assert verbs == set(app_commands.VERBS)
    assert any(r.new for r in opened)
    assert {r.install for r in opened} == {"", *app_commands._INSTALL_WORDS}
    jobs = {app_finder._DEFAULT_WORDS.get(re.sub(r"^the ", "", r.name)) for r in opened}
    assert jobs - {None} == set(app_finder._DEFAULT_WORDS.values())


def test_opening_an_address_and_a_web_search_are_listed():
    kinds = {type(r).__name__ for r in _requests()}
    assert {"OpenRequest", "AddressRequest", "SearchRequest"} <= kinds


def test_every_edit_done_in_code_is_listed():
    """Each exact edit (all caps, a list, indent, remove a word, Delete) and
    each formatting shortcut has a phrase: they work only as worded."""
    listed = [voice_edit_rules._norm(p) for p in _listed("exact")]
    rules = (voice_edit_rules._UPPER, voice_edit_rules._LOWER, voice_edit_rules._TITLE,
             voice_edit_rules._REMOVE_MARKS, voice_edit_rules._BULLETED,
             voice_edit_rules._NUMBERED, voice_edit_rules._INDENT, voice_edit_rules._UNINDENT,
             voice_edit_rules._REMOVE_BULLETS, voice_edit_rules._REMOVE_WORD,
             voice_edit_rules._DELETE)
    missing = [rule.pattern for rule in rules if not any(rule.match(p) for p in listed)]
    assert missing == []
    shortcuts = {k for p in _listed("format") for k in voice_edit_rules.formatting(p)}
    assert shortcuts == set(voice_edit_rules._SHORTCUTS.values())


def test_every_undo_and_fix_a_word_command_is_listed():
    units = {undo.detect_undo_command(p)[0] for p in _listed("undo")}
    assert units == {"word", "sentence", "paragraph", "everything"}
    fixes = {p for p in voice_commands._FIX_WORD_PHRASES if not p.endswith(" please")}
    assert fixes == set(_listed("fix"))


def test_every_spoken_mark_is_listed():
    """Each rule normalize turns words into a mark or a break by is shown."""
    listed = _listed("inline")
    rules = normalize._MULTI_WORD_REPLACEMENTS + normalize._SINGLE_WORD_REPLACEMENTS
    missing = [pattern.pattern for pattern, _mark in rules
               if not any(pattern.search(f"apple {p} banana") for p in listed)]
    assert missing == []


def test_each_section_is_in_one_of_the_groups():
    assert {s.group for s in commands_reference.SECTIONS} == set(commands_reference.GROUPS)


def test_every_mouse_action_is_listed():
    """Each thing the grid can do, from the parser's own request type, has a
    listed phrase; so do the clicks that need no grid."""
    made = [command_phrases.parse(p, navigation=False, actions=True, grid=True)
            for p in _listed("mouse")]
    assert {r.action for r in made} == {"grid", "zoom", "click", "double click",
                                        "right click", "middle click", "move", "nudge",
                                        "cancel"}
    assert all(isinstance(r, mouse_phrases.MouseRequest) for r in made)
    no_grid = [p for p in _listed("mouse")
               if command_phrases.parse(p, navigation=False, actions=True, grid=False)]
    assert {"mouse grid", "left click", "right click"} <= set(no_grid)
    assert "click" not in no_grid and "five" not in no_grid
