"""
Dictation cleanup: remove "um"/"uh" and accidental repeated words.

Deterministic rules, no AI and no download: instant, and it only ever
deletes hesitation sounds and exact repeats. Repeats that are usually meant
(numbers, names like "Walla Walla", "had had") are left alone.
Self-corrections ("3, no wait, 4") need to understand the sentence, so those
are handled separately by the optional AI step (ai_cleanup.py).

Runs on the normalized text, after spoken commands have been turned into
punctuation, so an intentional "new line new line" (two line breaks) has
already become markers and is never mistaken for a stutter. A spoken "press
tab" is a real tab character by then, so these rules only ever touch spaces.
"""
import re

# Hesitation sounds. Deliberately narrow: "hmm" is often meant ("Hmm, not
# sure"), "er" is too close to real text, and "you know"/"like" depend on
# meaning, so none of those are touched.
_FILLER = r"(?:u+m+|u+h+m*|erm)"

# Words that are correctly doubled in ordinary English: "I know that that is
# true", "she had had enough", "what it is is...", "very very", "so so".
# The AI's corrections are held to the same list (ai_cleanup).
LEGIT_DOUBLES = {"that", "had", "is", "do", "very", "so", "bye", "no", "yeah", "ha", "really",
                 "far", "long"}

# Numbers are read out digit by digit ("five five five", "4 4 7 1"), so a
# repeated number is a phone number or PIN, never a stutter.
_NUMBER_WORDS = {
    "zero", "oh", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
    "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
    "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand", "million", "billion", "point", "double", "triple",
}

# Pronouns that are capitalized anywhere, so a capital says nothing about them.
_ALWAYS_CAPITAL = {"i", "i'm", "i'll", "i've", "i'd"}

# Words that keep their own comma when a filler after them is removed.
_COMMA_WORDS = {"okay", "ok", "yes", "yeah", "no", "well", "so", "alright", "right",
                "sure", "oh", "hey", "hi", "hello", "thanks", "now", "anyway", "also"}

# A filler with the commas and spaces around it:  ", um,"  "Um, "  " uh "
# One joined to a hyphen or apostrophe is part of a real word ("uh-huh",
# "uh-oh"), so it stays.
_RE_FILLER = re.compile(rf"(?i)(, *)?(?<![-'’])\b{_FILLER}\b(?![-'’])[,.]? *")

# One to three words repeated immediately with only spaces between them:
# "the the", "will will", "I think I think". A comma between the copies
# ("no, no, no", "again, again") usually means the repeat was deliberate, and
# so does a tab ("total<tab>total" is two fields).
_RE_REPEAT = re.compile(r"(?i)\b((?:[\w']+ +){0,2}[\w']+)(?: +\1\b)+")


def _remove_fillers(text: str) -> str:
    """Delete hesitation sounds, with the commas that were only there for them."""
    def repl(m):
        """What one filler match becomes: nothing, a space or the comma that
        was the sentence's own, plus a mark that capitalizes the next word
        when the filler opened the sentence."""
        start = m.start()
        at_line_start = start == 0 or text[:start].endswith(("\xa7", "\n", "\t"))
        # "Um, so we..." -> "So we...": when the filler opened a sentence (it
        # was capitalized), the word that now opens it takes the capital.
        capitalize = m.group(0).lstrip(", ")[:1].isupper()
        # ", uh," inside a sentence: both commas were only there for the
        # filler ("we should, uh, go" -> "we should go"). But after a word
        # like "okay" or "yes" the first comma is real punctuation:
        # "Okay, um, sounds good" -> "Okay, sounds good".
        prev = re.search(r"([\w']+)\W*$", text[:start])
        # A comma before the filler with none after it ("selected, um what")
        # was the sentence's own comma, so it stays too.
        trailing_comma = m.group(0).rstrip().endswith(",")
        keep_comma = bool(m.group(1)) and (
            not trailing_comma or (prev and prev.group(1).lower() in _COMMA_WORDS))
        sep = ", " if keep_comma else ("" if at_line_start else " ")
        return sep + ("\x00" if capitalize else "")
    out = _RE_FILLER.sub(repl, text)
    # \x00 marks "capitalize the next letter"; resolve it now.
    out = re.sub(r"\x00(\s*)(\w)", lambda m: m.group(1) + m.group(2).upper(), out)
    return out.replace("\x00", "")


def _is_name(m) -> bool:
    """A capitalized repeat in mid-sentence is a name ("Walla Walla")."""
    first = m.group(1).split()[0]
    if not first[0].isupper() or first.lower() in _ALWAYS_CAPITAL:
        return False
    before = m.string[:m.start()].rstrip(" \t\"'“‘([{")
    return bool(before) and not before.endswith((".", "?", "!", "…", "\xa7", "\n"))


def _collapse_repeats(text: str) -> str:
    """Collapse accidental repeats, keeping numbers, names and words meant twice."""
    def repl(m):
        phrase = m.group(1)
        if phrase.lower() in LEGIT_DOUBLES:
            return m.group(0)
        words = phrase.lower().split()
        if any(c.isdigit() for c in phrase) or any(w in _NUMBER_WORDS for w in words):
            return m.group(0)
        if _is_name(m):
            return m.group(0)
        return phrase
    return _RE_REPEAT.sub(repl, text)


def _tidy(text: str, original: str) -> str:
    """Fix the spacing, punctuation and capitals the deletions left behind."""
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r" +([,.!?;:\t])", r"\1", text)         # "word ," -> "word,"
    text = re.sub(r",( *[,.!?;:])", r"\1", text)          # ",." or ",," -> "."
    text = re.sub(r"^[ ,]+", "", text)                      # leading comma/space
    # Keep the original's trailing space (auto-space adds one).
    if original.endswith(" ") and not text.endswith(" "):
        text += " "
    return text


def clean_fillers_and_repeats(text: str) -> str:
    """Remove "um"/"uh" and accidental repeated words from dictated text."""
    if not text:
        return text
    cleaned = _collapse_repeats(_remove_fillers(text))
    # A filler between two copies of a word ("the um the") becomes a repeat
    # only after the filler is gone, so collapse once more.
    cleaned = _collapse_repeats(cleaned)
    if cleaned == text:
        return text  # nothing removed, so nothing for _tidy to repair
    return _tidy(cleaned, text)
