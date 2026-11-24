"""TuxTalk's settings on disk: loading, recovering and saving config.toml
under its lock, which settings apply live and which restart dictation, the
data folders and the snippets file. The fields themselves and the values
each accepts are settings_schema.py's."""
from __future__ import annotations
import contextlib
import dataclasses
import fcntl
import os
import shutil
import sys
import tempfile
import threading
import time
try:
    import tomllib  # Python 3.11+
    _USE_TOMLLIB = True  # tomllib requires binary mode ("rb")
except ImportError:
    import toml as tomllib  # Python 3.10 fallback
    _USE_TOMLLIB = False  # toml library requires text mode ("r")

from .logger import setup_logger
from .settings_schema import Settings, apply_toml, repaired, settings_text, toml_string

# setup_logger, so every process that reads settings also has the log file
# handler that "Damaged config…" and "Resetting invalid setting…" go to.
logger = setup_logger(__name__)

# Detect dev mode - use separate paths for dev vs production
DEV_MODE = os.environ.get("DEV_MODE") == "1"
CONFIG_DIR = "tuxtalk-dev" if DEV_MODE else "tuxtalk"
DATA_DIR = "TuxTalk-dev" if DEV_MODE else "TuxTalk"
# The desktop/AppStream/portal app ID. (The D-Bus name the tray, service and
# GNOME extension talk on deliberately stays io.github.ronb1964.TalkType: see
# dbus_service.py.)
APP_ID = "io.github.ronb1964.TuxTalk"


def _config_home():
    """Base config dir. Inside a Flatpak use the private per-app XDG_CONFIG_HOME
    so the app never adopts a HOST install's config when the user has granted
    --filesystem=home (e.g. a global override for GTK theming). Elsewhere it is
    ~/.config."""
    if os.environ.get("FLATPAK_ID"):
        return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.expanduser("~/.config")


def _data_home():
    """Base data dir. Private per-app XDG_DATA_HOME inside a Flatpak (same reason
    as _config_home); ~/.local/share elsewhere."""
    if os.environ.get("FLATPAK_ID"):
        return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.expanduser("~/.local/share")


CONFIG_PATH = os.path.join(_config_home(), CONFIG_DIR, "config.toml")


class ConfigNotLoadedError(Exception):
    """Refused to save because the settings on disk were never read.

    When load_config() cannot READ the file (permissions, a bad mount, an
    I/O error) it returns defaults so the app can keep running. Those
    defaults must never be written back: the file's contents may be
    perfectly good, and overwriting it destroys every setting the user has
    — silently, because the app looks like it started normally.
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def load_toml_file(path: str) -> dict:
    """Load a TOML file and return its data as a dict. Raises OSError if it
    can't be read, or a parse error if the content is not TOML."""
    file_mode = "rb" if _USE_TOMLLIB else "r"
    with open(path, file_mode) as f:
        return tomllib.load(f)


def parse_toml_text(text: str) -> dict:
    """Parse TOML from a string (tomllib, or the toml fallback's loads() on
    Python 3.10). Raises if *text* is not valid TOML."""
    return tomllib.loads(text)


# Set once a repair has been attempted in this process, so the tray's
# once-per-second polling cannot turn recovery into a write loop.
_repair_attempted = False


# Config cache — avoids re-reading TOML from disk when file hasn't changed.
# The tray polls load_config() every 1 second; this reduces disk I/O to near zero.
_config_cache = None
_config_stamp = None

# True when the last load_config() could not READ an existing settings file.
# Guards save_config() so the defaults handed out during the failure are never
# written over a file whose contents may be perfectly good.
_config_read_failed = False

# Set once a recovery was kept in memory, so its warning isn't logged again
# at every one of the tray's once-a-second reads.
_warned_in_memory = False


