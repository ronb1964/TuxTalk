"""Select and move by voice (TuxTalk Labs): "select the last sentence",
"go to the end", "unselect".

Pure logic, like undo.py: which dictations are commands, and the keys each
one presses. voice_commands.handle_navigation presses them through the
output backend, so the host (the virtual keyboard) and the Flatpak (libei)
do the same.

A dictation is a command only when the WHOLE of it is one of the phrases
here, so "I want to select the last sentence" is typed as text. With voice
commands in German ("den letzten Satz markieren", "geh zum Zeilenende")
the German phrases count too. These only
select and move: changing the words is voice editing's (Shift with the
dictation key, or a dictation that starts with "edit that").

Keys are named, not numbered ("CTRL", "LEFT", "A"); the backend turns them
into key codes, the letter A for the active keyboard layout.
"""
import re
from dataclasses import dataclass

from .undo import GERMAN_NUMBER_WORDS, GERMAN_UNITS, NUMBER_WORDS, calculate_undo_length

C, S = "CTRL", "SHIFT"
MAX_COUNT = 10

_N = r"(?:(?P<n>\d+|" + "|".join(NUMBER_WORDS) + r")\s+)?"
_GERMAN_N = r"(?:(?P<n>\d+|" + "|".join(GERMAN_NUMBER_WORDS) + r")\s+)?"
_COUNTS = {**NUMBER_WORDS, **GERMAN_NUMBER_WORDS}


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


def normalize(text: str) -> str:
    """Lower case, letters, digits and single spaces; no "please"."""
    s = re.sub(r"[^\w\s']", " ", (text or "").lower())
    s = " ".join(s.split())
    s = re.sub(r"^please\s+|\s+please$", "", s)
    return s


# A German command's verb said first, as the infinitive it is said last as:
# "markiere den letzten Satz" is read as "den letzten satz markieren", so
# each German rule is written once, the verb last.
_GERMAN_VERBS = {"markier": "markieren", "markiere": "markieren", "markieren": "markieren",
                 "wähle": "markieren", "geh": "gehen", "gehe": "gehen", "gehen": "gehen",
                 "spring": "gehen", "springe": "gehen", "springen": "gehen"}


def _german_order(said: str) -> str:
    """*said* (normalized) with its German verb last and no "bitte"."""
    words = [w for w in said.split() if w != "bitte"]
    if len(words) > 1 and words[0] in _GERMAN_VERBS:
        words = [*words[1:], _GERMAN_VERBS[words[0]]]
    elif words and words[-1] in _GERMAN_VERBS:
        words[-1] = _GERMAN_VERBS[words[-1]]
    return " ".join(words)


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

