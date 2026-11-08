"""Addresses and web searches by voice (TuxTalk Labs): "go to youtube.com"
and "web search cheap tires", done in the user's own browser with its own
search engine. In a browser the address bar takes them (Ctrl+L). Elsewhere an
address goes to the default browser through the desktop, and a search waits
for that browser to have the focus, then opens a tab for it (Ctrl+T).

The address and the words are never logged: they are as private as a
dictation. Run on the dictation worker, never GTK's thread, since a browser
that has to start can take seconds to come forward."""
import os
import time
from typing import NamedTuple

from . import app_finder, app_focus, app_launcher, command_notices, desktop_detect, focused_window
from . import keyboard_layout, keyboard_output, virtual_keyboard
from .app_finder import AppEntry
from .command_phrases import AddressRequest, SearchRequest
from .window_classes import is_browser_class

BROWSER_WAIT_S = 10.0     # how long a browser brought forward may take to have the focus
BROWSER_START_WAIT_S = 20.0   # and one that had to start: a first start on a slow disk
POLL_S = 0.25
# A new tab puts the cursor in its address bar a moment after Ctrl+T; keys
# sent before that land in the page that was showing.
OPENER_SETTLE_S = 0.2
# An address or search is typed this many characters at a time, the focused
# window checked before each: a switch partway can let at most the rest of
# one chunk into the window switched to.
CHUNK_CHARS = 8

_ADDRESS_BAR = [(("CTRL",), "L")]
_NEW_TAB = [(("CTRL",), "T")]
# Delete first takes away what the browser filled in after the words (its
# inline autofill): "web search python" would otherwise open python.org, and
# a whole address can be stretched to a longer one the browser remembers.
# After a complete address it does nothing.
_DELETE_ENTER = [((), "DELETE"), ((), "ENTER")]

_GO_TO = "go to an address"
_SEARCH = "web search"
SEARCH_DONE = "done: a web search"      # esc_stopped's: "Not done: a web search"


class _Browser(NamedTuple):
    """The user's default browser, as its menu entry says."""
    entry: AppEntry
    # Other copies installed (the distro's Firefox and Flathub's): only a
    # window of this one counts, as app_match.find decides.
    exact: bool
    classes: frozenset      # its windows' classes, without case; never empty
    # On X11, another copy's windows may have one of those classes too: no
    # window can be told for this copy's.
    alike: bool = False


def _x11_classes(entry: AppEntry) -> frozenset:
    """Every class *entry*'s windows may have on X11: its id, its
    StartupWMClass and, unless they disagree, its program's name, which a
    Flatpak's windows have whether its entry says so or not."""
    return app_focus.window_names(entry) | {app_focus.desktop_file(entry).casefold()}


def _default_browser() -> _Browser | None:
    """The default browser, or None when the desktop names none in the menu.
    With other copies installed, on Wayland only its app id counts (a
    Wayland window's class, which copies don't share); on X11 every class
    its windows may have, and copies that share one are alike."""
    entries = app_finder.installed_apps()
    browser_id = app_finder.default_apps().get("browser")
    entry = next((e for e in entries if e.app_id == browser_id), None)
    if entry is None:
        return None
    copies = app_finder.copies(entry, entries)
    classes = _x11_classes(entry)
    if len(copies) == 1:
        return _Browser(entry, False, classes)
    if not desktop_detect.is_x11_session(os.environ):
        return _Browser(entry, True, frozenset({app_focus.desktop_file(entry).casefold()}))
    alike = any(classes & _x11_classes(other) for other in copies[1:])
    return _Browser(entry, True, classes, alike)


def _is_default(wm_class: str | None, browser: _Browser | None) -> bool:
    """Whether *wm_class* is the default *browser*'s windows' class."""
    return bool(wm_class) and browser is not None and wm_class.casefold() in browser.classes


def _in_a_browser(wm_class: str | None) -> bool:
    """Whether the window of class *wm_class* is a browser: one TuxTalk
    knows, or the user's default, which may be one it doesn't (the Flatpaks
    of Edge, Vivaldi or Floorp)."""
    if is_browser_class(wm_class):
        return True
    return bool(wm_class) and _is_default(wm_class, _default_browser())


def go_to(request: AddressRequest, where, beeps_on: bool) -> bool:
    """Open *request*'s address. In the browser window *where*
    (focused_window.look(), taken when the command was recognised), in its
    current tab; anywhere else, in the default browser, as it opens links.
    True when it went in or was handed over; False after a note, or after
    Esc (said already)."""
    if _in_a_browser(where[0]):
        return _into_address_bar(where, _ADDRESS_BAR, request.url, _GO_TO, beeps_on)
    if not app_launcher.open_uri(request.url):
        command_notices.say("address_not_opened", beeps_on)
        return False
    return command_notices.outcome(True, _GO_TO, beeps_on)


