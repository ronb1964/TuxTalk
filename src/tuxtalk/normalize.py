"""
Spoken punctuation and commands to text: "comma" becomes ",", "new line" a
line-break marker, and the result gets tidy punctuation, spacing and capitals.
"""
import re

# A line break in a dictation ("new line"). Typed, it's a Shift+Enter: a plain
# Enter would send a chat message or run a terminal's half-typed command.
LINE_BREAK = "\xa7SHIFT_ENTER\xa7"
# "soft break": three spaces, held as this until the space tidy is over,
# which would make them one.
_SOFT_BREAK = "\xa7SOFT_BREAK\xa7"

# =====================================================================
# Precompiled regex patterns — compiled once at import time.
# This avoids recompiling 40+ patterns on every dictation call.
# =====================================================================

# --- 0) Quoted text protection ---
_RE_QUOTED_TEXT = re.compile(
    r'\b(?:open\s+quotes?)[,.\s]+(.*?)[,.\s]+(?:close\s+quotes?)\b',
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


# --- 0.1) Literal word escapes ---
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
}

# Restore mapping: each placeholder → its original spoken word (1:1, lossless)
LITERAL_RESTORE = {ph: word for word, ph in LITERAL_REPLACEMENTS.items()}

# Precompile literal escape patterns: "literal <word>" or "the word <word>"
_LITERAL_PATTERNS = [
    (re.compile(rf"\b(?:literal|the\s+words?)\s+{re.escape(word)}\b", re.IGNORECASE), placeholder)
    for word, placeholder in LITERAL_REPLACEMENTS.items()
]

# --- 0.2) Context-aware protection for command words used as English nouns ---
# Words like "period", "comma", "return", "dash", "quote" are command triggers
# in dictation, but they are also normal English nouns in phrases like "period
# of time", "in return", "a dash of salt", "the comma operator". Without this
# pass, those phrases get corrupted: "period of time" → "period. Of time".
#
# Each pattern captures surrounding context and substitutes a placeholder for
# just the protected word. Same mechanism as _LITERAL_PATTERNS — placeholders
# are restored to their original words by the pass at step 12.
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

    # "[article/adj/possessive] quote"
    (re.compile(
        r"\b(a|an|the|this|that|my|his|her|its|our|their|your|every|each|"
        r"any|some|famous|great|good|nice|important|brief|long|short|"
        r"memorable|favorite|powerful|funny|inspiring)\s+quote\b",
        re.IGNORECASE,
    ), r"\1 __LIT_QUOTE__"),

    # "ended with a question mark", "add a comma": with an article the mark
    # is being talked about, not dictated. Nobody dictates "a question mark"
    # to end a sentence (without this, "ended with a question mark" typed
    # "ended with a?").
    (re.compile(r"\b(a|an|the|this|that|another|no|one)\s+question\s+mark\b",
                re.IGNORECASE), r"\1 __LIT_QMARK__"),
    (re.compile(r"\b(a|an|the|this|that|another|no|one)\s+exclamation\s+point\b",
                re.IGNORECASE), r"\1 __LIT_EPOINT__"),
    (re.compile(r"\b(a|an|the|this|that|another|no|one)\s+exclamation\s+mark\b",
                re.IGNORECASE), r"\1 __LIT_EPOINT2__"),
    (re.compile(r"\b(a|an|the|this|that|another|no|one)\s+comma\b",
                re.IGNORECASE), r"\1 __LIT_COMMA__"),

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

# --- 1) Multi-word spoken punctuation → symbol ---
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
    (re.compile(r"[, ]*\bpress\s+tab\b(?!\s+key\b)[,.:; ]*", re.IGNORECASE), "\t"),
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
    (re.compile(r"\bopen\s+quote\b|\bopen\s+quotes\b", re.IGNORECASE), "\u201c"),
    (re.compile(r"\bclose\s+quote\b|\bclose\s+quotes\b", re.IGNORECASE), "\u201d"),
]

