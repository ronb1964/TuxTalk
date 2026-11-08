"""Every whole-recording command phrase, read by one parser.

parse() says what a dictation is: select and move's Command, an open-app
request, an everyday key, a desktop, sound, music or system request, a window
request, a web address or a web search.
Like the other command parsers it is pure and whole-recording only, so a
sentence that merely contains a command is typed as text.

The order matters, first match wins:
- select and move first (when on), so "go to the end" and "select all" stay
  its own; "go to the top/bottom" are keys only when it is off;
- mouse requests next while a grid shows (a number or "click" is then a
  command, "close grid" is not a window), after everything else otherwise;
- web search before the rest, since it takes any words after its prefix;
- keys before windows, so "close tab" is a key and not an app named "tab";
- fixed phrases (desktop, sound, music, system) before windows, so "switch to
  desktop two" is never an app named "desktop two";
- addresses before open-app, so "open youtube.com" isn't an app.
"""
import re
from dataclasses import dataclass

from . import mouse_phrases, voice_navigation
from .app_commands import OpenRequest, app_name, parse as parse_open
from .mouse_phrases import MouseRequest
from .undo import NUMBER_WORDS
from .voice_navigation import MAX_COUNT, normalize


@dataclass(frozen=True)
class KeyRequest:
    name: str            # a KEY_PHRASES name ("new tab"), "scroll up" or "scroll down"
    count: int = 1       # scrolling only


@dataclass(frozen=True)
class WindowRequest:
    action: str          # "close", "minimize", "maximize", "unmaximize", "restore", "switch"
    app: OpenRequest | None = None   # None: the focused window
    every: bool = False  # "close all Firefox"


SNAP_PLACES = ("left", "right", "top", "bottom", "top left", "top right", "bottom left",
               "bottom right")
FRONT_WINDOW_ACTIONS = frozenset(
    {f"snap {place}" for place in SNAP_PLACES}
    | {"center", "to desktop", "to next desktop", "to previous desktop", "to next screen"})
DESKTOP_ACTIONS = FRONT_WINDOW_ACTIONS | {
    "show desktop", "overview", "desktop", "next desktop", "previous desktop"}


@dataclass(frozen=True)
class DesktopRequest:
    action: str                  # a DESKTOP_ACTIONS name
    number: int | None = None    # 1..9, for "desktop" and "to desktop" only


@dataclass(frozen=True)
class SoundRequest:
    action: str          # "volume up", "volume down", "mute", "unmute"
    steps: int = 1       # 1..MAX_COUNT


@dataclass(frozen=True)
class MusicRequest:
    action: str          # "pause", "play", "next", "previous"


@dataclass(frozen=True)
class SystemRequest:
    action: str          # "lock", "screenshot", "window screenshot", "commands"


@dataclass(frozen=True)
class AddressRequest:
    url: str


@dataclass(frozen=True)
class SearchRequest:
    words: str           # as said, case kept


# A word like "copy", "paste" or "save" alone is an answer in a chat, so each key
# phrase needs a noun and the bare word is typed ("copy that" means "understood");
# "Go back." is an answer too, so the page commands say "a page".
KEY_PHRASES = {
    **dict.fromkeys(("new tab", "open new tab", "open a new tab"), "new tab"),
    **dict.fromkeys(("close tab", "close the tab", "close this tab"), "close tab"),
    "next tab": "next tab",
    "previous tab": "previous tab",
    **dict.fromkeys(("reopen tab", "reopen the tab", "reopen closed tab",
                     "reopen the closed tab"), "reopen tab"),
    **dict.fromkeys(("go back a page", "go back one page"), "go back"),
    **dict.fromkeys(("go forward a page", "go forward one page"), "go forward"),
    **dict.fromkeys(("reload the page", "refresh the page", "reload page",
                     "refresh page"), "reload"),
    "page up": "page up",
    "page down": "page down",
    **dict.fromkeys(("go to the top", "go to top"), "go to the top"),
    **dict.fromkeys(("go to the bottom", "go to bottom"), "go to the bottom"),
    "find on page": "find",
    **dict.fromkeys(("save the file", "save file"), "save"),
    "select all": "select all",
    **dict.fromkeys(("copy the selection", "copy selection"), "copy"),
    **dict.fromkeys(("paste it", "paste that", "paste the clipboard"), "paste"),
    "press escape": "escape",
}

