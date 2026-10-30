"""TuxTalk's settings: the Settings fields and their defaults, loading,
checking, repairing and saving config.toml, which settings apply live and
which restart dictation, the data folders, the microphone lookup and the
snippets file."""
from __future__ import annotations
import dataclasses
import os
import shutil
import subprocess
import sys
import tempfile
import time
try:
    import tomllib  # Python 3.11+
    _USE_TOMLLIB = True  # tomllib requires binary mode ("rb")
except ImportError:
    import toml as tomllib  # Python 3.10 fallback
    _USE_TOMLLIB = False  # toml library requires text mode ("r")
from dataclasses import dataclass, field, fields

from .logger import setup_logger
from .virtual_keyboard import KEY_DELAY_MS

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


class ConfigError(Exception):
    """One or more settings hold values the app cannot use.

    Carries the individual problems so a caller can show the user which setting
    is wrong instead of just failing.
    """

    def __init__(self, errors, fields=None):
        self.errors = list(errors)
        self.fields = list(fields or [])
        super().__init__("; ".join(self.errors))

# ---------------------------------------------------------------------------
# Validation constants — defined once, used by validate_config()
# ---------------------------------------------------------------------------

VALID_MODELS = {
    "tiny", "tiny.en",
    "base", "base.en",
    "small", "small.en",
    "medium", "medium.en",
    "large", "large-v1", "large-v2", "large-v3",
    # NVIDIA Parakeet, a different engine (see parakeet_engine.py).
    "parakeet-v3",
}

# vulkan: any GPU through whisper.cpp (whisper_vulkan.py)
VALID_DEVICES = {"cpu", "cuda", "vulkan"}
VALID_MODES = {"hold", "toggle"}

# The dictation keys (hotkey, toggle_hotkey), by the name the settings save and
# Preferences and Help show, with the key's evdev name. Many laptops and
# compact keyboards send F1-F12 only with Fn held (Discussion #18), so three
# keys that never need Fn come after them. Pause isn't one: most keyboards
# send its release the moment it's pressed, so it can't be held. Nor are
# Insert (a press that reached an editor would turn on overwrite) or Menu
# (the Compose key on many setups).
HOTKEY_CHOICES = {
    **{f"F{i}": f"KEY_F{i}" for i in range(1, 13)},
    "Right Ctrl": "KEY_RIGHTCTRL",
    "Right Alt": "KEY_RIGHTALT",
    "Scroll Lock": "KEY_SCROLLLOCK",
}
VALID_INJECTION_MODES = {"type", "paste", "auto"}

VALID_INDICATOR_POSITIONS = {
    "center", "top-left", "top-center", "top-right",
    "bottom-left", "bottom-center", "bottom-right",
    "left-center", "right-center",
}

VALID_INDICATOR_SIZES = {"small", "medium", "large"}
VALID_INDICATOR_STYLES = {"orb", "waveform", "bars", "radial"}
# Match the desktop accent, or pick a color. An older config's "cyan" mode is
# migrated to custom + CLASSIC_CYAN_HEX on load (see _migrate_settings).
VALID_COLOR_MODES = {"system", "custom"}

# The classic cyan the orb has always drawn, as a hex color. This is the exact
# blue of the original hand-tuned orb gradient (0.30, 0.70, 1.0), so when it is
# the chosen custom color the orb uses that original draw path and looks
# exactly as it always has. It also seeds the first swatch
# in the color picker so the classic color is always one click away.
CLASSIC_CYAN_HEX = "#4db3ff"


def locale_language(value):
    """"de_DE.UTF-8" -> "de"; None for "C", "POSIX" or anything unreadable."""
    value = (value or "").strip()
    if not value or value == "POSIX" or value.split(".")[0] == "C":
        return None
    code = value.split(".")[0].split("@")[0].split("_")[0].lower()
    return code if code.isalpha() and 2 <= len(code) <= 3 else None


