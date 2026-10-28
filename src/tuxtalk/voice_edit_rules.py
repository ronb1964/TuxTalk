"""Voice edits that don't go to the AI.

The voice-editing model (Qwen2.5-1.5B) is good at rewording and poor at exact,
letter-by-letter jobs: asked for all caps it can write "DRYWALL ScreWz", it
doesn't reliably remove a comma, and "remove the word testing" can lose a
whole line. Those jobs are done here in code, exactly.

Formatting is not text at all: the edit is pasted back as plain words. Bold,
italics and underline are the app's own Ctrl+B / Ctrl+I / Ctrl+U, sent only to
word processors, where those keys are known to mean that (in KWrite Ctrl+B sets
a bookmark, in a browser it can open the bookmarks). Font, size and colour
can't be done at all, and the user is told so.

Every pattern here matches the whole instruction, so anything longer
("remove the word testing and make it formal") still goes to the AI.
"""
import re

# --- "edit that, ..." said in a dictation ------------------------------------------------

# Voice editing without its key: a dictation (or a Type at pause phrase) that
# STARTS with "edit that" is an instruction for what TuxTalk typed just
# before it. Anywhere else in a sentence it's words. So that "Edit that file
# and send it" stays words, the instruction must come after a pause (the
# engine writes a comma or a period there) or start with an editing word.
_SPOKEN_EDIT = re.compile(r"^\s*(?:please\s+)?edit\s+that\b(?P<pause>[\s]*[,.:;!?-])?\s*(?P<rest>.*?)\s*$",
                          re.IGNORECASE | re.DOTALL)
_EDIT_VERBS = frozenset("""make change turn remove delete take add put replace rewrite reword
    fix correct shorten lengthen expand simplify use say write translate capitalize
    capitalise uppercase lowercase bold italicize underline swap move cut drop
    spell format convert""".split())


def spoken_edit(raw: str):
    """The instruction in "edit that, make it shorter": "make it shorter";
    "" for "edit that" alone (the next phrase says what); None when the
    dictation doesn't start with "edit that", or what follows it reads as
    a sentence ("edit that file")."""
    m = _SPOKEN_EDIT.match(raw or "")
    if m is None:
        return None
    rest = m.group("rest")
    if not rest or m.group("pause"):
        return rest
    first = re.sub(r"[^a-z]", "", rest.split()[0].lower())
    return rest if first in _EDIT_VERBS or first in ("please", "can", "could") else None


# --- the instruction, tidied ------------------------------------------------------------

def _norm(instruction: str) -> str:
    """Lower case, no closing punctuation or "please", single spaces."""
    s = " ".join(instruction.lower().replace(",", " ").split())
    s = s.strip(" .!?")
    s = re.sub(r"^(?:please|can you|could you)\s+", "", s)
    return re.sub(r"\s+please$", "", s)


_TARGET = (r"(?:this|it|that|these|everything|all of (?:this|it)|the (?:whole )?(?:text|selection"
           r"|selected text|words?|sentences?|lines?|thing)|(?:these|those|each|every) (?:lines?|sentences?))")
# "make this", "change it to", "put everything in", or nothing at all
_LEAD = rf"(?:(?:make|change|turn|convert|put|set|switch|write)\s+(?:{_TARGET}\s+)?(?:(?:to|in|into)\s+)?)?"


def _case_rule(words: str, verbs: str) -> re.Pattern:
    """"make this all caps" and "uppercase this" shapes for one kind of case."""
    return re.compile(rf"^(?:{_LEAD}(?:all\s+)?(?:{words})(?:\s+letters)?"
                      rf"|(?:{verbs})\s+(?:{_TARGET}|all))$")


_UPPER = _case_rule(r"caps|capitals|capital letters|upper ?case|block capitals|caps lock",
                    r"capitali[sz]e|upper ?case")
_LOWER = _case_rule(r"lower ?case|small letters|no capitals|no caps", r"lower ?case")
_TITLE = re.compile(rf"^(?:{_LEAD}title case|capitali[sz]e (?:the first letter of )?"
                    r"(?:each|every|all the) words?)$")

