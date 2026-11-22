"""Write numbers as digits: "twenty-five chickens" becomes "25 chickens".

Parakeet writes small numbers, dates and ordinals as words ("three cats",
"october fifteenth"), while Whisper mostly writes digits, so the option
(Preferences → Advanced → Numbers) runs on the finished text and
works the same with every engine (GitHub Discussion #11).

The conversion itself is text2num's alpha2digit, which knows eight languages
and already leaves "no one", "one of them" and "the second one" alone. Around
it sit a few fixes for what it gets wrong in dictation:
- fractions stay words ("two thirds", not "two 3rds");
- "a hundred people" stays as said (not "a 100 people");
- digits read out one at a time join up ("08 6 2 0" -> "08620");
- years join up ("19 84" -> "1984", "19 oh 5" -> "1905");
- times get their colon ("at 7 30" -> "at 7:30");
- with "Digits from 10 up", a count below 10 the engine wrote as a digit
  becomes a word again ("3 cats" -> "three cats"), as Parakeet Ultra often
  writes them.
The English-only fixes run for English alone; the rest is plain digits.
"""
import logging
import re

from .language_codes import locale_language
from .normalize import LINE_BREAK, format_times

logger = logging.getLogger(__name__)

# The languages text2num reads.
SUPPORTED_LANGUAGES = frozenset({"en", "de", "es", "fr", "it", "pt", "nl", "da"})

# A lone number below this stays a word: text2num's own default (3) keeps "one"
# and "two", which are far more often words ("one of them", "two-way") than
# counts; _one_and_two then writes the ones that are labels or counts.
# Spelling out one to nine is the style-guide choice.
_ALL_THRESHOLD = 3
_SMALL_AS_WORDS_THRESHOLD = 10

_NUMBER_WORD = (r"(?:one|two|three|four|five|six|seven|eight|nine|ten|"
                r"eleven|twelve|a|an)")
_FRACTION_WORD = r"(?:third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|quarter)"

# English phrases that must reach the reader as words, hidden from text2num:
# "two thirds", "three quarters", "a third of it", "one fifth".
_RE_FRACTION = re.compile(
    rf"\b(?:{_NUMBER_WORD}[\s-]+)?{_FRACTION_WORD}s\b"          # plural: always a fraction
    rf"|\b(?:a|one)[\s-]+{_FRACTION_WORD}\b(?=\s+of\b)"          # "a third of", "one fifth of"
    rf"|\bone[\s-]+{_FRACTION_WORD}\b",                          # "one third" on its own
    re.IGNORECASE,
)
# "a hundred people", unless more of the number follows ("a hundred and fifty").
_RE_A_HUNDRED = re.compile(
    r"\ban?\s+(?:hundred|thousand|million|billion)\b"
    r"(?!\s+(?:and\s+)?(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand)\b)",
    re.IGNORECASE,
)

# Three or more digits read out one at a time: a zip code, a PIN, a phone
# number. text2num gives "08 6 2 0" for "zero eight six two zero".
_RE_DIGIT_RUN = re.compile(r"(?<![\d.,:])\b(?:0\d|\d)(?: \d\b){2,}(?![.,:]?\d)")
# "at 7 30", "by 11 45": an hour of 1-12 and its minutes after a time word.
_RE_TIME_AFTER_WORD = re.compile(
    r"\b(at|by|until|till|from|before|after|around|about|to)\s+(1[0-2]|[1-9]) ([0-5]\d)\b",
    re.IGNORECASE,
)
# "7 15 pm": an hour and minutes right before am/pm.
_RE_TIME_BEFORE_AMPM = re.compile(
    r"\b(1[0-2]|[1-9]) ([0-5]\d)(?=\s*[ap]\.?\s*m\b\.?)", re.IGNORECASE)
# "19 84", "20 25": the two halves of a spoken year. Only centuries people
# actually say (1100s to 2000s), so "10 30" or "7 30" are never years.
_RE_YEAR = re.compile(r"(?<![\d.,:])\b(1[1-9]|20) ([1-9]\d)\b(?![.,:]?\d)")
# "19 oh 5": nineteen oh five.
_RE_YEAR_OH = re.compile(r"\b(1[1-9]|20) oh (\d)\b", re.IGNORECASE)

