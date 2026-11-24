"""TuxTalk's settings as values: the Settings fields and their defaults,
which values each accepts, how an older or hand-edited config.toml's values
are brought forward or set back to their default, the file's text for a
set of values, and which changed settings the dictation service takes on
without a restart. Reading, locking and saving the file is config.py's."""
from __future__ import annotations
import re
from dataclasses import dataclass, field, fields

from .dictation_keys import same_last_key, problem as dictation_key_problem
from .language_codes import swiss_german_locale
from .logger import setup_logger

logger = setup_logger(__name__)

# key_delay's default: the wait after each key event the virtual keyboard
# sends (virtual_keyboard.py). Measured in the test VMs: at 1 ms GTK 3 and 4,
# Qt, Xwayland, gnome-terminal, Konsole, Firefox and Brave (Chromium, the
# engine of Electron apps) all got every character of a 200-character text
# right, also with every processor busy; with no wait the kernel's buffer
# overflowed and whole words were lost. That buffer holds about 64 key events
# while the desktop isn't reading, so the wait also sets how long a freeze of
# the desktop typing survives: 64 times it. Frozen on purpose mid-text, KWin
# lost letters after 100 ms at 1 ms and after 200 ms at 2 ms, none at 4 ms.
# The 12 ms TuxTalk once typed at came from letters arriving out of order at
# 5 ms through ydotoold, a daemon in between that this keyboard doesn't
# have. The key_delay setting raises it for a computer that loses letters.
KEY_DELAY_MS = 2


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
# The values each setting accepts, used by validate_config()
# ---------------------------------------------------------------------------

# The models TuxTalk downloads and offers, smallest first: every screen with a
# model picker builds from this, so their lists can't drift apart. Parakeet
# comes last: it is a different engine (parakeet_engine.py), not part of
# Whisper's size ladder.
VALID_MODELS = ("tiny", "base", "small", "medium", "large-v3", "parakeet-v3")
# Whisper names TuxTalk neither offers nor downloads, which an older config
# may hold, as the offered model nearest to each: model_helper can find and
# fetch only VALID_MODELS.
OLD_MODEL_NAMES = {"tiny.en": "tiny", "base.en": "base", "small.en": "small",
                   "medium.en": "medium", "large": "large-v3", "large-v1": "large-v3",
                   "large-v2": "large-v3"}

# vulkan: any GPU through whisper.cpp (whisper_vulkan.py)
VALID_DEVICES = {"cpu", "cuda", "vulkan"}
VALID_MODES = {"hold", "toggle"}

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
    # On a computer with more than one card, the one to use, by name
    # (graphics_cards.py); empty lets each engine choose
    graphics_card: str = ""
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
    # TuxTalk Labs: apps, windows, keys and the mouse by voice ("open Firefox",
    # "close window", "new tab", "mouse grid"); the name is from when it only opened apps
    open_apps: bool = False
    # TuxTalk Labs: what's been said so far, in a box by the indicator
    # while dictating (live_text.py)
    live_text: bool = False
    # No closing period in terminals and code editors, no starting capital in terminals
    adapt_to_app: bool = True
    # Press Enter after every dictation, as if each ended with "press enter"
    press_enter_after: bool = False
    # ...after this many seconds, so a tap of the hotkey can hold it (0-4)
    press_enter_delay: float = 2.0
    mode: str = "hold"          # "hold" or "toggle"
    # the tap-to-start, tap-to-stop key, on beside the hold key (empty: none)
    toggle_hotkey: str = ""
    mic: str = ""               # substring to match input device (empty = default)
    notify: bool = False        # desktop notifications
    language: str = ""          # optional language code (e.g., "en"); empty = auto-detect
    # a space after each dictation, so the next one doesn't run into it (none
    # after a line break); TuxTalk can't see what's typed by hand before one
    auto_space: bool = True
    auto_period: bool = True   # append a period when an utterance ends without terminal punctuation
    # "auto" (smart detection), "type" (the virtual keyboard), or "paste" (wl-copy + Ctrl+V)
    injection_mode: str = "auto"
    key_delay: int = KEY_DELAY_MS  # milliseconds after each typed key event (1-50, higher = slower)
    # Turn dictation off after a while unused, to save power. Off here; the
    # platform layer turns it on for a laptop (config.defaults()), and on a
    # desktop F8 then simply always works.
    auto_timeout_enabled: bool = False
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
    # show in the window being dictated into, not at a fixed spot on the screen
    indicator_follows_window: bool = True
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
    # fix "3, no wait, 4" with a local AI model (ai_download.py downloads it)
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
# Validation
# ---------------------------------------------------------------------------

