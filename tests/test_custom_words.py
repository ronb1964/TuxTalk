"""Your Words: names and terms TuxTalk should spell the user's way.

Whisper models are told about them before they listen. Every engine,
Parakeet included, then gets a strict spelling pass: a word that came out
split up, hyphenated or with the wrong capitals ("tux talk") is written the
user's way. Parakeet can't be told about words beforehand at all.
"""
import pytest

from tuxtalk import config, custom_words as cw


@pytest.fixture
def words_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CUSTOM_COMMANDS_PATH", str(tmp_path / "custom_commands.toml"))
    return tmp_path / "custom_words.txt"


# --- the file ---------------------------------------------------------------

def test_the_file_sits_next_to_the_snippets(words_file):
    assert cw.path() == str(words_file)


def test_missing_file_means_no_words(words_file):
    assert cw.load() == []


def test_save_and_load_round_trip(words_file):
    cw.save(["TuxTalk", "BambuStudio", "ydotool"])
    assert cw.load() == ["TuxTalk", "BambuStudio", "ydotool"]


def test_load_skips_blanks_comments_and_repeats(words_file):
    words_file.write_text("# a comment\n\n  TuxTalk  \ntuxtalk\nBambu Studio\n")
    assert cw.load() == ["TuxTalk", "Bambu Studio"]


def test_parse_text_box(words_file):
    assert cw.parse("TuxTalk\n\n ydotool \nTuxTalk\n") == ["TuxTalk", "ydotool"]


# --- telling Whisper ----------------------------------------------------------

def test_prompt_lists_the_words():
    assert cw.prompt(["TuxTalk", "BambuStudio"]) == "TuxTalk, BambuStudio."


def test_no_words_no_prompt():
    assert cw.prompt([]) is None


def test_prompt_is_capped_to_fit_whisper():
    """Whisper keeps only about 220 tokens of prompt; a giant list would push
    out the words and slow every dictation."""
    many = [f"Word{i:03d}" for i in range(500)]
    text = cw.prompt(many)
    assert len(text) <= cw.PROMPT_MAX_CHARS
    assert text.startswith("Word000, Word001")


# --- the spelling pass ---------------------------------------------------------

WORDS = ["TuxTalk", "BambuStudio", "ydotool", "GitHub"]


@pytest.mark.parametrize("heard, typed", [
    ("I use tux talk every day", "I use TuxTalk every day"),
    ("Tux Talk is great.", "TuxTalk is great."),
    ("open Tux-Talk now", "open TuxTalk now"),
    ("tuxtalk works", "TuxTalk works"),
    ("print it in bambu studio, then", "print it in BambuStudio, then"),
    ("push it to git hub.", "push it to GitHub."),
    ('he said "tux talk"', 'he said "TuxTalk"'),
])
def test_fixes_split_and_miscapitalised_words(heard, typed):
    assert cw.fix_spelling(heard, WORDS) == typed


@pytest.mark.parametrize("heard", [
    "the bamboo studio was closed",        # a different word: Fix a Word's job
    "tux. Talk to me",                     # a sentence break is not a split word
    "tux, talk",
    "I talk to tux",                       # wrong order
    "",
])
def test_leaves_everything_else_alone(heard):
    assert cw.fix_spelling(heard, WORDS) == heard


def test_plain_capitalised_words_are_not_forced_on_ordinary_speech():
    """'Mark' in the list must not capitalise every 'mark'. Only words with
    unusual capitals (TuxTalk, iPhone) are recapitalised on their own."""
    assert cw.fix_spelling("mark the date", ["Mark"]) == "mark the date"
    assert cw.fix_spelling("mark the date", ["Marker"]) == "mark the date"
    assert cw.fix_spelling("my iphone died", ["iPhone"]) == "my iPhone died"


def test_short_words_are_skipped():
    """Two or three letters join up by accident far too easily ('a i' -> AI)."""
    assert cw.fix_spelling("a i is here", ["AI"]) == "a i is here"


