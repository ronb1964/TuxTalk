"""The dictation service's options that change without a restart: read
from config.toml at startup and again whenever Preferences saves it
(apply), with Your Words and the snippets, each from its own file. The
service reads them here at the moment it uses them, as live_settings.X."""
import os
import threading

from . import config as config_module
from . import send_countdown
from .config import load_custom_commands
from .logger import redact_text, setup_logger

logger = setup_logger(__name__)

# Whether the full text of transcriptions may be written to the on-disk log.
# Off by default so dictation never persists in plain text; refreshed from
# config at startup and by the live-settings reload, so the Preferences toggle
# takes effect on the next utterance without a restart.
log_transcripts = False

# Whether each dictation is counted in the usage stats (stats.py, Preferences >
# Stats). Numbers only; the text itself is never stored.
usage_stats = True

# Put the user's clipboard back after pasting a dictation (config
# restore_clipboard, issue #7); refreshed with the other live settings.
restore_clipboard = False

# Pause music and videos while recording (config pause_media, media_pause.py).
# Off until the service reads config at startup, so tests that call the real
# start_recording never pause anyone's music; refreshed with the live settings.
pause_media = False

# Turn other apps' sound down while recording (config duck_audio, audio_duck.py).
# Off until the service reads config, for the same reason as pause_media.
duck_audio = False

# The Paste Last Dictation combo (config paste_last_hotkey), for the "couldn't
# type that" notice; refreshed with the live settings. "" when turned off.
paste_last_hotkey = ""


def loggable(text):
    """Render transcribed text for a log line, redacted unless the user opted in."""
    return redact_text(text, reveal=log_transcripts)


# Dictation cleanup (Preferences). remove_fillers enables the "um"/"uh" and
# repeated-word rules; ai_engine is the running self-correction engine, or
# None while that option is off. Both follow config through
# apply(), at startup and on every live-settings reload.
remove_fillers = False
swiss_spelling = False     # ß -> ss in the finished text (Swiss Standard German)

# Spoken numbers as digits: None (off), "all" or "small_as_words" (one to nine
# stay words), and the language to read them in. See spoken_numbers.py.
numbers_mode = None
numbers_language = "en"

# Select and move by voice (TuxTalk Labs, voice_navigation.py).
voice_navigation = False

# Plain text in terminals and code editors (app_kind, plain_for_app).
adapt_to_app = True

# Press Enter after every dictation, as if each ended with "press enter"
# (key_commands.py).
press_enter_after = False      # after send_countdown's wait

# The languages auto-detect may pick from, () for any (language_limit.py).
auto_languages = ()
ai_engine = None

# Which of the two features using the AI engine are on. The engine runs while
# either is; each checks its own flag before using it.
ai_corrections = False
voice_editing = False

# Seconds of silence that end a toggle recording (0 = auto-stop off).
auto_stop_seconds = 0.0

# Seconds of silence that end a chunk (0 = chunked dictation off), and the live
# settings object middle chunks read beeps, quotes and language from.
chunk_pause_seconds = 0.0

chunk_cfg = None

# Type at pause fine-tuning (Preferences → Advanced → TuxTalk Labs).
chunk_speech_db = 6.0

chunk_context_marks = True


