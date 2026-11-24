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
a tab character (normalize.py). "press the tab key" and "press the enter key"
count at the end too, so "To indent, press the tab key." presses Tab, while
mid-sentence they're typed as words. "literal press enter" types the words.

"Send each dictation automatically" presses Enter without being asked, so it
holds back when the dictation was plainly cut off ("... and I think and"):
sending half a sentence can't be taken back (auto_send).
"""

import re

from .logger import setup_logger

logger = setup_logger(__name__)

# The keys these commands and select and move by voice (voice_navigation.py)
# press, by name, as Linux key codes: one table for both ways of typing
# (keyboard_output.send_chords, libei_ctypes.press_chords). Letters aren't in it: their
# keys depend on the keyboard layout.
KEY_CODES = {"CTRL": 29, "SHIFT": 42, "ALT": 56, "LEFT": 105, "RIGHT": 106, "UP": 103,
             "DOWN": 108, "HOME": 102, "END": 107, "PAGEUP": 104, "PAGEDOWN": 109,
             "ENTER": 28, "TAB": 15, "ESC": 1, "F5": 63, "DELETE": 111}

# Where the letters sit on a US keyboard: the Flatpak's keyboard types them
# there (libei_ctypes), and a shortcut's letter goes there on a layout without
# Latin letters (keyboard_layout.shortcut_keycode).
US_LETTER_KEYS = dict(zip("abcdefghijklmnopqrstuvwxyz",
                          (30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50,
                           49, 24, 25, 16, 19, 31, 20, 22, 47, 17, 45, 21, 44), strict=True))

# "press enter", "hit enter", "press the enter key", "press return", "press
# tab". Speech engines often capitalise and punctuate it ("... noon. Press
# Enter."), and a short pause after "press" comes out as a comma ("press,
# tab"). "literal" or "the word" before it types the words.
_LITERAL = r"(?:literal|the\s+words?)"
# With German on, either language's word for it does, before either's
# command: "literal Enter drücken" asked for the words as surely.
_GERMAN_LITERAL = r"(?:literal|the\s+words?|wörtlich|das\s+wort|die\s+wörter)"


def _english_trailing(literal: str) -> re.Pattern:
    """The English command at the end, with *literal* the words that keep it
    words."""
    return re.compile(
        r"(?P<lead>^|[\s,;:.!?]+)"
        rf"(?P<literal>{literal}\s+)?"
        r"(?:press|hit),?\s+(?:the\s+)?(?P<key>enter|return|tab)(?:\s+key)?"
        r"[\s.!?,]*$",
        re.IGNORECASE,
    )


# German: "Enter drücken", "Eingabe drücken", "Tab-Taste drücken", the verb
# last as most say it, or first ("drück Enter").
_GERMAN_LEAD = rf"(?P<lead>^|[\s,;:.!?]+)(?P<literal>{_GERMAN_LITERAL}\s+)?"
_GERMAN_KEY = r"(?P<key>enter|eingabe|return|tab|tabulator)(?:[\s-]?taste)?"
_GERMAN_TRAILING = (
    re.compile(_GERMAN_LEAD + r"(?:die\s+)?" + _GERMAN_KEY + r",?\s+drücken[\s.!?,]*$",
               re.IGNORECASE),
    re.compile(_GERMAN_LEAD + r"drück(?:e|en)?,?\s+(?:die\s+)?" + _GERMAN_KEY
               + r"[\s.!?,]*$", re.IGNORECASE),
)
# Each language's patterns, its own first; English is understood in every one.
_TRAILING = _english_trailing(_LITERAL)
_TRAILING_BY_LANGUAGE = {"en": (_TRAILING,),
                         "de": (*_GERMAN_TRAILING, _english_trailing(_GERMAN_LITERAL))}
_TAB_NAMES = frozenset({"tab", "tabulator"})


def split_key_command(raw: str, lang: str = "en") -> tuple[str, str]:
    """(the dictation without a closing "press enter" or "press tab", the key
    said: "ENTER", "TAB" or ""), with *lang*'s words for it understood too
    ("Enter drücken").

    The words before Enter keep their own sentence-ending punctuation
    ("noon."), but a comma or colon that only led into the command is dropped.
    Before Tab a period goes too: the engine ends "John" with one, and a name
    or a number in a form box doesn't; a "?" or "!" was said and stays.
    """
    match = next((m for pattern in _TRAILING_BY_LANGUAGE.get(lang, (_TRAILING,))
                  if raw and (m := pattern.search(raw))), None)
    if not match:
        return raw, ""
    key = "TAB" if match.group("key").lower() in _TAB_NAMES else "ENTER"
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
# Not "with" or "of": a sentence can end on either ("to play around with",
# "what it's made of"), and holding back a finished message looks as if
# sending were off.
_UNFINISHED = frozenset({
    "and", "but", "or", "because", "the", "a", "an", "if",
    "my", "your", "our", "their", "um", "uh",
})
# German's, with voice commands in German. Not "aber", "oder" or "ein", which
# end whole sentences ("Ich will aber!", "Du kommst, oder?", "Ich lade dich
# ein."), nor an article that can be a pronoun ("Ich weiß das.").
_GERMAN_UNFINISHED = _UNFINISHED | {"und", "weil", "dass", "eine", "einen", "des", "mein",
                                     "dein", "unser", "äh", "ähm"}


def ends_unfinished(text: str, lang: str = "en") -> str:
    """The word a cut-off dictation ends on ("and"), or "" when it looks
    whole; with *lang* "de" German's words count too ("und")."""
    words = text.split()
    if not words:
        return ""
    last = words[-1].strip(".,;:!?\"'“”„‘’").lower()
    return last if last in (_GERMAN_UNFINISHED if lang == "de" else _UNFINISHED) else ""


def auto_send(words: str, why_not: str, lang: str = "en") -> tuple[bool, str]:
    """For "Send each dictation automatically": (whether these words are
    sent, the word they were cut off on or "").

    *why_not* is window_classes.why_no_automatic_enter's answer for the
    window: where it gives a reason nothing is sent, but a cut-off dictation
    is still reported, since it gets no period either."""
    cut_off = ends_unfinished(words, lang)
    if why_not:
        logger.info(f"Not sending: {why_not}")
        return False, cut_off
    if cut_off:
        # The key came up mid-sentence: type it, but don't send half a
        # message. Saying "press enter" always sends.
        log_held_back(cut_off)
    return not cut_off, cut_off


def log_held_back(cut_off: str) -> None:
    """Say in the log that the automatic Enter waits: the dictation ended on
    *cut_off* ("and"), a word from a fixed list, never the user's words.
    The VM checks look for this line."""
    logger.info(f'Held back the Enter: the dictation ended on "{cut_off}"')
