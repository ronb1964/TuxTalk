"""Every whole-recording command phrase, read by one parser.

parse() says what a dictation is: select and move's Command, an open-app
request, an everyday key, a window request, a web address or a web search.
Like the other command parsers it is pure and whole-recording only, so a
sentence that merely contains a command is typed as text.

The order matters, first match wins:
- select and move first (when on), so "go to the end" and "select all" stay
  its own; "go to the top/bottom" are keys only when it is off;
- web search before the rest, since it takes any words after its prefix;
- keys before windows, so "close tab" is a key and not an app named "tab";
- addresses before open-app, so "open youtube.com" isn't an app.
"""
import re
from dataclasses import dataclass

from . import voice_navigation
from .app_commands import OpenRequest, app_name, parse as parse_open
from .undo import NUMBER_WORDS
from .voice_navigation import MAX_COUNT, normalize


@dataclass(frozen=True)
class KeyRequest:
    name: str            # a KEY_NAMES entry: "new tab", "scroll down"
    count: int = 1       # scrolling only


@dataclass(frozen=True)
class WindowRequest:
    action: str          # "close", "minimize", "maximize", "unmaximize", "switch"
    app: OpenRequest | None = None   # None: the focused window
    every: bool = False  # "close all Firefox"


@dataclass(frozen=True)
class AddressRequest:
    url: str


@dataclass(frozen=True)
class SearchRequest:
    words: str           # as said, case kept


KEY_PHRASES = {
    **dict.fromkeys(("new tab", "open new tab", "open a new tab"), "new tab"),
    **dict.fromkeys(("close tab", "close the tab", "close this tab"), "close tab"),
    "next tab": "next tab",
    "previous tab": "previous tab",
    **dict.fromkeys(("reopen tab", "reopen the tab", "reopen closed tab",
                     "reopen the closed tab"), "reopen tab"),
    "go back": "go back",
    "go forward": "go forward",
    **dict.fromkeys(("reload", "refresh", "reload the page", "refresh the page",
                     "reload page", "refresh page"), "reload"),
    "page up": "page up",
    "page down": "page down",
    **dict.fromkeys(("go to the top", "go to top"), "go to the top"),
    **dict.fromkeys(("go to the bottom", "go to bottom"), "go to the bottom"),
    **dict.fromkeys(("find", "find on page"), "find"),
    "save": "save",
    "select all": "select all",
    "copy": "copy",
    "paste": "paste",
    **dict.fromkeys(("escape", "press escape"), "escape"),
}
KEY_NAMES = frozenset(KEY_PHRASES.values()) | {"scroll up", "scroll down"}

_SCROLL = re.compile(r"scroll (up|down)(?: (\d+|" + "|".join(NUMBER_WORDS) + "))?")

_THIS = r"(?:this |the )?window"
_WINDOW_ACTIONS = (
    (re.compile(rf"close {_THIS}"), "close"),
    (re.compile(rf"minimi[sz]e {_THIS}"), "minimize"),
    (re.compile(rf"maximi[sz]e {_THIS}"), "maximize"),
    (re.compile(rf"(?:unmaximi[sz]e|restore) {_THIS}"), "unmaximize"),
)
# Spoken punctuation ("close quote", "close paren") is never a window request.
_PUNCTUATION = {"quote", "unquote", "paren", "parenthesis", "parentheses", "bracket",
                "brackets", "brace", "braces"}

_SEARCH = re.compile(r"^\s*web\s+search\s*[,:]?\s+(?P<words>.+?)[\s.!?]*$", re.IGNORECASE)
_GO_TO = re.compile(r"^\s*(?:go\s+to|open)\s+(?P<rest>.+)$", re.IGNORECASE)
_LITERAL = re.compile(r"^\s*literal\s+(?P<rest>.+)$", re.IGNORECASE)

ADDRESS_ENDINGS = frozenset({
    "com", "org", "net", "io", "gov", "edu", "app", "dev", "co", "uk", "de", "ca", "au",
    "us", "fr", "nl", "it", "es", "eu", "info", "tv", "me", "ai", "ch", "at", "be", "se",
    "no", "dk", "fi", "pl", "ie", "nz", "in", "jp", "br", "mx",
})
_ADDRESS = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?")
_SCHEME = re.compile(r"^https?://", re.IGNORECASE)


def address(said: str) -> str | None:
    """The web address *said* is ("youtube dot com" -> https://youtube.com),
    or None. Only the scheme and the host are lowercased: a path or a query
    may be case-sensitive (a reset link's token)."""
    text = said.strip().rstrip(".!?,")
    scheme = _SCHEME.match(text)
    text = text[scheme.end():] if scheme else text
    text = re.sub(r"(?i)\s+dot\s+", ".", text)
    text = re.sub(r"\s*\.\s*", ".", text)
    text = re.sub(r"(?i)^www ", "www.", text)
    host, slash, path = text.partition("/")
    text = host.lower() + slash + path
    if not _ADDRESS.fullmatch(text):
        return None
    if host.lower().split(".")[-1] not in ADDRESS_ENDINGS:
        return None
    return (scheme.group().lower() if scheme else "https://") + text


def _key(said: str) -> KeyRequest | None:
    if name := KEY_PHRASES.get(said):
        return KeyRequest(name)
    m = _SCROLL.fullmatch(said)
    if not m:
        return None
    raw = m.group(2)
    count = 1 if raw is None else (int(raw) if raw.isdigit() else NUMBER_WORDS[raw])
    return KeyRequest(f"scroll {m.group(1)}", count) if 1 <= count <= MAX_COUNT else None


def _window(said: str) -> WindowRequest | None:
    """The window request *said* is. "close <name>" and "switch to <name>" accept
    any short name here: the caller finds the app and types the words if none fits.
    """
    for pattern, action in _WINDOW_ACTIONS:
        if pattern.fullmatch(said):
            return WindowRequest(action)
    words = said.split()
    if len(words) < 2 or _PUNCTUATION.intersection(words):
        return None
    if words[0] == "close" and words[1] in ("all", "every"):
        # "Close all." names no app, never one whose name is "all".
        rest = words[3:] if words[2:3] == ["the"] else words[2:]
        app = app_name(rest) if rest else None
        return WindowRequest("close", app, every=True) if app else None
    if words[0] == "close":
        app = app_name(words[1:])
        return WindowRequest("close", app) if app else None
    if words[:2] == ["switch", "to"]:
        app = app_name(words[2:])
        return WindowRequest("switch", app) if app else None
    return None


def _address_request(raw: str) -> AddressRequest | None:
    m = _GO_TO.match(re.sub(r"(?i)^\s*please\s+|\s+please\W*$", "", raw))
    url = address(m.group("rest")) if m else None
    return AddressRequest(url) if url else None


def parse(text: str, *, navigation: bool, actions: bool) -> (
        voice_navigation.Command | OpenRequest | KeyRequest | WindowRequest
        | AddressRequest | SearchRequest | None):
    """What *text* commands, or None when it's dictation to type.

    *navigation* is select and move being on, *actions* the voice commands.
    """
    if navigation and (command := voice_navigation.parse(text)):
        return command
    if not actions:
        return None
    if m := _SEARCH.match(text):
        return SearchRequest(m.group("words"))
    said = normalize(text)
    return _key(said) or _window(said) or _address_request(text) or parse_open(text)


def literal_words(raw: str, *, navigation: bool, actions: bool) -> str:
    """The words to type for "literal <command>", else *raw* unchanged."""
    m = _LITERAL.match(raw)
    if m and parse(m.group("rest"), navigation=navigation, actions=actions):
        return m.group("rest")
    return raw