def display_locale(env=None) -> str:
    """The locale the desktop's text is in, region and all where it's known
    ("de_CH.UTF-8"; "de" from LANGUAGE), or "" for the C locale or none.

    The first of LC_ALL, LC_MESSAGES and LANG that is set, unless LANGUAGE's
    first readable entry names another language or a region of its own, as
    in gettext (which ignores LANGUAGE when the locale is C). KDE sets
    LANGUAGE when the display language differs from the formats locale, and
    sets the bare "de" for a Swiss German user, which mustn't hide the de_CH
    locale."""
    env = os.environ if env is None else env
    locale = next((env[v] for v in ("LC_ALL", "LC_MESSAGES", "LANG") if env.get(v)), "")
    language = locale_language(locale)
    if not language:
        return ""               # C or POSIX wins over everything, LANGUAGE included
    first = next((e for e in (env.get("LANGUAGE") or "").split(":")
                  if locale_language(e)), None)
    if first and (locale_language(first) != language or "_" in first):
        return first
    return locale


def swiss_german_locale(env=None) -> bool:
    """Whether the system is set to Swiss German (de_CH, or gsw for Swiss
    German itself), where ß is never written."""
    return display_locale(env).replace("-", "_").startswith(("de_CH", "gsw"))


def has_battery(power_dir="/sys/class/power_supply") -> bool:
    """Whether this computer runs on a battery (a laptop). A wireless mouse's
    or keyboard's battery doesn't count: the kernel marks those scope=Device."""
    try:
        names = os.listdir(power_dir)
    except OSError:
        return False
    for name in names:
        def read(what):
            try:
                with open(os.path.join(power_dir, name, what)) as f:
                    return f.read().strip()
            except OSError:
                return ""
        if read("type") == "Battery" and read("scope") != "Device":
            return True
    return False