_SCROLL = re.compile(r"scroll (up|down)(?: (\d+|" + "|".join(NUMBER_WORDS) + "))?")

_THIS = r"(?:this |the )?window"
_WINDOW_ACTIONS = (
    (re.compile(rf"close {_THIS}"), "close"),
    (re.compile(rf"minimi[sz]e {_THIS}"), "minimize"),
    (re.compile(rf"maximi[sz]e {_THIS}"), "maximize"),
    (re.compile(rf"(?:unmaximi[sz]e|restore) {_THIS}"), "unmaximize"),
)
# The verbs that act on a named app's window ("close <name>").
_NAMED_WINDOW_VERBS = {"close": "close", "minimize": "minimize", "minimise": "minimize",
                       "maximize": "maximize", "maximise": "maximize", "restore": "restore"}
# Spoken punctuation ("close quote", "close paren") is never a window request.
_PUNCTUATION = {"quote", "unquote", "paren", "parenthesis", "parentheses", "bracket",
                "brackets", "brace", "braces"}

_PLACE = "|".join(sorted(SNAP_PLACES, key=len, reverse=True))
# "too": Parakeet often writes a "two" said alone that way.
_DIGIT = r"[1-9]|one|two|too|three|four|five|six|seven|eight|nine"
_COUNT = r"\d+|" + "|".join(w for w in NUMBER_WORDS if w not in ("a", "an"))
# Whole-recording phrases that name one request; the first group, when there is
# one, is the place, the direction or the desktop number.
_DESKTOP_PHRASES = (
    (rf"snap(?: {_THIS})?(?: to(?: the)?)? ({_PLACE})",
     lambda m: DesktopRequest(f"snap {m[1]}")),
    (rf"move {_THIS} to (?:the )?({_PLACE})", lambda m: DesktopRequest(f"snap {m[1]}")),
    (rf"cent(?:er|re) {_THIS}", lambda m: DesktopRequest("center")),
    (rf"move {_THIS} to the cent(?:er|re)", lambda m: DesktopRequest("center")),
    (r"show (?:the )?desktop", lambda m: DesktopRequest("show desktop")),
    (r"show (?:all )?(?:the )?windows", lambda m: DesktopRequest("overview")),
    (r"(?:(?:go|switch) to the )?(next|previous) desktop",
     lambda m: DesktopRequest(f"{m[1]} desktop")),
    (rf"(?:switch|go) to desktop ({_DIGIT})",
     lambda m: DesktopRequest("desktop", _number(m[1]))),
    (rf"move {_THIS} to desktop ({_DIGIT})",
     lambda m: DesktopRequest("to desktop", _number(m[1]))),
    (rf"move {_THIS} to the (next|previous) desktop",
     lambda m: DesktopRequest(f"to {m[1]} desktop")),
    (rf"move {_THIS} to the (?:other|next) screen", lambda m: DesktopRequest("to next screen")),
)
_SOUND_PHRASES = (
    (rf"(?:turn (?:the )?)?volume (up|down)(?: ({_COUNT}))?",
     lambda m: _volume(m[1], m[2])),
    (r"turn (up|down) the volume", lambda m: SoundRequest(f"volume {m[1]}")),
    (r"mute (?:the )?sound", lambda m: SoundRequest("mute")),
    (r"unmute (?:the )?sound", lambda m: SoundRequest("unmute")),
)
_OTHER_PHRASES = (
    (r"pause (?:the )?music", lambda m: MusicRequest("pause")),
    (r"(?:play|resume) (?:the )?music", lambda m: MusicRequest("play")),
    (r"(next|previous) (?:track|song)", lambda m: MusicRequest(m[1])),
    (r"lock (?:the )?(?:screen|computer)", lambda m: SystemRequest("lock")),
    (r"take (?:a )?screen ?shot", lambda m: SystemRequest("screenshot")),
    (r"(?:take (?:a )?screen ?shot of|screen ?shot) (?:this|the) window"
     r"|take (?:a )?window screen ?shot", lambda m: SystemRequest("window screenshot")),
    (r"what can i say|show (?:the )?voice commands", lambda m: SystemRequest("commands")),
)
_FIXED = tuple((re.compile(pattern), build) for pattern, build in
               _DESKTOP_PHRASES + _SOUND_PHRASES + _OTHER_PHRASES)
