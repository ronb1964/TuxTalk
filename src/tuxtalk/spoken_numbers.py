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

from .normalize import LINE_BREAK, format_times

logger = logging.getLogger(__name__)

# The languages text2num reads.
SUPPORTED_LANGUAGES = frozenset({"en", "de", "es", "fr", "it", "pt", "nl", "da"})

# A lone number below this stays a word: text2num's own default (3) keeps "one"
# and "two", which are far more often words ("one of them", "two-way") than
# counts. Spelling out one to nine is the style-guide choice.
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


def _language(code: str) -> str:
    """"en_US", "pt-BR" or "de" -> the bare language code."""
    return (code or "").replace("-", "_").split("_")[0].lower()


def to_digits(text: str, language: str, small_as_words: bool = False) -> str:
    """*text* with its spoken numbers written as digits.

    *language* is the dictation language ("en", "de_CH", ...); for one
    text2num doesn't read, the text comes back unchanged. With
    *small_as_words*, a lone number below 10 stays a word ("three cats",
    "25 chickens"); digits read out one at a time are joined either way.
    """
    lang = _language(language)
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

    for i, phrase in enumerate(hidden):
        text = text.replace(chr(0xE000 + i), phrase)
    return text