# What "remove the commas" removes. Commas and points between two digits stay:
# 1,250.50 is a number, not punctuation.
_MARKS = {
    "commas": ",", "periods": ".", "full stops": ".", "dots": ".",
    "exclamation points": "!", "exclamation marks": "!", "question marks": "?",
    "semicolons": ";", "colons": ":", "quotes": "\"“”",
    "quotation marks": "\"“”", "apostrophes": "'’",
    "dashes": "-–—", "hyphens": "-–—",
    "brackets": "()[]", "parentheses": "()",
    "punctuation": ",.!?;:\"“”–—",
}
_REMOVE = r"(?:remove|delete|take out|get rid of|drop|strip(?: out)?|lose|cut)"
_FROM = rf"(?:\s+(?:from|in|out of)\s+{_TARGET})?(?:\s+everywhere)?"
_REMOVE_MARKS = re.compile(rf"^{_REMOVE}\s+(?:all\s+)?(?:of\s+)?(?:the\s+)?"
                           rf"({'|'.join(sorted(_MARKS, key=len, reverse=True))}){_FROM}$")
_REMOVE_BULLETS = re.compile(rf"^{_REMOVE}\s+(?:all\s+)?(?:of\s+)?(?:the\s+)?"
                             rf"(?:bullets?|bullet points|numbering|list (?:markers|numbers)){_FROM}$")
# A list line's marker: "- ", "* ", "• ", "2. ", "10) ".
_BULLET = re.compile(r"^(?:[-*\u2022\u2013]|\d+[.)])\s+")
_BULLETED = re.compile(rf"^(?:{_LEAD}|add\s+|use\s+)?(?:an?\s+)?(?:bullet(?:ed)?(?: point)?s?(?: list)?"
                       r"|bullet points|list with bullets)$")
_NUMBERED = re.compile(rf"^(?:{_LEAD}(?:an?\s+)?numbered list|number (?:{_TARGET}|them|the lines|each line)"
                       r"|add numbers|numbered)$")
_INDENT = re.compile(rf"^(?:indent\s+(?:{_TARGET}|them|each line)|add (?:an |some )?indent(?:ation)?"
                     rf"(?:\s+(?:to|in)\s+{_TARGET})?|{_LEAD}indented)$")
_UNINDENT = re.compile(rf"^(?:(?:unindent|outdent|dedent)(?:\s+(?:{_TARGET}|them))?"
                       rf"|{_REMOVE}\s+(?:the\s+|all\s+the\s+)?(?:indent|indents|indentation)"
                       rf"{_FROM})$")
# Four spaces, not a tab: typed out (rather than pasted), Tab moves the focus
# in some apps.
INDENT = "    "
_REMOVE_WORD = re.compile(rf"^{_REMOVE}\s+(?:all\s+)?(?:of\s+)?(?:the\s+|every\s+)?"
                          rf"(?:words?|phrase|term)\s+(.+?){_FROM}$")


# --- doing it ----------------------------------------------------------------------------

def _per_line(text: str, change) -> str:
    """Apply *change* to each line's content, keeping its indentation and the
    line breaks exactly where they were."""
    out = []
    for line in text.split("\n"):
        body = line.lstrip()
        out.append(line[:len(line) - len(body)] + change(body) if body else line)
    return "\n".join(out)


def _tidy(body: str) -> str:
    """Spaces after something was taken out: no doubles, none before
    punctuation, no comma left leading the line or doubled."""
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r" +([,.!?;:])", r"\1", body)
    body = re.sub(r",(?:\s*,)+", ",", body)
    body = re.sub(r",(?=[.!?;:])", "", body)
    return body.strip().lstrip(",;: ")


def _remove_marks(text: str, marks: str) -> str:
    """*text* without the punctuation *marks*, line by line."""
    cls = re.escape(marks)
    # A mark goes unless it sits between two digits. Dashes become a space, so
    # "well-known" reads "well known" rather than "wellknown".
    pattern = re.compile(rf"(?<!\d)[{cls}]|[{cls}](?!\d)")

    def change(body):
        if not pattern.search(body):
            return body                   # a line without the mark stays as it is
        return _tidy(pattern.sub(lambda m: " " if m.group() in "-–—" else "", body))
    return _per_line(text, change)


def _remove_phrase(text: str, phrase: str):
    """*text* without *phrase* (whole words, any case), or None if it isn't there."""
    phrase = phrase.strip(" '\"“”‘’")
    if not phrase:
        return None
    pattern = re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
    if not pattern.search(text):
        return None                       # not there: let the AI make sense of it

    def change(body):
        if not pattern.search(body):
            return body                   # a line without the phrase stays as it is
        out, pos, capitalise = [], 0, False
        for m in pattern.finditer(body):
            before = body[pos:m.start()]
            if capitalise:
                before, capitalise = _capitalise_first(before)
            out.append(before)
            # The phrase opened a sentence: the word after it opens it now.
            # Only that word: other sentences keep their letters ("iPhone").
            head = "".join(out)
            capitalise = capitalise or (m.group()[0].isupper()
                                        and re.search(r"(?:^|[.!?])\s*$", head) is not None)
            pos = m.end()
        tail = body[pos:]
        if capitalise:
            tail, _ = _capitalise_first(tail)
        out.append(tail)
        return _tidy("".join(out))
    return _per_line(text, change)


