"""Teaching TuxTalk a word from a recent dictation (Recent Dictations > Fix a Word).

The fix is saved as an ordinary custom voice command ("bamboo studio" =
"BambuStudio"), so it works for every engine: it rewrites the transcription
after the model heard it, which Parakeet has no other way to take.
"""

import pytest

from tuxtalk import config, vocabulary
from tuxtalk import live_settings as live


# --- which words were picked -------------------------------------------------

@pytest.mark.parametrize("text, first, last, phrase", [
    ("I use bamboo studio daily", 2, 3, "bamboo studio"),
    ("Open Bamboo Studio.", 1, 2, "bamboo studio"),        # case and full stop
    ("He said \"gift tarot\", then left", 2, 3, "gift tarot"),  # quotes and comma
    ("why do tool works", 0, 2, "why do tool"),
    ("it's fine", 0, 0, "it's"),                             # inner apostrophe kept
])
def test_phrase_is_the_selection_lowercased_without_edge_punctuation(text, first, last, phrase):
    assert vocabulary.phrase_for(text, first, last) == phrase


def test_selection_order_does_not_matter():
    assert vocabulary.phrase_for("a b c d", 3, 1) == "b c d"


def test_selection_of_only_punctuation_gives_empty_phrase():
    assert vocabulary.phrase_for("well -- okay", 1, 1) == ""


# --- the corrected dictation -------------------------------------------------

def test_fixed_text_replaces_the_words_and_keeps_surrounding_punctuation():
    text = "I opened Bamboo Studio, then printed."
    assert vocabulary.fixed_text(text, 2, 3, "BambuStudio") == \
        "I opened BambuStudio, then printed."


def test_fixed_text_keeps_quotes_and_the_rest_untouched():
    text = 'He said "gift tarot" twice.\nNext line stays.'
    assert vocabulary.fixed_text(text, 2, 3, "Giftara") == \
        'He said "Giftara" twice.\nNext line stays.'


def test_fixed_text_keeps_original_spacing_elsewhere():
    assert vocabulary.fixed_text("one  two three", 2, 2, "3") == "one  two 3"


# --- when to warn ------------------------------------------------------------

@pytest.mark.parametrize("phrase", ["the", "four", "and", "is", "you"])
def test_common_single_words_need_a_warning(phrase):
    assert vocabulary.is_risky(phrase)


@pytest.mark.parametrize("phrase", ["bamboo studio", "ydotool", "giftara", "the van"])
def test_uncommon_words_and_phrases_do_not(phrase):
    assert not vocabulary.is_risky(phrase)


# --- saving ------------------------------------------------------------------

@pytest.fixture
def commands_file(tmp_path, monkeypatch):
    path = tmp_path / "custom_commands.toml"
    monkeypatch.setattr(config, "CUSTOM_COMMANDS_PATH", str(path))
    return path


def test_save_adds_to_existing_voice_commands(commands_file):
    config.save_custom_commands({"my email address": "me@example.com"})

    previous = vocabulary.save_fix("bamboo studio", "BambuStudio")

    assert previous is None
    assert config.load_custom_commands() == {
        "my email address": "me@example.com",
        "bamboo studio": "BambuStudio",
    }


def test_save_reports_what_it_replaced(commands_file):
    config.save_custom_commands({"gift tarot": "Giftarot"})

    assert vocabulary.save_fix("gift tarot", "Giftara") == "Giftarot"
    assert config.load_custom_commands() == {"gift tarot": "Giftara"}


def test_existing_fix_is_found_before_saving(commands_file):
    config.save_custom_commands({"gift tarot": "Giftarot"})
    assert vocabulary.existing_fix("gift tarot") == "Giftarot"
    assert vocabulary.existing_fix("bamboo studio") is None


def test_save_refuses_when_the_commands_file_is_unreadable(commands_file):
    commands_file.write_text("this is [not valid toml")

    with pytest.raises(config.ConfigNotLoadedError):
        vocabulary.save_fix("bamboo studio", "BambuStudio")

    assert commands_file.read_text() == "this is [not valid toml"


def test_saved_fix_is_applied_to_the_next_dictation(commands_file, monkeypatch):
    """End to end with the dictation service's own matcher."""
    from tuxtalk import app

    vocabulary.save_fix("bamboo studio", "BambuStudio")
    monkeypatch.setattr(live, "commands", config.load_custom_commands())

    text, _protected = app._apply_custom_commands("i opened Bamboo Studio today")
    assert text == "i opened BambuStudio today"


# --- mapping a click or drag in the text box to whole words -------------------

