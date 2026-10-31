"""Your Words: names and terms TuxTalk should spell the user's way.

Set in Preferences > Words and kept in custom_words.txt, one per line, next
to the snippets file. Every engine uses them, before and after it listens:

- Whisper models are told about them before they listen: faster-whisper's
  `hotwords` and whisper.cpp's `prompt`. That steers Whisper towards the
  user's spelling of a name it would otherwise guess at.
- Parakeet leans toward them while it decodes, on the processor
  (parakeet_bias.py) and on the graphics card (TuxTalk's patch to
  whisper.cpp, parakeet_gpu_worker.py): a word is written whole where the
  audio fits it.
- Every engine then gets a spelling pass (fix_spelling). It only fixes a
  word that came out split up, hyphenated, with the wrong capitals or in a
  spelling that sounds the same ("tux talk", "Tux-Talk", "Tuck's talk" ->
  "TuxTalk"; _SOUND_FOLDS lists the spellings). A word heard as something
  else entirely ("bamboo studio") is Fix a Word's job: guessing at words
  that only sound a bit like it would rewrite ordinary speech.
"""
import os
import re

from . import config
from .logger import setup_logger

logger = setup_logger(__name__)

FILE_NAME = "custom_words.txt"
# Whisper keeps about 220 tokens of prompt (half its 448-token context) and
# drops the rest from the front. ~600 characters stays well inside that.
PROMPT_MAX_CHARS = 600
MIN_LETTERS = 4         # shorter words join up by accident ("a i" -> AI)
MIN_SOUND_LETTERS = 6   # sound-alike matching only for longer words ("Mack's" is not Max)
MAX_PIECES = 4          # a word split into at most this many pieces
ECHO_MIN_WORDS = 3      # Whisper reading the list back, not a short real phrase

_WORD_RE = re.compile(r"\S+")
# A token's leading punctuation, core and trailing punctuation. Every part is
# optional, so it always matches.
_EDGE_RE = re.compile(r"^([^\w]*)(.*?)([^\w]*)$", re.DOTALL)


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


# What Fix a Word may add: a word or a short name, not an email address, a
# web address, a snippet-style block of text or a literal ("quoted") one.
_NOT_A_WORD = ("@", "://", "\\", "\n", '"')
MAX_WORD_PIECES = 3
MAX_WORD_CHARS = 40


def is_word_like(text):
    """Whether *text* is short and plain enough to be one of Your Words."""
    text = text.strip()
    if not text or len(text) > MAX_WORD_CHARS or any(m in text for m in _NOT_A_WORD):
        return False
    return len(text.split()) <= MAX_WORD_PIECES and len(_letters(text)) >= 2


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


def prompt(words):
    """The words as a Whisper prompt ("TuxTalk, BambuStudio."), or None."""
    text = ""
    for word in words:
        piece = word if not text else f", {word}"
        if len(text) + len(piece) + 1 > PROMPT_MAX_CHARS:
            break
        text += piece
    return f"{text}." if text else None


def _letters(text):
    """*text* folded to lower case, letters and digits only, for comparing words."""
    return "".join(c for c in text.casefold() if c.isalnum())


# Spellings that sound the same, in the order they're folded. Parakeet hears
# the x of TuxTalk as "ks" and writes "Tuck's talk" or "Tuckstalk".
_SOUND_FOLDS = (("ph", "f"), ("cks", "x"), ("ks", "x"), ("cs", "x"), ("ck", "k"), ("c", "k"))


def _sound(letters):
    """*letters* (from _letters) folded so that sound-alike spellings match:
    x/ks/cks/cs, ck/c/k, ph/f, and doubled letters counted once."""
    for a, b in _SOUND_FOLDS:
        letters = letters.replace(a, b)
    return "".join(c for i, c in enumerate(letters) if i == 0 or c != letters[i - 1])


_APOSTROPHES = "'\u2019"


def _joinable(core, word, hyphens=True):
    """Whether *core* (one heard piece) may be part of *word*. Hyphens may
    split a word ("Tux-Talk"); other punctuation inside a piece only if the
    word has it too. An apostrophe makes "she'll" a contraction and
    "William's" a possessive, never the word Shell or Williams, and a dot
    makes "tux.talk" something else again."""
    word_has_apostrophe = any(a in word for a in _APOSTROPHES)
    return all(c.isalnum() or (hyphens and c == "-") or c in word
               or (c in _APOSTROPHES and word_has_apostrophe)
               for c in core)