def _capitalise_first(segment: str):
    """*segment* with its first letter in upper case, and True when it has
    no letter or digit, so the capital still goes to whatever follows it. A
    sentence that now opens with a number keeps its letters as they were."""
    m = re.search(r"[^\W_]", segment)
    if not m:
        return segment, True
    i = m.start()
    if not segment[i].isalpha():
        return segment, False
    return segment[:i] + segment[i].upper() + segment[i + 1:], False


def _title(body: str) -> str:
    """Title Case: every word's first letter in upper case."""
    # Not after an apostrophe: "don't" must not become "Don'T".
    return re.sub(r"(?<![\w'’])([a-z])", lambda m: m.group(1).upper(), body)


def mechanical_edit(text: str, instruction: str):
    """*text* changed exactly as *instruction* asks, for the jobs done in code,
    or None for everything that goes to the AI."""
    said = _norm(instruction)
    if _UPPER.match(said):
        return text.upper()
    if _LOWER.match(said):
        return text.lower()
    if _TITLE.match(said):
        return _per_line(text, _title)
    m = _REMOVE_MARKS.match(said)
    if m:
        return _remove_marks(text, _MARKS[m.group(1)])
    lines = [line for line in text.split("\n") if line.strip()]
    # A list of lines is made exactly. One paragraph into points needs the AI,
    # which decides where the points are.
    if len(lines) > 1 and _BULLETED.match(said):
        return _per_line(text, lambda body: "- " + _BULLET.sub("", body))
    if len(lines) > 1 and _NUMBERED.match(said):
        count = iter(range(1, len(lines) + 1))
        return _per_line(text, lambda body: f"{next(count)}. " + _BULLET.sub("", body))
    if _INDENT.match(said):
        return "\n".join(INDENT + line if line.strip() else line for line in text.split("\n"))
    if _UNINDENT.match(said):
        return "\n".join(_one_level_less(line) for line in text.split("\n"))
    if _REMOVE_BULLETS.match(said):
        return _per_line(text, lambda body: _BULLET.sub("", body))
    m = _REMOVE_WORD.match(said)
    if m:
        return _remove_phrase(text, m.group(1))
    return None


def _one_level_less(line: str) -> str:
    """*line* with one indent taken off: a tab, or up to INDENT's spaces."""
    if line.startswith("\t"):
        return line[1:]
    return line[min(len(INDENT), len(line) - len(line.lstrip(" "))):]


# Not "cut": people expect a cut to land on the clipboard.
_DELETE = re.compile(rf"^(?:delete|remove|erase|clear|get rid of|take out)\s+"
                     rf"(?:{_TARGET}|all|it all|all of that)$")


def is_delete(instruction: str) -> bool:
    """"Delete this": the selection goes, with the Delete key. Sent to the AI,
    it can come back as "None.", which would replace the text."""
    return bool(_DELETE.match(_norm(instruction)))


# --- formatting --------------------------------------------------------------------------

CANT_FORMAT = "cant_format"

_SHORTCUTS = {"bold": "bold", "bolded": "bold", "italic": "italic", "italics": "italic",
              "italicize": "italic", "italicise": "italic", "italicized": "italic",
              "italicised": "italic", "underline": "underline", "underlined": "underline"}
_CANT = {"font", "fonts", "typeface", "size", "bigger", "larger", "smaller", "tiny", "huge",
         "color", "colour", "colors", "colours", "highlight", "highlighted", "strikethrough",
         "center", "centre", "centered", "centred", "align", "justify", "superscript",
         "subscript"}
# Words that leave a formatting request a formatting request. Anything else
# ("make this sound bold and confident") means it's about the wording.
_FILLER = {"make", "this", "it", "that", "these", "the", "text", "selection", "selected",
           "word", "words", "all", "everything", "in", "into", "to", "put", "turn", "set",
           "change", "and", "a", "an", "style", "letters", "be", "of", "whole", "thing",
           "font", "type", "please", "point", "pt"}
# One colour is a colour to make the text ("make this red"); two are words to
# swap ("change red to blue"), which is about the wording.
_COLOURS = {"red", "blue", "green", "yellow", "orange", "purple", "black", "white", "gray",
            "grey", "pink", "brown"}