@dataclass
class Settings:
    """Every setting, with its default. The fields are what config.toml
    holds; a field added here is a new setting that older files don't have
    yet, so they get the default."""
    model: str = "parakeet-v3"  # parakeet-v3 or tiny/base/small/medium/large-v3 …
                                # Parakeet is the default: more accurate than large-v3 on
                                # English (Open ASR Leaderboard) and fast on a processor.
    # "cpu", "cuda" (NVIDIA via CUDA) or "vulkan" (any GPU via whisper.cpp)
    device: str = "cpu"
    hotkey: str = ""            # hold-to-talk hotkey (empty until user picks during onboarding)
    beeps: bool = True          # beeps on/off
    smart_quotes: bool = True   # "smart quotes"
    # ß -> ss in the finished text (Swiss Standard German); on by default on Swiss German systems
    swiss_spelling: bool = field(default_factory=swiss_german_locale)
    # Spoken numbers written as digits ("twenty-five" -> 25) in the finished text
    numbers_as_digits: bool = False
    # ...but a lone one to nine stays a word ("three cats", "25 chickens")
    numbers_small_as_words: bool = False
    # TuxTalk Labs: "select the last sentence", "go to the end" (voice_navigation.py)
    voice_navigation: bool = False
    # No closing period in terminals and code editors, no starting capital in terminals
    adapt_to_app: bool = True
    # Press Enter after every dictation, as if each ended with "press enter"
    press_enter_after: bool = False
    # ...after this many seconds, so a tap of the hotkey can hold it (0-4)
    press_enter_delay: float = 2.0
    mode: str = "hold"          # "hold" or "toggle"
    # used only when mode="toggle" (empty until user picks during onboarding)
    toggle_hotkey: str = ""
    mic: str = ""               # substring to match input device (empty = default)
    notify: bool = False        # desktop notifications
    language: str = ""          # optional language code (e.g., "en"); empty = auto-detect
    # prepend a space before new utterance when not starting a new line/tab
    auto_space: bool = True
    auto_period: bool = True   # append a period when an utterance ends without terminal punctuation
    # "auto" (smart detection), "type" (the virtual keyboard), or "paste" (wl-copy + Ctrl+V)
    injection_mode: str = "auto"
    key_delay: int = KEY_DELAY_MS  # milliseconds after each typed key event (1-50, higher = slower)
    # Turn dictation off after a while unused, to save power: on by default
    # on a laptop, off on a desktop, where F8 then simply always works.
    auto_timeout_enabled: bool = field(default_factory=lambda: has_battery())
    auto_timeout_minutes: int = 5       # minutes of inactivity before auto-stop
    recording_indicator: bool = True    # show visual recording indicator near cursor
    # screen position: center, top-left, top-center, top-right, bottom-left, bottom-center,
    # bottom-right, left-center, right-center
    indicator_position: str = "center"
    # custom X offset from position anchor (pixels, can be negative)
    indicator_offset_x: int = 0
    # custom Y offset from position anchor (pixels, can be negative)
    indicator_offset_y: int = 0
    indicator_size: str = "medium"      # indicator size: small, medium, large
    indicator_style: str = "orb"        # orb / waveform / bars / radial
    indicator_color_mode: str = "custom"  # system / custom (governs every style)
    indicator_color: str = CLASSIC_CYAN_HEX  # the chosen color; defaults to classic cyan
    indicator_sensitivity: float = 1.0  # scales how strongly audio drives the animation (0.5-2.0)
    # TuxTalk Labs: show near the bottom of the window being dictated into
    indicator_follow_window: bool = False
    voice_commands_hotkey: str = "Ctrl+Alt+V"  # hotkey combo to open voice commands quick reference
    # hotkey combo that types the last dictation again ("" = off)
    paste_last_hotkey: str = "Ctrl+Alt+P"
    auto_check_updates: bool = True      # automatically check for updates on startup (once per day)
    last_update_check: str = ""          # ISO timestamp of last update check
    # "auto" (detect language) or "manual" (use `language`); UI state for prefs
    language_mode: str = "auto"
    # auto-detect only picks from these, "en,es" (language_limit.py); "" = any
    auto_languages: str = ""
    # start TuxTalk automatically at login (prefs manages the autostart file)
    launch_at_login: bool = False
    # write full transcribed text to the log (off = redacted); opt-in for troubleshooting only
    log_transcripts: bool = False
    # drop "um"/"uh" and accidental repeated words (cleanup.py; rules, no download)
    remove_fillers: bool = False
    # fix "3, no wait, 4" with a local AI model (ai_cleanup.py downloads it)
    ai_corrections: bool = False
    # Shift + dictation key rewrites the selected text with the same AI model
    voice_editing: bool = False
    # toggle recordings stop by themselves when you stop talking (silence.py)
    auto_stop_silence: bool = False
    auto_stop_seconds: float = 2.0       # seconds of silence before an auto-stop (0.5-10)
    # type each phrase at the pause after it, while still recording (chunking.py; config.toml
    # only)
    chunked_dictation: bool = False
    # seconds of silence that end a chunk (0.3-3.0; kept below auto_stop_seconds)
    chunk_pause_seconds: float = 0.8
    # how much louder than the room speech must be, in dB (3-12)
    chunk_speech_db: float = 6.0
    # decide the mark at a pause by hearing both chunks together
    chunk_context_marks: bool = True
    # count words/dictations/speaking time per day (stats.py; numbers only, never text)
    usage_stats: bool = True
    # put back what the user had copied after pasting a dictation (clipboard.ClipboardKeeper,
    # issue #7)
    restore_clipboard: bool = False
    # the user's typing speed, for "time saved" on the Stats tab (10-200)
    typing_wpm: int = 40
    # language picked at first run (recommend.py); "" = system locale
    dictation_language: str = ""
    # the one-time "better setup" notice was shown (or not needed)
    recommend_notice_shown: bool = False
    # the one-time Parakeet Ultra offer was answered (tray.py)
    parakeet_update_offered: bool = False
    # the speed check found the processor faster than the graphics chip
    vulkan_slower: bool = False
    # pause playing music/videos while dictating, resume after (media_pause.py)
    pause_media: bool = True
    # turn other apps' sound down while dictating, back after (audio_duck.py)
    duck_audio: bool = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_toml_file(path: str) -> dict:
    """Load a TOML file and return its data as a dict. Raises OSError if it
    can't be read, or a parse error if the content is not TOML."""
    file_mode = "rb" if _USE_TOMLLIB else "r"
    with open(path, file_mode) as f:
        return tomllib.load(f)


