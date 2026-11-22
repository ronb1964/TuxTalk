"""Your Words: names and terms TuxTalk should spell the user's way.

Set in Preferences > Words and kept in custom_words.txt, one per line, next
to the snippets file; this module reads and writes it. Every engine uses
them, before and after it listens:

- Whisper models are told about them before they listen: faster-whisper's
  `hotwords` and whisper.cpp's `prompt` (word_spelling.prompt). That steers
  Whisper towards the user's spelling of a name it would otherwise guess at.
- Parakeet leans toward them while it decodes, on the processor
  (parakeet_bias.py) and on the graphics card (TuxTalk's patch to
  whisper.cpp, parakeet_gpu_worker.py): a word is written whole where the
  audio fits it.
- Every engine then gets word_spelling's spelling pass.
"""
import os

from . import config
from .logger import setup_logger
from .word_spelling import is_word_like

logger = setup_logger(__name__)

FILE_NAME = "custom_words.txt"


def path():
    """custom_words.txt, beside custom_commands.toml (follows DEV_MODE and Flatpak)."""
    return os.path.join(os.path.dirname(config.CUSTOM_COMMANDS_PATH), FILE_NAME)


def _is_comment(line):
    """'# note' or a bare '#'. '#TuxTalk' is a word (a hashtag), not a comment."""
    return line == "#" or line.startswith("# ")


def parse(text):
    """The words in *text*: one per line, blanks and comments skipped,
    repeats (ignoring case) dropped, order kept."""
    words, seen = [], set()
    for line in text.splitlines():
        word = line.strip()
        if not word or _is_comment(word) or word.casefold() in seen:
            continue
        seen.add(word.casefold())
        words.append(word)
    return words


def load(strict=False):
    """The user's words; [] when there is no file. A file that can't be read
    gives [] too, or raises with *strict*: Preferences must not open it empty
    and save that over the user's words."""
    try:
        with open(path(), encoding="utf-8") as f:
            return parse(f.read())
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as e:
        if strict:
            raise
        logger.warning(f"Could not read your words: {e}")
        return []


# The header save() writes. A file without it predates the Words tab, whose
# first opening moves the user's word fixes in (vocabulary.adopt_fixes).
HEADER = "# TuxTalk: your words, one per line (Preferences > Words)"


def save(words):
    """Write *words* to custom_words.txt, under a header comment."""
    config.write_text_atomic(path(), "\n".join([HEADER] + list(words)) + "\n")


def has_header():
    """Whether custom_words.txt starts with today's HEADER."""
    try:
        with open(path(), encoding="utf-8") as f:
            return f.readline().rstrip("\n") == HEADER
    except (OSError, UnicodeDecodeError):
        return False


def learn_fix(word, previous=None, common=frozenset()):
    """Fix a Word just saved a fix to *word* (it was *previous*, if it was
    re-fixed): add *word* to Your Words and drop *previous*, so Whisper isn't
    told the old spelling. *common* words are left out. A words file that
    can't be read is left alone. Returns whether the list changed."""
    try:
        words = load(strict=True)
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Your words couldn't be read, so the fix wasn't added to them: {e}")
        return False
    changed = False
    if previous and previous != word and previous in words:
        words.remove(previous)
        changed = True
    if (is_word_like(word) and word.strip().casefold() not in common
            and word.strip().casefold() not in {w.casefold() for w in words}):
        words.append(word.strip())
        changed = True
    if changed:
        save(words)
    return changed