@pytest.mark.parametrize("start, end, expected", [
    (10, 10, (2, 2)),    # click inside "Bamboo"
    (9, 9, (2, 2)),      # click right at its first letter
    (15, 15, (2, 2)),    # click right after its last letter
    (11, 18, (2, 3)),    # drag from inside "Bamboo" into "Studio"
    (18, 11, (2, 3)),    # dragged backwards
    (3, 40, (1, 5)),     # sloppy drag snaps to whole words
])
def test_word_range_snaps_clicks_and_drags_to_whole_words(start, end, expected):
    text = "I opened Bamboo Studio, then printed."
    #       0 2      9     16     23   28
    assert vocabulary.word_range(text, start, end) == expected


def test_word_range_is_none_when_nothing_is_under_the_click():
    assert vocabulary.word_range("one   two", 4, 4) is None
    assert vocabulary.word_range("", 0, 0) is None


def test_word_span_covers_the_picked_words():
    text = "I opened Bamboo Studio, then printed."
    assert vocabulary.word_span(text, 2, 3) == (9, 23)


# --- Fix a Word also feeds Your Words -----------------------------------------

def test_a_fixed_word_is_added_to_your_words(commands_file):
    from tuxtalk import custom_words
    vocabulary.save_fix("bamboo studio", "BambuStudio")
    vocabulary.save_fix("tux talk", "TuxTalk")
    assert custom_words.load() == ["BambuStudio", "TuxTalk"]


def test_a_word_already_there_is_not_added_twice(commands_file):
    from tuxtalk import custom_words
    custom_words.save(["bambustudio"])
    vocabulary.save_fix("bamboo studio", "BambuStudio")
    assert custom_words.load() == ["bambustudio"]


@pytest.mark.parametrize("replacement", [
    "me@example.com", "https://example.com", '"literal text"',
    "a whole sentence of text here", "line one\\nline two", "their", "",
])
def test_only_word_like_fixes_become_your_words(commands_file, replacement):
    from tuxtalk import custom_words
    vocabulary.save_fix("some phrase", replacement)
    assert custom_words.load() == []


def test_refixing_a_word_swaps_the_old_spelling_out(commands_file):
    from tuxtalk import custom_words
    vocabulary.save_fix("gift tarot", "Giftarot")
    vocabulary.save_fix("gift tarot", "Giftara")
    assert custom_words.load() == ["Giftara"]


def test_an_unreadable_words_file_is_left_alone(commands_file):
    words = commands_file.parent / "custom_words.txt"
    words.write_bytes(b"caf\xe9\n")
    vocabulary.save_fix("bamboo studio", "BambuStudio")
    assert config.load_custom_commands()["bamboo studio"] == "BambuStudio"
    assert words.read_bytes() == b"caf\xe9\n"


# --- the Words tab: each word with the mishearings fixed to it -----------------

@pytest.fixture
def words_file(commands_file):
    return commands_file.parent / "custom_words.txt"


def test_fixes_are_listed_under_their_word():
    words = ["ClaudeClaw", "TuxTalk"]
    commands = {"clawed claw": "ClaudeClaw", "my email": "me@example.com",
                "claude claw": "ClaudeClaw", "bamboo studio": "BambuStudio"}
    rows, snippets = vocabulary.split_commands(words, commands)
    assert rows == [("ClaudeClaw", ["clawed claw", "claude claw"]), ("TuxTalk", [])]
    assert snippets == {"my email": "me@example.com", "bamboo studio": "BambuStudio"}


def test_word_fixes_become_your_words_once(commands_file, words_file):
    from tuxtalk import custom_words
    custom_words.save(["TuxTalk"])
    words_file.write_text("# an older header\nTuxTalk\n")
    config.save_custom_commands({
        "my email address": "me@example.com", "my address": "37 Main Street, Hamilton, NJ",
        "by the way": '"/btw "', "clawed claw": "ClaudeClaw", "claude claw": "ClaudeClaw",
        "kashi os": "CachyOS", "tux talk": "TuxTalk", "and": "their"})
    assert vocabulary.adopt_fixes() is True
    assert custom_words.load() == ["TuxTalk", "ClaudeClaw", "CachyOS"]
    assert (words_file.parent / "custom_words.txt.before-words-tab").read_text() == \
        "# an older header\nTuxTalk\n"
    # Done once: a short snippet added later stays a snippet.
    config.save_custom_commands({"sig": "Ron"})
    assert vocabulary.adopt_fixes() is False
    assert custom_words.load() == ["TuxTalk", "ClaudeClaw", "CachyOS"]