def apply(cfg):
    """Bring every live option in line with *cfg*, at startup and on each
    settings reload; start or stop the AI engine."""
    global remove_fillers, ai_engine, auto_stop_seconds, ai_corrections, voice_editing
    global swiss_spelling, numbers_mode, numbers_language, voice_navigation, adapt_to_app
    global auto_languages, press_enter_after
    global chunk_pause_seconds, chunk_cfg, chunk_speech_db, chunk_context_marks
    global log_transcripts, usage_stats, restore_clipboard, pause_media, duck_audio
    global paste_last_hotkey
    log_transcripts = getattr(cfg, "log_transcripts", False)
    usage_stats = getattr(cfg, "usage_stats", True)
    restore_clipboard = getattr(cfg, "restore_clipboard", False)
    pause_media = getattr(cfg, "pause_media", True)
    duck_audio = getattr(cfg, "duck_audio", False)
    paste_last_hotkey = getattr(cfg, "paste_last_hotkey", "")
    auto_stop_seconds = (float(getattr(cfg, "auto_stop_seconds", 2.0))
                         if getattr(cfg, "auto_stop_silence", False) else 0.0)
    chunk_cfg = cfg
    chunk_speech_db = float(getattr(cfg, "chunk_speech_db", 6.0))
    chunk_context_marks = bool(getattr(cfg, "chunk_context_marks", True))
    pause = (float(getattr(cfg, "chunk_pause_seconds", 0.8))
             if getattr(cfg, "chunked_dictation", False) else 0.0)
    if pause > 0 and auto_stop_seconds > 0 and pause >= auto_stop_seconds:
        # A pause as long as auto-stop's would end the recording instead of
        # the chunk, so the chunk pause gives way.
        clamped = max(0.3, auto_stop_seconds - 0.2)
        logger.warning(f"chunk_pause_seconds {pause} is not below auto_stop_seconds "
                       f"{auto_stop_seconds}; using {clamped}")
        pause = clamped
    chunk_pause_seconds = pause
    remove_fillers = bool(getattr(cfg, "remove_fillers", False))
    swiss_spelling = bool(getattr(cfg, "swiss_spelling", False))
    voice_navigation = bool(getattr(cfg, "voice_navigation", False))
    adapt_to_app = bool(getattr(cfg, "adapt_to_app", True))
    press_enter_after = bool(getattr(cfg, "press_enter_after", False))
    send_countdown.configure(cfg)
    from . import language_limit
    auto_languages = language_limit.parse(getattr(cfg, "auto_languages", ""))
    if getattr(cfg, "numbers_as_digits", False):
        small = getattr(cfg, "numbers_small_as_words", False)
        numbers_mode = "small_as_words" if small else "all"
    else:
        numbers_mode = None
    from .recommend import effective_language
    numbers_language = effective_language(cfg)
    ai_corrections = bool(getattr(cfg, "ai_corrections", False))
    voice_editing = bool(getattr(cfg, "voice_editing", False))
    want_ai = ai_corrections or voice_editing
    if want_ai and ai_engine is None:
        from . import ai_cleanup
        if ai_cleanup.is_installed():
            ai_engine = ai_cleanup.CorrectionEngine()
            ai_engine.start()
        else:
            logger.warning("The AI is on but not downloaded; "
                           "turn the option off and on in Preferences")
    elif not want_ai and ai_engine is not None:
        engine, ai_engine = ai_engine, None
        # stop() can wait up to 5 s for the server to exit. This runs on the
        # input thread, which must keep reading the hotkeys meanwhile.
        threading.Thread(target=engine.stop, daemon=True, name="tt-ai-stop").start()


# Global custom commands (loaded from config in main)
commands: dict[str, str] = {}

# Your Words (custom_words.py): names and terms to spell the user's way. Told
# to Whisper before it listens, and fixed in every engine's output afterwards.
# Empty until main() loads them; reloaded when custom_words.txt changes.
words: list[str] = []

_last_words_mtime = None

# mtime of the config file as of the last check. None means "not yet seen".
_last_config_mtime = None

# Same, for custom_commands.toml. It is a separate file, so it needs its own
# mtime — config.toml's says nothing about whether the commands changed.
_last_commands_mtime = None


def config_file_changed() -> bool:
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


def commands_file_changed() -> bool:
    """Whether custom_commands.toml's mtime has moved since the last check.

    Custom commands live in their own file, so config.toml's mtime says nothing
    about them; this is what makes an edit in the Commands tab take effect.

    Same contract as config_file_changed(): True once per change, and a missing
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


def reload_commands() -> None:
    """Re-read custom_commands.toml into the running service.

    A damaged file leaves the commands already in force untouched. load_config's
    reload follows the same rule: a bad file must never degrade a live dictation
    session, and load_custom_commands() returns {} on a read failure, which would
    otherwise silently disable every command mid-session.
    """
    global commands
    try:
        fresh = load_custom_commands()
        if config_module._commands_read_failed:
            logger.warning("Custom commands unreadable, keeping the current ones")
            return
        commands = fresh
        logger.info(f"Reloaded {len(commands)} custom voice command(s)")
    except Exception as e:
        logger.warning(f"Could not reload custom commands, keeping current: {e}")


def words_file_changed() -> bool:
    """Whether custom_words.txt's mtime has moved since the last check.
    Same contract as commands_file_changed()."""
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


def reload_words() -> None:
    """Re-read custom_words.txt into the running service. A file that can't
    be read keeps the words already in force, like reload_commands."""
    global words
    from . import custom_words
    try:
        fresh = custom_words.load(strict=True)
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Could not reload your words, keeping the current ones: {e}")
        return
    words = fresh
    logger.info(f"Reloaded your words ({len(words)})")