def _parse_toml_text(text: str) -> dict:
    """Parse TOML from a string. Raises if *text* is not valid TOML."""
    if _USE_TOMLLIB:
        return tomllib.loads(text)
    return tomllib.loads(text)  # the toml fallback exposes loads() too


# Set once a repair has been attempted in this process, so the tray's
# once-per-second polling cannot turn recovery into a write loop.
_repair_attempted = False


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validation_problems(s: Settings) -> list[tuple[str, str]]:
    """Return (field name, message) for every setting holding an unusable value.

    Naming the field, not just the message, lets a caller repair that one
    setting instead of discarding the whole configuration.
    """
    problems = []

    if s.model not in VALID_MODELS:
        problems.append(("model", f"Invalid model '{s.model}'. "
                                  f"Valid options: {', '.join(sorted(VALID_MODELS))}"))

    if s.device.lower() not in VALID_DEVICES:
        problems.append(("device", f"Invalid device '{s.device}'. "
                                   "Must be 'cpu', 'cuda' or 'vulkan'"))

    if s.mode.lower() not in VALID_MODES:
        problems.append(("mode", f"Invalid mode '{s.mode}'. Must be 'hold' or 'toggle'"))

    if s.injection_mode.lower() not in VALID_INJECTION_MODES:
        problems.append(("injection_mode", f"Invalid injection_mode '{s.injection_mode}'. "
                                           "Must be 'type', 'paste', or 'auto'"))

    if s.auto_timeout_minutes <= 0:
        problems.append(("auto_timeout_minutes",
                         f"Invalid auto_timeout_minutes '{s.auto_timeout_minutes}'. "
                         "Must be positive"))

    if s.indicator_position.lower() not in VALID_INDICATOR_POSITIONS:
        problems.append(("indicator_position",
                         f"Invalid indicator_position '{s.indicator_position}'. "
                         f"Valid options: {', '.join(sorted(VALID_INDICATOR_POSITIONS))}"))

    if s.indicator_size.lower() not in VALID_INDICATOR_SIZES:
        problems.append(("indicator_size",
                         f"Invalid indicator_size '{s.indicator_size}'. "
                         f"Valid options: {', '.join(sorted(VALID_INDICATOR_SIZES))}"))

    if s.indicator_style.lower() not in VALID_INDICATOR_STYLES:
        problems.append(("indicator_style",
                         f"Invalid indicator_style '{s.indicator_style}'. "
                         f"Valid options: {', '.join(sorted(VALID_INDICATOR_STYLES))}"))

    if s.indicator_color_mode.lower() not in VALID_COLOR_MODES:
        problems.append(("indicator_color_mode",
                         f"Invalid indicator_color_mode '{s.indicator_color_mode}'. "
                         f"Valid options: {', '.join(sorted(VALID_COLOR_MODES))}"))

    if not (0.0 <= s.press_enter_delay <= 4.0):
        problems.append(("press_enter_delay", f"Invalid press_enter_delay '{s.press_enter_delay}'. "
                                              "Must be between 0 and 4"))
    if not (0.5 <= s.auto_stop_seconds <= 10.0):
        problems.append(("auto_stop_seconds", f"Invalid auto_stop_seconds '{s.auto_stop_seconds}'. "
                                              "Must be between 0.5 and 10"))
    if not (0.3 <= s.chunk_pause_seconds <= 3.0):
        problems.append(("chunk_pause_seconds",
                         f"Invalid chunk_pause_seconds '{s.chunk_pause_seconds}'. "
                         "Must be between 0.3 and 3.0"))
    if not (3.0 <= s.chunk_speech_db <= 12.0):
        problems.append(("chunk_speech_db", f"Invalid chunk_speech_db '{s.chunk_speech_db}'. "
                                            "Must be between 3 and 12"))
    if not (1 <= s.key_delay <= 50):
        problems.append(("key_delay", f"Invalid key_delay '{s.key_delay}'. "
                                      "Must be between 1 and 50"))
    if not (0.5 <= s.indicator_sensitivity <= 2.0):
        problems.append(("indicator_sensitivity",
                         f"Invalid indicator_sensitivity '{s.indicator_sensitivity}'. "
                         "Must be between 0.5 and 2.0"))

    return problems


