import os
# CRITICAL: Disable HuggingFace XET downloads BEFORE any imports
# XET bypasses tqdm_class progress tracking, breaking our download progress UI
os.environ["HF_HUB_DISABLE_XET"] = "1"

# Import torch_init FIRST to configure CUDA library paths before any torch imports
from .torch_init import init_cuda_for_pytorch
init_cuda_for_pytorch()

import sys
import time
import re
import shutil
import subprocess
import atexit
import argparse
import fcntl
import select
import signal
from dataclasses import dataclass
import numpy as np
import sounddevice as sd
from evdev import InputDevice, ecodes, list_devices

from typing import NamedTuple

from .normalize import normalize_text, append_auto_punct, plain_for_app, swiss_spelling
from .config import load_config, Settings, load_custom_commands
# Imported as a module too, so the live-settings reload can read CONFIG_PATH and
# LIVE_APPLIED_KEYS without pulling each name in separately.
from . import config as config_module
from .logger import setup_logger, redact_text
from .recording_indicator import RecordingIndicator
from .undo import detect_undo_command, calculate_undo_length
import threading

logger = setup_logger(__name__)

# Whether the full text of transcriptions may be written to the on-disk log.
# Off by default so dictation never persists in plain text; refreshed from
# config at startup and by the live-settings reload, so the Preferences toggle
# takes effect on the next utterance without a restart.
_log_transcripts = False
# Whether each dictation is counted in the usage stats (stats.py, Preferences >
# Stats). Numbers only; the text itself is never stored.
_usage_stats = True
# Put the user's clipboard back after pasting a dictation (config
# restore_clipboard, issue #7); refreshed with the other live settings.
_restore_clipboard = False
_clipboard_keeper = None        # clipboard.ClipboardKeeper, created on the first paste that needs it
# Pause music and videos while recording (config pause_media, media_pause.py).
# Off until the service reads config at startup, so tests that call the real
# start_recording never pause anyone's music; refreshed with the live settings.
_pause_media = False
_media_pauser = None            # media_pause.MediaPauser, created on the first pause
# The Paste Last Dictation combo (config paste_last_hotkey), for the "couldn't
# type that" notice; refreshed with the live settings. "" when turned off.
_paste_last_hotkey = ""


def _pause_media_players():
    """Pause playing music and videos, in the background (the keyboard is grabbed)."""
    global _media_pauser
    if not _pause_media:
        return
    if _media_pauser is None:
        from .media_pause import MediaPauser
        _media_pauser = MediaPauser()
        # Quitting mid-dictation would otherwise leave the music paused.
        atexit.register(_media_pauser.resume_now)
    _media_pauser.pause()


def _resume_media_players():
    """Resume what _pause_media_players paused. Runs even if the option was
    switched off mid-recording, so nothing is left paused."""
    if _media_pauser is not None:
        _media_pauser.resume()


def _loggable(text):
    """Render transcribed text for a log line, redacted unless the user opted in."""
    return redact_text(text, reveal=_log_transcripts)


# Dictation cleanup (Preferences). _remove_fillers enables the "um"/"uh" and
# repeated-word rules; _ai_engine is the running self-correction engine, or
# None while that option is off. Both follow config through
# _apply_cleanup_settings(), at startup and on every live-settings reload.
_remove_fillers = False
_swiss_spelling = False     # ß -> ss in the finished text (Swiss Standard German)
# Spoken numbers as digits: None (off), "all" or "small_as_words" (one to nine
# stay words), and the language to read them in. See spoken_numbers.py.
_numbers_mode = None
_numbers_language = "en"
# Select and move by voice (TuxTalk Labs, voice_navigation.py).
_voice_navigation = False
# Plain text in terminals and code editors (app_kind, plain_for_app).
_adapt_to_app = True
# The languages auto-detect may pick from, () for any (language_limit.py).
_auto_languages = ()
_ai_engine = None
# Which of the two features using the AI engine are on. The engine runs while
# either is; each checks its own flag before using it.
_ai_corrections = False
_voice_editing = False

# Seconds of silence that end a toggle recording (0 = auto-stop off).
_auto_stop_seconds = 0.0

# Seconds of silence that end a chunk (0 = chunked dictation off), and the live
# settings object middle chunks read beeps, quotes and language from.
_chunk_pause_seconds = 0.0
_chunk_cfg = None
# Type at pause fine-tuning (Preferences → Advanced → TuxTalk Labs).
_chunk_speech_db = 6.0
_chunk_context_marks = True


def _apply_cleanup_settings(cfg):
    """Bring the cleanup and auto-stop options in line with *cfg*; start or
    stop the AI engine."""
    global _remove_fillers, _ai_engine, _auto_stop_seconds, _ai_corrections, _voice_editing, _swiss_spelling
    global _numbers_mode, _numbers_language, _voice_navigation, _adapt_to_app, _auto_languages
    global _chunk_pause_seconds, _chunk_cfg, _chunk_speech_db, _chunk_context_marks
    _auto_stop_seconds = (float(getattr(cfg, "auto_stop_seconds", 2.0))
                          if getattr(cfg, "auto_stop_silence", False) else 0.0)
    _chunk_cfg = cfg
    _chunk_speech_db = float(getattr(cfg, "chunk_speech_db", 6.0))
    _chunk_context_marks = bool(getattr(cfg, "chunk_context_marks", True))
    pause = (float(getattr(cfg, "chunk_pause_seconds", 0.8))
             if getattr(cfg, "chunked_dictation", False) else 0.0)
    if pause > 0 and _auto_stop_seconds > 0 and pause >= _auto_stop_seconds:
        # A pause as long as auto-stop's would end the recording instead of
        # the chunk, so the chunk pause gives way.
        clamped = max(0.3, _auto_stop_seconds - 0.2)
        logger.warning(f"chunk_pause_seconds {pause} is not below auto_stop_seconds "
                       f"{_auto_stop_seconds}; using {clamped}")
        pause = clamped
    _chunk_pause_seconds = pause
    _remove_fillers = bool(getattr(cfg, "remove_fillers", False))
    _swiss_spelling = bool(getattr(cfg, "swiss_spelling", False))
    _voice_navigation = bool(getattr(cfg, "voice_navigation", False))
    _adapt_to_app = bool(getattr(cfg, "adapt_to_app", True))
    from . import language_limit
    _auto_languages = language_limit.parse(getattr(cfg, "auto_languages", ""))
    if getattr(cfg, "numbers_as_digits", False):
        _numbers_mode = "small_as_words" if getattr(cfg, "numbers_small_as_words", False) else "all"
    else:
        _numbers_mode = None
    from .recommend import effective_language
    _numbers_language = effective_language(cfg)
    _ai_corrections = bool(getattr(cfg, "ai_corrections", False))
    _voice_editing = bool(getattr(cfg, "voice_editing", False))
    want_ai = _ai_corrections or _voice_editing
    if want_ai and _ai_engine is None:
        from . import ai_cleanup
        if ai_cleanup.is_installed():
            _ai_engine = ai_cleanup.CorrectionEngine()
            _ai_engine.start()
        else:
            logger.warning("The AI is on but not downloaded; turn the option off and on in Preferences")
    elif not want_ai and _ai_engine is not None:
        engine, _ai_engine = _ai_engine, None
        # stop() can wait up to 5 s for the server to exit. This runs on the
        # input thread, which must keep reading the hotkeys meanwhile.
        threading.Thread(target=engine.stop, daemon=True, name="tt-ai-stop").start()

# Cached tool path lookups — avoids searching PATH on every text injection
_tool_cache = {}
def _which(name):
    """Cached shutil.which() — tools don't move during a session."""
    if name not in _tool_cache:
        _tool_cache[name] = shutil.which(name)
    return _tool_cache[name]

# Optional desktop notifications via libnotify
from .notify import notify as _notify

# --- Single instance lock (user runtime dir) ---
def _runtime_dir():
    base = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    # Inside a Flatpak, XDG_RUNTIME_DIR is shared with the host, so a host
    # service's lock would make the Flatpak service exit as "already running".
    # The per-app $XDG_RUNTIME_DIR/app/$FLATPAK_ID is private to the sandbox, so
    # the lock goes there. Mirrors tray._runtime_dir.
    fid = os.environ.get("FLATPAK_ID")
    if fid:
        app_dir = os.path.join(base, "app", fid)
        if os.path.isdir(app_dir):
            return app_dir
    return base

def _acquire_single_instance():
    """Take the single-instance lock, or exit if another service holds it.

    flock is atomic, and the kernel releases the lock when the process exits.
    """
    global _lockfile_handle
    lockfile_path = os.path.join(_runtime_dir(), "talktype.lock")
    try:
        # Opened without truncating: the running instance's PID stays in the
        # file until this process actually holds the lock.
        fd = os.open(lockfile_path, os.O_RDWR | os.O_CREAT, 0o600)
        lockfile = os.fdopen(fd, "r+")

        # Close-on-exec so an in-place restart (os.execv) releases the lock
        # instead of inheriting the still-locked FD and exiting as "already
        # running". The tray's singleton lock does the same.
        _flags = fcntl.fcntl(lockfile.fileno(), fcntl.F_GETFD)
        fcntl.fcntl(lockfile.fileno(), fcntl.F_SETFD, _flags | fcntl.FD_CLOEXEC)

        try:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Only this error means another process holds the lock. Anything
            # else (an unwritable runtime folder) is not "already running".
            print("Another TuxTalk instance is already running. Exiting.", file=sys.stderr)
            sys.exit(0)

        # The PID is for people inspecting the file; the lock itself is the flock.
        lockfile.truncate(0)
        lockfile.write(str(os.getpid()))
        lockfile.flush()

        # Held in a global so garbage collection never closes it (and drops the lock).
        _lockfile_handle = lockfile

        logger.debug(f"Acquired singleton lock: {lockfile_path}")

    except OSError as e:
        logger.warning(f"Could not acquire singleton lock at {lockfile_path}: {e}")
        print(f"Warning: could not acquire singleton lock: {e}", file=sys.stderr)

# Global to keep lock file open
_lockfile_handle = None

# --- Runtime state ---
SAMPLE_RATE = 16000
CHANNELS = 1
MIN_HOLD_MS = 200
START_BEEP = (1200, 0.12)
HANDS_FREE_BEEP = (1600, 0.07)  # confirms a double-tap switched the recording to hands-free
# Two taps of the toggle key closer together than this switch the recording
# that the first tap started to hands-free, instead of stopping it.
DOUBLE_TAP_S = 0.4
CANCEL_BEEP = (500, 0.12)
READY_BEEP  = (1000, 0.09)

@dataclass
class RecordingState:
    """Encapsulates mutable state for recording sessions."""
    is_recording: bool = False
    was_cancelled: bool = False
    frames: list = None
    stream: object = None
    press_t0: float = None
    # Undo history - tracks last inserted text for voice-activated undo
    last_inserted_text: str = ""
    # Select and move by voice: the known text (left, right) of the cursor
    # inside the last dictation after a word or letter move, so the next move
    # is counted exactly too; None when the cursor is somewhere unknown.
    nav_context: tuple = None
    # The window the last dictation was typed into (its class), or None.
    last_inserted_window: str = None
    # Mid-sentence continuation - if True, lowercase the first letter of next dictation
    continue_mid_sentence: bool = False
    # Hands-free auto-stop for this recording (a silence.SilenceDetector), or
    # None unless a double-tap of the toggle key armed it.
    silence: object = None
    # When the toggle key started this recording (None for any other start),
    # so a quick second tap can be told apart from a stop.
    toggle_started_at: float = None
    # Paste Last Dictation: {"key", "key_up", "at"} from the combo press until
    # every key of it is let go, else None (see _handle_key_event).
    paste_last: object = None
    # Voice editing: this recording is an instruction for the selected text
    # (Shift + the dictation key), not dictation.
    edit_mode: bool = False
    # Chunked dictation: this recording's pause detector (chunking.PauseDetector)
    # or None, and how many chunks it has cut so far.
    chunker: object = None
    chunks_submitted: int = 0
    # The last chunk typed held back its final mark (app._hold_back_boundary):
    # {"mark", "audio", "raw", "typed"} until the next chunk decides it, else
    # None. Belongs to its recording only (cleared by _arm_chunker).
    pending_boundary: object = None
    # A phrase of this recording has been typed, so a correction said after the
    # next pause ("no wait, four") may change it (app._correct_across_pause).
    chunk_typed: bool = False
    # A phrase that was only a correction cue ("No wait."), not typed yet: the
    # corrected value comes in the next phrase.
    held_cue: str = ""
    # Speech the pause detector heard after its last cut, taken at the stop:
    # 0.0 means the rest of the recording is room noise only.
    last_chunk_speech: float = None
    # A recording typed in phrases (Type at pause) is still one dictation.
    # recording_serial counts recordings (_arm_chunker). said_text is all the
    # one numbered said_serial has typed or tried to: its Recent Dictations
    # entry (_remember_said). shown_text is the part of it on screen in
    # shown_window, ending at the cursor, for "fix that word" to fix in
    # place; None once that isn't known (_remember_shown).
    recording_serial: int = 0
    said_serial: int = -1
    said_text: str = ""
    shown_serial: int = -1
    shown_text: object = None
    shown_window: object = None

    def __post_init__(self):
        if self.frames is None:
            self.frames = []

# Global recording state
state = RecordingState()

# Global recording indicator and D-Bus service (initialized in main)
recording_indicator = None
dbus_service = None

# ---------------------------------------------------------------------------
# Ydotool environment helper — used by all ydotool calls to set socket path
# ---------------------------------------------------------------------------
def _get_ydotool_env():
    """Return env dict with YDOTOOL_SOCKET set for subprocess calls."""
    from .uinput_helper import ydotool_socket_path
    env = os.environ.copy()
    env["YDOTOOL_SOCKET"] = ydotool_socket_path()
    return env

# Undo voice-command parsing/length logic lives in tuxtalk.undo so it can
# be unit-tested without dragging in heavy audio/CUDA imports.

# D-Bus proxy for notifying the tray process of recording state changes.
# The tray owns the D-Bus name and relays signals to the GNOME extension.
_tray_dbus_proxy = None

# Calls to the tray come from the input thread, which holds an exclusive grab
# on every keyboard while recording. dbus-python's default reply timeout is 25
# seconds, so a busy tray (loading a Whisper model on its main loop, say) would
# freeze the keyboard system-wide for that long. These are fire-and-forget
# notifications, and a late one is worthless anyway.
_TRAY_DBUS_TIMEOUT = 1.5

# Thread-safe recording command flags — set by signal handler or D-Bus thread, consumed by evdev loop.
# Using two separate Events (rather than one flag) so start and stop can't overwrite each other.
_cmd_start_recording = threading.Event()
_cmd_stop_recording = threading.Event()
# Set alongside _cmd_start_recording when the Flatpak's toggle SHORTCUT (not a
# menu) started it, so a quick second press can switch it to hands-free.
_cmd_start_from_toggle_key = threading.Event()
_inject_lock = threading.Lock()  # serialise Backend B transcribe/inject workers

# Chunked dictation (chunking.py). The audio callback cuts chunks and the stop
# path takes the rest; this lock keeps the two from both taking the same frames.
_chunk_lock = threading.Lock()
_chunk_worker_instance = None
# How long stopping waits for the queued chunks to be typed (evdev path).
CHUNK_DRAIN_TIMEOUT_S = 60.0


def _chunk_worker():
    """The one chunk worker of this service, started on first use."""
    global _chunk_worker_instance
    if _chunk_worker_instance is None:
        from .chunking import ChunkWorker
        _chunk_worker_instance = ChunkWorker(_inject_lock)
    return _chunk_worker_instance

# Hotkey test mode: while Preferences' Test Hotkeys dialog keeps its flag file
# fresh (service_launcher.hotkey_test_flag_path), hotkey presses are reported via
# the D-Bus HotkeyPressed signal instead of starting/stopping recording. This
# keeps the evdev grab alive and avoids XWayland phantom key-repeat floods.
_hotkey_test_flag = None    # the flag's path, worked out on first use
_hotkey_test_was_on = False  # for logging when test mode starts and ends


def _hotkey_test_active() -> bool:
    """Whether the Test Hotkeys dialog is open: its flag exists and is fresh."""
    global _hotkey_test_flag, _hotkey_test_was_on
    from . import service_launcher
    if _hotkey_test_flag is None:
        _hotkey_test_flag = service_launcher.hotkey_test_flag_path()
    try:
        age = time.time() - os.path.getmtime(_hotkey_test_flag)
        on = age < service_launcher.HOTKEY_TEST_LEASE_S
    except OSError:
        on = False
    if on != _hotkey_test_was_on:
        _hotkey_test_was_on = on
        logger.info(f"Hotkey test mode {'on' if on else 'off'}")
    return on


def _handle_sigusr1(signum, frame):
    """SIGUSR1 = toggle recording. Sent by the tray process via os.kill().
    Sets the appropriate Event; the evdev loop consumes it from the main thread.
    """
    if state.is_recording:
        _cmd_stop_recording.set()
    else:
        _cmd_start_recording.set()

def _notify_tray_recording_state(is_recording: bool):
    """Send recording state to the tray's D-Bus service.

    The tray process owns the D-Bus name that the GNOME extension listens to.
    App.py cannot emit signals that the extension will see, so we call
    the tray's NotifyRecordingState method which then emits the signal.

    Called from start_recording/stop_recording — that is, with every keyboard
    exclusively grabbed. It must never block for long: see _TRAY_DBUS_TIMEOUT.
    """
    try:
        import dbus
        proxy = _get_tray_dbus_proxy()
        proxy.NotifyRecordingState(
            dbus.Boolean(is_recording),
            dbus_interface='io.github.ronb1964.TalkType',
            timeout=_TRAY_DBUS_TIMEOUT,
        )
    except Exception as e:
        logger.debug(f"Could not notify tray of recording state: {e}")

def _handle_sigusr2(signum, frame):
    """SIGUSR2 is accepted and ignored: unhandled, it ends the process, and an
    older Preferences may still send one to switch hotkey test mode."""
    logger.info("Ignored SIGUSR2 (hotkey test mode follows Preferences' flag file)")


def _install_signal_handlers():
    """Handle SIGUSR1 and SIGUSR2 from the very start. Both end a process
    that doesn't handle them, and the tray and Preferences can send them
    while the model is still loading."""
    signal.signal(signal.SIGUSR1, _handle_sigusr1)
    signal.signal(signal.SIGUSR2, _handle_sigusr2)

def _get_tray_dbus_proxy():
    """Get or create the D-Bus proxy for communicating with the tray process.

    introspect=False: Introspect() is itself a blocking call with the 25-second
    reply timeout, so it would double the worst-case stall on a busy tray.
    Without it dbus-python guesses argument types from the Python values, so
    wrap anything that isn't a plain str or bool (dbus.Array(..., signature=...)).
    """
    global _tray_dbus_proxy
    import dbus
    if _tray_dbus_proxy is None:
        import dbus.mainloop.glib
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus()
        _tray_dbus_proxy = bus.get_object(
            'io.github.ronb1964.TalkType',
            '/io/github/ronb1964/TalkType',
            introspect=False,
        )
    return _tray_dbus_proxy


def _claim_desktop_hotkeys(cfg):
    """Ask the desktop to keep the hotkeys away from the focused app.

    We read them below the desktop, so without this F8 also reaches the app
    ("~" in a terminal). On KDE a KWin script holds them (kwin_hotkeys); on
    GNOME the extension does, from the list sent to the tray here. Called at
    startup and after every live settings change. Neither part can fail
    dictation: the KDE claim never raises, and this is a fire-and-forget call.
    """
    from . import gnome_hotkeys, kwin_hotkeys
    kwin_hotkeys.claim(cfg)
    try:
        import dbus
        _get_tray_dbus_proxy().NotifyClaimedHotkeys(
            dbus.Array(gnome_hotkeys.accelerators(cfg), signature='s'),
            dbus_interface='io.github.ronb1964.TalkType',
            timeout=_TRAY_DBUS_TIMEOUT,
        )
    except Exception as e:
        logger.debug(f"Could not send the hotkeys to the tray: {e}")


def _notify_tray_hotkey_pressed(key_name: str):
    """Send hotkey press notification to tray D-Bus service during test mode.

    Called from the evdev loop while hotkey test mode is on. The tray's D-Bus
    service emits a HotkeyPressed signal that the prefs dialog listens for.
    """
    try:
        proxy = _get_tray_dbus_proxy()
        proxy.NotifyHotkeyPressed(
            key_name,
            dbus_interface='io.github.ronb1964.TalkType',
            timeout=_TRAY_DBUS_TIMEOUT,
        )
    except Exception as e:
        logger.debug(f"Could not notify tray of hotkey press: {e}")


def _show_voice_commands_via_dbus():
    """Tell the tray to show the voice commands quick reference via D-Bus."""
    try:
        proxy = _get_tray_dbus_proxy()
        proxy.ShowVoiceCommands(
            dbus_interface='io.github.ronb1964.TalkType',
            timeout=_TRAY_DBUS_TIMEOUT,
        )
    except Exception as e:
        logger.debug(f"Could not show voice commands via D-Bus: {e}")

# Global typing delay (set from config in main)
_typing_delay = 12  # milliseconds, default value

# Global custom commands (loaded from config in main)
_custom_commands: dict[str, str] = {}
# Your Words (custom_words.py): names and terms to spell the user's way. Told
# to Whisper before it listens, and fixed in every engine's output afterwards.
# Empty until main() loads them; reloaded when custom_words.txt changes.
_custom_words: list[str] = []
_last_words_mtime = None


def _expand_escapes(replacement: str) -> str:
    """Expand the two escapes the Preferences hint documents, and nothing else.

    Any other backslash stays literal, so a Windows path or a stray \\N in a
    replacement is typed as written.
    """
    return replacement.replace("\\n", "\n").replace("\\t", "\t")


# Whisper is inconsistent about writing a spoken number as a word or a digit; the
# same phrase comes out both ways on different days. Commands match the raw
# transcription, so a trigger containing a number matches either spelling.
#
# Single tokens only, deliberately: compounds like "twenty one" are ambiguous
# (21? 20 then 1?) and pull in a whole number parser for no real benefit.
_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90",
}
_DIGIT_WORDS = {digit: word for word, digit in _NUMBER_WORDS.items()}

# Split into whole words, whole numbers, runs of whitespace, and single other
# characters. Tokenising is what keeps this narrow: "money" is one token and
# never sees the "one" inside it.
_TOKEN_RE = re.compile(r"\d+|[^\W\d]+|\s+|.")


def _canonical_command_key(text: str) -> str:
    """Fold a phrase to the form used for command lookup.

    Case is flattened, whitespace runs collapse, and every number becomes its
    digit form, so "Test Phrase One" and "test phrase 1" arrive at one key.
    Only lookup uses this — dictated text is never rewritten by it.
    """
    out = []
    for token in _TOKEN_RE.findall(text.lower()):
        if token.isspace():
            out.append(" ")
        else:
            out.append(_NUMBER_WORDS.get(token, token))
    return "".join(out)


def _command_pattern(phrase: str) -> str:
    """Build the match pattern for one trigger phrase.

    Word boundaries are applied only at ends that actually start or finish with
    a word character: \\b after "+" requires a following word character, so a
    trigger like "c++" or "#tag" could otherwise never match.

    Number tokens match either spelling, so the trigger fires whichever one
    Whisper produced.
    """
    prefix = r"\b" if phrase[:1].isalnum() or phrase[:1] == "_" else ""
    suffix = r"\b" if phrase[-1:].isalnum() or phrase[-1:] == "_" else ""

    parts = []
    for token in _TOKEN_RE.findall(phrase):
        lowered = token.lower()
        if lowered in _NUMBER_WORDS:
            alt = _NUMBER_WORDS[lowered]
        elif token in _DIGIT_WORDS:
            alt = _DIGIT_WORDS[token]
        else:
            # Whitespace is matched loosely so a double space in either the
            # trigger or the transcription still lines up.
            parts.append(r"\s+" if token.isspace() else re.escape(token))
            continue
        parts.append(f"(?:{re.escape(token)}|{re.escape(alt)})")

    return prefix + "".join(parts) + suffix

def _apply_custom_commands(text: str) -> tuple[str, dict[str, str]]:
    """
    Apply user-defined custom voice commands to the transcribed text.

    Replaces spoken phrases with their configured replacements.
    Uses case-insensitive matching with word boundaries to avoid
    accidental replacements within longer words.

    If a replacement value is wrapped in double quotes (e.g. "Hello, world!"),
    the quotes are stripped and the text is protected from normalization by
    substituting a placeholder token. The caller must restore these tokens
    after normalize_text() runs.

    Args:
        text: Raw transcribed text

    Returns:
        Tuple of (processed text, protected dict mapping placeholder → literal text)
    """
    if not _custom_commands or not text:
        return text, {}

    # A trigger saved as "tux talk website" also fires on "TuxTalk website":
    # with TuxTalk in Your Words, Whisper is steered to hear it joined.
    commands = dict(_custom_commands)
    if _custom_words:
        from .custom_words import fix_spelling
        for phrase, replacement in _custom_commands.items():
            commands.setdefault(fix_spelling(phrase, _custom_words), replacement)

    # Longest phrase first, so "our brand customs llc" wins over the shorter
    # "our brand customs" that is also a prefix of it.
    phrases = sorted((p for p in commands if p), key=len, reverse=True)
    if not phrases:
        return text, {}

    # Keyed on the canonical form so a match found via the other number
    # spelling still resolves. Phrases are longest-first and the first claim on
    # a key wins, so if two triggers fold together ("test 1" and "test one")
    # the longer one keeps it — the same rule the match order follows.
    lookup: dict[str, str] = {}
    for p in phrases:
        lookup.setdefault(_canonical_command_key(p), commands[p])
    combined = "|".join(f"(?:{_command_pattern(p)})" for p in phrases)

    protected: dict[str, str] = {}  # placeholder → literal replacement text
    counter = 0

    def substitute(match):
        """Return the replacement for one matched phrase.

        A function rather than a template string, because re.sub does not
        interpret backslashes in what a function returns, so a Windows path or
        a stray \\N in a user's command can't raise.
        """
        nonlocal counter
        replacement = lookup.get(_canonical_command_key(match.group(0)))
        if replacement is None:  # pragma: no cover — every branch is in lookup
            return match.group(0)

        if len(replacement) >= 2 and replacement.startswith('"') and replacement.endswith('"'):
            # Quoted replacement — inject exactly as written, bypass normalization
            placeholder = f"§CMDLIT_{counter}§"
            counter += 1
            protected[placeholder] = replacement[1:-1]
            return placeholder

        return _expand_escapes(replacement)

    # One pass over the original text, so one command's output is never
    # re-scanned and rewritten by another.
    result = re.sub(combined, substitute, text, flags=re.IGNORECASE)

    if result != text:
        logger.info(f"Custom commands applied: {_loggable(text)} -> {_loggable(result)}")

    return result, protected