# --- 2) Single-word spoken punctuation → symbol ---
# A bare "quote" opens or closes depending on its place: _pair_spoken_quotes.
_SPOKEN_QUOTE = "\x00QUOTE\x00"
_SINGLE_WORD_REPLACEMENTS = [
    (re.compile(r"\bcomma\b,?", re.IGNORECASE), ","),
    (re.compile(r"\bsemicolon\b;?", re.IGNORECASE), ";"),
    (re.compile(r"\bcolon\b:?", re.IGNORECASE), ":"),
    (re.compile(r"\bperiod\b\.?", re.IGNORECASE), "."),
    (re.compile(r"\bapostrophe\b", re.IGNORECASE), "'"),
    (re.compile(r"\bquote\b", re.IGNORECASE), _SPOKEN_QUOTE),
    # "em dash" MUST run before the bare hyphen/dash rule — otherwise
    # "em dash" is rewritten to "em -" first and this pattern never matches.
    (re.compile(r"\bem\s*dash\b", re.IGNORECASE), "—"),
    # A spoken hyphen joins the words beside it right away ("well dash
    # known" -> "well-known"). Only spaces are taken, so a tab stays, and a
    # " - " the engine wrote for a pause is never mistaken for one.
    (re.compile(r" *\b(?:hyphen|dash)\b *", re.IGNORECASE), "-"),
]

def _pair_spoken_quotes(text: str) -> str:
    """Each bare "quote" said, as an opening and then a closing smart quote
    in turn ("he said quote hello quote"), so step 3 tidies their spaces as
    it does an "open quote". A last one without a partner is a plain ".
    """
    parts = text.split(_SPOKEN_QUOTE)
    said = len(parts) - 1
    marks = ["\u201d" if i % 2 else "\u201c" for i in range(said)]
    if said % 2:
        marks[-1] = '"'
    return "".join(part + mark for part, mark in zip(parts, marks + [""]))


# --- 3) Quotes/brackets spacing ---
_RE_OPEN_BRACKET_SPACE = re.compile(r"(\(|\[|\{)\s+")
_RE_SPACE_CLOSE_BRACKET = re.compile(r"\s+(\)|\]|\})")
_RE_OPEN_SMART_QUOTE_SPACE = re.compile(r"(\u201c)\s+")
_RE_SPACE_CLOSE_SMART_QUOTE = re.compile(r"\s+(\u201d)")
_RE_OPEN_QUOTE_COMMA = re.compile(r"\u201c\s*,+")
_RE_COMMA_CLOSE_QUOTE = re.compile(r",+\s*\u201d")

# --- 4) Space before punctuation ---
_RE_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,;:.?!…—])")

# --- 5) Normalize punctuation combos ---
_RE_WEAK_BEFORE_STRONG = re.compile(r"[,;:]\s*([.?!…])")
_RE_PERIOD_AFTER_BANG_Q = re.compile(r"([!?])\s*\.")
_RE_COMMA_AFTER_BANG_Q = re.compile(r"([!?])\s*,")
_RE_COMMAS_BEFORE_BANG_Q = re.compile(r",+\s*([!?])")
_RE_COMMA_BEFORE_PERIOD = re.compile(r",\s*\.")
_RE_MULTIPLE_COMMAS = re.compile(r",\s*,+")
_RE_DASH_COLON = re.compile(r"-\s*:")
_RE_COLON_DASH = re.compile(r":\s*-")

# --- 6) Collapse punctuation runs ---
_RE_MANY_DOTS = re.compile(r"\.{4,}")
_RE_DOUBLE_DOT = re.compile(r"(?<!\.)\.\.(?!\.)")
_RE_REPEATED_BANG_Q = re.compile(r"([!?])\1+")

# --- 7) Space after sentence enders ---
_RE_SPACE_AFTER_ENDER = re.compile(r"([.?!…])(?!\s|$|[\u201d\)\]\}])")

# --- 8) Em dash spacing ---
_RE_EM_DASH_SPACE = re.compile(r"\s*—\s*")

# --- 9) Tidy spaces around newlines ---
_RE_SPACE_BEFORE_NEWLINE = re.compile(r"[ \t]+\n")
_RE_SPACE_AFTER_NEWLINE = re.compile(r"\n[ ]+")
_RE_LEADING_PUNCT = re.compile(r"^[\s]*[\.,;:!?]+(?!…)")
_RE_MULTI_SPACE = re.compile(r"[ ]{2,}")

# --- 10) Capitalization ---
_RE_TRAILING_COMMA = re.compile(r",$")
# What counts as "this line is already punctuated". Colon and semicolon are
# included: a dictated "colon" is deliberate punctuation and must not get a
# full stop straight after it ("the plan:."). Closing
# quotes and brackets after the mark are stepped over, since step 11 moves
# the full stop inside a closing quote ("Hello.”" is finished, not "Hello.”.").
_RE_NO_END_PUNCT = re.compile(r"[.?!…:;][\"'”’)\]}]*$")
# Negative lookbehind: don't capitalize after single-letter abbreviations
# ("e.g. that" / "U.S. economy" must not become "e.g. That" / "U.S. Economy").
_RE_CAP_AFTER_ENDER = re.compile(r"(?<![A-Za-z]\.[A-Za-z])([.?!…]\s+)([a-z])")
# Standalone lowercase "i" → "I" (also catches "i'll", "i'm", "i've", "i'd"
# because the apostrophe is a word boundary). Case-sensitive: an existing
# "I" is left alone. Words like "in", "it", "iPad" are not matched because
# they have no word boundary on the right side of the "i".
# The negative lookahead keeps "i.e." intact: the dot is a word boundary, so
# \bi\b alone would match its first letter and write "I.e." mid-sentence.
_RE_STANDALONE_I = re.compile(r"\bi\b(?!\.\s*e\.)")

