"""Fitting a dictation into the text on either side of the cursor: no
capital in the middle of a sentence, a space in front where one is missing,
and no closing period or doubled space when text follows on the line.

The text around the cursor comes from the app (atspi_helper), a few
characters each side; when the app doesn't say, nothing here runs and the
dictation is typed as it always was."""
import re

from . import chunk_text
from .language_codes import locale_language
from .normalize import safe_to_lowercase_first_word

# How many characters on each side of the cursor are asked for: enough to
# look past a few spaces to the nearest visible one.
WINDOW = 8

_SENTENCE_ENDS = ".!?"
# A line break as apps report it: Qt's rich text boxes give U+2029.
_LINE_BREAKS = "\n\r\u2028\u2029"
# After one of these, a dictation goes in without a space in front; a
# straight quote only when it opens (_opens).
_OPENERS = "([{\u201c\u2018"
_STRAIGHT_QUOTES = "\"'"
# Closing marks that can follow a sentence's end: (see the note.)
_CLOSERS = ")]}\u201d\u2019\"'"
_BLANKS = " \t"
# A mark with more after it inside a dictation, and the word it follows.
_MARK_THEN_MORE = re.compile(r"([\w.'\u2019-]*)([.!?])[)\]}\u201d\u2019\"']*\s+\S")
_DOTTED_LETTERS = re.compile(r"(?:[^\W\d_]\.)*[^\W\d_]")
_LINE_THEN_MORE = re.compile(r"[\n\r\u2028\u2029]\s*\S")
# Languages that write every noun with a capital, so a capital at the start
# of a dictation says nothing about where the sentence begins.
_NOUN_CAPITALS = {"de", "lb"}


# The marks a dictation can start with when said on its own (dictation_text),
# which join the text before it as they are (_fit_start).
MARKS = ".,;:!?"
_BLANKS_AT_END = re.compile(r"[ \t]*\Z")
# The word a full stop ends, its own dots with it ("U.S" of "U.S.").
_WORD_BEFORE_STOP = re.compile(r"[\w.]*(?=\.\Z)")


def taken_by_mark(before: str) -> str:
    """The end of *before*, the text before the cursor, that a dictation
    starting with a mark said on its own goes in place of: the blanks after
    the last word, with the full stop after it ("Is it ready. " then
    "question mark" is "Is it ready?"), not an ellipsis's nor an
    abbreviation's (is_abbreviation). Nothing past a line break."""
    blanks = _BLANKS_AT_END.search(before).group()
    text = before[:len(before) - len(blanks)]
    if not text or text[-1] in _LINE_BREAKS:
        return ""       # the mark starts the line: nothing before it on it
    if not text.endswith(".") or text.endswith(".."):
        return blanks
    word = _WORD_BEFORE_STOP.search(text).group()
    return blanks if ends_abbreviation(word) else "." + blanks


def fit(text: str, before: str, after: str, lowercase_ok: bool, words=()) -> str:
    """*text*, a dictation shaped for typing, fitted between *before* and
    *after*, the text on either side of the cursor ("" at the start or end
    of the field). *lowercase_ok*: the dictation's language doesn't
    capitalise nouns. *words*: Your Words, which keep their capitals."""
    if not text:
        return text
    own_sentence = _starts_sentence(before) or _breaks_inside(text.rstrip())
    return _fit_end(_fit_start(text, before, lowercase_ok, words), after, own_sentence)


def fit_end(text: str, after: str) -> str:
    """The end of *text* fitted before *after*, the text after the cursor:
    the last phrase of a recording typed at each pause (type_at_pause), or
    the mark and space held back after it. As a whole dictation's end, but
    its period stays before a capital: where its sentence began can't be
    read back (the read is a few characters, and the phrase's keys may not
    have landed yet), and a period too many is easier to see than two
    sentences run together."""
    return _fit_end(text, after, own_sentence=True) if text else text


