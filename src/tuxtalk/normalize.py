"""
Spoken punctuation and commands to text: "comma" becomes ",", "new line" a
line-break marker, and the result gets tidy punctuation, spacing and capitals.
"""
import re

from .spoken_quotes import CLOSING, OPENING, mark_spoken_quotes
from .undo import GERMAN_NUMBER_PATTERN

# A line break in a dictation ("new line"). Typed, it's a Shift+Enter: a plain
# Enter would send a chat message or run a terminal's half-typed command.
LINE_BREAK = "\xa7SHIFT_ENTER\xa7"
# "soft break": three spaces, held as this until the space tidy is over,
# which would make them one.
_SOFT_BREAK = "\xa7SOFT_BREAK\xa7"

# Typographic marks and their plain look-alikes: TuxTalk's smart quotes and
# ellipsis, and the ones an app's autocorrect puts in (LibreOffice Writer's).
# Where only plain ASCII can be typed (the Flatpak's keyboard), and where text
# is compared for its words (voice editing's "edit that").
PLAIN_LOOKALIKES = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
    "\u2013": "-", "\u2014": "-", "\u00a0": " ", "\u2026": "...",
})

# =====================================================================
# The patterns, each under the step of normalize_text that uses it.
# =====================================================================

# --- _hold_quoted: "open quote ... close quote" kept as said ---
# Also "begin quote" ... "end quote" or "unquote", but not a bare "quote",
# which can be the word.
_RE_QUOTED_TEXT = re.compile(
    rf'\b{OPENING}[,.\s]+(.*?)[,.\s]+{CLOSING}\b',
    re.IGNORECASE
)

def _hold_quoted(text: str):
    """*text* with each "open quote ... close quote" section swapped for a
    placeholder, so no command inside it applies, and the sections."""
    sections = []

    def hold(match):
        sections.append(match.group(1))
        return f"__QUOTED_{len(sections) - 1}__"
    return _RE_QUOTED_TEXT.sub(hold, text), sections


def _restore_quoted(text: str, sections) -> str:
    """The held sections back, in smart quotes (prepare_text makes them
    straight when that's off), to be capitalised and punctuated like any
    quote: "he said open quote hello close quote" is He said “hello.”"""
    for i, quoted in enumerate(sections):
        text = text.replace(f"__QUOTED_{i}__", f"\u201c{quoted}\u201d")
    return text


# --- _protect_command_words: "literal <word>" ---
# Map of spoken words to placeholder tokens. When the user says
# "literal period" we preserve the word "period" instead of inserting "."
LITERAL_REPLACEMENTS = {
    "period": "__LIT_PERIOD__",
    "comma": "__LIT_COMMA__",
    "press tab": "__LIT_PRESS_TAB__",
    "newline": "__LIT_NEWLINE__",
    "new line": "__LIT_NEW_LINE__",
    "return": "__LIT_RETURN__",
    "line break": "__LIT_LINEBREAK__",
    "new paragraph": "__LIT_NEWPARA__",
    "paragraph break": "__LIT_PARABK__",
    "question mark": "__LIT_QMARK__",
    "exclamation point": "__LIT_EPOINT__",
    "exclamation mark": "__LIT_EPOINT2__",
    "semicolon": "__LIT_SEMICOLON__",
    "colon": "__LIT_COLON__",
    "apostrophe": "__LIT_APOSTROPHE__",
    "quote": "__LIT_QUOTE__",
    "hyphen": "__LIT_HYPHEN__",
    "dash": "__LIT_DASH__",
    "ellipsis": "__LIT_ELLIPSIS__",
    "dot dot dot": "__LIT_DOTDOTDOT__",
    "full stop": "__LIT_FULLSTOP__",
}

# Restore mapping: each placeholder → its original spoken word ("press, tab"
# comes back as "press tab", the one escape that takes a comma)
LITERAL_RESTORE = {ph: word for word, ph in LITERAL_REPLACEMENTS.items()}

# Precompile literal escape patterns: "literal <word>" or "the word <word>".
# "press, tab" too, which the Tab rule below also accepts; a comma in the
# others is a real one ("the word question, Mark").
_LITERAL_PATTERNS = [
    (re.compile(rf"\b(?:literal|the\s+words?)\s+{re.escape(word)}\b", re.IGNORECASE), placeholder)
    for word, placeholder in LITERAL_REPLACEMENTS.items()
] + [(re.compile(r"\b(?:literal|the\s+words?)\s+press,\s+tab\b", re.IGNORECASE),
      LITERAL_REPLACEMENTS["press tab"])]

# An article before a mark word makes it a thing talked about. A capital A
# counts only where a sentence, a line (a dictated line break, LINE_BREAK, ends
# in §) or a quotation starts: elsewhere it's
# a letter ("Part A colon introduction" is "Part A: introduction").
_ARTICLE = (r"\b(?:a|an|the|another|An|The|Another)"
            r"|(?:^|(?<=[.!?:]\s)|(?<=\n)|(?<=\xa7)|(?<=[\u201c\"]))A")
# "this", "that", "no" or "one" before a mark makes it a thing talked about
# when more of the sentence follows ("remove this comma from it", "that comma
# is wrong"); at the end of a phrase, or before a word that starts a new
# clause, it's said for the mark ("is it this question mark", "first do this
# comma then that", "I'll take that one comma thanks").
_POINTER = r"\b(?i:this|that|no|one)"
_TALKED_ABOUT = (r"(?=\s+(?!(?i:then|and|but|so|or|thanks|thank|please|i|we|you|he|she|they|"
                 r"it|(?:is|was|are|were|do|does|did|can|could|will|would|should)\s+"
                 r"(?:that|this|it|he|she|they|we|you|i))\b)\w)")