def test_adopting_leaves_an_unreadable_commands_file_alone(commands_file, words_file):
    commands_file.write_text("this is [not valid toml")
    assert vocabulary.adopt_fixes() is False
    assert not words_file.exists()


def test_a_new_word_is_saved_with_its_mishearings(commands_file):
    from tuxtalk import custom_words
    config.save_custom_commands({"my email": "me@example.com"})
    vocabulary.save_word("ClaudeClaw", ["Clawed  Claw", "", "claude claw", "clawed claw"])
    assert custom_words.load() == ["ClaudeClaw"]
    assert config.load_custom_commands() == {
        "my email": "me@example.com", "clawed claw": "ClaudeClaw", "claude claw": "ClaudeClaw"}


def test_renaming_a_word_moves_its_fixes_and_keeps_its_place(commands_file):
    from tuxtalk import custom_words
    custom_words.save(["TuxTalk", "Claudeclaw", "CachyOS"])
    config.save_custom_commands({"clawed claw": "Claudeclaw", "kashi os": "CachyOS"})
    vocabulary.save_word("ClaudeClaw", ["clawed claw", "claude claw"], old="Claudeclaw")
    assert custom_words.load() == ["TuxTalk", "ClaudeClaw", "CachyOS"]
    assert config.load_custom_commands() == {
        "kashi os": "CachyOS", "clawed claw": "ClaudeClaw", "claude claw": "ClaudeClaw"}


def test_a_mishearing_taken_off_the_list_stops_being_fixed(commands_file):
    from tuxtalk import custom_words
    custom_words.save(["ClaudeClaw"])
    config.save_custom_commands({"clawed claw": "ClaudeClaw", "claude claw": "ClaudeClaw"})
    vocabulary.save_word("ClaudeClaw", ["claude claw"], old="ClaudeClaw")
    assert config.load_custom_commands() == {"claude claw": "ClaudeClaw"}


@pytest.mark.parametrize("word, phrases, old, says", [
    ("ClaudeClaw", ["my email"], None, "my email"),       # a snippet's phrase
    ("ClaudeClaw", ["kashi os"], None, "CachyOS"),        # another word's
    ("ClaudeClaw", ["the"], None, "the"),                 # an everyday word
    ("cachyos", [], None, "CachyOS"),                     # already a word
    ("", ["clawed claw"], None, "word"),
    ("TuxTalk\\nhi", [], None, "word"),                   # snippet-like text
])
def test_a_word_that_clashes_is_refused_and_nothing_written(commands_file, word, phrases, old, says):
    from tuxtalk import custom_words
    custom_words.save(["CachyOS"])
    config.save_custom_commands({"my email": "me@example.com", "kashi os": "CachyOS"})
    before = (commands_file.read_text(), (commands_file.parent / "custom_words.txt").read_text())
    with pytest.raises(vocabulary.WordProblem, match=says):
        vocabulary.save_word(word, phrases, old=old)
    assert (commands_file.read_text(), (commands_file.parent / "custom_words.txt").read_text()) == before


def test_removing_a_word_removes_its_fixes_only(commands_file):
    from tuxtalk import custom_words
    custom_words.save(["ClaudeClaw", "CachyOS"])
    config.save_custom_commands({"clawed claw": "ClaudeClaw", "my email": "me@example.com",
                                 "kashi os": "CachyOS"})
    vocabulary.remove_word("ClaudeClaw")
    assert custom_words.load() == ["CachyOS"]
    assert config.load_custom_commands() == {"my email": "me@example.com", "kashi os": "CachyOS"}


def test_saving_a_word_refuses_an_unreadable_commands_file(commands_file, words_file):
    commands_file.write_text("this is [not valid toml")
    with pytest.raises(config.ConfigNotLoadedError):
        vocabulary.save_word("ClaudeClaw", ["clawed claw"])
    assert commands_file.read_text() == "this is [not valid toml"
    assert not words_file.exists()


def test_a_new_word_takes_in_snippets_that_already_type_it(commands_file):
    config.save_custom_commands({"clawed claw": "ClaudeClaw"})
    vocabulary.save_word("ClaudeClaw", ["claude claw"])
    assert config.load_custom_commands() == {"clawed claw": "ClaudeClaw", "claude claw": "ClaudeClaw"}


def test_saving_a_word_leaves_an_unreadable_words_file_alone(commands_file, words_file):
    """Never save a list read as empty over the user's real words."""
    words_file.write_bytes(b"caf\xe9\n")
    with pytest.raises(UnicodeDecodeError):
        vocabulary.save_word("ClaudeClaw", ["clawed claw"])
    assert words_file.read_bytes() == b"caf\xe9\n"
    assert config.load_custom_commands() == {}