def formatting(instruction: str):
    """None for an instruction about the words. Otherwise the shortcut kinds
    asked for, in order (["bold", "italic"]), or CANT_FORMAT for formatting no
    shortcut does (font, size, colour)."""
    said = _norm(instruction)
    said = re.sub(r"\b(?:strike ?through|strike ?out|cross(?:ed)? out)\b", "strikethrough", said)
    said = re.sub(r"\b(\d+)\s*-?\s*(?:pt|points?)\b", r"\1 point", said)     # "14pt", "14-point"
    words = said.split()
    keys = [_SHORTCUTS[w] for w in words if w in _SHORTCUTS]
    cant = [w for w in words if w in _CANT]
    colours = [w for w in words if w in _COLOURS]
    if len(colours) == 1:
        cant += colours
    if "point" in words and any(w.isdigit() for w in words):
        cant.append("point")                        # a font size: "make it 14 point"
    if not keys and not cant:
        return None
    leftover = [w for w in words if w not in _SHORTCUTS and w not in _CANT
                and w not in _FILLER and w not in _COLOURS and not w.isdigit()]
    if leftover:
        return None
    if cant:
        return CANT_FORMAT
    return list(dict.fromkeys(keys))


# Word processors, where Ctrl+B, Ctrl+I and Ctrl+U are bold, italics and
# underline. Window classes and Wayland app ids, matched whole and ignoring case.
_WORD_PROCESSORS = {
    "libreoffice", "libreoffice-writer", "libreoffice-calc", "libreoffice-impress",
    "libreoffice-draw", "soffice", "org.libreoffice.libreoffice",
    "onlyoffice-desktopeditors", "desktopeditors", "org.onlyoffice.desktopeditors",
    "abiword", "com.abisource.abiword", "calligrawords", "org.kde.calligrawords",
}


def has_formatting_shortcuts(window_class) -> bool:
    """Whether the focused app is a word processor, where Ctrl+B/I/U format text."""
    return bool(window_class) and window_class.lower() in _WORD_PROCESSORS


# --- two changes at once --------------------------------------------------------------

# Words a change starts with. A piece of an instruction is a change of its own
# only if it starts with one, so "make it shorter and simpler" stays one
# instruction ("simpler" is no command) while "make it formal and remove the
# commas" is two.
_COMMANDS = frozenset("""
    make change turn convert put set switch write rewrite reword rephrase remove
    delete take get drop strip lose cut add use insert capitalize capitalise
    uppercase lowercase fix correct translate shorten expand lengthen simplify
    replace swap sort number indent unindent outdent dedent join combine merge
    split separate summarize summarise spell keep start end begin""".split())
# ", then" / "and then" / "and also" / ", and" / "and" / a bare comma, longest first.
_STEP_BREAK = re.compile(r"\s*(?:,\s*(?:and\s+)?then|\s+and\s+(?:then|also)|,?\s+and|,)\s+",
                         re.IGNORECASE)
MAX_STEPS = 3


def split_steps(instruction: str) -> list:
    """*instruction* as the changes it asks for, in order: "make it formal and
    remove the commas" -> ["make it formal", "remove the commas"].

    Split only where every piece starts with a command word; otherwise, and
    for more than MAX_STEPS pieces, the whole instruction is one step, as
    before. Bold, italics and Delete press keys in the app, which can't follow
    a rewrite that's typed in afterwards, so an instruction holding one of
    those stays whole too."""
    text = instruction.strip().rstrip(".!?")
    pieces = [p.strip() for p in _STEP_BREAK.split(text) if p.strip()]
    if not 1 < len(pieces) <= MAX_STEPS:
        return [instruction.strip()]
    for piece in pieces:
        first = _norm(piece).split()[0] if _norm(piece) else ""
        if first not in _COMMANDS or is_delete(piece) or formatting(piece):
            return [instruction.strip()]
    return pieces


# --- which edits may change the lines -----------------------------------------------------

_RELAYOUT = re.compile(
    r"\b(?:lists?|bulleted|numbered|join|combine|merge|one line|single line|paragraphs?"
    r"|shorten|shorter|summar\w*|condense|e-?mail|letter|split|separate|sentences|sort"
    r"|alphabeti\w*|reorder|poem|haiku|into|table|outline)\b")


def keeps_lines(instruction: str) -> bool:
    """Whether the edit should come back with the selection's lines: false
    for edits that lay the text out anew (lists, joining, shortening)."""
    return not _RELAYOUT.search(_norm(instruction))