# --- _protect_command_words: command words used as English nouns ---
# Words like "period", "comma", "return" and "dash" are command triggers
# in dictation, but they are also normal English nouns in phrases like "period
# of time", "in return", "a dash of salt", "the comma operator". Without this
# pass, those phrases get corrupted: "period of time" → "period. Of time".
#
# Each pattern captures surrounding context and substitutes a placeholder for
# just the protected word. Same mechanism as _LITERAL_PATTERNS — placeholders
# are restored to their original words by _restore_protected_words.
_CONTEXT_PROTECT_PATTERNS = [
    # "[article/adj/possessive] period" — strong noun signal
    (re.compile(
        r"\b(a|an|the|this|that|these|those|my|his|her|its|our|their|your|"
        r"any|some|each|every|long|short|brief|extended|lengthy|given|"
        r"certain|trial|grace|menstrual|test|same|full|half|specific|"
        r"particular|entire|whole|two-week|three-month|24-hour)\s+period\b",
        re.IGNORECASE,
    ), r"\1 __LIT_PERIOD__"),
    # "period of [word]" — fixed noun phrase: "period of time", "period of mourning"
    (re.compile(r"\bperiod(\s+of\b)", re.IGNORECASE), r"__LIT_PERIOD__\1"),

    # "[prep/article/possessive/adj/modal] return" — noun/verb usage.
    # Modals ("will/can return") and infinitive "to return" are always the
    # verb. Subject pronouns are deliberately NOT protected: "thank you
    # return" is a very common command usage and must keep working.
    (re.compile(
        r"\b(in|on|at|the|a|an|my|his|her|its|our|their|your|tax|safe|"
        r"swift|prompt|early|late|partial|full|complete|annual|monthly|"
        r"yearly|first|second|third|grand|major|sudden|big|home|no|any|carriage|"
        r"every|each|some|triumphant|long-awaited|"
        r"to|will|would|can|could|shall|should|may|might|must|never|not|"
        r"ll|just|please)\s+return\b",
        re.IGNORECASE,
    ), r"\1 __LIT_RETURN__"),
    # "return [to/of/from/home/...]" — verb or noun-of-X usage
    (re.compile(
        r"\breturn(\s+(?:to|of|from|home|the|on|by|policy|address|trip|"
        r"flight|ticket|date|window|policy|customer|item))\b",
        re.IGNORECASE,
    ), r"__LIT_RETURN__\1"),
    # "return it/this/that TO/FOR/..." — pronoun object plus a second signal
    # word. Bare "return this ..." is NOT protected: "this/it/a" are the most
    # common sentence starters after a line-break command.
    (re.compile(
        r"\breturn(\s+(?:it|this|that|them|these|those)\s+"
        r"(?:to|from|for|by|at|in|on|before|after|tomorrow|today|soon|"
        r"later|now|please|immediately))\b",
        re.IGNORECASE,
    ), r"__LIT_RETURN__\1"),

    # "[article/adj] dash"
    (re.compile(
        r"\b(a|an|the|this|that|my|his|her|every|each|any|some|"
        r"quick|sudden|mad|small|tiny|little|big|huge)\s+dash\b",
        re.IGNORECASE,
    ), r"\1 __LIT_DASH__"),
    # "dash of [word]"
    (re.compile(r"\bdash(\s+of\b)", re.IGNORECASE), r"__LIT_DASH__\1"),

    # "ended with a question mark", "add a semicolon", "came to a full stop":
    # with an article the mark is being talked about, not dictated. Nobody
    # dictates "a question mark" to end a sentence (without this, "ended with
    # a question mark" typed "ended with a?").
    *((re.compile(rf"({_ARTICLE})\s+(?i:{spoken})\b|({_POINTER})\s+(?i:{spoken})\b"
                  + _TALKED_ABOUT),
       lambda m, word=word: f"{m.group(1) or m.group(2)} {LITERAL_REPLACEMENTS[word]}")
      for spoken, word in (
          (r"question\s+mark", "question mark"), (r"exclamation\s+point", "exclamation point"),
          (r"exclamation\s+mark", "exclamation mark"), ("comma", "comma"),
          (r"full\s+stop", "full stop"), ("semicolon", "semicolon"), ("colon", "colon"),
          ("apostrophe", "apostrophe"), ("hyphen", "hyphen"), ("ellipsis", "ellipsis"))),
    # A line break is a thing too ("we need a new line of products").
    (re.compile(rf"({_ARTICLE})\s+(?i:new\s+line)\b"), r"\1 __LIT_NEW_LINE__"),
    (re.compile(rf"({_ARTICLE})\s+(?i:new\s+paragraph)\b"), r"\1 __LIT_NEWPARA__"),
    (re.compile(r"\bnew\s+line(\s+of\b)", re.IGNORECASE), r"__LIT_NEW_LINE__\1"),
    # The body part: "colon cancer", "colon surgery".
    (re.compile(r"\bcolon(\s+(?:cancer|surgery|polyps?|health|cleanse|screening|"
                r"exam|biopsy))\b", re.IGNORECASE), r"__LIT_COLON__\1"),

    # "comma operator/splice/key" — programming / writing terminology
    (re.compile(r"\bcomma\s+(operator|splice|key)\b", re.IGNORECASE),
     r"__LIT_COMMA__ \1"),
    # "Oxford/serial comma" — writing style
    (re.compile(r"\b(oxford|serial)\s+comma\b", re.IGNORECASE),
     r"\1 __LIT_COMMA__"),
]

# A period the engine put after a word, just before a spoken mark, and the
# space after it (see "question mark" below).
_ENGINE_PERIOD = r"(?:(?<=[^\W\d_])(?<!\.[^\W\d_])\.\s+)?"

# --- _spoken_marks_to_symbols: phrases, first ---
_MULTI_WORD_REPLACEMENTS = [
    # A spoken "comma" is still a word here, so a comma written just before
    # the command is the engine's pause and goes, as it does for paragraphs.
    (re.compile(r"[,\s]*\b(?:new\s*line|return|line\s+break)\b[,.:;\s]*", re.IGNORECASE),
     LINE_BREAK),
    (re.compile(r"[,.\s]*\bnew\s+paragraph\b[,.:;\s]*|\bparagraph\s+break\b[,.:;\s]*",
                re.IGNORECASE), LINE_BREAK * 2),
    (re.compile(r"[,.\s]*\bsoft\s+(?:break|line)\b[,.\s]*", re.IGNORECASE), _SOFT_BREAK),
    # Only "press tab" is a Tab: a bare "tab" is far more often the word ("every
    # tab shows"). Like "new line", it takes the spaces and punctuation the
    # engine wrote around it, so the next field doesn't start with a space.
    # "press tab key" and "press the tab key" are someone talking about the key.
    # A comma after "press" is a short pause there, not a second sentence.
    (re.compile(r"[, ]*\bpress,?\s+tab\b(?!\s+key\b)[,.:; ]*", re.IGNORECASE), "\t"),
    # The engine often ends the sentence itself before a spoken mark ("Send
    # it. Question mark."): the spoken mark replaces that period, or the text
    # came out as "Send it. ?". Only a period right after a word and before a
    # space, so "U.S." and an ellipsis keep theirs.
    (re.compile(_ENGINE_PERIOD + r"\bexclamation\s+(?:point|mark)\b", re.IGNORECASE), "!"),
    (re.compile(_ENGINE_PERIOD + r"\bquestion\s+mark\b", re.IGNORECASE), "?"),
    (re.compile(r"\bfull\s*stop\b", re.IGNORECASE), "."),
    (re.compile(r"\bdot\s+dot\s+dot\b|\bellipsis\b", re.IGNORECASE), "…"),
    (re.compile(r"\bopen\s+parenthesis\b", re.IGNORECASE), "("),
    (re.compile(r"\bclose\s+parenthesis\b", re.IGNORECASE), ")"),
    (re.compile(r"\bopen\s+bracket\b", re.IGNORECASE), "["),
    (re.compile(r"\bclose\s+bracket\b", re.IGNORECASE), "]"),
    (re.compile(r"\bopen\s+brace\b", re.IGNORECASE), "{"),
    (re.compile(r"\bclose\s+brace\b", re.IGNORECASE), "}"),
]

# --- _spoken_marks_to_symbols: single words ---
_SINGLE_WORD_REPLACEMENTS = [
    (re.compile(r"\bcomma\b,?", re.IGNORECASE), ","),
    (re.compile(r"\bsemicolon\b;?", re.IGNORECASE), ";"),
    (re.compile(r"\bcolon\b:?", re.IGNORECASE), ":"),
    (re.compile(r"\bperiod\b\.?", re.IGNORECASE), "."),
    (re.compile(r"\bapostrophe\b", re.IGNORECASE), "'"),
    # "em dash" MUST run before the bare hyphen/dash rule — otherwise
    # "em dash" is rewritten to "em -" first and this pattern never matches.
    (re.compile(r"\bem\s*dash\b", re.IGNORECASE), "—"),
    # A spoken hyphen joins the words beside it right away ("well dash
    # known" -> "well-known"). Only spaces are taken, so a tab stays, and a
    # " - " the engine wrote for a pause is never mistaken for one.
    (re.compile(r" *\b(?:hyphen|dash)\b *", re.IGNORECASE), "-"),
]

