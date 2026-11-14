"""Fitting a dictation into the text on either side of the cursor: no
capital in the middle of a sentence, a space in front where one is missing,
and no closing period or doubled space when text follows on the line.

The text around the cursor comes from the app (atspi_helper), a few
characters each side; when the app doesn't say, nothing here runs and the
dictation is typed as it always was."""
from . import config
from .normalize import safe_to_lowercase_first_word

# How many characters on each side of the cursor are asked for: enough to
# look past a few spaces to the nearest visible one.
WINDOW = 8

_SENTENCE_ENDS = ".!?"
# What a dictation can start with that joins the text before it as it is.
_PUNCTUATION = ".,;:!?"
# A line break as apps report it: Qt's rich text boxes give U+2029.
_LINE_BREAKS = "\n\r\u2028\u2029"
# After one of these, a dictation goes in without a space in front; a
# straight quote only when it opens (_opens).
_OPENERS = "([{\u201c\u2018"
_STRAIGHT_QUOTES = "\"'"
# Closing marks that can follow a sentence's end: (see the note.)
_CLOSERS = ")]}\u201d\u2019\"'"
_BLANKS = " \t"
# Languages that write every noun with a capital, so a capital at the start
# of a dictation says nothing about where the sentence begins.
_NOUN_CAPITALS = {"de", "lb"}


def fit(text: str, before: str, after: str, lowercase_ok: bool, words=()) -> str:
    """*text*, a dictation shaped for typing, fitted between *before* and
    *after*, the text on either side of the cursor ("" at the start or end
    of the field). *lowercase_ok*: the dictation's language doesn't
    capitalise nouns. *words*: Your Words, which keep their capitals."""
    if not text:
        return text
    visible = before.rstrip(_BLANKS)
    starts_sentence = not visible or visible[-1] in _LINE_BREAKS \
        or visible.rstrip(_CLOSERS).endswith(tuple(_SENTENCE_ENDS))
    return _fit_end(_fit_start(text, before, lowercase_ok, words), after, starts_sentence,
                    words)


def _fit_start(text, before, lowercase_ok, words):
    """The start of *text* after *before*: carried on in lowercase in the
    middle of a sentence, with a space in front where one is missing.
    Punctuation said on its own and a line break go in as they are."""
    visible = before.rstrip(_BLANKS)
    if text[0] in _PUNCTUATION + _LINE_BREAKS + _BLANKS or not visible \
            or visible[-1] in _LINE_BREAKS:
        return text     # joins as it is, or a new line: a new sentence, as always
    if not visible.rstrip(_CLOSERS).endswith(tuple(_SENTENCE_ENDS)) and lowercase_ok \
            and text[0].isupper() and safe_to_lowercase_first_word(text, words):
        text = text[0].lower() + text[1:]
    if before[-1] in _BLANKS or _opens(before):
        return text
    return " " + text


def _opens(before):
    """Whether *before* ends on an opening bracket or quote: a straight
    quote opens after a space, another opener or nothing ('he said "')."""
    last = before[-1]
    if last in _OPENERS:
        return True
    return last in _STRAIGHT_QUOTES and (len(before) == 1 or before[-2] in _BLANKS + _OPENERS)


def _fit_end(text, after, starts_sentence, words):
    """The end of *text* before *after*: no space of its own to double, and
    no closing period when the sentence goes on after it on the same line;
    a whole sentence put in front of another (*starts_sentence*, and a
    capital after it that only a sentence start has: not "I", an acronym or
    one of Your *words*) keeps it. A dictation ending in a line break ends
    as it is."""
    if text.rstrip(_BLANKS)[-1:] in tuple(_LINE_BREAKS):
        return text.rstrip(_BLANKS)
    if after and after[0] in _BLANKS:
        text = text.rstrip(_BLANKS)
    following = after.lstrip(_BLANKS)
    if not following or following[0] in _LINE_BREAKS:
        return text
    text = text.rstrip(_BLANKS)
    if text.endswith(".") and not text.endswith("..") \
            and not (starts_sentence and following[0].isupper()
                     and safe_to_lowercase_first_word(following, words)):
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
        return config.locale_language(language) in _NOUN_CAPITALS
    if auto_languages:
        return any(config.locale_language(code) in _NOUN_CAPITALS for code in auto_languages)
    return config.locale_language(usual_language) in _NOUN_CAPITALS
