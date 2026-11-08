"""Everyday keys by voice (TuxTalk Labs): "new tab", "go back a page", "reload",
"scroll down 3", "copy". The keys differ in a terminal, where Ctrl+C stops the
running program and Ctrl+Shift+C copies, so the focused window's class picks
the set, and the keys go only into the window the command was checked against."""
from . import command_notices, keyboard_output
from .command_phrases import KeyRequest
from .window_classes import is_terminal_class

SCROLL_CLICKS = 5        # wheel notches per "scroll"; "scroll down 3" turns 15

_C, _CS, _A = ("CTRL",), ("CTRL", "SHIFT"), ("ALT",)

# name -> one chord, as keys outside a terminal.
_GENERAL = {
    "new tab": (_C, "T"), "close tab": (_C, "W"), "next tab": (_C, "TAB"),
    "previous tab": (_CS, "TAB"), "reopen tab": (_CS, "T"),
    "go back": (_A, "LEFT"), "go forward": (_A, "RIGHT"), "reload": ((), "F5"),
    "page up": ((), "PAGEUP"), "page down": ((), "PAGEDOWN"),
    "go to the top": (_C, "HOME"), "go to the bottom": (_C, "END"),
    "find": (_C, "F"), "save": (_C, "S"), "select all": (_C, "A"),
    "copy": (_C, "C"), "paste": (_C, "V"), "escape": ((), "ESC"),
}
# Where a terminal differs; a name left out here is the general chord, and
# None is a key a terminal has no equivalent of: Ctrl+F, S and A are the
# shell's own line editing there, and Alt+Left, Alt+Right and F5 put
# escape-sequence leftovers on the command line.
_TERMINAL = {
    "new tab": (_CS, "T"), "close tab": (_CS, "W"), "next tab": (_C, "PAGEDOWN"),
    "previous tab": (_C, "PAGEUP"), "reopen tab": None, "find": None, "save": None,
    "select all": None, "copy": (_CS, "C"), "paste": (_CS, "V"),
    "go back": None, "go forward": None, "reload": None,
}


def chords_for(name: str, wm_class: str | None) -> list | None:
    """The chords for the key *name* in a window of class *wm_class*, None
    where it doesn't apply (a terminal without that key). Scrolling turns the
    wheel and has no chords: [] for it."""
    if name.startswith("scroll"):
        return []
    key = _GENERAL[name]
    if is_terminal_class(wm_class):
        key = _TERMINAL.get(name, key)
    return None if key is None else [key]


def press(request: KeyRequest, where, beeps_on: bool) -> bool:
    """Do *request* in the window *where* (focused_window.look(), taken when
    the command was recognised). True when the keys or wheel went in, with the
    ready beep; False after a note, or after Esc stopped it (already said).

    With the window's class unknown (GNOME without TuxTalk's extension) it
    may be a terminal, so a key a terminal has its own of is never guessed:
    Ctrl+C would stop the running program there, Ctrl+S freeze it."""
    if where[0] is None and request.name in _TERMINAL:
        command_notices.say_focus_unknown(beeps_on)
        return False
    chords = chords_for(request.name, where[0])
    if chords is None:
        command_notices.say("terminal", beeps_on)
        return False
    if request.name.startswith("scroll"):
        sign = 1 if request.name == "scroll up" else -1
        clicks = sign * request.count * SCROLL_CLICKS
        sent = keyboard_output.send_only_into(
            where, lambda: keyboard_output.send_scroll(clicks))
    else:
        sent = keyboard_output.send_only_into(
            where, lambda: keyboard_output.output_backend().press_keys(chords))
    return command_notices.outcome(sent, request.name, beeps_on)