def _recover_damaged_config() -> tuple:
    """Set a damaged config aside, recover the last known good settings, and
    write them back so the file is actually repaired.

    Returns (the recovered Settings, whether the file was looked at under
    the lock): from the .bak written by the previous successful save when
    one exists, otherwise defaults. Under the lock, and only if the file is
    still damaged: another process may have repaired or saved it since it
    was read, and writing the backup's settings over that would undo the
    save (transcript logging turned off, say).
    """
    try:
        with locked():
            return _recover_if_still_damaged(), True
    except OSError as e:
        # No lock (a read-only folder) or no reading the file now: nothing
        # is set aside or written, which could land on a good save. Said
        # once: the tray tries again every second.
        global _warned_in_memory
        if not _warned_in_memory:
            logger.warning(f"Settings recovered in memory only, the file left alone: {e}")
            _warned_in_memory = True
        return _backup_settings()[0], False


def _recover_if_still_damaged() -> Settings:
    """Under the lock: the file's settings if it reads well now, else the
    recovery. A read error (OSError) is the caller's: that's no damage."""
    try:
        data = load_toml_file(CONFIG_PATH)
    except OSError:
        raise
    except Exception as e:
        logger.info(f"Settings file still damaged with the lock held ({e}); recovering")
        return _recover_from_backup()
    if not data:
        return _recover_from_backup()
    fresh = Settings()
    apply_toml(fresh, data)
    return fresh


def _backup_settings():
    """(the settings in config.toml.bak, or the defaults, whether they came
    from it)."""
    recovered = Settings()
    backup = f"{CONFIG_PATH}.bak"
    if os.path.exists(backup):
        try:
            data = load_toml_file(backup)
            if data:
                apply_toml(recovered, data)
                logger.info(f"Recovered settings from {backup}")
                return recovered, True
        except Exception as restore_error:
            logger.error(f"Backup at {backup} is also unreadable: {restore_error}")
    return recovered, False


def _recover_from_backup() -> Settings:
    """_recover_damaged_config's work, under the lock, the file damaged."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    quarantine = f"{CONFIG_PATH}.corrupt-{stamp}"
    quarantined = False
    try:
        shutil.copy2(CONFIG_PATH, quarantine)
        quarantined = True
        logger.error(f"Damaged config saved to {quarantine}")
    except OSError as copy_error:
        logger.error(f"Could not preserve damaged config: {copy_error}")

    recovered, restored_from_backup = _backup_settings()
    if not restored_from_backup:
        logger.warning("Settings have been reset to defaults")
    # Also on stderr: started from a terminal, that's where the user looks.
    print("TuxTalk's settings file was damaged"
          + (f"; a copy was kept at {quarantine}" if quarantined else "")
          + (". Your previous settings were restored from the backup." if restored_from_backup
             else ". Settings have been reset to defaults."), file=sys.stderr)

    # Only replace the file when its previous contents are safely stored
    # elsewhere. If the quarantine copy failed — an unreadable file, a full
    # disk — the original bytes are the user's only copy, so leave them be and
    # run from the in-memory recovery for this session.
    if quarantined:
        _persist_recovered_config(recovered)
    else:
        logger.warning("Damaged config left in place: it could not be copied aside first")
    return recovered


def _persist_recovered_config(recovered: Settings) -> None:
    """Replace the damaged file with the recovered settings.

    Recovering in memory alone would leave the damaged file in place, so every
    process that starts (tray, dictation service, Preferences) would quarantine
    it again, show the same alarming warning and add another
    config.toml.corrupt-* file, never fixing anything.

    keep_backup is off deliberately: the existing .bak is the last copy of the
    user's real settings, and backing up the damaged file first would overwrite
    it with garbage — removing the safety net at exactly the moment it matters.
    Ordinary saves keep refreshing the backup as usual.
    """
    global _repair_attempted
    if _repair_attempted:
        # Belt and braces: the tray polls load_config() about once a second, so
        # anything that lets the repair recur becomes a write every second for
        # as long as the app runs. One attempt per process is always enough.
        return
    _repair_attempted = True

    try:
        text = settings_text(dataclasses.asdict(recovered))

        # Never replace a damaged file with another damaged file. Writing
        # something that does not parse turns recovery into a livelock: the
        # next poll sees damage again, quarantines again, and rewrites again.
        parse_toml_text(text)

        write_text_atomic(CONFIG_PATH, text, keep_backup=False)
        logger.info("Damaged config replaced with recovered settings")
    except Exception as e:
        # A read-only home, a full disk, or settings that will not serialise
        # cleanly. The in-memory recovery is still usable for this run.
        logger.error(f"Could not write recovered settings back: {e}")


def file_stamp(path):
    """What tells one version of the file at *path* from the next, or None
    when it can't be seen: its time to the nanosecond, its inode (each atomic
    save is a new file) and its size. Seconds alone missed a second save in
    the same tick on a coarse clock."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_ino, st.st_size


