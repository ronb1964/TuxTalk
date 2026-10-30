"""Select and move by voice (TuxTalk Labs): "select the last sentence",
"go to the end", "unselect".

Pure logic, like undo.py: which dictations are commands, and the keys each
one presses. app.py presses them through the output backend, so the host
(the virtual keyboard) and the Flatpak (libei) do the same.

A dictation is a command only when the WHOLE of it is one of the phrases
here, so "I want to select the last sentence" is typed as text. Plain F8
never rewrites anything: rewording stays on the voice-edit key. See
docs/superpowers/specs/2026-10-14-select-and-move-by-voice-design.md.

Keys are named, not numbered ("CTRL", "LEFT", "A"); the backend turns them
into key codes, the letter A for the active keyboard layout.
"""
import re
from dataclasses import dataclass

from .undo import calculate_undo_length

C, S = "CTRL", "SHIFT"
MAX_COUNT = 10

_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_N = r"(?:(?P<n>\d+|" + "|".join(_NUMBER_WORDS) + r")\s+)?"


@dataclass(frozen=True)
class Command:
    name: str               # for the log: "go back 3 words"
    said_unit: str = ""     # "all", "word", "sentence" or "paragraph": what I just said
    count: int = 1
    keys: tuple = ()        # pressed count times
    first: tuple = ()       # pressed once before (Home, for "select the line")
    # Word and letter moves: ("word" or "char", -1 back or +1 forward, select?).
    # In text TuxTalk typed, these are counted exactly (plan()); elsewhere the
    # keys above are the app's own Ctrl+arrows.
    step: tuple = ()


def _norm(text: str) -> str:
    """Lower case, letters, digits and single spaces; no "please"."""
    s = re.sub(r"[^\w\s']", " ", (text or "").lower())
    s = " ".join(s.split())
    s = re.sub(r"^please\s+|\s+please$", "", s)
    return s


# (pattern, builder). Each pattern matches the whole normalized dictation; the
# builder gets the count and returns (said_unit, keys, first).
_DOC = r"(?:\s+of\s+(?:the\s+)?(?:document|page|text))?"
# "the line", "this line", "that line", "the current line": people say all of them.
_LINE = r"(?:(?:the|this|that) )?(?:current )?line"
_RULES = [
    # What I just said: exact, from the text TuxTalk typed last.
    (r"select (?:that|it|what i (?:just )?said|the last dictation)", lambda n: ("all", (), ())),
    (rf"select (?:the )?last {_N}(?P<u>word|sentence|paragraph)s?", lambda n, u: (u, (), ())),
    # Around the cursor.
    (r"select (?:all|everything)", lambda n: ("", (((C,), "A"),), ())),
    (rf"select {_LINE}", lambda n: ("", (((S,), "END"),), (((), "HOME"),))),
    (rf"select (?:the )?next {_N}words?",
     lambda n: ("", (((C, S), "RIGHT"),), (), ("word", 1, True))),
    (rf"select (?:the )?previous {_N}words?",
     lambda n: ("", (((C, S), "LEFT"),), (), ("word", -1, True))),
    (rf"select (?:the )?next {_N}lines?", lambda n: ("", (((S,), "DOWN"),), ())),
    (rf"select (?:the )?previous {_N}lines?", lambda n: ("", (((S,), "UP"),), ())),
    (rf"select to (?:the )?end of {_LINE}", lambda n: ("", (((S,), "END"),), ())),
    (rf"select to (?:the )?(?:start|beginning) of {_LINE}", lambda n: ("", (((S,), "HOME"),), ())),
    (rf"select to (?:the )?(?:end|bottom){_DOC}", lambda n: ("", (((C, S), "END"),), ())),
    (rf"select to (?:the )?(?:start|beginning|top){_DOC}", lambda n: ("", (((C, S), "HOME"),), ())),
    # Moving.
    (rf"go to (?:the )?end of {_LINE}", lambda n: ("", (((), "END"),), ())),
    (rf"go to (?:the )?(?:start|beginning) of {_LINE}", lambda n: ("", (((), "HOME"),), ())),
    (rf"go to (?:the )?(?:end|bottom){_DOC}", lambda n: ("", (((C,), "END"),), ())),
    (rf"go to (?:the )?(?:start|beginning|top){_DOC}", lambda n: ("", (((C,), "HOME"),), ())),
    (rf"go (?:back|left) {_N}words?|previous word",
     lambda n: ("", (((C,), "LEFT"),), (), ("word", -1, False))),
    (rf"go (?:forward|right) {_N}words?|next word",
     lambda n: ("", (((C,), "RIGHT"),), (), ("word", 1, False))),
    (rf"go (?:back|left) {_N}(?:characters?|letters?)",
     lambda n: ("", (((), "LEFT"),), (), ("char", -1, False))),
    (rf"go (?:forward|right) {_N}(?:characters?|letters?)",
     lambda n: ("", (((), "RIGHT"),), (), ("char", 1, False))),
    (rf"go up {_N}lines?", lambda n: ("", (((), "UP"),), ())),
    (rf"go down {_N}lines?", lambda n: ("", (((), "DOWN"),), ())),
    # Unselect: Right collapses a selection to its end.
    (r"(?:unselect|deselect)(?: (?:that|it|all|everything))?|clear (?:the )?selection",
     lambda n: ("", (((), "RIGHT"),), ())),
]
_COMPILED = [(re.compile(rf"^(?:{pattern})$"), build) for pattern, build in _RULES]