# --- 11) Smart quote punctuation placement ---
_RE_PUNCT_OUTSIDE_QUOTE = re.compile(r"\u201d( ?)([!?,;:.])")

# --- 14) Email/URL formatting ---
# Country endings (.no .it .my .in .us ...) are also words that start
# sentences, so they only count written joined to the domain ("example.de");
# with spaces around the dot ("gmail. com") only endings that can't be words
# do. Otherwise "Look at this. My car" would become "Look@this.My car".
_EMAIL_TLDS_SAFE = "com|org|net|edu|gov|io"
_EMAIL_TLDS_ALL = (_EMAIL_TLDS_SAFE + "|co|uk|ca|de|fr|us|au|jp|cn|in|br|mx|ru|kr|es|it|nl|se|"
                   "no|fi|pl|cz|hu|ro|gr|pt|ie|nz|za|ae|il|tr|th|vn|ph|id|my|sg|hk|tw")
_EMAIL_NAME = r"(?<![\w'’])([a-zA-Z0-9._-]+)"
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
    return m.group(0) if name.lower() in _NOT_EMAIL_NAMES else f"{name}@{domain}".lower()


def _join_spaced_email(m):
    """"ron at gmail. com" -> "ron@gmail.com", unless "ron" is an ordinary word."""
    name, domain, tld = m.groups()
    return m.group(0) if name.lower() in _NOT_EMAIL_NAMES else f"{name}@{domain}.{tld}".lower()


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


# --- Guards for the spacing (7) and capitalization (10) passes ---
# Characters that can precede the first word of a line without being the word:
# straight and smart quotes, and opening brackets.
_LINE_OPENERS = "\"'“‘([{"

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

# --- 6.5) Time formatting ---
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
    """Insert a space after a sentence ender — except inside numbers,
    single-letter abbreviations and filenames, where the period is part of
    the token: '3.5', '$19.99', 'U.S.', 'e.g.', 'a.m.', 'report.pdf' must
    not be split apart."""
    ch = m.group(1)
    if ch == ".":
        s, i = m.string, m.start(1)
        prev = s[i - 1] if i > 0 else ""
        prev2 = s[i - 2] if i > 1 else ""
        nxt = s[i + 1] if i + 1 < len(s) else ""
        nxt2 = s[i + 2] if i + 2 < len(s) else ""
        # Decimal/price/version numbers: digit.digit
        if prev.isdigit() and nxt.isdigit():
            return ch
        # Single-letter abbreviations: e.g., i.e., U.S., a.m.
        # (single letter before the dot, letter+dot after it)
        if prev.isalpha() and not prev2.isalpha() and nxt.isalpha() and nxt2 == ".":
            return ch
        # Filenames: the dot binds a name to its extension, so splitting here
        # would invent a sentence break and the capitalization pass would then
        # upper-case the extension ("report.pdf" -> "report. Pdf").
        if (prev.isalnum() or prev in "_-") and _RE_FILE_EXT.match(s, i):
            return ch
        # Domains written joined ("example.com", "a.co.uk"): same reason.
        if (prev.isalnum() or prev == "-") and _RE_JOINED_TLD.match(s, i):
            return ch
    return ch + " "


# Trailing line-break markers (possibly repeated) at the end of an utterance.
_RE_TRAILING_BREAKS = re.compile(r"(?:\s*(?:" + re.escape(LINE_BREAK) + r"))+\s*$")


def _ends_punctuated(text: str) -> bool:
    """Same rule as the per-line pass in normalize_text, so the two agree."""
    return bool(_RE_NO_END_PUNCT.search(text.rstrip()))


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


# =====================================================================
# Main normalization function
# =====================================================================