def load_config() -> Settings:
    """The current settings, read from disk only when the file has changed."""
    global _config_cache, _config_stamp, _config_read_failed

    # Return cached config if the file hasn't been modified.
    # The stamp is recorded only AFTER a successful read (see the end of this
    # function), so a transient read error can't cache the defaults under the
    # real file's mtime and hide the good file for good.
    #
    # Every caller gets its own copy. Callers change the object they are given
    # before saving it; on the shared cached object an unsaved or failed change
    # would show in the tray and be written by the next unrelated save.
    current_stamp = file_stamp(CONFIG_PATH)
    # No file yet: the cache if there is one.
    if _config_cache is not None and current_stamp in (None, _config_stamp):
        return dataclasses.replace(_config_cache)

    _config_read_failed = False

    s = Settings()
    if os.path.exists(CONFIG_PATH):
        try:
            data = load_toml_file(CONFIG_PATH)
            # An existing file with nothing in it is damage, not configuration:
            # empty parses as valid TOML and would silently give all defaults,
            # including a blank hotkey, which stops dictation with no message.
            if not data:
                raise ValueError("config file is empty")
            # Apply TOML values using dataclass field types for casting
            apply_toml(s, data)
        except OSError as e:
            # The file could not be READ — permissions, a bad mount, too many
            # open handles. That is not corruption, and it must never trigger
            # the repair path: the contents may be perfectly good, and
            # rewriting them would destroy settings that only needed a chmod.
            logger.error(f"Could not read {CONFIG_PATH}: {e}; using defaults for now, "
                         "the file left untouched")
            s = dataclasses.replace(_config_cache) if _config_cache else Settings()
            # Hand out defaults so the app keeps running, but mark them as
            # never-loaded: save_config() must refuse to write them back over
            # a file it could not read, or any later save (the daily update
            # check's, say) would turn a chmod problem into the loss of every
            # setting.
            _config_read_failed = True
            current_stamp = None
        except Exception as e:
            # Genuinely unparseable content. Preserve the damaged file and fall
            # back to the last known good settings. Silently resetting to
            # defaults would look like a fresh install, and the next save would
            # overwrite the user's real settings for good.
            logger.error(f"Settings file is damaged: {e}")
            s, looked = _recover_damaged_config()
            if not looked:
                # Recovered in memory only: like a failed read, nothing may
                # save these over the file, and the next load tries it again.
                _config_read_failed = True
                current_stamp = None

    s = repaired(s)
    _config_cache = s
    # Only now is the cache known to reflect the file. On a failed read
    # current_stamp is None, so the next call re-reads instead of serving
    # defaults from a cache that looks up to date.
    _config_stamp = current_stamp
    return dataclasses.replace(s)


def saved_hold_key() -> str:
    """The push-to-talk key in the settings, F8 when there is none yet."""
    try:
        return load_config().hotkey or "F8"
    except Exception as e:
        logger.warning(f"Could not read the hold key from the settings: {e}")
        return "F8"