def validate_config(s: Settings) -> None:
    """Raise ConfigError if any setting holds a value the app cannot use.

    Raises rather than exits: this runs inside load_config(), which the tray
    calls once per second, and a SystemExit would bypass the tray's
    `except Exception` and take its icon off the panel with no word about
    which setting was at fault.
    """
    problems = _validation_problems(s)
    if problems:
        raise ConfigError([msg for _, msg in problems],
                          fields=[name for name, _ in problems])


_CASE_FREE_CHOICES = ("device", "mode", "injection_mode", "indicator_position",
                      "indicator_size", "indicator_style", "indicator_color_mode")


def _migrate_settings(s: Settings) -> Settings:
    """Bring an older config forward to the current schema, in place.

    Runs before validation so retired-but-recognized values become their modern
    equivalents silently, instead of tripping validation and being blanked.

    - indicator_color_mode 'cyan' → 'custom' + CLASSIC_CYAN_HEX, which keeps
      exactly the color these users saw.
    - The choices validation accepts in any case are lowercased, since the
      rest of the app compares them as written ("Vulkan" blanked the
      indicator's device line).
    """
    for name in _CASE_FREE_CHOICES:
        value = getattr(s, name)
        if isinstance(value, str):
            setattr(s, name, value.lower())
    if s.indicator_color_mode == "cyan":
        s.indicator_color_mode = "custom"
        s.indicator_color = CLASSIC_CYAN_HEX
    return s


def repaired(s: Settings) -> Settings:
    """*s* as the service uses it: brought forward to the current schema,
    and each unusable value (nan or inf for a number too) back at its
    default. Preferences reads the file this way as well, so it shows what
    the service uses."""
    # Before validating, so retired values (the old 'cyan' color mode) become
    # their modern equivalents instead of tripping validation and being reset.
    _migrate_settings(s)
    try:
        validate_config(s)
    except ConfigError as e:
        # Keep the process alive. The tray polls this once a second, so a single
        # bad value must cost the user that setting, not their tray icon.
        s = _repair_invalid_fields(s, e)
    return s


def _repair_invalid_fields(s: Settings, error: ConfigError) -> Settings:
    """Reset only the settings that are unusable, keeping everything else.

    One bad value should cost the user that one setting, not their whole
    configuration and not a working tray icon.
    """
    defaults = Settings()
    for name in error.fields:
        default = getattr(defaults, name)
        logger.warning(f"Resetting invalid setting {name!r} to default {default!r}")
        setattr(s, name, default)
    return s


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------

