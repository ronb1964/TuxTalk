"""Your Words, matched in the text: the user's spelling of a name or term put
back where the engine split, hyphenated or misspelt it, and the checks that
go with it. The list itself, custom_words.txt, is custom_words.py's.

Every engine gets the spelling pass (fix_spelling). It only fixes a word
that came out split up, hyphenated, with the wrong capitals or in a spelling
that sounds the same ("tux talk", "Tux-Talk", "Tuck's talk" -> "TuxTalk";
_SOUND_FOLDS lists the spellings). A word heard as something else entirely
("bamboo studio") is Fix a Word's job: guessing at words that only sound a
bit like it would rewrite ordinary speech.
"""
import re

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


def sound_key(letters):
    """*letters* (from _letters) folded so that sound-alike spellings match:
    x/ks/cks/cs, ck/c/k, ph/f, and doubled letters counted once."""
    for a, b in _SOUND_FOLDS:
        letters = letters.replace(a, b)
    return "".join(c for i, c in enumerate(letters) if i == 0 or c != letters[i - 1])


_APOSTROPHES = "'’"
_NO_APOSTROPHES = str.maketrans("", "", _APOSTROPHES)


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
            sounds.setdefault(sound_key(key), word)
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
            if any(text[a[1]:b[0]].strip(" ") for a, b in zip(group, group[1:], strict=False)):
                continue
            joined = "".join(_letters(t[3]) for t in group)
            word = targets.get(joined)
            if word is not None:
                if not all(_joinable(t[3], word, hyphens) for t in group):
                    continue
            else:
                # Same sound, other letters. The apostrophe of "Tuck's" belongs
                # to the mishearing, so it may sit inside the match.
                word = sounds.get(sound_key(joined)) if len(joined) >= MIN_SOUND_LETTERS else None
                if word is None or not all(
                        _joinable(t[3].translate(_NO_APOSTROPHES), word, hyphens)
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