def parse(text: str):
    """The Command *text* is, or None when it's dictation to type."""
    said = _norm(text)
    if not said:
        return None
    for pattern, build in _COMPILED:
        m = pattern.fullmatch(said)
        if not m:
            continue
        groups = m.groupdict()
        raw = groups.get("n")
        count = 1 if raw is None else (int(raw) if raw.isdigit() else _NUMBER_WORDS[raw])
        if not 1 <= count <= MAX_COUNT:
            return None
        args = (count, groups["u"]) if "u" in groups and groups["u"] else (count,)
        unit, keys, first, *step = build(*args)
        name = said if raw is None else said[:m.start("n")] + str(count) + said[m.end("n"):]
        return Command(name=name, said_unit=unit, count=count, keys=keys, first=first,
                       step=step[0] if step else ())
    return None


@dataclass(frozen=True)
class Plan:
    chords: list
    # The known text left and right of the cursor afterwards, inside what
    # TuxTalk typed, or None when the cursor is somewhere unknown (or text is
    # selected) and the next word move must use the app's own keys.
    left: str = None
    right: str = ""


def _words_back(left: str, n: int) -> int:
    """Letters from the cursor back to the start of the n-th word before it.
    A word is everything between spaces, so "end." is one word: apps that
    stop at the period (KWrite's Ctrl+Left) would go one word short."""
    i = len(left)
    for _ in range(n):
        while i > 0 and left[i - 1].isspace():
            i -= 1
        if i == 0:
            break
        while i > 0 and not left[i - 1].isspace():
            i -= 1
    return len(left) - i


def _words_forward(right: str, n: int) -> int:
    """Letters from the cursor to the start of the n-th word after it (or the
    end of the known text)."""
    i = 0
    for _ in range(n):
        while i < len(right) and not right[i].isspace():
            i += 1
        while i < len(right) and right[i].isspace():
            i += 1
        if i >= len(right):
            break
    return i


def plan(command: Command, left=None, right: str = ""):
    """The keys for *command*, given the known text *left* and *right* of the
    cursor (None when unknown), and that text afterwards. None when the
    command needs the last dictation and there is none."""
    if command.said_unit:
        if left is None:
            return None
        # From wherever an earlier move left the cursor, back to the end first.
        to_end = [((), "RIGHT")] * len(right)
        selected = chords(command, left + right)
        return None if selected is None else Plan(to_end + selected)
    if command.step and left is not None:
        unit, direction, select = command.step
        if direction < 0:
            n = (_words_back(left, command.count) if unit == "word"
                 else min(command.count, len(left)))
        else:
            n = (_words_forward(right, command.count) if unit == "word"
                 else min(command.count, len(right)))
        mods = (S,) if select else ()
        keys = [(mods, "LEFT" if direction < 0 else "RIGHT")] * n
        if select:
            return Plan(keys)
        if direction < 0:
            return Plan(keys, left[:len(left) - n], left[len(left) - n:] + right)
        return Plan(keys, left + right[:n], right[n:])
    return Plan(list(command.first) + list(command.keys) * command.count)


# Marks that end a word in a sentence but aren't part of it.
_WORD_END_MARKS = ".,;:!?\u2026\"'\u201d\u2019)]"


def chords(command: Command, last_text: str):
    """The keys to press, in order, as [(modifiers, key)]; None when the
    command needs the last dictation and there is none."""
    if not command.said_unit:
        return list(command.first) + list(command.keys) * command.count
    if not last_text or not last_text.strip():
        return None
    if command.said_unit == "all":
        span = last_text
    else:
        span = last_text[-calculate_undo_length(last_text, command.said_unit, command.count):]
    # The cursor sits after the span: step over its trailing spaces without
    # selecting them, then select back over the words. Its leading space (auto-
    # space before a dictation) stays out of the selection.
    core = span.rstrip()
    if command.said_unit == "word":
        # A word without the sentence's mark after it: "select the last word"
        # is for replacing or fixing the word, and the period stays. A
        # sentence keeps its mark, it's part of the sentence.
        core = core.rstrip(_WORD_END_MARKS)
    trailing = len(span) - len(core)
    words = len(core.strip())
    if not words:
        return None
    return [((), "LEFT")] * trailing + [((S,), "LEFT")] * words
