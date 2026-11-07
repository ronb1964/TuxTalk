"""The words of mouse by voice: what a whole dictation asks the grid to do.

Like the other command parsers this is pure and whole-recording only, so a
sentence that merely contains "click" or a number is typed as text. A bare
number or "click" is also an everyday word, so those only count while a grid
shows (*grid*); "mouse grid" and the clicks that act where the pointer already
is ("left click", "right click", ...) work at any time.
"""
import re
from dataclasses import dataclass

from . import spoken_numbers
from .undo import NUMBER_WORDS

MAX_NUMBER = 99


@dataclass(frozen=True)
class MouseRequest:
    action: str   # "grid", "zoom", "click", "double click", "right click",
                  # "middle click", "move", "nudge" or "cancel"
    number: int | None = None   # the grid cell, or a nudge's steps; None: the spot
    # Heard while a grid showed, so one of its commands: never done without
    # it, should the grid close before the request is acted on.
    in_grid: bool = False
    direction: str | None = None   # a nudge's "left", "right", "up" or "down"


_GRID = frozenset({"mouse grid", "show grid", "show mouse grid"})
_CANCEL = frozenset({"cancel", "close grid", "hide grid"})
# A click word to its action; "click" alone is a word in chat, so it needs a grid.
_CLICKS = {"left click": "click", "right click": "right click",
           "double click": "double click", "middle click": "middle click"}
_CLICK = re.compile(r"(click|left click|right click|double click|middle click)"
                    r"(?: (?P<cell>.+))?")
_NUDGE = re.compile(r"(?P<direction>left|right|up|down)(?: (?P<steps>.+))?")
_MOVE = re.compile(r"move (?:to )?(?P<cell>.+)")


def _number(words: str) -> int | None:
    """The cell number *words* say ("5", "five", "number twelve", "twenty five"),
    or None. "too" is how Parakeet often writes a "two" said alone."""
    words = words.removeprefix("number ")
    if words == "too":
        return 2
    if words in NUMBER_WORDS and words not in ("a", "an"):
        number = NUMBER_WORDS[words]
    else:
        digits = words if words.isdigit() else spoken_numbers.to_digits(words, "en")
        # isdigit() also accepts "²" and other digits int() refuses.
        if not (digits.isascii() and digits.isdigit() and len(digits) < 4):
            return None
        number = int(digits)
    return number if 1 <= number <= MAX_NUMBER else None


def _click(said: str, grid: bool) -> MouseRequest | None:
    m = _CLICK.fullmatch(said)
    if not m:
        return None
    word = m[1]
    if m["cell"] is None:
        if grid or word != "click":
            return MouseRequest(_CLICKS.get(word, "click"), in_grid=grid)
        return None
    number = _number(m["cell"]) if grid else None
    return MouseRequest(_CLICKS.get(word, "click"), number, in_grid=True) if number else None


def _nudge(said: str) -> MouseRequest | None:
    """"left", "up two": move the click spot, one step when no count is said."""
    m = _NUDGE.fullmatch(said)
    if not m:
        return None
    steps = 1 if m["steps"] is None else _number(m["steps"])
    return MouseRequest("nudge", steps, in_grid=True, direction=m["direction"]) if steps else None


def parse(said: str, grid: bool) -> MouseRequest | None:
    """The mouse request *said* (command_phrases' normalized form) is, or None."""
    if said in _GRID:
        return MouseRequest("grid")
    if request := _click(said, grid):
        return request
    if not grid:
        return None
    if said in _CANCEL:
        return MouseRequest("cancel", in_grid=True)
    if said == "move here":
        return MouseRequest("move", in_grid=True)
    if m := _MOVE.fullmatch(said):
        number = _number(m["cell"])
        return MouseRequest("move", number, in_grid=True) if number else None
    if request := _nudge(said):
        return request
    number = _number(said)
    return MouseRequest("zoom", number, in_grid=True) if number else None
