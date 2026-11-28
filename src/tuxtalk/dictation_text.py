"""What a dictation's words go through before they're typed: the snippets
and Your Words (custom_commands.py), the spoken-command rules, filler words,
numbers as digits, punctuation, plain text for terminals and code editors,
and the AI's self-corrections when they're on.

What the user's settings say comes in as *settings*: anything with
live_settings' names (commands, words, numbers_mode, numbers_language,
command_language, remove_fillers, ai_corrections, ai_engine, swiss_spelling,
no_final_period, loggable), and
live_settings itself in the service. Whether the dictation carries on the
last sentence is the caller's to say, since that depends on the window."""
import re
from collections.abc import Callable

from . import custom_commands
from .logger import setup_logger
from .normalize import (
    LINE_BREAK,
    address_alone,
    append_auto_punct,
    leading_line_breaks,
    normalize_text,
    plain_for_app,
    safe_to_lowercase_first_word,
    swiss_spelling,
)
from .sentence_fit import MARKS, is_abbreviation

logger = setup_logger(__name__)


def apply_custom_commands(text: str, settings) -> tuple[str, dict[str, str]]:
    """The user's custom voice commands applied to the transcribed *text*
    (custom_commands.apply), with Your Words."""
    result, protected = custom_commands.apply(text, settings.commands, settings.words)
    if result != text:
        logger.info(f"Custom commands applied: {settings.loggable(text)} -> "
                    f"{settings.loggable(result)}")
    return result, protected


def numbers_to_digits(text: str, settings) -> str:
    """*text* with spoken numbers as digits, when that option is on."""
    if not settings.numbers_mode:
        return text
    from .spoken_numbers import to_digits
    return to_digits(text, settings.numbers_language,
                     small_as_words=settings.numbers_mode == "small_as_words")


# A made-up word put before a dictation to see whether it starts with a
# mark said aloud: the normalizer drops the marks a line starts with (the
# engine's strays), and after a word it keeps them.
_PROBE = "Zqxv"


# The mark the engine often writes after a word said on its own ("Comma.").
_ENGINE_END = re.compile(rf"(?<=\w)[{re.escape(MARKS)}]\s*$")


def _mark_alone(text: str, lang: str) -> str | None:
    """The mark a dictation of nothing else says ("question mark", "Comma."),
    or None. As said: a comma that ends a dictation is a full stop
    otherwise, and an auto period isn't put after it."""
    said = _ENGINE_END.sub("", text.strip())
    if not said[:1].isalpha():
        return None
    probed = normalize_text(f"{_PROBE} {said}", auto_period=False, lang=lang)
    mark = probed[len(_PROBE):]
    return mark if probed.startswith(_PROBE) and len(mark) == 1 and mark in MARKS else None


def _normalized(text: str, auto_period: bool, lang: str, whole: bool) -> str:
    """normalize_text of *text*, keeping a mark said at its start ("question
    mark", "comma and then he left"), which normalize_text alone drops with
    the engine's stray ones. Only a whole dictation's: a Type at pause
    phrase's marks are type_at_pause's to decide."""
    text_alone = normalize_text(text, auto_period=auto_period, lang=lang)
    if not whole or not text.lstrip()[:1].isalpha():
        return text_alone       # an engine's stray mark comes as the mark itself
    # Asked with no auto-period: that would end the made-up word's line with
    # a full stop of its own where a line break is said first ("new line, ...").
    probed = normalize_text(f"{_PROBE} {text}", auto_period=False, lang=lang)
    # A tuple, so an empty rest isn't "in" it as "" is in a string.
    if probed.startswith(_PROBE) and probed[len(_PROBE):][:1] in tuple(MARKS):
        return normalize_text(f"{_PROBE} {text}", auto_period=auto_period, lang=lang)[len(_PROBE):]
    return text_alone


# The full stop a dictation ends with: before the auto-space, a closing
# quote or bracket, and a line break said last; not one of an ellipsis.
_FINAL_STOP = re.compile(r"(?<![.\s])\.(?=[\"'\u201d\u2019)\]}]*(?:\s|"
                         + re.escape(LINE_BREAK) + r")*\Z)")