def _unusual_capitals(word):
    """TuxTalk, iPhone, GitHub: capitals a plain 'capitalise the first letter'
    would never produce, so a single word may be recapitalised to match."""
    return any(c.isupper() for c in word[1:]) or (word[:1].islower() and word != word.lower())


def fix_spelling(text, words, hyphens=True):
    """*text* with any of *words* that came out split up, hyphenated or with
    the wrong capitals written the user's way.

    Pieces join only across plain spaces: a tab or line break there was
    dictated. *hyphens* False once the text has been tidied up, where a
    hyphen came from the user saying "dash", not from the engine."""
    targets, sounds = {}, {}
    for word in words:
        key = _letters(word)
        if len(key) >= MIN_LETTERS:
            targets.setdefault(key, word)
        if len(key) >= MIN_SOUND_LETTERS:
            sounds.setdefault(_sound(key), word)
    if not targets or not text:
        return text

    tokens = []   # (start, end, lead, core, trail) for each run of non-spaces
    for m in _WORD_RE.finditer(text):
        lead, core, trail = _EDGE_RE.match(m.group()).groups()
        tokens.append((m.start(), m.end(), lead, core, trail))

    out, pos, i = [], 0, 0
    while i < len(tokens):
        replaced = False
        for n in range(min(MAX_PIECES, len(tokens) - i), 0, -1):
            group = tokens[i:i + n]
            # Pieces of one word: no punctuation between them, only around them.
            if any(t[4] for t in group[:-1]) or any(t[2] for t in group[1:]):
                continue
            if any(text[a[1]:b[0]].strip(" ") for a, b in zip(group, group[1:])):
                continue
            joined = "".join(_letters(t[3]) for t in group)
            word = targets.get(joined)
            if word is not None:
                if not all(_joinable(t[3], word, hyphens) for t in group):
                    continue
            else:
                # Same sound, other letters. The apostrophe of "Tuck's" belongs
                # to the mishearing, so it may sit inside the match.
                word = sounds.get(_sound(joined)) if len(joined) >= MIN_SOUND_LETTERS else None
                if word is None or not all(
                        _joinable(t[3].replace("'", "").replace("\u2019", ""), word, hyphens)
                        for t in group):
                    continue
            core_start = group[0][0] + len(group[0][2])
            core_end = group[-1][1] - len(group[-1][4])
            heard = text[core_start:core_end]
            if heard == word:
                break
            if n == 1 and heard.casefold() == word.casefold() and not _unusual_capitals(word):
                break       # only capitals differ, and the word has none unusual
            out.append(text[pos:core_start])
            out.append(word)
            pos = core_end
            i += n
            replaced = True
            break
        if not replaced:
            i += 1
    out.append(text[pos:])
    return "".join(out)


def is_echo(text, words):
    """Whether *text* is just the word list read back. Given a prompt, Whisper
    sometimes 'hears' it in silence or noise.

    Only whole entries count, in the list's own order, at least
    ECHO_MIN_WORDS of them (of 4+ letters) and nothing else. Real speech made
    of listed words ("the new bank" with "New York" listed, or the user's own
    terms in another order) is never dropped."""
    order = {}
    for i, word in enumerate(words):
        key = _letters(word)
        if key:
            order.setdefault(key, i)
    heard = [h for h in (_letters(t) for t in _WORD_RE.findall(text)) if h]
    # Short entries ("AI") are in the prompt too, so they may be read back;
    # they just don't count towards ECHO_MIN_WORDS.
    matched, i, last = 0, 0, -1
    while i < len(heard):
        for n in range(min(MAX_PIECES, len(heard) - i), 0, -1):
            key = "".join(heard[i:i + n])
            index = order.get(key)
            if index is not None and index > last:
                matched += len(key) >= MIN_LETTERS
                last, i = index, i + n
                break
        else:
            return False
    return matched >= ECHO_MIN_WORDS


def starts_with_word(text, words):
    """Whether *text* begins with one of *words* that has capitals (possibly
    possessive: "TuxTalk's"), so its first letter must not be lowercased."""
    keys = {_letters(w) for w in words if any(c.isupper() for c in w)}
    tokens = _WORD_RE.findall(text)[:MAX_PIECES]
    joined = ""
    for token in tokens:
        # Punctuation after a possessive ("TuxTalk's,") would hide its "'s".
        piece = token.rstrip(".,;:!?…\"”)]}")
        if piece.endswith(tuple(a + "s" for a in _APOSTROPHES)):
            piece = piece[:-2]
        joined += _letters(piece)
        if joined in keys:
            return True
    return False
