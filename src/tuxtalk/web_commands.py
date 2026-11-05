"""Addresses and web searches by voice (TuxTalk Labs): "go to youtube.com"
and "web search cheap tires", done in the user's own browser with its own
search engine. In a browser the address bar takes them (Ctrl+L). Elsewhere an
address goes to the default browser through the desktop, and a search waits
for that browser to have the focus, then opens a tab for it (Ctrl+T).

The address and the words are never logged: they are as private as a
dictation. Run on the dictation worker, never GTK's thread, since a browser
that has to start can take seconds to come forward."""
import time

from . import app_finder, app_focus, app_launcher, command_notices, focused_window
from . import keyboard_output, virtual_keyboard
from .command_phrases import AddressRequest, SearchRequest
from .window_classes import is_browser_class

BROWSER_WAIT_S = 10.0     # how long a raised or started browser may take to have the focus
POLL_S = 0.25
# A new tab puts the cursor in its address bar a moment after Ctrl+T; keys
# sent before that land in the page that was showing.
OPENER_SETTLE_S = 0.2

_ADDRESS_BAR = [(("CTRL",), "L")]
_NEW_TAB = [(("CTRL",), "T")]
_ENTER = [((), "ENTER")]

_GO_TO = "go to an address"
_SEARCH = "web search"


def go_to(request: AddressRequest, where, beeps_on: bool) -> bool:
    """Open *request*'s address. In the browser window *where*
    (focused_window.look(), taken when the command was recognised), in its
    current tab; anywhere else, in the default browser, as it opens links.
    True when it went in or was handed over; False after a note, or after
    Esc (said already)."""
    if is_browser_class(where[0]):
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
    False after a note, or after Esc (said already)."""
    if is_browser_class(where[0]):
        return _into_address_bar(where, _ADDRESS_BAR, request.words, _SEARCH, beeps_on)
    browser = _browser_in_front(beeps_on)
    if browser is None:
        return False
    return _into_address_bar(browser, _NEW_TAB, request.words, _SEARCH, beeps_on)


def _into_address_bar(where, opener, text: str, what: str, beeps_on: bool) -> bool:
    """*opener* (Ctrl+L or Ctrl+T, each leaving the cursor in the address
    bar), *text* and Enter, into the window *where* only: if another window
    takes the focus partway, what's left goes nowhere, Enter above all."""
    press = keyboard_output.output_backend().press_keys

    def send():
        if not press(opener):
            return False
        time.sleep(OPENER_SETTLE_S)
        return keyboard_output.type_text(text) and press(_ENTER)
    try:
        sent = keyboard_output.send_only_into(where, send)
    except virtual_keyboard.StoppedPartway as stop:
        # Part of the text is in the address bar, with no Enter after it: nothing opens,
        # and the note says why the typing stopped.
        sent = stop.reason if stop.reason in ("moved", "cancelled") else False
    return command_notices.outcome(sent, what, beeps_on)


def _browser_in_front(beeps_on: bool):
    """The look of the default browser's window, brought forward or started
    for a search. None after a note (no browser, or none came forward) or
    after Esc."""
    entries = app_finder.installed_apps()
    browser_id = app_finder.default_apps().get("browser")
    entry = next((e for e in entries if e.app_id == browser_id), None)
    if entry is None:
        command_notices.say("no_browser", beeps_on)
        return None
    # Other copies installed (the distro's Firefox and Flathub's): only a
    # window of the default one counts, as open_app.matched_app decides.
    exact = len(app_finder.copies(entry, entries)) > 1
    if not app_focus.focus_running(entry, exact=exact) and not app_launcher.launch(entry):
        command_notices.say("no_browser", beeps_on)
        return None
    return _wait_for_browser(beeps_on)


def _wait_for_browser(beeps_on: bool):
    """The look of the focused window once it's a browser's, asked every
    POLL_S for up to BROWSER_WAIT_S: a browser that's starting, or a desktop
    that brings a window forward slowly, gets there in its own time. None
    when Esc stopped the wait, or when none came forward (said): the words
    must not be typed into whatever window has the focus instead."""
    deadline = time.monotonic() + BROWSER_WAIT_S
    while not keyboard_output.esc_stopped("done: a web search"):
        look = focused_window.look()
        if is_browser_class(look[0]):
            return look
        if time.monotonic() >= deadline:
            command_notices.say("browser_not_forward", beeps_on)
            return None
        time.sleep(POLL_S)
    return None
