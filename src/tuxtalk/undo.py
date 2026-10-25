"""
Undo voice-command parsing and character-length calculation.

Pure logic — no keyboard, GTK, or audio dependencies. Importable from tests
without dragging in CUDA/Whisper/etc. The keystroke-sending side
(`send_backspaces`, `send_select_all_delete`) lives in keyboard_output.py.
"""

import re

# "Everything" phrases clear the entire input field (Ctrl+A + Backspace) and
# don't require any tracked dictation. Exact-match strings.
EVERYTHING_PATTERNS = frozenset({
    'undo everything', 'undo all',
    'delete everything', 'delete all',
    'clear everything', 'clear all',
})

# Spoken number words → integers. Used for counted undo like
# "delete last three sentences". "a"/"an" map to 1 because Whisper often
# transcribes "delete a sentence" instead of "delete one sentence".
NUMBER_WORDS = {
    'a': 1, 'an': 1,
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
}

# Counted/uncounted unit-undo regex. Matches:
#   "delete last word"            → ('word', 1)
#   "undo last two sentences"     → ('sentence', 2)
#   "remove last 3 paragraphs"    → ('paragraph', 3)
#   "delete the last five words"  → ('word', 5)
_UNDO_UNIT_RE = re.compile(
    r"^(?:undo|delete|remove)\s+(?:the\s+)?last\s+"
    r"(?:(\d+)\s+|(" + "|".join(re.escape(w) for w in NUMBER_WORDS) + r")\s+)?"
    r"(word|sentence|paragraph)s?$",
    re.IGNORECASE,
)


def detect_undo_command(raw_text: str) -> tuple[str, int] | None:
    """
    Check if the raw transcription is an undo command.

    Returns a (undo_type, count) tuple or None.
    - undo_type: 'word', 'sentence', 'paragraph', or 'everything'
    - count: how many units to remove (always 1 for 'everything')
    """
    normalized = re.sub(r'[^\w\s]', '', raw_text.lower()).strip()
    normalized = ' '.join(normalized.split())

    if normalized in EVERYTHING_PATTERNS:
        return ('everything', 1)

    m = _UNDO_UNIT_RE.match(normalized)
    if m:
        digit, word, unit = m.groups()
        if digit:
            count = int(digit)
        elif word:
            count = NUMBER_WORDS[word.lower()]
        else:
            count = 1
        return (unit.lower(), count)

    return None


def single_unit_length(text: str, undo_type: str) -> int:
    """
    Length (in characters) of the LAST word/sentence/paragraph in `text`.

    Returns len(text) when no internal boundary is found (i.e. the whole
    remaining buffer is the last unit). The 'everything' type is handled
    by the caller — it's not a per-unit measurement.
    """
    if not text:
        return 0

    text_to_analyze = text.rstrip()

    if undo_type == 'word':
        # Any whitespace ends a word — the buffer stores line breaks as '\n'
        # (one char = one backspace), so newlines/tabs are boundaries too.
        last_ws = max(
            text_to_analyze.rfind(' '),
            text_to_analyze.rfind('\n'),
            text_to_analyze.rfind('\t'),
        )
        if last_ws == -1:
            return len(text)
        return len(text) - last_ws - 1

    if undo_type == 'sentence':
        # Skip any trailing sentence-ending punctuation, and the closing quote
        # or bracket after it, so we find the PREVIOUS sentence end, not the
        # current one.
        text_stripped = text_to_analyze.rstrip('.?!… ' + _SENTENCE_CLOSERS)
        for i in range(len(text_stripped) - 1, -1, -1):
            if text_stripped[i] in '.?!…' and _is_sentence_end(text_stripped, i):
                # Delete from just past the punctuation — and past any closing
                # quote or bracket that belongs with it, and the space or line
                # break after it — to the end. That space stays, as it does for
                # a word: the next dictation brings only a space of its own at
                # its END, so "message." would run on into "message.The".
                end = _end_of_sentence(text_stripped, i)
                while end < len(text_stripped) and text_stripped[end] in ' \t\n':
                    end += 1
                return len(text) - end
        return len(text)

    if undo_type == 'paragraph':
        # The buffer stores line breaks as '\n', like the word case above.
        newline_pos = text_to_analyze.rfind('\n')
        if newline_pos != -1:
            return len(text) - newline_pos - 1
        return len(text)

    return 0


def _is_sentence_end(text: str, i: int) -> bool:
    """True if the punctuation at index *i* really ends a sentence.

    A decimal or an abbreviation is not a boundary: taking one for it deletes
    far too little ("I paid $19.99 for the book." would leave "I paid $19.",
    which still looks like a finished sentence and is easy to miss).
    """
    ch = text[i]
    prev = text[i - 1] if i > 0 else ''
    prev2 = text[i - 2] if i > 1 else ''
    immediate = text[i + 1] if i + 1 < len(text) else ''

    # Decimals, prices and version numbers: 19.99, 2.5
    if ch == '.' and prev.isdigit() and immediate.isdigit():
        return False

    # Step over any closers that belong to this sentence. normalize_text moves
    # sentence punctuation INSIDE a closing smart quote, so `."` is a shape this
    # app produces itself; missing the boundary there would delete the previous
    # sentence too, often the whole text.
    end = _end_of_sentence(text, i)

    if ch in '?!…':
        return True

    nxt = text[end] if end < len(text) else ''

    # A sentence end is followed by whitespace, or by nothing at all.
    if nxt and not nxt.isspace():
        return False

    # Single-letter abbreviations — U.S., a.m., e.g. Their period can also be
    # doing double duty as the full stop, which a following capital reveals.
    if prev.isalpha() and (prev2 == '.' or not prev2.isalnum()):
        following = text[end:].lstrip()
        return bool(following[:1].isupper())

    return True


# Closing marks that belong to the sentence they follow, not to the next one.
_SENTENCE_CLOSERS = '"\'”’)]}'


def _end_of_sentence(text: str, i: int) -> int:
    """Index just past the punctuation at *i* and any closing quotes/brackets."""
    j = i + 1
    while j < len(text) and text[j] in _SENTENCE_CLOSERS:
        j += 1
    return j


def calculate_undo_length(text: str, undo_type: str, count: int = 1) -> int:
    """
    Calculate how many characters to delete based on undo type and count.

    For counted undos ("delete last 3 sentences"), iterates the single-unit
    logic N times, accumulating the cut length. Clamps to what's available —
    "delete last 50 words" on a 3-word buffer just removes those 3 words.

    Args:
        text: The last inserted text
        undo_type: 'word', 'sentence', 'paragraph', or 'everything'
        count: how many units to remove (ignored for 'everything')

    Returns:
        Number of characters to delete (backspaces to send)
    """
    if not text or count < 1:
        return 0

    if undo_type == 'everything':
        return len(text)

    total = 0
    remaining = text
    completed = 0
    while completed < count and remaining:
        single = single_unit_length(remaining, undo_type)
        if single > 0:
            total += single
            remaining = remaining[:len(remaining) - single]
            completed += 1
            continue
        # Nothing measurable is left to remove; stop rather than loop forever.
        break
    return total