# The name write_text_atomic's temp file has after the file's own, as a
# regular expression (tempfile.mkstemp's eight characters): what a save
# killed part-way leaves beside the file.
ATOMIC_TEMP = r"\.[a-z0-9_]{8}\.tmp"


def write_text_atomic(path: str, text: str | bytes, keep_backup: bool = True,
                      mode: int | None = None) -> None:
    """Replace *path* with *text* without ever leaving it half-written. The
    new file is readable by this user only, unless *mode* says otherwise
    (set once it's whole, so it's private while being written).

    Writing in place would leave an empty file after an interruption (most
    realistically a full disk, which multi-gigabyte model downloads can
    cause), and every setting would fall back to its default, including the
    hotkey, which defaults to blank and silently stops dictation.

    Writing to a sibling temp file and renaming is atomic on POSIX: readers see
    either the old file or the new one, never a partial one. The fsync before
    the rename is what makes that hold across a power loss rather than just a
    crash.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    # A UNIQUE temp file per writer. TuxTalk runs three processes that all save
    # settings — the tray, the dictation service and Preferences — and a shared
    # temp name would let them truncate each other's half-written file and fail
    # the rename with FileNotFoundError.
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path) + ".",
                                    suffix=".tmp")
    try:
        # Bytes as they are: a file put back after a failed restore
        # (settings_backup) needn't be valid UTF-8.
        with os.fdopen(fd, "wb" if isinstance(text, bytes) else "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
            if mode is not None:
                os.fchmod(f.fileno(), mode)

        # An empty file is no backup: copied, it would replace the last good
        # copy with nothing, the one thing that could still recover it.
        if keep_backup and os.path.exists(path) and os.path.getsize(path) > 0:
            try:
                shutil.copy2(path, f"{path}.bak")
            except OSError as e:
                # A backup is a bonus, not a precondition for saving.
                logger.warning(f"Could not back up {path}: {e}")

        os.replace(tmp_path, path)
    except BaseException:
        # Never leave a stray temp file behind on failure.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# Which threads hold config.toml's lock now: a save inside an update (or a
# repair inside the read an update starts with) mustn't wait on itself.
_held = threading.local()


@contextlib.contextmanager
def locked():
    """config.toml's lock, held across a fresh read, the change and the
    write: two saves at once (Preferences' Apply, the tray's model switch,
    the update check's time) each read the file before the other wrote, and
    the later one undid the earlier, turning transcript logging back on, say.
    An flock on config.toml.lock beside it, so every TuxTalk process waits
    its turn; the kernel lets it go if one is killed holding it."""
    if getattr(_held, "depth", 0):
        _held.depth += 1
        try:
            yield
        finally:
            _held.depth -= 1
        return
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    fd = os.open(f"{CONFIG_PATH}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        _held.depth = 1
        try:
            yield
        finally:
            _held.depth = 0
    finally:
        os.close(fd)


def save_config(s: Settings) -> None:
    """Write every setting in *s* to config.toml: the whole settings, as a
    repair or a test sets them. A change of a few goes through save_changes,
    which reads the file afresh and keeps what it doesn't change.

    Refuses to write when the last load_config() could not read an existing
    settings file: what it returned were defaults, and writing those back
    destroys the real settings. Losing an update-check timestamp is a far
    better outcome than losing the user's hotkey, model and device.
    """
    with locked():
        if _config_read_failed and os.path.exists(CONFIG_PATH):
            raise ConfigNotLoadedError(
                f"Refusing to overwrite {CONFIG_PATH}: it could not be read, so the "
                f"settings in memory are defaults rather than the user's own."
            )
        write_text_atomic(CONFIG_PATH, settings_text(dataclasses.asdict(s)))


def save_changes(**changes) -> Settings:
    """Change the settings named in *changes* and nothing else, keys this
    version doesn't know kept too: read afresh, changed and written under
    the lock, so another process's save meanwhile stays. The settings now.
    Raises as rewrite_config does. A damaged or emptied file is repaired
    from its backup first (load_config), not saved over with one setting;
    one still empty (the repair is tried once per process) raises
    ConfigNotLoadedError and is left alone."""
    load_config()

    def merge(on_disk):
        if not on_disk and os.path.exists(CONFIG_PATH):
            raise ConfigNotLoadedError(f"{CONFIG_PATH} is empty; one setting won't be "
                                       "saved as all of them")
        return {**on_disk, **changes}
    rewrite_config(merge)
    return load_config()


def rewrite_config(merge) -> dict:
    """Write what *merge* makes of the file's raw contents ({key: value}, {}
    when there's no file yet), under the lock, keeping keys this version
    doesn't know: Preferences' merge-on-save. What was written. A file that
    can't be read or parsed is left alone: ConfigNotLoadedError (the next
    load_config repairs a damaged one)."""
    with locked():
        try:
            on_disk = load_toml_file(CONFIG_PATH) if os.path.exists(CONFIG_PATH) else {}
        except (OSError, ValueError) as e:      # a TOML error is a ValueError
            raise ConfigNotLoadedError(f"{CONFIG_PATH} could not be read: {e}") from e
        values = merge(on_disk)
        write_text_atomic(CONFIG_PATH, settings_text(values))
    return values


def changed_keys(original: dict, current: dict) -> set:
    """Keys whose value differs between *original* and *current*.

    A key missing from *original* counts as changed — it is a value the user
    has now set. Shared with merge_changed_keys so there is exactly one
    definition of "changed"; the Preferences restart decision and the config
    merge must never disagree about what the user touched.
    """
    return {
        key for key, value in current.items()
        if key not in original or original[key] != value
    }


def record_update_check(timestamp: str) -> None:
    """Persist the update-check timestamp without dragging other settings back.

    The update check spends up to ~30 s on the network, so the settings are
    re-read right before the write: a change made in Preferences meanwhile is
    kept, and only the timestamp is carried across. A file that can't be
    read is left alone, never overwritten with defaults. The caller's own
    settings are never what gets written.
    """
    try:
        save_changes(last_update_check=timestamp)
    except Exception as e:
        logger.warning(f"Could not save the update-check time: {e}")


def merge_changed_keys(original: dict, current: dict, base: dict) -> dict:
    """Overlay only the keys that changed between *original* and *current*
    onto *base*, returning base.

    Used by the Preferences window to avoid clobbering settings another
    process (the tray) wrote while the window was open: *original* is the
    config as loaded when the window opened, *current* is the UI state at
    save time, and *base* is a fresh read of the file on disk. Keys the
    user didn't touch keep their on-disk values; keys the user changed win.
    """
    for key in changed_keys(original, current):
        base[key] = current[key]
    return base


# ---------------------------------------------------------------------------
# Which settings need the dictation service restarted
# ---------------------------------------------------------------------------
#
# Restarting rebuilds the speech model — roughly ten seconds during which the
# hotkey does nothing at all — so only the settings that need it trigger one.
#
# Most settings are read off the long-lived cfg object at the point of use
# (every dictation ends by passing cfg.beeps, cfg.auto_period and the rest into
# stop_recording), so refreshing that object's fields makes them live. The rest
# are re-applied explicitly by the service's config watcher.

# The model is constructed once at startup; changing either rebuilds it.
RESTART_REQUIRED_KEYS = {
    "model",
    "device",
    "graphics_card",
    # Turning the indicator on would mean constructing a GTK window from the
    # polling path mid-flight. Low value, real risk — restart instead.
    "recording_indicator",
}

LIVE_APPLIED_KEYS = {
    # Read off cfg at the moment they are used — live once cfg is refreshed.
    "beeps",
    "notify",
    "smart_quotes",
    "language",
    "auto_space",
    "auto_period",
    "injection_mode",
    "auto_timeout_enabled",
    "auto_timeout_minutes",
    "mic",  # already re-resolved by the service when a stream fails

    # Copied into locals or module state at startup; the watcher re-applies them.
    "hotkey",
    "toggle_hotkey",
    "voice_commands_hotkey",
    "paste_last_hotkey",
    "mode",
    "key_delay",
    "indicator_position",
    "indicator_size",
    "indicator_offset_x",
    "indicator_offset_y",
    "indicator_style",
    "indicator_color_mode",
    "indicator_color",
    "indicator_sensitivity",
    "indicator_follows_window",

    # The dictation service never reads these at all — they belong to
    # Preferences and the tray. Listing them stops a "Launch at login" toggle
    # costing a ten-second model reload.
    "launch_at_login",
    "typing_wpm",          # only the Stats tab uses it
    "auto_check_updates",
    "last_update_check",
    "language_mode",
    # Read into a module global on each reload, used on each dictation.
    "auto_languages",
    # Read into a module global by the service so toggling transcript logging in
    # Preferences takes effect on the next utterance, no restart.
    "log_transcripts",
    # Dictation cleanup. Read on each utterance; turning AI corrections on or
    # off starts or stops its engine from the same reload (live_settings.apply).
    "remove_fillers",
    "swiss_spelling",
    "numbers_as_digits",
    "numbers_small_as_words",
    "voice_navigation",
    "open_apps",
    "live_text",
    "adapt_to_app",
    "press_enter_after",
    "press_enter_delay",
    "ai_corrections",
    "voice_editing",
    # Hands-free auto-stop; read when each toggle recording starts.
    "auto_stop_silence",
    "auto_stop_seconds",
    # Chunked dictation; read when each recording starts.
    "chunked_dictation",
    "chunk_pause_seconds",
    "chunk_context_marks",
    # Usage stats; read into a module global on each reload, checked per dictation.
    "usage_stats",
    # Read into a module global on each reload, checked on each paste.
    "restore_clipboard",
    # Read into a module global on each reload, checked when each recording starts.
    "pause_media",
    "duck_audio",
    # Read by the tray and first run (recommend.py), never by the service.
    "dictation_language",
    "recommend_notice_shown",
    "parakeet_update_offered",
    "vulkan_slower",
}


HOTKEY_KEYS = ("hotkey", "toggle_hotkey")


def unapplied_hotkeys(shown: dict, saved: dict) -> list:
    """Hotkey settings the user has changed but not yet applied.

    The hotkey test reports which ROLE the service matched (hold or toggle) and
    the dialog prints that against the key names currently shown in Preferences.
    Those agree only while the shown config matches the saved one the service
    reads. Change the hold key from F4 to F5 without applying, press F5, and the
    service — still on F4/F5 — matches its TOGGLE key, so the dialog ticks the
    row labelled F6. Nothing is wrong with the detection; the labels simply
    describe a config that is not in force yet.
    """
    return [k for k in HOTKEY_KEYS if shown.get(k) != saved.get(k)]


def needs_service_restart(changed: set) -> bool:
    """Whether a set of changed keys requires restarting the dictation service.

    Anything not explicitly known to be live counts as requiring a restart.
    That direction is deliberate: a setting added later then costs an
    unnecessary restart, which is merely slow. Defaulting the other way would
    make it silently fail to apply: a setting that lies about itself.
    """
    return bool(set(changed) - LIVE_APPLIED_KEYS)


def get_data_dir():
    """
    Get the data directory path based on dev mode.

    Returns:
        str: Path to ~/.local/share/TuxTalk (production) or ~/.local/share/TuxTalk-dev (dev mode).
             Inside a Flatpak this is the private per-app XDG_DATA_HOME/<DATA_DIR>.
    """
    return os.path.join(_data_home(), DATA_DIR)


# Custom voice commands configuration
CUSTOM_COMMANDS_PATH = os.path.join(_config_home(), CONFIG_DIR, "custom_commands.toml")

# True when the last load_custom_commands() could not read an existing file.
# Same guard as _config_read_failed: an empty dict produced by a read failure
# must never be written back over the user's real commands.
_commands_read_failed = False

def load_custom_commands() -> dict[str, str]:
    """
    Load custom voice commands from TOML file.

    Returns:
        dict: Mapping of spoken phrases to replacement text
              e.g., {"my email": "user@example.com", "signature": "Best regards,\\nAlex"}
    """
    global _commands_read_failed

    _commands_read_failed = False
    commands = {}
    if os.path.exists(CUSTOM_COMMANDS_PATH):
        try:
            data = load_toml_file(CUSTOM_COMMANDS_PATH)
            commands = _text_snippets(data.get("commands", {}))
        except Exception as e:
            logger.warning(f"Could not load the snippets: {e}")
            # Preferences rebuilds its list from whatever this returns and
            # writes it back on every Apply/OK, so a bare {} would delete every
            # custom command. Mark the failure so the writer refuses.
            _commands_read_failed = True
    return commands


def custom_commands_unreadable() -> bool:
    """Whether the last load_custom_commands() found the file but couldn't
    read it (a hand edit that broke it, say): the empty list it gave is not
    the user's, and saving is refused until the file is fixed."""
    return _commands_read_failed and os.path.exists(CUSTOM_COMMANDS_PATH)


