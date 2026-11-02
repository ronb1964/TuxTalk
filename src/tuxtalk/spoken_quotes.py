"""Spoken quotation marks, told apart from "quote" said as a word.

The marks: a bare "quote" opens and the next one closes ("he said quote hello
quote"); "unquote", "end quote" and "close quote" close what the last bare
"quote" opened; "open quote" and "begin quote" open; "quote unquote" puts the
next word in quotes ("a quote unquote expert"). "quote" is always the word
after an article or a possessive with at most one describing word ("a quote
for the job", "the price quote", "my favorite quote"), and as the verb with a
person after it ("to quote him", "don't quote me"). "unquote" and "end quote"
with nothing open are the words ("the end quote was long"); "close quote"
stays a mark, closing what the dictation before may have opened.

Any other bare "quote" can be either, and the words beside it don't settle
it: "please quote hello quote" and "she said quote me too quote" are
quotations although "please quote" and "quote me" read like the verb. So
they pair, except where the words beside one point to the word strongly. Of
an odd number, which can't all be marks, the one they point to most is the
word ("to quote Einstein quote imagination is everything quote"). Then those
after "to", "this", "his" and the like are words in twos ("his quote and her
quote"), as long as the rest still pair: in "he said quote this quote" the
second closes the first. With no such words a last one is a plain ".
"""
import re
from dataclasses import dataclass

OPEN, CLOSE, PLAIN = "\u201c", "\u201d", '"'

# The phrases that only open or only close, also what normalize keeps the
# words between as said.
OPENING = r"(?:open|begin)\s+quotes?"
CLOSING = r"(?:(?:close|end)\s+quotes?|unquote)"
_RE_SPOKEN = re.compile(
    r"(?P<pair>\bquote[\s,.-]+unquote\b)"
    rf"|(?P<open>\b{OPENING}\b)"
    rf"|(?P<close>\b{CLOSING}\b)"
    r"|(?P<bare>\bquote\b)",
    re.IGNORECASE,
)
# The comma or full stop the engine writes for the pause after a spoken
# opening mark ("He said, quote, hello"), and the full stop it writes after a
# closing one that the quotation already ended with ("Never. Quote.").
_RE_PAUSE_MARK = re.compile(r"\s*[,.]")
_RE_ENDED = re.compile(r"[.?!\u2026]\s*$")
# The word "quote unquote" puts in quotes.
_RE_NEXT_WORD = re.compile(r"[\s,.]*(\w[\w'’-]*)")

_DESCRIBING = (r"famous|great|good|nice|important|brief|long|short|memorable|favou?rite|"
               r"powerful|funny|inspiring|best|price|insurance|written|free|first|last|"
               r"new|final|old")
# Before "quote", these make it the noun. An article can't end a quotation,
# and "this" or "his" only with a describing word after it ("this famous").
_RE_NOUN_BEFORE = re.compile(
    r"\b(?:(?:a|an|the|my|our|your|their|every|each|any|some)\s+(?:(?:" + _DESCRIBING
    + r")\s+)?|(?:this|that|these|those|his|her|its)\s+(?:" + _DESCRIBING + r")\s+)$",
    re.IGNORECASE,
)

# "quote" as the verb with a person after it ("to quote him", "don't quote
# me"): neither a quotation's first word nor what follows one.
_RE_VERB_WITH_OBJECT = re.compile(
    r"\b(?:to|will|would|can|could|should|might|must|don't|didn't|won't|can't|never|"
    r"i'd|i'll|we'll|let's)\s+(quote)\s+(?:him|her|them|me|us|you)\b",
    re.IGNORECASE,
)

# How strongly the word before or after a bare "quote" points to the word:
# "to quote", "this quote", "don't quote me", "quote of the day".
_WORD_BEFORE = {
    "to": 2, "this": 2, "that": 2, "these": 2, "those": 2, "his": 2, "her": 2, "its": 2,
    "will": 1, "would": 1, "can": 1, "could": 1, "should": 1, "might": 1, "must": 1,
    "don't": 1, "didn't": 1, "won't": 1, "can't": 1, "i": 1, "we": 1, "they": 1,
    "i'd": 1, "i'll": 1, "we'll": 1, "let's": 1,
}
_WORD_AFTER = {"of": 1, "from": 1, "for": 1, "him": 1, "her": 1, "them": 1, "me": 1,
               "us": 1, "was": 1}
_RE_WORD_BEFORE = re.compile(r"([\w'’]+)[\s,.]*$")
_RE_WORD_AFTER = re.compile(r"[\s,.]*([\w'’]+)")
# A score this high (one of the strongest words before, "to", "this", "his",
# or a weaker word both before and after) keeps a quote the word if the
# others still pair.
_STRONG = 2
# Enough of the text before a "quote" to hold the word before it.
_NEARBY = 40


def _word_score(text: str, start: int, end: int) -> int:
    """How strongly the words beside the "quote" at *start*:*end* say it's
    the word and not a mark."""
    before = _RE_WORD_BEFORE.search(text[max(0, start - _NEARBY):start])
    after = _RE_WORD_AFTER.match(text, end)
    word = before.group(1).lower().replace("’", "'") if before else ""
    return _WORD_BEFORE.get(word, 0) + (_WORD_AFTER.get(after.group(1).lower(), 0)
                                        if after else 0)