_LAST_WORD = re.compile(r"[\w.]*\Z")
# A full stop the engine wrote itself, after a word (not inside "U.S.").
_ENGINE_STOPS = re.compile(r"(?<=\w)\.(?=[\s\"'\u201d\u2019)\]}]|\Z)")


def _said_last_stop(heard: str, lang: str) -> bool:
    """Whether *heard*, the dictation as the engine wrote it, ends with a
    full stop said ("... period", "full stop", "Punkt"), as the normalizer
    reads it: the engine's own full stops taken off, does one still end it?"""
    said = _ENGINE_STOPS.sub("", heard)
    text = normalize_text(said, auto_period=False, lang=lang).replace(LINE_BREAK, " ")
    return text.rstrip().rstrip("\"'\u201d\u2019)]}").endswith(".")


def without_final_period(text: str, heard: str, lang: str) -> str:
    """*text*, ready to type, without the full stop it ends with (Period at
    the end: Never): not an abbreviation's (sentence_fit.is_abbreviation),
    nor one said (_said_last_stop, *heard* in command language *lang*)."""
    found = _FINAL_STOP.search(text)
    if found is None:
        return text
    if is_abbreviation(_LAST_WORD.search(text[:found.start()]).group()) \
            or _said_last_stop(heard, lang):
        return text
    return text[:found.start()] + text[found.end():]


def clean_up(text, ai, settings):
    """The cleanup options: the rules first ("um", "the the"), then the AI
    for sentences with a self-correction (unless *ai* is False)."""
    if settings.remove_fillers:
        from .cleanup import clean_fillers_and_repeats
        text = clean_fillers_and_repeats(text)
    if ai and settings.ai_corrections and settings.ai_engine is not None:
        from . import ai_cleanup
        text = ai_cleanup.fix_self_corrections(text, settings.ai_engine)
    return text


def _carry_on_sentence(text, words, continues):
    """*text* carrying on the sentence the last dictation left unfinished,
    when *continues* (asked only now, see prepare_text) says it does: its
    first letter small, unless that would spoil a word (I, NASA, Your
    Words)."""
    if not text or continues is None or not continues():
        return text
    if text[0].isupper() and safe_to_lowercase_first_word(text, words):
        text = text[0].lower() + text[1:]
        logger.info("Lowercased first letter for mid-sentence continuation")
    return text


def _address_on_its_own(processed: str, protected: dict[str, str], settings) -> str | None:
    """The dictation typed as the address it is, when nothing else was said
    but line breaks before it ("New line, John at gmail dot com"); None
    otherwise. After the snippets, so "my email" said alone is an address on
    its own too; a quoted one is typed exactly as written."""
    breaks, said = "", processed
    if not address_alone(custom_commands.restore_protected(processed, protected)):
        breaks, said = leading_line_breaks(processed, settings.command_language)
        # Only where the line-break rules agree ("Return to ..." stays words).
        if not breaks or not normalize_text(
                processed, lang=settings.command_language).startswith(breaks):
            return None
    literal = custom_commands.restore_protected(said, protected)
    address = address_alone(literal)
    if address and literal != said:
        # As written, without the engine's full stop after the snippet.
        address = custom_commands.restore_protected(said.rstrip(" .,!?;:"), protected).strip()
    return breaks + address if address else None


def _said_alone(processed: str, protected: dict[str, str], settings) -> str | None:
    """What a whole dictation of only an address or only a mark types, as
    it is, with none of the rules for sentences; None for anything else."""
    address = _address_on_its_own(processed, protected, settings)
    if address:
        logger.info(f"Normalized text: {settings.loggable(address)} (an address on its own)")
        return address
    mark = not protected and _mark_alone(processed, settings.command_language)
    if mark:
        logger.info(f"Normalized text: {settings.loggable(mark)} (a mark said on its own)")
        return mark
    return None