def _restore_protected(text: str, protected: dict[str, str]) -> str:
    """Put quoted (literal) replacements back after normalization has run.

    Quoted commands promise the text is injected exactly as written, so a
    sentence-ending period that normalization parked directly against one is
    dropped — but only when the literal already ends in something other than a
    letter or digit. That covers a literal supplying its own punctuation
    ("Hello, world!", "Thanks.") or deliberately ending in a space ("/btw "),
    while a literal ending in a word still gets the period the user expects.
    """
    for placeholder, literal in protected.items():
        if literal and not literal[-1].isalnum():
            text = text.replace(placeholder + ".", literal)
        text = text.replace(placeholder, literal)
    return text


# YouTube outro phrases Whisper invents from silence, which nobody says to a
# text box. Stripped from the end of any transcription.
_YOUTUBE_HALLUCINATION_PHRASES = [
    # NOT bare "subscribe": it is an ordinary verb, and stripping it would eat
    # the last word of real sentences ("tell them to subscribe"). Whisper
    # hallucinates the multi-word forms, which nobody dictates.
    "like and subscribe",
    "please subscribe",
    "don't forget to subscribe",
    "hit the bell",
]

# Sign-offs Whisper also invents from silence, but which people do dictate at
# the end of an email or a script ("Great meeting today, see you next time.").
# Treated like "thank you" below: dropped only when Whisper itself says the
# audio was probably not speech.
_SIGN_OFF_PHRASES = [
    "thanks for watching",
    "thanks for listening",
    "thank you for watching",
    "thank you for listening",
    "see you next time",
    "see you in the next video",
    "see you in the next one",
]

# Common words/phrases that Whisper hallucinates from silence.
# These are only stripped when they are the ENTIRE transcription,
# because they could also be real speech at the end of a sentence
# (e.g., "I want to say thank you" should keep "thank you").
_WHOLE_TEXT_HALLUCINATION_PHRASES = [
    "thank you",
    "thank you very much",
    "thank you so much",
    "thanks",
    "bye",
    "bye bye",
    "goodbye",
    "you",
    "oh",
    "ah",
    "hmm",
    "uh",
    "um",
    # Parakeet's word for silence
    "mm-hmm",
    "mhm",
    "mm",
    "uh-huh",
] + _SIGN_OFF_PHRASES

# Phrases that Whisper hallucinates at the END of longer dictations.
# Only stripped when trailing real speech AND no_speech_prob is very high (>0.8),
# which means Whisper itself is uncertain the audio contained real speech.
# Kept separate from _WHOLE_TEXT_HALLUCINATION_PHRASES because the trailing
# check needs a higher confidence threshold and minimum text length to avoid
# false positives (e.g., "Sure, thank you." is real speech and should be kept).
_TRAILING_HALLUCINATION_PHRASES = [
    "thank you",
    "thank you very much",
    "thank you so much",
    "thanks",
    "you",
] + _SIGN_OFF_PHRASES


def _trailing_phrase_re(phrases):
    """A pattern for any of *phrases* as whole words at the very end of a text,
    with the punctuation and spaces around it. Matched on the original text,
    case-insensitively, so the cut lands in the right place even where
    lower-casing changes a string's length (Turkish "İ")."""
    alternatives = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(r"[\s,.;:!?]*\b(?:" + alternatives + r")[\s.,!?;:]*$", re.IGNORECASE)


_YOUTUBE_TAIL_RE = _trailing_phrase_re(_YOUTUBE_HALLUCINATION_PHRASES)
_TRAILING_TAIL_RE = _trailing_phrase_re(_TRAILING_HALLUCINATION_PHRASES)


def _cut_tail(text: str, start: int) -> str:
    """*text* up to *start*, keeping the sentence's own closing mark: "Great
    meeting today, see you next time." ends as "Great meeting today."."""
    kept = text[:start].rstrip(" ,;:")
    if not kept:
        return ""
    # A sentence that ended before the phrase keeps its own mark ("week. Thanks
    # for watching!" -> "week."); one the phrase finished takes the phrase's.
    first = text[start:].lstrip()[:1]
    last = text.rstrip()[-1:]
    for mark in (first, last):
        if mark in (".", "!", "?"):
            return kept + mark
    return kept


def _strip_hallucinations(text: str, no_speech_prob: float = 0.0) -> str:
    """Remove phrases Whisper invents from silence, in three tiers:

    1. A whole text that is only "thank you", "bye" or a sign-off, when
       no_speech_prob (0-1) is above 0.6; an intentional "Thank you." stays.
    2. YouTube outros ("like and subscribe") at the end, always.
    3. "thank you" or a sign-off trailing a longer dictation, when
       no_speech_prob is above 0.8.

    Parakeet passes 0.0, so only tier 2 applies to it. The Vulkan engine passes
    1.0 for a recording with no speech in it and 0.0 otherwise, since
    whisper.cpp's own score can't be trusted.
    """
    if not text:
        return text

    original = text
    text = text.strip()

    # Strip any trailing punctuation for comparison
    lower_clean = text.lower().rstrip(" .,!?;:")

    # Tier 1: If the ENTIRE text is a common hallucination phrase AND
    # Whisper thinks there was no real speech, discard it.
    # This catches silence -> "Thank you." while preserving real "Thank you."
    if lower_clean in _WHOLE_TEXT_HALLUCINATION_PHRASES and no_speech_prob > 0.6:
        logger.info(f"Stripped whole-text hallucination (no_speech_prob={no_speech_prob:.2f}): {_loggable(original)}")
        return ""

    # Tier 2: Strip YouTube outros from the end of text, repeatedly, since
    # Whisper sometimes strings two together.
    while text:
        match = _YOUTUBE_TAIL_RE.search(text)
        if not match:
            break
        text = _cut_tail(text, match.start())

    # Tier 3: Strip trailing hallucination phrases from the end of real sentences.
    # Only when: (a) no_speech_prob is very high (>0.8, Whisper is very uncertain),
    # and (b) there's substantial text before the trailing phrase (>50 chars),
    # so we don't accidentally strip real speech like "Sure, thank you."
    if text and no_speech_prob > 0.8 and len(text) > 50:
        match = _TRAILING_TAIL_RE.search(text)
        # Only a phrase after real speech, never the whole text (tier 1's job).
        if match and match.start() > 0:
            text = _cut_tail(text, match.start())

    if text != original:
        logger.info(f"Stripped hallucination: {_loggable(original)} -> {_loggable(text)}")

    return text


_cached_output_device = None
_output_device_cached = False

def _find_output_device():
    """Find a working output device. Cached — device doesn't change during a session."""
    global _cached_output_device, _output_device_cached
    if _output_device_cached:
        return _cached_output_device
    for name_hint in ['pipewire', 'pulse', 'sysdefault', 'default']:
        for i, d in enumerate(sd.query_devices()):
            if d['max_output_channels'] > 0 and name_hint in d.get('name', '').lower():
                try:
                    sd.check_output_settings(device=i, samplerate=44100, channels=1)
                    _cached_output_device = i
                    _output_device_cached = True
                    return i
                except Exception:
                    continue
    _output_device_cached = True  # Cache the "not found" result too
    return None  # Fall back to sounddevice default

def _beep(enabled: bool, freq=1000, duration=0.15):
    """Play a short sine tone, if beeps are on. Audio errors are ignored."""
    if not enabled:
        return
    samplerate = 44100
    t = np.linspace(0, duration, int(samplerate * duration), False)
    tone = (np.sin(freq * t * 2 * np.pi) * 0.2).astype(np.float32)
    try:
        sd.play(tone, samplerate, device=_find_output_device()); sd.wait()
    except Exception:
        pass

def _sd_callback(indata, frames_count, time_info, status):
    """sounddevice's audio callback: collect frames while recording."""
    if state.is_recording:
        state.frames.append(indata.tobytes())

        # Chunked dictation: at a pause, the phrase so far goes off to be typed.
        chunker = state.chunker
        if chunker is not None and chunker.feed(indata):
            _cut_chunk(chunker)

        # Hands-free auto-stop: when the speaker has finished, ask the service
        # loop to stop, exactly as a second press of the toggle key would.
        detector = state.silence
        if detector is not None and detector.feed(indata):
            if detector.nothing_said:
                logger.info("Auto-stop: nothing said, stopping without typing anything")
            else:
                logger.info("Auto-stop: speaker went quiet, stopping the recording")
            _cmd_stop_recording.set()

        # Feed the recording indicator whatever its active style needs.
        if recording_indicator:
            _feed_indicator(recording_indicator, indata, _spectrum_processor)


def _cut_chunk(chunker):
    """The speaker paused: hand the audio so far to the chunk worker.

    Runs on the audio thread, so it only swaps a list and queues a job; the
    transcription happens on the worker."""
    with _chunk_lock:
        if state.chunker is not chunker:
            return  # the recording ended while this block was arriving
        frames, state.frames = state.frames, []
        _drop_tail_samples(frames, chunker.tail_samples())
        state.chunks_submitted += 1
        number = state.chunks_submitted
        rec_sr = getattr(state, "recording_samplerate", SAMPLE_RATE)
        pause = chunker.last_pause
        # Queued before the lock is let go: otherwise the stop could slip in
        # between counting this chunk and queuing it, and queue the last chunk
        # ahead of it. put() on the unbounded queue never blocks.
        _chunk_worker().submit(
            lambda: _transcribe_chunk(frames, rec_sr, "middle", number, pause))


def _transcribe_chunk(frames, rec_sr, role, number=None, pause=None):
    """Transcribe and type one chunk cut during the recording, with the
    settings as they are now."""
    if number is not None:
        # Logged here, on the worker, to keep file writes off the audio thread.
        seconds = sum(len(f) for f in frames) / 2 / rec_sr
        logger.info(f"Chunk {number}: {seconds:.1f}s of audio, cut after a "
                    f"{pause:.2f}s pause")
    cfg = _chunk_cfg
    _transcribe_and_inject(frames, rec_sr, cfg.beeps, cfg.smart_quotes, cfg.notify,
                           cfg.language, cfg.auto_space, cfg.auto_period,
                           cfg.injection_mode, chunk_role=role)


def _arm_chunker(rec_sr, edit_mode=False):
    """Give this recording a pause detector when chunked dictation is on.
    Never for a voice edit: its instruction needs the whole recording."""
    chunker = None
    if _chunk_pause_seconds > 0 and not edit_mode:
        from .chunking import PauseDetector
        chunker = PauseDetector(_chunk_pause_seconds, rec_sr, speech_db=_chunk_speech_db)
        # Started here, so the audio callback never has to build it.
        _chunk_worker()
    with _chunk_lock:
        state.recording_serial += 1
        state.chunks_submitted = 0
        state.pending_boundary = None
        state.chunk_typed = False
        state.held_cue = ""
        state.chunker = chunker


def _feed_indicator(indicator, indata, spectrum_processor):
    """Feed the recording indicator the data its active style needs.

    The RMS level is always cheap and drives the orb and the color brightness.
    Raw samples (waveform, radial) and the FFT spectrum (bars) are computed
    ONLY for the style that consumes them, so an orb user pays for no FFT.
    """
    audio = np.frombuffer(indata, dtype=np.int16).astype(np.float64)
    rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
    indicator.set_audio_level(min(1.0, rms / 3000.0))

    style = getattr(indicator, "style", "orb")
    if style in ("waveform", "radial"):
        indicator.set_waveform(audio.astype(np.float32) / 32768.0)
    elif style == "bars" and spectrum_processor is not None:
        indicator.set_spectrum(spectrum_processor.process(audio.astype(np.float32) / 32768.0))


# Built once when the recording indicator is created; used by _feed_indicator
# to compute the bars spectrum. None until then (and for non-bars styles).
_spectrum_processor = None


def _keycode_from_name(name: str) -> int | None:
    """Convert key name to evdev keycode. Returns None if name is empty (no hotkey configured)."""
    if not name or not name.strip():
        return None  # No hotkey configured - don't activate any key
    n = name.strip().upper()
    fkeys = {f"F{i}": getattr(ecodes, f"KEY_F{i}") for i in range(1,13)}
    if n in fkeys: return fkeys[n]
    if len(n) == 1 and "A" <= n <= "Z":
        return getattr(ecodes, f"KEY_{n}")
    return None  # Unknown key name - don't activate


# Modifier key evdev codes for tracking combo hotkeys
_MODIFIER_CODES = {
    ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL,
    ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT,
    ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT,
    ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA,
}

# Map modifier names to their evdev code pairs (left + right)
_MODIFIER_NAMES = {
    "CTRL":  (ecodes.KEY_LEFTCTRL,  ecodes.KEY_RIGHTCTRL),
    "SHIFT": (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT),
    "ALT":   (ecodes.KEY_LEFTALT,   ecodes.KEY_RIGHTALT),
    "SUPER": (ecodes.KEY_LEFTMETA,  ecodes.KEY_RIGHTMETA),
}


def _parse_hotkey_combo(combo_str: str) -> tuple[set[str], int | None] | None:
    """Parse a hotkey combo string like 'Ctrl+Shift+H' into (modifier_names, keycode).

    Returns None if the string is empty or invalid.
    modifier_names is a set of uppercase modifier names (e.g. {'CTRL', 'SHIFT'}).
    keycode is the evdev keycode for the non-modifier key.
    """
    if not combo_str or not combo_str.strip():
        return None

    parts = [p.strip().upper() for p in combo_str.split("+")]
    if len(parts) < 2:
        return None  # Need at least one modifier + one key

    # Last part is the main key, everything before is a modifier
    modifiers = set()
    for part in parts[:-1]:
        if part in _MODIFIER_NAMES:
            modifiers.add(part)
        else:
            return None  # Unknown modifier

    main_key = _keycode_from_name(parts[-1])
    if main_key is None:
        return None  # Unknown key

    return (modifiers, main_key)


def _check_modifiers_held(required_mods: set[str], held_keys: set[int]) -> bool:
    """Check if all required modifiers are currently held down."""
    for mod_name in required_mods:
        left, right = _MODIFIER_NAMES[mod_name]
        if left not in held_keys and right not in held_keys:
            return False
    return True

def _pick_input_device(mic_substring: str | None):
    """Return device index for the best matching input device.

    Delegates to config.find_input_device() which handles PipeWire
    auto-detection when no mic name is configured. This avoids the
    broken ALSA "default" virtual device that returns garbage audio
    on PipeWire systems.
    """
    from .config import find_input_device
    return find_input_device(mic_substring)

def _get_device_samplerate(device_idx):
    """Get the native sample rate for a device, falling back to SAMPLE_RATE.

    Some ALSA hw: devices only support their native rate (e.g. 48000 Hz)
    and reject 16000 Hz. We detect this and record at the native rate,
    then resample to 16000 Hz afterward.
    """
    try:
        sd.check_input_settings(device=device_idx, samplerate=SAMPLE_RATE, channels=CHANNELS)
        return SAMPLE_RATE  # Device supports 16kHz directly
    except sd.PortAudioError:
        pass
    # Fall back to device's default sample rate
    try:
        info = sd.query_devices(device_idx)
        native_sr = int(info['default_samplerate'])
        sd.check_input_settings(device=device_idx, samplerate=native_sr, channels=CHANNELS)
        print(f"Mic doesn't support {SAMPLE_RATE}Hz, recording at {native_sr}Hz (will resample)")
        return native_sr
    except Exception:
        return SAMPLE_RATE  # Last resort, let it fail naturally

def _resample_audio(audio, orig_sr, target_sr):
    """Resample audio from orig_sr to target_sr using linear interpolation.

    Good enough for speech audio going to Whisper. No extra dependencies needed.
    """
    if orig_sr == target_sr:
        return audio
    # Calculate new length and interpolate
    duration = len(audio) / orig_sr
    target_len = int(duration * target_sr)
    orig_indices = np.linspace(0, len(audio) - 1, target_len)
    return np.interp(orig_indices, np.arange(len(audio)), audio.astype(np.float64)).astype(audio.dtype)

# ---------------------------------------------------------------------------
# Exclusive input-device grabs
#
# While recording we take an exclusive grab on the keyboard so the hotkey
# doesn't leak through to whatever app has focus. A grab that is taken and
# never released leaves the user's keyboard dead SYSTEM-WIDE until TuxTalk
# exits, so the grabbed devices are tracked in module state rather than being
# threaded through return values — that way every exit path (normal stop,
# cancel, toggle, ESC, or an exception) can release them with one call.
# ---------------------------------------------------------------------------

# Devices we currently hold an exclusive grab on.
_grabbed_devices = []

# How often to look for input devices that have appeared or come back.
# Frequent enough that a reconnected keyboard starts working on its own,
# rare enough that it costs nothing.
DEVICE_RESCAN_SECONDS = 3.0

# How long the input loop sleeps waiting for a key event before it does its
# other work: D-Bus start/stop commands, the auto-timeout, settings changes.
# Waiting on the devices instead of reading each one every few milliseconds
# lets an idle service sleep; a key press still wakes it at once.
INPUT_WAIT_S = 0.05

# The shortest pass through the input loop, so a mouse sending 1000 events a
# second can't spin the loop flat out.
INPUT_MIN_PASS_S = 0.005


def _devices_with_input(devices, timeout):
    """The devices with events waiting, after sleeping up to *timeout* for one."""
    try:
        ready, _, _ = select.select(devices, [], [], timeout)
    except (OSError, ValueError):
        # A device closed under us. Reading every device finds it, and the
        # loop's error handling drops it.
        return list(devices)
    return ready

# How often to check whether the config file changed on disk. Preferences
# doesn't restart the service for settings that can be applied live, so this
# poll is what makes them take effect. One stat() per second.
CONFIG_RECHECK_SECONDS = 1.0

# mtime of the config file as of the last check. None means "not yet seen".
_last_config_mtime = None

# Same, for custom_commands.toml. It is a separate file, so it needs its own
# mtime — config.toml's says nothing about whether the commands changed.
_last_commands_mtime = None


class LiveSettings(NamedTuple):
    """Values the main loop keeps in locals and must rebind after a reload."""
    hold_key: int | None
    toggle_key: int | None
    vc_hotkey_str: str
    voice_cmds_combo: tuple | None
    voice_cmds_main_key: int | None
    mode: str
    paste_last_combo: tuple | None = None


def _config_file_changed() -> bool:
    """Whether config.toml's mtime has moved since the last check.

    Returns True on the very first call once the file exists, so a service that
    starts before the file is written still picks it up. A missing or unreadable
    file is not a change — there is nothing new to apply.
    """
    global _last_config_mtime
    try:
        mtime = os.path.getmtime(config_module.CONFIG_PATH)
    except OSError:
        return False

    if mtime == _last_config_mtime:
        return False

    _last_config_mtime = mtime
    return True


def _commands_file_changed() -> bool:
    """Whether custom_commands.toml's mtime has moved since the last check.

    Custom commands live in their own file, so config.toml's mtime says nothing
    about them; this is what makes an edit in the Commands tab take effect.

    Same contract as _config_file_changed(): True once per change, and a missing
    or unreadable file is not a change.
    """
    global _last_commands_mtime
    try:
        mtime = os.path.getmtime(config_module.CUSTOM_COMMANDS_PATH)
    except OSError:
        return False

    if mtime == _last_commands_mtime:
        return False

    _last_commands_mtime = mtime
    return True


def _reload_custom_commands() -> None:
    """Re-read custom_commands.toml into the running service.

    A damaged file leaves the commands already in force untouched. load_config's
    reload follows the same rule: a bad file must never degrade a live dictation
    session, and load_custom_commands() returns {} on a read failure, which would
    otherwise silently disable every command mid-session.
    """
    global _custom_commands
    try:
        fresh = load_custom_commands()
        if config_module._commands_read_failed:
            logger.warning("Custom commands unreadable, keeping the current ones")
            return
        _custom_commands = fresh
        logger.info(f"Reloaded {len(_custom_commands)} custom voice command(s)")
    except Exception as e:
        logger.warning(f"Could not reload custom commands, keeping current: {e}")


def _words_file_changed() -> bool:
    """Whether custom_words.txt's mtime has moved since the last check.
    Same contract as _commands_file_changed()."""
    global _last_words_mtime
    from . import custom_words
    try:
        mtime = os.path.getmtime(custom_words.path())
    except OSError:
        return False
    if mtime == _last_words_mtime:
        return False
    _last_words_mtime = mtime
    return True


def _reload_custom_words() -> None:
    """Re-read custom_words.txt into the running service. A file that can't
    be read keeps the words already in force, like _reload_custom_commands."""
    global _custom_words
    from . import custom_words
    try:
        fresh = custom_words.load(strict=True)
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Could not reload your words, keeping the current ones: {e}")
        return
    _custom_words = fresh
    logger.info(f"Reloaded your words ({len(_custom_words)})")


def _reload_live_settings(cfg, indicator):
    """Refresh settings that can change without restarting the service.

    Updates *cfg* IN PLACE — the whole service holds a reference to that one
    object and reads most settings off it at the point of use, so refreshing
    its fields is what makes them live.

    Deliberately leaves model and device alone: those describe the speech model
    that is already loaded, and rewriting them would make the config disagree
    with reality. Changing them still restarts the service.

    Returns a LiveSettings for the caller to rebind its loop locals, or None if
    the config could not be read — in which case the previous settings stay in
    force. A damaged config must never degrade a running dictation session.
    """
    try:
        fresh = load_config()
    except Exception as e:
        # Runs from the input loop; log once per change, and
        # never let anything escape into the loop.
        logger.warning(f"Could not reload settings, keeping current ones: {e}")
        return None

    try:
        for key in config_module.LIVE_APPLIED_KEYS:
            if hasattr(fresh, key) and hasattr(cfg, key):
                setattr(cfg, key, getattr(fresh, key))

        global _typing_delay
        _typing_delay = getattr(cfg, "typing_delay", 12)

        global _log_transcripts
        _log_transcripts = getattr(cfg, "log_transcripts", False)

        global _usage_stats
        _usage_stats = getattr(cfg, "usage_stats", True)

        global _restore_clipboard
        _restore_clipboard = getattr(cfg, "restore_clipboard", False)

        global _pause_media
        _pause_media = getattr(cfg, "pause_media", True)

        global _paste_last_hotkey
        _paste_last_hotkey = getattr(cfg, "paste_last_hotkey", "")

        _apply_cleanup_settings(cfg)

        if indicator is not None:
            indicator.apply_settings(
                cfg.indicator_position,
                cfg.indicator_size,
                cfg.indicator_offset_x,
                cfg.indicator_offset_y,
                style=cfg.indicator_style,
                color_mode=cfg.indicator_color_mode,
                custom_color=cfg.indicator_color,
                backing=cfg.indicator_backing,
                sensitivity=cfg.indicator_sensitivity,
                follow_window=getattr(cfg, "indicator_follow_window", False),
            )

        vc_hotkey_str = getattr(cfg, "voice_commands_hotkey", "")
        voice_cmds_combo = _parse_hotkey_combo(vc_hotkey_str)

        return LiveSettings(
            hold_key=_keycode_from_name(cfg.hotkey),
            toggle_key=_keycode_from_name(cfg.toggle_hotkey) if cfg.toggle_hotkey else None,
            vc_hotkey_str=vc_hotkey_str,
            voice_cmds_combo=voice_cmds_combo,
            voice_cmds_main_key=voice_cmds_combo[1] if voice_cmds_combo else None,
            mode=cfg.mode.lower().strip(),
            paste_last_combo=_parse_hotkey_combo(getattr(cfg, "paste_last_hotkey", "")),
        )
    except Exception as e:
        logger.warning(f"Could not apply reloaded settings: {e}", exc_info=True)
        return None

# The service grabs only real keyboards; the same test tells onboarding and
# Preferences whether the service will hear the hotkey.
from .uinput_helper import LETTER_KEYS as _LETTER_KEYS  # noqa: E402,F401
from .uinput_helper import is_keyboard_device as _is_keyboard_device  # noqa: E402


# --- Keeping the desktop's key state honest across a grab --------------------
#
# The desktop (libinput, in KWin or GNOME Shell) sees a hotkey go DOWN before we
# can grab the keyboard, but the grab hides the key going UP. libinput counts
# presses minus releases per key, so the key stays "held" for the rest of the
# session and each dictation adds another. Most apps never notice; anything
# that asks which keys are held (VM viewers, remote desktop, games) sees F8
# stuck and auto-repeats it. See freedesktop bug 101796.
#
# So before grabbing, send the release the desktop is owed, through the
# same keyboard device, so it sees a balanced press/release. After grabbing,
# send the press back, which only we see, so we still get the real release
# when the key comes up. Our own read loop hears both injected events and must
# skip them, or the injected release would end the recording it just started.

_INJECTED_TTL_S = 2.0
_injected_pending: list = []   # [(device path, code, value, expiry time)]