# Whatever follows "switch to desktop" that isn't 1 to 9 ("ten", "eleven") names no
# desktop, and must not fall through to "switch to <app>" and look for an app
# called "desktop eleven"; an address ("go to desktop dot com") still is one.
_NO_SUCH_DESKTOP = re.compile(
    rf"(?:(?:switch|go) to|move {_THIS} to) (?:the )?desktop(?: .*)?")

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


def _number(word: str) -> int:
    if word == "too":
        return 2
    return int(word) if word.isdigit() else NUMBER_WORDS[word]


def _volume(direction: str, raw: str | None) -> SoundRequest | None:
    steps = 1 if raw is None else _number(raw)
    return SoundRequest(f"volume {direction}", steps) if 1 <= steps <= MAX_COUNT else None


def _fixed(said: str) -> DesktopRequest | SoundRequest | MusicRequest | SystemRequest | None:
    """The desktop, sound, music or system request *said* is exactly."""
    for pattern, build in _FIXED:
        if m := pattern.fullmatch(said):
            return build(m)
    return None


def _window(said: str) -> WindowRequest | None:
    """The window request *said* is. "close <name>", "switch to <name>",
    "minimize <name>", "maximize <name>" and "restore <name>" accept any short
    name here: the caller finds the app and types the words if none fits.
    """
    for pattern, action in _WINDOW_ACTIONS:
        if pattern.fullmatch(said):
            return WindowRequest(action)
    words = said.split()
    if len(words) < 2 or _PUNCTUATION.intersection(words):
        return None
    if words[0] == "close" and words[1] in ("all", "every"):
        # "Close all." names no app, never one whose name is "all". A "the"
        # stays in the name: app_finder takes a word of a name after it for
        # a sentence's ("Close all the chrome.").
        rest = words[2:]
        named = rest[1:] if rest[:1] == ["the"] else rest
        app = app_name(rest) if named else None
        return WindowRequest("close", app, every=True) if app else None
    if words[:2] == ["switch", "to"]:
        action, rest = "switch", words[2:]
    elif words[0] in _NAMED_WINDOW_VERBS:
        action, rest = _NAMED_WINDOW_VERBS[words[0]], words[1:]
    else:
        return None
    app = app_name(rest)
    return WindowRequest(action, app) if app else None


def _address_request(raw: str) -> AddressRequest | None:
    m = _GO_TO.match(re.sub(r"(?i)^\s*please\s+|\s+please\W*$", "", raw))
    url = address(m.group("rest")) if m else None
    return AddressRequest(url) if url else None


def parse(text: str, *, navigation: bool, actions: bool, grid: bool = False) -> (
        voice_navigation.Command | OpenRequest | KeyRequest | DesktopRequest | SoundRequest
        | MusicRequest | SystemRequest | WindowRequest | AddressRequest | SearchRequest
        | MouseRequest | None):
    """What *text* commands, or None when it's dictation to type.

    *navigation* is select and move being on, *actions* the voice commands,
    *grid* the mouse grid being on screen.
    """
    if navigation and (command := voice_navigation.parse(text)):
        return command
    if not actions:
        return None
    if m := _SEARCH.match(text):
        return SearchRequest(m.group("words"))
    said = normalize(text)
    if grid and (mouse := mouse_phrases.parse(said, grid)):
        return mouse
    if key := _key(said):
        return key
    if fixed := _fixed(said):
        return fixed
    if _NO_SUCH_DESKTOP.fullmatch(said):
        return _address_request(text)
    return (_window(said) or _address_request(text) or parse_open(text)
            or mouse_phrases.parse(said, grid))


def literal_words(raw: str, *, navigation: bool, actions: bool, grid: bool = False) -> str:
    """The words to type for "literal <command>", else *raw* unchanged."""
    m = _LITERAL.match(raw)
    if m and parse(m.group("rest"), navigation=navigation, actions=actions, grid=grid):
        return m.group("rest")
    return raw