def prepare_text(raw: str, smart_quotes: bool, auto_period: bool, auto_space: bool,
                 app_kind: str = "", ai: bool = True, whole: bool = True, *,
                 settings, continues: Callable[[], bool] | None = None) -> str:
    """Apply voice commands, normalize, handle mid-sentence, add auto-period/space.

    *app_kind* ("terminal", "code" or ""): the focused app wants plain text
    (no closing period, straight quotes; in a terminal, no starting capital
    either).
    *ai* False skips the AI self-corrections: Type at pause asks the AI about
    a phrase together with the one before it instead (type_at_pause.correct_across_pause).
    *whole* False is a Type at pause phrase, which may be part of a sentence:
    only a whole dictation can be an address said on its own (address_alone).
    *continues*: asked, last thing before the capitals are settled (after
    the AI, which can take seconds: the window may change meanwhile),
    whether it carries on the sentence the last dictation left unfinished
    in the same window, so its first letter is small (_carry_on_sentence).

    Returns the final text string ready for injection.
    """
    if app_kind:
        # A shell or a compiler doesn't read “curly” quotes as quotes.
        auto_period = smart_quotes = False
    # Apply custom voice commands (phrase → replacement)
    # Quoted replacements come back as placeholder tokens in `protected`
    processed, protected = apply_custom_commands(raw, settings)
    # Your Words, first pass: before tidying, a hyphen inside a word can only
    # have come from the engine ("Tux-Talk"), so it may join here.
    if settings.words:
        from .word_spelling import fix_spelling
        processed = fix_spelling(processed, settings.words)
    alone = whole and _said_alone(processed, protected, settings)
    if alone:
        return alone + (" " if auto_space else "")

    # Normalize text (capitalization, punctuation, etc.). auto_period is passed
    # through because this pass otherwise ends every line with a full stop of
    # its own, and append_auto_punct below would never get to decide.
    text = _normalized(
        processed if smart_quotes else processed.replace("\u201c","\"").replace("\u201d","\""),
        auto_period, settings.command_language, whole)
    # A lone "open quote" or "close quote" becomes a curly quote inside
    # normalize_text, so the preference is applied to its output as well.
    if not smart_quotes:
        text = text.replace("\u201c", "\"").replace("\u201d", "\"").replace("\u201e", "\"")

    # Optional cleanup, while quoted custom-command replacements are still
    # placeholders, so a literal replacement is never "cleaned". Rules first
    # ("um", "the the"), then the AI for sentences with a self-correction.
    text = clean_up(text, ai, settings)

    # Your Words, second pass: "tux talk" -> "TuxTalk" and iPhone's capitals
    # back after a sentence start made it IPhone. After snippets, so a snippet
    # whose phrase contains the split-up word still matches; while quoted
    # snippets are still placeholders, so a literal one is never touched; and
    # with no hyphen joins, since by now a hyphen means the user said "dash".
    if settings.words:
        from .word_spelling import fix_spelling
        text = fix_spelling(text, settings.words, hyphens=False)

    # Spoken numbers as digits. Before the quoted snippets come back, so a
    # literal snippet is typed exactly as written.
    text = numbers_to_digits(text, settings)

    # Restore quoted (literal) custom command replacements before any further
    # processing so that auto-period/space checks see the real final text.
    text = custom_commands.restore_protected(text, protected)

    # Swiss Standard German writes ss where the engines write ß. Last, so it
    # also covers snippets, Your Words and the AI's corrections.
    if settings.swiss_spelling:
        text = swiss_spelling(text)

    text = _carry_on_sentence(text, settings.words, continues)

    # Auto-period and auto-space. When the utterance ends with a line-break
    # command, the period lands BEFORE the break and no trailing space is
    # added, so "hello new line" leaves no orphan ". " on the next line.
    text = append_auto_punct(text, auto_period, auto_space)
    if whole and settings.no_final_period:
        text = without_final_period(text, raw, settings.command_language)
    if app_kind:
        text = plain_for_app(text, app_kind)
    logger.info(f"Normalized text: {settings.loggable(text)}")
    return text
