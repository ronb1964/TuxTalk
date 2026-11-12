"""The transcripts Transcribe a File keeps for next time, so closing the
window or opening another file loses nothing: the window shows the newest
one on opening, and Earlier transcripts lists them all.

They are kept in one JSON list in TuxTalk's data folder, readable only by
this user, newest first and at most MAX_RECENT. The one transcript an older
TuxTalk kept (last-transcript.json) comes into the list once. A list that
can't be read is set aside, not written over, so the transcripts in it
aren't lost; Clear This List removes those copies too.
"""
import json
import math
import os
import re
import time

from .file_transcribe import Piece
from .logger import setup_logger

logger = setup_logger(__name__)

RECENT_FILE = "recent-transcripts.json"
MAX_RECENT = 10
OLD_LAST_FILE = "last-transcript.json"     # one kept transcript, before the list
# What a damaged list set aside is named after (_aside_name), for clearing and
# for uninstall: .corrupt-<date>-<time>, -2 for a second in the same second.
SET_ASIDE_PATTERN = r"\.corrupt-\d{8}-\d{6}(?:-\d+)?"


class Kept:
    """One transcript kept for next time."""
    def __init__(self, path, pieces, total, when):
        self.path, self.pieces, self.total, self.when = path, pieces, total, when

    @property
    def name(self):
        return os.path.basename(self.path or "") or "A file"


def recent_path():
    from . import config
    return os.path.join(config.get_data_dir(), RECENT_FILE)


def _read_list(target):
    with open(target, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else [data]


# The longest time into a file a kept transcript may reach: far past any
# recording, and small enough for the subtitle clock (file_transcribe.clock)
# to count it.
_LONGEST_S = 10_000_000


def _number(value):
    """*value* as a finite float; NaN and infinity raise ValueError."""
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"not a usable number: {value!r}")
    return number


def _seconds(value):
    """*value* as a time into the file, 0 to _LONGEST_S; else ValueError."""
    seconds = _number(value)
    if not 0 <= seconds <= _LONGEST_S:
        raise ValueError(f"not a time in a file: {value!r}")
    return seconds


def _entry(data):
    """The Kept in one saved entry, or None when it's damaged: no text, a
    path that isn't text, or a number that isn't one (NaN, infinite, a date
    too far off to show, a time no file reaches), each of which broke Earlier
    Transcripts or saving the subtitles."""
    try:
        pieces = [Piece(_seconds(a), _seconds(b), str(t)) for a, b, t in data["pieces"]]
        path, when = data.get("path"), _number(data.get("when", 0))
        time.localtime(when)            # a date Earlier Transcripts can show
        if not pieces or (path is not None and not isinstance(path, str)):
            return None
        return Kept(path, pieces, _seconds(data.get("total", 0)), when)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None


# What reading the list found, beside the transcripts in it: nothing wrong, a
# list that's damaged (set aside before the next transcript is written), or
# one that can't be read just now (left alone, and the next one not added).
_FINE, _DAMAGED, _UNAVAILABLE = "fine", "damaged", "unavailable"
# The problems already logged: a list that stays damaged is read at every
# opening of the window, and each finished transcript.
_reported = set()


def _report(problem):
    """Log *problem* the first time it comes up in this process."""
    if problem not in _reported:
        _reported.add(problem)
        logger.warning(problem)


def recent():
    """The transcripts kept, newest first. Never raises."""
    return _read_kept()[0]


def _read_kept():
    """(the transcripts kept, newest first, and _FINE, _DAMAGED or _UNAVAILABLE).
    Only a transcript with text is ever kept (transcribe_window), so an entry
    that can't be read is damage too, and setting the list aside keeps it."""
    target = recent_path()
    try:
        entries = _read_list(target)
    except FileNotFoundError:
        return _kept_before_the_list(target), _FINE
    except (ValueError, PermissionError) as e:
        _report(f"The list of kept transcripts can't be read: {e}")
        return [], _DAMAGED
    except OSError as e:        # a passing failure (too many files open, a disk error)
        _report(f"The list of kept transcripts can't be read just now: {e}")
        return [], _UNAVAILABLE
    parsed = [_entry(d) for d in entries]
    kept = [e for e in parsed if e]
    if len(kept) < len(parsed):
        _report(f"{len(parsed) - len(kept)} of the kept transcripts can't be read")
        return kept[:MAX_RECENT], _DAMAGED
    return kept[:MAX_RECENT], _FINE