def _starts_sentence(before):
    """Whether text put after *before* starts a sentence: at the start of
    the field or a line, or after a sentence's end."""
    visible = before.rstrip(_BLANKS)
    return not visible or visible[-1] in _LINE_BREAKS \
        or visible.rstrip(_CLOSERS).endswith(tuple(_SENTENCE_ENDS))


def _fit_start(text, before, lowercase_ok, words):
    """The start of *text* after *before*: carried on in lowercase in the
    middle of a sentence, with a space in front where one is missing.
    Punctuation said on its own and a line break go in as they are."""
    visible = before.rstrip(_BLANKS)
    if text[0] in MARKS + _LINE_BREAKS + _BLANKS or not visible \
            or visible[-1] in _LINE_BREAKS:
        return text     # joins as it is, or a new line: a new sentence, as always
    if not _starts_sentence(before) and lowercase_ok \
            and text[0].isupper() and safe_to_lowercase_first_word(text, words):
        text = text[0].lower() + text[1:]
    if before[-1] in _BLANKS or _opens(before):
        return text
    return " " + text


def _breaks_inside(text):
    """Whether *text* ends a sentence or a line before its last sentence
    ("After lunch. We left."); an abbreviation's period ("U.S.", "Dr.", an
    initial) ends none."""
    if _LINE_THEN_MORE.search(text):
        return True
    return any(mark != "." or not is_abbreviation(word)
               for word, mark in _MARK_THEN_MORE.findall(text))


def ends_abbreviation(word):
    """Whether a full stop after *word*, the last one before it, is the
    abbreviation's (is_abbreviation), not a sentence's: "I" and "a", alone,
    end sentences ("Neither do I."). False for no word."""
    return bool(word) and word not in ("I", "a", "A") and is_abbreviation(word)


def is_abbreviation(word):
    """Whether *word*, before a period, is an abbreviation: single letters
    with periods between ("U.S", "e.g"), an initial, or a known one
    (chunk_text). A number ("3.14") or an address ("github.com") isn't."""
    return _DOTTED_LETTERS.fullmatch(word) is not None \
        or word.casefold() in chunk_text.ABBREVIATIONS


def _opens(before):
    """Whether *before* ends on an opening bracket or quote: a straight
    quote opens after a space, another opener or nothing ('he said "')."""
    last = before[-1]
    if last in _OPENERS:
        return True
    return last in _STRAIGHT_QUOTES and (len(before) == 1 or before[-2] in _BLANKS + _OPENERS)


def _fit_end(text, after, own_sentence):
    """The end of *text* before *after*: no space of its own to double, and
    no closing period when the sentence goes on after it on the same line.
    A sentence of its own (*own_sentence*: it starts one, or holds a break
    before its last) put before a capital keeps it: whether "I" or a name
    there starts a sentence can't be told, and a period too many is easier
    to see than two sentences run together. A dictation ending in a line
    break ends as it is."""
    if text.rstrip(_BLANKS)[-1:] in tuple(_LINE_BREAKS):
        return text.rstrip(_BLANKS)
    if after and after[0] in _BLANKS:
        text = text.rstrip(_BLANKS)
    following = after.lstrip(_BLANKS)
    if not following or following[0] in _LINE_BREAKS:
        return text
    text = text.rstrip(_BLANKS)
    if text.endswith(".") and not text.endswith("..") \
            and not (own_sentence and following[0].isupper()):
        text = text[:-1]
    if after[0] not in _BLANKS and following[0].isalnum():
        text += " "
    return text


def capitalised_nouns(language, auto_languages, usual_language: str) -> bool:
    """Whether a dictation may be in a language that capitalises its nouns:
    *language*, the dictation's own when set; else any of *auto_languages*
    (those auto-detect picks from); else *usual_language*, the one first run
    picked or the desktop's (recommend.effective_language)."""
    if language:
        return locale_language(language) in _NOUN_CAPITALS
    if auto_languages:
        return any(locale_language(code) in _NOUN_CAPITALS for code in auto_languages)
    return locale_language(usual_language) in _NOUN_CAPITALS
