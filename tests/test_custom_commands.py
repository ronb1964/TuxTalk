"""Tests for custom voice commands (phrase → replacement).

Custom commands run on the text of every dictation, so a fault here doesn't
degrade one phrase — it breaks all dictation. These tests cover four ways that
can happen:

  * a backslash in a replacement crashing every transcription
  * one command's output being rewritten by another command
  * a trigger containing punctuation silently never matching
  * a quoted "inject exactly as written" replacement getting a period appended
"""

import pytest

from tuxtalk import app, custom_commands


@pytest.fixture
def commands(monkeypatch):
    """Install a set of custom commands for one test."""
    def install(mapping):
        monkeypatch.setattr(app, "_custom_commands", mapping)
        return lambda text: app._apply_custom_commands(text)[0]
    return install


@pytest.fixture
def pipeline(monkeypatch):
    """Run the full path a dictation takes: commands → normalize → restore."""
    from tuxtalk.normalize import normalize_text

    def install(mapping):
        monkeypatch.setattr(app, "_custom_commands", mapping)

        def run(text):
            processed, protected = app._apply_custom_commands(text)
            return custom_commands.restore_protected(normalize_text(processed), protected)

        return run
    return install


# --- backslashes must never be treated as regex syntax ---------------------

def test_backslash_escape_in_a_replacement_does_not_break_dictation(commands):
    """A replacement is a *value*, not a pattern.

    Treating it as one would let a single bad command take down every
    dictation, even ones without its trigger.
    """
    apply = commands({"sign off": r"Best,\NRon"})

    assert apply("nothing here relates to that command") == "nothing here relates to that command"


def test_windows_path_replacement_is_inserted_literally(commands):
    """Reachable straight from the Preferences window."""
    apply = commands({"my folder": r"C:\Users\Ron"})

    assert apply("open my folder now") == r"open C:\Users\Ron now"


def test_group_reference_in_a_replacement_is_inserted_literally(commands):
    apply = commands({"cite": r"Section \1"})

    assert apply("see cite below") == r"see Section \1 below"


def test_backslash_n_still_inserts_a_line_break(commands):
    """Documented feature — the Preferences hint tells users to write \\n."""
    apply = commands({"sig": r"Best,\nRon"})

    assert apply("sig") == "Best,\nRon"


def test_backslash_t_still_inserts_a_tab(commands):
    apply = commands({"indent": r"a\tb"})

    assert apply("indent") == "a\tb"


# --- one pass: a command's output is final ---------------------------------

def test_one_commands_replacement_is_not_rewritten_by_another(commands):
    """Commands are applied in one pass, so text produced by one command is
    never re-scanned and mangled by the next."""
    apply = commands({"sign off": "Thanks, Ron", "thanks": "Thank you"})

    assert apply("that is all sign off") == "that is all Thanks, Ron"


def test_the_longest_matching_phrase_still_wins(commands):
    """Overlapping phrases, where one trigger is the start of another."""
    apply = commands({
        "our brand customs llc": "RBrand Customs LLC",
        "our brand customs": "RBrand Customs",
    })

    assert apply("I work for our brand customs llc today") == "I work for RBrand Customs LLC today"
    assert apply("I work for our brand customs today") == "I work for RBrand Customs today"


def test_a_replacement_containing_another_trigger_is_left_alone(commands):
    apply = commands({"greet": "say hello", "hello": "GOODBYE"})

    assert apply("greet") == "say hello"


# --- triggers containing punctuation ---------------------------------------

def test_trigger_ending_in_punctuation_matches(commands):
    """A trigger like `c++` must fire, not just look configured in Preferences."""
    apply = commands({"c++": "C++"})

    assert apply("I write c++ every day") == "I write C++ every day"


def test_trigger_starting_with_punctuation_matches(commands):
    apply = commands({"#tag": "hashtag"})

    assert apply("add #tag here") == "add hashtag here"


def test_word_triggers_still_match_whole_words_only(commands):
    """The boundary behaviour that made punctuation triggers fail is still
    needed for ordinary word triggers."""
    apply = commands({"cat": "dog"})

    assert apply("concatenate the cat") == "concatenate the dog"


def test_matching_is_case_insensitive(commands):
    apply = commands({"talk type": "TalkType"})

    assert apply("Talk Type is running") == "TalkType is running"


# --- quoted replacements are injected exactly as written -------------------

def test_quoted_replacement_keeps_its_trailing_space(pipeline):
    """A real command, "by the way" = "/btw ", must not come out as `/btw .`"""
    run = pipeline({"by the way": '"/btw "'})

    assert run("by the way") == "/btw "


def test_quoted_replacement_ending_in_punctuation_gets_no_extra_period(pipeline):
    run = pipeline({"greet": '"Hello, world!"'})

    assert run("greet") == "Hello, world!"


def test_quoted_replacement_already_ending_in_a_period_is_not_doubled(pipeline):
    run = pipeline({"thx": '"Thanks."'})

    assert run("thx") == "Thanks."


def test_quoted_replacement_ending_in_a_word_still_ends_the_sentence(pipeline):
    """Only punctuation the command supplied itself is respected — a literal
    ending in a word still gets the sentence period the user expects."""
    run = pipeline({"sig": '"Best, Ron"'})

    assert run("sig") == "Best, Ron."


def test_unquoted_replacements_are_still_normalized(pipeline):
    """Unquoted replacements still go through normalization."""
    run = pipeline({"talk type": "TalkType"})

    assert run("talk type is great") == "TalkType is great."


# --- robustness -------------------------------------------------------------

def test_no_commands_configured_leaves_text_untouched(monkeypatch):
    monkeypatch.setattr(app, "_custom_commands", {})

    assert app._apply_custom_commands("hello there") == ("hello there", {})


def test_an_empty_trigger_is_ignored(commands):
    """An empty phrase would otherwise match at every position."""
    apply = commands({"": "XXX", "real": "REAL"})

    assert apply("a real phrase") == "a REAL phrase"