def test_lowercase_words_in_the_list_still_join():
    assert cw.fix_spelling("run why do tool", ["ydotool"]) == "run why do tool"
    assert cw.fix_spelling("run y do tool", ["ydotool"]) == "run ydotool"


def test_already_right_is_unchanged():
    assert cw.fix_spelling("TuxTalk and GitHub", WORDS) == "TuxTalk and GitHub"


# --- Whisper reading the list back ----------------------------------------------

def test_an_echo_of_the_list_is_recognised():
    """Given a prompt, Whisper sometimes 'hears' it in silence."""
    assert cw.is_echo("TuxTalk, BambuStudio, ydotool.", WORDS)
    assert cw.is_echo("TuxTalk, BambuStudio, ydotool, GitHub.", WORDS)


def test_real_speech_is_not_an_echo():
    assert not cw.is_echo("I opened TuxTalk today.", WORDS)
    assert not cw.is_echo("TuxTalk.", WORDS)              # one word can be real
    assert not cw.is_echo("TuxTalk GitHub", WORDS)        # two can too
    assert not cw.is_echo("anything", [])


# --- wiring into the dictation service -------------------------------------------

import numpy as np

from tuxtalk import dictation_text
from tuxtalk import engine
from tuxtalk import live_settings as live
from tuxtalk import recording_state as rs
from tuxtalk import whisper_vulkan as wv


class FakeVulkan(wv.VulkanWhisperModel):
    def __init__(self, reply="Thank you."):
        self.reply, self.prompts = reply, []

    def transcribe(self, audio, language=None, prompt=None):
        self.prompts.append(prompt)
        return self.reply, 0.0


class FakeWhisper:
    """faster-whisper's WhisperModel as _transcribe_audio uses it."""
    def __init__(self, reply="Hello."):
        self.reply, self.kwargs = reply, None

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        seg = type("Seg", (), {"text": self.reply, "no_speech_prob": 0.0,
                               "avg_logprob": -0.1, "compression_ratio": 1.0})()
        return [seg], None


@pytest.fixture
def with_words(monkeypatch):
    monkeypatch.setattr(live, "words", ["TuxTalk", "BambuStudio", "ydotool"])
    monkeypatch.setattr(live, "commands", {})


def test_the_service_starts_with_no_words():
    assert live.words == []


def test_vulkan_whisper_is_told_the_words(with_words, monkeypatch):
    fake = FakeVulkan("I like TuxTalk.")
    monkeypatch.setattr(engine, "model", fake, raising=False)
    assert engine.transcribe(np.zeros(16000, dtype=np.float32), "en") == "I like TuxTalk."
    assert fake.prompts == ["TuxTalk, BambuStudio, ydotool."]


def test_vulkan_whisper_without_words_gets_no_prompt(monkeypatch):
    fake = FakeVulkan("Hello.")
    monkeypatch.setattr(engine, "model", fake, raising=False)
    engine.transcribe(np.zeros(16000, dtype=np.float32), "en")
    assert fake.prompts == [None]


def test_faster_whisper_gets_them_as_hotwords(with_words, monkeypatch):
    fake = FakeWhisper()
    monkeypatch.setattr(engine, "model", fake, raising=False)
    engine.transcribe(np.zeros(16000, dtype=np.float32), "en")
    assert fake.kwargs["hotwords"] == "TuxTalk, BambuStudio, ydotool."


def test_faster_whisper_without_words_gets_no_hotwords(monkeypatch):
    fake = FakeWhisper()
    monkeypatch.setattr(engine, "model", fake, raising=False)
    engine.transcribe(np.zeros(16000, dtype=np.float32), "en")
    assert fake.kwargs["hotwords"] is None


def test_whisper_reading_the_list_back_counts_as_nothing_said(with_words, monkeypatch):
    monkeypatch.setattr(engine, "model", FakeVulkan("TuxTalk, BambuStudio, ydotool."), raising=False)
    assert engine.transcribe(np.zeros(16000, dtype=np.float32), "en") is None


def test_parakeet_output_gets_the_spelling_pass(with_words):
    assert "TuxTalk" in dictation_text.prepare_text("i use tux talk daily", True, True, False)