# Type casters for dataclass fields — maps annotation strings to builtins.
# Needed because `from __future__ import annotations` stores types as strings.
def _to_bool(value):
    """A TOML boolean, or the words true/false a hand edit may have quoted.
    bool("false") is True: a quoted log_transcripts = "false" switched
    transcript logging on."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ValueError("not true or false")


def _to_int(value):
    """A TOML integer, or a number a hand edit may have quoted: "5" or "5.0"."""
    return int(float(value)) if isinstance(value, str) else int(value)


_TYPE_CASTERS = {"str": str, "bool": _to_bool, "int": _to_int, "float": float}


def apply_toml(s: Settings, data: dict) -> list[str]:
    """Copy the values in *data* onto *s*, casting each to its field's type.

    A value that will not cast (a hand-edited `key_delay = "fast"`) keeps
    that one setting at its default instead of making the whole file look
    damaged. Returns the names of the settings it skipped.
    """
    skipped = []
    for fld in fields(s):
        if fld.name not in data:
            continue
        cast = _TYPE_CASTERS.get(fld.type, str)
        try:
            setattr(s, fld.name, cast(data[fld.name]))
        except (TypeError, ValueError, OverflowError) as e:
            # int() raises OverflowError, not ValueError, for a TOML `inf`.
            logger.warning(f"Ignoring setting {fld.name!r} = {data[fld.name]!r}: {e}")
            skipped.append(fld.name)
    return skipped


# Config cache — avoids re-reading TOML from disk when file hasn't changed.
# The tray polls load_config() every 1 second; this reduces disk I/O to near zero.
_config_cache = None
_config_mtime = 0.0

# True when the last load_config() could not READ an existing settings file.
# Guards save_config() so the defaults handed out during the failure are never
# written over a file whose contents may be perfectly good.
_config_read_failed = False

def _recover_damaged_config() -> Settings:
    """Set a damaged config aside, recover the last known good settings, and
    write them back so the file is actually repaired.

    Returns the recovered Settings — from the .bak written by the previous
    successful save when one exists, otherwise defaults.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    quarantine = f"{CONFIG_PATH}.corrupt-{stamp}"
    quarantined = False
    try:
        shutil.copy2(CONFIG_PATH, quarantine)
        quarantined = True
        logger.error(f"Damaged config saved to {quarantine}")
    except OSError as copy_error:
        logger.error(f"Could not preserve damaged config: {copy_error}")

    recovered = Settings()
    restored_from_backup = False
    backup = f"{CONFIG_PATH}.bak"
    if os.path.exists(backup):
        try:
            data = _load_toml_file(backup)
            if data:
                apply_toml(recovered, data)
                restored_from_backup = True
                logger.info(f"Recovered settings from {backup}")
        except Exception as restore_error:
            logger.error(f"Backup at {backup} is also unreadable: {restore_error}")

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
        lines = ["# TuxTalk config"]
        for fld in fields(recovered):
            lines.append(f"{fld.name} = {_toml_value(getattr(recovered, fld.name))}")
        text = "\n".join(lines) + "\n"

        # Never replace a damaged file with another damaged file. Writing
        # something that does not parse turns recovery into a livelock: the
        # next poll sees damage again, quarantines again, and rewrites again.
        _parse_toml_text(text)

        write_text_atomic(CONFIG_PATH, text, keep_backup=False)
        logger.info("Damaged config replaced with recovered settings")
    except Exception as e:
        # A read-only home, a full disk, or settings that will not serialise
        # cleanly. The in-memory recovery is still usable for this run.
        logger.error(f"Could not write recovered settings back: {e}")


def load_config() -> Settings:
    """The current settings, read from disk only when the file has changed."""
    global _config_cache, _config_mtime, _config_read_failed

    # Return cached config if the file hasn't been modified.
    # The mtime is recorded only AFTER a successful read (see the end of this
    # function), so a transient read error can't cache the defaults under the
    # real file's mtime and hide the good file for good.
    #
    # Every caller gets its own copy. Callers change the object they are given
    # before saving it; on the shared cached object an unsaved or failed change
    # would show in the tray and be written by the next unrelated save.
    current_mtime = None
    try:
        current_mtime = os.path.getmtime(CONFIG_PATH)
        if _config_cache is not None and current_mtime == _config_mtime:
            return dataclasses.replace(_config_cache)
    except OSError:
        # File doesn't exist yet — return cache if we have one
        if _config_cache is not None:
            return dataclasses.replace(_config_cache)

    _config_read_failed = False

    s = Settings()
    if os.path.exists(CONFIG_PATH):
        try:
            data = _load_toml_file(CONFIG_PATH)
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
            current_mtime = None
        except Exception as e:
            # Genuinely unparseable content. Preserve the damaged file and fall
            # back to the last known good settings. Silently resetting to
            # defaults would look like a fresh install, and the next save would
            # overwrite the user's real settings for good.
            logger.error(f"Settings file is damaged: {e}")
            s = _recover_damaged_config()

    s = repaired(s)
    _config_cache = s
    # Only now is the cache known to reflect the file. On a failed read
    # current_mtime is None, so the next call re-reads instead of serving
    # defaults from a cache that looks up to date.
    _config_mtime = current_mtime
    return dataclasses.replace(s)


def saved_hold_key() -> str:
    """The push-to-talk key in the settings, F8 when there is none yet."""
    try:
        return load_config().hotkey or "F8"
    except Exception as e:
        logger.warning(f"Could not read the hold key from the settings: {e}")
        return "F8"


