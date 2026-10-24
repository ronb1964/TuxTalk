"""Saying "press enter" or "press tab" at the end of a dictation presses
that key.

"See you at noon, press enter" types "See you at noon." and then presses
Enter, so a chat message is sent, a terminal command runs or a form is
submitted, without touching the keyboard. "John press tab" types "John" and
presses Tab, which moves a form on to its next box; a tab character typed or
pasted there would stay in the box. Either command on its own just presses
the key. A spoken "new line" stays Shift+Enter (normalize.py), which starts a
new line without sending anything.

Only the very end of a dictation counts, so "press enter to continue" in the
middle of a sentence is typed as words, and a "press tab" mid-sentence types
a tab character (normalize.py). "literal press enter" types the words too.

"Send each dictation automatically" presses Enter without being asked, so it
holds back when the dictation was plainly cut off ("... and I think and"):
sending half a sentence can't be taken back (auto_send).
"""

import re

from .logger import setup_logger

logger = setup_logger(__name__)

# The keys these commands and select and move by voice (voice_navigation.py)
# press, by name, as Linux key codes: one table for both ways of typing
# (app._send_chords, libei_ctypes.press_chords). Letters aren't in it: their
# keys depend on the keyboard layout.
KEY_CODES = {"CTRL": 29, "SHIFT": 42, "LEFT": 105, "RIGHT": 106, "UP": 103,
             "DOWN": 108, "HOME": 102, "END": 107, "ENTER": 28, "TAB": 15}

# "press enter", "hit enter", "press the enter key", "press return", "press
# tab". Speech engines often capitalise and punctuate it ("... noon. Press
# Enter."). "literal" or "the word" before it types the words.
_TRAILING = re.compile(
    r"(?P<lead>^|[\s,;:.!?]+)"
    r"(?P<literal>(?:literal|the\s+words?)\s+)?"
    r"(?:press|hit)\s+(?:the\s+)?(?P<key>enter|return|tab)(?:\s+key)?"
    r"[\s.!?,]*$",
    re.IGNORECASE,
)


def split_key_command(raw: str) -> tuple[str, str]:
    """(the dictation without a closing "press enter" or "press tab", the key
    said: "ENTER", "TAB" or "").

    The words before Enter keep their own sentence-ending punctuation
    ("noon."), but a comma or colon that only led into the command is dropped.
    Before Tab a period goes too: the engine ends "John" with one, and a name
    or a number in a form box doesn't; a "?" or "!" was said and stays.
    """
    match = _TRAILING.search(raw) if raw else None
    if not match:
        return raw, ""
    key = "TAB" if match.group("key").lower() == "tab" else "ENTER"
    if match.group("literal"):
        # Typed as words, without "literal", like "literal new line". A
        # "press tab" keeps it: without it normalize.py would type a tab.
        if key == "TAB":
            return raw, ""
        return raw[:match.start("literal")] + raw[match.end("literal"):], ""
    lead = match.group("lead").strip()
    ending = next((c for c in lead if c in ("!?" if key == "TAB" else ".!?")), "")
    return (raw[:match.start()].rstrip(" ,;:") + ending).strip(), key


# Words an English sentence doesn't end on: letting go of the key after one
# means the dictation was cut off. Kept short on purpose. "so", "to", "that"
# and "then" are left out because "I think so", "I'd love to", "I like that"
# and "see you then" are whole sentences.
_UNFINISHED = frozenset({
    "and", "but", "or", "because", "the", "a", "an", "of", "with", "if",
    "my", "your", "our", "their", "um", "uh",
})


def ends_unfinished(text: str) -> str:
    """The word a cut-off dictation ends on ("and"), or "" when it looks whole."""
    words = text.split()
    if not words:
        return ""
    last = words[-1].strip(".,;:!?\"'“”‘’").lower()
    return last if last in _UNFINISHED else ""


def auto_send(words: str, in_document: bool) -> tuple[bool, str]:
    """For "Send each dictation automatically": (whether these words are
    sent, the word they were cut off on or "").

    In a text editor Enter only starts a new line, so nothing is sent there,
    but a cut-off dictation is still reported: it gets no period either."""
    cut_off = ends_unfinished(words)
    if in_document:
        logger.info("Not sending: Enter only starts a new line in this app")
        return False, cut_off
    if cut_off:
        # The key came up mid-sentence: type it, but don't send half a
        # message. Saying "press enter" always sends.
        logger.info(f'Held back the Enter: the dictation ended on "{cut_off}"')
    return not cut_off, cut_off
