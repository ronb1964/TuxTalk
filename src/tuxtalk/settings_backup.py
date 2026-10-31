"""Back up and restore TuxTalk's settings.

Two uses:
- A backup file the user saves and loads from Preferences → Updates & Backup, to move
  to another computer or keep through a reinstall. It holds the settings, the
  snippets and voice commands, Your Words (with the fixes Fix a Word saves
  under them) and the usage stats.
- Automatic copies, so a change or an update that went wrong can be undone.
  The tray takes one at startup when the settings changed since the last one,
  and keeps the last KEEP. They hold the same but the stats: rolling back a
  setting mustn't take away today's word count.

Both are ordinary zip files with a small manifest, read by the same code. The
manifest lists the files there were none of, so restoring takes away the
snippets or words added since; a backup from before 0.19 lists none, and
leaves them be.
Models and the CUDA / Vulkan files are not included: they are large and
download again by themselves.
"""
import datetime
import json
import os
import re
import tempfile
import zipfile
import zlib
from dataclasses import dataclass

from . import config, stats
from .logger import setup_logger

logger = setup_logger(__name__)

# Kept from TalkType, so every backup ever saved still loads.
MANIFEST = "talktype-backup.json"
# The app names a backup may carry: TuxTalk, and TalkType for backups saved
# before the rename (they must keep loading).
_OUR_APP_NAMES = ("TuxTalk", "TalkType")
KEEP = 10                       # automatic copies kept
LIST_MAX = 15                   # restore points listed; more opens as a grid on Wayland
_SETTINGS = "config.toml"
_COMMANDS = "custom_commands.toml"
_WORDS = "custom_words.txt"          # Your Words (custom_words.py)
_STATS = "stats.json"
# The settings files a computer may not have yet, in words.
_OPTIONAL = (_COMMANDS, _WORDS)
_OPTIONAL_WORDS = {_COMMANDS: "snippets and voice commands", _WORDS: "Your Words"}
_COPY_NAME = re.compile(r"^settings-(\d{8}-\d{6})\.zip$")
_DEVICE_LINE = re.compile(r'^device\s*=.*$', re.MULTILINE)
_DEVICE_NAMES = {"cuda": "CUDA", "vulkan": "Vulkan"}
# Largest member read from a backup. Real ones are a few kilobytes; the cap
# keeps a damaged or crafted zip from filling memory while Preferences waits.
_MAX_MEMBER_BYTES = 4 * 1024 * 1024


def _parse_toml(text):
    """config's TOML reader: tomllib from Python 3.11, the toml package on
    3.10, which the AppImage runs, so importing tomllib directly here would
    crash the AppImage at startup."""
    return config._parse_toml_text(text)


class BackupError(Exception):
    """A file that isn't a TuxTalk backup, or is damaged. The message is
    written for the user."""


@dataclass
class Backup:
    """The contents of a backup file or automatic copy."""
    version: str                # the TuxTalk version that made it
    created: str                # ISO date and time
    files: dict                 # name in the zip -> bytes
    absent: tuple = ()          # _OPTIONAL files there were none of when it was made


@dataclass
class Snapshot:
    """A restore point Preferences lists: where it is and when it was made."""
    path: str
    when: datetime.datetime
    kind: str = "auto"          # "auto": a copy TuxTalk took; "file": the user's backup


def _now():
    return datetime.datetime.now()


def _uses_24_hour_clock():
    """Whether this computer's region shows 21:50 rather than 9:50 PM.

    Read from the region setting (LC_TIME), which KDE and GNOME both set
    from their own Region settings: en_US formats times as %r (with AM/PM),
    de_DE, en_GB and fr_FR as %T. The time-of-day codes that need AM/PM are
    %r, %I, %l and %p; anything else is a 24-hour clock.
    """
    import locale
    try:
        locale.setlocale(locale.LC_TIME, "")    # GTK has done this already; harmless again
        fmt = locale.nl_langinfo(locale.T_FMT)
    except (locale.Error, AttributeError, ValueError):
        return False
    return not any(code in fmt for code in ("%r", "%I", "%l", "%p"))