def _inject_key(dev, code: int, value: int) -> bool:
    """Send a key event through *dev* and remember to skip it when it comes back."""
    try:
        dev.write(ecodes.EV_KEY, code, value)
        dev.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
    except Exception as e:
        logger.warning(f"Could not inject key {code}={value} on {getattr(dev, 'name', dev)}: {e}")
        return False
    _injected_pending.append((getattr(dev, "path", None), code, value, time.time() + _INJECTED_TTL_S))
    return True


def _is_own_injected_event(dev, event) -> bool:
    """True (once) if *event* is one we injected. Stale entries expire, so a
    lost injection can never swallow a real key event later."""
    now = time.time()
    _injected_pending[:] = [p for p in _injected_pending if p[3] > now]
    path = getattr(dev, "path", None)
    for i, (p, code, value, _) in enumerate(_injected_pending):
        if p == path and code == event.code and value == event.value:
            del _injected_pending[i]
            return True
    return False


def _grab_keyboards_hiding(devices, codes) -> None:
    """Grab the keyboards without leaving the desktop believing *codes* are held.

    Only keys the kernel still reports as down are balanced: if one was already
    released (a very fast tap), the desktop saw that release and nothing is owed.
    """
    keyboards = [d for d in devices if _is_keyboard_device(d)]
    held = []
    for dev in keyboards:
        try:
            down = set(dev.active_keys())
        except Exception:
            continue
        for code in codes:
            if code in down and _inject_key(dev, code, 0):
                held.append((dev, code))
    _grab_all_devices(keyboards)
    # Mark the keys down again. With the grab in place only we see this, and it
    # lets the kernel pass us the real release when the key comes up. On a
    # device the grab failed on, the desktop sees it too, but then it also sees
    # the real release, so it stays balanced either way.
    for dev, code in held:
        _inject_key(dev, code, 1)


def _grab_all_devices(devices) -> None:
    """Take an exclusive grab on each device, tracking the ones that succeed.

    Devices that refuse the grab (permissions, already grabbed) are skipped and
    left untracked so we never try to release something we don't hold.
    """
    for dev in devices:
        try:
            dev.grab()
            _grabbed_devices.append(dev)
            logger.info(f"Grabbed device: {dev.name}")
        except Exception as e:
            logger.warning(f"Could not grab {getattr(dev, 'name', dev)}: {e}")


def _release_all_grabs() -> None:
    """Release every grab we hold. Idempotent, and safe to call from any path.

    One unplugged device must not strand the grabs on every other keyboard, so
    each release is attempted independently and the registry is always cleared.
    """
    global _grabbed_devices
    for dev in _grabbed_devices:
        try:
            dev.ungrab()
            logger.info(f"Ungrabbed device: {getattr(dev, 'name', dev)}")
        except Exception as e:
            logger.warning(f"Could not ungrab {getattr(dev, 'name', dev)}: {e}")
    _grabbed_devices = []


def _rediscover_devices(devices) -> int:
    """Add keyboards that have appeared since the last scan. Returns how many.

    Without this, a device dropped by _drop_dead_devices (after a wireless
    receiver blip, a USB reset or a resume from suspend) would be gone for the
    life of the process, and on a single-keyboard machine the hotkey would
    silently stop working.

    Matched on the device node path, which is what identifies a device across
    reconnects here. Non-keyboards are ignored so this never grows the poll
    list with mice or power buttons.
    """
    known = {getattr(d, "path", None) for d in devices}
    added = 0
    for path in list_devices():
        if path in known:
            continue
        try:
            dev = InputDevice(path)
            if not _is_keyboard_device(dev):
                # Closed now, not whenever it is garbage-collected: every
                # mouse and button node is opened again on each scan.
                dev.close()
                continue
            devices.append(dev)  # evdev opens it non-blocking already
            added += 1
            logger.info(f"Input device appeared, now monitoring: {dev.name}")
        except Exception as e:
            # A node we cannot open (permissions, disappeared mid-scan) must
            # not stop the rest of the scan.
            logger.debug(f"Could not open {path}: {e}")
    return added


def _input_access_diagnosis() -> str:
    """Explain, in terms the user can act on, why no keyboards are readable.

    evdev's list_devices() filters nodes with os.access(R_OK | W_OK) and returns
    an empty list, never an exception, when the user can open none of them.
    Without this message the service looks healthy while ignoring every
    keypress.

    The two cases need opposite advice, so they are reported separately:
    not in the 'input' group (fixable with usermod + a re-login) versus in the
    group already (so the device node permissions are what is wrong).
    """
    import grp
    import pwd

    head = ("No readable keyboard devices found - hotkeys cannot work. "
            "TuxTalk reads keys from /dev/input, which needs the 'input' group. ")
    try:
        group = grp.getgrnam("input")
    except KeyError:
        return head + "This system has no 'input' group at all."

    # os.getgroups() is this SESSION's group list, fixed at login; the group's
    # member list is the SYSTEM's, updated the moment usermod runs. They
    # disagree only when setup granted the group after this session started,
    # which needs a restart rather than another usermod.
    in_session = group.gr_gid in os.getgroups()
    try:
        username = pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        username = os.environ.get("USER", "")
    in_system = username in group.gr_mem

    if in_session:
        return head + (
            "This user IS in the 'input' group, so the device nodes themselves "
            "are wrong: /dev/input/event* should be 'crw-rw---- root input'."
        )
    if in_system:
        return head + (
            f"Setup already added '{username}' to the 'input' group, but this "
            "session started before that and is still using the old "
            "permissions. RESTART the computer - logging out is often not "
            "enough, because a lingering user session keeps the old group list."
        )
    return head + (
        "This user is NOT in the 'input' group. Fix it with:\n"
        "    sudo usermod -a -G input $USER\n"
        "then RESTART the computer. Logging out and back in is often not "
        "enough - a lingering user session keeps the old group list alive."
    )


def _drop_dead_devices(dead_devices, devices, cfg) -> None:
    """Remove input devices that failed mid-read, ending a recording we could
    no longer end any other way.

    A hold-key recording (the only kind that keeps the keyboards grabbed) is
    stopped by a key-up that can only come from the device the key was
    pressed on. If that device dies — a wireless receiver blip, a
    USB reset, a resume from suspend — the release event is unreachable and
    state.is_recording would stay True for the life of the process.

    That matters far more than the lost device: both the stranded-grab
    backstop and the auto-timeout are gated on `not state.is_recording`, so a
    stuck flag leaves every *other* keyboard exclusively grabbed indefinitely
    and stops the service ever timing out. The user cannot type anywhere, and
    the only way back is the tray. So: end the recording deliberately.
    """
    if not dead_devices:
        return

    for dev in dead_devices:
        if dev in devices:
            devices.remove(dev)

    # The hold key works whatever the configured mode, so the grabs, not the
    # mode, tell a hold recording apart. A toggle or hands-free recording
    # holds no grab and carries on: any keyboard can still stop it.
    if state.is_recording and _grabbed_devices:
        logger.warning(
            "Input device died while the hold hotkey was down — ending the "
            "recording, since its key-up can no longer arrive"
        )
        _release_all_grabs()
        stop_recording(cfg.beeps, cfg.smart_quotes, cfg.notify, cfg.language,
                       cfg.auto_space, cfg.auto_period, cfg.injection_mode)

    dead_devices.clear()


def _release_grabs_if_not_recording() -> bool:
    """Safety net: holding an exclusive grab while not recording is always a bug.

    Called once per event-loop pass so that any path which fails to release —
    including one added later — self-heals within milliseconds instead of
    leaving the user with a dead keyboard. Returns True if it had to recover.
    """
    if _grabbed_devices and not state.is_recording:
        logger.warning(f"Releasing {len(_grabbed_devices)} stranded input grab(s)")
        _release_all_grabs()
        return True
    return False


def _open_input_stream(device_idx) -> None:
    """Open and start the microphone stream. Raises if the device is unusable."""
    # Use the device's native sample rate (may differ from 16kHz on ALSA hw: devices)
    state.recording_samplerate = _get_device_samplerate(device_idx)
    sd.default.channels = CHANNELS
    sd.default.samplerate = state.recording_samplerate
    state.stream = sd.InputStream(callback=_sd_callback, dtype='int16', device=device_idx)
    state.stream.start()


def start_recording(beeps_on: bool, notify_on: bool, input_device_idx,
                    from_toggle: bool = False, edit_mode: bool = False) -> bool:
    """Begin capturing audio. Returns True only if recording actually started.

    *from_toggle* marks a start by the toggle key, so a quick second tap can
    switch it to hands-free (see _toggle_pressed_while_recording).

    On failure this releases any input-device grabs before returning. The
    keyboard is grabbed just before this is called, so without that release a
    microphone error would leave the user unable to type anywhere at all.
    """
    state.frames = []
    state.was_cancelled = False
    state.silence = None
    state.chunker = None
    state.press_t0 = time.time()
    state.toggle_started_at = state.press_t0 if from_toggle else None
    state.edit_mode = edit_mode
    # Set before the stream starts — _sd_callback drops frames unless it's set.
    state.is_recording = True
    try:
        _open_input_stream(input_device_idx)
    except Exception as first_error:
        # The mic is stored as a device NUMBER, resolved once at startup. Unplug
        # a USB device and the numbering shifts, leaving that number pointing at
        # nothing. Re-resolve by name and try once more before giving up.
        logger.warning(f"Microphone {input_device_idx} failed ({first_error}); re-resolving")
        _stop_stream_safely()
        try:
            fresh_idx = _pick_input_device(load_config().mic)
            _open_input_stream(fresh_idx)
            logger.info(f"Microphone re-resolved to device {fresh_idx}")
        except Exception as e:
            # Genuinely unavailable: unplugged, or held exclusively by another
            # app. Undo everything and tell the user; failing silently would
            # leave the keyboard grabbed and the machine unusable.
            logger.error(f"Could not start recording: {e}", exc_info=True)
            print(f"Could not start recording: {e}")
            state.is_recording = False
            _stop_stream_safely()
            _release_all_grabs()
            _beep(beeps_on, *CANCEL_BEEP)
            if notify_on:
                _notify("TuxTalk", "Microphone unavailable — check that no other app is using it")
            return False

    _arm_chunker(getattr(state, "recording_samplerate", SAMPLE_RATE), edit_mode)
    _pause_media_players()
    print("Recording…")
    logger.debug("Recording started")
    _beep(beeps_on, *START_BEEP)
    if notify_on: _notify("TuxTalk", "Recording… (speak now)")

    # Notify GNOME extension that recording started (turns icon red)
    _notify_tray_recording_state(True)

    # Show recording indicator
    if recording_indicator:
        try:
            recording_indicator.show_at_position()
            recording_indicator.start_recording()
        except Exception as e:
            print(f"Failed to show recording indicator: {e}")
            logger.error(f"Failed to show recording indicator: {e}", exc_info=True)

    return True

def _stop_stream_safely():
    """Stop and close the microphone stream. close() runs even when stop()
    fails (an unplugged mic), so the device is free to be opened again."""
    stream, state.stream = state.stream, None
    if stream is None:
        return
    try:
        stream.stop()
    except Exception as e:
        logger.warning(f"Could not stop the microphone stream: {e}")
    finally:
        try:
            stream.close()
        except Exception as e:
            logger.warning(f"Could not close the microphone stream: {e}")

def cancel_recording(beeps_on: bool, notify_on: bool, reason="Cancelled"):
    """End a recording without transcribing it, telling the user *reason*."""
    state.is_recording = False
    state.was_cancelled = True
    state.edit_mode = False
    _stop_stream_safely()
    _resume_media_players()
    _beep(beeps_on, *CANCEL_BEEP)
    print(reason)
    logger.debug(f"Recording cancelled: {reason}")
    if notify_on: _notify("TuxTalk", reason)

    # Hide recording indicator
    if recording_indicator:
        recording_indicator.hide_indicator()

    # Notify GNOME extension that recording stopped (icon returns to normal)
    _notify_tray_recording_state(False)

def _is_wayland_session() -> bool:
    """True when a Wayland compositor is available for wl-copy to talk to."""
    return bool(os.environ.get("WAYLAND_DISPLAY"))


# ydotool talks to ydotoold over a socket. These calls run on the same thread
# that polls for the hotkey, so an untimed call against a wedged daemon stops
# dictation responding at all, with the tray still showing the service as up.
YDOTOOL_TIMEOUT_S = 5.0

# The wtype fallback types a whole utterance, so it needs far longer than a
# single keystroke. wtype has no per-key delay, so a fixed ceiling is enough.
WTYPE_TIMEOUT_S = 20.0

# How long wl-copy may take to hand the dictation to the compositor. It is
# usually a few milliseconds; a slower one is stuck, and nothing is pasted.
WL_COPY_READY_TIMEOUT_S = 1.0

# ydotool's own default wait after each `ydotool key` event. Backspaces pass it
# explicitly, so their time limit is worked out from the speed really used.
BACKSPACE_DELAY_MS = 12


class KeystrokesStoppedPartway(Exception):
    """ydotool was stopped by its time limit after sending some of the keys, so
    an unknown number of them reached the app."""


def _typing_timeout(n_chars: int, delay_ms: int) -> float:
    """Seconds to allow `ydotool type -d D -H D` for *n_chars* characters.

    Each character is a press, a hold and a release, about two delays, and a
    capital adds a Shift press; the 50% margin covers that and a busy system.
    It still stops a wedged ydotoold, just not a long dictation.
    """
    return 10 + n_chars * 2 * delay_ms / 1000 * 1.5


