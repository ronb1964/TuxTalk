"""Fitting a dictation into the text on either side of the cursor: no
capital in the middle of a sentence, a space in front where one is missing,
and no closing period or doubled space when text follows on the line.

The text around the cursor comes from the app (atspi_helper), a few
characters each side; when the app doesn't say, nothing here runs and the
dictation is typed as it always was."""
from .normalize import safe_to_lowercase_first_word

# How many characters on each side of the cursor are asked for: enough to
# look past a few spaces to the nearest visible one.
WINDOW = 8

_SENTENCE_ENDS = ".!?"
# After one of these, a dictation goes in without a space in front.
_OPENERS = "([{\"'“‘"
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
    return _fit_end(_fit_start(text, before, lowercase_ok, words), after)


def _fit_start(text, before, lowercase_ok, words):
    """The start of *text* after *before*: carried on in lowercase in the
    middle of a sentence, with a space in front where one is missing."""
    visible = before.rstrip(_BLANKS)
    if not visible or visible.endswith("\n"):
        return text     # the start of a line: a new sentence, as always
    if not visible.endswith(tuple(_SENTENCE_ENDS)) and lowercase_ok and text[0].isupper() \
            and safe_to_lowercase_first_word(text, words):
        text = text[0].lower() + text[1:]
    if before[-1] in _BLANKS or before[-1] in _OPENERS:
        return text
    return " " + text


def _fit_end(text, after):
    """The end of *text* before *after*: no closing period and no space of
    its own to double when text follows on the same line."""
    if after and after[0] in _BLANKS:
        text = text.rstrip(_BLANKS)
    following = after.lstrip(_BLANKS)
    if not following or following.startswith("\n"):
        return text
    text = text.rstrip(_BLANKS)
    if text.endswith(".") and not text.endswith(".."):
        text = text[:-1]
    if after[0] not in _BLANKS and following[0].isalnum():
        text += " "
    return text


def _code(language: str) -> str:
    """The two- or three-letter language code of *language* ("de_CH" -> "de")."""
    return language.replace("-", "_").split("_")[0].lower()


def capitalised_nouns(language, auto_languages, system_language: str) -> bool:
    """Whether a dictation may be in a language that capitalises its nouns:
    *language*, the dictation's own when set; else any of *auto_languages*
    (those auto-detect picks from); else the system's language."""
    if language:
        return _code(language) in _NOUN_CAPITALS
    if auto_languages:
        return any(_code(code) in _NOUN_CAPITALS for code in auto_languages)
    return bool(system_language) and _code(system_language) in _NOUN_CAPITALS