def _kept_before_the_list(target):
    """The one transcript kept before there was a list, which comes along once."""
    old = os.path.join(os.path.dirname(target), OLD_LAST_FILE)
    try:
        entries = _read_list(old)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        _report(f"The transcript an older TuxTalk kept can't be read: {e}")
        return []
    parsed = [_entry(d) for d in entries]
    kept = [e for e in parsed if e]
    if len(kept) < len(parsed):     # kept as it is: moving it would lose that one
        _report(f"The transcript an older TuxTalk kept is damaged; {old} is left as it is")
        return kept
    if _write(kept):
        try:
            os.remove(old)
        except OSError as e:
            logger.warning(f"Couldn't remove {old} now its transcript is in the list: {e}")
    return kept


def _aside_name(target):
    """*target*.corrupt-<date>-<time>, as a damaged config.toml is kept
    (config.py; SET_ASIDE_PATTERN is its pattern), with -2, -3 and so on when
    a copy from that second is there already. The window runs once at a time,
    so nothing takes the name between the look and the move."""
    base = f"{target}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
    name, number = base, 1
    while os.path.lexists(name):
        number += 1
        name = f"{base}-{number}"
    return name


def _set_aside(target):
    """Move a damaged list out of the way, so the next transcript doesn't
    write over the ones in it (it may be mended, or read by hand). True when
    the way is clear."""
    aside = _aside_name(target)
    try:
        os.rename(target, aside)
    except OSError as e:
        logger.warning(f"The damaged list of kept transcripts can't be moved, so it's left "
                       f"as it is and this transcript isn't added: {e}")
        return False
    logger.warning(f"The damaged list of kept transcripts is set aside as {aside}")
    return True


def _write(entries):
    """Write the list whole or not at all, readable only by this user."""
    target = recent_path()
    tmp = f"{target}.tmp"
    data = [{"path": e.path, "total": e.total, "when": e.when,
             "pieces": [[p.start, p.end, p.text] for p in e.pieces]} for e in entries]
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, target)
        return True
    except OSError as e:
        logger.warning(f"Couldn't keep the transcripts for next time: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def remember(path, pieces, total):
    """Put this transcript first in the list (a file done again replaces its
    older copy) and keep MAX_RECENT. Returns the Kept, or None when it
    couldn't be written."""
    entry = Kept(path, list(pieces), total, time.time())
    kept, state = _read_kept()
    if state == _UNAVAILABLE or (state == _DAMAGED and not _set_aside(recent_path())):
        return None
    others = [e for e in kept if not (path and e.path == path)]
    entries = [entry] + others[:MAX_RECENT - 1]
    return entry if _write(entries) else None


def further_kept(path, pieces):
    """The kept copy of *path* that reaches further into the file than
    *pieces*, or None."""
    def reach(some):
        return some[-1].end if some else 0.0
    return next((e for e in recent() if e.path == path and reach(e.pieces) > reach(pieces)),
                None)


def forget_all():
    """Clear the list, the damaged ones set aside, an older TuxTalk's one
    transcript (the user asked for the transcripts gone; left, that one would
    come back as the list) and a half-written list a write killed midway left
    behind, which holds them in full. True when nothing is kept any more. The
    list goes last, so when something else can't go, what the window shows is
    still kept."""
    target = recent_path()
    folder = os.path.dirname(target)
    set_aside = re.compile(re.escape(RECENT_FILE) + SET_ASIDE_PATTERN)
    try:
        with os.scandir(folder) as found:
            copies = [entry.path for entry in found if set_aside.fullmatch(entry.name)]
    except FileNotFoundError:
        copies = []
    except OSError as e:
        logger.warning(f"Couldn't look for the earlier transcripts set aside: {e}")
        return False
    for name in (*copies, os.path.join(folder, OLD_LAST_FILE), f"{target}.tmp", target):
        try:
            os.remove(name)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"Couldn't clear the earlier transcripts: {e}")
            return False
    return True