def _toml_value(val) -> str:
    """Format a Python value as a TOML literal.

    Strings go through _toml_string so quotes, backslashes and newlines are
    escaped: a microphone named 'Blue Yeti "Nano" USB' in bare quotes would
    make the file unparseable, and the next load would treat it as damaged.
    """
    if isinstance(val, bool):
        return str(val).lower()     # true / false
    if isinstance(val, int):
        return str(val)             # bare integer
    if isinstance(val, float):
        return repr(val)            # bare float — else it round-trips as a string
    return _toml_string(str(val))


# Characters with a short TOML escape. Every other control character (except
# tab, which TOML allows as is) is written as \uXXXX.
_TOML_SHORT_ESCAPES = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\n": "\\n",
                       "\f": "\\f", "\r": "\\r"}


def _toml_string(value: str) -> str:
    """Quote and escape *value* as a TOML basic string.

    TOML forbids raw control characters in a basic string, so a single one (a
    pasted Windows line break's \\r) would make the whole file unreadable.
    """
    out = []
    for ch in value:
        if ch in _TOML_SHORT_ESCAPES:
            out.append(_TOML_SHORT_ESCAPES[ch])
        elif (ord(ch) < 0x20 and ch != "\t") or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


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

        if keep_backup and os.path.exists(path):
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


