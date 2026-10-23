"""Phrases the speech models invent from silence, taken out of a transcription.

Whisper was trained on subtitles, so silence or room noise comes out as
"Thank you." or a YouTube outro; Parakeet writes "mm-hmm". strip() removes
them without eating real words: what people do dictate is only removed when
the model itself says the audio probably wasn't speech.
"""
import re


# YouTube outro phrases Whisper invents from silence, which nobody says to a
# text box. Stripped from the end of any transcription.
_YOUTUBE_HALLUCINATION_PHRASES = [
    # NOT bare "subscribe": it is an ordinary verb, and stripping it would eat
    # the last word of real sentences ("tell them to subscribe"). Whisper
    # hallucinates the multi-word forms, which nobody dictates.
    "like and subscribe",
    "please subscribe",
    "don't forget to subscribe",
    "hit the bell",
]

# Sign-offs Whisper also invents from silence, but which people do dictate at
# the end of an email or a script ("Great meeting today, see you next time.").
# Treated like "thank you" below: dropped only when Whisper itself says the
# audio was probably not speech.
_SIGN_OFF_PHRASES = [
    "thanks for watching",
    "thanks for listening",
    "thank you for watching",
    "thank you for listening",
    "see you next time",
    "see you in the next video",
    "see you in the next one",
]

# Common words/phrases that Whisper hallucinates from silence.
# These are only stripped when they are the ENTIRE transcription,
# because they could also be real speech at the end of a sentence
# (e.g., "I want to say thank you" should keep "thank you").
_WHOLE_TEXT_HALLUCINATION_PHRASES = [
    "thank you",
    "thank you very much",
    "thank you so much",
    "thanks",
    "bye",
    "bye bye",
    "goodbye",
    "you",
    "oh",
    "ah",
    "hmm",
    "uh",
    "um",
    # Parakeet's word for silence
    "mm-hmm",
    "mhm",
    "mm",
    "uh-huh",
] + _SIGN_OFF_PHRASES

# Phrases that Whisper hallucinates at the END of longer dictations.
# Only stripped when trailing real speech AND no_speech_prob is very high (>0.8),
# which means Whisper itself is uncertain the audio contained real speech.
# Kept separate from _WHOLE_TEXT_HALLUCINATION_PHRASES because the trailing
# check needs a higher confidence threshold and minimum text length to avoid
# false positives (e.g., "Sure, thank you." is real speech and should be kept).
_TRAILING_HALLUCINATION_PHRASES = [
    "thank you",
    "thank you very much",
    "thank you so much",
    "thanks",
    "you",
] + _SIGN_OFF_PHRASES


def _trailing_phrase_re(phrases):
    """A pattern for any of *phrases* as whole words at the very end of a text,
    with the punctuation and spaces around it. Matched on the original text,
    case-insensitively, so the cut lands in the right place even where
    lower-casing changes a string's length (Turkish "İ")."""
    alternatives = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(r"[\s,.;:!?]*\b(?:" + alternatives + r")[\s.,!?;:]*$", re.IGNORECASE)


_YOUTUBE_TAIL_RE = _trailing_phrase_re(_YOUTUBE_HALLUCINATION_PHRASES)
_TRAILING_TAIL_RE = _trailing_phrase_re(_TRAILING_HALLUCINATION_PHRASES)


def _cut_tail(text: str, start: int) -> str:
    """*text* up to *start*, keeping the sentence's own closing mark: "Great
    meeting today, see you next time." ends as "Great meeting today."."""
    kept = text[:start].rstrip(" ,;:")
    if not kept:
        return ""
    # A sentence that ended before the phrase keeps its own mark ("week. Thanks
    # for watching!" -> "week."); one the phrase finished takes the phrase's.
    first = text[start:].lstrip()[:1]
    last = text.rstrip()[-1:]
    for mark in (first, last):
        if mark in (".", "!", "?"):
            return kept + mark
    return kept


def strip(text: str, no_speech_prob: float = 0.0) -> str:
    """Remove phrases Whisper invents from silence, in three tiers:

    1. A whole text that is only "thank you", "bye" or a sign-off, when
       no_speech_prob (0-1) is above 0.6; an intentional "Thank you." stays.
    2. YouTube outros ("like and subscribe") at the end, always.
    3. "thank you" or a sign-off trailing a longer dictation, when
       no_speech_prob is above 0.8.

    Parakeet passes 0.0, so only tier 2 applies to it. The Vulkan engine passes
    1.0 for a recording with no speech in it and 0.0 otherwise, since
    whisper.cpp's own score can't be trusted.
    """
    if not text:
        return text

    text = text.strip()

    # Strip any trailing punctuation for comparison
    lower_clean = text.lower().rstrip(" .,!?;:")

    # Tier 1: If the ENTIRE text is a common hallucination phrase AND
    # Whisper thinks there was no real speech, discard it.
    # This catches silence -> "Thank you." while preserving real "Thank you."
    if lower_clean in _WHOLE_TEXT_HALLUCINATION_PHRASES and no_speech_prob > 0.6:
        return ""

    # Tier 2: Strip YouTube outros from the end of text, repeatedly, since
    # Whisper sometimes strings two together.
    while text:
        match = _YOUTUBE_TAIL_RE.search(text)
        if not match:
            break
        text = _cut_tail(text, match.start())

    # Tier 3: Strip trailing hallucination phrases from the end of real sentences.
    # Only when: (a) no_speech_prob is very high (>0.8, Whisper is very uncertain),
    # and (b) there's substantial text before the trailing phrase (>50 chars),
    # so we don't accidentally strip real speech like "Sure, thank you."
    if text and no_speech_prob > 0.8 and len(text) > 50:
        match = _TRAILING_TAIL_RE.search(text)
        # Only a phrase after real speech, never the whole text (tier 1's job).
        if match and match.start() > 0:
            text = _cut_tail(text, match.start())

    return text
