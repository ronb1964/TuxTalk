"""The text side of Type at pause: lining each phrase up with the next one
and deciding the mark at the pause between them.

The engine punctuates each chunk as if it were a whole sentence, so nearly
every pause would end in a period. The chunk's final mark is held back
instead, and once the next chunk arrives the engine hears both together:
with the words (and the voice) on both sides of the pause, the mark it puts
at the seam is a real verdict. Only that mark is taken from the joint text;
the words typed come from each chunk's own transcription, except for a
phrase too short to be heard alone (words_in_context).

The pause detector that cuts the chunks is chunking.py's.
"""
import difflib
import re

from .logger import setup_logger

logger = setup_logger(__name__)

_WORD = re.compile(r"\w+(?:['’-]\w+)*")
_MARKS = ".?!…,;:"


def _seam(prev_raw: str, joint_raw: str):
    """(the words of *joint_raw*, the index of *prev_raw*'s last word among
    them), or None when the words can't be lined up or nothing follows them."""
    prev = [w.casefold() for w in _WORD.findall(prev_raw)]
    joint = list(_WORD.finditer(joint_raw))
    if not prev or not joint:
        return None
    words = [m.group().casefold() for m in joint]
    last = len(prev) - 1
    matcher = difflib.SequenceMatcher(None, prev, words, autojunk=False)
    for a, b, size in matcher.get_matching_blocks():
        if size and a <= last < a + size:
            j = b + (last - a)
            break
    else:
        return None
    if j == len(joint) - 1:
        return None
    return joint, j


def boundary_mark(prev_raw: str, joint_raw: str):
    """The punctuation the engine put after the last word of *prev_raw* when it
    transcribed that chunk together with the next one (*joint_raw*): ".", "?",
    ",", ... or "" for none. None when the words can't be lined up, or when
    nothing follows them (so there is no context to judge by)."""
    seam = _seam(prev_raw, joint_raw)
    if seam is None:
        return None
    joint, j = seam
    gap = joint_raw[joint[j].end():joint[j + 1].start()]
    marks = [c for c in gap if c in _MARKS]
    return marks[0] if marks else ""


def words_in_context(prev_raw: str, joint_raw: str):
    """What *joint_raw* says after the words of *prev_raw*: the next chunk as
    the engine heard it with the one before. None when they can't be lined up.

    A very short chunk alone is barely a word or two, and Parakeet sometimes
    invents a sentence for it: "it" came out as "It's a little bit
    more than a little bit.", while the joint pass heard "... run out it"."""
    seam = _seam(prev_raw, joint_raw)
    if seam is None:
        return None
    joint, j = seam
    return joint_raw[joint[j + 1].start():].strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text))


def _last_word(text: str) -> str:
    words = re.findall(r"\w+", text.casefold())
    return words[-1] if words else ""


def hold_back_boundary(text: str, raw: str):
    """Split a middle chunk's text into what to type now and the final mark to
    hold back: (text, mark), or (text, None) when nothing is held back.

    The engine heard the chunk alone, so its final period or question mark is
    a guess; the next chunk decides it (type_at_pause.resolve_boundary). The trailing
    space waits too, so whatever comes next lands right after the last word."""
    core = text.rstrip(" ")
    if not core:
        return text, None
    m = re.search(r"[.?!…]+$", core)
    mark = m.group() if m else ""
    if mark:
        core = core[:m.start()].rstrip()
        # A mark the speaker dictated ("Punkt") is theirs, not the engine's
        # guess: the word before it then isn't the raw text's last word.
        if not core or _last_word(core) != _last_word(raw):
            return text, None
    elif not core[-1].isalnum():
        return text, None   # ends in a line break, a quote, a bracket...
    return core, mark


def engine_period(text: str, raw: str) -> bool:
    """Whether *text* ends with a period the engine put after the last word
    it heard (*raw*'s), which a question mark may replace: not "...", an
    abbreviation ("p.m.") or a period the speaker dictated."""
    core = text.rstrip()
    return (re.search(r"(?<![.\w])[\w'’-]*\w\.$", core) is not None
            and _last_word(core) == _last_word(raw))


def carry_question(text: str) -> str:
    """End a phrase that finishes an open question with "?" instead of the
    period. Only a phrase holding one sentence: with a second one in it, the
    question has already ended somewhere the engine couldn't mark."""
    m = re.fullmatch(r"([^.?!]*\w)\.(\s*)", text)
    if not m:
        return text
    logger.info("The question goes on across the pause: it ends with ?")
    return m.group(1) + "?" + m.group(2)