# --- German, when voice commands are said in German (lang "de") ---
# The mark words. A German one said as a word is held as a placeholder that
# keeps what was said, its noun capitalised as German writes it ("um punkt
# 12 Uhr" comes back "um Punkt 12 Uhr"); English placeholders give back a
# fixed word instead.
_GERMAN_MARK_WORDS = (r"komma|beistrich|punkt|fragezeichen|ausrufe?zeichen|ausrufungszeichen|"
                      r"doppelpunkt|semikolon|strichpunkt|apostroph|hochkomma|hochstrich|"
                      r"bindestrich|gedankenstrich|zeilenumbruch|neue\s+zeile|"
                      r"neue[nr]?\s+absatz|anführungszeichen|gänsefü(?:ß|ss)chen|auslassungspunkte")
_RE_GERMAN_HELD = re.compile(r"__LITDE_(\w+?)__")
# Said with German commands on, a mark word is the mark, as in Dragon, and
# "wörtlich" before it types the word. Rules can't parse German, so only
# the clear cases keep it a word ("Ich sehe das Fragezeichen" is typed "Ich
# sehe das?"):
# - right after an article that is never a clause's last word ("auf den
#   Punkt", "kein Komma", "zum Punkt"), or "ein", "kein" or a possessive
#   with an adjective ending as such adjectives do ("ein großes
#   Fragezeichen", "ein ganz wichtiger Punkt");
# - after der, die, das, mit, sein, ihr, ein or ohne, which end clauses too
#   ("Was ist das Fragezeichen", "Kommst du mit Fragezeichen"), only with
#   more of the sentence after it, maybe with an adjective between; after
#   Komma that more isn't a conjunction ("Es könnte sein Komma dass er kommt");
# - "mit", "ohne" and "ein" before a mark other than the sentence's end
#   ("mit Bindestrich", "ohne Komma"), unless a conjunction follows a
#   "mit" or "ein" ("Kommst du mit Komma oder bleibst du");
# - in numbers and times, compounds and the rhyme below.
_GERMAN_ARTICLE = (r"\b(?i:den|dem|des|"
                   r"(?:ein|kein|mein|dein|unser|eur|sein|ihr)(?:e|en|em|er|es)|"
                   r"kein|mein|dein|unser|euer|"
                   r"dies(?:e|en|em|er|es)|jed(?:e|en|em|er|es)|"
                   r"am|im|zum|zur|beim|vom|ins|ans)\s+")
_GERMAN_DEGREE = r"(?:(?i:ganz|sehr|so|zu|recht|ziemlich|besonders|wirklich|echt)\s+)?"
_GERMAN_STRONG = (rf"\b(?i:ein|kein|mein|dein|unser|euer)\s+{_GERMAN_DEGREE}"
                  r"(?-i:[a-zäöüß]+(?:er|es))\s+")
_GERMAN_CLAUSE_END = r"\b(?i:der|die|das|mit|sein|ihr|ein|ohne)\s+"
# An adjective written small between one and a mark that more of the
# sentence follows ("der springende Punkt bei der Sache"), but not before
# Komma: there it's the verb that ends a clause ("Wenn wir das machen Komma
# dann klappt es").
_GERMAN_ADJECTIVE = (rf"{_GERMAN_DEGREE}(?-i:(?!(?:hier|immer|wieder|später|lieber|aber|oder|"
                      r"sehr|mehr|weiter|früher|länger|öfter|der)\s)[a-zäöüß]+(?:e|en|er|es))\s+")
_GERMAN_CONJUNCTION = r"(?i:und|oder|aber|dass|ob|weil|wenn|bitte)\b"
_GERMAN_SENTENCE_END = r"(?i:fragezeichen|ausrufe?zeichen|ausrufungszeichen|punkt)\b"
_GERMAN_NUMBER = rf"(?:\d+|(?i:{GERMAN_NUMBER_PATTERN})\b)"


def _hold_german(m: re.Match) -> str:
    """The protected mark word *m* matched as a placeholder that keeps it."""
    return m.group(1) + "__LITDE_" + "_".join(m.group(2).split()) + "__"


def _give_back_german(m: re.Match) -> str:
    """A held German word as it was said, its noun (the last word) with a
    capital."""
    *before, noun = m.group(1).split("_")
    return " ".join([*before, noun[:1].upper() + noun[1:]])


# Three or more mark words in a row with the engine's commas between them
# ("Punkt, Punkt, Komma, Strich" in the children's rhyme) are words; two
# can be commands said with a pause ("Komma, neue Zeile"). So are the
# rhyme's two before "Strich", "Punkt für Punkt" and "null Komma nichts".
_RE_GERMAN_MARK_RUN = re.compile(
    rf"\b(?:{_GERMAN_MARK_WORDS})(?:,\s+(?:{_GERMAN_MARK_WORDS})\b){{2,}}"
    r"|\bpunkt\s+für\s+punkt\b|\bnull\s+komma\s+nichts\b"
    rf"|\b(?:{_GERMAN_MARK_WORDS})(?:,\s+(?:{_GERMAN_MARK_WORDS})\b)+,\s+strich\b",
    re.IGNORECASE)
_RE_GERMAN_MARK_WORD = re.compile(rf"()({_GERMAN_MARK_WORDS})", re.IGNORECASE)


def _hold_german_run(m: re.Match) -> str:
    """Every mark word in the run *m* matched, held as a word."""
    return _RE_GERMAN_MARK_WORD.sub(_hold_german, m.group())


_GERMAN_PROTECT_PATTERNS = [
    # "wörtlich Komma", "das Wort Punkt": the word itself, without "wörtlich".
    re.compile(rf"\b(?:wörtlich|das\s+wort)\s+()({_GERMAN_MARK_WORDS})\b", re.IGNORECASE),
    # "Komma-Fehler": a mark word starting a compound.
    re.compile(rf"(\b)({_GERMAN_MARK_WORDS})(?=-\w)", re.IGNORECASE),
    re.compile(rf"({_GERMAN_ARTICLE}|{_GERMAN_STRONG})({_GERMAN_MARK_WORDS})\b", re.IGNORECASE),
    re.compile(rf"(\b(?i:ohne)\s+)(?!(?i:fragezeichen|ausrufe?zeichen|ausrufungszeichen)\b)"
               rf"({_GERMAN_MARK_WORDS})\b", re.IGNORECASE),
    re.compile(rf"(\b(?i:mit|ein)\s+)(?!{_GERMAN_SENTENCE_END})({_GERMAN_MARK_WORDS})\b"
               rf"(?!\s+{_GERMAN_CONJUNCTION})", re.IGNORECASE),
    re.compile(rf"({_GERMAN_CLAUSE_END})(komma|beistrich)\b(?=\s+(?!{_GERMAN_CONJUNCTION})"
               r"[^\W\d_])", re.IGNORECASE),
    re.compile(rf"({_GERMAN_CLAUSE_END}(?:{_GERMAN_ADJECTIVE})?)"
               rf"((?!komma\b|beistrich\b){_GERMAN_MARK_WORDS})\b(?=\s+[^\W\d_])", re.IGNORECASE),
    # "Er hat den letzten Punkt vergessen", "Mit dem neuen Komma ist es besser".
    re.compile(rf"({_GERMAN_ARTICLE}{_GERMAN_ADJECTIVE})({_GERMAN_MARK_WORDS})\b"
               r"(?=\s+[^\W\d_])", re.IGNORECASE),
    # "Punkt 12 Uhr", "um Punkt zwölf", "um Punkt halb acht": the time.
    re.compile(rf"(\b)(punkt)(?=\s+(?:{_GERMAN_NUMBER}|(?i:halb|viertel|ein\s+uhr)\b))",
               re.IGNORECASE),
    # "drei Komma fünf", "ein Komma fünf" are 3,5 and 1,5 (the engines mostly
    # write them so already).
    re.compile(rf"((?<!\w)(?:{_GERMAN_NUMBER}|(?i:ein)\b)\s+)(komma)(?=\s+{_GERMAN_NUMBER})",
               re.IGNORECASE),
]

