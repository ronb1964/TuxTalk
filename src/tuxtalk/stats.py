"""
Usage stats: how much you've dictated, and roughly how much typing it saved.

Privacy: TuxTalk promises that what you say never reaches the disk, so this
keeps numbers only. Per day: how many dictations, how many words, and how many
seconds of speech. Never the words themselves. The file lives in TuxTalk's
data folder on this computer and nothing sends it anywhere.

The dictation service records each dictation (app.py); Preferences reads the
file and shows the totals on its Stats tab. Only the service writes, one
dictation at a time, so a plain atomic rewrite is enough.
"""
import datetime
import json
import os
import re
import tempfile

from .logger import setup_logger
from .normalize import LINE_BREAK

logger = setup_logger(__name__)

# What "time saved" compares against, unless the user sets their own typing
# speed on the Stats tab (config typing_wpm). 40 words a minute is the usual
# figure for average typing speed.
TYPING_WPM = 40
MIN_TYPING_WPM, MAX_TYPING_WPM = 10, 200

CHART_DAYS = 14
_VERSION = 1

# A word is a run of letters or digits, apostrophes inside it included, so
# "it's" is one word and "3/4" is two numbers. Smart quotes and dashes don't
# count as words. Chinese and Japanese don't put spaces between words, so each
# of their characters counts as one word, the usual convention in word
# processors; otherwise a whole sentence would count as a single word.
# (Korean spaces its words, so it is counted like English.)
_CJK = "぀-ヿ㐀-䶿一-鿿豈-﫿"
_LETTERS = rf"(?:(?![{_CJK}])[^\W_])+"          # letters/digits, CJK excluded
_WORD_RE = re.compile(rf"[{_CJK}]|{_LETTERS}(?:['’]{_LETTERS})*")


def stats_path():
    """stats.json in TuxTalk's data folder."""
    from .config import get_data_dir
    return os.path.join(get_data_dir(), "stats.json")


def count_words(text):
    """How many words *text* holds, by _WORD_RE's rules."""
    return len(_WORD_RE.findall((text or "").replace(LINE_BREAK, " ")))


def _clean_day(value):
    """One day's counts, with anything malformed read as zero."""
    def num(key, kind):
        v = value.get(key, 0) if isinstance(value, dict) else 0
        return kind(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else kind(0)
    return {"dictations": num("dictations", int), "words": num("words", int),
            "seconds": num("seconds", float)}


def _read():
    """The stored days, or None if the file exists but can't be read."""
    try:
        with open(stats_path(), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning(f"Could not read usage stats: {e}")
        return None
    days = data.get("days", {}) if isinstance(data, dict) else {}
    clean = {}
    for day, value in (days.items() if isinstance(days, dict) else []):
        try:
            datetime.date.fromisoformat(day)
        except (TypeError, ValueError):
            continue
        clean[day] = _clean_day(value)
    return clean


def load():
    """Stored counts per day ({"2026-10-02": {...}}). Never raises."""
    return _read() or {}


def is_damaged():
    """True if a stats file exists but can't be read. record() then stops
    adding to it (rather than overwrite what might be recoverable), so
    Preferences says so instead of claiming nothing has been counted."""
    return _read() is None


def _write(days):
    """Atomic but light: a temp file renamed into place, so the file is never
    half-written, without forcing it to disk or keeping a backup copy. Stats
    are saved on every dictation and a lost count after a power cut doesn't
    matter, unlike settings (config.write_text_atomic)."""
    path = stats_path()
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".stats-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": _VERSION, "days": days}, f, indent=1)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def merge_days(theirs):
    """Add *theirs* (a backup's {day: counts}) to the stored days, keeping the
    higher word count for a day both have. Returns False, and writes nothing,
    when the stored file exists but can't be read: like record(), it leaves
    what might be recoverable alone."""
    days = _read()
    if days is None:
        return False
    for day, value in (theirs.items() if isinstance(theirs, dict) else []):
        try:
            datetime.date.fromisoformat(day)
        except (TypeError, ValueError):
            continue
        value = _clean_day(value)
        if day not in days or value["words"] > days[day]["words"]:
            days[day] = value
    _write(days)
    return True


def record(words, seconds, today=None):
    """Add one dictation to today's counts. Never raises: stats must not get
    in the way of the dictation itself. A file that can't be read is left
    alone rather than replaced, so a glitch can't wipe the history."""
    if words <= 0:
        return
    try:
        days = _read()
        if days is None:
            return
        day = (today or datetime.date.today()).isoformat()
        counts = days.setdefault(day, _clean_day({}))
        counts["dictations"] += 1
        counts["words"] += int(words)
        counts["seconds"] = round(counts["seconds"] + float(seconds), 1)
        _write(days)
    except Exception as e:
        logger.warning(f"Could not save usage stats: {e}")


def reset():
    """Forget all usage stats, including any stats.json.bak an earlier
    development build left behind (it saved through config.write_text_atomic,
    which keeps a backup). False when a file couldn't be deleted."""
    done = True
    for path in (stats_path(), stats_path() + ".bak"):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"Could not reset usage stats: {e}")
            done = False
    return done


def clamp_typing_wpm(wpm):
    """A usable typing speed: whole words a minute, MIN..MAX, 40 if unreadable."""
    try:
        return min(MAX_TYPING_WPM, max(MIN_TYPING_WPM, int(wpm)))
    except (TypeError, ValueError):
        return TYPING_WPM


def time_saved(words, seconds, wpm=TYPING_WPM):
    """Seconds saved: typing *words* at *wpm*, minus the time spent speaking."""
    return max(0, round(words / clamp_typing_wpm(wpm) * 60 - seconds))


def _totals(day_counts, wpm):
    """Summed counts and time saved over *day_counts*."""
    words = sum(d["words"] for d in day_counts)
    seconds = sum(d["seconds"] for d in day_counts)
    return {"dictations": sum(d["dictations"] for d in day_counts), "words": words,
            "seconds": seconds, "saved": time_saved(words, seconds, wpm)}


def summary(days, today=None, typing_wpm=TYPING_WPM):
    """Everything the Stats tab shows, worked out from load()'s days.
    *typing_wpm* is the user's typing speed, used for "time saved"."""
    today = today or datetime.date.today()
    by_date = {datetime.date.fromisoformat(k): v for k, v in days.items()}
    week_start = today - datetime.timedelta(days=6)
    everything = _totals(by_date.values(), typing_wpm)

    streak = 0
    day = today if today in by_date else today - datetime.timedelta(days=1)
    while by_date.get(day, {}).get("dictations"):
        streak += 1
        day -= datetime.timedelta(days=1)

    minutes = everything["seconds"] / 60
    return {
        "today": _totals([v for d, v in by_date.items() if d == today], typing_wpm),
        "week": _totals([v for d, v in by_date.items() if week_start <= d <= today],
                        typing_wpm),
        "all": everything,
        "wpm": round(everything["words"] / minutes) if minutes >= 0.5 else None,
        "streak": streak,
        "first_day": min(by_date) if by_date else None,
        "daily": [(today - datetime.timedelta(days=back),
                   by_date.get(today - datetime.timedelta(days=back), {}).get("words", 0))
                  for back in range(CHART_DAYS - 1, -1, -1)],
    }


def format_duration(seconds):
    """Short, readable duration: "under a minute", "12 min", "1 hr 25 min"."""
    seconds = int(round(seconds))
    if seconds == 0:
        return "0 min"
    if seconds < 60:
        return "under a minute"
    minutes = seconds // 60
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return f"{minutes} min"
    return f"{hours} hr" + (f" {minutes} min" if minutes else "")