# A lone digit 1-9 before a lowercase word: a count Parakeet Ultra wrote as a
# digit ("3 cats"), which "Digits from 10 up" writes as a word.
_RE_SMALL_DIGIT = re.compile(r"(?<![\w.,:$€£#+\-/])([1-9])(?=\s+([a-z][\w'-]*))")
_RE_RANGE_AFTER = re.compile(r"\s+(?:to|and|or|through|out of|x|by)\s+\d")
_RE_RANGE_BEFORE = re.compile(r"\d\s+(?:to|and|or|through|out of|x|by)$")
_SMALL_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
# Words that put a number on something rather than count it ("page 3").
_LABELS = frozenset({
    "page", "pages", "chapter", "step", "version", "level", "number", "no", "room", "row",
    "line", "gate", "track", "season", "episode", "part", "volume", "grade", "floor",
    "platform", "channel", "verse", "figure", "table", "option", "round", "day", "week",
    "slide", "section", "item", "zone", "phase", "stage", "seat", "lane", "bus", "route",
})
# Everyday nouns that number something only where the number ends the phrase
# ("test one", "plan two"): mid-sentence the number starts the next part ("just
# in case one of them breaks", "a question one might ask").
_LABELS_AT_THE_END = frozenset({
    "test", "plan", "thing", "team", "player", "act", "scene", "model", "type", "size",
    "class", "group", "unit", "case", "exhibit", "article", "rule", "question", "problem",
    "exercise", "lesson", "task", "camera", "server", "account", "file", "folder", "tab",
    "screen", "monitor", "door", "box", "column", "field", "sheet", "tier", "batch",
    "sprint", "suite", "building", "block", "terminal", "studio", "court", "ward",
})
# Of _LABELS, the ones that also count time or places, which a number after
# them mid-sentence usually begins something new ("every day one of them is
# late", "in the room two people were talking").
_COUNTING_LABELS = frozenset({"day", "week", "room", "line", "row", "round", "seat"})
# Units and clock words a digit stays in front of ("3 pm", "2 kg").
_UNITS = frozenset({
    "am", "pm", "kg", "km", "cm", "mm", "m", "g", "mg", "lb", "lbs", "oz", "ft", "mph",
    "kph", "gb", "mb", "kb", "tb", "ms", "hz", "khz", "ghz", "v", "w", "x", "k", "percent",
})


def _small_digits_as_words(text: str) -> str:
    """The counts below 10 that the engine wrote as digits, as words. A digit
    after a name or a label ("Python 3", "page 3"), before a unit ("3 GB",
    "3 pm") or in a range ("2 to 4") stays a digit."""
    def word(m: re.Match) -> str:
        """One to nine back as a word ("3 cats" -> "three cats"), capitalized
        at a sentence's start, but kept a digit with a unit, in a range, or
        after a name or a label ("Python 3", "page 4")."""
        digit, after = m.group(1), m.group(2)
        before = text[:m.start()].rstrip()
        prev = re.search(r"([\w'-]+)\W*$", before)
        if after.lower() in _UNITS:
            return digit
        if _RE_RANGE_AFTER.match(text, m.end()) or _RE_RANGE_BEFORE.search(before):
            return digit                                    # "2 to 4", "3 out of 10"
        sentence_start = (not before or before[-1] in ".!?"
                          or text[:m.start()].rstrip(" \t").endswith(("\n", LINE_BREAK)))
        if prev and not sentence_start:
            before_word = prev.group(1)
            # A name: any capital in it ("Python 3", "iPhone 5"), not "I".
            if before_word.lower() in _LABELS or (before_word != "I"
                                                  and not before_word.islower()):
                return digit
        spelled = _SMALL_WORDS[int(digit) - 1]
        return spelled.capitalize() if sentence_start else spelled
    return _RE_SMALL_DIGIT.sub(word, text)