# A German closing quote, “, is English's opening one, which the spacing
# rules treat as one: it stands in as this until they have run, for a
# spoken one and for one the engine wrote after its „ ("„Hallo“ und").
_GERMAN_CLOSE = "\x02"
_RE_GERMAN_PAIR = re.compile("\u201e([^\u201e\u201c\u201d]*)\u201c")

# Phrases first, as in English: "Punkt Punkt Punkt" before "Punkt",
# "eckige Klammer auf" before "Klammer auf". German quotes are „…“, each
# taking the spaces and the engine's pause comma on its inner side.
_GERMAN_MARKS = [
    (re.compile(r"\b(?:anführungszeichen|gänsefü(?:ß|ss)chen)\s+(?:auf|unten)\b[,.]?\s*",
                re.IGNORECASE), "\u201e"),
    (re.compile(r"[,\s]*\b(?:anführungszeichen|gänsefü(?:ß|ss)chen)\s+(?:zu|oben)\b",
                re.IGNORECASE),
     _GERMAN_CLOSE),
    (re.compile(r"[,\s]*\b(?:neue\s+zeile|zeilenumbruch)\b[,.:;\s]*", re.IGNORECASE),
     LINE_BREAK),
    (re.compile(r"[,.\s]*\bneue[nr]?\s+absatz\b[,.:;\s]*", re.IGNORECASE), LINE_BREAK * 2),
    (re.compile(r"\bpunkt\s+punkt\s+punkt\b|\bauslassungspunkte\b", re.IGNORECASE), "…"),
    (re.compile(_ENGINE_PERIOD + r"\b(?:ausrufe?zeichen|ausrufungszeichen)\b", re.IGNORECASE),
     "!"),
    (re.compile(_ENGINE_PERIOD + r"\bfragezeichen\b", re.IGNORECASE), "?"),
    (re.compile(r"\bgeschweifte\s+klammer\s+auf\b", re.IGNORECASE), "{"),
    (re.compile(r"\bgeschweifte\s+klammer\s+zu\b", re.IGNORECASE), "}"),
    (re.compile(r"\beckige\s+klammer\s+auf\b", re.IGNORECASE), "["),
    (re.compile(r"\beckige\s+klammer\s+zu\b", re.IGNORECASE), "]"),
    (re.compile(r"\bklammer\s+auf\b", re.IGNORECASE), "("),
    (re.compile(r"\bklammer\s+zu\b", re.IGNORECASE), ")"),
    (re.compile(r"\b(?:komma|beistrich)\b,?", re.IGNORECASE), ","),
    (re.compile(r"\b(?:semikolon|strichpunkt)\b;?", re.IGNORECASE), ";"),
    (re.compile(r"\bdoppelpunkt\b:?", re.IGNORECASE), ":"),
    (re.compile(r"\bpunkt\b\.?", re.IGNORECASE), "."),
    (re.compile(r"\b(?:apostroph|hochkomma|hochstrich)\b", re.IGNORECASE), "'"),
    # German's dash between words is the en dash, with a space each side.
    (re.compile(r" *\bgedankenstrich\b *", re.IGNORECASE), " – "),
    (re.compile(r" *\bbindestrich\b *", re.IGNORECASE), "-"),
]


# --- _tidy_punctuation: spaces inside brackets and quotes, before marks,
#     and runs of marks, in this order ---
def _colon_after_dash(m: re.Match) -> str:
    """The spoken dash took the space after it, so a colon gets one back."""
    return ": " if m.string[m.end():m.end() + 1].isalnum() else ":"


_PUNCTUATION_TIDY = [
    (re.compile(r"(\(|\[|\{)\s+"), r"\1"),
    (re.compile(r"\s+(\)|\]|\})"), r"\1"),
    (re.compile(r"(\u201c)\s+"), r"\1"),
    (re.compile(r"\s+(\u201d)"), r"\1"),
    (re.compile(r"\u201c\s*,+"), "\u201c"),
    (re.compile(r",+\s*\u201d"), "\u201d"),
    # No space before a mark, but before a word that starts with a dot
    # (".NET", ".env", ".5").
    (re.compile(r"\s+([,;:?!…—]|\.(?!\w))"), r"\1"),
    # One mark where the engine and the speaker each wrote one.
    (re.compile(r"[,;:]\s*([.?!…])"), r"\1"),
    (re.compile(r"([!?])\s*\."), r"\1"),
    (re.compile(r"([!?])\s*,"), r"\1"),
    (re.compile(r",+\s*([!?])"), r"\1"),
    (re.compile(r",\s*\."), "."),
    (re.compile(r",\s*,+"), ","),
    (re.compile(r"-\s*:"), ":"),
    (re.compile(r":\s*-"), _colon_after_dash),
    # Runs of marks: four dots or more are an ellipsis, two are one dot.
    (re.compile(r"\.{4,}"), "…"),
    (re.compile(r"(?<!\.)\.\.(?!\.)"), "."),
    (re.compile(r"([!?])\1+"), r"\1"),
]

# --- _space_sentences ---
_RE_SPACE_AFTER_ENDER = re.compile(r"([.?!…])(?!\s|$|[\u201d\x02\)\]\}])")
_RE_EM_DASH_SPACE = re.compile(r"\s*—\s*")

# --- _tidy_line_spaces ---
_RE_SPACE_BEFORE_NEWLINE = re.compile(r"[ \t]+\n")
_RE_SPACE_AFTER_NEWLINE = re.compile(r"\n[ ]+")
# Not a dot that starts a word (".NET is great").
_RE_LEADING_PUNCT = re.compile(r"^[\s]*(?:[,;:!?]|\.(?!\w))+(?!…)")
_RE_MULTI_SPACE = re.compile(r"[ ]{2,}")

# --- _capitalize_and_end_lines ---
_RE_TRAILING_COMMA = re.compile(r",$")
# What counts as "this line is already punctuated". Colon and semicolon are
# included: a dictated "colon" is deliberate punctuation and must not get a
# full stop straight after it ("the plan:."). Closing quotes and brackets
# after the mark are stepped over, since the full stop is later moved inside
# a closing quote ("Hello.”" is finished, not "Hello.”.").
_RE_ENDS_PUNCTUATED = re.compile(r"[.?!…:;][\"'”“’)\]}]*$")
# Negative lookbehind: don't capitalize after single-letter abbreviations
# ("e.g. that" / "U.S. economy" must not become "e.g. That" / "U.S. Economy").
_RE_CAP_AFTER_ENDER = re.compile(r"(?<![A-Za-z]\.[A-Za-z])([.?!…]\s+)([^\W\d_])")
# Standalone lowercase "i" → "I" (also catches "i'll", "i'm", "i've", "i'd"
# because the apostrophe is a word boundary). Case-sensitive: an existing
# "I" is left alone. Words like "in", "it", "iPad" are not matched because
# they have no word boundary on the right side of the "i".
# The negative lookahead keeps "i.e." intact: the dot is a word boundary, so
# \bi\b alone would match its first letter and write "I.e." mid-sentence.
_RE_STANDALONE_I = re.compile(r"\bi\b(?!\.\s*e\.)")

# --- Sentence punctuation moved inside a closing smart quote ---
_RE_PUNCT_OUTSIDE_QUOTE = re.compile(r"\u201d( ?)([!?,;:.])")
# The quotation's last word, to tell an address ("ron@example.com") from words.
_RE_LAST_WORD = re.compile(r"(\S+)$")


