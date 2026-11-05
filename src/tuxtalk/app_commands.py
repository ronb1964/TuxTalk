"""Open apps by voice (TuxTalk Labs): "open Firefox", "launch the calculator".

Pure logic, like voice_navigation.py: which dictations are an open command and
what they ask for. Finding the app and starting it are other modules' jobs.

A dictation is a command only when the WHOLE of it is a verb and up to four
more words, so "I opened Firefox yesterday" and "open the door and come in
now" are typed as text. A short sentence like "Start over." is a request
here; it's typed when app_finder finds no app it names. Spoken quotation
("open quote", "close quote") is never a request. "literal open Firefox" is
left alone here: the caller strips "literal" and types the rest.
"""
from dataclasses import dataclass

from .voice_navigation import normalize

VERBS = ("open", "launch", "start")
# Long enough for "visual studio code" or "gnome system monitor"; a longer
# sentence is never taken for an app name.
MAX_NAME_WORDS = 4
_NEW = ("new", "another")
_INSTALL_WORDS = ("flatpak", "snap", "appimage")
# spoken_quotes.py's words: "open quote", "close quote", "end quote", "unquote".
_QUOTATION = {"quote", "unquote"}


@dataclass(frozen=True)
class OpenRequest:
    name: str                # what was said for the app: "firefox", "fire fox"
    new: bool = False        # "new" or "another": start a second one
    install: str = ""        # "", "flatpak", "snap" or "appimage": which copy


def parse(text: str) -> OpenRequest | None:
    """The OpenRequest *text* is, or None when it's dictation to type."""
    words = normalize(text).split()
    if len(words) < 2 or words[0] not in VERBS or _QUOTATION.intersection(words):
        return None
    words = words[1:]
    new = words[0] in _NEW
    if new:
        words = words[1:]
    install = ""
    # Speech models write the AppImage word as one or two.
    if words[-2:] == ["app", "image"]:
        install, words = "appimage", words[:-2]
    elif words and words[-1] in _INSTALL_WORDS:
        install, words = words[-1], words[:-1]
    if not 1 <= len(words) <= MAX_NAME_WORDS:
        return None
    return OpenRequest(" ".join(words), new=new, install=install)
