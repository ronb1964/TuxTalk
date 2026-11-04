"""Open apps by voice (TuxTalk Labs): "open Firefox", "launch the calculator".

Pure logic, like voice_navigation.py: which dictations are an open command and
what they ask for. Finding the app and starting it are other modules' jobs.

A dictation is a command only when the WHOLE of it is a verb and an app name,
so "I opened Firefox yesterday" and "open the door and come in now" are typed
as text. "literal open Firefox" is left alone here: the caller strips
"literal" and types the rest.
"""
from dataclasses import dataclass

from .voice_navigation import normalize

VERBS = ("open", "launch", "start")
# Long enough for "visual studio code" or "gnome system monitor", short enough
# that a spoken sentence never counts as an app name.
MAX_NAME_WORDS = 4
_NEW = ("new", "another")
_INSTALL_WORDS = {"flatpak": "flatpak", "snap": "snap", "appimage": "appimage"}


@dataclass(frozen=True)
class OpenRequest:
    name: str                # what was said for the app: "firefox", "fire fox"
    new: bool = False        # "new" or "another": start a second one
    install: str = ""        # "", "flatpak", "snap" or "appimage": which copy


def parse(text: str):
    """The OpenRequest *text* is, or None when it's dictation to type."""
    words = normalize(text).split()
    if len(words) < 2 or words[0] not in VERBS:
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
        install, words = _INSTALL_WORDS[words[-1]], words[:-1]
    if not 1 <= len(words) <= MAX_NAME_WORDS:
        return None
    return OpenRequest(" ".join(words), new=new, install=install)