def _mark_inside_quote(m: re.Match) -> str:
    """The mark after a closing quote, moved inside it (American style), but
    not after an email or web address: \u201cron@example.com.\u201d types the
    period as part of it."""
    last = _RE_LAST_WORD.search(m.string, 0, m.start())
    if last and ("@" in last.group(1) or "://" in last.group(1)
                 or _RE_JOINED_TLD.search(last.group(1))):
        return m.group()
    return m.group(2) + "\u201d"

# --- _join_spoken_addresses and _finish_addresses ---
# Country endings (.no .it .my .in .us ...) are also words that start
# sentences, so they only count written joined to the domain ("example.de");
# with spaces around the dot ("gmail. com") only endings that can't be words
# do. Otherwise "Look at this. My car" would become "Look@this.My car".
_EMAIL_TLDS_SAFE = "com|org|net|edu|gov|io"
_EMAIL_TLDS_ALL = (_EMAIL_TLDS_SAFE + "|co|uk|ca|de|fr|us|au|jp|cn|in|br|mx|ru|kr|es|it|nl|se|"
                   "no|fi|pl|cz|hu|ro|gr|pt|ie|nz|za|ae|il|tr|th|vn|ph|id|my|sg|hk|tw")
# A name may be said with its marks between two of its words: "john dot doe",
# "jane underscore smith", "john no space doe" (as in Dragon). The speech
# model writes "no space" as one word as often ("Nospace", "no-space"), so
# the marks are looked up with spaces and hyphens taken out.
_SPOKEN_NAME_MARKS = {"dot": ".", "underscore": "_", "dash": "-", "hyphen": "-", "nospace": ""}
_NAME_MARK = r"\s+(?i:dot|underscore|dash|hyphen|no[\s-]*space)\s+"
_RE_NAME_MARK = re.compile(_NAME_MARK)
_EMAIL_NAME = r"(?<![\w'’])((?:[a-zA-Z0-9._-]+" + _NAME_MARK + r")*[a-zA-Z0-9._-]+)"
# Case-sensitive: endings are lowercase, and "three.No" is a missed space.
_RE_EMAIL_JOINED = re.compile(
    _EMAIL_NAME + r"\s+at\s+([a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.(?:" + _EMAIL_TLDS_ALL + r"))\b"
)
_RE_EMAIL_AT_WORD = re.compile(
    _EMAIL_NAME + r"\s+at\s+([a-zA-Z0-9.-]+?)\s*\.\s*(" + _EMAIL_TLDS_SAFE + r")\b",
    re.IGNORECASE
)
# A gap the punctuation passes opened inside an address ("john@gmail. Com").
# Space is required next to the dot (a joined ending is already part of the
# address), and the ending must be one that can't start a sentence, so
# "john@gmail.com. It was great" keeps its full stop. "co" counts only before
# "uk". The ending alone ignores case: the capitalization pass wrote "Com".
_RE_EMAIL_SPLIT_TLD = re.compile(
    r"(@[\w-]+(?:\.[\w-]+)*)(?:\s+\.\s*|\.\s+)"
    r"((?i:" + _EMAIL_TLDS_SAFE + r"|uk|co(?=\s*\.\s*uk\b)))\b"
)
_RE_EMAIL_AT_SPACE = re.compile(r"@\s+")
_RE_ENDS_IN_TLD = re.compile(r"\.(?:" + _EMAIL_TLDS_ALL + r")$")
# A dot in an email address's name ("john.doe@gmail.com").
_RE_IN_MAILBOX = re.compile(r"\.[\w.+-]*@")
# A dot written joined to a lowercase web ending is part of an address, and
# so is one followed by more lowercase parts that end in one ("www.example.org").
_RE_JOINED_TLD = re.compile(r"\.(?:[a-z0-9-]+\.)*(?:" + _EMAIL_TLDS_ALL + r"|dev)\b")

# Words that come before "at" in ordinary speech ("look at github.com", "we
# arrived at example.com") and are never the mailbox name of a spoken address.
_NOT_EMAIL_NAMES = frozenset("""
    look looks looked looking arrive arrives arrived arriving be is are was were
    am been it here there online live lives lived posted hosted available found
    located based meet met stay stays stayed staying visit visited work works
    worked working sign signed log logged up in on out me us you him her them
""".split())


# A spoken "dot" inside an address: "gmail dot com", "www dot example dot
# org". Parakeet writes the word about one time in three; this makes it come
# out the same as "gmail.com". Only before an ending that can't be a word, and
# only where it reads as an address: after "at" (an email), with two dots, or
# at the end of a clause. "Dot com" is also a description ("a dot com
# company", "the dot com days"), and those stay words.
_RE_SPOKEN_DOT_DOMAIN = re.compile(
    r"(?<![\w'’])((?!dot\b)[a-z0-9][a-z0-9-]*(?:\s+dot\s+(?!dot\b)[a-z0-9][a-z0-9-]*)*)"
    r"\s+dot\s+(" + _EMAIL_TLDS_SAFE + r")\b",
    re.IGNORECASE
)
_NOT_DOMAIN_WORDS = frozenset("the a an this that my your our its his her their and or of".split())
_RE_AFTER_AT = re.compile(r"(?i)\bat\s+$")
_RE_CLAUSE_GOES_ON = re.compile(
    r"(?i)\s*($|[.,!?;:)\]\"'”’§]|(for|to|and|or|if|when|so|but|please|today|"
    r"tomorrow|now|instead|too|as|with|from)\b)")


def _join_spoken_dots(m):
    """"gmail dot com" -> "gmail.com" where it reads as an address."""
    labels, tld = m.groups()
    words = labels.split()
    if words[0].lower() in _NOT_DOMAIN_WORDS:
        return m.group(0)
    s = m.string
    if not (_RE_AFTER_AT.search(s, 0, m.start()) or len(words) > 1
            or _RE_CLAUSE_GOES_ON.match(s, m.end())):
        return m.group(0)
    return re.sub(r"\s+dot\s+", ".", labels, flags=re.IGNORECASE).lower() + "." + tld.lower()


def _join_email(m):
    """"ron at example.de" -> "ron@example.de", unless "ron" is an ordinary word.
    Lower case, as addresses are written: Parakeet capitalizes names ("John")."""
    name, domain = m.groups()
    return _email_or_said(m.group(0), name, domain)


def _join_spaced_email(m):
    """"ron at gmail. com" -> "ron@gmail.com", unless "ron" is an ordinary word."""
    name, domain, tld = m.groups()
    return _email_or_said(m.group(0), name, f"{domain}.{tld}")


def _email_or_said(said, name, domain):
    """*said*, "name at domain", as the email it says: the name with its
    spoken marks written out (_spoken_name), or only its last word when
    another word in it is an ordinary one ("put a dot here at ..."); *said*
    as it was when that word is ordinary too."""
    head = ""
    if any(word.lower() in _NOT_NAME_WORDS for word in _RE_NAME_MARK.split(name)[:-1]):
        head, _, name = name.rpartition(" ")
        head += " "
    if name.lower() in _NOT_EMAIL_NAMES:
        return said
    return head + f"{_spoken_name(name)}@{domain}".lower()


def _spoken_name(name):
    """A mailbox name as said, written out: "john dot doe" -> "john.doe"."""
    return _RE_NAME_MARK.sub(
        lambda m: _SPOKEN_NAME_MARKS[re.sub(r"[\s-]", "", m.group().lower())], name)