def when_label(when, now=None, twenty_four=None):
    """'Today 8:43 AM', 'Yesterday 9:50 PM' or 'Oct 10, 2026, 9:50 PM', with
    21:50 instead of 9:50 PM where the region uses a 24-hour clock."""
    now = now or _now()
    if twenty_four is None:
        twenty_four = _uses_24_hour_clock()
    clock = f"{when:%H:%M}" if twenty_four else f"{when:%-I:%M %p}"
    if when.date() == now.date():
        return f"Today {clock}"
    if when.date() == now.date() - datetime.timedelta(days=1):
        return f"Yesterday {clock}"
    # English month names whatever the region: %b would give "Okt 2" next to
    # the English "Today" on a German computer, and TuxTalk is in English.
    month = ("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec").split()[when.month - 1]
    return f"{month} {when.day}, {when.year}, {clock}"


def suggested_name():
    """Save Backup's file name. With the time, so a second backup the same
    day doesn't ask to replace the first."""
    return f"TuxTalk-settings-{_now():%Y-%m-%d-%H%M}.zip"


def _setting_files():
    """The settings files by their name inside a backup. Read at call time,
    so the paths follow DEV_MODE and Flatpak like the rest of config."""
    from . import custom_words
    return {_SETTINGS: config.CONFIG_PATH, _COMMANDS: config.CUSTOM_COMMANDS_PATH,
            _WORDS: custom_words.path()}


def snapshots_dir():
    """Where the automatic copies live, next to the settings."""
    return os.path.join(os.path.dirname(config.CONFIG_PATH), "backups")


_LAST_SAVED = "last-saved-backup"
_FOLDER = "backup-folder"
# Names the copy taken just before a restore, while the restore writes.
_PENDING = "restore-pending"
# A zip being written (_write_zip); one stays if TuxTalk is killed meanwhile.
_TEMP_PREFIX = ".talktype-backup-"


def is_ours(name):
    """Whether the file *name* in snapshots_dir() is one this module writes:
    the uninstall deletes only these, since someone may keep files of their
    own in a folder named backups."""
    return (name in (_LAST_SAVED, _FOLDER, _PENDING) or _COPY_NAME.match(name) is not None
            or (name.startswith(_TEMP_PREFIX) and name.endswith(".tmp")))


def _read_bytes(path):
    """*path*'s bytes, or None when there's no such file."""
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _current_files(with_stats):
    """This computer's settings files (and stats, if *with_stats*) by their name in a backup."""
    files = {}
    for name, path in _setting_files().items():
        data = _read_bytes(path)
        if data is not None:
            files[name] = data
    if with_stats:
        data = _read_bytes(stats.stats_path())
        if data is not None:
            files[_STATS] = data
    return files