def _text_snippets(table) -> dict[str, str]:
    """The snippets of a hand-editable table that can be typed: a number
    as its text (`answer = 42`), anything else (a list, a table) left out,
    since a dictation with that phrase would fail on it."""
    snippets = {}
    for phrase, text in table.items():
        if isinstance(text, (int, float)) and not isinstance(text, bool):
            text = str(text)
        if isinstance(text, str):
            snippets[phrase] = text
        else:
            logger.warning("A snippet in custom_commands.toml isn't text; it's left out, and "
                           "goes from the file the next time snippets are saved")
    return snippets


def merge_custom_command_edits(loaded: dict[str, str], edited: dict[str, str],
                               on_disk: dict[str, str]) -> dict[str, str]:
    """Apply the user's edits (from *loaded* to *edited*) on top of *on_disk*.

    Preferences holds the commands it loaded when it opened. Writing that list
    back as-is would delete anything saved in the meantime, such as a word fixed
    from the tray with Fix a Word. Only what the user added, changed or
    removed in Preferences is applied; everything else comes from the file.
    """
    merged = dict(on_disk)
    for phrase in loaded:
        if phrase not in edited:
            merged.pop(phrase, None)                 # removed in Preferences
    for phrase, replacement in edited.items():
        if loaded.get(phrase) != replacement:
            merged[phrase] = replacement             # added or changed there
    return merged


def save_custom_commands(commands: dict[str, str]) -> None:
    """
    Save custom voice commands to TOML file.

    Args:
        commands: Dictionary mapping spoken phrases to replacement text

    Raises:
        ConfigNotLoadedError: if the existing file could not be read. What the
            caller is holding is an empty list produced by that failure, not
            the user's commands, and writing it would delete all of them.
    """
    if custom_commands_unreadable():
        raise ConfigNotLoadedError(
            f"Refusing to overwrite {CUSTOM_COMMANDS_PATH}: it could not be read, "
            f"so the commands in memory are empty rather than the user's own."
        )

    lines = [
        "# TuxTalk Custom Voice Commands",
        '# Format: "spoken phrase" = "replacement text"',
        "# Use \\n for line breaks in replacements",
        "",
        "[commands]",
    ]
    for phrase, replacement in commands.items():
        # Escape BOTH sides: one unescaped double quote makes the whole file
        # unparseable, and every custom command stops working at once.
        lines.append(f'{toml_string(phrase)} = {toml_string(replacement)}')

    write_text_atomic(CUSTOM_COMMANDS_PATH, "\n".join(lines) + "\n")
