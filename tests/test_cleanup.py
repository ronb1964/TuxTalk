"""Rule-based dictation cleanup: "um"/"uh" and accidental repeated words.

These rules may only delete hesitation sounds and exact repeats. The cases
below pin down both halves: what must be cleaned, and what must be left alone
because a person meant it.
"""
import pytest

from tuxtalk.cleanup import clean_fillers_and_repeats as clean

CLEANED = [
    ("Um, so I think we should, uh, go with the blue one. ", "So I think we should go with the blue one. "),
    ("I was going to the the store yesterday.", "I was going to the store yesterday."),
    ("Uh, can you, um, check the GitHub messages?", "Can you check the GitHub messages?"),
    ("let go of the um the the talk key", "let go of the talk key"),
    ("and I think uh I think that would be nice.", "and I think that would be nice."),
    ("everything will will paste into your chat window.", "everything will paste into your chat window."),
    ("First line.\xa7SHIFT_ENTER\xa7Um, second line.", "First line.\xa7SHIFT_ENTER\xa7Second line."),
    ("The the quick brown fox.", "The quick brown fox."),
    ("Is it it working?", "Is it working?"),
    ("It's done. Um, next we wire it.", "It's done. Next we wire it."),
    ("Okay, um, sounds good.", "Okay, sounds good."),
    ("Yes, uh, that works.", "Yes, that works."),
    ("if nothing is selected, um what you can do", "if nothing is selected, what you can do"),
    ("Ummm, maybe.", "Maybe."),
    ("so I I think so", "so I think so"),
    ("Hello. The the end.", "Hello. The end."),
]

LEFT_ALONE = [
    "I know that that is true.",
    "She had had enough.",
    "Hmm, not sure about that.",
    "Umbrella sales are up.",
    "Yeah yeah, fine.",
    "I said it again, again.",
    "No, no, no, that's wrong.",
    "You know, I think it's, like, pretty good.",
    "Our humble road van.",
    "First line.\xa7SHIFT_ENTER\xa7\xa7SHIFT_ENTER\xa7Second line.",
    # Numbers read out digit by digit are phone numbers and PINs, not stutters.
    "my number is five five five one two one two",
    "my PIN is 4 4 7 1",
    "call 555 555 1234",
    "it was a 50 50 chance",
    # A capitalized repeat in mid-sentence is a name.
    "We went to Walla Walla.",
    "We flew to Bora Bora today.",
    # A filler sound joined by a hyphen is a real word.
    "uh-huh that works",
    "Uh-oh, we missed it.",
    "She said huh-uh.",
]


@pytest.mark.parametrize("text,want", CLEANED)
def test_cleans(text, want):
    assert clean(text) == want


@pytest.mark.parametrize("text", LEFT_ALONE)
def test_leaves_meant_words_alone(text):
    assert clean(text) == text


def test_empty():
    assert clean("") == ""


def test_literal_command_placeholders_are_never_collapsed():
    """Quoted custom-command replacements are still placeholders when cleanup
    runs; each has its own number, so two can never look like a stutter."""
    text = "Send \xa7CMDLIT_0\xa7 \xa7CMDLIT_1\xa7 now."
    assert clean(text) == text


def test_cleanup_runs_in_prepare_text_only_when_enabled(monkeypatch):
    from tuxtalk import app
    monkeypatch.setattr(app, "_ai_engine", None)
    monkeypatch.setattr(app, "_remove_fillers", False)
    assert "um" in app._prepare_text("um so the the van is ready", False, True, False).lower()
    monkeypatch.setattr(app, "_remove_fillers", True)
    assert app._prepare_text("um so the the van is ready", False, True, False) == "So the van is ready."


# A spoken "press tab" is a real tab character by the time cleanup runs, and
# the tidy-up step must keep it rather than treat it as stray whitespace, and
# leave no space before it where a filler was taken out.
@pytest.mark.parametrize("said, want", [
    ("hello press tab world", "Hello\tworld."),
    ("name press tab press tab age", "Name\t\tage."),
    ("press tab press tab indented", "\t\tIndented."),
    ("first press tab second press tab third", "First\tsecond\tthird."),
    ("total press tab total", "Total\ttotal."),
    ("hello press tab um world", "Hello\tworld."),
    ("hello um press tab world", "Hello\tworld."),
    ("press tab um hello there", "\tHello there."),
])
def test_spoken_tabs_survive_cleanup(said, want):
    from tuxtalk.normalize import normalize_text
    assert clean(normalize_text(said)) == want


def test_text_with_nothing_to_remove_is_returned_untouched():
    text = "Spacing  left \t as it was ,"
    assert clean(text) == text