def search(request: SearchRequest, where, beeps_on: bool) -> bool:
    """Search the web for *request*'s words: the browser's address bar
    searches with the engine the user picked there. Always in a new tab,
    which never takes the user away from a page they were writing in: of
    the browser window *where*, else of the default browser, brought
    forward or started for it. True when the words went in;
    False after a note, or after Esc (said already).

    A desktop that doesn't say which window has the focus can't tell a
    browser either, so that is said at once, not after waiting for one."""
    if where[0] is None:
        command_notices.say_focus_unknown(beeps_on)
        return False
    default = _default_browser()
    if is_browser_class(where[0]) or _is_default(where[0], default):
        return _into_address_bar(where, _NEW_TAB, request.words, _SEARCH, beeps_on)
    browser = _browser_in_front(default, beeps_on)
    if browser is None:
        return False
    return _into_address_bar(browser, _NEW_TAB, request.words, _SEARCH, beeps_on)


def _into_address_bar(where, opener, text: str, what: str, beeps_on: bool) -> bool:
    """*opener* (Ctrl+L or Ctrl+T, each leaving the cursor in the address
    bar), *text*, Delete and Enter, into the window
    *where* only: if another window takes the focus partway, what's left
    goes nowhere, Enter above all. Each send is checked against *where*
    right before its first key (only_into), so the text goes CHUNK_CHARS
    at a time: one send would be checked once, and the rest of a long
    address or search would land in the window switched to."""
    press = keyboard_output.output_backend().press_keys

    def send():
        if not press(opener):
            return False
        time.sleep(OPENER_SETTLE_S)
        return (all(keyboard_output.type_text(piece) for piece in _pieces(text))
                and press(_DELETE_ENTER))
    try:
        sent = keyboard_output.send_only_into(where, send)
    except virtual_keyboard.StoppedPartway as stop:
        # Part of the text is in the address bar, with no Enter after it: nothing opens,
        # and the note says why the typing stopped.
        sent = stop.reason if stop.reason in ("moved", "cancelled") else False
    return command_notices.outcome(sent, what, beeps_on)


def _pieces(text: str) -> list[str]:
    """*text* in CHUNK_CHARS pieces, or whole when the keyboard layout can't
    type it. Then it is pasted, and one paste is a single Ctrl+V, checked
    like a piece; pasted pieces would each replace the clipboard, perhaps
    before the browser had read the one before."""
    try:
        keyboard_layout.layout_key_events(text)
    except keyboard_layout.UntypableText:
        return [text]
    except OSError:
        pass        # no libxkbcommon: type_text says so and types another way
    return [text[i:i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS)]


def _browser_in_front(browser: _Browser | None, beeps_on: bool):
    """The look of the default *browser*'s window, brought forward or
    started for a search. None after a note (no browser, two copies whose
    windows look alike, it didn't start, or it didn't come forward) or after
    Esc."""
    if browser is None:
        command_notices.say("no_browser", beeps_on)
        return None
    if browser.alike:
        command_notices.say("browser_copies_alike", beeps_on)
        return None
    if app_focus.focus_running(browser.entry, exact=browser.exact):
        wait_s = BROWSER_WAIT_S
    elif app_launcher.launch(browser.entry):
        wait_s = BROWSER_START_WAIT_S
    else:
        command_notices.say("browser_not_started", beeps_on)
        return None
    return _wait_for_browser(browser, wait_s, beeps_on)


def _wait_for_browser(browser: _Browser, wait_s: float, beeps_on: bool):
    """The look of the focused window once it's the default *browser*'s,
    asked every POLL_S for up to *wait_s*: a browser that's starting, or a
    desktop that brings a window forward slowly, gets there in its own time.
    Another browser taking the focus meanwhile isn't it. None when Esc
    stopped the wait, or when it didn't come forward (said): the words must
    not be typed into whatever window has the focus instead."""
    deadline = time.monotonic() + wait_s
    while not keyboard_output.esc_stopped(SEARCH_DONE):
        look = focused_window.look()
        if _is_default(look[0], browser):
            return look
        if time.monotonic() >= deadline:
            command_notices.say("browser_not_forward", beeps_on)
            return None
        time.sleep(POLL_S)
    return None
