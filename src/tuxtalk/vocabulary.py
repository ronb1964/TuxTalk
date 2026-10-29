"""
Teach TuxTalk a word it keeps getting wrong, from a recent dictation.

The user picks the misheard words in a dictation ("bamboo studio") and types
what they should be ("BambuStudio"). The fix is saved as an ordinary custom
voice command, so:

- it works for every engine. It rewrites the transcription after the model
  heard it, which catches what steering the model (Whisper's prompt,
  parakeet_bias for Parakeet on the processor) still misses.
- the corrected word is also added to Your Words (custom_words.py), so
  Whisper models are told about it before they listen, and the fix shows up
  under that word in Preferences > Words, where it can be edited or removed.
- the dictation service picks it up on the next dictation, because it
  reloads custom_commands.toml whenever the file changes.

The Words tab works the same way: a word's mishearings are custom voice
commands that type that word, so the dictation service treats them like any
other. Commands that type something else are the Snippets.

The dialog lives in fix_word_dialog.py; this module is the part with no GTK,
so the rules can be tested.
"""
import os
import re

from . import config
from .logger import setup_logger

logger = setup_logger(__name__)

_WORD_RE = re.compile(r"\S+")
# Punctuation hugging the ends of a selection: quotes, commas, full stops,
# brackets. Apostrophes inside a word ("it's") are not at an edge and stay.
# Every part is optional, so it always matches.
_EDGE_RE = re.compile(r"^([^\w]*)(.*?)([^\w]*)$", re.DOTALL)

# Single words so common that "always fix" them would rewrite ordinary
# speech everywhere. Not exhaustive; it only has to catch the likely
# accidents, and the user can still go ahead after the warning.
_COMMON_WORDS = frozenset("""
a about after all also am an and any are as at be because been but by can
could did do does for from get go going got had has have he her here him his
how i if in into is it its it's just know like me more my no not now of on
one only or our out over really said say see she so some that the their them
then there these they this to too two three four five six seven eight nine ten
up us very was we well were what when where which who why will with would yes
yeah you your okay ok right
""".split())


def word_span(text, first, last):
    """(start, end) character offsets covering words *first*..*last*
    inclusive, in either order: the picked words in the dialog's text box."""
    first, last = sorted((first, last))
    spans = [m.span() for m in _WORD_RE.finditer(text)]
    return spans[first][0], spans[last][1]


def word_range(text, start, end):
    """The (first, last) words touched by a click or drag from *start* to
    *end* (character offsets, either order), or None if it hit only spaces.

    A plain click (start == end) picks the word under it, including a click
    just before its first letter or just after its last.
    """
    start, end = sorted((start, end))
    hit = []
    for i, m in enumerate(_WORD_RE.finditer(text)):
        if start == end:
            touched = m.start() <= start <= m.end()
        else:
            touched = m.start() < end and start < m.end()
        if touched:
            hit.append(i)
    return (hit[0], hit[-1]) if hit else None


def phrase_for(text, first, last):
    """The trigger phrase for a selection: lowercased, single-spaced, with the
    punctuation at its ends removed. Custom commands match case-insensitively
    against the raw transcription, which has no such punctuation."""
    start, end = word_span(text, first, last)
    core = _EDGE_RE.match(text[start:end]).group(2)
    return " ".join(core.split()).lower()


def core_span(text, first, last):
    """(start, end) of the picked words without the punctuation around them:
    the part fixed_text replaces ("Bamboo Studio," -> just "Bamboo Studio")."""
    start, end = word_span(text, first, last)
    lead, core, _trail = _EDGE_RE.match(text[start:end]).groups()
    return start + len(lead), start + len(lead) + len(core)


def fixed_text(text, first, last, replacement):
    """*text* with the selected words replaced, keeping the punctuation around
    them ("Bamboo Studio," -> "BambuStudio,") and everything else as it was."""
    start, end = word_span(text, first, last)
    lead, _core, trail = _EDGE_RE.match(text[start:end]).groups()
    return text[:start] + lead + replacement + trail + text[end:]


def is_risky(phrase):
    """True for a single everyday word, which would be rewritten every time
    the user says it, not just when the model mishears something."""
    return " " not in phrase and phrase in _COMMON_WORDS


def _matching_keys(commands, phrase):
    """The command phrases equal to *phrase* once lowercased and single-spaced."""
    # Preferences stores phrases lowercased, but a hand-edited file may not.
    return [k for k in commands if " ".join(k.split()).lower() == phrase]


def existing_fix(phrase):
    """What *phrase* is already corrected to, or None."""
    commands = config.load_custom_commands()
    keys = _matching_keys(commands, phrase)
    return commands[keys[0]] if keys else None


def save_fix(phrase, replacement):
    """Save *phrase* -> *replacement* into the custom voice commands.

    Returns what the phrase used to be replaced with, or None if it is new.
    Raises config.ConfigNotLoadedError rather than write, if the existing
    commands file could not be read (writing would delete the user's commands).
    """
    commands = config.load_custom_commands()
    keys = _matching_keys(commands, phrase)
    previous = commands[keys[0]] if keys else None
    for k in keys:
        del commands[k]
    commands[phrase] = replacement
    config.save_custom_commands(commands)
    # Also one of Your Words, so Whisper is told about it before it listens.
    # The fix itself is already saved; this part must never undo that. The
    # old word stays while another mishearing still fixes to it: dropped, that
    # fix shows as a snippet in the Words tab.
    dropped = None if previous in commands.values() else previous
    try:
        from . import custom_words
        custom_words.learn_fix(replacement, dropped, _COMMON_WORDS)
    except Exception as e:
        logger.warning(f"Fix saved, but not added to your words: {e}")
    return previous