def test_a_snippet_still_beats_a_word(with_words, monkeypatch):
    """Snippets run first, so 'tux talk link' still finds its snippet."""
    monkeypatch.setattr(live, "commands", {"tux talk link": "https://example.com"})
    out = dictation_text.prepare_text("open tux talk link", True, False, False)
    assert "https://example.com" in out


def test_continuing_mid_sentence_keeps_a_words_capitals(with_words, monkeypatch):
    monkeypatch.setattr(rs.state, "continue_mid_sentence", True)
    out = dictation_text.prepare_text("tux talk is running", True, False, False)
    assert out.startswith("TuxTalk")


def test_the_vulkan_request_carries_the_prompt_only_when_there_is_one():
    assert wv.request_fields("en", "TuxTalk.")["prompt"] == "TuxTalk."
    assert "prompt" not in wv.request_fields("en", None)
    assert wv.request_fields(None)["language"] == "auto"


# --- contractions, possessives and echoes ------------------------------------------

@pytest.mark.parametrize("heard, words", [
    ("she'll be there", ["Shell"]),
    ("we'll see", ["Well"]),
    ("she'd know", ["Shed"]),
    ("I took Edward's car to William's place", ["Edwards", "Williams"]),
    ("I took Edward’s car", ["Edwards"]),
    ("see tux.talk", ["TuxTalk"]),
])
def test_contractions_possessives_and_dots_are_never_joined(heard, words):
    assert cw.fix_spelling(heard, words) == heard


def test_a_word_with_an_apostrophe_of_its_own_still_matches():
    assert cw.fix_spelling("visit o'reilly media", ["O'Reilly"]) == "visit O'Reilly media"


@pytest.mark.parametrize("heard, words", [
    ("the new bank", ["New York", "The Who", "Bank of America"]),
    ("a of the", ["Bank of America", "The Who", "A Team"]),
    ("Kubernetes, Docker and Helm.", ["Kubernetes", "Docker", "Helm", "and"]),
    ("ydotool, TuxTalk, BambuStudio.", ["TuxTalk", "BambuStudio", "ydotool"]),  # out of order
])
def test_real_phrases_from_listed_words_are_not_an_echo(heard, words):
    assert not cw.is_echo(heard, words)


def test_an_echo_of_multi_word_entries_is_recognised():
    assert cw.is_echo("New York, Bank of America, TuxTalk.", ["New York", "Bank of America", "TuxTalk"])


def test_hash_words_are_kept_and_only_hash_space_is_a_comment():
    assert cw.parse("# a comment\n#TuxTalk\n#\n") == ["#TuxTalk"]


def test_an_unreadable_file_can_be_reported_instead_of_emptied(words_file):
    words_file.write_bytes(b"caf\xe9\n")       # Latin-1, not UTF-8
    assert cw.load() == []
    with pytest.raises(UnicodeDecodeError):
        cw.load(strict=True)


def test_a_lowercase_first_word_keeps_its_case_at_a_sentence_start(monkeypatch):
    monkeypatch.setattr(live, "words", ["iPhone", "eBay"])
    monkeypatch.setattr(live, "commands", {})
    out = dictation_text.prepare_text("iphone is great. ebay too", True, True, False)
    assert out.startswith("iPhone is great. eBay too")


def test_a_snippet_spoken_as_the_joined_word_still_fires(with_words, monkeypatch):
    """Whisper, told about TuxTalk, hears 'TuxTalk website' while the snippet
    is saved as 'tux talk website'."""
    monkeypatch.setattr(live, "commands", {"tux talk website": '"https://tuxtalk.example"'})
    assert "https://tuxtalk.example" in dictation_text.prepare_text("open TuxTalk website", True, False, False)


