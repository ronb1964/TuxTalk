"""Addresses and web searches by voice (TuxTalk Labs): "go to youtube.com"
and "web search cheap tires", done in the user's own browser with its own
search engine. In a browser the address bar takes them (Ctrl+L). Elsewhere an
address goes to the default browser through the desktop, and a search waits
for that browser to have the focus, then opens a tab for it (Ctrl+T).

The address and the words are never logged: they are as private as a
dictation. Run on the dictation worker, never GTK's thread, since a browser
that has to start can take seconds to come forward."""
import time
from typing import NamedTuple

from . import app_finder, app_focus, app_launcher, command_notices, focused_window
from . import keyboard_output, virtual_keyboard
from .app_finder import AppEntry
from .command_phrases import AddressRequest, SearchRequest
from .window_classes import is_browser_class

BROWSER_WAIT_S = 10.0     # how long a browser brought forward may take to have the focus
BROWSER_START_WAIT_S = 20.0   # and one that had to start: a first start on a slow disk
POLL_S = 0.25
# A new tab puts the cursor in its address bar a moment after Ctrl+T; keys
# sent before that land in the page that was showing.
OPENER_SETTLE_S = 0.2

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
    classes: frozenset      # its windows' classes, without case


def _default_browser() -> _Browser | None:
    """The default browser, or None when the desktop names none in the menu."""
    entries = app_finder.installed_apps()
    browser_id = app_finder.default_apps().get("browser")
    entry = next((e for e in entries if e.app_id == browser_id), None)
    if entry is None:
        return None
    exact = len(app_finder.copies(entry, entries)) > 1
    classes = app_focus.window_names(entry, exact) | {app_focus.desktop_file(entry).casefold()}
    return _Browser(entry, exact, classes)


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
    searches with the engine the user picked there. In the browser window
    *where*, in its current tab; anywhere else, in a new tab of the default
    browser, brought forward or started for it. True when the words went in;
    False after a note, or after Esc (said already).

    A desktop that doesn't say which window has the focus can't tell a
    browser either, so that is said at once, not after waiting for one."""
    if where[0] is None:
        command_notices.say_focus_unknown(beeps_on)
        return False
    default = _default_browser()
    if is_browser_class(where[0]) or _is_default(where[0], default):
        return _into_address_bar(where, _ADDRESS_BAR, request.words, _SEARCH, beeps_on)
    browser = _browser_in_front(default, beeps_on)
    if browser is None:
        return False
    return _into_address_bar(browser, _NEW_TAB, request.words, _SEARCH, beeps_on)


def _into_address_bar(where, opener, text: str, what: str, beeps_on: bool) -> bool:
    """*opener* (Ctrl+L or Ctrl+T, each leaving the cursor in the address
    bar), *text*, Delete and Enter, into the window
    *where* only: if another window takes the focus partway, what's left
    goes nowhere, Enter above all."""
    press = keyboard_output.output_backend().press_keys

    def send():
        if not press(opener):
            return False
        time.sleep(OPENER_SETTLE_S)
        return keyboard_output.type_text(text) and press(_DELETE_ENTER)
    try:
        sent = keyboard_output.send_only_into(where, send)
    except virtual_keyboard.StoppedPartway as stop:
        # Part of the text is in the address bar, with no Enter after it: nothing opens,
        # and the note says why the typing stopped.
        sent = stop.reason if stop.reason in ("moved", "cancelled") else False
    return command_notices.outcome(sent, what, beeps_on)


def _browser_in_front(browser: _Browser | None, beeps_on: bool):
    """The look of the default *browser*'s window, brought forward or
    started for a search. None after a note (no browser, it didn't start, or
    it didn't come forward) or after Esc."""
    if browser is None:
        command_notices.say("no_browser", beeps_on)
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