@dataclass
class _Said:
    """One spoken quote word or phrase: *kind* is the _RE_SPOKEN group it
    matched, *mark* what it's typed as once decided ("word" keeps it)."""
    kind: str
    start: int
    end: int
    mark: str = ""


def _claim_explicit(said: list) -> None:
    """A bare "quote" before a closing phrase, with only words between,
    opens what it closes; one after an opening phrase closes it."""
    for i, phrase in enumerate(said):
        if phrase.kind not in ("open", "close"):
            continue
        step = -1 if phrase.kind == "close" else 1
        j = i + step
        while 0 <= j < len(said) and said[j].kind == "bare" and said[j].mark:
            j += step       # one already the word, or taken by another phrase
        if 0 <= j < len(said) and said[j].kind == "bare":
            said[j].mark = OPEN if phrase.kind == "close" else CLOSE


def _keep_strong_words(left: list, scores: list) -> None:
    """Mark as the word each quote in *left* whose score is _STRONG or more,
    leaving the rest an even number of marks: if the weaker ones are odd,
    the strong one after the last of them closes it ("he said quote this
    quote"), or else the strong one nearest before it opens it."""
    marks = [i for i, n in enumerate(scores) if n < _STRONG]
    if len(marks) % 2 and len(marks) < len(left):
        last = marks[-1]
        marks.append(last + 1 if last + 1 < len(left)
                     else max(i for i in range(last) if scores[i] >= _STRONG))
    for i, s in enumerate(left):
        if i not in marks:
            s.mark = "word"


def _pair_bare(text: str, said: list) -> None:
    """The bare quotes no phrase took, in pairs. Of an odd number, the one
    the words beside it point to most is the word, and so are the strongly
    word-like ones the pairs can do without."""
    left = [s for s in said if s.kind == "bare" and not s.mark]
    if not left:
        return
    scores = [_word_score(text, s.start, s.end) for s in left]
    if len(left) % 2 and max(scores):
        i = scores.index(max(scores))
        left.pop(i).mark = "word"
        scores.pop(i)
    _keep_strong_words(left, scores)
    left = [s for s in left if not s.mark]
    for i, s in enumerate(left):
        s.mark = CLOSE if i % 2 else OPEN
    if len(left) % 2:
        left[-1].mark = PLAIN


def _close_unopened(said: list, text: str) -> None:
    """An "unquote" or "end quote" with no mark open before it is the word
    ("the end quote was long")."""
    opened = False
    for s in said:
        if s.kind == "open" or s.mark == OPEN:
            opened = True
        elif s.kind == "close" or s.mark == CLOSE:
            if not (opened or text[s.start:s.end].lower().startswith("close")):
                s.mark = "word"
            opened = False


def _spoken(text: str) -> list:
    """Each spoken quote word or phrase in *text*; a bare "quote" that can
    only be the word is marked so already. The word "quote unquote" puts in
    quotes is left out, even when it's "quote"."""
    verbs = {m.start(1) for m in
             _RE_VERB_WITH_OBJECT.finditer(text.replace("\u2019", "'"))}
    said, taken = [], set()
    for m in _RE_SPOKEN.finditer(text):
        if m.start() in taken:
            continue
        quoted = m.lastgroup == "pair" and _RE_NEXT_WORD.match(text, m.end())
        if quoted:
            taken.add(quoted.start(1))
        word = m.lastgroup == "bare" and (m.start() in verbs
                                          or _RE_NOUN_BEFORE.search(text, 0, m.start()))
        said.append(_Said(m.lastgroup, m.start(), m.end(), "word" if word else ""))
    return said


def _written(text: str, s: _Said):
    """(what *s* is typed as, where the text carries on after it)."""
    if s.mark == "word":
        return text[s.start:s.end], s.end
    if s.kind == "pair":
        word = _RE_NEXT_WORD.match(text, s.end)
        if not word:
            return text[s.start:s.end], s.end
        return f"{OPEN}{word.group(1)}{CLOSE}", word.end()
    if s.kind == "open" or s.mark == OPEN:
        pause = _RE_PAUSE_MARK.match(text, s.end)
        return OPEN, pause.end() if pause else s.end
    if s.kind == "close" or s.mark == CLOSE:
        stop = _RE_ENDED.search(text, 0, s.start) and _RE_PAUSE_MARK.match(text, s.end)
        return CLOSE, stop.end() if stop else s.end
    return s.mark, s.end


def mark_spoken_quotes(text: str) -> str:
    """*text* with every spoken quote typed as a mark or kept as the word."""
    said = _spoken(text)
    if not said:
        return text
    _claim_explicit(said)
    _pair_bare(text, said)
    _close_unopened(said, text)
    out, pos = [], 0
    for s in said:
        written, after = _written(text, s)
        out += [text[pos:s.start], written]
        pos = after
    out.append(text[pos:])
    return "".join(out)