# --- the Words tab ------------------------------------------------------------

class WordProblem(ValueError):
    """A word or mishearing the Words tab can't save; the message says why."""


def _clean_phrase(phrase):
    return " ".join(phrase.split()).lower()


def split_commands(words, commands):
    """(rows, snippets) for the Words tab. rows: each of *words* with the
    command phrases that type exactly it, [(word, [phrase, ...])] in the
    words' order. snippets: every other command."""
    fixes = {word: [] for word in words}
    snippets = {}
    for phrase, replacement in commands.items():
        if replacement in fixes:
            fixes[replacement].append(phrase)
        else:
            snippets[phrase] = replacement
    return [(word, fixes[word]) for word in words], snippets


def adopt_fixes():
    """Once, the first time the Words tab opens: add the spelling each word
    fix types ("clawed claw" -> ClaudeClaw) to Your Words, so the fixes are
    listed under their word and not among the snippets. Only what Fix a Word
    would add itself (custom_words.learn_fix); a copy of the old words file
    is kept beside it. Returns whether Your Words changed."""
    import shutil
    from . import custom_words
    if custom_words.has_header():
        return False
    commands = config.load_custom_commands()
    if config._commands_read_failed:
        return False            # try again when the file can be read
    try:
        words = custom_words.load(strict=True)
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Your Words: the words file couldn't be read: {e}")
        return False
    known = {w.casefold() for w in words}
    added = []
    for replacement in commands.values():
        word = replacement.strip()
        if (custom_words.is_word_like(word) and word.casefold() not in _COMMON_WORDS
                and word.casefold() not in known):
            known.add(word.casefold())
            added.append(word)
    old = custom_words.path()
    # Kept once: a header taken out by hand runs this again, after which the
    # file is no longer the one from before the Words tab.
    if os.path.exists(old) and not os.path.exists(old + ".before-words-tab"):
        shutil.copyfile(old, old + ".before-words-tab")
    custom_words.save(words + added)       # the header marks it done
    return bool(added)


def _phrases_for(word, phrases, commands, old):
    """The mishearings *phrases* to fix to *word*, cleaned and without
    repeats. Raises WordProblem for an everyday word, or a phrase another
    word or snippet already types (*old* is the row's previous word)."""
    wanted = []
    for phrase in map(_clean_phrase, phrases):
        if not phrase or phrase in wanted:
            continue
        if is_risky(phrase):
            raise WordProblem(f"\u201c{phrase}\u201d is an everyday word: TuxTalk would "
                              f"change it every time you say it.")
        # Every spelling of the phrase a hand edit left, since the word
        # takes them all over.
        taken = next((commands[k] for k in _matching_keys(commands, phrase)
                      if commands[k] not in (old, word)), None)
        if taken is not None:
            raise WordProblem(f"\u201c{phrase}\u201d already types \u201c{taken}\u201d. "
                              "Change or remove that one first.")
        wanted.append(phrase)
    return wanted


def save_word(word, phrases, old=None):
    """Save one row of the Words tab: *word*, and the *phrases* TuxTalk hears
    instead, which are fixed to it. *old* is the word the row had (None for a
    new row). Raises WordProblem (nothing written) for a clash, and
    config.ConfigNotLoadedError for a snippets file that can't be read."""
    from . import custom_words
    word = word.strip()
    if not custom_words.is_word_like(word):
        raise WordProblem("Type the word the way it should be spelled: a name or a "
                          "short term, not a sentence or an address.")
    commands = config.load_custom_commands()
    if config._commands_read_failed:
        raise config.ConfigNotLoadedError("The snippets file couldn't be read.")
    words = custom_words.load(strict=True)
    if old is not None and old not in words:
        old = None                       # removed meanwhile: save it as new
    for other in words:
        if other != old and other.casefold() == word.casefold():
            raise WordProblem(f"{other} is already one of your words.")
    wanted = _phrases_for(word, phrases, commands, old)
    before = dict(commands)
    if old is not None:
        # The row's own list is the whole list: a phrase taken off stops.
        # (A new word keeps any snippet already typing it; it's listed
        # under the word from now on.)
        commands = {p: r for p, r in commands.items() if r != old or p in wanted}
    for phrase in wanted:
        for same in _matching_keys(commands, phrase):
            del commands[same]          # a hand-edited "Clawed  Claw" becomes this one
        commands[phrase] = word
    if old is not None:
        words[words.index(old)] = word
    elif word not in words:
        words.append(word)
    _save_both(commands, words, before)


def remove_word(word):
    """Take *word* off Your Words, and the mishearings fixed to it. Raises
    config.ConfigNotLoadedError for a snippets file that can't be read."""
    from . import custom_words
    commands = config.load_custom_commands()
    if config._commands_read_failed:
        raise config.ConfigNotLoadedError("The snippets file couldn't be read.")
    words = custom_words.load(strict=True)
    _save_both({p: r for p, r in commands.items() if r != word},
               [w for w in words if w != word], commands)


def _save_both(commands, words, before):
    """Save the snippets file, then the words file. Each write is atomic but
    the pair isn't: when the words can't be written (a full disk), the
    snippets go back to *before*, so the Words tab doesn't show a word half
    saved. Raises the words file's OSError; if the put-back fails as well,
    one that says the snippets were changed all the same."""
    from . import custom_words
    config.save_custom_commands(commands)
    try:
        custom_words.save(words)
    except OSError as words_error:
        try:
            config.save_custom_commands(before)
        except OSError as e:
            logger.error(f"Your Words: the snippets couldn't be put back: {e}")
            raise OSError(f"{words_error}. The snippets were changed all the same, and "
                          "couldn't be put back.") from e
        raise
