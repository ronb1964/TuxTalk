"""Custom voice commands, the snippets in Preferences → Words: spoken
phrases replaced in the transcription by what they stand for.

A trigger matches whole words, case-blind, with a number in either spelling
("one" or "1"), and a quoted replacement goes in exactly as written: the
caller keeps it out of normalization with a placeholder (apply), then puts it
back (restore_protected).
"""
import re


def _expand_escapes(replacement: str) -> str:
    """Expand the two escapes the Preferences hint documents, and nothing else.

    Any other backslash stays literal, so a Windows path or a stray \\N in a
    replacement is typed as written.
    """
    return replacement.replace("\\n", "\n").replace("\\t", "\t")


# Whisper is inconsistent about writing a spoken number as a word or a digit; the
# same phrase comes out both ways on different days. Commands match the raw
# transcription, so a trigger containing a number matches either spelling.
#
# Single tokens only, deliberately: compounds like "twenty one" are ambiguous
# (21? 20 then 1?) and pull in a whole number parser for no real benefit.
_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90",
}
_DIGIT_WORDS = {digit: word for word, digit in _NUMBER_WORDS.items()}

# Split into whole words, whole numbers, runs of whitespace, and single other
# characters. Tokenising is what keeps this narrow: "money" is one token and
# never sees the "one" inside it.
_TOKEN_RE = re.compile(r"\d+|[^\W\d]+|\s+|.")


def _canonical_command_key(text: str) -> str:
    """Fold a phrase to the form used for command lookup.

    Case is flattened, whitespace runs collapse, and every number becomes its
    digit form, so "Test Phrase One" and "test phrase 1" arrive at one key.
    Only lookup uses this — dictated text is never rewritten by it.
    """
    out = []
    for token in _TOKEN_RE.findall(text.lower()):
        if token.isspace():
            out.append(" ")
        else:
            out.append(_NUMBER_WORDS.get(token, token))
    return "".join(out)


def _command_pattern(phrase: str) -> str:
    """Build the match pattern for one trigger phrase.

    Word boundaries are applied only at ends that actually start or finish with
    a word character: \\b after "+" requires a following word character, so a
    trigger like "c++" or "#tag" could otherwise never match.

    Number tokens match either spelling, so the trigger fires whichever one
    Whisper produced.
    """
    prefix = r"\b" if phrase[:1].isalnum() or phrase[:1] == "_" else ""
    suffix = r"\b" if phrase[-1:].isalnum() or phrase[-1:] == "_" else ""

    parts = []
    for token in _TOKEN_RE.findall(phrase):
        lowered = token.lower()
        if lowered in _NUMBER_WORDS:
            alt = _NUMBER_WORDS[lowered]
        elif token in _DIGIT_WORDS:
            alt = _DIGIT_WORDS[token]
        else:
            # Whitespace is matched loosely so a double space in either the
            # trigger or the transcription still lines up.
            parts.append(r"\s+" if token.isspace() else re.escape(token))
            continue
        parts.append(f"(?:{re.escape(token)}|{re.escape(alt)})")

    return prefix + "".join(parts) + suffix

def apply(text: str, custom_commands: dict[str, str],
          your_words=()) -> tuple[str, dict[str, str]]:
    """
    Apply user-defined *custom_commands* to the transcribed text.

    Replaces spoken phrases with their configured replacements.
    Uses case-insensitive matching with word boundaries to avoid
    accidental replacements within longer words.

    If a replacement value is wrapped in double quotes (e.g. "Hello, world!"),
    the quotes are stripped and the text is protected from normalization by
    substituting a placeholder token. The caller must restore these tokens
    after normalize_text() runs (restore_protected).

    Returns:
        Tuple of (processed text, protected dict mapping placeholder → literal text)
    """
    if not custom_commands or not text:
        return text, {}

    # A trigger saved as "tux talk website" also fires on "TuxTalk website":
    # with TuxTalk in Your Words, Whisper is steered to hear it joined.
    commands = dict(custom_commands)
    if your_words:
        from .custom_words import fix_spelling
        for phrase, replacement in custom_commands.items():
            commands.setdefault(fix_spelling(phrase, your_words), replacement)

    # Longest phrase first, so "our brand customs llc" wins over the shorter
    # "our brand customs" that is also a prefix of it.
    phrases = sorted((p for p in commands if p), key=len, reverse=True)
    if not phrases:
        return text, {}

    # Keyed on the canonical form so a match found via the other number
    # spelling still resolves. Phrases are longest-first and the first claim on
    # a key wins, so if two triggers fold together ("test 1" and "test one")
    # the longer one keeps it — the same rule the match order follows.
    lookup: dict[str, str] = {}
    for p in phrases:
        lookup.setdefault(_canonical_command_key(p), commands[p])
    combined = "|".join(f"(?:{_command_pattern(p)})" for p in phrases)

    protected: dict[str, str] = {}  # placeholder → literal replacement text
    counter = 0

    def substitute(match):
        """Return the replacement for one matched phrase.

        A function rather than a template string, because re.sub does not
        interpret backslashes in what a function returns, so a Windows path or
        a stray \\N in a user's command can't raise.
        """
        nonlocal counter
        replacement = lookup.get(_canonical_command_key(match.group(0)))
        if replacement is None:  # pragma: no cover — every branch is in lookup
            return match.group(0)

        if len(replacement) >= 2 and replacement.startswith('"') and replacement.endswith('"'):
            # Quoted replacement — inject exactly as written, bypass normalization
            placeholder = f"§CMDLIT_{counter}§"
            counter += 1
            protected[placeholder] = replacement[1:-1]
            return placeholder

        return _expand_escapes(replacement)

    # One pass over the original text, so one command's output is never
    # re-scanned and rewritten by another.
    return re.sub(combined, substitute, text, flags=re.IGNORECASE), protected


def restore_protected(text: str, protected: dict[str, str]) -> str:
    """Put quoted (literal) replacements back after normalization has run.

    Quoted commands promise the text is injected exactly as written, so a
    sentence-ending period that normalization parked directly against one is
    dropped — but only when the literal already ends in something other than a
    letter or digit. That covers a literal supplying its own punctuation
    ("Hello, world!", "Thanks.") or deliberately ending in a space ("/btw "),
    while a literal ending in a word still gets the period the user expects.
    """
    for placeholder, literal in protected.items():
        if literal and not literal[-1].isalnum():
            text = text.replace(placeholder + ".", literal)
        text = text.replace(placeholder, literal)
    return text