def normalize_text(text: str, auto_period: bool = True) -> str:
    """Spoken punctuation -> symbols; tidy punctuation; auto-capitalize; keep newlines/tabs.

    auto_period mirrors the "Ensure period at end of sentences" preference.
    It is honoured *here* as well as in append_auto_punct: this pass ends every
    line with a full stop and runs first, so otherwise unchecking the
    preference would do nothing.
    """
    if not text:
        return text

    # --- 0) Handle quoted text - preserve everything inside quotes as literal ---
    # A spoken address written joined ("ron at example.de") is joined first,
    # before the space-after-period pass can split it. Spoken dots go first.
    text = _RE_SPOKEN_DOT_DOMAIN.sub(_join_spoken_dots, text)
    text = _RE_EMAIL_JOINED.sub(_join_email, text)
    text, quoted_sections = _hold_quoted(text)

    # --- 0.1) Escape sequences for literal words ---
    for pat, placeholder in _LITERAL_PATTERNS:
        text = pat.sub(placeholder, text)

    # --- 0.2) Protect command words used as English nouns ("period of time",
    #          "in return", "a dash of salt", "comma operator", etc.) ---
    for pat, repl in _CONTEXT_PROTECT_PATTERNS:
        text = pat.sub(repl, text)

    # --- 1) Multi-word replacements first (order matters) ---
    for pat, repl in _MULTI_WORD_REPLACEMENTS:
        text = pat.sub(repl, text)

    # --- 2) Single-word replacements ---
    for pat, repl in _SINGLE_WORD_REPLACEMENTS:
        text = pat.sub(repl, text)
    text = _pair_spoken_quotes(text)

    # --- 3) Quotes/brackets spacing ---
    text = _RE_OPEN_BRACKET_SPACE.sub(r"\1", text)
    text = _RE_SPACE_CLOSE_BRACKET.sub(r"\1", text)
    text = _RE_OPEN_SMART_QUOTE_SPACE.sub(r"\1", text)
    text = _RE_SPACE_CLOSE_SMART_QUOTE.sub(r"\1", text)
    text = _RE_OPEN_QUOTE_COMMA.sub("\u201c", text)
    text = _RE_COMMA_CLOSE_QUOTE.sub("\u201d", text)

    # --- 4) Remove spaces before punctuation ---
    text = _RE_SPACE_BEFORE_PUNCT.sub(r"\1", text)

    # --- 5) Normalize punctuation combos ---
    text = _RE_WEAK_BEFORE_STRONG.sub(r"\1", text)
    text = _RE_PERIOD_AFTER_BANG_Q.sub(r"\1", text)
    text = _RE_COMMA_AFTER_BANG_Q.sub(r"\1", text)
    text = _RE_COMMAS_BEFORE_BANG_Q.sub(r"\1", text)
    text = _RE_COMMA_BEFORE_PERIOD.sub(".", text)
    text = _RE_MULTIPLE_COMMAS.sub(",", text)
    text = _RE_DASH_COLON.sub(":", text)
    # The spoken dash took the space after it, so give the colon one back.
    text = _RE_COLON_DASH.sub(
        lambda m: ": " if m.string[m.end():m.end() + 1].isalnum() else ":", text)

    # --- 6) Collapse punctuation runs; handle ellipsis ---
    text = _RE_MANY_DOTS.sub("…", text)
    text = _RE_DOUBLE_DOT.sub(".", text)
    text = _RE_REPEATED_BANG_Q.sub(r"\1", text)
    text = text.replace("...", "…")

    # --- 6.5) Fix time formatting ---
    # "11. 30 p. m." → "11:30 PM", "3:15 a. m." → "3:15 AM", etc.
    # Before the spacing and capitalization passes, which would otherwise read
    # the "m." of "a. m." as a sentence end and capitalize the next word
    # ("meet at 9 a. m. tomorrow" → "9 AM Tomorrow").
    text = _RE_TIME_FORMAT.sub(_fix_time_ampm, text)
    text = _join_letter_numbers(text)

    # --- 7) Ensure one space after sentence enders ---
    # (callable repl skips decimals like 3.5 and abbreviations like U.S.)
    text = _RE_SPACE_AFTER_ENDER.sub(_space_after_ender_repl, text)

    # --- 8) Em dash spacing (spoken hyphens were joined in step 2) ---
    text = _RE_EM_DASH_SPACE.sub(" — ", text)

    # --- 9) Tidy spaces around newlines ---
    text = _RE_SPACE_BEFORE_NEWLINE.sub("\n", text)
    text = _RE_SPACE_AFTER_NEWLINE.sub("\n", text)

    lines = []
    for raw_line in text.split("\n"):
        # Strip leading stray punctuation (keep ellipsis and quotes/parens)
        line = _RE_LEADING_PUNCT.sub("", raw_line)

        leading_tabs = len(line) - len(line.lstrip("\t"))
        core = line.lstrip("\t")
        core = core.lstrip(" ")
        core = _RE_MULTI_SPACE.sub(" ", core)
        line = ("\t" * leading_tabs) + core
        lines.append(line.rstrip().removesuffix(_SOFT_BREAK).rstrip())   # nothing after it
    text = "\n".join(lines)

    # Capitalize standalone lowercase "i" (and "i'll", "i'm", "i've", "i'd")
    # to "I". Done before the per-line capitalization pass so cap_first sees
    # an already-correct string and doesn't have to special-case the pronoun.
    text = _RE_STANDALONE_I.sub("I", text)

    # --- 9.5) Restore quoted sections, before capitals and step 11 ---
    text = _restore_quoted(text, quoted_sections)

    # --- 10) Capitalization ---
    def cap_first(s: str) -> str:
        """Capitalize the first word of a line, or leave the line alone.

        Only the *first* thing on the line is a candidate, so a line opening
        with a number or symbol is left as it is: "2024 was a good year", not
        "2024 Was a good year".
        """
        i = 0
        # Opening quotes and brackets aren't the word — step over them.
        while i < len(s) and (s[i].isspace() or s[i] in _LINE_OPENERS):
            i += 1
        if i >= len(s) or not s[i].isalpha():
            return s
        # An address or URL is written the way the user said it, not sentence-cased.
        if _RE_ADDRESSY.match(s[i:]):
            return s
        return s[:i] + s[i].upper() + s[i+1:]

    # Temporarily convert §SHIFT_ENTER§ to newlines for capitalization processing
    temp_text = text.replace(LINE_BREAK, "\n")

    lines = []
    split_lines = temp_text.split("\n")
    last = len(split_lines) - 1
    for idx, line in enumerate(split_lines):
        line = cap_first(line)
        # A comma before a line break was said on purpose ("Dear John,"), so
        # only a comma that ends the whole utterance becomes the full stop.
        if auto_period and idx == last:
            line = _RE_TRAILING_COMMA.sub(".", line)
        # Add period at end of line if no punctuation exists
        if (auto_period and line and line.strip()
                and not _RE_NO_END_PUNCT.search(line.rstrip())
                and not line.rstrip().endswith(",")):
            line = line.rstrip() + "."
        # Capitalize after sentence enders
        line = _RE_CAP_AFTER_ENDER.sub(lambda m: m.group(1) + m.group(2).upper(), line)
        lines.append(line)
    temp_text = "\n".join(lines)

    # Convert newlines back to §SHIFT_ENTER§ markers
    text = temp_text.replace("\n", LINE_BREAK)

    # --- 11) Place sentence punctuation inside closing smart quote ---
    text = _RE_PUNCT_OUTSIDE_QUOTE.sub(r"\2" + "\u201d", text)

    # --- 12) Restore literal tokens, and a soft break's three spaces ---
    for placeholder, word in (*LITERAL_RESTORE.items(), (_SOFT_BREAK, "   ")):
        text = text.replace(placeholder, word)

    # --- 12.5) Re-capitalize line starts after literal restore ---
    # cap_first() ran while protected words were placeholders like
    # "__LIT_RETURN__" (whose first letter is already uppercase), so a line
    # STARTING with a protected word would stay lowercase ("return to sender
    # was..."). Idempotent: lines already capitalized are left untouched.
    temp_text = text.replace(LINE_BREAK, "\n")
    temp_text = "\n".join(cap_first(line) for line in temp_text.split("\n"))
    text = temp_text.replace("\n", LINE_BREAK)

    # --- 13) Fix email/URL formatting ---
    text = _RE_EMAIL_AT_WORD.sub(_join_spaced_email, text)
    text = _fix_email_spacing(text)

    return text


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

    Proper nouns like "Anna" are genuinely ambiguous — Whisper capitalizes them
    both as names and as sentence starts — so they are left alone here and
    remain a known limitation rather than a guess.
    """
    first = text.split(maxsplit=1)[0] if text.split() else ""
    if not first:
        return False
    # The pronoun "I", bare or contracted.
    stripped = first.rstrip(".,;:!?")
    if stripped == "I" or stripped.startswith("I'"):
        return False
    # Acronyms: more than one capital and no lowercase letters ("NASA", "USA").
    letters = [c for c in stripped if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return False
    if your_words:
        from .custom_words import starts_with_word
        return not starts_with_word(text, your_words)
    return True