# German, the verb last (_german_order); a moving command may end in "gehen".
_WORDS = r"(?:wort|wörter|worte)"
_GO = r"(?: gehen)?"
_GERMAN_LINE = r"(?:(?:die|diese) )?(?:aktuelle )?zeile"
_LINE_END = r"(?:zeilenende|ende der zeile)"
_LINE_START = r"(?:zeilenanfang|anfang der zeile)"
_TEXT_END = r"(?:ende|textende|ende des textes|ende des dokuments)"
_TEXT_START = r"(?:anfang|textanfang|anfang des textes|anfang des dokuments)"
_GERMAN_RULES = [
    (r"(?:das|es|das gesagte) markieren", lambda n: ("all", (), ())),
    (rf"(?:(?:den|die|das) )?letzte[nsr]? {_GERMAN_N}(?P<u>{'|'.join(GERMAN_UNITS)}) markieren",
     lambda n, u: (u, (), ())),
    (r"alles markieren", lambda n: ("", (((C,), "A"),), ())),
    (rf"{_GERMAN_LINE} markieren", lambda n: ("", (((S,), "END"),), (((), "HOME"),))),
    (rf"(?:(?:das|die) )?nächste[n]? {_GERMAN_N}{_WORDS} markieren",
     lambda n: ("", (((C, S), "RIGHT"),), (), ("word", 1, True))),
    (rf"(?:(?:das|die) )?vorherige[n]? {_GERMAN_N}{_WORDS} markieren",
     lambda n: ("", (((C, S), "LEFT"),), (), ("word", -1, True))),
    (rf"(?:die )?nächste[n]? {_GERMAN_N}zeilen? markieren",
     lambda n: ("", (((S,), "DOWN"),), ())),
    (rf"(?:die )?vorherige[n]? {_GERMAN_N}zeilen? markieren",
     lambda n: ("", (((S,), "UP"),), ())),
    (rf"bis zum {_LINE_END} markieren", lambda n: ("", (((S,), "END"),), ())),
    (rf"bis zum {_LINE_START} markieren", lambda n: ("", (((S,), "HOME"),), ())),
    (rf"bis zum {_TEXT_END} markieren", lambda n: ("", (((C, S), "END"),), ())),
    (rf"bis zum {_TEXT_START} markieren", lambda n: ("", (((C, S), "HOME"),), ())),
    (rf"(?:zum|ans) {_LINE_END}{_GO}", lambda n: ("", (((), "END"),), ())),
    (rf"zum {_LINE_START}{_GO}", lambda n: ("", (((), "HOME"),), ())),
    (rf"(?:(?:zum|ans) {_TEXT_END}|nach ganz unten){_GO}", lambda n: ("", (((C,), "END"),), ())),
    (rf"(?:zum {_TEXT_START}|nach ganz oben){_GO}", lambda n: ("", (((C,), "HOME"),), ())),
    (rf"{_GERMAN_N}{_WORDS} zurück{_GO}|vorheriges wort",
     lambda n: ("", (((C,), "LEFT"),), (), ("word", -1, False))),
    (rf"{_GERMAN_N}{_WORDS} (?:vor|weiter|vorwärts){_GO}|nächstes wort",
     lambda n: ("", (((C,), "RIGHT"),), (), ("word", 1, False))),
    (rf"{_GERMAN_N}(?:zeichen|buchstaben?) zurück{_GO}",
     lambda n: ("", (((), "LEFT"),), (), ("char", -1, False))),
    (rf"{_GERMAN_N}(?:zeichen|buchstaben?) (?:vor|weiter|vorwärts){_GO}",
     lambda n: ("", (((), "RIGHT"),), (), ("char", 1, False))),
    (rf"{_GERMAN_N}zeilen? (?:nach oben|hoch|rauf){_GO}", lambda n: ("", (((), "UP"),), ())),
    (rf"{_GERMAN_N}zeilen? (?:nach unten|runter){_GO}", lambda n: ("", (((), "DOWN"),), ())),
    (r"(?:markierung|auswahl) (?:aufheben|löschen|entfernen)",
     lambda n: ("", (((), "RIGHT"),), ())),
]
_GERMAN_COMPILED = [(re.compile(rf"^(?:{pattern})$"), build) for pattern, build in _GERMAN_RULES]


def parse(text: str, lang: str = "en"):
    """The Command *text* is, or None when it's dictation to type. With
    *lang* "de" the German phrases count too."""
    said = normalize(text)
    if not said:
        return None
    if lang == "de" and (command := _match(_german_order(said), _GERMAN_COMPILED)):
        return command
    return _match(said, _COMPILED)


def _match(said: str, rules):
    """The Command the first of *rules* that matches all of *said* makes, or
    None."""
    for pattern, build in rules:
        m = pattern.fullmatch(said)
        if not m:
            continue
        groups = m.groupdict()
        raw = groups.get("n")
        count = 1 if raw is None else (int(raw) if raw.isdigit() else _COUNTS[raw])
        if not 1 <= count <= MAX_COUNT:
            return None
        unit_said = groups.get("u")
        args = (count, GERMAN_UNITS.get(unit_said, unit_said)) if unit_said else (count,)
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
    # selecting them, then select back over the words. Its leading space (the
    # auto-space after the dictation before) stays out of the selection.
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