# A dictation that is only an address goes into a To: box or an address bar
# (issue #44): it's typed as the address alone, with no full stop after it.
# Its name is one word, or words joined by spoken marks: "john doe at ..."
# could as well be "ssh john at ..." or "it's john at ...". A country ending
# counts in lower case only, as in the passes above: "Wait.No." is a missed
# space, not an address.
_ADDRESS_ENDING = r"(?:(?i:" + _EMAIL_TLDS_SAFE + r")|" + _EMAIL_TLDS_ALL + r")"
_RE_EMAIL_ALONE = re.compile(
    r"((?:[\w.+-]+" + _NAME_MARK + r")*[\w.+-]+)\s+(?i:at)\s+"
    r"([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\." + _ADDRESS_ENDING + r")")
_RE_EMAIL_WRITTEN = re.compile(
    r"[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\." + _ADDRESS_ENDING)
_RE_WEB_ALONE = re.compile(
    r"(?:(?i:https?://))?[\w-]+(?:\.[\w-]+)*\." + _ADDRESS_ENDING + r"(?:/\S*)?")
_RE_SPACED_ENDING = re.compile(r"\.\s+((?i:" + _EMAIL_TLDS_SAFE + r"))\b")
_NOT_NAME_WORDS = _NOT_EMAIL_NAMES | {"i", "we", "they", "he", "she", "the", "a", "an", "to", "and",
                                        "my", "our"}


def address_alone(text: str) -> str | None:
    """*text*, a whole dictation as the engine wrote it, as the one address
    it says ("Doe at gmail dot com." -> "doe@gmail.com", "github dot com."
    -> "github.com"), or None when it says anything else. A web address
    keeps its case: a path may need it."""
    said = _RE_SPOKEN_DOT_DOMAIN.sub(_join_spoken_dots, text.strip())
    said = _RE_SPACED_ENDING.sub(r".\1", said).rstrip(".").rstrip()
    web = said
    said = said.rstrip("!?")     # an email's; a web address's may be its path's
    m = _RE_EMAIL_ALONE.fullmatch(said)
    if m:
        name = m.group(1)
        if any(word.lower() in _NOT_NAME_WORDS for word in _RE_NAME_MARK.split(name)):
            return None
        return f"{_spoken_name(name)}@{m.group(2)}".lower()
    if _RE_EMAIL_WRITTEN.fullmatch(said):
        return said.lower()     # written with its @ by the engine, or a snippet
    return web if _RE_WEB_ALONE.fullmatch(web) else None


def _close_tld_gap(m):
    """Join the ending to the address, unless the address is already complete
    and the "ending" is capitalized: that is a new sentence ("bob@work.com.
    Net profit was up"), not a split address ("john@gmail. Net"). "Uk" after
    ".co" is still the address: the "co." before it got it capitalized."""
    address, tld = m.groups()
    if (tld != tld.lower() and _RE_ENDS_IN_TLD.search(address)
            and not (tld.lower() == "uk" and address.lower().endswith(".co"))):
        return m.group(0)
    return f"{address}.{tld.lower()}"


def _fix_email_spacing(text: str) -> str:
    """Close the gaps the punctuation passes open inside an address:
    "john@gmail. Com" -> "john@gmail.com", "a@b. co. uk" -> "a@b.co.uk"."""
    text = _RE_EMAIL_AT_SPACE.sub("@", text)
    while True:
        fixed = _RE_EMAIL_SPLIT_TLD.sub(_close_tld_gap, text)
        if fixed == text:
            return text
        text = fixed


# --- Guards for _space_sentences and _cap_first ---
# Characters that can precede the first word of a line without being the word:
# straight and smart quotes, and opening brackets.
_LINE_OPENERS = "\"'“„‘([{"

# An email address, URL, or bare domain at the start of a line. These are
# written the way the user said them, so sentence capitalization leaves them be.
# A bare domain may have a space after its dot: the space-after-period pass runs
# first and can split a domain it doesn't recognise ("Example. Com").
_RE_ADDRESSY = re.compile(
    r'(?:'
    r'[\w.-]*@'                                                 # email
    r'|https?://'                                               # explicit URL
    r'|www\.'                                                   # www host
    r'|[\w-]+\.\s*(?:com|org|net|edu|gov|io|dev|co|uk)\b'       # bare domain
    r')',
    re.IGNORECASE
)

# File extensions the space-after-period pass must not split. Kept to formats
# a person plausibly dictates the name of; the guard also requires a word
# character immediately before the dot, so ordinary sentences are unaffected.
_FILE_EXTENSIONS = (
    "pdf|docx?|xlsx?|pptx?|odt|ods|odp|rtf|tex|csv|tsv|txt|md|log|conf|ini|cfg"
    "|toml|ya?ml|json|xml|html?|css|js|ts|py|sh|rb|go|rs|java|c|cpp|h"
    "|png|jpe?g|gif|bmp|svg|webp|ico|tiff?"
    "|mp3|mp4|m4a|wav|flac|ogg|mov|avi|mkv|webm"
    "|zip|tar|gz|bz2|xz|7z|rar|iso|deb|rpm|appimage|exe|dmg|apk"
)
_RE_FILE_EXT = re.compile(rf"\.(?:{_FILE_EXTENSIONS})\b", re.IGNORECASE)

# --- format_times and _join_letter_numbers ---
# Fix the engines' mangled times: "11. 30 p. m." → "11:30 PM", "11 p. m." → "11 PM".
# Minutes are optional, and AM/PM may be written "p. m.", "p. M.", "p.m.", "PM",
# "a. m.", "a.m." or "AM" (any case or spacing). The trailing (?![A-Za-z]) stops
# the AM/PM group from matching the start of ordinary words: "5 among us" and
# "3 amazing" are not times.
# Minutes may also come run together with the hour: Parakeet writes a spoken
# "six forty-one p.m." as "6.41 p.m." one time and "641 p.m." the next. Only an
# hour of 1-12 and minutes of 00-59 count, so "2000 pm" is left alone.
_RE_TIME_FORMAT = re.compile(
    r'\b(1[0-2]|0?[1-9])(?:[.:]\s*([0-5][0-9])|([0-5][0-9]))?'
    r'\s+([Pp]\.?\s*[Mm]\.?|[Aa]\.?\s*[Mm]\.?)(?![A-Za-z])',
    re.IGNORECASE
)

# "F eight" -> "F8", "B five A seven" -> "B5 A7": a capital letter and a
# number name one thing (a key, a grid square, a seat, vitamin B12), so the
# number stays a digit whatever "Write numbers as digits" does with eight on
# its own. Parakeet writes "F8" one time and "F eight" the next. "A" and "I"
# are words too ("A five minute walk"), so they are joined only beside
# another pair ("B five A seven"). A lowercase "f" counts because Whisper
# writes "f eight".
_LETTER_NUMBERS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
                   "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
                   "ten": "10", "eleven": "11", "twelve": "12"}
_NUM = r"((?i:" + "|".join(_LETTER_NUMBERS) + r")|\d{1,2})\b"
_RE_LETTER_NUMBER = re.compile(r"(?<![\w'\u2019.-])([B-HJ-Zf])[ -]" + _NUM)
_RE_A_I_AFTER_PAIR = re.compile(r"(\b[B-HJ-Z]\d{1,2})(\s+)([AI])[ -]" + _NUM)
_RE_A_I_BEFORE_PAIR = re.compile(r"(?<![\w'\u2019.-])([AI])[ -]" + _NUM + r"(?=\s+[B-HJ-Z]\d)")


def _letter_number(letter: str, number: str) -> str:
    return letter.upper() + _LETTER_NUMBERS.get(number.lower(), number)


def _join_letter_numbers(text: str) -> str:
    text = _RE_LETTER_NUMBER.sub(lambda m: _letter_number(m.group(1), m.group(2)), text)
    text = _RE_A_I_AFTER_PAIR.sub(
        lambda m: m.group(1) + m.group(2) + _letter_number(m.group(3), m.group(4)), text)
    return _RE_A_I_BEFORE_PAIR.sub(lambda m: _letter_number(m.group(1), m.group(2)), text)


