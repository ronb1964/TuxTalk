"""The numbered grid of "mouse by voice", as plain geometry.

Each monitor is cut into 3 x 3 cells numbered row by row, top-left first,
the numbers going on from one monitor to the next. Saying a number zooms into
that cell, which is cut into 3 x 3 again, until a cell would be too small to
aim at. `Fit` turns the chosen screen point into the 0..1 fraction the
virtual pointer's tablet wants. Nothing here touches GTK, so it is tested
without a screen.
"""
from typing import NamedTuple

# A zoom that would make cells narrower or lower than this is refused.
MIN_CELL_PX = 8

SPLIT = 3

# One nudge step, in desktop pixels: a few of them at the 3x desktop zoom
# are a visible move.
NUDGE_PX = 5

_NUDGES = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}


class Rect(NamedTuple):
    """A rectangle in desktop pixels."""

    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)


class Cell(NamedTuple):
    """One numbered square of the grid."""

    number: int
    rect: Rect


def _split(rect: Rect, first_number: int) -> list[Cell]:
    """The nine cells of a rectangle, row by row from the top left."""
    w, h = rect.width / SPLIT, rect.height / SPLIT
    return [
        Cell(first_number + row * SPLIT + col,
             Rect(rect.x + col * w, rect.y + row * h, w, h))
        for row in range(SPLIT) for col in range(SPLIT)
    ]


class Grid:
    """The cells on screen now: every monitor's nine, or nine inside a zoom."""

    def __init__(self, monitors: list[Rect]):
        self.monitors = list(monitors)      # each monitor's rectangle, as read once
        self._top = [cell for i, m in enumerate(self.monitors)
                     for cell in _split(m, i * SPLIT * SPLIT + 1)]
        self.region: Rect | None = None
        self._zoomed: list[Cell] = []
        # Where a click with no number lands: the part's centre, moved by
        # nudges. None until a number has been said.
        self.spot: tuple[float, float] | None = None

    @property
    def cells(self) -> list[Cell]:
        return self._zoomed if self.region else self._top

    @property
    def bounds(self) -> Rect:
        """The box around all monitors, which the tablet's range covers."""
        left = min(m.x for m in self.monitors)
        top = min(m.y for m in self.monitors)
        right = max(m.x + m.width for m in self.monitors)
        bottom = max(m.y + m.height for m in self.monitors)
        return Rect(left, top, right - left, bottom - top)

    def calibration_spots(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """Where the pointer is sent when the grid opens, to see how it lands:
        the centres of the first monitor's cell 1 and of the last one's cell
        9. Both are on a screen, which a spot fixed in the box around all of
        them needn't be (the desktop would hold the pointer at an edge).
        Fit.measured needs them apart on both axes, which staggered screens
        can line up; the first monitor's own cell 9 always is."""
        first, last = self._top[0].rect.center, self._top[-1].rect.center
        if first[0] == last[0] or first[1] == last[1]:
            last = self._top[SPLIT * SPLIT - 1].rect.center
        return first, last

    def _cell(self, number: int) -> Cell | None:
        return next((c for c in self.cells if c.number == number), None)

    def zoom(self, number: int) -> str:
        """Go into a cell: "ok", "no_cell" or "smallest" (nothing changes)."""
        cell = self._cell(number)
        if cell is None:
            return "no_cell"
        if min(cell.rect.width, cell.rect.height) / SPLIT < MIN_CELL_PX:
            return "smallest"
        self.region = cell.rect
        self._zoomed = _split(cell.rect, 1)
        self.spot = cell.rect.center
        return "ok"

    def nudge(self, direction: str, steps: int) -> str:
        """Move the click spot *steps* steps, staying inside the part shown
        (its last pixel at most: one further is the next monitor's first):
        "ok", or "nudge_needs_a_number" when no part has been chosen yet."""
        if self.region is None or self.spot is None:
            return "nudge_needs_a_number"
        dx, dy = _NUDGES[direction]
        x, y = self.spot[0] + dx * NUDGE_PX * steps, self.spot[1] + dy * NUDGE_PX * steps
        region = self.region
        self.spot = (min(max(x, region.x), region.x + region.width - 1),
                     min(max(y, region.y), region.y + region.height - 1))
        return "ok"

    def target(self, number: int | None) -> tuple[float, float] | str:
        """Where to click: a cell's centre, or with no number the zoomed
        spot (a lone monitor's centre, at the top). "say_a_number" when that
        would be a guess between monitors, "no_cell" for a number not shown."""
        if number is not None:
            cell = self._cell(number)
            return cell.rect.center if cell else "no_cell"
        if self.spot:
            return self.spot
        if len(self.monitors) == 1:
            return self.monitors[0].center
        return "say_a_number"


class Fit:
    """Turns a screen point into the tablet's 0..1 fraction on each axis.

    Per axis a straight line, fraction = scale * point + offset, either the
    plain one over the desktop's box or one fitted from two spots where the
    pointer was seen to land, for a desktop that maps the tablet differently.
    """

    def __init__(self, scale: tuple[float, float], offset: tuple[float, float]):
        self._scale = scale
        self._offset = offset

    @classmethod
    def straight(cls, bounds: Rect) -> "Fit":
        scale = (1 / bounds.width, 1 / bounds.height)
        return cls(scale, (-bounds.x * scale[0], -bounds.y * scale[1]))

    @classmethod
    def measured(cls, fractions: tuple[tuple, tuple],
                 arrivals: tuple[tuple, tuple]) -> "Fit | None":
        """Fit from two moves: the fractions sent and the pixels reached.

        None when both arrivals, or both sent fractions, share an x or a y:
        one spot says nothing about that axis's slope."""
        scale, offset = [], []
        for axis in (0, 1):
            f0, f1 = fractions[0][axis], fractions[1][axis]
            a0, a1 = arrivals[0][axis], arrivals[1][axis]
            if a0 == a1 or f0 == f1:
                return None
            slope = (f1 - f0) / (a1 - a0)
            scale.append(slope)
            offset.append(f0 - slope * a0)
        return cls((scale[0], scale[1]), (offset[0], offset[1]))

    def fraction(self, point: tuple[float, float]) -> tuple[float, float]:
        return (point[0] * self._scale[0] + self._offset[0],
                point[1] * self._scale[1] + self._offset[1])


def close_enough(target: tuple[float, float], arrived: tuple[float, float],
                 tolerance: float = 3.0) -> bool:
    """Whether the pointer landed within `tolerance` pixels on both axes."""
    return (abs(target[0] - arrived[0]) <= tolerance
            and abs(target[1] - arrived[1]) <= tolerance)