# --- "one" and "two" with digits on, by the shape of the phrase ---
# A word, the space-free runs between words (with any mark, a phrase
# boundary), and what text2num left of "one" and "two".
_RE_WORD = re.compile(r"[^\W_][\w'’-]*")
_ONE_TWO = ("one", "two")
# Before "one" or "two", these make it a pronoun ("the right one", "this
# one", "which one", "my two cents"), two words back too ("the blue one").
_POINTERS = frozenset({
    "the", "a", "an", "this", "that", "these", "those", "which", "each", "every", "no",
    "any", "some", "another", "other", "either", "neither", "my", "your", "his", "her",
    "its", "our", "their", "both", "all", "only", "same", "last", "first", "next", "top", "not",
    "right", "wrong", "good", "nice", "great", "big", "little", "new", "old", "best",
    "better", "worst", "favorite", "favourite", "lucky", "special",
})
# Words that aren't a label before a number at a phrase's end ("I want one",
# "call me at one", "table for two"), nor a counted thing after one ("one is
# enough", "two are coming").
_FUNCTION_WORDS = frozenset({
    "i", "me", "you", "he", "him", "she", "it", "we", "us", "they", "them", "who",
    "is", "are", "was", "were", "be", "been", "am", "do", "does", "did", "have", "has",
    "had", "will", "would", "can", "could", "should", "may", "might", "must", "shall",
    "want", "need", "take", "took", "get", "got", "give", "gave", "buy", "bought", "make",
    "made", "pick", "choose", "see", "saw", "find", "found", "use", "add", "try", "call",
    "keep", "show", "send", "bring", "say", "said", "like", "love", "ask", "hold", "wait",
    "pay", "cost", "costs", "sell", "order", "go", "goes", "went", "come", "came", "count",
    "of", "for", "to", "in", "on", "at", "by", "with", "from", "about", "into", "than",
    "as", "and", "or", "but", "so", "if", "then", "between", "after", "before", "around",
    "under", "over", "until", "since", "per", "via", "only", "just", "also", "too",
    "again", "more", "less", "here", "there", "now", "even", "still", "maybe", "exactly",
    "almost", "least", "most", "very", "really", "each", "not", "today", "tomorrow",
    "yesterday", "tonight", "up", "down", "out", "off", "away", "back", "later", "soon",
    "please", "either", "ever", "yet", "already", "left", "free", "never", "always",
    "usually", "often", "against", "versus", "vs", "all", "sharp", "nil", "apiece", "win",
    "lose", "without", "ish",
})
# Past forms after "one" that make it a person ("if one knew", "one thought so").
_PAST_VERBS = frozenset({
    "knew", "thought", "felt", "took", "made", "got", "went", "saw", "found", "kept", "meant",
    "said", "told", "came", "gave", "left", "ran", "won", "lost", "bought", "brought",
})
# After "one", phrases where it isn't a count ("one of", "one day", "one
# more thing", "one small step"); after "two", the same few ("two of", "my
# two cents").
_IDIOMS_AFTER = {
    "one": frozenset({
        "of", "another", "by", "at", "way", "or", "and", "too", "day", "time", "thing",
        "more", "last", "point", "hand", "side", "sec", "second", "minute", "moment",
        "bit", "kind", "sort", "each", "either", "other", "half", "small", "big",
        "little", "single", "whole", "such", "short", "after", "piece", "condition"}),
    "two": frozenset({"of", "or", "and", "cents", "each", "by", "at", "way"}),
}


def _neighbours(text: str, words: list, i: int):
    """(the two words before words[i], the two after), each lower case and
    None past a phrase boundary: anything but spaces between two words."""
    def joined(a, b):
        return not text[words[a].end():words[b].start()].strip(" \t")
    before = [None, None]
    if i >= 1 and joined(i - 1, i):
        before[1] = words[i - 1].group().lower()
        if i >= 2 and joined(i - 2, i - 1):
            before[0] = words[i - 2].group().lower()
    after = [None, None]
    if i + 1 < len(words) and joined(i, i + 1):
        after[0] = words[i + 1].group().lower()
        if i + 2 < len(words) and joined(i + 1, i + 2):
            after[1] = words[i + 2].group().lower()
    return before, after


# After a number, words that leave it the end of its phrase: "test one and
# two", "go to room two now", "the version two we installed".
_PHRASE_GOES_ON = frozenset({
    "and", "or", "but", "then", "now", "today", "tomorrow", "tonight", "yesterday",
    "later", "again", "please", "too", "instead", "i", "we", "you", "he", "she", "they",
})


def _after_a_label(word: str, prev: str, prev2: str, ends: bool) -> bool:
    """Whether *prev* numbers this "one" or "two" ("page two of it", "test
    one" where the phrase *ends*). Not after a determiner ("the room two
    people were in"); "no one" is nobody, and "my number one" the one liked
    best."""
    if prev2 in _POINTERS and not (ends and prev in _LABELS):
        return False
    if prev in _LABELS_AT_THE_END or prev in _COUNTING_LABELS:
        # "the round one" is the one that's round.
        return ends and not (prev in _COUNTING_LABELS and prev2 in _POINTERS)
    if prev not in _LABELS or prev == "no":
        return False
    return not (prev == "number" and word == "one" and prev2 in _POINTERS)


def _looks_past(word: str) -> bool:
    """Whether *word* reads as a verb's past ("wanted", "learned"), not a
    short word or one in -eed ("red", "bed", "speed")."""
    return word.endswith("ed") and len(word) >= 5 and not word.endswith("eed")


def _counts(word: str, nxt: str) -> bool:
    """Whether this "one" or "two" counts *nxt*, the word after it ("two
    cats"), rather than starting an idiom ("one of", "one day") or standing
    before a verb or small word ("one is", "two came"). One of something is
    never plural, so "one" before a word in -s is a person or thing doing
    it ("if one breaks", "one says yes"), and so it is before a past form
    ("one knew", "what one wanted")."""
    if word == "one" and (nxt in _PAST_VERBS or _looks_past(nxt)
                          or (nxt.endswith("s") and not nxt.endswith(("ss", "us", "is")))):
        return False
    return (nxt not in _IDIOMS_AFTER[word] and nxt not in _FUNCTION_WORDS
            and nxt[:1].isalpha())