def _fix_time_ampm(m: re.Match) -> str:
    """Normalize a matched time string to HH:MM AM/PM or HH AM/PM format.

    The trailing period of "a.m." can double as the sentence's full stop, which
    rewriting it to "AM" would drop. It is kept only when the abbreviation ends
    the text. Mid-text it is left out: "9 a.m. We should be early" and "8 a.m.
    Tuesday" look the same (the engines capitalize names as well as sentence
    starts), and a wrong sentence break is worse than a missing full stop.
    """
    hour = m.group(1)
    minute = m.group(2) or m.group(3)  # "6.41" / "641"; None if no minutes
    raw_ampm = m.group(4)
    ampm = re.sub(r'[\s.]', '', raw_ampm).upper()  # "p. m." / "p. M." → "PM"

    following = m.string[m.end():].strip()
    ends_sentence = raw_ampm.rstrip().endswith(".") and not following
    tail = "." if ends_sentence else ""

    if minute:
        return f"{hour}:{minute} {ampm}{tail}"
    return f"{hour} {ampm}{tail}"


def format_times(text: str) -> str:
    """Tidy every time in *text*: "7:15 p. m." -> "7:15 PM" (see _fix_time_ampm)."""
    return _RE_TIME_FORMAT.sub(_fix_time_ampm, text)


def _space_after_ender_repl(m: re.Match) -> str:
    """Insert a space after a sentence ender, except a period that belongs
    to the token around it (_dot_binds)."""
    ch = m.group(1)
    if ch == "." and _dot_binds(m.string, m.start(1)):
        return ch
    return ch + " "


def _dot_between(prev: str, prev2: str, after: str) -> bool:
    """Whether a period between *prev* (with *prev2* before it) and the two
    characters *after* it is inside a short token: a decimal, single letters
    with dots, or a word that starts with one."""
    nxt, nxt2 = after[:1], after[1:2]
    if prev.isdigit() and nxt.isdigit():
        return True     # a decimal, a price, a version
    if prev.isalpha() and not prev2.isalpha() and nxt.isalpha() and nxt2 == ".":
        return True     # single letters with dots: e.g., U.S., a.m.
    # .NET, .env, .5, and in a path or brackets: ~/.config, (.env)
    return (not prev or prev.isspace() or prev in "/~(") and nxt.isalnum()


def _dot_binds(s: str, i: int) -> bool:
    """Whether the period at *i* in *s* is part of a token, not a sentence's
    end: '3.5', '$19.99', 'U.S.', 'e.g.', 'a.m.', 'report.pdf',
    'example.com', 'john.doe@gmail.com' must not be split apart. Split, the
    capitalization pass would then upper-case the rest ("report. Pdf")."""
    prev = s[i - 1] if i > 0 else ""
    if _dot_between(prev, s[i - 2:i - 1] if i > 1 else "", s[i + 1:i + 3]):
        return True
    if not (prev.isalnum() or prev in "_-"):
        return False
    # A file's extension, a domain written joined, an email address's name.
    return bool(_RE_FILE_EXT.match(s, i) or _RE_JOINED_TLD.match(s, i)
                or _RE_IN_MAILBOX.match(s, i))


# Trailing line-break markers (possibly repeated) at the end of an utterance.
_RE_TRAILING_BREAKS = re.compile(r"(?:\s*(?:" + re.escape(LINE_BREAK) + r"))+\s*$")


def _ends_punctuated(text: str) -> bool:
    """True when *text* already ends a sentence (or a "colon"), the one rule
    for normalize_text and append_auto_punct."""
    return bool(_RE_ENDS_PUNCTUATED.search(text.rstrip()))


def append_auto_punct(text: str, auto_period: bool, auto_space: bool) -> str:
    """Append the automatic period/space to a finished utterance.

    When the utterance ends with line-break markers ("hello new line"),
    the period goes BEFORE the break; after it, it would start the next line
    with an orphan ". ". No trailing space is added after a line break (the
    cursor is already on a fresh line).
    """
    # Whitespace alone is not an utterance: punctuating it would type a lone
    # "." out of a recording that captured nothing.
    if not text or not text.strip():
        return text
    m = _RE_TRAILING_BREAKS.search(text)
    if m:
        core, trailing = text[:m.start()], text[m.start():]
        if not core.strip():
            return text  # utterance is only line breaks — nothing to punctuate
        # A comma before the break is the user's ("Dear John," new line).
        if auto_period and not _ends_punctuated(core) and not core.rstrip().endswith(","):
            core = core.rstrip() + "."
        return core + trailing
    if auto_period and not _ends_punctuated(text):
        text = text.rstrip() + "."
    if auto_space and not text.endswith((" ", "\n", "\t")):
        text = text + " "
    return text


def _join_spoken_addresses(text: str) -> str:
    """A spoken address joined ("gmail dot com", "ron at example.de"), before
    the space-after-period pass can split it. Spoken dots go first."""
    text = _RE_SPOKEN_DOT_DOMAIN.sub(_join_spoken_dots, text)
    return _RE_EMAIL_JOINED.sub(_join_email, text)


def _protect_command_words(text: str, lang: str) -> str:
    """Placeholders for command words that are meant as words: "literal
    period", and nouns such as "period of time" or "a dash of salt"; with
    *lang* "de" the German ones too ("auf den Punkt")."""
    if lang == "de":
        text = _RE_GERMAN_MARK_RUN.sub(_hold_german_run, text)
        for pat in _GERMAN_PROTECT_PATTERNS:
            text = pat.sub(_hold_german, text)
    for pat, placeholder in (*_LITERAL_PATTERNS, *_CONTEXT_PROTECT_PATTERNS):
        text = pat.sub(placeholder, text)
    return text


def _spoken_marks_to_symbols(text: str, lang: str) -> str:
    """Spoken punctuation and breaks as symbols: "comma" is ",", "new line"
    LINE_BREAK, and with *lang* "de" "Komma" too. Quotes first, each a mark
    or the word (spoken_quotes), then the phrases before single words
    ("question mark" before "mark")."""
    text = mark_spoken_quotes(text)
    german = _GERMAN_MARKS if lang == "de" else ()
    for pat, repl in (*german, *_MULTI_WORD_REPLACEMENTS, *_SINGLE_WORD_REPLACEMENTS):
        text = pat.sub(repl, text)
    return text


def _tidy_punctuation(text: str) -> str:
    """Spaces around brackets, quotes and marks, and doubled marks:
    "( hi )" is "(hi)", "yes ,." is "yes.", "wait!!" is "wait!"."""
    for pat, repl in _PUNCTUATION_TIDY:
        text = pat.sub(repl, text)
    return text.replace("...", "…")


def _space_sentences(text: str) -> str:
    """One space after a sentence ender (not inside "3.5", "U.S." or
    "report.pdf"), and one each side of an em dash."""
    text = _RE_SPACE_AFTER_ENDER.sub(_space_after_ender_repl, text)
    return _RE_EM_DASH_SPACE.sub(" — ", text)


def _tidy_line(line: str) -> str:
    """One line without stray punctuation at its start, runs of spaces, or
    trailing space. Leading tabs stay; a soft break at the end goes, since
    nothing follows it."""
    line = _RE_LEADING_PUNCT.sub("", line)
    leading_tabs = len(line) - len(line.lstrip("\t"))
    core = _RE_MULTI_SPACE.sub(" ", line.lstrip("\t").lstrip(" "))
    return ("\t" * leading_tabs + core).rstrip().removesuffix(_SOFT_BREAK).rstrip()