def _write_zip(path, files):
    """Write the zip beside *path* and rename it into place, so a full disk
    never leaves half a backup. Readable by the user only: commands can hold
    an email address or a signature."""
    from . import __version__
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    manifest = {"app": "TuxTalk", "version": __version__,
                "created": _now().isoformat(timespec="seconds"),
                "absent": [name for name in _OPTIONAL if name not in files]}
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=_TEMP_PREFIX, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f, zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(MANIFEST, json.dumps(manifest, indent=1))
            for name, data in files.items():
                z.writestr(name, data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _last_saved_path():
    """When a backup file was last saved, for "Last saved:" in Preferences.
    Kept out of config.toml: Preferences writing a setting outside Apply/OK
    moves the baseline Apply compares against."""
    return os.path.join(snapshots_dir(), _LAST_SAVED)


def last_saved():
    """When a backup file was last saved here, or None."""
    data = _read_bytes(_last_saved_path())
    try:
        return datetime.datetime.fromisoformat(data.decode().strip()) if data else None
    except (UnicodeDecodeError, ValueError):
        return None


def _folder_path():
    """The folder the user picked for backup files, kept beside the copies
    for the same reason as _last_saved_path."""
    return os.path.join(snapshots_dir(), _FOLDER)


def backup_folder():
    """The folder the user picked for backup files, or None when none was
    picked or it no longer exists (deleted, renamed, an unplugged drive)."""
    data = _read_bytes(_folder_path())
    try:
        folder = data.decode().strip() if data else ""
    except UnicodeDecodeError:
        return None
    return folder if folder and os.path.isdir(folder) else None


def set_backup_folder(folder):
    """Remember *folder* for Save Backup and Load Backup to open in."""
    os.makedirs(snapshots_dir(), mode=0o700, exist_ok=True)
    with open(_folder_path(), "w") as f:
        f.write(folder)


def write_backup(path):
    """Save the settings, commands and stats to the backup file *path*."""
    files = _current_files(with_stats=True)
    if _SETTINGS not in files:
        raise BackupError("There are no settings to back up yet.")
    _write_zip(path, files)
    try:
        os.makedirs(snapshots_dir(), mode=0o700, exist_ok=True)
        with open(_last_saved_path(), "w") as f:
            f.write(_now().isoformat(timespec="seconds"))
    except OSError as e:
        logger.warning(f"Could not note when the backup was saved: {e}")


def read_backup(path):
    """Open a backup file or automatic copy. Raises BackupError when it isn't
    one, or its settings are damaged, before anything is changed."""
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if MANIFEST not in names:
                raise BackupError("This file isn't a TuxTalk settings backup.")
            wanted = [n for n in (MANIFEST, _SETTINGS, _COMMANDS, _WORDS, _STATS) if n in names]
            if any(z.getinfo(n).file_size > _MAX_MEMBER_BYTES for n in wanted):
                raise BackupError("This file is far too big to be a TuxTalk settings backup.")
            manifest = json.loads(z.read(MANIFEST))
            files = {n: z.read(n) for n in wanted if n != MANIFEST}
    except BackupError:
        raise
    except Exception as e:
        # Only zip reading and JSON parsing happen above, and damaged data
        # surfaces as many types (zlib.error, EOFError, RuntimeError for an
        # encrypted member, NotImplementedError for an unknown compression).
        raise BackupError(
            f"This file isn't a TuxTalk settings backup, or it's damaged ({e}).") from e
    return _checked(manifest, files)


def _checked(manifest, files):
    """The Backup read_backup read, as *manifest* and *files*. Raises
    BackupError when it isn't one of ours, or its settings are damaged."""
    if not isinstance(manifest, dict) or manifest.get("app") not in _OUR_APP_NAMES:
        raise BackupError("This file isn't a TuxTalk settings backup.")
    if _SETTINGS not in files:
        raise BackupError("This backup has no settings in it.")
    for name in (_SETTINGS, _COMMANDS):
        if name in files:
            try:
                _parse_toml(files[name].decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise BackupError(f"The settings in this backup are damaged ({name}).")
    if _WORDS in files:
        try:
            files[_WORDS].decode("utf-8")
        except UnicodeDecodeError:
            raise BackupError(f"The settings in this backup are damaged ({_WORDS}).")
    absent = manifest.get("absent")
    absent = tuple(name for name in _OPTIONAL if name not in files
                   and isinstance(absent, list) and name in absent)
    return Backup(version=str(manifest.get("version", "")),
                  created=str(manifest.get("created", "")), files=files, absent=absent)


def _device_works_here(device):
    """Whether this computer can use *device*. CPU always can."""
    if device == "cuda":
        from .cuda_helper import detect_nvidia_gpu
        return bool(detect_nvidia_gpu())
    if device == "vulkan":
        from . import whisper_vulkan
        return whisper_vulkan.is_offered()
    return True


def _fit_device(text, notes):
    """The restored settings *text*, with the device swapped for this
    computer's own when this computer can't use the one in the backup (a
    backup from an NVIDIA computer, loaded on one without). The rest of the
    settings come across unchanged."""
    wanted = _parse_toml(text).get("device")
    if not wanted or _device_works_here(wanted):
        return text
    current = _read_bytes(config.CONFIG_PATH)
    keep = "cpu"
    if current is not None:
        try:
            keep = _parse_toml(current.decode("utf-8")).get("device") or "cpu"
        except (UnicodeDecodeError, ValueError):
            pass
    if not _device_works_here(keep):
        keep = "cpu"
    name = _DEVICE_NAMES.get(wanted, wanted)
    notes.append(f"The backup uses {name}, which this computer doesn't have, so the "
                 f"Device stays on {_DEVICE_NAMES.get(keep, 'CPU')}.")
    return _DEVICE_LINE.sub(f'device = "{keep}"', text, count=1)


def _merge_stats(data, notes):
    """Add the backup's days to this computer's, keeping the higher count for
    a day both have, so loading a backup never loses any dictation."""
    try:
        theirs = json.loads(data).get("days", {})
    except (ValueError, AttributeError):
        return
    try:
        merged = stats.merge_days(theirs)
    except OSError as e:
        # The settings are already in place; a failed stats write must not
        # turn the whole restore into an error. The stats file itself is
        # untouched, so this note must not suggest resetting it.
        logger.warning(f"Could not add the backup's stats: {e}")
        notes.append("The backup's usage stats couldn't be saved, so your current "
                     "stats were left as they are.")
        return
    if not merged:
        notes.append("Your usage stats couldn't be read, so the backup's stats weren't "
                     "added to them. Reset Stats on the Stats tab starts them fresh.")


class _HalfRestored(BackupError):
    """A restore that failed part-way and couldn't put every file back."""


def _write_all_or_none(writes):
    """Write each (path, text) of *writes*, a text of None removing the
    file; if one fails (a full disk), put back the ones already written, so
    the settings are never half the backup's and half the old ones, and
    raise BackupError (_HalfRestored when a file couldn't be put back
    either). Every original is read first, as it is, so one that can't be
    read changes nothing."""
    try:
        before = {path: _read_bytes(path) for path, _ in writes}
    except OSError as e:
        raise BackupError("Your current settings couldn't be read, so nothing was "
                          f"changed ({e}).") from e
    written = []
    try:
        for path, text in writes:
            if text is not None:
                config.write_text_atomic(path, text)
            elif before[path] is not None:
                os.remove(path)
            written.append(path)
    except OSError as e:
        stuck = [path for path in reversed(written) if not _put_back(path, before[path])]
        if stuck:
            raise _HalfRestored(
                f"The backup couldn't be loaded ({e}), and {len(stuck)} of your settings files "
                "couldn't be put back either. The copy saved just before is under Earlier "
                "settings: restore it once there's room.") from e
        raise BackupError("The backup couldn't be loaded, so your settings are as they "
                          f"were ({e}).") from e


def _put_back(path, old) -> bool:
    """Undo one write of _write_all_or_none: *old* bytes, or no file. True
    when it was put back."""
    try:
        if old is None:
            os.remove(path)
        else:
            config.write_text_atomic(path, old)
        return True
    except OSError as e:
        logger.error(f"Couldn't put {path} back after a failed restore: {e}")
        return False


def _optional_paths():
    """Where each of _OPTIONAL is on this computer."""
    from . import custom_words
    return {_COMMANDS: config.CUSTOM_COMMANDS_PATH, _WORDS: custom_words.path()}


def removed_here(backup):
    """What restoring *backup* takes away from this computer, in words: the
    _OPTIONAL files it was made without that this computer has. A backup
    from a computer with no Your Words removes this one's, and the question
    before it and the note after it say so."""
    paths = _optional_paths()
    return [_OPTIONAL_WORDS[name] for name in backup.absent if os.path.exists(paths[name])]


def removal_note(backup, done=False):
    """The sentence saying what restoring *backup* removes here (or, once
    *done*, removed), or ""."""
    removed = " or ".join(removed_here(backup))
    if not removed:
        return ""
    return (f"This backup has no {removed}, so the ones on this computer "
            f"{'were' if done else 'are'} removed. Earlier settings, on the "
            "Updates & Backup tab, can bring them back.")


def _writes(backup, settings_text):
    """What restoring *backup* writes, as (path, text or None to remove),
    with *settings_text* for its settings."""
    paths = _optional_paths()
    return [(config.CONFIG_PATH, settings_text)] + [
        (paths[name], backup.files[name].decode("utf-8") if name in backup.files else None)
        for name in _OPTIONAL if name in backup.files or name in backup.absent]


def _pending_path():
    """The record of a restore under way (_PENDING)."""
    return os.path.join(snapshots_dir(), _PENDING)


def restore(backup):
    """Replace the settings and commands with *backup*'s, after saving the
    current ones as an automatic copy (so this can be undone). Stats, when
    the backup has them, are merged rather than replaced. While the files
    are written, a record names that copy: a restore cut off between them
    (killed, a power cut) is undone at the next start
    (recover_cut_off_restore).

    Returns notes for the user, such as a device kept for this computer.
    """
    notes = []
    try:
        # The copy just taken, or when none was needed the newest, which
        # holds these very files. Not simply the newest by name: names are
        # local times, and in the hour repeated when the clocks go back an
        # older copy can sort newer.
        undo = _snapshot()
        copies = list_snapshots()
        if undo is None and copies and os.path.exists(config.CONFIG_PATH):
            undo = copies[0].path
        if undo is not None:
            config.write_text_atomic(_pending_path(), os.path.basename(undo),
                                     keep_backup=False)
    except Exception as e:
        raise BackupError("Your current settings couldn't be saved as a copy first, "
                          f"so nothing was changed ({e}).") from e
    writes = _writes(backup, _fit_device(backup.files[_SETTINGS].decode("utf-8"), notes))
    removing = removal_note(backup, done=True)
    try:
        _write_all_or_none(writes)
    except _HalfRestored:
        raise           # the record stays: the next start puts the copy back
    except BackupError:
        _forget_pending()       # every file was put back
        raise
    _forget_pending()
    if removing:
        notes.append(removing)
    if _STATS in backup.files:
        _merge_stats(backup.files[_STATS], notes)
    logger.info(f"Settings restored from a backup made {backup.created} "
                f"by TuxTalk {backup.version}")
    return notes


def _forget_pending():
    """Remove the record of a restore under way, now it's over."""
    try:
        os.remove(_pending_path())
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(f"Couldn't remove the note of a finished restore: {e}")


def recover_cut_off_restore():
    """At start, before anything reads the settings: a restore cut off
    between its file writes left some files the backup's and the rest the
    old ones. The copy taken just before it is put back whole; one that
    can't be written now is tried again at the next start. Never raises: it
    runs before the tray exists, and an unreadable note must not stop
    TuxTalk starting."""
    try:
        _recover_cut_off_restore()
    except Exception as e:
        logger.warning(f"Could not check for a restore of the settings cut off part-way: {e}")


def _recover_cut_off_restore():
    """recover_cut_off_restore's work, which may raise."""
    data = _read_bytes(_pending_path())
    if data is None:
        return
    name = data.decode("utf-8", "replace").strip()
    try:
        if not _COPY_NAME.match(name):
            raise BackupError(f"the note names no copy ({name!r})")
        copy = read_backup(os.path.join(snapshots_dir(), name))
        # Taken from these very files just before: what it lacks wasn't
        # there, whatever an older manifest says.
        copy.absent = tuple(n for n in _OPTIONAL if n not in copy.files)
    except BackupError as e:
        logger.warning(f"A restore of the settings was cut off part-way, and the copy "
                       f"from before it can't be read: {e}")
        _forget_pending()
        return
    try:
        _write_all_or_none(_writes(copy, copy.files[_SETTINGS].decode("utf-8")))
    except BackupError as e:
        logger.warning(f"A restore of the settings was cut off part-way; the settings "
                       f"from before it couldn't be put back yet: {e}")
        return
    logger.info(f"Put back the settings from before a restore that was cut off part-way "
                f"({name})")
    _forget_pending()


def list_snapshots():
    """The automatic copies, newest first."""
    try:
        names = os.listdir(snapshots_dir())
    except FileNotFoundError:
        return []
    copies = []
    for name in names:
        match = _COPY_NAME.match(name)
        if not match:
            continue
        try:
            when = datetime.datetime.strptime(match.group(1), "%Y%m%d-%H%M%S")
        except ValueError:
            logger.info(f"Skipped {name}: its name holds no real date")
            continue
        copies.append(Snapshot(os.path.join(snapshots_dir(), name), when))
    return sorted(copies, key=lambda c: c.when, reverse=True)


def _made_at(path):
    """When the backup file *path* was made, from its manifest, or None when
    it isn't a TuxTalk backup. Reads only the manifest, so a folder of
    backups lists quickly."""
    try:
        with zipfile.ZipFile(path) as z:
            if z.getinfo(MANIFEST).file_size > _MAX_MEMBER_BYTES:
                return None     # read_backup turns it away too
            manifest = json.loads(z.read(MANIFEST))
        if manifest.get("app") not in _OUR_APP_NAMES:
            return None
        when = datetime.datetime.fromisoformat(manifest["created"])
    except (zipfile.BadZipFile, OSError, KeyError, ValueError, AttributeError, TypeError,
            zlib.error, EOFError, RuntimeError, NotImplementedError):
        # Damaged data surfaces as zlib.error or EOFError, an encrypted
        # member as RuntimeError, an unknown compression as NotImplementedError.
        return None
    # The copies' times are plain local ones; a zone (a hand-edited
    # manifest) can't be sorted beside them.
    return when.astimezone().replace(tzinfo=None) if when.tzinfo else when


def restore_points():
    """Everything Preferences offers to go back to, newest first, at most
    LIST_MAX: the automatic copies, and the user's own backup files in the
    backup folder they picked."""
    points = list_snapshots()
    folder = backup_folder()
    if folder:
        try:
            names = os.listdir(folder)
        except OSError:
            names = []
        for name in names:
            path = os.path.join(folder, name)
            if name.lower().endswith(".zip") and os.path.isfile(path):
                when = _made_at(path)
                if when:
                    points.append(Snapshot(path, when, "file"))
    # On a tie (a backup saved the second TuxTalk started), the user's file first.
    return sorted(points, key=lambda p: (p.when, p.kind == "file"), reverse=True)[:LIST_MAX]


def _snapshot():
    """Save the settings and commands as an automatic copy, unless they are
    the same as in the newest copy. Keeps the last KEEP. Returns the new
    copy's path, or None when nothing needed saving. Raises when the copy
    could not be written."""
    files = _current_files(with_stats=False)
    if _SETTINGS not in files:
        return None
    copies = list_snapshots()
    if copies:
        try:
            if read_backup(copies[0].path).files == files:
                return None
        except BackupError:
            pass                    # a damaged newest copy: take a fresh one
    # Named by the second, and a copy taken the same second as the last
    # one (a restore right after startup) must not overwrite it.
    when = _now()
    while True:
        path = os.path.join(snapshots_dir(), f"settings-{when:%Y%m%d-%H%M%S}.zip")
        if not os.path.exists(path):
            break
        when += datetime.timedelta(seconds=1)
    os.makedirs(snapshots_dir(), mode=0o700, exist_ok=True)
    _write_zip(path, files)
    logger.info(f"Saved a copy of the settings: {os.path.basename(path)}")
    # Once the new copy exists the undo point is there; failing to trim the
    # old ones is only untidy.
    for old in list_snapshots()[KEEP:]:
        try:
            os.remove(old.path)
        except OSError as e:
            logger.warning(f"Could not remove an old settings copy: {e}")
    return path


def snapshot_if_changed():
    """_snapshot() for the tray's startup. Never raises: a missed copy
    mustn't stop TuxTalk starting."""
    try:
        return _snapshot()
    except Exception as e:
        logger.warning(f"Could not save a copy of the settings: {e}")
        return None