def _ydotool_key(keys, timeout: float = YDOTOOL_TIMEOUT_S, what: str = "keystroke",
                 raise_on_timeout: bool = False) -> bool:
    """Send raw key codes via ydotool. True only if they were actually delivered.

    ydotool exits non-zero when it can't reach ydotoold ("Please check if
    ydotoold is running"), so the exit code is checked: keystrokes that never
    happened must not reach the undo buffer.

    With *raise_on_timeout*, running out of time raises KeystrokesStoppedPartway
    instead of returning False, for a caller that must tell "none of the keys
    were sent" from "some of them were".
    """
    if not _which("ydotool"):
        return False
    try:
        result = subprocess.run(
            ["ydotool", "key"] + list(keys),
            check=False, env=_get_ydotool_env(),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.error(f"ydotool {what} timed out after {timeout:.0f}s — is ydotoold wedged?")
        if raise_on_timeout:
            raise KeystrokesStoppedPartway(what)
        return False
    except Exception as e:
        logger.debug(f"ydotool {what} failed: {e}")
        return False

    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        logger.error(f"ydotool {what} failed (exit {result.returncode}): {detail}")
        return False
    return True


def _typed_breaks_for(text: str, window_class) -> str:
    """*text* ready to type into *window_class*. Typed line breaks go in as
    Shift+Enter, but terminals such as gnome-terminal treat Shift+Enter as
    Enter and would run the half-dictated command, so in a terminal every
    line break becomes a space. Pasting (Wayland) keeps them: a terminal takes
    pasted newlines as text."""
    if not is_terminal_class(window_class):
        return text
    joined = re.sub(r"[ \t]*(?:\xa7SHIFT_ENTER\xa7|\n)+[ \t]*", " ", text)
    if joined != text:
        logger.info("Typing into a terminal: line breaks typed as spaces")
    return joined


def _send_shift_enter():
    """Send Shift+Enter keystrokes to create line break without submitting."""
    # KEY_LEFTSHIFT (42), KEY_ENTER (28)
    return _ydotool_key(["42:1", "28:1", "28:0", "42:0"], what="shift+enter")

def _send_enter():
    """Send Enter keystroke to create a new line."""
    # KEY_ENTER (28)
    return _ydotool_key(["28:1", "28:0"], what="enter")

def _send_select_all_delete():
    """Send Ctrl+A then Backspace to clear the entire input field.

    Used by the 'delete everything' voice command — wipes the whole textarea
    regardless of whether the contents came from TuxTalk, manual typing, or
    pasting from elsewhere.
    """
    # KEY_LEFTCTRL (29) + KEY_A (30), then KEY_BACKSPACE (14)
    if not _ydotool_key(["29:1", "30:1", "30:0", "29:0"], what="select-all"):
        return False
    time.sleep(0.05)
    return _ydotool_key(["14:1", "14:0"], what="delete")

_OUTPUT_BACKEND = None


def _output_backend():
    """Process-wide OutputBackend, chosen once by FLATPAK_ID. Backend A
    (ydotool) delegates to _type_text below, so the non-Flatpak path is
    unchanged; Backend B (libei) is used only inside a Flatpak."""
    global _OUTPUT_BACKEND
    if _OUTPUT_BACKEND is None:
        from .output_backends import get_output_backend
        _OUTPUT_BACKEND = get_output_backend()
    return _OUTPUT_BACKEND


def _type_text(text: str) -> bool:
    """Type text (handling line-break markers). Returns True if it was
    actually typed into the focused app, False if injection failed.

    Stops at the first part or line break that fails: typing on after a gap
    would leave the later lines run into the earlier ones."""
    # Handle special markers first
    if "§SHIFT_ENTER§" in text:
        parts = text.split("§SHIFT_ENTER§")
        for i, part in enumerate(parts):
            if part and not _type_text_raw(part):
                return False
            if i < len(parts) - 1:  # Not the last part, send Shift+Enter
                time.sleep(0.05)  # Small delay between text and key
                # The line break is part of the text: a failed one is a
                # partial injection, and the undo buffer must not count it.
                if not _send_shift_enter():
                    return False
                time.sleep(0.05)  # Small delay after key
        return True

    # Handle regular newlines by converting them to Enter key presses
    if "\n" in text:
        parts = text.split("\n")
        for i, part in enumerate(parts):
            if part and not _type_text_raw(part):
                return False
            if i < len(parts) - 1:  # Not the last part, send Enter
                time.sleep(0.05)  # Small delay between text and key
                if not _send_enter():
                    return False
                time.sleep(0.05)  # Small delay after key
        return True

    # Normal text typing
    return _type_text_raw(text)

def _type_on_layout(text: str, delay_ms: int, what: str):
    """Type *text* on a non-US keyboard layout, or return None to use
    `ydotool type` (plain US layouts, or the layout couldn't be read).

    `ydotool type` sends US key positions, which the compositor reads with the
    real layout: Y and Z swapped on QWERTZ, umlauts impossible (issue #8).
    keyboard_layout works out the real keys instead. If the layout can't type
    some character at all, the dictation is pasted, which works on any layout."""
    from . import keyboard_layout
    try:
        tokens = keyboard_layout.layout_key_events(text)
    except keyboard_layout.UntypableText as e:
        layout, blocker = e.args
        logger.info(f"{what}: the {layout} keyboard layout can't type {blocker!r} "
                    f"(U+{ord(blocker):04X}); pasting instead")
        if _is_wayland_session():
            return _paste_text(text)
        return _paste_text_x11(text)
    except Exception as e:
        logger.warning(f"{what}: couldn't work out keys for the keyboard layout ({e}); typing as US")
        return None
    if tokens is None:
        return None
    logger.info(f"{what}: typing {len(text)} chars for the keyboard layout, delay={delay_ms}ms")
    # ydotool key -d is the wait between every press and release, so a
    # character takes about two delays, like `ydotool type -d -H` with the same value.
    timeout = 10 + len(tokens) * delay_ms / 1000 * 2
    return _ydotool_key(["-d", str(delay_ms)] + tokens, timeout=timeout, what=what)


def _type_text_raw(text: str):
    """Type text with ydotool, falling back to wtype. True only if it was typed.

    _typing_delay sets the keystroke timing: lower is faster but can
    transpose letters.
    """
    if _which("ydotool"):
        layout_result = _type_on_layout(text, max(5, min(50, _typing_delay)), "layout typing")
        if layout_result is not None:
            return layout_result
        try:
            env = _get_ydotool_env()
            # -d = pause between keys, -H = how long each key is held (ms).
            # Lower values are faster but may cause letters to arrive out of order.
            delay_ms = max(5, min(50, _typing_delay))  # Clamp to 5-50ms
            delay_str = str(delay_ms)
            timeout = _typing_timeout(len(text), delay_ms)
            logger.info(f"ydotool type: delay={delay_str}ms, text_len={len(text)}")
            proc = subprocess.Popen(
                ["ydotool", "type", "-d", delay_str, "-H", delay_str, "-f", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env
            )
            try:
                _, stderr = proc.communicate(input=text.encode("utf-8"), timeout=timeout)
            except subprocess.TimeoutExpired:
                # Kill and reap the hung ydotool so it doesn't linger and keep
                # typing into whatever is focused later.
                proc.kill()
                proc.communicate()
                logger.error(f"ydotool type timed out after {timeout:.0f}s; killed the process")
                return False
            if proc.returncode != 0:
                logger.error(f"ydotool type failed: rc={proc.returncode}, stderr={stderr.decode()}")
                return False
            logger.info(f"ydotool type succeeded: rc=0, typed {len(text)} chars")
            return True
        except Exception as e:
            logger.debug(f"ydotool failed: {e}")
    if _which("wtype"):
        # Report wtype's real exit code so the undo buffer only tracks text
        # that was actually typed, and bound the wait like the ydotool branch
        # so a stalled compositor can't freeze dictation.
        proc = subprocess.Popen(["wtype", "--", text])
        try:
            proc.communicate(timeout=WTYPE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            # Kill and reap, so it cannot come back to life and type into
            # whatever the user has focused by then.
            proc.kill()
            proc.communicate()
            logger.error(f"wtype timed out after {WTYPE_TIMEOUT_S}s; killed the process")
            return False
        return proc.returncode == 0
    if _which("wl-copy"):
        try:
            import pyperclip
            pyperclip.copy(text)
            print("Copied to clipboard. Ctrl+V to paste.")
            logger.info("Text copied to clipboard (fallback mode)")
            # Nothing was actually typed into the field — report failure so
            # the undo buffer doesn't track text the user must paste manually.
            return False
        except Exception:
            pass
    logger.error("Could not type text: no ydotool/wtype/wl-copy available")
    print("Could not type text (no ydotool/wtype).")
    return False

# Window classes that need Ctrl+Shift+V for paste (terminals).
# All other apps get plain Ctrl+V — works in chat/editor/browser inputs and
# avoids a Chromium/Electron regression that mishandles synthetic Ctrl+Shift+V.
_TERMINAL_WM_CLASSES = frozenset({
    "org.gnome.Ptyxis", "Ptyxis",
    "org.gnome.Terminal", "Gnome-terminal",
    "konsole", "org.kde.konsole",
    "yakuake", "org.kde.yakuake",
    "kitty",
    "Alacritty",
    "WezTerm", "org.wezfurlong.wezterm",
    "foot", "footclient",
    "Tilix", "com.gexperts.Tilix",
    "Terminator",
    "xterm", "XTerm", "UXTerm",
    "rxvt", "URxvt",
    # Common on Sway / Hyprland setups.
    "com.mitchellh.ghostty", "ghostty",
    "org.gnome.Console", "kgx",
    "xfce4-terminal", "Xfce4-terminal",
    "com.system76.CosmicTerm",
    "mate-terminal", "lxterminal", "qterminal", "terminology",
    "st", "st-256color", "sakura", "Termite", "contour",
    # X11 desktops (XFCE, MATE, Cinnamon...) report these WM_CLASS names.
    "Guake", "Tilda", "Roxterm", "cool-retro-term", "Eterm",
})

# Lower-cased once, because the class arrives from several independent sources
# (the GNOME Shell extension, the KWin script, the compositors) that don't
# agree on capitalisation.
_TERMINAL_WM_CLASSES_LOWER = frozenset(c.lower() for c in _TERMINAL_WM_CLASSES)


# Code editors, for "Plain text in terminals and code editors": no period
# typed at the end. Matched case-insensitively, plus any JetBrains IDE
# (jetbrains-idea, jetbrains-pycharm...). Kate is left out: it's used for
# prose as much as code.
_CODE_EDITOR_CLASSES_LOWER = frozenset({
    "code", "code-oss", "code-url-handler", "codium", "vscodium",
    "dev.zed.zed", "zed", "sublime_text", "geany", "org.gnome.builder",
    "org.kde.kdevelop", "neovide",
})


def app_kind(wm_class: str | None) -> str:
    """"terminal", "code" or "" for the focused window, for plain_for_app."""
    if not wm_class:
        return ""
    if is_terminal_class(wm_class):
        return "terminal"
    lower = wm_class.lower()
    if lower in _CODE_EDITOR_CLASSES_LOWER or lower.startswith("jetbrains-"):
        return "code"
    return ""


def is_terminal_class(wm_class: str | None) -> bool:
    """Whether the focused window is a terminal, so paste uses Ctrl+Shift+V.

    A terminal treats plain Ctrl+V as readline's quoted-insert and shows
    nothing, so getting this wrong is silent. None means nobody reported focus
    (no provider on this desktop) and must not be read as a terminal.

    Matching is exact-but-case-insensitive: substring matching would make
    "kitty-photo-viewer" a terminal.
    """
    if not wm_class:
        return False
    return wm_class.lower() in _TERMINAL_WM_CLASSES_LOWER


# Electron/Chromium apps where synthetic paste (Ctrl+V via ydotool) is broken
# on Wayland — modifier-key chords from /dev/uinput are silently dropped and
# the focused input blurs. For these apps, route to a fast Type path instead
# (plain character keystrokes still work, per OpenWhispr #240 and others).
_ELECTRON_PASTE_BROKEN_CLASSES = frozenset({
    "Claude", "claude", "claude-desktop", "Claude Desktop", "anthropic-claude",
    # Claude Desktop reports this reverse-DNS id or one of the names above,
    # depending on how it was packaged; a missing one means a paste that
    # silently does nothing.
    "com.anthropic.Claude",
})


def _query_focused_window_class() -> str | None:
    """The focused window's wm_class (or Wayland app_id), or None if unknown.

    The GNOME extension and the KWin script (kwin_focus.py) push it to the
    tray, which we ask over D-Bus. Sway, i3, Hyprland and niri have nothing
    to push it, so when the tray doesn't know, the compositor is asked
    directly (compositor_focus.py).

    TuxTalk's own windows count as unknown: a stale report of Preferences
    must not pick the paste shortcut for the app the user is really in.
    """
    from .desktop_identity import is_own_window_class
    reported = _query_reported_window_class()
    if reported and not is_own_window_class(reported):
        return reported
    from .compositor_focus import focused_class
    asked = focused_class()
    return None if is_own_window_class(asked) else asked


def _query_reported_window_class() -> str | None:
    """Query the tray's D-Bus service for the currently focused window's wm_class.

    This process is a subprocess of the tray, which owns the D-Bus service and
    receives the focus updates, so it is asked across processes. Returns None
    if the class is unknown or D-Bus is unavailable.
    """
    try:
        # The shared, introspect-free proxy with a short timeout: this runs
        # between the hotkey release and the text appearing, and it is only a
        # hint for choosing the paste shortcut, so a fast "don't know" beats a
        # slow answer.
        proxy = _get_tray_dbus_proxy()
        result = proxy.GetFocusedWindowClass(
            dbus_interface="io.github.ronb1964.TalkType",
            timeout=_TRAY_DBUS_TIMEOUT,
        )
        s = str(result) if result else ""
        return s if s else None
    except Exception as e:
        logger.debug(f"Focused window class query failed: {e}")
        return None


def _type_text_fast(text: str, delay_ms: int = 1):
    """Type text via ydotool with a very small inter-event delay.

    Used for Electron/Chromium apps where clipboard-paste is broken on Wayland.
    1ms per event (about 415 chars/sec) is the practical floor; going lower
    risks Electron's input buffer dropping characters under burst.
    """
    if not _which("ydotool"):
        logger.error("Fast-type fallback: ydotool unavailable")
        return False
    layout_result = _type_on_layout(text, max(1, delay_ms), "layout fast-type")
    if layout_result is not None:
        return layout_result
    try:
        env = _get_ydotool_env()
        d = str(max(1, delay_ms))
        timeout = _typing_timeout(len(text), max(1, delay_ms))
        logger.info(f"ydotool fast-type: delay={d}ms, text_len={len(text)}")
        proc = subprocess.Popen(
            ["ydotool", "type", "-d", d, "-H", d, "-f", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env,
        )
        try:
            _stdout, stderr = proc.communicate(input=text.encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            logger.error(f"ydotool fast-type timed out after {timeout:.0f}s; killed the process")
            return False
        if proc.returncode != 0:
            logger.error(f"ydotool fast-type failed: rc={proc.returncode}, stderr={stderr.decode(errors='replace')}")
            return False
        return True
    except Exception as e:
        logger.error(f"Fast-type exception: {e}")
        return False


def _send_paste_keystroke(focused_class: str | None, primary_holds_text: bool = False) -> bool:
    """Send the paste shortcut for *focused_class*. True if it was sent.

    A terminal gets Ctrl+Shift+V (Ctrl+V is readline's quoted-insert there and
    shows nothing) and other known apps Ctrl+V. An unknown window gets
    Shift+Insert when the primary selection holds the text too: GUI toolkits
    paste the clipboard on Shift+Insert and terminals paste the primary
    selection, so it lands either way. Without that, Ctrl+V, which suits most
    apps."""
    # KEY_LEFTSHIFT=42, KEY_LEFTCTRL=29, KEY_V=47, KEY_INSERT=110
    if is_terminal_class(focused_class):
        keys = ["42:1", "29:1", "47:1", "47:0", "29:0", "42:0"]  # Ctrl+Shift+V
        logger.info(f"Paste: Ctrl+Shift+V (terminal class={focused_class!r})")
    elif focused_class is None and primary_holds_text:
        keys = ["42:1", "110:1", "110:0", "42:0"]  # Shift+Insert
        logger.info("Paste: Shift+Insert (window unknown; clipboard and primary selection set)")
    else:
        keys = ["29:1", "47:1", "47:0", "29:0"]  # Ctrl+V
        logger.info(f"Paste: Ctrl+V (class={focused_class!r})")
    if not _ydotool_key(keys, what="paste"):
        # The keystroke never reached the desktop: the text is sitting on the
        # clipboard but never made it into the app.
        logger.error("Paste keystroke failed — text was not inserted")
        return False
    return True


def _copy_to_primary(text: str) -> bool:
    """Put *text* on the primary selection as well (wl-copy --primary). True
    once wl-copy has handed it to the compositor; False if the desktop has no
    primary selection or wl-copy failed."""
    from . import clipboard
    try:
        result = subprocess.run(clipboard.wl_copy_command() + ["--primary"],
                                input=text.encode("utf-8"), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=WL_COPY_READY_TIMEOUT_S)
    except Exception as e:
        logger.info(f"Could not set the primary selection: {e}")
        return False
    if result.returncode != 0:
        logger.info(f"Could not set the primary selection (wl-copy exit {result.returncode})")
        return False
    return True


def _paste_text_x11(text: str) -> bool:
    """Paste on an X11 session, for text the keyboard layout can't type.

    _paste_text needs wl-copy, which only talks to Wayland. Here the service
    puts the text on the X11 clipboard through its own GTK loop and then sends
    the same paste keystroke. It's only the fallback for untypable text
    (Latin words on a Ukrainian layout, an emoji); ordinary X11 dictation is
    still typed. True only if the text reached the app.
    """
    from . import clipboard
    if not clipboard.copy_text_on_gtk_thread(text):
        logger.error("X11 paste: couldn't put the text on the clipboard; nothing was inserted")
        return False
    time.sleep(0.08)    # let the selection settle before the app asks for it
    return _send_paste_keystroke(_query_focused_window_class())


def _paste_text(text: str) -> bool:
    """Wayland paste: put *text* on the clipboard with wl-copy, then send the
    paste keystroke (_send_paste_keystroke picks Ctrl+V, Ctrl+Shift+V or
    Shift+Insert).

    Returns True only when the text actually reached the focused app: the undo
    buffer records it, and a paste over an unset clipboard would insert
    whatever the user copied earlier. *text* must not contain §SHIFT_ENTER§.
    """
    # wl-copy talks to a Wayland compositor. On an X11 login it fails instantly,
    # and firing Ctrl+V anyway pastes the previous clipboard contents.
    if not _is_wayland_session():
        logger.info("Paste unavailable: not a Wayland session — falling back to typing")
        return False

    try:
        if _which("wl-copy") and _which("ydotool"):
            # Copy text to clipboard
            paste_start = time.time()
            logger.info(f"TIMING: Starting paste operation for {len(text)} chars")
            # Optionally keep what the user had copied, to put back afterwards
            # (issue #7). Off by default: a dictation left on the clipboard can
            # be re-pasted if it landed in the wrong window.
            from . import clipboard
            global _clipboard_keeper
            keeping = _restore_clipboard
            if keeping:
                if _clipboard_keeper is None:
                    _clipboard_keeper = clipboard.ClipboardKeeper()
                _clipboard_keeper.before_paste()
            # --sensitive keeps the dictation out of clipboard managers' history
            # (Klipper saves every entry to disk) where wl-copy supports it.
            proc = subprocess.Popen(clipboard.wl_copy_command(), stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                proc.stdin.write(text.encode("utf-8"))
                proc.stdin.close()

                # wl-copy forks a background copy to serve the clipboard once
                # the compositor has accepted the selection, and the process we
                # started then exits 0. Until it exits the clipboard may still
                # hold the old contents, and pasting would insert those.
                try:
                    status = proc.wait(timeout=WL_COPY_READY_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    logger.error(f"wl-copy did not set the clipboard within "
                                 f"{WL_COPY_READY_TIMEOUT_S}s — skipping paste")
                    return False
                if status != 0:
                    err = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
                    logger.error(f"wl-copy failed (exit {status}): {err} — clipboard not set, skipping paste")
                    return False
                wl_copy_time = time.time() - paste_start
                logger.info(f"TIMING: wl-copy set the clipboard in {wl_copy_time:.3f}s")

                # With the window unknown, the primary selection gets the text
                # too, so the paste key works in a terminal as well.
                focused_class = _query_focused_window_class()
                primary_holds_text = False
                if focused_class is None:
                    if keeping:
                        _clipboard_keeper.before_primary()
                    primary_holds_text = _copy_to_primary(text)

                ydotool_start = time.time()
                if not _send_paste_keystroke(focused_class, primary_holds_text):
                    return False
                ydotool_time = time.time() - ydotool_start
                logger.info(f"TIMING: ydotool paste command took {ydotool_time:.3f}s")

                # Brief delay for paste to register
                time.sleep(0.05)
                total_paste_time = time.time() - paste_start
                logger.info(f"TIMING: Total paste operation time: {total_paste_time:.3f}s")
                return True
            finally:
                # Only a wl-copy that never finished handing off is still ours
                # to stop; the background copy serving the clipboard is not.
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=0.3)
                except Exception:
                    pass  # wl-copy cleanup is best-effort
                if proc.stderr is not None:
                    proc.stderr.close()
                # Restore even after a failed paste, so the user's clipboard
                # isn't lost either way.
                if keeping:
                    _clipboard_keeper.after_paste(text)
    except subprocess.TimeoutExpired as e:
        logger.error(f"Paste injection timeout: {e}")
    except Exception as e:
        logger.error(f"Paste injection failed: {e}")
    return False

def _determine_injection_method(injection_mode: str) -> tuple[str, str]:
    """
    Decide between typing and pasting for this dictation.

    Args:
        injection_mode: User's configured mode ("type", "paste", or "auto")

    Returns:
        Tuple of (actual_mode, reason)
        - actual_mode: "type" or "paste" (what to actually use)
        - reason: str (explanation for logging)

    Auto pastes on Wayland: the paste keystroke is picked per paste from the
    focused window's class (Ctrl+Shift+V in a terminal), and Electron apps where
    paste is broken are switched to fast typing in _inject_text. It types on X11.
    """
    # Inside a Flatpak, clipboard-paste needs wl-copy (not bundled) and the libei
    # output backend types directly — always type, never attempt paste.
    if os.environ.get("FLATPAK_ID"):
        return ("type", "Flatpak: libei typing")

    # If not auto mode, use user's choice directly
    if injection_mode.lower() != "auto":
        if injection_mode.lower() == "paste":
            return ("paste", "User selected paste mode")
        else:
            return ("type", "User selected type mode")

    # Pasting needs wl-copy, which only talks to a Wayland compositor. On X11
    # every paste would fail and fall back to typing anyway.
    if not _is_wayland_session():
        return ("type", "Auto: not a Wayland session, typing")
    return ("paste", "Auto: paste")

def plan_line_breaks(text: str, is_terminal: bool) -> list[str]:
    """How to deliver *text*'s line breaks: the chunks to paste, in order.

    One chunk means one paste with real newlines in it. More than one means
    paste each and send Shift+Enter between them.

    Terminals bind no soft-newline key — Enter runs the command — so the
    keystroke is simply ignored there and everything lands on one line. What a
    terminal does accept is a newline inside pasted text: bracketed paste (on by
    default in modern bash and zsh) inserts it literally instead of executing at
    it. So a terminal gets one paste carrying real newlines.

    Everywhere else keeps the keystroke, because in a chat input like Claude
    Desktop a literal newline SUBMITS the message instead of breaking the line.
    """
    marker = "\xa7SHIFT_ENTER\xa7"
    normalised = text.replace("\n", marker)
    if is_terminal:
        return [normalised.replace(marker, "\n")]
    return normalised.split(marker)


def _send_backspaces(count: int) -> bool:
    """Send *count* backspace keypresses. True only if they were delivered.

    The caller updates the undo buffer from this result, so a wrong answer here
    desynchronises the buffer from the document and the next undo eats the
    user's own writing. Raises KeystrokesStoppedPartway when the time limit
    stopped ydotool after some of the backspaces had already landed.
    """
    if count <= 0:
        return True
    # KEY_BACKSPACE = 14, pressed and released once per character. Each event
    # is followed by BACKSPACE_DELAY_MS, so a long undo takes seconds and the
    # time limit grows with it (plus a 50% margin and the startup allowance).
    key_sequence = ["-d", str(BACKSPACE_DELAY_MS)]
    for _ in range(count):
        key_sequence.extend(["14:1", "14:0"])
    timeout = YDOTOOL_TIMEOUT_S + count * 2 * BACKSPACE_DELAY_MS / 1000 * 1.5
    return _ydotool_key(key_sequence, timeout=timeout, what=f"{count} backspaces",
                        raise_on_timeout=True)

# Key names voice_navigation uses -> Linux key codes (the letter A is looked up
# on the active layout, as Ctrl shortcuts are matched by letter).
_NAV_KEYCODES = {"CTRL": 29, "SHIFT": 42, "LEFT": 105, "RIGHT": 106, "UP": 103,
                 "DOWN": 108, "HOME": 102, "END": 107}


def _nav_keycode(name: str) -> int:
    if name in _NAV_KEYCODES:
        return _NAV_KEYCODES[name]
    from .keyboard_layout import shortcut_keycode
    try:
        return shortcut_keycode(name.lower()) or 30
    except Exception:
        return 30    # KEY_A on a US layout


# Navigation keys go faster than typing: a selection is drawn as it grows, and
# at typing speed it crawled visibly across the line.
NAV_KEY_DELAY_MS = 4


def _send_chords(chords) -> bool:
    """Press [(modifiers, key)] in order with ydotool. Consecutive keys with
    the same modifiers share one press of them (Shift held once across forty
    Lefts, not pressed forty times), which halves the events and never leaves
    a modifier flapping. True only if they were delivered."""
    if not chords:
        return True
    sequence = ["-d", str(NAV_KEY_DELAY_MS)]
    events = 0
    held = ()
    for mods, key in list(chords) + [((), None)]:
        if tuple(mods) != held:
            sequence += [f"{_nav_keycode(m)}:0" for m in reversed(held)]
            sequence += [f"{_nav_keycode(m)}:1" for m in mods]
            events += len(held) + len(mods)
            held = tuple(mods)
        if key is not None:
            code = _nav_keycode(key)
            sequence += [f"{code}:1", f"{code}:0"]
            events += 2
    timeout = YDOTOOL_TIMEOUT_S + events * NAV_KEY_DELAY_MS / 1000 * 1.5
    return _ydotool_key(sequence, timeout=timeout, what=f"{len(chords)} navigation keys")


def _transcribe_audio(audio_f32, language: str | None) -> str | None:
    """Transcribe audio with the loaded engine (Whisper or Parakeet) and filter hallucinations.

    Returns the raw transcribed text, or None if no speech was detected.
    """
    transcribe_start = time.time()

    from .whisper_vulkan import VulkanWhisperModel
    from . import custom_words, language_limit
    words_prompt = custom_words.prompt(_custom_words)
    # Auto-detect held to the user's languages: one language is simply used,
    # two or more are checked against Whisper's own pick (language_limit.py).
    # Parakeet below has no language setting, so this changes nothing there.
    if not language:
        language = language_limit.forced_language(_auto_languages)
    limit_to = () if language else _auto_languages
    if isinstance(model, VulkanWhisperModel):
        # Only pass a prompt or a language list when there is one, so nothing
        # changes without them.
        extra = {}
        if words_prompt:
            extra["prompt"] = words_prompt
        if limit_to:
            extra["limit_to"] = limit_to
        raw, _no_speech = model.transcribe(audio_f32, language, **extra)
        logger.info(f"TIMING: Transcription completed in {time.time() - transcribe_start:.2f}s (Vulkan GPU)")
        print(f"Raw (graphics): {_loggable(raw)}")
        logger.info(f"Raw transcription (Vulkan GPU): {_loggable(raw)}")
        # whisper.cpp's no-speech score isn't reliable (0.90 on clear speech,
        # 0.00 on silence, see whisper_vulkan.parse_result), so the recording's
        # own loudness stands in for it. With nobody talking, a lone "Thanks
        # for watching!" is invented; said aloud, it is kept.
        if custom_words.is_echo(raw, _custom_words):
            logger.info("Whisper read your words back with nothing said; dropped")
            return None
        from .silence import has_no_speech
        no_speech = 1.0 if has_no_speech(audio_f32, SAMPLE_RATE) else 0.0
        return _strip_hallucinations(raw, no_speech_prob=no_speech) or None

    from .parakeet_engine import ParakeetModel
    from .parakeet_gpu import ParakeetGpuModel
    if isinstance(model, (ParakeetModel, ParakeetGpuModel)):
        # Parakeet detects the language itself and has no Whisper-style
        # decoding options. It has no no-speech score either, yet it does
        # invent a word in silence: an F9 tap with nothing said typed
        # "Mm-hmm." So, as for the Vulkan engine, a recording with nobody
        # talking counts as no speech, and a filler alone is dropped.
        raw = model.recognize(audio_f32)
        where = " on GPU" if isinstance(model, ParakeetGpuModel) else ""
        logger.info(f"TIMING: Transcription completed in {time.time() - transcribe_start:.2f}s (Parakeet{where})")
        print(f"Raw (Parakeet): {_loggable(raw)}")
        logger.info(f"Raw transcription (Parakeet): {_loggable(raw)}")
        from .silence import has_no_speech
        no_speech = 1.0 if raw and has_no_speech(audio_f32, SAMPLE_RATE) else 0.0
        return _strip_hallucinations(raw, no_speech_prob=no_speech) or None

    whisper_options = dict(
        vad_filter=False,
        beam_size=5,
        condition_on_previous_text=True,
        temperature=0.0,
        # Word-level timestamps strengthen Whisper's long-form seek logic,
        # preventing dropped words at pause boundaries.
        word_timestamps=True,
        # Repetition-loop guards: Whisper can get stuck repeating one phrase
        # many times in a row. A mild penalty plus n-gram blocking breaks
        # the loop without changing normal decoding.
        repetition_penalty=1.1,
        no_repeat_ngram_size=5,
        # Your Words, so Whisper spells them the user's way. faster-whisper
        # puts them in the prompt of every 30-second window.
        hotwords=words_prompt,
        # vad_filter is disabled: Silero VAD trims speech onsets at segment
        # boundaries (especially the start of recordings and after sentence
        # pauses), causing leading words to vanish from raw Whisper output.
        # See: github.com/SYSTRAN/faster-whisper/issues/925
        # Push-to-talk dictation has minimal non-speech audio to filter,
        # so disabling VAD has near-zero quality cost.
        # hallucination_silence_threshold also stays unset — same reason:
        # it drops real speech after natural 2+ second pauses. Trailing
        # hallucination phrases are handled by _strip_hallucinations().
    )
    segments, info = model.transcribe(audio_f32, language=(language or None), **whisper_options)
    # faster-whisper detects the language before it decodes a word (segments
    # is a generator), so starting again in another language costs only that
    # detection, and only when its pick is off the user's list.
    better = (language_limit.redirect(info.language, dict(info.all_language_probs or ()), limit_to)
              if limit_to else None)
    if better:
        logger.info(f"Language: Whisper heard {info.language}, which isn't on your list; using {better}")
        segments, info = model.transcribe(audio_f32, language=better, **whisper_options)
    transcribe_time = time.time() - transcribe_start
    logger.info(f"TIMING: Transcription completed in {transcribe_time:.2f}s")
    if transcribe_time > 2.0:
        logger.warning(f"Transcription took {transcribe_time:.2f}s (first run may be slower due to CUDA compilation)")

    # Collect segments (generator can only be consumed once)
    seg_list = list(segments)

    raw = " ".join(seg.text for seg in seg_list).strip()
    max_no_speech_prob = max((seg.no_speech_prob for seg in seg_list), default=0.0)
    print(f"Raw (before filter): {_loggable(raw)}  [no_speech_prob={max_no_speech_prob:.2f}]")
    logger.info(f"Raw transcription (before hallucination filter): {_loggable(raw)}  [no_speech_prob={max_no_speech_prob:.2f}]")

    if custom_words.is_echo(raw, _custom_words):
        logger.info("Whisper read your words back with nothing said; dropped")
        return None

    # Strip common Whisper hallucinations like "thank you" from the end
    raw = _strip_hallucinations(raw, no_speech_prob=max_no_speech_prob)
    if raw:
        print(f"Raw (after filter): {_loggable(raw)}")
        logger.info(f"Raw transcription (after hallucination filter): {_loggable(raw)}")
    return raw or None


def _handle_undo(raw: str, beeps_on: bool, notify_on: bool) -> bool:
    """Check if raw text is an undo command and execute it.

    Returns True if an undo was handled (caller should return early).
    """
    detected = detect_undo_command(raw)
    if not detected:
        return False

    undo_type, count = detected
    # Deleting changes the text a voice move remembered (nav_context).
    state.nav_context = None
    label = undo_type if count == 1 else f"{count} {undo_type}s"
    logger.info(f"Undo command detected: {label}")
    print(f"Undo command: {label}")

    # 'everything' clears the entire input field via Ctrl+A + Backspace.
    # No tracked dictation required: it wipes whatever is in the textarea,
    # including text that wasn't typed by TuxTalk.
    if undo_type == 'everything':
        # In a terminal Ctrl+A jumps to the start of the line and Backspace
        # there deletes nothing, so the line is cleared the shell's way:
        # Ctrl+E to its end, Ctrl+U to delete back to the prompt (bash, zsh,
        # fish). Ctrl+A would also be screen's prefix key.
        if is_terminal_class(_query_focused_window_class()):
            cleared = _output_backend().press_keys([(("CTRL",), "E"), (("CTRL",), "U")])
        else:
            cleared = _output_backend().select_all_delete()
        if cleared:
            state.last_inserted_text = ""
            _forget_shown()
            state.continue_mid_sentence = False
            _beep(beeps_on, *READY_BEEP)
            if notify_on: _notify("TuxTalk", "Cleared input field")
        else:
            print("Could not clear input field (text injection unavailable)")
            _beep(beeps_on, *CANCEL_BEEP)
            if notify_on: _notify("TuxTalk", "Could not clear field")
        return True

    if not state.last_inserted_text:
        print("Nothing to undo (no previous dictation)")
        logger.info("Undo requested but no previous text to undo")
        _beep(beeps_on, *CANCEL_BEEP)
        if notify_on: _notify("TuxTalk", "Nothing to undo")
        return True

    # Backspacing the last dictation's length in a different window would
    # delete text there that TuxTalk never typed. Unknown windows go ahead,
    # as before.
    focused = _query_focused_window_class()
    if (focused is not None and state.last_inserted_window is not None
            and focused != state.last_inserted_window):
        logger.info(f"Undo refused: the last dictation went to {state.last_inserted_window!r}, "
                    f"not {focused!r}")
        _edit_problem("undo_other_window", beeps_on)
        return True

    # Calculate how many characters to delete (iterates `count` times)
    delete_count = calculate_undo_length(state.last_inserted_text, undo_type, count)
    logger.info(f"Undo: deleting {delete_count} characters (last text was {len(state.last_inserted_text)} chars, count={count})")
    print(f"Undoing {delete_count} characters ({label})")

    if delete_count > 0:
        # Only shrink the buffer once the deletion is confirmed; otherwise it
        # falls out of step with the document and the next undo backspaces
        # over the user's own text.
        try:
            deleted = _output_backend().backspaces(delete_count)
        except KeystrokesStoppedPartway:
            # Some of the text is gone and some isn't, by an unknown amount,
            # so the buffer no longer matches the document. Forgetting it is
            # the only safe choice: a later undo then deletes nothing.
            print("Undo stopped partway")
            logger.error("Undo stopped partway: an unknown number of backspaces landed; undo buffer cleared")
            state.last_inserted_text = ""
            _forget_shown()
            state.continue_mid_sentence = False
            _beep(beeps_on, *CANCEL_BEEP)
            if notify_on:
                _notify("TuxTalk", "Undo stopped partway, so some of the text may still be there")
            return True
        if not deleted:
            print("Undo failed — nothing was deleted")
            logger.error("Undo failed: backspaces were not delivered; buffer left unchanged")
            _beep(beeps_on, *CANCEL_BEEP)
            if notify_on:
                _notify("TuxTalk", "Undo failed — text couldn't be deleted")
            return True

        # Update last_inserted_text to reflect what remains
        _trim_shown(state.last_inserted_text, delete_count)
        if delete_count >= len(state.last_inserted_text):
            state.last_inserted_text = ""
            state.continue_mid_sentence = False
        else:
            state.last_inserted_text = state.last_inserted_text[:-delete_count]
            remaining = state.last_inserted_text.rstrip()
            if remaining and not remaining.endswith(('.', '?', '!', '\u2026')):
                state.continue_mid_sentence = True
                logger.info("Mid-sentence continuation enabled for next dictation")
            else:
                state.continue_mid_sentence = False
        _beep(beeps_on, *READY_BEEP)
        if notify_on: _notify("TuxTalk", f"Undid last {label}")
    else:
        print("Nothing to undo for this scope")
        _beep(beeps_on, *CANCEL_BEEP)
    return True


def _safe_to_lowercase_first_word(text: str) -> bool:
    """True when the leading capital is Whisper's sentence-start capital and
    nothing more, so undoing it is safe.

    Two kinds of word are capitalized for reasons that have nothing to do with
    where the sentence begins, and lowercasing them corrupts real text:

      * the pronoun "I" (and I'm / I'll / I've / I'd), which would become "i"
      * acronyms, where only the first letter would change: "NASA" -> "nASA"

    Proper nouns like "Ron" are genuinely ambiguous — Whisper capitalizes them
    both as names and as sentence starts — so they are left alone here and
    remain a known limitation rather than a guess.
    """
    first = text.split(maxsplit=1)[0] if text.split() else ""
    if not first:
        return False

    # The pronoun "I", bare or contracted.
    stripped = first.rstrip(".,;:!?")
    if stripped == "I" or stripped.startswith("I'"):
        return False

    # Acronyms: more than one capital and no lowercase letters ("NASA", "USA").
    letters = [c for c in stripped if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return False

    # One of Your Words, already spelled the user's way ("TuxTalk",
    # "New York", "TuxTalk's").
    if _custom_words:
        from .custom_words import starts_with_word
        if starts_with_word(text, _custom_words):
            return False

    return True


def _numbers_to_digits(text: str) -> str:
    """*text* with spoken numbers as digits, when that option is on."""
    if not _numbers_mode:
        return text
    from .spoken_numbers import to_digits
    return to_digits(text, _numbers_language, small_as_words=_numbers_mode == "small_as_words")


def _prepare_text(raw: str, smart_quotes: bool, auto_period: bool, auto_space: bool,
                  app_kind: str = "", ai: bool = True) -> str:
    """Apply voice commands, normalize, handle mid-sentence, add auto-period/space.

    *app_kind* ("terminal", "code" or ""): the focused app wants plain text
    (no closing period; in a terminal, no starting capital either).
    *ai* False skips the AI self-corrections: Type at pause asks the AI about
    a phrase together with the one before it instead (_correct_across_pause).

    Returns the final text string ready for injection.
    """
    if app_kind:
        auto_period = False
    # Apply custom voice commands (phrase → replacement)
    # Quoted replacements come back as placeholder tokens in `protected`
    processed, protected = _apply_custom_commands(raw)
    # Your Words, first pass: before tidying, a hyphen inside a word can only
    # have come from the engine ("Tux-Talk"), so it may join here.
    if _custom_words:
        from .custom_words import fix_spelling
        processed = fix_spelling(processed, _custom_words)

    # Normalize text (capitalization, punctuation, etc.). auto_period is passed
    # through because this pass otherwise ends every line with a full stop of
    # its own, and append_auto_punct below would never get to decide.
    text = normalize_text(
        processed if smart_quotes else processed.replace("\u201c","\"").replace("\u201d","\""),
        auto_period=auto_period,
    )
    # A lone "open quote" or "close quote" becomes a curly quote inside
    # normalize_text, so the preference is applied to its output as well.
    if not smart_quotes:
        text = text.replace("\u201c", "\"").replace("\u201d", "\"")

    # Optional cleanup, while quoted custom-command replacements are still
    # placeholders, so a literal replacement is never "cleaned". Rules first
    # ("um", "the the"), then the AI for sentences with a self-correction.
    if _remove_fillers:
        from .cleanup import clean_fillers_and_repeats
        text = clean_fillers_and_repeats(text)
    if ai and _ai_corrections and _ai_engine is not None:
        from . import ai_cleanup
        text = ai_cleanup.fix_self_corrections(text, _ai_engine)

    # Your Words, second pass: "tux talk" -> "TuxTalk" and iPhone's capitals
    # back after a sentence start made it IPhone. After snippets, so a snippet
    # whose phrase contains the split-up word still matches; while quoted
    # snippets are still placeholders, so a literal one is never touched; and
    # with no hyphen joins, since by now a hyphen means the user said "dash".
    if _custom_words:
        from .custom_words import fix_spelling
        text = fix_spelling(text, _custom_words, hyphens=False)

    # Spoken numbers as digits. Before the quoted snippets come back, so a
    # literal snippet is typed exactly as written.
    text = _numbers_to_digits(text)

    # Restore quoted (literal) custom command replacements before any further
    # processing so that auto-period/space checks see the real final text.
    text = _restore_protected(text, protected)

    # Swiss Standard German writes ss where the engines write ß. Last, so it
    # also covers snippets, Your Words and the AI's corrections.
    if _swiss_spelling:
        text = swiss_spelling(text)

    # Handle mid-sentence continuation after undo:
    # lowercase the first letter if we're continuing a sentence
    if state.continue_mid_sentence and text:
        if text[0].isupper() and _safe_to_lowercase_first_word(text):
            text = text[0].lower() + text[1:]
            logger.info("Lowercased first letter for mid-sentence continuation")
        state.continue_mid_sentence = False

    # Auto-period and auto-space. When the utterance ends with a line-break
    # command, the period lands BEFORE the break and no trailing space is
    # added, so "hello new line" leaves no orphan ". " on the next line.
    text = append_auto_punct(text, auto_period, auto_space)
    if app_kind:
        text = plain_for_app(text, app_kind)
    logger.info(f"Normalized text: {_loggable(text)}")
    return text


# wl-copy opens a window of its own for a moment to set the clipboard. It
# never receives the dictation, so it's never the window the text went to.
_CLIPBOARD_TOOL_CLASSES = frozenset({"io.github.bugaevc.wl-clipboard", "wl-clipboard"})


def _inject_text(text: str, injection_mode: str, t0: float, one_block: bool = False) -> str:
    """Insert *text* into the active app (_deliver_text), then remember the
    window it really landed in, for undo, "fix that word" and select and move.

    The focus is checked once more after typing because the report from
    before can be out of date. On KDE, clicking the desktop reports
    plasmashell; wl-copy's clipboard window then hands the focus back to the
    last real window, and the text lands there. Remembering plasmashell made
    "delete last sentence" refuse in the very window that got the text
    (Ron, 2026-10-15).
    """
    outcome = _deliver_text(text, injection_mode, t0, one_block)
    if outcome != "failed":
        try:
            landed = _query_focused_window_class()
        except Exception:
            landed = None
        if landed and landed not in _CLIPBOARD_TOOL_CLASSES:
            if landed != state.last_inserted_window:
                logger.info(f"The text landed in {landed!r}, not {state.last_inserted_window!r}")
            state.last_inserted_window = landed
    return outcome


def _deliver_text(text: str, injection_mode: str, t0: float, one_block: bool = False) -> str:
    """Determine the best injection method and insert text into the active app.

    Pastes or types as _determine_injection_method decides, typing whatever a
    failed paste could not deliver.
    t0 is the timestamp when stop_recording began (for timing logs).

    Returns how it went: "ok" (all of it arrived), "partial" (some chunks of a
    multi-part dictation arrived, then it stopped) or "failed" (none did).
    The caller tells the user about anything but "ok", so a dictation that
    never arrived doesn't fail silently.

    *one_block* (voice editing) pastes text with line breaks in one go, real
    newlines and all. Line by line with Enter in between, an editor continues
    each "- " list line and the dashes pile up. Typed, the breaks still go in
    as Shift+Enter, since a plain Enter would send a chat message; that holds
    for every typed line break, not only voice editing's.
    """
    # Which window this text goes into, so select and move by voice only
    # trusts "what I just said" in that same window.
    try:
        state.last_inserted_window = _query_focused_window_class()
    except Exception:
        state.last_inserted_window = None
    # Determine injection method (auto mode does smart detection)
    detection_start = time.time()
    actual_mode, reason = _determine_injection_method(injection_mode)
    detection_time = time.time() - detection_start
    logger.info(f"TIMING: Injection method detection took {detection_time:.3f}s")
    if detection_time > 0.1:
        logger.warning(f"Injection method detection slow: {detection_time:.2f}s")

    injection_start = time.time()

    # Electron-paste-broken override: if the focused window is an Electron app
    # where synthetic Ctrl+V silently fails on Wayland, skip clipboard-paste and
    # use fast direct typing instead. Only kicks in for auto/paste — if the user
    # explicitly chose "type", we already use Type mode.
    if injection_mode != "type":
        focused_class = _query_focused_window_class()
        logger.info(f"Electron-broken check: focused_class={focused_class!r}")
        if focused_class in _ELECTRON_PASTE_BROKEN_CLASSES and not os.environ.get("FLATPAK_ID"):
            logger.info(f"Electron-paste-broken override: class={focused_class!r}, using fast-type")
            print(f"Electron app ({focused_class}): using fast-type fallback")

            # Split on §SHIFT_ENTER§ (line-break voice command) and \n so
            # we send actual Shift+Enter keystrokes between text chunks
            # instead of typing the marker literally.
            # Tab characters (from the "tab" voice command) are converted to
            # 4 spaces here: a real Tab keypress moves focus from Claude Desktop's
            # chat textarea to the Send button, and the next typed space then
            # activates it — submitting the message mid-dictation.
            marker = "\xa7SHIFT_ENTER\xa7"
            unified = text.replace("\n", marker) if "\n" in text else text
            unified = unified.replace("\t", "    ")
            success = True
            delivered_parts = 0  # chunks confirmed in the document
            breaks_sent = 0      # line breaks confirmed after those chunks
            if marker in unified:
                parts = unified.split(marker)
                for i, part in enumerate(parts):
                    if part and not _type_text_fast(part):
                        success = False
                        break
                    delivered_parts = i + 1
                    if i < len(parts) - 1:
                        time.sleep(0.03)
                        if not _send_shift_enter():
                            success = False
                            break
                        breaks_sent += 1
                        time.sleep(0.03)
            else:
                success = _type_text_fast(unified)
                delivered_parts = 1 if success else 0

            if success:
                injection_time = time.time() - injection_start
                logger.info(f"TIMING: Fast-type injection completed in {injection_time:.2f}s")
                logger.info(f"Text injected via fast-type: {len(text)} chars in {injection_time:.2f}s")
                # Track injected text for undo. Store the typed form: tabs
                # already replaced with 4 spaces, and each line-break marker
                # as one '\n' (one Shift+Enter keystroke = one backspace),
                # so backspace counts match what was actually sent.
                state.last_inserted_text = unified.replace(marker, "\n")
                logger.debug(f"Stored last inserted text for undo: {len(state.last_inserted_text)} chars (fast-type)")
                return "ok"

            # Partial delivery: chunks already in the document must not be sent
            # again by the fallback below, or the user gets a duplicate copy.
            if delivered_parts:
                landed = marker.join(unified.split(marker)[:delivered_parts])
                if breaks_sent == delivered_parts:
                    landed += marker   # the break after the last chunk landed too
                state.last_inserted_text = landed.replace(marker, "\n")
                logger.warning(
                    f"Fast-type stopped after {delivered_parts} chunk(s) — "
                    f"not re-injecting, undo buffer holds only what landed"
                )
                return "partial"
            # Nothing landed, so the normal injection path can safely try again.
            logger.warning("Fast-type failed, falling through to normal injection")
        elif focused_class in _ELECTRON_PASTE_BROKEN_CLASSES:
            # Flatpak: the libei backend types real key events, which are immune
            # to the broken synthetic Ctrl+V that fast-type exists to work around
            # — and ydotool isn't in the sandbox anyway. So there's nothing to
            # fast-type around: keep only the Claude-Desktop tab safety (a real
            # Tab jumps focus to the Send button) and fall through to libei typing.
            logger.info(f"Flatpak + Electron app ({focused_class!r}): libei typing, "
                        f"tab→spaces (skipping ydotool fast-type)")
            text = text.replace("\t", "    ")

    use_paste = (actual_mode == "paste")
    logger.info(f"Injection mode: configured={injection_mode!r}, actual={actual_mode!r}, reason={reason}")
    if injection_mode == "auto":
        print(f"Auto mode: {reason}")

    # Tracks whether text actually reached the focused app. Only a confirmed
    # injection updates the undo buffer; otherwise a later "undo last word"
    # would backspace characters the user's document never received.
    inject_ok = True

    # --- Smart hybrid paste (text with line-break markers) ---
    if use_paste and not one_block and ("\xa7SHIFT_ENTER\xa7" in text or "\n" in text):
        marker = "\xa7SHIFT_ENTER\xa7"
        # A terminal honours a newline inside a paste, but treats Shift+Enter
        # like Enter, so it gets one chunk carrying real newlines and no
        # keystrokes at all.
        parts = plan_line_breaks(text, is_terminal_class(_query_focused_window_class()))
        logger.info(f"Smart hybrid mode: {len(parts)} part(s)")

        # Track where delivery stopped, so the fallback types only the rest:
        # re-injecting the whole text would duplicate the chunks that already
        # landed, which undo can't clean up.
        resume_at = None
        needs_break = False
        for i, part in enumerate(parts):
            if part:
                logger.info(f"Pasting part {i+1}/{len(parts)}: {len(part)} chars")
                if not _paste_text(part):
                    logger.warning(f"Paste failed on part {i+1}; will type the remainder")
                    resume_at = i
                    break
                time.sleep(0.08)
            if i < len(parts) - 1:
                logger.info(f"Sending Shift+Enter after part {i+1}")
                if not _send_shift_enter():
                    # The chunk landed but its line break didn't: resume from
                    # the next chunk and supply the missing break.
                    logger.warning(f"Line break failed after part {i+1}; will type the remainder")
                    resume_at, needs_break = i + 1, True
                    break
                time.sleep(0.05)

        if resume_at is None:
            print(f"Inject (smart paste) {len(parts)} chunks, {len(text)} total chars")
            logger.info(f"Smart hybrid paste completed: {len(parts)} chunks")
        else:
            remainder = (marker if needs_break else "") + marker.join(parts[resume_at:])
            # A terminal's single chunk carries real newlines for bracketed
            # paste. Typed, a newline is a plain Enter and Shift+Enter is no
            # safer in a terminal, so _typed_breaks_for turns them into spaces
            # there; other apps get Shift+Enter.
            remainder = _typed_breaks_for(remainder.replace("\n", marker),
                                          _query_focused_window_class())
            print(f"Inject (type) remainder len={len(remainder)} [paste failed partway]")
            if _output_backend().type_text(remainder):
                inject_ok = True  # delivered chunks + typed remainder == full text
            else:
                # The chunks before the failure reached the document, and so did
                # the line break after the last of them unless that break is what
                # failed. Recording only those makes undo delete too little rather
                # than too much if part of the remainder was typed as well.
                delivered = marker.join(parts[:resume_at])
                if resume_at and not needs_break:
                    delivered += marker
                state.last_inserted_text = delivered.replace(marker, "\n")
                logger.warning(
                    f"Injection stopped after {resume_at}/{len(parts)} chunks \u2014 "
                    f"undo buffer holds only what landed"
                )
                return "partial" if resume_at else "failed"

    # --- Simple paste (no markers) ---
    elif use_paste and _paste_text(text):
        injection_time = time.time() - injection_start
        logger.info(f"TIMING: Paste injection completed in {injection_time:.2f}s")
        logger.info(f"Text injected via paste: {len(text)} chars in {injection_time:.2f}s")
        if injection_time > 1.0:
            logger.warning(f"Paste injection slow: {injection_time:.2f}s")

    # --- Typing fallback ---
    else:
        # Typed, a real newline is a plain Enter, which sends a chat message or
        # runs a terminal's half-typed command. A snippet's line breaks are real
        # newlines, so they go in as Shift+Enter like a spoken "new line".
        text = _typed_breaks_for(text.replace("\n", "\xa7SHIFT_ENTER\xa7"),
                                 _query_focused_window_class())
        backend = _output_backend()
        # The Flatpak's keyboard path types "…" as "..." and "café" as "cafe";
        # recording that form below keeps undo's backspace count right.
        text = backend.typeable(text)
        inject_ok = backend.type_text(text)
        injection_time = time.time() - injection_start
        logger.info(f"TIMING: Typing injection completed in {injection_time:.2f}s")
        logger.info(f"Text injected via typing: {len(text)} chars in {injection_time:.2f}s")
        if injection_time > 1.0:
            logger.warning(f"Typing injection slow: {injection_time:.2f}s")

    # Track injected text for undo — but ONLY when injection actually
    # succeeded. Line-break markers are stored as '\n' (one keystroke on
    # screen = one character in the buffer) so undo backspace counts stay in
    # sync with what was typed. A failed typing run may have typed part of
    # the text before it stopped, so the buffer matches neither this
    # dictation nor the last one: empty it, and undo then deletes nothing.
    if inject_ok:
        state.last_inserted_text = text.replace("\xa7SHIFT_ENTER\xa7", "\n")
        logger.debug(f"Stored last inserted text for undo: {len(state.last_inserted_text)} chars")
    else:
        state.last_inserted_text = ""
        logger.warning("Injection failed — undo buffer cleared")
    return "ok" if inject_ok else "failed"


def _toggle_pressed_while_recording(beeps_on: bool) -> bool:
    """A toggle-key press arrived during a recording. Returns True if it was
    the second tap of a double-tap and switched the recording to hands-free
    (the caller must then NOT stop it); False means a normal stop.

    A single tap keeps the toggle a plain on/off switch, however long the
    speaker pauses. Only a double-tap, with the option on, arms auto-stop; a
    double-tap would otherwise just start and cancel a too-short recording.
    """
    started = state.toggle_started_at
    if (_auto_stop_seconds <= 0 or started is None or state.silence is not None
            or time.time() - started > DOUBLE_TAP_S):
        return False
    from .silence import SilenceDetector
    state.silence = SilenceDetector(
        _auto_stop_seconds, getattr(state, "recording_samplerate", SAMPLE_RATE))
    logger.info("Double-tap: this recording will stop by itself when you go quiet")
    print("Hands-free: stops when you stop talking")
    _beep(beeps_on, *HANDS_FREE_BEEP)
    return True


def _trim_auto_stop_tail(frames):
    """After an auto-stop, drop the silence the detector measured at the end.

    Only whole trailing blocks are removed, and only up to what was measured
    as silent (less a small margin kept for the last word), so speech is never
    touched. Runs after the stream stops, so no block is still being added.
    """
    detector, state.silence = state.silence, None
    _drop_tail_samples(frames, detector.tail_to_trim() if detector is not None else 0)


def _drop_speechless_rest(frames):
    """The audio after the last cut, or none when the pause detector heard no
    speech in it: room noise longer than the tap check would otherwise reach
    the engine, which invents a word for it ("Yeah.")."""
    if state.last_chunk_speech == 0.0:
        logger.info("No speech after the last chunk; the rest isn't transcribed")
        return []
    return frames


def _drop_tail_samples(frames, to_trim):
    """Remove whole trailing blocks (int16 mono) holding at most *to_trim*
    samples. A block is never split, so speech is never touched."""
    while frames and to_trim > 0:
        block_samples = len(frames[-1]) // 2          # int16 mono
        if block_samples > to_trim:
            break
        frames.pop()
        to_trim -= block_samples


def _finish_capture(beeps_on: bool, notify_on: bool):
    """End a recording quickly and hand back its audio (the fast half of stop).

    Validates the hold time, stops the stream, hides the indicator, and SNAPSHOTS
    the captured frames. Returns (frames, rec_sr) or None if the recording was
    cancelled or too short. start_recording() reassigns state.frames to a fresh
    list, so the returned reference stays valid even if a new recording begins
    immediately, which is what lets Backend B run the slow transcription/typing
    on a worker thread without dropping or mixing audio."""
    if state.silence is not None and state.silence.nothing_said:
        # Hands-free with nothing said: drop it. Transcribing room noise can
        # type a stray "Thank you." (see silence.is_speechless_tap).
        state.silence = None
        cancel_recording(beeps_on, notify_on, "Nothing heard, so nothing was typed")
        return None
    held_ms = int((time.time() - (state.press_t0 or time.time())) * 1000)
    if held_ms < MIN_HOLD_MS:
        cancel_recording(beeps_on, notify_on, f"Cancelled (held {held_ms} ms)")
        return None
    with _chunk_lock:
        # No more cuts: from here on the audio is the last chunk.
        chunker, state.chunker = state.chunker, None
        state.last_chunk_speech = chunker.speech_time if chunker is not None else None
        frames = state.frames  # snapshot the reference before it can be reassigned
    if chunker is not None and not state.chunks_submitted:
        logger.info(f"Chunked dictation: no pause found ({chunker.describe()})")
    rec_sr = getattr(state, 'recording_samplerate', SAMPLE_RATE)
    state.is_recording = False
    _stop_stream_safely()
    _resume_media_players()
    _notify_tray_recording_state(False)  # the tray icon shows recording stopped
    _trim_auto_stop_tail(frames)
    if state.was_cancelled:
        return None
    # Hide recording indicator before text injection
    if recording_indicator:
        recording_indicator.hide_indicator()
    return frames, rec_sr


# What to tell the user when a dictation didn't reach the window. The text is
# always in Recent Dictations by then (it's saved before injection starts).
_UNDELIVERED_NOTICES = {
    "failed": "Couldn't type that dictation into the window. It's saved in Recent "
              "Dictations in the TuxTalk menu: click it to copy, then paste with Ctrl+V.",
    "partial": "Only part of that dictation reached the window. The whole text is saved "
               "in Recent Dictations in the TuxTalk menu: click it to copy, then paste "
               "with Ctrl+V.",
}


def _report_undelivered(outcome: str, beeps_on: bool):
    """Tell the user a dictation didn't (fully) reach the window.

    Always shown, whatever the notifications setting, like a missing microphone:
    otherwise words that are transcribed but never arrive look like a computer
    too slow to dictate. The low cancel beep follows the "done" beep, which has already played, so a
    desktop without notifications still hears that something went wrong.
    """
    message = _UNDELIVERED_NOTICES[outcome]
    if outcome == "failed":
        # When the typing setup is the reason, say what fixes it.
        try:
            from . import uinput_helper
            reason = uinput_helper.typing_blocked_reason()
            if reason == uinput_helper.TYPING_HELPER_STOPPED:
                uinput_helper.ensure_ydotoold_running()
            if reason:
                message = (f"{reason} The dictation is saved in Recent Dictations "
                           "in the TuxTalk menu: click it to copy, then paste with Ctrl+V.")
        except Exception as e:
            logger.debug(f"Couldn't work out why typing failed: {e}")
    if _paste_last_hotkey:
        message += f" Or press {_paste_last_hotkey} to type it again where your cursor is."
    print(message)
    logger.warning(f"Dictation not delivered ({outcome}); told the user")
    _beep(beeps_on, *CANCEL_BEEP)
    _notify("TuxTalk", message)


# Silence put between two chunks when the engine hears them together, so the
# pause is still there to hear (the cut kept only 0.3 s of it).
JOINT_GAP_S = 0.5
# Seconds heard on each side of the pause for that: enough words for the
# engine to judge the seam, so a long phrase isn't transcribed a second time
# whole (a 45 s phrase took a second 45 s pass).
CONTEXT_S = 6.0
_SENTENCE_ENDS = (".", "?", "!", "…")


def _context_samples() -> int:
    return int(CONTEXT_S * SAMPLE_RATE)


def _remember_said(text: str, chunked: bool, takes_back: str = "") -> None:
    """Put *text* in Recent Dictations, one entry per recording: a phrase of
    a recording typed at pauses (*chunked*) adds to its recording's entry.
    *takes_back* is the end of that entry a correction across a pause
    replaced ("Meet at three. " for "Meet at four")."""
    from . import history
    text = text.replace(history._LINE_BREAK_MARKER, "\n")
    old = state.said_text if chunked and state.said_serial == state.recording_serial else ""
    base = old[:len(old) - len(takes_back)] if takes_back and old.endswith(takes_back) else old
    state.said_serial, state.said_text = state.recording_serial, base + text
    if old:
        history.add_entry(state.said_text, replaces=old)
    else:
        history.add_entry(state.said_text)


def _remember_shown(piece: str, chunked: bool, ok: bool) -> None:
    """*piece* was just typed (*ok*) or tried: add it to this recording's
    screen copy. The copy is only trusted while every piece landed, in one
    window, one after the other."""
    if not (chunked and state.shown_serial == state.recording_serial):
        state.shown_serial, state.shown_text = state.recording_serial, ""
        state.shown_window = state.last_inserted_window
    if state.shown_text is None:
        return
    if ok and state.last_inserted_window == state.shown_window:
        state.shown_text += piece
    else:
        state.shown_text = None


def _trim_shown(tail: str, count: int) -> None:
    """*count* characters were backspaced off *tail*, the newest typing."""
    shown = state.shown_text
    if shown is not None:
        state.shown_text = shown[:len(shown) - count] if tail and shown.endswith(tail) else None


def _forget_shown() -> None:
    """The cursor moved or the text changed: the screen copy can't be trusted."""
    state.shown_text = None


def _corrects_across_pause(raw: str, chunk_role) -> bool:
    """True when this phrase corrects the one typed before the pause ("no
    wait, four" after "meet at three"), so the AI should hear both together.
    Needs AI self-corrections on and a phrase of this recording typed."""
    if chunk_role is None or not state.chunk_typed:
        return False
    if not (_ai_corrections and _ai_engine is not None and state.last_inserted_text.strip()):
        return False
    if state.held_cue:
        return True
    from . import ai_cleanup
    return ai_cleanup.opens_with_correction(state.last_inserted_text, raw)


def _erase_typed(text: str) -> bool:
    """Backspace over *text*, just typed by the phrase before the pause. False
    when it can't be done safely; then nothing more is erased."""
    focused = _query_focused_window_class()
    if (focused is not None and state.last_inserted_window is not None
            and focused != state.last_inserted_window):
        logger.info("Correction across a pause skipped: the phrase is in another window")
        return False
    try:
        deleted = _output_backend().backspaces(len(text))
    except KeystrokesStoppedPartway:
        logger.error("Correction across a pause stopped partway; typing the phrase as said")
        state.last_inserted_text = ""
        _forget_shown()
        _notify("TuxTalk", "A correction stopped partway, so check the text before it")
        return False
    if deleted:
        _trim_shown(text, len(text))
        state.last_inserted_text = ""
    return bool(deleted)


def _correct_across_pause(text: str):
    """(text to type, text taken back) for a phrase that corrects the one
    before the pause.

    The AI hears both phrases as one ("Meet at three. No wait, four."). When
    it fixes the correction, the earlier phrase is backspaced and the fixed
    whole is typed in its place ("Meet at four."); otherwise the phrase is
    typed as said, a cue held back from the phrase before included, and
    nothing is taken back."""
    from . import ai_cleanup
    prev = state.last_inserted_text
    cue, state.held_cue = state.held_cue, ""
    if cue and cue[-1] not in ".?!,;:":
        cue += ","
    if cue.endswith(",") and text[:1].isupper() and _safe_to_lowercase_first_word(text):
        text = text[0].lower() + text[1:]      # "No wait, four", not "Four"
    said = f"{cue} {text}" if cue else text
    joined = prev + ("" if prev[-1:].isspace() else " ") + said
    fixed = ai_cleanup.fix_self_corrections(joined, _ai_engine)
    if fixed == joined or not fixed.strip():
        return said, ""
    if not _erase_typed(prev):
        return said, ""
    logger.info("AI cleanup fixed a self-correction across a pause")
    return fixed, prev


def _last_word(text: str) -> str:
    words = re.findall(r"\w+", text.casefold())
    return words[-1] if words else ""


def _hold_back_boundary(text: str, raw: str):
    """Split a middle chunk's text into what to type now and the final mark to
    hold back: (text, mark), or (text, None) when nothing is held back.

    The engine heard the chunk alone, so its final period or question mark is
    a guess; the next chunk decides it (_resolve_boundary). The trailing space
    waits too, so whatever comes next lands right after the last word."""
    core = text.rstrip(" ")
    if not core:
        return text, None
    m = re.search(r"[.?!…]+$", core)
    mark = m.group() if m else ""
    if mark:
        core = core[:m.start()].rstrip()
        # A mark the speaker dictated ("Punkt") is theirs, not the engine's
        # guess: the word before it then isn't the raw text's last word.
        if not core or _last_word(core) != _last_word(raw):
            return text, None
    elif not core[-1].isalnum():
        return text, None   # ends in a line break, a quote, a bracket...
    return core, mark


def _type_boundary(pending: dict, tail: str, injection_mode: str) -> None:
    """Type what goes between a held-back chunk and what follows it."""
    if not tail:
        return
    _remember_said(tail, chunked=True)
    ok = _inject_text(tail, injection_mode, time.time()) == "ok"
    _remember_shown(tail, chunked=True, ok=ok)
    if ok:
        # Undo and "delete last sentence" then see the whole chunk.
        state.last_inserted_text = pending["typed"] + tail


def _resolve_boundary(audio_f32, language, injection_mode) -> None:
    """The next chunk has arrived: decide the mark held back after the last
    one, from the engine hearing both chunks together, and type it."""
    pending, state.pending_boundary = state.pending_boundary, None
    mark = pending["mark"]
    try:
        from .chunking import boundary_mark
        gap = np.zeros(int(JOINT_GAP_S * SAMPLE_RATE), dtype=np.float32)
        joint = _transcribe_audio(np.concatenate(
            [pending["audio"], gap, audio_f32[:_context_samples()]]), language)
        found = boundary_mark(pending["raw"], joint or "")
        logger.info(f"Chunk boundary: {pending['mark']!r} alone, {found!r} in context")
        if found is not None:
            mark = found
    except Exception as e:
        logger.warning(f"Couldn't hear the chunk boundary in context: {e}")
    _type_boundary(pending, (mark + " ") if mark else " ", injection_mode)
    # Lowercase on after a comma or no mark; a sentence end starts afresh.
    state.continue_mid_sentence = mark not in _SENTENCE_ENDS


def _flush_boundary(auto_period: bool, auto_space: bool, injection_mode: str) -> None:
    """The dictation ended in a pause: type the mark held back after its last
    chunk, or the auto-period when the engine wrote none."""
    pending, state.pending_boundary = state.pending_boundary, None
    if pending is None:
        return
    focused = _query_focused_window_class()
    if (focused is not None and state.last_inserted_window is not None
            and focused != state.last_inserted_window):
        return  # the end of this sentence isn't in the window that has focus
    kind = app_kind(focused) if _adapt_to_app else ""
    mark = pending["mark"] or ("." if auto_period and not kind else "")
    _type_boundary(pending, mark + (" " if auto_space and not kind else ""), injection_mode)


def _transcribe_and_inject(frames, rec_sr, beeps_on, smart_quotes, notify_on,
                           language=None, auto_space=True, auto_period=True,
                           injection_mode="type", chunk_role=None):
    """The slow half of stopping: transcribe the captured audio and inject the
    text. It touches no GTK (the indicator was already hidden in _finish_capture)
    and uses only CPU + audio + D-Bus + the output backend, so it is safe on a
    worker thread, which Backend B uses so the hotkey loop stays live.

    *chunk_role* is set for chunked dictation (chunking.py): "middle" for a
    chunk cut at a pause while recording goes on (no beep, no notice, silence
    dropped quietly), "last" for the rest after the stop (the usual READY beep,
    even when it holds no speech, since earlier chunks were typed)."""
    print("Recording stopped. Transcribing…")
    t0 = time.time()
    logger.info("Recording stopped, starting transcription")

    def _nothing_in_chunk(what: str) -> bool:
        """For a chunk, handle an empty or silent one; False for a whole dictation."""
        if chunk_role is None:
            return False
        logger.info(f"Chunk ({chunk_role}) {what}; nothing typed for it")
        if chunk_role == "last":
            if state.held_cue:
                # "No wait." was the last thing said: type it as it was said.
                cue, state.held_cue = state.held_cue, ""
                if auto_period and cue[-1] not in ".?!":
                    cue += "."
                _inject_text(cue + (" " if auto_space else ""), injection_mode, time.time())
            _flush_boundary(auto_period, auto_space, injection_mode)
            if not _chunk_context_marks:
                state.continue_mid_sentence = False   # it belongs to this recording
            _beep(beeps_on, *READY_BEEP)
        return True

    try:
        # Convert captured bytes → float32 mono PCM in [-1, 1]
        pcm_int16 = np.frombuffer(b''.join(frames), dtype=np.int16)
        if pcm_int16.size == 0:
            if _nothing_in_chunk("had no audio"):
                return
            print("(No audio captured)")
            return
        audio_f32 = pcm_int16.astype(np.float32) / 32768.0
        if rec_sr != SAMPLE_RATE:
            audio_f32 = _resample_audio(audio_f32, rec_sr, SAMPLE_RATE)

        # A quick tap of the key with nothing said: speech engines can invent
        # a word ("Thank you.") in a fraction of a second of room noise.
        from .silence import is_speechless_tap
        # Both stop with the cancel beep, as a too-short F8 tap and the
        # hands-free "nothing said" stop do: silence there read as broken.
        if is_speechless_tap(audio_f32, SAMPLE_RATE):
            if _nothing_in_chunk("had no speech"):
                return
            print("(Short tap with no speech; nothing typed)")
            logger.info(f"Skipped a {len(audio_f32) / SAMPLE_RATE:.2f}s recording with no speech in it")
            _beep(beeps_on, *CANCEL_BEEP)
            return

        # Stage 1: Transcribe audio → raw text
        raw = _transcribe_audio(audio_f32, language)
        logger.info(f"TIMING: Transcription pipeline took {time.time() - t0:.2f}s")
        if chunk_role is not None:
            logger.info(f"Chunk ({chunk_role}): {len(audio_f32) / SAMPLE_RATE:.1f}s of audio, "
                        f"raw: {_loggable(raw)}")
        if not raw:
            if _nothing_in_chunk("was not recognised"):
                return
            print("(No speech recognized)")
            logger.info("Nothing recognized, so nothing was typed")
            _beep(beeps_on, *CANCEL_BEEP)
            return

        # Chunked dictation: the chunk before this one held back its final
        # mark; decide it now, before a command acts on what was typed.
        if chunk_role is not None and state.pending_boundary is not None:
            _resolve_boundary(audio_f32, language, injection_mode)

        # Stage 2: Check for undo commands ("undo that", "undo word", etc.)
        if _handle_undo(raw, beeps_on, notify_on):
            return

        # Select and move by voice ("select the last sentence"), when the
        # whole dictation is one of its commands (TuxTalk Labs).
        if _handle_navigation(raw, beeps_on):
            return

        # "fix that word": Fix a Word on the dictation before this one.
        if _handle_fix_word(raw, beeps_on):
            return

        # Stage 3: Normalize text (voice commands, punctuation, spacing),
        # plainer for a terminal or code editor ("Plain text in terminals and
        # code editors").
        kind = app_kind(_query_focused_window_class()) if _adapt_to_app else ""
        # A pause is not a sentence end, so a middle chunk gets no auto-period
        # of its own; the last chunk (or _flush_boundary) ends the sentence.
        chunk_period = auto_period and chunk_role != "middle"
        # A correction said after a pause ("meet at three" ... "no wait,
        # four") goes to the AI together with the phrase it corrects.
        across = _corrects_across_pause(raw, chunk_role)
        text = _prepare_text(raw, smart_quotes, chunk_period, auto_space, app_kind=kind,
                             ai=not across)
        taken_back = ""
        if across:
            from . import ai_cleanup
            if chunk_role == "middle" and ai_cleanup.is_bare_cue(text):
                # Only "No wait.": the corrected value is in the next phrase.
                state.held_cue = text.strip()
                logger.info("Chunk is only a correction cue; waiting for the next phrase")
                return
            text, taken_back = _correct_across_pause(text)
        held_mark = None
        if chunk_role == "middle" and _chunk_context_marks:
            text, held_mark = _hold_back_boundary(text, raw)

        # Beep to confirm transcription is done
        if chunk_role != "middle":
            _beep(beeps_on, *READY_BEEP)
            _ellipsis = "…"
            if notify_on: _notify("TuxTalk", f"Transcribed: {text[:80]}{_ellipsis if len(text)>80 else ''}")

        # Stage 4: Inject text into the active application. It goes into the
        # Recent Dictations list first, so it can be recovered even if the
        # injection lands in the wrong window or fails outright.
        if text:
            chunked = chunk_role is not None
            _remember_said(text, chunked, taken_back)
            try:
                outcome = _inject_text(text, injection_mode, t0)
            except Exception as e:
                # The transcription worked; only getting it into the window
                # didn't, so this is reported as undelivered, not as the
                # "Transcription error" below.
                logger.error(f"Injection error: {e}", exc_info=True)
                outcome = "failed"
            _remember_shown(state.last_inserted_text, chunked, outcome == "ok")
            if outcome in _UNDELIVERED_NOTICES:
                _report_undelivered(outcome, beeps_on)
            if chunk_role == "middle" and not _chunk_context_marks and outcome == "ok":
                # Without the context pass the engine's own mark decides: after
                # a comma or none, the next chunk carries on lowercase.
                state.continue_mid_sentence = not text.rstrip().endswith(_SENTENCE_ENDS)
            if chunk_role is not None and outcome == "ok":
                state.chunk_typed = True
            if held_mark is not None and outcome == "ok":
                # Only the end of the phrase is kept: the engine hears it with
                # the start of the next one to decide the mark between them.
                state.pending_boundary = {"mark": held_mark,
                                          "audio": audio_f32[-_context_samples():],
                                          "raw": raw, "typed": state.last_inserted_text}
            if _usage_stats:
                # After typing, so saving the counts never delays the text.
                # Counts only (words, seconds dictating); see stats.py.
                from .stats import count_words, record
                record(count_words(text), len(audio_f32) / SAMPLE_RATE)

    except Exception as e:
        logger.error(f"Transcription error: {e}", exc_info=True)
        print(f"Transcription error: {e}")
        # The cancel beep and a notice whatever the notifications setting,
        # like _report_undelivered: nothing was typed, and the "done" beep
        # would sound like it worked.
        _beep(beeps_on, *CANCEL_BEEP)
        _ellipsis = "…"
        _notify("TuxTalk", f"Transcription failed: {str(e)[:60]}{_ellipsis if len(str(e))>60 else ''}")


def stop_recording(
    beeps_on: bool,
    smart_quotes: bool,
    notify_on: bool,
    language: str | None = None,
    auto_space: bool = True,
    auto_period: bool = True,
    injection_mode: str = "type",
):
    """Stop recording, transcribe audio, and inject text — synchronously.

    Backend A (evdev): the whole pipeline runs on the caller's thread. Backend B splits the two halves (see _service_tick) so the
    slow transcription/typing runs off the hotkey loop.
    """
    edit_mode, state.edit_mode = state.edit_mode, False
    cap = _finish_capture(beeps_on, notify_on)
    if cap is None:
        return
    frames, rec_sr = cap
    if edit_mode:
        _transcribe_and_edit(frames, rec_sr, beeps_on, language, injection_mode)
        return
    if state.chunks_submitted:
        # Chunked dictation: the rest goes in after the chunks already queued,
        # and stopping waits for all of them, as it waits for one
        # transcription when nothing was chunked.
        params = (beeps_on, smart_quotes, notify_on, language, auto_space,
                  auto_period, injection_mode)
        frames = _drop_speechless_rest(frames)
        worker = _chunk_worker()
        worker.submit(lambda: _transcribe_and_inject(frames, rec_sr, *params,
                                                     chunk_role="last"))
        if not worker.drain(CHUNK_DRAIN_TIMEOUT_S):
            logger.warning("Chunked dictation: chunks still typing after "
                           f"{CHUNK_DRAIN_TIMEOUT_S:.0f}s; not waiting longer")
        return
    _transcribe_and_inject(frames, rec_sr, beeps_on, smart_quotes, notify_on,
                           language, auto_space, auto_period, injection_mode)


# --- Voice editing ---

# How long after typing the rewrite the user's clipboard is put back: after
# ClipboardKeeper's own restore (0.5 s) when "Restore clipboard" is on, and
# after the app has read a pasted rewrite.
EDIT_RESTORE_DELAY_S = 1.0

_EDIT_NOTICES = {
    "not_installed": "Voice editing needs its AI. Turn it on in Preferences, Advanced.",
    "not_ready": "Voice editing's AI is still starting. Try again in a few seconds.",
    "no_selection": "Select the text to change first, then hold Shift with your dictation "
                    "key and say how to change it.",
    "too_long": "That's too much text to edit at once. Select a smaller part.",
    "no_instruction": "I didn't catch what to change. Hold Shift with your dictation key "
                      "and say it, like \u201cmake this more formal\u201d.",
    "no_answer": "The AI didn't come up with a change. Try saying it another way.",
    "terminal": "Voice editing doesn't work in a terminal: there's no selection there to "
                "replace.",
    "needs_wayland": "Voice editing works only in a Wayland session for now. This desktop "
                     "is running X11.",
    "no_clipboard": "TuxTalk couldn't use the clipboard to read the selection, so nothing "
                    "was changed.",
    "window_changed": "You switched windows while the AI was working, so the rewrite "
                      "wasn't typed. It's in Recent Dictations in the TuxTalk menu.",
    "focus_moved": "You switched windows before the edit was done, so nothing was changed.",
    "cant_check_window": "TuxTalk couldn't tell which window has the focus, so it didn't "
                         "press Delete. Press it yourself to delete the selection.",
    "no_shortcuts": "Bold, italics and underline work in LibreOffice and other word "
                    "processors. This app has no formatting TuxTalk can use.",
    "cant_format": "Voice editing changes the words. It can't change the font, size or color.",
    "shortcut_failed": "TuxTalk couldn't press the keys for that. Try doing it from the "
                       "keyboard yourself.",
    "nothing_said": "TuxTalk can only select what you just dictated, right after you "
                    "dictate it. For other text, say \u201cselect the previous 3 words\u201d "
                    "or \u201cselect the line\u201d, or use the mouse.",
    "nav_terminal": "Select and move by voice doesn't work in a terminal, where those "
                    "keys mean other things.",
    "undo_other_window": "TuxTalk can only delete what you just dictated, right after "
                         "you dictate it. Use Ctrl+Z in this app instead.",
}


# The same problems in a few words, for the label floating over the window
# (screen_message): glanced at, gone in seconds. The notification keeps the
# full sentence, in the notification history.
_EDIT_LABELS = {
    "not_installed": "Turn on voice editing's AI in Preferences",
    "not_ready": "The AI is still starting",
    "no_selection": "Select some text first",
    "too_long": "Too much text, select less",
    "no_instruction": "Didn't catch what to change",
    "no_answer": "No change, try other words",
    "terminal": "Can't edit in a terminal",
    "needs_wayland": "Voice editing needs Wayland",
    "no_clipboard": "Couldn't read the selection",
    "window_changed": "Window changed, see Recent Dictations",
    "focus_moved": "Window changed, nothing done",
    "cant_check_window": "Couldn't check the window, nothing deleted",
    "no_shortcuts": "No bold or italics in this app",
    "cant_format": "Can't change font, size or color",
    "shortcut_failed": "Couldn't press the keys",
    "nothing_said": "Works only right after dictating",
    "nav_terminal": "Can't select or move in a terminal",
    "undo_other_window": "Works only right after dictating",
}


def _edit_problem(kind: str, beeps_on: bool) -> None:
    """Tell the user a voice edit didn't happen, and why. Always shown, like
    _report_undelivered: otherwise nothing at all would seem to happen.

    A problem that is really a rule ("select works right after you dictate")
    opens a small window explaining it, every time, until "Don't explain
    this again" is ticked for it (explain.py); then the short floating label
    alone. The notification is for real errors only: a whole
    explanation in it is gone before it can be read."""
    message = _EDIT_NOTICES[kind]
    logger.info(f"Voice edit not done: {kind}")
    print(message)
    _beep(beeps_on, *CANCEL_BEEP)
    from . import explain
    # "Don't explain this again" ticked, or a window already open: the
    # label is enough. Not opened (no window could start): the notification
    # as before.
    explained = False
    if explain.should_explain(kind) and not explain.window_open():
        if explain.open_window(kind):
            return
    else:
        explained = kind in explain.EXPLANATIONS
    # Where the user is looking: over the active window (screen_message.py).
    # The notification stays as a backup, in the notification history.
    try:
        from . import screen_message
        screen_message.show_over_active_window(_EDIT_LABELS[kind])
    except Exception as e:
        logger.debug(f"Could not float the message over the window: {e}")
    if not explained:
        _notify("TuxTalk", message)


def _send_copy_keystroke() -> bool:
    """Ctrl+C. Terminals never get here (_transcribe_and_edit turns them away:
    Ctrl+C would stop the running program). True if it was sent."""
    # KEY_LEFTCTRL=29, KEY_C=46
    return _ydotool_key(["29:1", "46:1", "46:0", "29:0"], what="copy")


_FORMAT_LETTERS = {"bold": "b", "italic": "i", "underline": "u"}


def _send_format_shortcut(kind: str) -> bool:
    """Ctrl+B, Ctrl+I or Ctrl+U, on the key that is that letter on the user's
    keyboard layout. Sent only to word processors (voice_edit_rules)."""
    from .keyboard_layout import shortcut_keycode
    key = shortcut_keycode(_FORMAT_LETTERS[kind])
    # KEY_LEFTCTRL=29
    return _ydotool_key(["29:1", f"{key}:1", f"{key}:0", "29:0"], what=kind)


def _focus_moved(focused_class) -> bool:
    """Whether another window has the focus than when the edit started. Only
    the class is known, so a second window of the same app is not noticed.
    A window that can't be found now counts as moved: it can't be checked."""
    now = _query_focused_window_class()
    if now is None and focused_class is not None:
        logger.info("Voice edit: the focused window can't be found now, so it can't be checked")
    return now != focused_class


# "fix that word": the whole dictation is one of these (normalized: lower case,
# no punctuation). Fix a Word opens on the dictation before it.
_FIX_WORD_PHRASES = frozenset({
    "fix that", "fix that word", "fix a word", "fix the word", "fix this word",
    "fix the last word", "fix that please", "fix that word please",
})


def _handle_fix_word(raw: str, beeps_on: bool) -> bool:
    """"fix that word": open Fix a Word over the app, on the newest dictation.
    True when the dictation was that command."""
    said = " ".join(re.sub(r"[^\w\s]", " ", raw.lower()).split())
    if said not in _FIX_WORD_PHRASES:
        return False
    logger.info("Voice command: fix that word")
    # Right after a dictation, in its window, Fix a Word can also fix it in
    # the text: it gets exactly what was typed, through a file only this
    # user can read (the runtime folder), which it deletes once read.
    args = []
    focused = _query_focused_window_class()
    typed = state.last_inserted_text
    if typed and state.shown_text and state.shown_text.endswith(typed):
        typed = state.shown_text        # the whole recording, phrases and all
    if typed.strip() and not (
            focused is not None and state.last_inserted_window is not None
            and focused != state.last_inserted_window):
        try:
            import json
            path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
                                "tuxtalk-fix-word.json")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"text": typed, "window": focused}, f)
            args = ["--in-place", path]
            # The text is about to change: undo and voice moves can't trust
            # their counts any more.
            state.last_inserted_text = ""
            _forget_shown()
            state.nav_context = None
        except OSError as e:
            logger.debug(f"Fix a Word can't fix it in the text: {e}")
    try:
        from .explain import start_helper_window
        start_helper_window("tuxtalk.fix_word_dialog", *args)
        _beep(beeps_on, *READY_BEEP)
    except Exception as e:
        logger.warning(f"Could not open Fix a Word: {e}")
        _beep(beeps_on, *CANCEL_BEEP)
        _notify("TuxTalk", "Fix a Word didn't open. Choose it in the TuxTalk menu instead.")
    return True


_UNKNOWN_WINDOW = object()


def _handle_navigation(raw: str, beeps_on: bool, focused_class=_UNKNOWN_WINDOW) -> bool:
    """Select and move by voice (TuxTalk Labs). True when the whole dictation
    was one of voice_navigation's commands, done or refused with a notice;
    False to type it as text."""
    if not _voice_navigation:
        return False
    from . import voice_navigation as nav
    command = nav.parse(raw)
    if command is None:
        return False
    logger.info(f"Voice command: {command.name}")
    if focused_class is _UNKNOWN_WINDOW:
        focused_class = _query_focused_window_class()
    if is_terminal_class(focused_class):
        _edit_problem("nav_terminal", beeps_on)
        return True
    # Right after a dictation the cursor is at its end; after an exact move,
    # nav_context says where in it. Only in the window it was typed into:
    # dictating into a chat, then saying "select the last sentence" in Writer
    # would otherwise select the chat message's length of Writer's text.
    elsewhere = (focused_class is not None and state.last_inserted_window is not None
                 and focused_class != state.last_inserted_window)
    if elsewhere:
        left, right = None, ""
    elif state.last_inserted_text:
        left, right = state.last_inserted_text, ""
    else:
        left, right = state.nav_context or (None, "")
    plan = nav.plan(command, left, right)
    if plan is None:
        # One message for "just restarted" and "dictated somewhere else since":
        # both come down to the one rule (it teaches the rule, too).
        _edit_problem("nothing_said", beeps_on)
        return True
    try:
        pressed = _output_backend().press_keys(plan.chords)
    except Exception as e:
        logger.error(f"Voice command: pressing the keys failed: {e}")
        pressed = False
    # The cursor has moved (or may have): undo's character count no longer
    # matches the document, and "delete last word" would eat the wrong text.
    state.last_inserted_text = ""
    _forget_shown()
    state.nav_context = (plan.left, plan.right) if pressed and plan.left is not None else None
    if pressed:
        _beep(beeps_on, *READY_BEEP)
    else:
        _edit_problem("shortcut_failed", beeps_on)
    return True


def _transcribe_and_edit(frames, rec_sr, beeps_on, language, injection_mode):
    """Voice editing: the recording is an instruction ("make this more formal")
    for the text selected in the focused app, which the AI rewrites and the
    rewrite replaces. Ctrl+Z in the app undoes it."""
    from . import ai_cleanup, clipboard, history, voice_edit_rules
    from .silence import is_speechless_tap
    t0 = time.time()
    pcm_int16 = np.frombuffer(b''.join(frames), dtype=np.int16)
    if pcm_int16.size == 0:
        return
    audio_f32 = pcm_int16.astype(np.float32) / 32768.0
    if rec_sr != SAMPLE_RATE:
        audio_f32 = _resample_audio(audio_f32, rec_sr, SAMPLE_RATE)
    if is_speechless_tap(audio_f32, SAMPLE_RATE):
        return

    if not _is_wayland_session():
        # The selection is read with wl-copy / wl-paste, which have no X11
        # side; without this every edit would say "select some text first".
        _edit_problem("needs_wayland", beeps_on)
        return
    focused_class = _query_focused_window_class()
    if is_terminal_class(focused_class):
        _edit_problem("terminal", beeps_on)
        return

    # The selection first, so "select some text first" comes up even when
    # nothing was said.
    try:
        selected, put_back = clipboard.copy_selection(_send_copy_keystroke)
    except clipboard.ClipboardUnavailable:
        _edit_problem("no_clipboard", beeps_on)
        return
    if not selected:
        # "select the last sentence" needs nothing selected: it makes the
        # selection the next edit works on (select and move by voice). The
        # clipboard is already back (copy_selection does that when empty).
        if _voice_navigation:
            instruction = _transcribe_audio(audio_f32, language)
            if instruction and _handle_navigation(instruction, beeps_on, focused_class):
                return
        _edit_problem("no_selection", beeps_on)
        return
    result = None
    try:
        instruction = _transcribe_audio(audio_f32, language)
        if not instruction:
            _edit_problem("no_instruction", beeps_on)
            return
        if _handle_navigation(instruction, beeps_on, focused_class):
            return
        logger.info(f"Voice edit: {_loggable(instruction)} ({len(selected)} characters)")
        # Deleting, formatting and exact jobs need no AI (voice_edit_rules.py).
        if voice_edit_rules.is_delete(instruction):
            # Transcribing can take seconds; Delete must not reach a window
            # the user has switched to meanwhile, so it needs a window to
            # compare with.
            if focused_class is None:
                logger.info("Voice edit: the focused window is unknown, so Delete can't be checked")
                _edit_problem("cant_check_window", beeps_on)
                return
            if _focus_moved(focused_class):
                _edit_problem("focus_moved", beeps_on)
                return
            # KEY_DELETE=111: the selection is still selected.
            if _ydotool_key(["111:1", "111:0"], what="delete"):
                logger.info("Voice edit: deleted the selection")
                _beep(beeps_on, *READY_BEEP)
            else:
                _edit_problem("shortcut_failed", beeps_on)
            return
        fmt = voice_edit_rules.formatting(instruction)
        if fmt == voice_edit_rules.CANT_FORMAT:
            _edit_problem("cant_format", beeps_on)
            return
        if fmt:
            if not voice_edit_rules.has_formatting_shortcuts(focused_class):
                logger.info(f"Voice edit: no formatting shortcuts in {focused_class!r}")
                _edit_problem("no_shortcuts", beeps_on)
                return
            if _focus_moved(focused_class):
                _edit_problem("focus_moved", beeps_on)
                return
            # The selection is still selected, so the app's shortcut formats it.
            if all(_send_format_shortcut(kind) for kind in fmt):
                logger.info(f"Voice edit: {', '.join(fmt)} with the app's shortcut ({focused_class})")
                _beep(beeps_on, *READY_BEEP)
            else:
                _edit_problem("shortcut_failed", beeps_on)
            return
        # Deleting and formatting press keys only, so any length will do; a
        # rewrite is typed back, and the AI's context holds only so much.
        if len(selected) > ai_cleanup.MAX_EDIT_CHARS:
            _edit_problem("too_long", beeps_on)
            return
        # "make it formal and remove the commas" is two changes, made one
        # after the other, each in code or by the AI (split_steps).
        steps = voice_edit_rules.split_steps(instruction)
        if len(steps) > 1:
            logger.info(f"Voice edit: {len(steps)} changes, one at a time")
        engine = _ai_engine
        text = selected
        for step in steps:
            done = voice_edit_rules.mechanical_edit(text, step)
            if done is not None:
                logger.info("Voice edit done in code, without the AI"
                            + (f" ({_loggable(step)})" if len(steps) > 1 else ""))
            elif engine is None:
                _edit_problem("not_installed", beeps_on)
                return
            elif not engine.is_ready():
                _edit_problem("not_ready", beeps_on)
                return
            else:
                done = engine.edit(text, step)
                if done and done.split() == text.split():
                    # Only spacing changed ("select all" got the line ends
                    # trimmed): not an edit anyone asked for.
                    done = None
                if done is None and len(steps) > 1:
                    # One change the AI had nothing for needn't cost the others.
                    logger.info(f"Voice edit: no change for {_loggable(step)}")
            if done is not None:
                text = done
        result = text if text != selected else None
        if result and _swiss_spelling:
            result = swiss_spelling(result)
        if result:
            result = _numbers_to_digits(result)
        # Exact: for an edit done in code, spacing may be the whole change
        # (indenting one line); the AI's spacing-only answers are dropped above.
        if not result or result == selected:
            _edit_problem("no_answer", beeps_on)
            return
        logger.info(f"Voice edit result: {_loggable(result)} "
                    f"(took {time.time() - t0:.2f}s)")
        history.add_entry(result)
        if focused_class is None:
            # Typed unchecked, as ordinary dictation is: refusing here would
            # turn voice editing off wherever the window can't be found.
            logger.info("Voice edit: the focused window is unknown, so typing goes ahead unchecked")
        elif _focus_moved(focused_class):
            # Typing now would put the rewrite into whatever window has focus.
            _edit_problem("window_changed", beeps_on)
            return
        # The selection is still active, so typing or pasting replaces it, in
        # one block (see _inject_text).
        try:
            outcome = _inject_text(result, injection_mode, t0, one_block=True)
        except Exception as e:
            logger.error(f"Voice edit: typing the rewrite failed: {e}", exc_info=True)
            outcome = "failed"
        if outcome in _UNDELIVERED_NOTICES:
            _report_undelivered(outcome, beeps_on)
        else:
            _beep(beeps_on, *READY_BEEP)
    finally:
        # A pasted rewrite may still be on the clipboard, whole or (pasted a
        # line at a time) its last line; that may be replaced.
        also = (result, *result.split("\n")) if result else ()
        if EDIT_RESTORE_DELAY_S:
            threading.Timer(EDIT_RESTORE_DELAY_S, put_back, kwargs={"also": also}).start()
        else:
            put_back(also=also)


# A combo press whose keys aren't all let go within this long is dropped, so a
# missed key release can never make TuxTalk paste out of the blue later.
PASTE_LAST_EXPIRY_S = 5.0


_SHIFT_CODES = frozenset({ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT})


# How long the end of a Shift+toggle edit waits for Shift to be let go.
EDIT_SHIFT_WAIT_S = 2.0


def _wait_for_keys_up(devices, codes, timeout) -> bool:
    """Wait until none of *codes* is down on any keyboard. False on timeout."""
    keyboards = [d for d in devices if _is_keyboard_device(d)]
    deadline = time.time() + timeout
    while True:
        down = set()
        for dev in keyboards:
            try:
                down |= set(dev.active_keys())
            except Exception:
                pass
        if not down & set(codes):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.02)


def _held_shift(held_modifiers) -> set:
    """The Shift keys among *held_modifiers*: Shift + the dictation key starts
    a voice edit instead of a dictation."""
    return set(held_modifiers) & _SHIFT_CODES


def _paste_last_dictation(cfg) -> None:
    """Type the newest Recent Dictations entry again, the way a dictation is
    typed (line breaks, injection mode, the trailing space)."""
    from .history import get_entries, _LINE_BREAK_MARKER
    entries = get_entries()
    if not entries:
        logger.info("Paste last dictation: nothing dictated yet")
        _beep(cfg.beeps, *CANCEL_BEEP)
        return
    text = entries[0].replace("\n", _LINE_BREAK_MARKER)
    if getattr(cfg, "auto_space", True):
        text += " "
    logger.info("Paste last dictation")
    outcome = _inject_text(text, cfg.injection_mode, time.time())
    if outcome in _UNDELIVERED_NOTICES:
        _report_undelivered(outcome, cfg.beeps)


def _handle_key_event(event, mode, hold_key, toggle_key,
                      voice_cmds_combo, held_modifiers,
                      devices, cfg, input_device_idx, paste_last_combo=None):
    """Handle a single keyboard event. Both hold (F8) and toggle (F9) are always active.

    Grabbed devices are tracked in module state (see _grab_all_devices), not
    returned, so that every way recording can end — release, toggle, ESC, or an
    exception inside start_recording — releases the keyboard.
    """
    # --- Hotkey test mode: report presses via D-Bus instead of recording ---
    if event.value == 1 and _hotkey_test_active():
        key_name = None
        if event.code == hold_key:
            key_name = "hold"
        elif toggle_key and event.code == toggle_key:
            key_name = "toggle"
        if key_name:
            _notify_tray_hotkey_pressed(key_name)
        return  # Don't process hotkeys normally in test mode

    # --- Paste Last Dictation combo (e.g. Ctrl+Alt+P) ---
    # Typed only once every key of the combo is let go: typing while Ctrl and
    # Alt are still down would turn each letter into a Ctrl+Alt shortcut.
    pending = state.paste_last
    if pending is not None:
        if time.time() - pending["at"] > PASTE_LAST_EXPIRY_S:
            state.paste_last = pending = None
        elif event.value == 0 and (event.code == pending["key"] or event.code in _MODIFIER_CODES):
            # Only the combo's own keys are waited for. Any other release
            # goes on to the handlers below: swallowing it would lose a hold
            # key's key-up and leave that recording running.
            if event.code == pending["key"]:
                pending["key_up"] = True
            if pending["key_up"] and not held_modifiers:
                state.paste_last = None
                if not state.is_recording:
                    _paste_last_dictation(cfg)
            return
    if paste_last_combo and event.value == 1 and not state.is_recording:
        required_mods, main_key = paste_last_combo
        if event.code == main_key and _check_modifiers_held(required_mods, held_modifiers):
            # Same brief grab as the Voice Commands combo below, so the key
            # press doesn't reach the focused app.
            _grab_keyboards_hiding(devices, {main_key, *held_modifiers})
            _release_all_grabs()
            state.paste_last = {"key": main_key, "key_up": False, "at": time.time()}
            return

    # --- Voice Commands combo hotkey (e.g. Ctrl+Shift+H) ---
    # Not while recording: its brief grab ends by releasing every grab, which
    # would also drop the ones a hold recording depends on.
    if voice_cmds_combo and event.value == 1 and not state.is_recording:
        required_mods, main_key = voice_cmds_combo
        if event.code == main_key and _check_modifiers_held(required_mods, held_modifiers):
            # Briefly grab the keyboards (not the pointer) to stop the keypress
            # leaking into the focused app. The same tracked grabs as recording,
            # and a finally that always releases, so a failing tray call can't
            # strand them. The modifiers are balanced too: a stuck Ctrl in the
            # desktop would be far worse than a stuck F8.
            _grab_keyboards_hiding(devices, {main_key, *held_modifiers})
            try:
                _show_voice_commands_via_dbus()
            finally:
                _release_all_grabs()
            return

    # --- Hold-to-talk: hold key down to record, release to stop ---
    if event.code == hold_key:
        if event.value == 1 and not state.is_recording:
            # Grab the keyboards so the hotkey doesn't reach the focused app,
            # without leaving the desktop thinking the key is still held (see
            # _grab_keyboards_hiding). start_recording releases the grabs itself
            # if the mic fails to open. With Shift (voice editing) Shift is
            # hidden too, or the Ctrl+C that copies the selection would arrive
            # as Ctrl+Shift+C.
            shift = _held_shift(held_modifiers) if _voice_editing else set()
            _grab_keyboards_hiding(devices, {hold_key, *shift})
            start_recording(cfg.beeps, cfg.notify, input_device_idx, edit_mode=bool(shift))
        elif event.value == 0 and state.is_recording:
            # Ungrab BEFORE text injection so ydotool can work
            _release_all_grabs()
            stop_recording(cfg.beeps, cfg.smart_quotes, cfg.notify, cfg.language,
                           cfg.auto_space, cfg.auto_period, cfg.injection_mode)

    # --- Tap-to-toggle: press once to start, press again to stop ---
    if toggle_key and event.code == toggle_key and event.value == 1:
        shift = _held_shift(held_modifiers) if _voice_editing else set()
        if not state.is_recording:
            if shift:
                # An edit: no double-tap hands-free, so not "from_toggle".
                start_recording(cfg.beeps, cfg.notify, input_device_idx, edit_mode=True)
            else:
                start_recording(cfg.beeps, cfg.notify, input_device_idx, from_toggle=True)
        elif state.edit_mode:
            # A hold edit holds the keyboards (brushing the toggle key mid-hold
            # lands here): let go first, or they stay grabbed for the whole edit.
            _release_all_grabs()
            # Stopping an edit. If Shift is down again, wait for the user to
            # let go, or the Ctrl+C that copies the selection would arrive as
            # Ctrl+Shift+C. Hiding it with injected key events, as the hold key
            # does, would replay them after a slow edit and start a new
            # recording by itself.
            if shift and not _wait_for_keys_up(devices, _SHIFT_CODES, EDIT_SHIFT_WAIT_S):
                cancel_recording(cfg.beeps, cfg.notify,
                                 "Voice edit cancelled: let go of Shift to finish it")
                return
            stop_recording(cfg.beeps, cfg.smart_quotes, cfg.notify, cfg.language,
                           cfg.auto_space, cfg.auto_period, cfg.injection_mode)
        elif _toggle_pressed_while_recording(cfg.beeps):
            pass  # second tap of a double-tap: now hands-free, keep recording
        else:
            # Also reached by brushing the toggle key mid-hold. Release first,
            # or the hold key's release branch is skipped and the grab is stranded.
            _release_all_grabs()
            stop_recording(cfg.beeps, cfg.smart_quotes, cfg.notify, cfg.language,
                           cfg.auto_space, cfg.auto_period, cfg.injection_mode)

    # --- ESC cancels in any mode ---
    if event.code == ecodes.KEY_ESC and state.is_recording and event.value == 1:
        cancel_recording(cfg.beeps, cfg.notify, "Cancelled by ESC")
        _release_all_grabs()


class _ServiceState:
    """Backend-agnostic per-service state (auto-timeout tracking), shared by the
    evdev loop (Backend A) and the portal loop (Backend B) via _service_tick."""

    def __init__(self, cfg, input_device_idx=None):
        # Held, not copied: _reload_live_settings updates cfg IN PLACE, so
        # reading through it keeps the auto-timeout settings live.
        self._cfg = cfg
        self.last_activity = time.time()
        # 0.0 == "never checked", so the first idle tick reloads immediately.
        self.last_config_check = 0.0
        # The mic is configured by name but recorded from by device number, so
        # the number has to be re-resolved whenever the name changes. Owning it
        # here keeps one copy, which a reload can update.
        self.input_device_idx = input_device_idx
        self._resolved_mic = getattr(cfg, "mic", "")

    def refresh_input_device(self) -> None:
        """Re-resolve the mic if its configured name changed.

        Called after a live reload. Resolving enumerates audio devices, so this
        is gated on the name actually changing rather than run every tick. A
        failure keeps the device currently in use — a mic that cannot be
        resolved must not take down the one that works.
        """
        mic = getattr(self._cfg, "mic", "")
        if mic == self._resolved_mic:
            return
        try:
            self.input_device_idx = _pick_input_device(mic)
            logger.info(f"Microphone changed to {mic!r} (device {self.input_device_idx})")
        except Exception as e:
            logger.warning(f"Could not resolve microphone {mic!r}, keeping current: {e}")
        # Either way, do not retry the same name every second.
        self._resolved_mic = mic

    @property
    def timeout_enabled(self) -> bool:
        return getattr(self._cfg, "auto_timeout_enabled", False)

    @property
    def timeout_minutes(self):
        return getattr(self._cfg, "auto_timeout_minutes", 5)

    @property
    def timeout_seconds(self):
        return self.timeout_minutes * 60


def _service_tick(cfg, svc: "_ServiceState") -> "LiveSettings | None":
    """One iteration of backend-agnostic service work: consume the thread-safe
    start/stop commands (set by the evdev loop OR the portal signal handlers),
    enforce the auto-timeout, and pick up settings changed in Preferences. Safe
    from either backend — _release_all_grabs() is a no-op when nothing is
    grabbed (the portal case).

    Returns the reloaded LiveSettings when the config changed, so a caller that
    keeps loop locals (the evdev loop's hold_key/mode) can rebind them; None
    otherwise. The return value matters because _config_file_changed() is
    edge-triggered: it reports a given change exactly once, so the caller cannot
    simply ask again.

    The microphone comes off *svc*, not a parameter: it is re-resolved on a
    live reload, and a caller holding its own copy would keep the startup one."""
    now = time.time()
    if _cmd_start_recording.is_set():
        _cmd_start_recording.clear()
        # Only the Flatpak's toggle SHORTCUT can double-tap; it says so. The
        # tray and GNOME menu toggles and the hold shortcut are plain starts.
        from_toggle = _cmd_start_from_toggle_key.is_set()
        _cmd_start_from_toggle_key.clear()
        if not state.is_recording:
            start_recording(cfg.beeps, cfg.notify, svc.input_device_idx, from_toggle=from_toggle)
            svc.last_activity = now
    if _cmd_stop_recording.is_set():
        _cmd_stop_recording.clear()
        if state.is_recording:
            _release_all_grabs()
            if os.environ.get("FLATPAK_ID"):
                # Backend B (Flatpak): transcription + portal typing are slow
                # and would freeze THIS GLib loop — and F3/F4 with it — for as
                # long as they run (10–20s on a CPU-only box). Snapshot the
                # recording here (fast) and run the slow half on a worker
                # thread so the hotkey stays responsive. _inject_lock serialises
                # workers so two dictations never transcribe/type at once.
                cap = _finish_capture(cfg.beeps, cfg.notify)
                if cap is not None:
                    frames, rec_sr = cap
                    if _chunk_pause_seconds > 0:
                        # Chunked dictation: through the chunk worker, so the
                        # rest is typed after this recording's chunks and
                        # before the next recording's. No wait, as below.
                        role = "last" if state.chunks_submitted else None
                        if role:
                            frames = _drop_speechless_rest(frames)
                        _chunk_worker().submit(
                            lambda frames=frames, rec_sr=rec_sr, cfg=cfg, role=role:
                            _transcribe_and_inject(
                                frames, rec_sr, cfg.beeps, cfg.smart_quotes,
                                cfg.notify, cfg.language, cfg.auto_space,
                                cfg.auto_period, cfg.injection_mode, chunk_role=role))
                    else:
                        def _worker(frames=frames, rec_sr=rec_sr, cfg=cfg):
                            with _inject_lock:
                                _transcribe_and_inject(
                                    frames, rec_sr, cfg.beeps, cfg.smart_quotes,
                                    cfg.notify, cfg.language, cfg.auto_space,
                                    cfg.auto_period, cfg.injection_mode)
                        threading.Thread(target=_worker, daemon=True,
                                         name="tt-inject").start()
            else:
                stop_recording(cfg.beeps, cfg.smart_quotes, cfg.notify, cfg.language,
                               cfg.auto_space, cfg.auto_period, cfg.injection_mode)
            svc.last_activity = now
    if svc.timeout_enabled and not state.is_recording:
        if now - svc.last_activity > svc.timeout_seconds:
            print(f"Auto-timeout: No activity for {svc.timeout_minutes} "
                  f"minutes, shutting down...")
            if cfg.notify:
                _notify("TuxTalk Auto-Timeout",
                        f"Service stopped after {svc.timeout_minutes} minutes of inactivity")
            sys.exit(0)

    # Pick up Preferences changes without a restart — indicator style/position,
    # typing delay, etc. The evdev loop (Backend A) does this inline; Backend B
    # (the portal path used by the Flatpak) runs ONLY this tick, so without the
    # same recheck live settings never apply there — the recording indicator
    # stays on whatever it was built with. Idle-only and throttled, mirroring the
    # evdev path. _reload_live_settings updates cfg in place and pushes the new
    # values into the running recording indicator.
    if not state.is_recording and now - svc.last_config_check >= CONFIG_RECHECK_SECONDS:
        svc.last_config_check = now
        # Custom commands live in their own file and are checked on the same
        # tick. Unconditionally, and before the early return below: an edit to
        # the Commands tab alone leaves config.toml untouched, so gating this on
        # a config change would be gating it on the thing that did not happen.
        if _commands_file_changed():
            _reload_custom_commands()
        if _words_file_changed():
            _reload_custom_words()
        if _config_file_changed():
            # Saving settings is activity too. Switching the auto-timeout on
            # must not stop the service a second later because the clock still
            # counts from the last D-Bus command.
            svc.last_activity = now
            live = _reload_live_settings(cfg, recording_indicator)
            # cfg.mic is fresh now; the device number behind it may not be.
            svc.refresh_input_device()
            return live
    return None


def _loop_evdev(cfg: Settings, input_device_idx):
    """Main event loop: monitor keyboard for hotkey presses and dispatch to recording."""
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    print(f"Session: {session} | Wayland={session=='wayland'}")
    logger.info(f"Session type: {session}, Wayland: {session=='wayland'}")
    mode = cfg.mode.lower().strip()
    # Both hotkeys work whatever the mode setting says.
    print(f"Hold key: {cfg.hotkey} | Toggle key: {cfg.toggle_hotkey or 'none'}")
    logger.info(f"Hold key: {cfg.hotkey}, Toggle key: {cfg.toggle_hotkey or 'none'}")

    # Auto-timeout setup
    svc = _ServiceState(cfg, input_device_idx)
    print(f"Auto-timeout: {svc.timeout_enabled} | Timeout: {svc.timeout_minutes} minutes")
    logger.info(f"Auto-timeout: enabled={svc.timeout_enabled}, minutes={svc.timeout_minutes}")

    # evdev opens these non-blocking, which the read loop below relies on.
    devices = [InputDevice(p) for p in list_devices()]
    last_device_scan = time.time()
    # Establish both files' current mtimes so the first poll does not report a
    # spurious change and re-apply what the service just started with.
    # The recheck timer itself lives on _ServiceState — _service_tick owns it
    # for both backends.
    _config_file_changed()
    _commands_file_changed()
    _words_file_changed()

    # First run check: exit early if onboarding not complete
    try:
        from tuxtalk.cuda_helper import is_first_run
        first_run = is_first_run()
    except Exception:
        first_run = False

    if first_run:
        logger.info("First run detected - onboarding not complete")
        print("Onboarding not complete. No hotkeys will be active until setup is finished.")
        return

    hold_key = _keycode_from_name(cfg.hotkey)
    toggle_key = _keycode_from_name(cfg.toggle_hotkey) if cfg.toggle_hotkey else None

    # Voice commands hotkey supports combos like "Ctrl+Shift+H"
    vc_hotkey_str = getattr(cfg, 'voice_commands_hotkey', '')
    voice_cmds_combo = _parse_hotkey_combo(vc_hotkey_str)
    voice_cmds_main_key = voice_cmds_combo[1] if voice_cmds_combo else None
    paste_last_combo = _parse_hotkey_combo(getattr(cfg, "paste_last_hotkey", ""))

    if hold_key is None:
        logger.info("No hotkey configured - service will not monitor any keys")
        print("No hotkey configured. Complete onboarding to activate dictation.")
        return

    # A hotkey is configured and onboarding is done, so from here on every
    # keypress is supposed to reach us. If no device could be opened, say so
    # now rather than running a poll loop over an empty device list forever.
    if not devices:
        diagnosis = _input_access_diagnosis()
        logger.error(diagnosis)
        print(diagnosis, file=sys.stderr)

    if voice_cmds_combo:
        print(f"Voice Commands hotkey: {vc_hotkey_str}")
        logger.info(f"Voice Commands hotkey: {vc_hotkey_str}")

    # Have the desktop hold the hotkeys back from the focused app (see
    # _claim_desktop_hotkeys). On KDE they are released by
    # stop_dictation_service, the tray, or this exit hook; registered even if
    # the claim failed, since a later hotkey change can still succeed, and
    # release() is a no-op off KDE. GNOME's extension lets go by itself when
    # the tray reports the service stopped.
    from . import kwin_hotkeys
    _claim_desktop_hotkeys(cfg)
    atexit.register(kwin_hotkeys.release)

    # Track which modifier keys are currently held (for combo detection)
    held_modifiers: set[int] = set()

    # Main event loop
    dead_devices = []
    while True:
        current_time = time.time()

        # Backend-agnostic work: consume D-Bus/portal start/stop commands,
        # enforce the auto-timeout, and pick up Preferences changes (all shared
        # with the portal loop).
        #
        # This loop keeps the hotkeys and mode in locals, so it rebinds them
        # from what the tick reloaded. It must not check _config_file_changed()
        # itself: that check is edge-triggered, and the tick has already
        # consumed the change.
        live = _service_tick(cfg, svc)
        if live is not None:
            hold_key = live.hold_key
            toggle_key = live.toggle_key
            vc_hotkey_str = live.vc_hotkey_str
            voice_cmds_combo = live.voice_cmds_combo
            voice_cmds_main_key = live.voice_cmds_main_key
            paste_last_combo = live.paste_last_combo
            mode = live.mode
            # Re-claim even if the hotkeys look unchanged: claim() is cheap,
            # and comparing would mean a second copy of the hotkey fields.
            _claim_desktop_hotkeys(cfg)
            logger.info("Applied settings change without restarting")

        # Read the devices that have key events waiting.
        for dev in _devices_with_input(devices, INPUT_WAIT_S):
            try:
                for event in dev.read():
                    if event.type == ecodes.EV_KEY:
                        # Our own injected key events (see _grab_keyboards_hiding)
                        # come back through this same read; they are not input.
                        if _is_own_injected_event(dev, event):
                            continue
                        # Track modifier key state for combo detection
                        if event.code in _MODIFIER_CODES:
                            if event.value in (1, 2):  # press or repeat
                                held_modifiers.add(event.code)
                            elif event.value == 0:  # release
                                held_modifiers.discard(event.code)

                        # Any hotkey counts as activity, whether or not the
                        # auto-timeout is on now: it can be switched on later,
                        # and must then count from the last real use.
                        if event.code in (hold_key, toggle_key, ecodes.KEY_ESC, voice_cmds_main_key,
                                          paste_last_combo[1] if paste_last_combo else None):
                            svc.last_activity = current_time
                        _handle_key_event(
                            event, mode, hold_key, toggle_key,
                            voice_cmds_combo, held_modifiers,
                            devices, cfg, svc.input_device_idx,
                            paste_last_combo=paste_last_combo)
            except BlockingIOError:
                pass
            except OSError as e:
                # Device unplugged or suspended mid-read. Drop it so we stop
                # polling a dead handle, but keep serving the others.
                logger.warning(f"Input device {getattr(dev, 'name', dev)} failed, dropping it: {e}")
                dead_devices.append(dev)
            except Exception as e:
                # Never swallow silently: an unlogged exception here would turn
                # a microphone error into an unexplained keyboard lockup.
                logger.error(f"Error handling input event: {e}", exc_info=True)

        _drop_dead_devices(dead_devices, devices, cfg)

        # Pick up keyboards that have (re)appeared. Only while idle: rescanning
        # mid-recording would add an ungrabbed device to a grabbed set, letting
        # the hotkey leak into the focused app. Throttled because it opens
        # every node under /dev/input.
        if not state.is_recording and current_time - last_device_scan >= DEVICE_RESCAN_SECONDS:
            last_device_scan = current_time
            _rediscover_devices(devices)

        # Backstop: we must never hold a keyboard grab while not recording.
        _release_grabs_if_not_recording()

        pass_time = time.time() - current_time
        if pass_time < INPUT_MIN_PASS_S:
            time.sleep(INPUT_MIN_PASS_S - pass_time)

class ModelUnavailable(Exception):
    """The speech model is not downloaded and the user cancelled the download,
    or it failed. Nothing to retry: the service cannot run without a model."""


class ModelWontLoad(ModelUnavailable):
    """The speech model is downloaded but won't load. Unlike a missing model,
    choosing it again in Preferences downloads nothing, so the fallback is for
    this run only and the user's choice stays."""


# Speech models from best quality to fastest. When the chosen model is not
# downloaded, the service falls back to the downloaded model nearest to it here.
_QUALITY_ORDER = ("large-v3", "parakeet-v3", "medium", "small", "base", "tiny")


def _guard_large_v3_without_cuda(cfg):
    """large-v3 without any CUDA libraries runs as medium.

    Without CUDA, large-v3 would run on the processor, far too slowly to use.
    This keeps the service usable for someone who set up large-v3 with CUDA
    and later lost the libraries (a fresh profile, a cleaned data folder). It
    uses the same check as Preferences, which accepts a system-wide CUDA
    install, so a model Preferences offers is never swapped here. It applies
    to every device but Vulkan: large-v3 runs there without any CUDA library,
    and Vulkan has its own fallback, which tells the user.
    """
    if cfg.model != "large-v3" or str(cfg.device).lower() == "vulkan":
        return
    try:
        from .cuda_helper import has_cuda_libraries
        if not has_cuda_libraries():
            logger.warning(
                "large-v3 selected but CUDA libraries not found — "
                "falling back to 'medium' to prevent non-functional state."
            )
            print("large-v3 requires CUDA (not installed). Falling back to 'medium'.")
            cfg.model = "medium"
            _notify("TuxTalk", "Large-v3 needs the CUDA files, which aren't installed, so "
                               "TuxTalk is using Medium for now. Pick CUDA under "
                               "Preferences, General, Device to download it and "
                               "use Large-v3 again.")
    except Exception:
        pass  # If cuda_helper unavailable, let the existing error handling deal with it


def _pick_fallback_model(wanted, downloaded, has_cuda):
    """The downloaded model closest in quality to *wanted*, or None.

    Ties go to the faster model. large-v3 is skipped without CUDA, where it
    is too slow to use (main() refuses it there for the same reason).
    """
    rank = {m: i for i, m in enumerate(_QUALITY_ORDER)}
    target = rank.get(wanted, rank["small"])
    options = [m for m in downloaded
               if m in rank and m != wanted and (m != "large-v3" or has_cuda)]
    if not options:
        return None
    return min(options, key=lambda m: (abs(rank[m] - target), -rank[m]))


def _build_model_or_exit(settings: Settings):
    """build_model, falling back to an already-downloaded model when the chosen
    one is not downloaded (the user cancelled, or the download failed).

    Cancelling usually means "not now", not "turn dictation off", so dictation
    carries on with the nearest model already on disk. The switch is saved, so
    every menu shows the model actually running, and the user is told. Only
    with no model downloaded at all does the service stop.

    A model that is downloaded but won't load (ModelWontLoad) gets the same
    fallback for this run only: saving it would replace the user's choice at
    every start, since choosing the model again downloads nothing.
    """
    try:
        return build_model(settings)
    except ModelUnavailable as e:
        from .model_helper import OFFERED_MODELS, is_model_cached_fast, model_display_name
        try:
            from .cuda_helper import has_cuda_libraries
            has_cuda = has_cuda_libraries()
        except Exception:
            has_cuda = False
        wanted = settings.model
        downloaded = [m for m in OFFERED_MODELS if is_model_cached_fast(m)]
        fallback = _pick_fallback_model(wanted, downloaded, has_cuda)
        if fallback is None:
            logger.warning(f"Dictation service stopping: {e}, and no other model is downloaded")
            print(f"{e}. Choose a model in Preferences to start dictating.")
            sys.exit(1)

        logger.warning(f"{e}; falling back to the downloaded {fallback} model")
        settings.model = fallback      # the D-Bus menus read this same object
        try:
            model = build_model(settings)
        except ModelUnavailable:
            logger.warning(f"Dictation service stopping: fallback {fallback} could not load either")
            sys.exit(1)
        if isinstance(e, ModelWontLoad):
            _notify("TuxTalk",
                    f"{model_display_name(wanted)} is downloaded but wouldn't load, so "
                    f"TuxTalk is using {model_display_name(fallback)} for now. It tries "
                    f"{model_display_name(wanted)} again the next time dictation starts.")
            return model
        try:
            from .config import load_config, save_config
            saved = load_config()
            saved.model = fallback
            save_config(saved)
        except Exception as save_error:
            logger.warning(f"Could not save the fallback model to settings: {save_error}")
        _notify("TuxTalk",
                f"{model_display_name(wanted)} wasn't downloaded, so TuxTalk is using "
                f"{model_display_name(fallback)} for now. You can choose "
                f"{model_display_name(wanted)} again in Preferences.")
        return model


def _build_vulkan_model(settings: Settings):
    """The Vulkan graphics engine for settings.model, or None to use the
    processor instead (with the reason logged and the user told). Never stops
    dictation: anything missing or broken falls back to the CPU."""
    from . import whisper_vulkan
    reason = None
    if not whisper_vulkan.is_installed(settings.model):
        reason = "its download isn't there"
    else:
        try:
            device_index = whisper_vulkan.find_device()
            if device_index is None:
                reason = "no graphics chip was found"
            else:
                model = whisper_vulkan.VulkanWhisperModel(settings.model, device_index)
                print(f"Model loaded successfully on the GPU through Vulkan (device {device_index})")
                logger.info(f"Model loaded: {settings.model} on vulkan device {device_index}")
                _record_engine(settings.model, "vulkan")
                return model
        except Exception as e:
            reason = f"the graphics engine failed to start ({e})"
    logger.warning(f"Vulkan GPU not used for {settings.model}: {reason}; using the processor")
    _notify("TuxTalk", "Couldn't use your graphics card through Vulkan, so TuxTalk is using the "
                        "processor for now. Choose the device again in Preferences to set it up.")
    return None


def _build_parakeet_gpu_model(settings: Settings):
    """Parakeet on the graphics chip through Vulkan (parakeet_gpu.py), or None
    to use the processor.

    Without its graphics files that's quiet: a setup with Vulkan as the device
    and Parakeet as the model can predate those files, and shouldn't get a
    warning at every start. Choosing Vulkan again in Preferences sets it up.
    A real failure is reported."""
    from . import parakeet_gpu, whisper_vulkan
    from .parakeet_engine import PARAKEET_MODEL
    if not whisper_vulkan.is_installed(PARAKEET_MODEL):
        logger.info("Parakeet's graphics files aren't downloaded; Parakeet runs on the processor")
        return None
    reason = None
    try:
        device_index = whisper_vulkan.find_device()
        if device_index is None:
            reason = "no graphics chip was found"
        else:
            model = parakeet_gpu.ParakeetGpuModel(device_index)
            print(f"Parakeet loaded on the GPU through Vulkan (device {device_index})")
            logger.info(f"Model loaded: {PARAKEET_MODEL} on vulkan device {device_index}")
            _record_engine(PARAKEET_MODEL, "vulkan")
            return model
    except Exception as e:
        reason = f"the graphics helper failed to start ({e})"
    logger.warning(f"Vulkan GPU not used for Parakeet: {reason}; using the processor")
    _notify("TuxTalk", "Couldn't run Parakeet on your graphics card, so TuxTalk is using the "
                        "processor for now. Choose the device again in Preferences to set it up.")
    return None


# What the service really loaded, as (model, device) with device "cpu", "cuda" or
# "vulkan". It can differ from the settings: Vulkan or CUDA falls back to the
# processor, Parakeet can't use CUDA, a missing model is swapped for one that's
# downloaded. The tray and the GNOME menu show this rather than the settings,
# which could say "GPU (Vulkan)" while the processor does the work.
_running_engine = None

# True once Gtk.main() runs on its own thread. From then on the service's main
# thread must not open the model download dialogs: two threads driving GTK at
# once crash the service.
_gtk_loop_running = False


def _record_engine(model, device):
    """Remember what was really loaded (see _running_engine)."""
    global _running_engine
    _running_engine = (str(model), str(device).lower())


def _notify_tray_running_engine():
    """Tell the tray what's really running (see _running_engine)."""
    if _running_engine is None:
        return
    try:
        _get_tray_dbus_proxy().NotifyRunningEngine(
            *_running_engine,
            dbus_interface='io.github.ronb1964.TalkType',
            timeout=_TRAY_DBUS_TIMEOUT,
        )
    except Exception as e:
        logger.debug(f"Could not tell the tray which engine is running: {e}")


# tuxtalk --toggle/--start/--stop (control.py). The tray normally answers
# them; these serve a service running without a tray, which then owns the
# D-Bus name itself.
_service_ready = False
_ready_lock = threading.Lock()
_waiting_start = None       # control.WaitingStart, made on first use


def _control_in_service(action):
    """A command-line action for a service with no tray; returns its status."""
    from . import control
    global _waiting_start
    with _ready_lock:
        if not _service_ready:
            if _waiting_start is None:
                _waiting_start = control.WaitingStart()
            return _waiting_start.apply(action)
    send, status = control.act_now(action, state.is_recording)
    if send:
        _handle_sigusr1(None, None)
    return status


def _mark_service_ready():
    """From here a toggle is acted on: start a recording the command line
    asked for meanwhile, and tell the tray (NotifyServiceReady)."""
    global _service_ready
    with _ready_lock:
        _service_ready = True
        start = _waiting_start is not None and _waiting_start.take()
    if start:
        logger.info("Starting the recording the command line asked for")
        _cmd_start_recording.set()
    from .service_launcher import write_ready_pid
    write_ready_pid(os.getpid())
    try:
        import dbus
        _get_tray_dbus_proxy().NotifyServiceReady(
            dbus.UInt32(os.getpid()),
            dbus_interface='io.github.ronb1964.TalkType',
            timeout=_TRAY_DBUS_TIMEOUT,
        )
    except Exception as e:
        logger.debug(f"Could not tell the tray the service is ready: {e}")


def build_model(settings: Settings):
    """Load the speech model on the configured device, falling back to the
    processor when the graphics card can't be used."""
    from .model_helper import BrokenModelFiles, download_model_with_progress
    from .parakeet_engine import is_parakeet

    if settings.device.lower() == "vulkan":
        build_on_gpu = _build_parakeet_gpu_model if is_parakeet(settings.model) else _build_vulkan_model
        vulkan_model = build_on_gpu(settings)
        if vulkan_model is not None:
            return vulkan_model
        # Fall through to the processor for this run only. settings.device is
        # left alone: a missing download or a driver hiccup is fixed from
        # Preferences, and saving "cpu" would quietly undo the user's choice.
        settings = Settings(**{**vars(settings), "device": "cpu"})

    if settings.device.lower() == "cuda" and not is_parakeet(settings.model):
        from . import cuda_helper
        if not cuda_helper.has_cuda_libraries():
            # The model loads "on cuda" without these files, then the first
            # dictation crashes inside the speech library (libcudnn missing),
            # past every fallback below. Processor for this run; the setting
            # stays, since downloading CUDA again in Preferences fixes it.
            logger.warning("Device is CUDA but the CUDA libraries are missing; using the processor")
            _notify("TuxTalk", "The CUDA files are missing, so TuxTalk is using the processor "
                                "for now. Pick CUDA under Preferences, General, Device to "
                                "download them again.")
            settings = Settings(**{**vars(settings), "device": "cpu"})

    compute_type = "float16" if settings.device.lower() == "cuda" else "int8"
    try:
        # Use model helper with progress dialog
        model = download_model_with_progress(
            settings.model,
            device=settings.device,
            compute_type=compute_type,
            redownload_broken=not _gtk_loop_running,
        )

        if model is None:
            raise ModelUnavailable(f"The {settings.model} model was not downloaded (cancelled or failed)")

        # Parakeet gets here only on the processor (the graphics route returned above).
        where = "cpu" if is_parakeet(settings.model) else settings.device
        print(f"Model loaded successfully on {where.upper()}")
        logger.info(f"Model loaded: {settings.model} on {where}")
        _record_engine(settings.model, where)
        return model
    except BrokenModelFiles as e:
        raise ModelWontLoad(str(e)) from e
    except ModelUnavailable:
        # Never "fall back to CPU" here. That fallback exists for a CUDA load
        # failure; treating a cancel as one would re-run the download without
        # asking and ignore the user's Cancel.
        raise
    except Exception as e:
        if settings.device.lower() == "cuda" and not is_parakeet(settings.model):
            print(f"CUDA failed: {e}")
            logger.error(f"CUDA error: {type(e).__name__}: {str(e)}")
            logger.debug("CUDA traceback:", exc_info=True)
            print("Falling back to CPU...")
            try:
                # Fall back to CPU — skip confirmation dialog since the user already
                # chose this model via the preset; a surprise dialog in a background
                # process would block silently and cause the service to hang.
                model = download_model_with_progress(
                    settings.model,
                    device="cpu",
                    compute_type="int8",
                    show_confirmation=False,
                    redownload_broken=not _gtk_loop_running,
                )

                if model is None:
                    raise ModelUnavailable(
                        f"The {settings.model} model was not downloaded (cancelled or failed)")

                print("Model loaded successfully on CPU (fallback)")
                logger.info("Model loaded on CPU (fallback from CUDA)")
                _record_engine(settings.model, "cpu")
                # This switches the saved device to the processor (below), so
                # the user is told.
                _notify("TuxTalk", "Couldn't use your NVIDIA graphics card (CUDA), so TuxTalk "
                                    "switched to the processor. Choose the device again in "
                                    "Preferences to try the graphics card.")

                # Persist device=cpu to config so future service restarts don't
                # try CUDA again and crash in a loop.
                try:
                    from .config import load_config, save_config
                    _cfg = load_config()
                    if _cfg.device == "cuda":
                        _cfg.device = "cpu"
                        save_config(_cfg)
                        logger.info("Updated config: device=cpu (CUDA unavailable — preventing crash loop)")
                except Exception as _ce:
                    logger.warning(f"Could not persist device=cpu after CUDA fallback: {_ce}")

                return model
            except BrokenModelFiles as cpu_e:
                raise ModelWontLoad(str(cpu_e)) from cpu_e
            except Exception as cpu_e:
                print(f"CPU fallback also failed: {cpu_e}")
                logger.error(f"CPU fallback failed: {cpu_e}")
                raise
        else:
            print(f"Model loading failed: {e}")
            logger.error(f"Model loading failed: {e}")
            raise

def parse_args():
    ap = argparse.ArgumentParser(prog="dictate", description="Press-and-hold / toggle dictation for Wayland")
    ap.add_argument("--model", help="Whisper model (tiny/base/small/medium/large-v3)", default=None)
    ap.add_argument("--device", help="Device (cpu/cuda)", default=None)
    ap.add_argument("--hotkey", help="Hold-to-talk hotkey (F1..F12 or a-z)", default=None)
    ap.add_argument("--mode", choices=["hold","toggle"], help="Activation mode", default=None)
    ap.add_argument("--toggle-hotkey", help="Toggle key (F1..F12 or a-z) when mode=toggle", default=None)
    ap.add_argument("--mic", help="Substring of input device name to use", default=None)
    ap.add_argument("--beeps", choices=["on","off"], help="Enable beeps", default=None)
    ap.add_argument("--smart-quotes", choices=["on","off"], help="Use “smart quotes”", default=None)
    ap.add_argument("--notify", choices=["on","off"], help="Desktop notifications", default=None)
    ap.add_argument("--language", help="Force language code (e.g., en). Empty = auto-detect", default=None)
    return ap.parse_args()

def main():
    from . import desktop_identity
    desktop_identity.apply()        # TuxTalk's icon on its windows, not KDE's generic one
    # The loaded speech model. Usually built after the GTK loop starts; built
    # earlier when it must first be downloaded (see the GTK section below).
    global model, _gtk_loop_running
    model = None
    _acquire_single_instance()
    _install_signal_handlers()

    cfg = load_config()
    args = parse_args()

    if args.model: cfg.model = args.model
    if args.device: cfg.device = args.device
    if args.hotkey: cfg.hotkey = args.hotkey
    if args.mode: cfg.mode = args.mode
    if args.toggle_hotkey: cfg.toggle_hotkey = args.toggle_hotkey
    if args.mic is not None: cfg.mic = args.mic
    if args.beeps: cfg.beeps = (args.beeps == "on")
    if args.smart_quotes: cfg.smart_quotes = (args.smart_quotes == "on")
    if args.notify: cfg.notify = (args.notify == "on")
    if args.language is not None: cfg.language = args.language

    _guard_large_v3_without_cuda(cfg)

    # Set global typing delay from config
    global _typing_delay
    _typing_delay = getattr(cfg, 'typing_delay', 12)
    logger.debug(f"Typing delay set to {_typing_delay}ms")

    # Whether transcribed text may be written to the log (off = redacted).
    global _log_transcripts
    _log_transcripts = getattr(cfg, 'log_transcripts', False)

    global _usage_stats
    _usage_stats = getattr(cfg, 'usage_stats', True)
    global _restore_clipboard
    _restore_clipboard = getattr(cfg, 'restore_clipboard', False)
    global _pause_media
    _pause_media = getattr(cfg, 'pause_media', True)
    global _paste_last_hotkey
    _paste_last_hotkey = getattr(cfg, 'paste_last_hotkey', '')

    # Dictation cleanup; the AI engine (if on) loads in the background.
    _apply_cleanup_settings(cfg)


    # Load custom voice commands
    global _custom_commands
    _custom_commands = load_custom_commands()
    if _custom_commands:
        logger.info(f"Loaded {len(_custom_commands)} custom voice command(s)")
        print(f"Loaded {len(_custom_commands)} custom voice command(s)")
    global _custom_words
    from . import custom_words
    _custom_words = custom_words.load()
    if _custom_words:
        logger.info(f"Loaded your words ({len(_custom_words)})")

    # Input device selection
    input_device_idx = _pick_input_device(cfg.mic)

    # Initialize GTK components (D-Bus service and recording indicator)
    # Both need the GTK main loop, so we initialize them together
    global dbus_service, recording_indicator
    dbus_service = None
    recording_indicator = None
    gtk_needed = False

    # Check if we need GTK at all
    if cfg.recording_indicator or True:  # Always try D-Bus for GNOME extension
        gtk_needed = True

    if gtk_needed:
        gtk_ok = False
        try:
            # Import GTK in the main thread
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk

            # Initialize D-Bus service for GNOME extension integration
            try:
                from .dbus_service import TuxTalkDBusService

                # Create a simple app instance with necessary attributes for D-Bus
                class AppInstance:
                    def __init__(self, cfg):
                        self.config = cfg
                        self.service_running = True
                        self.dbus_service = None

                    @property
                    def is_recording(self):
                        """Live recording state from the dictation engine."""
                        return state.is_recording

                    def show_preferences(self):
                        """Open preferences window"""
                        import subprocess
                        subprocess.Popen([sys.executable, "-m", "tuxtalk.prefs"])

                    def start_recording(self):
                        """Signal the evdev loop to start recording (thread-safe)."""
                        if not state.is_recording:
                            _cmd_start_recording.set()

                    def stop_recording(self):
                        """Signal the evdev loop to stop recording (thread-safe)."""
                        if state.is_recording:
                            _cmd_stop_recording.set()

                    def toggle_recording(self):
                        """Toggle recording state (thread-safe)."""
                        if state.is_recording:
                            _cmd_stop_recording.set()
                        else:
                            _cmd_start_recording.set()

                    def control(self, action):
                        """tuxtalk --toggle/--start/--stop with no tray running."""
                        return _control_in_service(action)

                    def start_service(self):
                        """Start the dictation service"""
                        import subprocess
                        try:
                            # Find the dictate script
                            src_dir = os.path.dirname(__file__)  # usr/src/tuxtalk
                            usr_dir = os.path.dirname(os.path.dirname(src_dir))  # usr
                            dictate_script = os.path.join(usr_dir, "bin", "dictate")

                            if os.path.exists(dictate_script):
                                subprocess.Popen([dictate_script], env=os.environ.copy())
                                logger.info(f"Started dictation service via {dictate_script}")
                            else:
                                subprocess.Popen([sys.executable, "-m", "tuxtalk.app"],
                                               env=os.environ.copy())
                                logger.info("Started dictation service via Python module")

                            self.service_running = True
                            if self.dbus_service:
                                self.dbus_service.emit_service_state(True)
                        except Exception as e:
                            logger.error(f"Failed to start service: {e}", exc_info=True)

                    def stop_service(self):
                        """Stop the dictation service"""
                        from .service_launcher import stop_dictation_service
                        try:
                            stop_dictation_service()
                            logger.info("Stopped dictation service")
                            self.service_running = False
                            if self.dbus_service:
                                self.dbus_service.emit_service_state(False)
                        except Exception as e:
                            logger.error(f"Failed to stop service: {e}", exc_info=True)

                    def set_model(self, model_name: str):
                        """Change the Whisper model (requires service restart).

                        Writes through config.save_config, the same path every
                        other writer uses.
                        """
                        try:
                            from .config import VALID_MODELS, load_config, save_config

                            if model_name not in VALID_MODELS:
                                logger.error(
                                    f"SetModel: refusing unknown model {model_name!r}"
                                )
                                return

                            cfg = load_config()
                            cfg.model = model_name
                            save_config(cfg)
                            self.config.model = model_name

                            logger.info(f"Model changed to {model_name} (restart required)")

                            # Emit signal so extension updates
                            if self.dbus_service:
                                self.dbus_service.emit_model_changed(model_name)
                        except Exception as e:
                            logger.error(f"Failed to set model: {e}", exc_info=True)

                    def quit(self):
                        """Quit the application"""
                        # stop_dictation_service stops only the service; a
                        # pkill by name would also match the tray and Preferences.
                        from .service_launcher import stop_dictation_service
                        try:
                            stop_dictation_service()
                        except Exception:
                            pass
                        sys.exit(0)

                app_instance = AppInstance(cfg)
                # A stand-in only: the tray owns the bus name (see
                # TuxTalkDBusService). This succeeds only with no tray running.
                dbus_service = TuxTalkDBusService(app_instance, primary=False)
                app_instance.dbus_service = dbus_service

                # Emit initial state so extension syncs properly
                dbus_service.emit_service_state(True)
                dbus_service.emit_model_changed(cfg.model)

                print("D-Bus service initialized for GNOME extension")
                logger.info("D-Bus service started successfully (no tray running)")
            except Exception as e:
                dbus_service = None
                if type(e).__name__ == "NameExistsException":
                    # The normal case: the tray already owns the name.
                    logger.info("The tray owns the D-Bus name; the service doesn't register its own")
                else:
                    print(f"Failed to initialize D-Bus service: {e}")
                    logger.error(f"D-Bus service initialization failed: {e}", exc_info=True)

            # Initialize recording indicator if enabled
            if cfg.recording_indicator:
                try:
                    recording_indicator = RecordingIndicator(
                        position=cfg.indicator_position,
                        offset_x=cfg.indicator_offset_x,
                        offset_y=cfg.indicator_offset_y,
                        size=cfg.indicator_size,
                        style=cfg.indicator_style,
                        color_mode=cfg.indicator_color_mode,
                        custom_color=cfg.indicator_color,
                        backing=cfg.indicator_backing,
                        sensitivity=cfg.indicator_sensitivity,
                        follow_window=getattr(cfg, "indicator_follow_window", False),
                    )
                    # Spectrum processor for the bars style; _feed_indicator uses it.
                    global _spectrum_processor
                    from .indicator_dsp import SpectrumProcessor
                    _spectrum_processor = SpectrumProcessor(bins=20)
                    print(f"Recording indicator initialized (position: {cfg.indicator_position}, size: {cfg.indicator_size})")
                    logger.info(f"Recording indicator initialized at position: {cfg.indicator_position}, size: {cfg.indicator_size}")
                except Exception as e:
                    print(f"Failed to initialize recording indicator: {e}")
                    logger.error(f"Recording indicator initialization failed: {e}", exc_info=True)
            gtk_ok = True
        except Exception as e:
            print(f"Failed to initialize GTK components: {e}")
            logger.error(f"GTK initialization failed: {e}", exc_info=True)

        # Outside the try above: a model that fails to load is not a GTK
        # failure, and must not be loaded a second time below.
        if gtk_ok:
            # If the model still has to be downloaded, do it NOW, before the GTK
            # loop thread starts. The download shows GTK dialogs and runs them on
            # this thread; with Gtk.main() already running on the thread below,
            # two threads would drive GTK at once and crash the service. An
            # already-downloaded model (the normal case) loads later, after the
            # loop is up.
            from .model_helper import is_model_cached_fast
            if not is_model_cached_fast(cfg.model):
                model = _build_model_or_exit(cfg)

            # Start single GTK main loop in a background thread for both D-Bus and recording indicator
            def run_gtk_loop():
                print("Starting GTK main loop...")
                Gtk.main()

            gtk_thread = threading.Thread(target=run_gtk_loop, daemon=True)
            gtk_thread.start()
            _gtk_loop_running = True
            print("GTK main loop started in background thread")

    if model is None:
        model = _build_model_or_exit(cfg)
    print(f"Config: model={cfg.model} device={cfg.device} lang={cfg.language or 'auto'} auto_space={cfg.auto_space} auto_period={cfg.auto_period}")
    logger.info(f"Configuration: model={cfg.model}, device={cfg.device}, language={cfg.language or 'auto'}, auto_space={cfg.auto_space}, auto_period={cfg.auto_period}")
    _notify_tray_running_engine()
    # A toggle sent while the model was loading would otherwise start a
    # recording seconds later, out of the blue, when the input loop starts.
    # One asked for on the command line waits in the tray instead (control.py).
    _cmd_start_recording.clear()
    _cmd_stop_recording.clear()
    _cmd_start_from_toggle_key.clear()
    _mark_service_ready()

    # Run the input backend chosen by FLATPAK_ID: evdev+ydotool everywhere
    # (EvdevInputBackend -> _loop_evdev), portals inside a Flatpak.
    from .input_backends import get_input_backend
    get_input_backend(cfg=cfg, input_device_idx=input_device_idx).start()

def _run_service_entry():
    """Entry for `python -m tuxtalk.app` (the dictation service).

    `-m` runs this file as the module "__main__", but the input backends import
    it as "tuxtalk.app", a DIFFERENT module object with its own globals. Running
    the package module's main() keeps the whole service in ONE namespace, so the
    globals main() sets (model, _custom_commands, ...) are the ones the
    recording loop reads; otherwise transcription fails with
    `NameError: name 'model'`.
    """
    from tuxtalk.app import main
    main()


if __name__ == "__main__":
    _run_service_entry()