def save_config(s: Settings) -> None:
    """Save Settings to TOML file.

    Uses dataclass introspection so new fields are automatically included.

    Refuses to write when the last load_config() could not read an existing
    settings file: what it returned were defaults, and writing those back
    destroys the real settings. Losing an update-check timestamp is a far
    better outcome than losing the user's hotkey, model and device.
    """
    if _config_read_failed and os.path.exists(CONFIG_PATH):
        raise ConfigNotLoadedError(
            f"Refusing to overwrite {CONFIG_PATH}: it could not be read, so the "
            f"settings in memory are defaults rather than the user's own."
        )

    lines = ["# TuxTalk config"]
    for fld in fields(s):
        lines.append(f"{fld.name} = {_toml_value(getattr(s, fld.name))}")
    write_text_atomic(CONFIG_PATH, "\n".join(lines) + "\n")


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
    kept, and only the timestamp is carried across. save_config refuses to
    write when that read failed, so a damaged config is never overwritten with
    defaults. The caller's own settings are never what gets written.
    """
    try:
        fresh = load_config()
    except Exception as e:
        logger.warning(f"Could not record the update-check time: {e}")
        return
    fresh.last_update_check = timestamp
    try:
        save_config(fresh)
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
    "indicator_follow_window",

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
    "chunk_speech_db",
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


# ---------------------------------------------------------------------------
# Audio device detection
# ---------------------------------------------------------------------------

def default_source_name() -> str | None:
    """PipeWire's default microphone, by its unique node name, or None.

    Used to notice the desktop switching its default input (GNOME Quick
    Settings, KDE's volume applet). None when wpctl is missing or fails, so a
    system without PipeWire never looks like a switch.
    """
    try:
        # errors="replace": a device name in another encoding is still a name.
        result = subprocess.run(["wpctl", "inspect", "@DEFAULT_SOURCE@"],
                                capture_output=True, text=True, errors="replace", timeout=2)
    except (OSError, subprocess.SubprocessError):   # no wpctl, or it hung
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        key, sep, value = line.strip().lstrip("*").partition("=")
        if sep and key.strip() == "node.name":
            return value.strip().strip('"') or None
    return None


def find_input_device(mic_substring: str | None) -> int | None:
    """Find the best input device index for recording.

    When mic_substring is set, finds a device whose name contains it.
    When empty/None, auto-detects the system's default microphone via
    PipeWire (wpctl) instead of relying on PortAudio's ALSA default,
    which can return garbage audio on PipeWire systems with virtual
    128-channel default devices.

    Returns:
        Device index for sounddevice, or None to use sounddevice's default.
    """
    try:
        import sounddevice as sd
        q = sd.query_devices()
    except Exception as e:      # no PortAudio, or it can't list: sounddevice's default
        logger.warning(f"Could not list the sound devices: {e}")
        return None

    # --- User specified a mic name — find it by substring match ---
    if mic_substring:
        m = mic_substring.lower()
        candidates = [
            (i, d) for i, d in enumerate(q)
            if d.get("max_input_channels", 0) > 0
            and m in d.get("name", "").lower()
        ]
        if candidates:
            return candidates[0][0]
        return None  # Not found — fall back to sounddevice default

    # --- No mic configured — auto-detect the real default source ---
    # PipeWire's ALSA "default" device (128 channels) can return clipped
    # garbage audio instead of actual mic input. We ask PipeWire directly
    # for the real default source name, then match it in sounddevice's list.
    default_source = None   # PipeWire's default source, when it could say
    try:
        result = subprocess.run(
            ["wpctl", "inspect", "@DEFAULT_SOURCE@"],
            capture_output=True, text=True, errors="replace", timeout=2
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                stripped = line.strip()
                # Look for node.nick (short name) or node.description (full name)
                if "node.nick" in stripped or "node.description" in stripped:
                    parts = stripped.split("=", 1)
                    if len(parts) == 2:
                        source_name = parts[1].strip().strip('"').strip()
                        if not source_name:
                            continue
                        default_source = source_name
                        # Find this device in sounddevice's list, skipping
                        # virtual devices with unrealistically many channels
                        for i, d in enumerate(q):
                            if (d.get("max_input_channels", 0) > 0
                                    and d.get("max_input_channels", 0) < 10
                                    and source_name.lower()
                                    in d.get("name", "").lower()):
                                logger.info(
                                    f"Auto-detected mic: [{i}] {d['name']}"
                                    " (via PipeWire default source)")
                                return i
    except (OSError, subprocess.SubprocessError):
        pass  # wpctl not installed (not a PipeWire system), or it hung

    # --- The default is virtual (noise suppression, a remapped source) ---
    # PortAudio lists only hardware under their own names, so a virtual
    # default source has no entry. Its "pipewire" device records from
    # whatever the default source is; a raw sound card would bypass it.
    if default_source:
        for i, d in enumerate(q):
            if (d.get("name", "").lower() in ("pipewire", "pulse")
                    and d.get("max_input_channels", 0) > 0):
                logger.info(f"Auto-detected mic: [{i}] {d['name']}"
                            f" (the default source {default_source!r} is not a hardware device)")
                return i

    # --- Fallback: pick a real hardware mic, avoiding virtual devices ---
    # Filter out monitors, virtual sinks, and PipeWire wrappers
    skip_names = {"monitor", "default", "pipewire", "sysdefault", "spdif"}
    hw_mics = [
        (i, d) for i, d in enumerate(q)
        if d.get("max_input_channels", 0) > 0
        and d.get("max_input_channels", 0) < 10
        and not any(s in d.get("name", "").lower() for s in skip_names)
    ]
    if hw_mics:
        # Prefer USB mics (external) over built-in analog
        usb = [(i, d) for i, d in hw_mics
               if "usb" in d.get("name", "").lower()]
        chosen_i, chosen_d = (usb or hw_mics)[0]
        logger.info(
            f"Auto-detected mic (hardware fallback):"
            f" [{chosen_i}] {chosen_d['name']}")
        return chosen_i

    return None  # Last resort: let sounddevice use its own default


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
            data = _load_toml_file(CUSTOM_COMMANDS_PATH)
            commands = _text_snippets(data.get("commands", {}))
        except Exception as e:
            logger.warning(f"Could not load the snippets: {e}")
            # Preferences rebuilds its list from whatever this returns and
            # writes it back on every Apply/OK, so a bare {} would delete every
            # custom command. Mark the failure so the writer refuses.
            _commands_read_failed = True
    return commands

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
    if _commands_read_failed and os.path.exists(CUSTOM_COMMANDS_PATH):
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
        lines.append(f'{_toml_string(phrase)} = {_toml_string(replacement)}')

    write_text_atomic(CUSTOM_COMMANDS_PATH, "\n".join(lines) + "\n")
