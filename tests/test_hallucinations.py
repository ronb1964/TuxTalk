"""Whisper invents phrases during silence; stripping them must not eat real words.

YouTube outros and sign-offs are only stripped when Whisper reports the audio
was probably not speech: people do say "please do not hit the bell" and "thanks
for watching", and a bare "subscribe" is an ordinary verb.
"""

from tuxtalk import hallucinations
from tuxtalk import engine


# --- ordinary sentences must survive ---------------------------------------

def test_a_sentence_ending_in_subscribe_keeps_the_word():
    """"subscribe" is not on the always-strip list, so a dictation ending in
    it keeps the word instead of silently losing it."""
    assert hallucinations.strip("tell them to subscribe", 0.0) == "tell them to subscribe"


def test_asking_someone_to_subscribe_survives():
    assert hallucinations.strip("remind me to subscribe", 0.0) == "remind me to subscribe"


def test_subscribe_mid_sentence_survives():
    text = "the subscribe button is broken"
    assert hallucinations.strip(text, 0.0) == text


def test_an_outro_said_in_a_real_sentence_is_kept():
    """Whisper heard speech, so the words were said: cutting "hit the bell"
    turned a warning into "Please do not."."""
    for text in ("Please do not hit the bell.", "The sign says please subscribe."):
        assert hallucinations.strip(text, 0.0) == text


# --- YouTube outros invented from silence are stripped ------------------------

def test_like_and_subscribe_is_still_stripped():
    out = hallucinations.strip("here is my point like and subscribe", 0.9)
    assert "subscribe" not in out
    assert out.strip() == "here is my point"


def test_please_subscribe_is_still_stripped():
    out = hallucinations.strip("here is my point please subscribe", 0.9)
    assert out.strip() == "here is my point"


def test_dont_forget_to_subscribe_is_still_stripped():
    out = hallucinations.strip("here is my point don't forget to subscribe", 0.9)
    assert out.strip() == "here is my point"


def test_a_whole_text_outro_is_still_dropped():
    assert hallucinations.strip("Like and subscribe.", 0.7) == ""


# --- sign-offs people really dictate ----------------------------------------
# "thanks for watching" and "see you next time" are stripped only when Whisper
# reports the audio was probably not speech. Cut every time, a dictation that
# was only "Thanks for listening." would type nothing.

def test_a_dictated_sign_off_is_kept():
    text = "Great meeting today, see you next time."
    assert hallucinations.strip(text, 0.0) == text


def test_a_script_ending_in_thanks_for_watching_is_kept():
    text = "That is the whole demo, thanks for watching!"
    assert hallucinations.strip(text, 0.0) == text


def test_a_dictated_sign_off_on_its_own_is_kept():
    assert hallucinations.strip("Thanks for listening.", 0.0) == "Thanks for listening."


def test_a_sign_off_invented_from_silence_is_dropped():
    assert hallucinations.strip("Thanks for listening.", 0.9) == ""


def test_a_sign_off_appended_to_speech_in_silence_is_stripped():
    text = "That covers everything we planned for the release this week. Thanks for watching!"
    out = hallucinations.strip(text, 0.9)
    assert out == "That covers everything we planned for the release this week."


def test_stripping_keeps_the_sentences_own_closing_mark():
    """The period belongs to the sentence, not to the phrase that was cut."""
    text = "We shipped the update to every user on Tuesday, like and subscribe."
    assert hallucinations.strip(text, 0.9) == "We shipped the update to every user on Tuesday."


def test_the_cut_lands_in_the_right_place_after_a_dotted_capital_i():
    """"İ".lower() is two characters, so an index found in a lower-cased copy
    would point past the phrase in the original and cut real words."""
    text = "İstanbul İzmir İzmit İnegöl like and subscribe"
    assert hallucinations.strip(text, 0.9) == "İstanbul İzmir İzmit İnegöl"


# --- the whole-text and trailing tiers --------------------------------------

def test_a_silent_recording_transcribed_as_thank_you_is_dropped():
    assert hallucinations.strip("thank you", 0.9).strip() == ""


def test_a_deliberate_thank_you_is_kept():
    """Low no_speech_prob means Whisper heard real speech."""
    assert hallucinations.strip("thank you", 0.0).strip() == "thank you"


# --- the log says so only when something was stripped -------------------------

def test_spaces_trimmed_off_a_dictation_arent_logged_as_a_hallucination(caplog):
    """Trimming the spaces was enough for the log to say "Stripped a
    hallucination", which buried the real ones."""
    with caplog.at_level("INFO", logger="tuxtalk.app"):
        assert engine._strip_hallucinations(" Hello there. ") == "Hello there."
        assert "Stripped a hallucination" not in caplog.text
        engine._strip_hallucinations("Hello there. Like and subscribe.", 0.9)
    assert "Stripped a hallucination" in caplog.text