@pytest.mark.parametrize("raw, first", [
    ("New York is big", "New York"),
    ("TuxTalk's menu", "TuxTalk's"),
    ("TuxTalk's, menu", "TuxTalk's,"),
])
def test_continuing_mid_sentence_keeps_multi_word_and_possessive_names(raw, first, monkeypatch):
    monkeypatch.setattr(live, "words", ["New York", "TuxTalk"])
    monkeypatch.setattr(live, "commands", {})
    monkeypatch.setattr(rs.state, "continue_mid_sentence", True)
    assert dictation_text.prepare_text(raw, True, False, False).startswith(first)


# --- whitespace, hyphens and short entries ----------------------------------------

def test_only_plain_spaces_join_pieces():
    assert cw.fix_spelling("tux \t talk.", ["TuxTalk"]) == "tux \t talk."
    assert cw.fix_spelling("tux\ntalk", ["TuxTalk"]) == "tux\ntalk"


def test_after_tidying_a_hyphen_is_the_users_own():
    assert cw.fix_spelling("Tux-talk.", ["TuxTalk"], hyphens=False) == "Tux-talk."
    assert cw.fix_spelling("tux talk.", ["TuxTalk"], hyphens=False) == "TuxTalk."


@pytest.mark.parametrize("spoken", ["I like tux press tab talk", "I like tux dash talk"])
def test_a_spoken_tab_or_dash_survives(spoken, monkeypatch):
    monkeypatch.setattr(live, "words", ["TuxTalk"])
    monkeypatch.setattr(live, "commands", {})
    assert "TuxTalk" not in dictation_text.prepare_text(spoken, True, True, False)


def test_an_engine_hyphen_still_joins(monkeypatch):
    monkeypatch.setattr(live, "words", ["TuxTalk"])
    monkeypatch.setattr(live, "commands", {})
    assert dictation_text.prepare_text("open Tux-Talk now", True, True, False).startswith("Open TuxTalk now")


def test_short_entries_do_not_hide_an_echo():
    assert cw.is_echo("TuxTalk, AI, GitHub, ydotool.", ["TuxTalk", "AI", "GitHub", "ydotool"])
    assert not cw.is_echo("AI, AI, AI.", ["AI"])


def test_an_unreadable_words_file_keeps_the_words_in_force(monkeypatch):
    """Like the custom-commands reload, a read error mid-session keeps the
    current words instead of dropping them all."""
    from tuxtalk import custom_words

    def unreadable(strict=False):
        if strict:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return []

    monkeypatch.setattr(custom_words, "load", unreadable)
    monkeypatch.setattr(live, "words", ["TuxTalk"])

    live.reload_words()

    assert live.words == ["TuxTalk"]


def test_a_possessive_followed_by_punctuation_is_still_the_word():
    assert cw.starts_with_word("TuxTalk's, menu", ["TuxTalk"])
    assert cw.starts_with_word("TuxTalk\u2019s.", ["TuxTalk"])


# --- sound-alikes: Parakeet hears TuxTalk's "x" as "ks" ------------------------------


@pytest.mark.parametrize("heard", [
    "I use Tuck's talk every day.", "I use Tucks talk every day.", "I use Tuckstalk every day.",
    "I use tucks talk every day.", "I use Tux talk every day.",
])
def test_a_sound_alike_spelling_becomes_the_word(heard):
    assert cw.fix_spelling(heard, ["TuxTalk"]) == "I use TuxTalk every day."


def test_sound_alikes_keep_the_punctuation_around_them():
    assert cw.fix_spelling("Open Tuckstalk, then dictate.", ["TuxTalk"]) == "Open TuxTalk, then dictate."


@pytest.mark.parametrize("heard, words", [
    ("Mack's car is red.", ["Max"]),              # short words never match by sound
    ("The talk was good.", ["TuxTalk"]),           # only part of the word
    ("Tucks and talks.", ["TuxTalk"]),             # other words in between
    ("Ducks talk loudly.", ["TuxTalk"]),           # a different sound
])
def test_sound_alikes_need_a_long_word_and_the_same_sound(heard, words):
    assert cw.fix_spelling(heard, words) == heard


def test_exact_spellings_still_win_over_sound_alikes():
    assert cw.fix_spelling("Philips and Filips", ["Filips", "Philips"]) == "Philips and Filips"