def _hotkey_problems(s: Settings) -> list[tuple[str, str]]:
    """The dictation keys that can't be: a key that types ("d" written by
    hand) would start a dictation with every one typed, and a toggle key
    ending in the hold key's own key (Alt+D, Ctrl+D) would be read as it."""
    problems = [(name, why) for name in HOTKEY_KEYS
                if (why := dictation_key_problem(getattr(s, name)))]
    if not problems and s.toggle_hotkey and same_last_key(s.hotkey, s.toggle_hotkey):
        problems.append(("toggle_hotkey", f"{s.toggle_hotkey} ends in the same key as "
                         f"the hold key {s.hotkey}"))
    return problems


def _validation_problems(s: Settings) -> list[tuple[str, str]]:
    """Return (field name, message) for every setting holding an unusable value.

    Naming the field, not just the message, lets a caller repair that one
    setting instead of discarding the whole configuration.
    """
    problems = []

    problems += _hotkey_problems(s)

    if s.model not in VALID_MODELS:
        problems.append(("model", f"Invalid model '{s.model}'. "
                                  f"Valid options: {', '.join(VALID_MODELS)}"))

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
    - Live text and Type at pause are one choice in Preferences: with both
      on, Type at pause goes off. Together, the box showed each phrase and
      then emptied as it was typed.
    - A model name no longer offered becomes its nearest (OLD_MODEL_NAMES).
    """
    for name in _CASE_FREE_CHOICES:
        value = getattr(s, name)
        if isinstance(value, str):
            setattr(s, name, value.lower())
    if s.indicator_color_mode == "cyan":
        s.indicator_color_mode = "custom"
        s.indicator_color = CLASSIC_CYAN_HEX
    if s.live_text and s.chunked_dictation:
        s.chunked_dictation = False
    if s.model in OLD_MODEL_NAMES:
        logger.info(f"Model {s.model!r} is no longer offered; using {OLD_MODEL_NAMES[s.model]}")
        s.model = OLD_MODEL_NAMES[s.model]
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


def _toml_value(val) -> str:
    """Format a Python value as a TOML literal.

    Strings go through toml_string so quotes, backslashes and newlines are
    escaped: a microphone named 'Blue Yeti "Nano" USB' in bare quotes would
    make the file unparseable, and the next load would treat it as damaged.
    """
    if isinstance(val, bool):
        return str(val).lower()     # true / false
    if isinstance(val, int):
        return str(val)             # bare integer
    if isinstance(val, float):
        return repr(val)            # bare float — else it round-trips as a string
    # A hand-written key Preferences keeps (settings_text) may hold these.
    if isinstance(val, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in val) + "]"
    if isinstance(val, dict):
        return "{" + ", ".join(f"{_toml_key(k)} = {_toml_value(v)}"
                               for k, v in val.items()) + "}"
    return toml_string(str(val))


# A key TOML takes without quotes; any other ("my note") is quoted, or the
# file no longer parses and the next read restores the backup.
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def _toml_key(key) -> str:
    """*key* as TOML writes it: bare when it can be, else a quoted string."""
    key = str(key)
    return key if _BARE_KEY.fullmatch(key) else toml_string(key)


def settings_text(values: dict) -> str:
    """config.toml's text for *values* ({key: value}), as every process
    writes it (a backup's restore puts back the file it saved)."""
    lines = ["# TuxTalk config"]
    lines += [f"{_toml_key(key)} = {_toml_value(value)}" for key, value in values.items()]
    return "\n".join(lines) + "\n"


# Characters with a short TOML escape. Every other control character (except
# tab, which TOML allows as is) is written as \uXXXX.
_TOML_SHORT_ESCAPES = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\n": "\\n",
                       "\f": "\\f", "\r": "\\r"}


def toml_string(value: str) -> str:
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