def _tidy_line_spaces(text: str) -> str:
    """No spaces at either side of a newline, and each line tidied."""
    text = _RE_SPACE_BEFORE_NEWLINE.sub("\n", text)
    text = _RE_SPACE_AFTER_NEWLINE.sub("\n", text)
    return "\n".join(_tidy_line(line) for line in text.split("\n"))


def _cap_first(line: str) -> str:
    """Capitalize the first word of a line, or leave the line alone.

    Only the *first* thing on the line is a candidate, so a line opening
    with a number or symbol is left as it is: "2024 was a good year", not
    "2024 Was a good year".
    """
    i = 0
    # Opening quotes and brackets aren't the word — step over them.
    while i < len(line) and (line[i].isspace() or line[i] in _LINE_OPENERS):
        i += 1
    if i >= len(line) or not line[i].isalpha():
        return line
    # An address or URL is written the way the user said it, not sentence-cased.
    if _RE_ADDRESSY.match(line[i:]):
        return line
    return line[:i] + line[i].upper() + line[i + 1:]


def _lines(text: str) -> list:
    """The lines of *text*, a dictated line break ending one as a newline does."""
    return text.replace(LINE_BREAK, "\n").split("\n")


def _finish_line(line: str, auto_period: bool, last: bool) -> str:
    """One line with a capital at its start and after each sentence ender,
    and, with *auto_period*, a full stop at its end."""
    line = _cap_first(line)
    if auto_period:
        # A comma before a line break was said on purpose ("Dear John,"), so
        # only a comma that ends the whole utterance becomes the full stop.
        if last:
            line = _RE_TRAILING_COMMA.sub(".", line)
        if line.strip() and not _ends_punctuated(line) and not line.rstrip().endswith(","):
            line = line.rstrip() + "."
    return _RE_CAP_AFTER_ENDER.sub(lambda m: m.group(1) + m.group(2).upper(), line)


def _capitalize_and_end_lines(text: str, auto_period: bool) -> str:
    """Every line finished by _finish_line. Newlines come back as dictated
    line breaks."""
    lines = _lines(text)
    return LINE_BREAK.join(_finish_line(line, auto_period, i == len(lines) - 1)
                           for i, line in enumerate(lines))


def _restore_protected_words(text: str) -> str:
    """The words _protect_command_words held, and a soft break's three spaces."""
    for placeholder, word in (*LITERAL_RESTORE.items(), (_SOFT_BREAK, "   ")):
        text = text.replace(placeholder, word)
    return _RE_GERMAN_HELD.sub(_give_back_german, text)


def _finish_addresses(text: str) -> str:
    """Spoken addresses the earlier passes left apart, joined: "ron at
    gmail. com" and "john@gmail. Com" are both john's address."""
    text = _RE_EMAIL_AT_WORD.sub(_join_spaced_email, text)
    return _fix_email_spacing(text)


def normalize_text(text: str, auto_period: bool = True, lang: str = "en") -> str:
    """Spoken punctuation -> symbols; tidy punctuation; auto-capitalize; keep newlines/tabs.

    auto_period mirrors the "Ensure period at end of sentences" preference;
    *lang* is the command language, "de" adding the German mark words.
    It is honoured *here* as well as in append_auto_punct: this pass ends every
    line with a full stop and runs first, so otherwise unchecking the
    preference would do nothing.

    The steps run in this order, each relying on the one before.
    """
    if not text:
        return text
    text = _join_spoken_addresses(text)
    # Everything inside "open quote ... close quote" is kept as said.
    text, quoted_sections = _hold_quoted(text)
    text = _protect_command_words(text, lang)
    text = _spoken_marks_to_symbols(text, lang)
    text = _RE_GERMAN_PAIR.sub("\u201e\\1" + _GERMAN_CLOSE, text)
    text = _tidy_punctuation(text)
    # Times before the spacing and capitals, which would otherwise read the
    # "m." of "a. m." as a sentence end ("meet at 9 a. m. Tomorrow").
    text = _join_letter_numbers(format_times(text))
    text = _space_sentences(text).replace(_GERMAN_CLOSE, "\u201c")
    text = _tidy_line_spaces(text)
    # Before the capitals, so they see "I'm" already right.
    text = _RE_STANDALONE_I.sub("I", text)
    text = _restore_quoted(text, quoted_sections)
    text = _capitalize_and_end_lines(text, auto_period)
    text = _restore_protected_words(text)
    # The capitals ran while a protected word was a placeholder (already a
    # capital), so a line that starts with one ("return to sender") gets
    # its capital now.
    text = LINE_BREAK.join(_cap_first(line) for line in _lines(text))
    # Addresses whole first: the mark after a closing quote stays outside
    # one ("ron at example. com" is only an address now).
    return _RE_PUNCT_OUTSIDE_QUOTE.sub(_mark_inside_quote, _finish_addresses(text))


def plain_for_app(text: str, kind: str) -> str:
    """*text* as a terminal or code editor wants it ("Plain text in terminals
    and code editors"): no period at the very end (the engines add one
    themselves), and in a terminal no capital at the start, so "git status"
    isn't typed as "Git status.". Periods between sentences, an ellipsis, a
    question mark and an all-caps word ("PATH") stay. Any other *kind*
    leaves the text as it is."""
    if kind not in ("terminal", "code") or not text:
        return text
    body = text.rstrip(" ")
    tail = text[len(body):]
    if body.endswith(".") and not body.endswith(".."):
        body = body[:-1]
    if kind == "terminal" and body[:1].isupper() and not body[1:2].isupper():
        body = body[0].lower() + body[1:]
    return body + tail


# A word that holds ß or ẞ, for swiss_spelling.
_RE_SHARP_S_WORD = re.compile(r"\w*[ßẞ]\w*")


def _swiss_word(m: re.Match) -> str:
    word = m.group()
    # In an all-caps word ("STRAßE") the sharp s stands for capitals too.
    letters = [c for c in word if c.isalpha() and c not in "ßẞ"]
    upper = bool(letters) and all(c.isupper() for c in letters)
    return word.replace("ẞ", "SS").replace("ß", "SS" if upper else "ss")


def swiss_spelling(text: str) -> str:
    """Swiss Standard German spelling: ß becomes ss, and ẞ becomes SS."""
    if "ß" not in text and "ẞ" not in text:
        return text
    return _RE_SHARP_S_WORD.sub(_swiss_word, text)


def safe_to_lowercase_first_word(text: str, your_words=()) -> bool:
    """True when the leading capital is Whisper's sentence-start capital and
    nothing more, so undoing it is safe.

    Some words are capitalized for reasons that have nothing to do with
    where the sentence begins, and lowercasing them corrupts real text:

      * the pronoun "I" (and I'm / I'll / I've / I'd), which would become "i"
      * acronyms, where only the first letter would change: "NASA" -> "nASA"
      * one of *your_words* (Your Words), already spelled the user's way
        ("TuxTalk", "New York", "TuxTalk's")

    A name like "Anna" can't be told from a sentence start (Whisper
    capitalizes both), so unless it's one of *your_words* this returns True
    for it and the name may be lowercased: a known limitation, not a guess.
    """
    first = text.split(maxsplit=1)[0] if text.split() else ""
    if not first:
        return False
    # The pronoun "I", bare or contracted.
    stripped = first.rstrip(".,;:!?")
    if stripped == "I" or stripped.startswith(("I'", "I\u2019")):     # I'm, I’ll
        return False
    # Acronyms: more than one capital and no lowercase letters ("NASA", "USA").
    letters = [c for c in stripped if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return False
    if your_words:
        from .word_spelling import starts_with_word
        return not starts_with_word(text, your_words)
    return True