def _as_digit(word: str, before: list, after: list, name: bool) -> bool:
    """Whether this "one" or "two" is a label or a count, by the words
    around it: after a label word ("test one", "part two of it") or a name
    at a phrase's end ("Python two"), or a count before what it counts
    ("two cats", "my two cats"). A pronoun ("the right one", "which one",
    "the one thing"), an idiom ("one of them", "one or two") and a number
    after a verb ("I want one", "we sold two") stay words."""
    prev2, prev = before
    nxt, nxt2 = after
    if prev in ("or", "and", "to") and prev2 in _ONE_TWO:
        return False                                    # "one or two people"
    ends = nxt is None or nxt in _PHRASE_GOES_ON
    if _after_a_label(word, prev, prev2, ends):
        return True
    if nxt in ("or", "and", "to") and nxt2 is not None and nxt2[:1].isdigit():
        return True                                     # "two or 3" -> "2 or 3"
    if ends:
        return name and prev not in _POINTERS          # "I still use Python two"
    if prev in _POINTERS or prev2 in _POINTERS:
        return word == "two" and _counts(word, nxt)     # "my 2 cats", "the one thing"
    return _counts(word, nxt)


def _starts_sentence(text: str, pos: int) -> bool:
    """Whether the word at *pos* is the first of its sentence or line."""
    before = text[:pos].rstrip(" \t")
    return not before or before.endswith((".", "!", "?", "\n", LINE_BREAK))


def _one_and_two(text: str) -> str:
    """The "one" and "two" text2num left as words, as digits where they are
    labels or counts ("number 1", "test 2", "2 cats"), words where they are
    pronouns or idioms ("this one", "one of them", "no one")."""
    words = list(_RE_WORD.finditer(text))
    out, pos, digits = [], 0, set()
    for i, m in enumerate(words):
        word = m.group().lower()
        if word not in _ONE_TWO:
            continue
        before, after = _neighbours(text, words, i)
        # A capital is a name's ("Python two"), but not a sentence's first
        # word's ("Pick one.").
        name = bool(before[1]) and words[i - 1].group()[:1].isupper() and not (
            _starts_sentence(text, words[i - 1].start()))
        # "page one of two": the second number goes with the first.
        of_a_digit = before[1] == "of" and i - 2 in digits
        if of_a_digit or _as_digit(word, before, after, name):
            out += [text[pos:m.start()], "1" if word == "one" else "2"]
            pos = m.end()
            digits.add(i)
    return "".join(out) + text[pos:]


def to_digits(text: str, language: str, small_as_words: bool = False) -> str:
    """*text* with its spoken numbers written as digits.

    *language* is the dictation language ("en", "de_CH", ...); for one
    text2num doesn't read, the text comes back unchanged. With
    *small_as_words*, a lone number below 10 stays a word ("three cats",
    "25 chickens"); digits read out one at a time are joined either way.
    """
    lang = locale_language(language)
    if not text or lang not in SUPPORTED_LANGUAGES:
        return text
    try:
        from text_to_num import alpha2digit
    except ImportError:
        logger.warning("Numbers as digits is on, but text2num isn't installed")
        return text

    english = lang == "en"
    # Hide the phrases that must stay words behind private-use characters,
    # which text2num reads as ordinary non-number words.
    hidden: list[str] = []

    def hide(m: re.Match) -> str:
        hidden.append(m.group())
        return chr(0xE000 + len(hidden) - 1)

    if english:
        text = _RE_FRACTION.sub(hide, text)
        text = _RE_A_HUNDRED.sub(hide, text)

    threshold = _SMALL_AS_WORDS_THRESHOLD if small_as_words else _ALL_THRESHOLD
    try:
        text = alpha2digit(text, lang, threshold=threshold)
    except Exception as e:  # a parser surprise must never cost the dictation
        logger.warning(f"Couldn't convert numbers to digits: {e}")

    if english:
        text = _RE_TIME_AFTER_WORD.sub(lambda m: f"{m.group(1)} {m.group(2)}:{m.group(3)}", text)
        text = _RE_TIME_BEFORE_AMPM.sub(lambda m: f"{m.group(1)}:{m.group(2)}", text)
        text = format_times(text)
        text = _RE_YEAR_OH.sub(lambda m: f"{m.group(1)}0{m.group(2)}", text)
    text = _RE_YEAR.sub(lambda m: m.group(1) + m.group(2), text)
    text = _RE_DIGIT_RUN.sub(lambda m: m.group().replace(" ", ""), text)
    if english and small_as_words:
        text = _small_digits_as_words(text)
    elif english:
        text = _one_and_two(text)

    for i, phrase in enumerate(hidden):
        text = text.replace(chr(0xE000 + i), phrase)
    return text
