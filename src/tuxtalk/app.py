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
import atexit
import select
import signal
import numpy as np
import sounddevice as sd
from evdev import InputDevice, ecodes, list_devices

from typing import NamedTuple

from .normalize import (normalize_text, append_auto_punct, plain_for_app,
                        safe_to_lowercase_first_word, swiss_spelling)
from .config import load_config, Settings, load_custom_commands
# Imported as a module too, so the live-settings reload can read CONFIG_PATH and
# LIVE_APPLIED_KEYS without pulling each name in separately.
from . import config as config_module
from . import notify as notify_module
from . import beeps, mic_stream
from .beeps import START_BEEP, HANDS_FREE_BEEP, CANCEL_BEEP, READY_BEEP
from .chunking import (boundary_mark, carry_question, hold_back_boundary, word_count,
                       words_in_context)
from .key_commands import auto_send, split_key_command
from .logger import setup_logger
from .recording_indicator import RecordingIndicator
from .undo import detect_undo_command, calculate_undo_length
from . import (custom_commands, delivery_notices, focused_window, modifier_hotkeys,
               secure_input, send_countdown, silence, single_instance, uinput_helper,
               virtual_keyboard)
from .window_classes import (ELECTRON_PASTE_BROKEN_CLASSES, SMART_NEWLINE_CLASSES_LOWER,
                             app_kind, is_document_window, is_terminal_class)
from .hotkey_keys import (MODIFIER_CODES, check_modifiers_held, keycode_from_name,
                          parse_hotkey_combo, track_modifier)
from . import recording_state as rs
from . import live_settings as live
from . import keyboard_grab
from . import keyboard_output
from . import tray_link
from . import engine
from . import last_dictation
from . import notices
import threading

logger = setup_logger(__name__)

_media_pauser = None            # media_pause.MediaPauser, created on the first pause
_audio_ducker = None            # audio_duck.AudioDucker, created on the first duck


def _pause_media_players():
    """Pause playing music and videos and turn other sounds down, as the
    options say, in the background (the keyboard is grabbed)."""
    global _media_pauser, _audio_ducker
    if live.duck_audio:
        if _audio_ducker is None:
            from .audio_duck import AudioDucker
            _audio_ducker = AudioDucker()
            # Quitting mid-dictation would otherwise leave everything quiet.
            atexit.register(_audio_ducker.restore_now)
        _audio_ducker.duck()
    if not live.pause_media:
        return
    if _media_pauser is None:
        from .media_pause import MediaPauser
        _media_pauser = MediaPauser()
        # Quitting mid-dictation would otherwise leave the music paused.
        atexit.register(_media_pauser.resume_now)
    _media_pauser.pause()


def _resume_media_players():
    """Resume what _pause_media_players paused and put back what it turned
    down. Runs even if an option was switched off mid-recording, so nothing
    is left paused or quiet."""
    if _media_pauser is not None:
        _media_pauser.resume()
    if _audio_ducker is not None:
        _audio_ducker.restore()



# Optional desktop notifications via libnotify

def _acquire_single_instance():
    """Take the service's single-instance lock (single_instance.py), or exit
    if another service holds it."""
    global _lockfile_handle
    try:
        lockfile = single_instance.open_lock("talktype.lock")
        claimed = single_instance.claim(lockfile)
    except OSError as e:        # no runtime folder, or it's full: not "already running"
        logger.warning(f"Could not take the single-instance lock: {e}")
        return
    if not claimed:
        print("Another TuxTalk instance is already running. Exiting.", file=sys.stderr)
        sys.exit(0)
    _lockfile_handle = lockfile     # closing it, even by garbage collection, lets go


# The single-instance lock's file, open while the service runs.
_lockfile_handle = None

CHANNELS = 1
MIN_HOLD_MS = 200
# Two toggle taps closer than this make the first tap's recording hands-free.
# Generous: 0.4 s missed slower hands (#21); a quick word still comes, at auto-stop.
DOUBLE_TAP_S = 0.7


# Global recording indicator and D-Bus service (initialized in main)
recording_indicator = None
dbus_service = None

# Undo voice-command parsing/length logic lives in tuxtalk.undo so it can
# be unit-tested without dragging in heavy audio/CUDA imports.


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
    if rs.state.is_recording:
        _cmd_stop_recording.set()
    else:
        _cmd_start_recording.set()


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


def _claim_desktop_hotkeys(cfg):
    """Ask the desktop to keep the hotkeys away from the focused app.

    We read them below the desktop, so without this F8 also reaches the app
    ("~" in a terminal). On KDE a KWin script holds them (kwin_hotkeys); on
    GNOME the extension does, from the list sent to the tray here; on COSMIC
    shortcuts of its own (cosmic_hotkeys); on other X11 desktops the X server
    (x11_hotkeys). Called at startup and after every live settings change.
    None of it can fail dictation: the claims never raise, and the tray call
    is fire-and-forget.
    """
    from . import cosmic_hotkeys, gnome_hotkeys, kwin_hotkeys, x11_hotkeys
    kwin_hotkeys.claim(cfg)
    cosmic_hotkeys.claim(cfg)
    x11_hotkeys.claim(cfg)
    try:
        import dbus
        tray_link.get_proxy().NotifyClaimedHotkeys(
            dbus.Array(gnome_hotkeys.accelerators(cfg), signature='s'),
            dbus_interface='io.github.ronb1964.TalkType',
            timeout=focused_window.TRAY_TIMEOUT_S,
        )
    except Exception as e:
        logger.debug(f"Could not send the hotkeys to the tray: {e}")



def _apply_custom_commands(text: str) -> tuple[str, dict[str, str]]:
    """The user's custom voice commands applied to the transcribed *text*
    (custom_commands.apply), with Your Words."""
    result, protected = custom_commands.apply(text, live.commands, live.words)
    if result != text:
        logger.info(f"Custom commands applied: {live.loggable(text)} -> {live.loggable(result)}")
    return result, protected



def _sd_callback(indata, frames_count, time_info, status):
    """sounddevice's audio callback: collect frames while recording."""
    if rs.state.is_recording:
        rs.state.frames.append(indata.tobytes())

        # Chunked dictation: at a pause, the phrase so far goes off to be typed.
        chunker = rs.state.chunker
        if chunker is not None and chunker.feed(indata):
            _cut_chunk(chunker)

        # Hands-free auto-stop: when the speaker has finished, ask the service
        # loop to stop, exactly as a second press of the toggle key would.
        detector = rs.state.silence
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
        if rs.state.chunker is not chunker:
            return  # the recording ended while this block was arriving
        frames, rs.state.frames = rs.state.frames, []
        _drop_tail_samples(frames, chunker.tail_samples())
        rs.state.chunks_submitted += 1
        number = rs.state.chunks_submitted
        rec_sr = getattr(rs.state, "recording_samplerate", engine.SAMPLE_RATE)
        pause = chunker.last_pause
        # Queued before the lock is let go: otherwise the stop could slip in
        # between counting this chunk and queuing it, and queue the last chunk
        # ahead of it. put() on the unbounded queue never blocks.
        _chunk_worker().submit(lambda message=rs.state.message: _transcribe_chunk(
            frames, rec_sr, "middle", number, pause, message))


def _transcribe_chunk(frames, rec_sr, role, number, pause, message):
    """Transcribe and type chunk *number*, cut during the recording after a
    *pause* (seconds), with the settings as they are now, into *message*
    (_transcribe_and_inject's)."""
    # Logged here, on the worker, to keep file writes off the audio thread.
    seconds = sum(len(f) for f in frames) / 2 / rec_sr
    logger.info(f"Chunk {number}: {seconds:.1f}s of audio, cut after a {pause:.2f}s pause")
    cfg = live.chunk_cfg
    # Hands-free's "stop when quiet" waits while this phrase is worked on: an
    # AI rewrite can take seconds, and the user is reading, not finished.
    detector = rs.state.silence
    if detector is not None:
        detector.hold()
    try:
        _transcribe_and_inject(frames, rec_sr, cfg.beeps, cfg.smart_quotes, cfg.notify,
                               cfg.language, cfg.auto_space, cfg.auto_period,
                               cfg.injection_mode, chunk_role=role, message=message)
    finally:
        if detector is not None:
            detector.release()


def _arm_chunker(rec_sr, edit_mode=False):
    """Give this recording a pause detector when chunked dictation is on.
    Never for a voice edit: its instruction needs the whole recording."""
    chunker = None
    if live.chunk_pause_seconds > 0 and not edit_mode:
        from .chunking import PauseDetector
        chunker = PauseDetector(live.chunk_pause_seconds, rec_sr, speech_db=live.chunk_speech_db)
        # Started here, so the audio callback never has to build it.
        _chunk_worker()
    with _chunk_lock:
        rs.state.recording_serial += 1
        rs.state.chunks_submitted = 0
        rs.state.pending_boundary = None
        rs.state.question_open = False
        rs.state.message = send_countdown.Message()
        rs.state.held_cue = ""
        rs.state.edit_waiting = None
        rs.state.chunker = chunker


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


def _pick_input_device(mic_substring: str | None):
    """Return device index for the best matching input device.

    Delegates to config.find_input_device() which handles PipeWire
    auto-detection when no mic name is configured. This avoids the
    broken ALSA "default" virtual device that returns garbage audio
    on PipeWire systems.
    """
    global _system_default_mic
    from .config import find_input_device
    if mic_substring:
        _system_default_mic = None      # a mic chosen by name stays put
        return find_input_device(mic_substring)
    source = config_module.default_source_name()
    idx = find_input_device(mic_substring)
    _system_default_mic = (source, idx)
    return idx


# (PipeWire default source name, device index) while the mic is on System
# Default; None when a mic was chosen by name. See _follow_system_default.
_system_default_mic = None


def _follow_system_default(input_device_idx):
    """The device to record from, following a switched desktop default.

    The default mic is turned into a device number when it's resolved, so
    switching inputs in GNOME's Quick Settings or KDE's applet used to need a
    restart (issue #14). Asking PipeWire costs about a millisecond, so it's
    done on every recording, and devices are re-resolved only on a change.
    Anything that goes wrong keeps the device already in use.
    """
    tracked = _system_default_mic
    if tracked is None:
        return input_device_idx
    source = config_module.default_source_name()
    if source is None or source == tracked[0]:
        return tracked[1]
    try:
        idx = _pick_input_device("")
    except Exception as e:
        logger.warning(f"Could not follow the new default microphone {source!r}: {e}")
        return tracked[1]
    logger.info(f"The system's default microphone changed to {source!r} (device {idx})")
    return idx

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
    # The hotkey helper may already hold events it took in while answering
    # another call (active_keys), or have a reconnect due: ready now.
    due = [d for d in devices if getattr(d, "needs_service", None) and d.needs_service()]
    if due:
        return due
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


class LiveSettings(NamedTuple):
    """Values the main loop keeps in locals and must rebind after a reload."""
    hold_key: int | None
    toggle_key: int | None
    vc_hotkey_str: str
    voice_cmds_combo: tuple | None
    voice_cmds_main_key: int | None
    paste_last_combo: tuple | None = None


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

        keyboard_output.key_delay_ms = cfg.key_delay

        live.apply(cfg)

        if indicator is not None:
            indicator.apply_settings(
                cfg.indicator_position,
                cfg.indicator_size,
                cfg.indicator_offset_x,
                cfg.indicator_offset_y,
                style=cfg.indicator_style,
                color_mode=cfg.indicator_color_mode,
                custom_color=cfg.indicator_color,
                sensitivity=cfg.indicator_sensitivity,
                follow_window=getattr(cfg, "indicator_follow_window", False),
            )

        vc_hotkey_str = getattr(cfg, "voice_commands_hotkey", "")
        voice_cmds_combo = parse_hotkey_combo(vc_hotkey_str)

        return LiveSettings(
            hold_key=keycode_from_name(cfg.hotkey),
            toggle_key=keycode_from_name(cfg.toggle_hotkey) if cfg.toggle_hotkey else None,
            vc_hotkey_str=vc_hotkey_str,
            voice_cmds_combo=voice_cmds_combo,
            voice_cmds_main_key=voice_cmds_combo[1] if voice_cmds_combo else None,
            paste_last_combo=parse_hotkey_combo(getattr(cfg, "paste_last_hotkey", "")),
        )
    except Exception as e:
        logger.warning(f"Could not apply reloaded settings: {e}", exc_info=True)
        return None


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
            if not uinput_helper.is_keyboard_device(dev):
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
    if rs.state.is_recording and keyboard_grab.grabbed_devices:
        logger.warning(
            "Input device died while the hold hotkey was down — ending the "
            "recording, since its key-up can no longer arrive"
        )
        keyboard_grab.release_all()
        stop_recording(cfg.beeps, cfg.smart_quotes, cfg.notify, cfg.language,
                       cfg.auto_space, cfg.auto_period, cfg.injection_mode)

    dead_devices.clear()


def _open_input_stream(device_idx) -> None:
    """Open and start the microphone stream. Raises if the device is unusable."""
    # Use the device's native sample rate (may differ from 16kHz on ALSA hw: devices)
    rs.state.recording_samplerate = mic_stream.input_samplerate(device_idx, engine.SAMPLE_RATE)
    sd.default.channels = CHANNELS
    sd.default.samplerate = rs.state.recording_samplerate
    rs.state.stream = sd.InputStream(callback=_sd_callback, dtype='int16', device=device_idx)
    rs.state.stream.start()


def start_recording(beeps_on: bool, notify_on: bool, input_device_idx,
                    from_toggle: bool = False, edit_mode: bool = False,
                    held_send: bool = False) -> bool:
    """Begin capturing audio. Returns True only if recording actually started.

    *from_toggle* marks a start by the toggle key, so a quick second tap can
    switch it to hands-free (see _toggle_pressed_while_recording).
    *held_send*: the press that starts it already held a pending send.

    On failure this releases any input-device grabs before returning. The
    keyboard is grabbed just before this is called, so without that release a
    microphone error would leave the user unable to type anywhere at all.
    """
    # Pressing a hotkey while a send counts down holds it: a tap leaves the
    # text unsent, a hold dictates more onto it.
    rs.state.held_send = send_countdown.hold(announce=False) or held_send
    rs.state.frames = []
    rs.state.was_cancelled = False
    rs.state.silence = None
    rs.state.chunker = None
    rs.state.press_t0 = time.time()
    rs.state.released_while_starting = False
    rs.state.toggle_started_at = None
    rs.state.edit_mode = edit_mode
    # Set before the stream starts — _sd_callback drops frames unless it's set.
    rs.state.is_recording = True
    if engine.model is None:
        # Power Saving let the model go: load it while the user talks.
        threading.Thread(target=engine.wake, daemon=True, name="tt-wake-model").start()
    input_device_idx = _follow_system_default(input_device_idx)
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
            if fresh_idx != input_device_idx:
                notices.warn_mic_switched(input_device_idx, fresh_idx)
        except Exception as e:
            # Genuinely unavailable: unplugged, or held exclusively by another
            # app. Undo everything and tell the user; failing silently would
            # leave the keyboard grabbed and the machine unusable.
            logger.error(f"Could not start recording: {e}", exc_info=True)
            rs.state.is_recording = False
            _stop_stream_safely()
            keyboard_grab.release_all()
            beeps.beep(beeps_on, *CANCEL_BEEP)
            if notify_on:
                notify_module.notify("Microphone unavailable",
                                     "Check that no other app is using it.")
            if rs.state.held_send:
                send_countdown.say_held()   # nothing to dictate onto it: it stays unsent
            return False

    _arm_chunker(getattr(rs.state, "recording_samplerate", engine.SAMPLE_RATE), edit_mode)
    _pause_media_players()
    rs.state.start_beep = beeps.beep(beeps_on, *START_BEEP)
    if notify_on: notify_module.notify("Recording… speak now", "")
    tray_link.notify_recording_state(True)
    if recording_indicator:
        try:
            recording_indicator.show_at_position()
            recording_indicator.start_recording()
        except Exception as e:
            logger.error(f"Failed to show recording indicator: {e}", exc_info=True)

    # The double-tap is timed from here, the start beep heard: keys wait on
    # this thread, so a tap made during a slow start still counts (#21).
    if from_toggle and rs.state.start_beep is not None:
        beeps.wait_until_played(over=rs.state.start_beep)
    rs.state.toggle_started_at = time.time() if from_toggle else None
    logger.info(f"Recording started in {time.time() - rs.state.press_t0:.2f}s")
    return True


def _stop_stream_safely():
    """Stop and close the microphone stream. An audio system that stops
    answering costs mic_stream.STOP_TIMEOUT_S, not the whole service."""
    stream, rs.state.stream = rs.state.stream, None
    if stream is not None:
        mic_stream.close(stream)


def cancel_recording(beeps_on: bool, notify_on: bool, reason="Cancelled"):
    """End a recording without transcribing it, telling the user *reason*."""
    rs.state.is_recording = False
    rs.state.was_cancelled = True
    rs.state.edit_mode = False
    _stop_stream_safely()
    _resume_media_players()
    beeps.beep(beeps_on, *CANCEL_BEEP)
    print(reason)
    logger.debug(f"Recording cancelled: {reason}")
    if notify_on: notify_module.notify(reason, "")

    if recording_indicator:
        recording_indicator.hide_indicator()
    tray_link.notify_recording_state(False)


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
    paste is broken are typed into instead, in _inject_text. It types on X11.
    """
    # Inside a Flatpak, clipboard-paste needs wl-copy (not bundled) and the libei
    # output backend types directly — always type, never attempt paste.
    if os.environ.get("FLATPAK_ID"):
        return ("type", "Flatpak: libei typing")

    # If not auto mode, use user's choice directly
    if injection_mode.lower() != "auto":
        mode = "paste" if injection_mode.lower() == "paste" else "type"
        return (mode, f"User selected {mode} mode")

    # Pasting needs wl-copy, which only talks to a Wayland compositor. On X11
    # every paste would fail and fall back to typing anyway.
    if not keyboard_output.is_wayland_session():
        return ("type", "Auto: not a Wayland session, typing")
    return ("paste", "Auto: paste")

def plan_line_breaks(text: str, one_paste: bool) -> list[str]:
    """How to deliver *text*'s line breaks: the chunks to paste, in order.

    One chunk means one paste with real newlines in it. More than one means
    paste each and send Shift+Enter between them.

    Terminals bind no soft-newline key — Enter runs the command — so the
    keystroke is simply ignored there and everything lands on one line. What a
    terminal does accept is a newline inside pasted text: bracketed paste (on by
    default in modern bash and zsh) inserts it literally instead of executing at
    it. So a terminal gets one paste carrying real newlines, and so do KDE's
    editors (SMART_NEWLINE_CLASSES_LOWER), where a pasted newline copies
    nothing from the line above (in a code mode Kate still indents it, as
    any paste). Not a plain Enter there: Kate's terminal panel would run it.

    Everywhere else keeps the keystroke, because in a chat input like Claude
    Desktop a literal newline SUBMITS the message instead of breaking the line.
    """
    marker = "\xa7SHIFT_ENTER\xa7"
    normalised = text.replace("\n", marker)
    if one_paste:
        return [normalised.replace(marker, "\n")]
    return normalised.split(marker)




def _handle_undo(raw: str, beeps_on: bool, notify_on: bool) -> bool:
    """Check if raw text is an undo command and execute it.

    Returns True if an undo was handled (caller should return early).
    """
    detected = detect_undo_command(raw)
    if not detected:
        return False

    undo_type, count = detected
    # Deleting changes the text a voice move remembered (nav_context).
    rs.state.nav_context = None
    label = undo_type if count == 1 else f"{count} {undo_type}s"
    logger.info(f"Undo command detected: {label}")

    # 'everything' clears the entire input field via Ctrl+A + Backspace.
    # No tracked dictation required: it wipes whatever is in the textarea,
    # including text that wasn't typed by TuxTalk.
    if undo_type == 'everything':
        # In a terminal Ctrl+A jumps to the start of the line and Backspace there
        # deletes nothing, so the line is cleared the shell's way: Ctrl+E to its end,
        # Ctrl+U to delete back to the prompt (bash, zsh, fish; Ctrl+A is screen's).
        backend = keyboard_output.output_backend()
        if is_terminal_class(focused_window.window_class()):
            cleared = backend.press_keys([(("CTRL",), "E"), (("CTRL",), "U")])
        else:
            cleared = backend.select_all_delete()
        if cleared:
            rs.state.last_inserted_text = ""
            last_dictation.forget_shown()
            rs.state.continue_mid_sentence = False
            beeps.beep(beeps_on, *READY_BEEP)
            if notify_on: notify_module.notify("Cleared the text box", "")
        else:
            print("Could not clear input field (text injection unavailable)")
            beeps.beep(beeps_on, *CANCEL_BEEP)
            if notify_on: notify_module.notify("Couldn't clear the text box", "")
        return True

    if not rs.state.last_inserted_text:
        logger.info("Undo requested but no previous text to undo")
        beeps.beep(beeps_on, *CANCEL_BEEP)
        if notify_on: notify_module.notify("Nothing to undo", "")
        return True

    # Backspacing the last dictation's length in a different window would
    # delete text there that TuxTalk never typed. Unknown windows go ahead,
    # as before.
    focused = focused_window.window_class()
    if keyboard_output.not_where_typed(focused):
        logger.info(f"Undo refused: the last dictation went to {rs.state.last_inserted_window!r}, "
                    f"not {focused!r}")
        notices.edit_problem("undo_other_window", beeps_on)
        return True

    # Calculate how many characters to delete (iterates `count` times)
    delete_count = calculate_undo_length(rs.state.last_inserted_text, undo_type, count)
    logger.info(f"Undo: deleting {delete_count} characters (last text was {len(rs.state.last_inserted_text)} chars, count={count})")

    if delete_count > 0:
        # Only shrink the buffer once the deletion is confirmed; otherwise it
        # falls out of step with the document and the next undo backspaces
        # over the user's own text.
        try:
            deleted = keyboard_output.output_backend().backspaces(delete_count)
        except virtual_keyboard.StoppedPartway:
            # Some of the text is gone and some isn't, by an unknown amount,
            # so the buffer no longer matches the document. Forgetting it is
            # the only safe choice: a later undo then deletes nothing.
            logger.error("Undo stopped partway: an unknown number of backspaces landed; undo buffer cleared")
            rs.state.last_inserted_text = ""
            last_dictation.forget_shown()
            rs.state.continue_mid_sentence = False
            beeps.beep(beeps_on, *CANCEL_BEEP)
            if notify_on:
                notify_module.notify("Undo stopped partway", "Some of the text may still be there.")
            return True
        if not deleted:
            logger.error("Undo failed: backspaces were not delivered; buffer left unchanged")
            beeps.beep(beeps_on, *CANCEL_BEEP)
            if notify_on:
                notify_module.notify("Undo failed", "The text couldn't be deleted.")
            return True

        last_dictation.trim_shown(rs.state.last_inserted_text, delete_count)
        if delete_count >= len(rs.state.last_inserted_text):
            rs.state.last_inserted_text = ""
            rs.state.continue_mid_sentence = False
        else:
            rs.state.last_inserted_text = rs.state.last_inserted_text[:-delete_count]
            remaining = rs.state.last_inserted_text.rstrip()
            if remaining and not remaining.endswith(('.', '?', '!', '\u2026')):
                rs.state.continue_mid_sentence = True
                logger.info("Mid-sentence continuation enabled for next dictation")
            else:
                rs.state.continue_mid_sentence = False
        beeps.beep(beeps_on, *READY_BEEP)
        if notify_on: notify_module.notify(f"Undid the last {label}", "")
    else:
        print("Nothing to undo for this scope")
        beeps.beep(beeps_on, *CANCEL_BEEP)
    return True


def _numbers_to_digits(text: str) -> str:
    """*text* with spoken numbers as digits, when that option is on."""
    if not live.numbers_mode:
        return text
    from .spoken_numbers import to_digits
    return to_digits(text, live.numbers_language,
                     small_as_words=live.numbers_mode == "small_as_words")


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
    if live.words:
        from .custom_words import fix_spelling
        processed = fix_spelling(processed, live.words)

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
    if live.remove_fillers:
        from .cleanup import clean_fillers_and_repeats
        text = clean_fillers_and_repeats(text)
    if ai and live.ai_corrections and live.ai_engine is not None:
        from . import ai_cleanup
        text = ai_cleanup.fix_self_corrections(text, live.ai_engine)

    # Your Words, second pass: "tux talk" -> "TuxTalk" and iPhone's capitals
    # back after a sentence start made it IPhone. After snippets, so a snippet
    # whose phrase contains the split-up word still matches; while quoted
    # snippets are still placeholders, so a literal one is never touched; and
    # with no hyphen joins, since by now a hyphen means the user said "dash".
    if live.words:
        from .custom_words import fix_spelling
        text = fix_spelling(text, live.words, hyphens=False)

    # Spoken numbers as digits. Before the quoted snippets come back, so a
    # literal snippet is typed exactly as written.
    text = _numbers_to_digits(text)

    # Restore quoted (literal) custom command replacements before any further
    # processing so that auto-period/space checks see the real final text.
    text = custom_commands.restore_protected(text, protected)

    # Swiss Standard German writes ss where the engines write ß. Last, so it
    # also covers snippets, Your Words and the AI's corrections.
    if live.swiss_spelling:
        text = swiss_spelling(text)

    # Handle mid-sentence continuation after undo:
    # lowercase the first letter if we're continuing a sentence
    if rs.state.continue_mid_sentence and text:
        # In another window it's a new sentence: a message after "meet me at
        # noon and" in KWrite mustn't begin with a small letter.
        focused = focused_window.window_class()
        if (not keyboard_output.not_where_typed(focused) and text[0].isupper()
                and safe_to_lowercase_first_word(text, live.words)):
            text = text[0].lower() + text[1:]
            logger.info("Lowercased first letter for mid-sentence continuation")
        rs.state.continue_mid_sentence = False

    # Auto-period and auto-space. When the utterance ends with a line-break
    # command, the period lands BEFORE the break and no trailing space is
    # added, so "hello new line" leaves no orphan ". " on the next line.
    text = append_auto_punct(text, auto_period, auto_space)
    if app_kind:
        text = plain_for_app(text, app_kind)
    logger.info(f"Normalized text: {live.loggable(text)}")
    return text


def _inject_text(text: str, injection_mode: str, one_block: bool = False, into=None,
                 message=None) -> str:
    """Insert *text* into the active app (_deliver_text), then remember the
    window it landed in, for undo, "fix that word" and select and move.
    *into*, a look taken earlier, is the only window it may go into: "moved"
    when another has the focus by the time it would. Words of *message*
    (send_countdown.Message) are added to it, with where they went, for an
    automatic Enter or a key said after them.

    The focus is checked once more after typing because the report from
    before can be out of date. On KDE, clicking the desktop reports
    plasmashell; wl-copy's clipboard window then hands the focus back to the
    last real window, and the text lands there, where "delete last
    sentence" must work. No automatic Enter follows it there: a focus that
    moved while the words went in can't be told from the user switching.
    """
    before = focused_window.destination()
    sends_before, landed = keyboard_output.this_thread.sends, None
    keyboard_output.this_thread.into = into
    try:
        # Checked again right before each key, after the waits for the paste
        # (wl-copy) or a dictation key held (_send_keys).
        if into is not None:
            focused_window.claim_into(into, before)
        outcome = _deliver_text(text, injection_mode, one_block, where=before)
    except (virtual_keyboard.StoppedPartway, virtual_keyboard.GaveUp,
            keyboard_output.PasswordFieldNow) as stop:
        typed_some = keyboard_output.this_thread.sends != sends_before
        outcome = notices.delivery_stopped(stop, typed_some=typed_some)
    finally:
        keyboard_output.this_thread.into = None
    if outcome in ("ok", "partial"):
        after = focused_window.look()
        if after[0]:
            if after[0].casefold() != (rs.state.last_inserted_window or "").casefold():
                logger.info(f"The text went to {after[0]!r}, not {rs.state.last_inserted_window!r}")
            rs.state.last_inserted_window = after[0]
        rs.state.last_inserted_where = (rs.state.last_inserted_window, after[1])
        if outcome == "ok":
            landed = send_countdown.landed(before, after)
    if message is not None:
        message.add(outcome, landed)
    return outcome



def _deliver_text(text: str, injection_mode: str, one_block: bool = False, *, where) -> str:
    """Determine the best injection method and insert text into the active app.

    Pastes or types as _determine_injection_method decides, typing whatever a
    failed paste could not deliver.

    Returns how it went: "ok" (all of it arrived), "partial" (some chunks of a
    multi-part dictation arrived, then it stopped), "failed" (none did) or
    "password_field" (the focus is in one, so the rest wasn't sent).
    The caller tells the user about anything but "ok", so a dictation that
    never arrived doesn't fail silently.

    The focus may have moved into a password field since the text was made:
    *where* (focused_window.destination, asked right before) says, and so
    does a check before each further chunk of a multi-part delivery. No check
    closes the milliseconds between the last answer and the keys after it.

    *one_block* (voice editing) pastes text with line breaks in one go, real
    newlines and all. Line by line with Enter in between, an editor continues
    each "- " list line and the dashes pile up. Typed, the breaks still go in
    as Shift+Enter, since a plain Enter would send a chat message; that holds
    for every typed line break, not only voice editing's.
    """
    if where.password:
        return "password_field"
    # Which window this text goes into, so select and move by voice only
    # trusts "what I just said" in that same window.
    rs.state.last_inserted_window = focused_class = where.wm_class
    if focused_class in ELECTRON_PASTE_BROKEN_CLASSES:
        # A tab as four spaces, however it's typed: a real Tab moves the
        # focus from Claude Desktop's box to the Send button, and the next
        # space typed sends the message.
        text = text.replace("\t", "    ")
    # Determine injection method (auto mode does smart detection)
    detection_start = time.time()
    actual_mode, reason = _determine_injection_method(injection_mode)
    detection_time = time.time() - detection_start
    if detection_time > 0.1:
        logger.warning(f"Injection method detection slow: {detection_time:.2f}s")

    injection_start = time.time()

    # Electron-paste-broken override: if the focused window is an Electron app
    # where synthetic Ctrl+V silently fails on Wayland, skip clipboard-paste and
    # type instead. Only kicks in for auto/paste — if the user explicitly chose
    # "type", we already use Type mode.
    if injection_mode != "type":
        if focused_class in ELECTRON_PASTE_BROKEN_CLASSES and not os.environ.get("FLATPAK_ID"):
            logger.info(f"Electron-paste-broken override: class={focused_class!r}, typing")

            # Line breaks go in as Shift+Enter between the parts.
            marker = "\xa7SHIFT_ENTER\xa7"
            unified = text.replace("\n", marker) if "\n" in text else text
            success = True
            delivered_parts = 0  # chunks confirmed in the document
            breaks_sent = 0      # line breaks confirmed after those chunks
            password_field = False
            if marker in unified:
                parts = unified.split(marker)
                for i, part in enumerate(parts):
                    if i and focused_window.is_password():
                        success, password_field = False, True
                        break
                    if part and not keyboard_output.type_text_raw(part):
                        success = False
                        break
                    delivered_parts = i + 1
                    if i < len(parts) - 1:
                        time.sleep(0.03)
                        if not keyboard_output.send_shift_enter():
                            success = False
                            break
                        breaks_sent += 1
                        time.sleep(0.03)
            else:
                success = keyboard_output.type_text_raw(unified)
                delivered_parts = 1 if success else 0

            if success:
                injection_time = time.time() - injection_start
                logger.info(f"TIMING: Electron typing completed in {injection_time:.2f}s")
                # Track injected text for undo. Store the typed form: tabs
                # already replaced with 4 spaces, and each line-break marker
                # as one '\n' (one Shift+Enter keystroke = one backspace),
                # so backspace counts match what was actually sent.
                rs.state.last_inserted_text = unified.replace(marker, "\n")
                logger.debug(f"Kept {len(rs.state.last_inserted_text)} chars for undo (typed)")
                return "ok"

            # Partial delivery: chunks already in the document must not be sent
            # again by the fallback below, or the user gets a duplicate copy.
            if delivered_parts:
                landed = marker.join(unified.split(marker)[:delivered_parts])
                if breaks_sent == delivered_parts:
                    landed += marker   # the break after the last chunk landed too
                rs.state.last_inserted_text = landed.replace(marker, "\n")
                logger.warning(
                    f"Typing stopped after {delivered_parts} chunk(s) — "
                    f"not re-injecting, undo buffer holds only what landed"
                )
                return "password_field" if password_field else "partial"
            # Nothing landed, so the normal injection path can safely try again.
            logger.warning("Typing failed, falling through to normal injection")

    use_paste = (actual_mode == "paste")
    logger.info(f"Injection mode: configured={injection_mode!r}, actual={actual_mode!r}, reason={reason}")

    # Tracks whether text actually reached the focused app. Only a confirmed
    # injection updates the undo buffer; otherwise a later "undo last word"
    # would backspace characters the user's document never received.
    inject_ok = True

    # --- Smart hybrid paste (text with line-break markers) ---
    if use_paste and not one_block and ("\xa7SHIFT_ENTER\xa7" in text or "\n" in text):
        marker = "\xa7SHIFT_ENTER\xa7"
        # A terminal honours a newline inside a paste, but treats Shift+Enter
        # like Enter, so it gets one chunk carrying real newlines and no
        # keystrokes at all; so do KDE's editors (plan_line_breaks).
        parts = plan_line_breaks(text, is_terminal_class(focused_class)
                                 or (focused_class or "").lower() in SMART_NEWLINE_CLASSES_LOWER)
        logger.info(f"Smart hybrid mode: {len(parts)} part(s)")

        # Track where delivery stopped, so the fallback types only the rest:
        # re-injecting the whole text would duplicate the chunks that already
        # landed, which undo can't clean up.
        resume_at = None
        needs_break = password_field = False
        for i, part in enumerate(parts):
            if i and focused_window.is_password():
                resume_at, password_field = i, True
                break
            if part:
                logger.info(f"Pasting part {i+1}/{len(parts)}: {len(part)} chars")
                if not keyboard_output.paste_text(part):
                    logger.warning(f"Paste failed on part {i+1}; will type the remainder")
                    resume_at = i
                    break
                time.sleep(0.08)
            if i < len(parts) - 1:
                logger.info(f"Sending Shift+Enter after part {i+1}")
                if not keyboard_output.send_shift_enter():
                    # The chunk landed but its line break didn't: resume from
                    # the next chunk and supply the missing break.
                    logger.warning(f"Line break failed after part {i+1}; will type the remainder")
                    resume_at, needs_break = i + 1, True
                    break
                time.sleep(0.05)

        if resume_at is None:
            logger.info(f"Smart hybrid paste completed: {len(parts)} chunks")
        elif password_field:
            # Not asked again: a second answer that comes back "no" (a slow
            # app, out of time) would type the rest into the password field.
            # The chunks before reached the document, and so did the line
            # break after the last of them: the check comes after it.
            delivered = marker.join(parts[:resume_at]) + marker
            rs.state.last_inserted_text = delivered.replace(marker, "\n")
            logger.warning(f"Stopped after {resume_at}/{len(parts)} chunks: a password field "
                           "has the focus now")
            return "password_field"
        else:
            remainder = (marker if needs_break else "") + marker.join(parts[resume_at:])
            # A terminal's single chunk carries real newlines for bracketed
            # paste. Typed, a newline is a plain Enter and Shift+Enter is no
            # safer in a terminal, so _typed_breaks_for turns them into spaces
            # there; other apps get Shift+Enter.
            remainder = _typed_breaks_for(remainder.replace("\n", marker),
                                          focused_window.window_class())
            if keyboard_output.output_backend().type_text(remainder):
                inject_ok = True  # delivered chunks + typed remainder == full text
            else:
                # The chunks before the failure reached the document, and so did
                # the line break after the last of them unless that break is what
                # failed. Recording only those makes undo delete too little rather
                # than too much if part of the remainder was typed as well.
                delivered = marker.join(parts[:resume_at])
                if resume_at and not needs_break:
                    delivered += marker
                rs.state.last_inserted_text = delivered.replace(marker, "\n")
                logger.warning(
                    f"Injection stopped after {resume_at}/{len(parts)} chunks \u2014 "
                    f"undo buffer holds only what landed"
                )
                return "partial" if resume_at else "failed"

    # --- Simple paste (no markers) ---
    elif use_paste and keyboard_output.paste_text(text):
        injection_time = time.time() - injection_start
        logger.info(f"Text injected via paste: {len(text)} chars in {injection_time:.2f}s")
        if injection_time > 1.0:
            logger.warning(f"Paste injection slow: {injection_time:.2f}s")

    # --- Typing fallback ---
    else:
        # Typed, a real newline is a plain Enter, which sends a chat message or
        # runs a terminal's half-typed command. A snippet's line breaks are real
        # newlines, so they go in as Shift+Enter like a spoken "new line".
        text = _typed_breaks_for(text.replace("\n", "\xa7SHIFT_ENTER\xa7"),
                                 focused_window.window_class())
        backend = keyboard_output.output_backend()
        # The Flatpak's keyboard path types "…" as "..." and "café" as "cafe";
        # recording that form below keeps undo's backspace count right.
        text = backend.typeable(text)
        inject_ok = backend.type_text(text)
        injection_time = time.time() - injection_start
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
        rs.state.last_inserted_text = text.replace("\xa7SHIFT_ENTER\xa7", "\n")
        logger.debug(f"Kept {len(rs.state.last_inserted_text)} chars for undo")
    else:
        rs.state.last_inserted_text = ""
        logger.warning("Injection failed — undo buffer cleared")
    return "ok" if inject_ok else "failed"


def _toggle_pressed_while_recording(beeps_on: bool) -> bool:
    """A toggle-key press arrived during a recording. Returns True if it was
    the second tap of a double-tap and switched the recording to hands-free
    (the caller must then NOT stop it); False means a normal stop.

    A single tap keeps the toggle a plain on/off switch. Only a double-tap,
    with the option on, arms auto-stop. A word said before the second tap
    counts as speech; the start beep the mic may pick up first does not.
    """
    started = rs.state.toggle_started_at
    if (live.auto_stop_seconds <= 0 or started is None or rs.state.silence is not None
            or time.time() - started > DOUBLE_TAP_S):
        return False
    rate = getattr(rs.state, "recording_samplerate", engine.SAMPLE_RATE)
    detector = silence.SilenceDetector(live.auto_stop_seconds, rate)
    beep_hz = START_BEEP[0] if rs.state.start_beep is not None else None
    if silence.has_a_word(_frames_f32(list(rs.state.frames)), rate, ignoring_hz=beep_hz):
        detector.heard_speech()
    rs.state.silence = detector
    logger.info("Double-tap: this recording will stop by itself when you go quiet")
    beeps.beep(beeps_on, *HANDS_FREE_BEEP)
    return True


def _trim_auto_stop_tail(frames):
    """After an auto-stop, drop the silence the detector measured at the end.

    Only whole trailing blocks are removed, and only up to what was measured
    as silent (less a small margin kept for the last word), so speech is never
    touched. Runs after the stream stops, so no block is still being added.
    """
    detector, rs.state.silence = rs.state.silence, None
    _drop_tail_samples(frames, detector.tail_to_trim() if detector is not None else 0)


def _drop_speechless_rest(frames):
    """The audio after the last cut, or none when the pause detector heard no
    speech in it: room noise longer than the tap check would otherwise reach
    the engine, which invents a word for it ("Yeah.")."""
    if rs.state.last_chunk_speech == 0.0:
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
    if rs.state.silence is not None and rs.state.silence.nothing_said:
        # Hands-free with nothing said: drop it. Transcribing room noise can
        # type a stray "Thank you." (see silence.is_speechless_tap).
        rs.state.silence = None
        rate = getattr(rs.state, "recording_samplerate", engine.SAMPLE_RATE)
        dead_mic = _frames_are_dead_mic(rs.state.frames, rate)
        cancel_recording(beeps_on, notify_on and not dead_mic, "Nothing heard, so nothing was typed")
        if dead_mic:
            notices.warn_silent_mic()
        return None
    # Before the space below: the time typing it takes isn't part of the press.
    held_ms = int((time.time() - (rs.state.press_t0 or time.time())) * 1000)
    if rs.state.held_send and send_countdown.owes_space() and not rs.state.chunks_submitted:
        # The hotkey that held the send is up now; a tap leaves the text as
        # it is, a dictation goes after this space.
        send_countdown.type_owed_space()
    if held_ms < MIN_HOLD_MS or (rs.state.held_send and rs.state.released_while_starting):
        if rs.state.held_send:
            cancel_recording(beeps_on, False, send_countdown.HELD_NOTICE)
            send_countdown.say_held()
        else:
            cancel_recording(beeps_on, notify_on, f"Cancelled (held {held_ms} ms)")
        return None
    with _chunk_lock:
        # No more cuts: from here on the audio is the last chunk.
        chunker, rs.state.chunker = rs.state.chunker, None
        rs.state.last_chunk_speech = chunker.speech_time if chunker is not None else None
        frames = rs.state.frames  # snapshot the reference before it can be reassigned
    if chunker is not None and not rs.state.chunks_submitted:
        logger.info(f"Chunked dictation: no pause found ({chunker.describe()})")
    rec_sr = getattr(rs.state, 'recording_samplerate', engine.SAMPLE_RATE)
    rs.state.is_recording = False
    _stop_stream_safely()
    _resume_media_players()
    tray_link.notify_recording_state(False)  # the tray icon shows recording stopped
    _trim_auto_stop_tail(frames)
    if rs.state.was_cancelled:
        return None
    if recording_indicator:
        recording_indicator.hide_indicator()
    return frames, rec_sr



def _shortened(text, limit):
    """*text* cut to *limit* characters for a notification, with … if cut."""
    return f"{text[:limit]}{'…' if len(text) > limit else ''}"


def _frames_f32(frames):
    """Raw int16 recording frames as float samples in -1..1."""
    return np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32) / 32768.0


def _frames_are_dead_mic(frames, rec_sr) -> bool:
    """silence.is_dead_mic for raw int16 recording frames."""
    return silence.is_dead_mic(_frames_f32(frames), rec_sr)



# Silence put between two chunks when the engine hears them together, so the
# pause is still there to hear (the cut kept only 0.3 s of it).
JOINT_GAP_S = 0.5
# Seconds heard on each side of the pause for that: enough words for the
# engine to judge the seam, so a long phrase isn't transcribed a second time
# whole (a 45 s phrase took a second 45 s pass).
CONTEXT_S = 6.0
_SENTENCE_ENDS = (".", "?", "!", "…")


def _context_samples() -> int:
    return int(CONTEXT_S * engine.SAMPLE_RATE)



def _corrects_across_pause(raw: str, chunk_role, message) -> bool:
    """True when this phrase corrects the one typed before the pause ("no
    wait, four" after "meet at three"), so the AI should hear both together.
    Needs AI self-corrections on and a phrase of this recording typed."""
    if chunk_role is None or not (message.has_words and message.complete):
        return False
    if not (live.ai_corrections and live.ai_engine is not None
            and rs.state.last_inserted_text.strip()):
        return False
    if rs.state.held_cue:
        return True
    from . import ai_cleanup
    return ai_cleanup.opens_with_correction(rs.state.last_inserted_text, raw)


def _erase_typed(text: str) -> bool:
    """Backspace over *text*, just typed by the phrase before the pause. False
    when it can't be done safely; then nothing more is erased."""
    focused = focused_window.window_class()
    if keyboard_output.not_where_typed(focused):
        logger.info("Correction across a pause skipped: the phrase is in another window")
        return False
    # The AI that found the correction can take seconds, time enough to
    # click into a password field of the same window.
    if focused_window.is_password():
        logger.info("Correction across a pause skipped: a password field has the focus")
        return False
    try:
        deleted = keyboard_output.output_backend().backspaces(len(text))
    except virtual_keyboard.StoppedPartway:
        logger.error("Correction across a pause stopped partway; typing the phrase as said")
        rs.state.last_inserted_text = ""
        last_dictation.forget_shown()
        notify_module.notify("A correction stopped partway", "Check the text before it.")
        return False
    if deleted:
        last_dictation.trim_shown(text, len(text))
        rs.state.last_inserted_text = ""
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
    prev = rs.state.last_inserted_text
    cue, rs.state.held_cue = rs.state.held_cue, ""
    if cue and cue[-1] not in ".?!,;:":
        cue += ","
    if (cue.endswith(",") and text[:1].isupper()
            and safe_to_lowercase_first_word(text, live.words)):
        text = text[0].lower() + text[1:]      # "No wait, four", not "Four"
    said = f"{cue} {text}" if cue else text
    joined = prev + ("" if prev[-1:].isspace() else " ") + said
    fixed = ai_cleanup.fix_self_corrections(joined, live.ai_engine)
    if fixed == joined or not fixed.strip():
        return said, ""
    if not _erase_typed(prev):
        return said, ""
    logger.info("AI cleanup fixed a self-correction across a pause")
    return fixed, prev


def _type_boundary(pending: dict, tail: str, injection_mode: str) -> None:
    """Type what goes between a held-back chunk and what follows it, as part
    of the message the chunk is in."""
    if not tail:
        return
    last_dictation.remember_said(tail, chunked=True)
    # Only into the phrase's window: the context pass before this can take a while.
    ok = _inject_text(tail, injection_mode, into=pending["where"],
                      message=pending["message"]) == "ok"
    last_dictation.remember_shown(tail, chunked=True, ok=ok)
    if ok:
        # Undo and "delete last sentence" then see the whole chunk.
        rs.state.last_inserted_text = pending["typed"] + tail


# A phrase this short that the context pass hears as "press enter" is taken
# as "press enter": alone it's barely a second of audio, and Parakeet heard
# "Press center." where both together read "... to say. Press enter."
ENTER_IN_CONTEXT_MAX_WORDS = 3
# A phrase this short (seconds) is typed as the context pass heard it when
# that pass heard fewer words: alone, the engine can make a sentence up
# around a word or two (chunking.words_in_context).
SHORT_CHUNK_S = 2.0


def _resolve_boundary(audio_f32, language, injection_mode, raw="", auto_period=True) -> str:
    """The next chunk has arrived: decide the mark held back after the last
    one, from the engine hearing both chunks together, and type it. Returns
    the chunk's text: "press enter" when only the context pass heard that,
    and for a very short chunk the words the context pass heard."""
    pending, rs.state.pending_boundary = rs.state.pending_boundary, None
    if focused_window.changed(pending["where"], focused_window.look()):
        # The user switched windows during the pause: that phrase's mark
        # belongs at its end, in the other window, not at the start of this
        # one ("? What's next?"). This window starts afresh.
        logger.info("Chunk boundary: another window has the focus now; "
                    "its mark stays with its phrase")
        rs.state.continue_mid_sentence = False
        return raw
    mark = pending["mark"]
    joint = ""
    try:
        gap = np.zeros(int(JOINT_GAP_S * engine.SAMPLE_RATE), dtype=np.float32)
        joint = engine.transcribe(np.concatenate(
            [pending["audio"], gap, audio_f32[:_context_samples()]]), language) or ""
        found = boundary_mark(pending["raw"], joint)
        logger.info(f"Chunk boundary: {pending['mark']!r} alone, {found!r} in context")
        if found is not None:
            mark = found
    except Exception as e:
        logger.warning(f"Couldn't hear the chunk boundary in context: {e}")
    if (raw and len(raw.split()) <= ENTER_IN_CONTEXT_MAX_WORDS
            and not split_key_command(raw)[1] and split_key_command(joint)[1] == "ENTER"):
        logger.info(f"Heard \"press enter\" in context ({live.loggable(raw)} alone)")
        raw = "press enter"
        # The sentence ends here, as it would after "press enter" said
        # clearly (_flush_boundary): its mark, and no space to send.
        mark = mark or ("." if auto_period else "")
        _type_boundary(pending, mark, injection_mode)
        rs.state.continue_mid_sentence = False
        return raw
    if joint and len(audio_f32) <= SHORT_CHUNK_S * engine.SAMPLE_RATE:
        heard = words_in_context(pending["raw"], joint) or ""
        # Whether to press a key stays the rule above's call.
        if (0 < word_count(heard) < word_count(raw)
                and split_key_command(heard)[1] == split_key_command(raw)[1]):
            logger.info(f"Short phrase: {live.loggable(raw)} alone, {live.loggable(heard)} "
                        "in context; typing what the context heard")
            raw = heard
    _type_boundary(pending, (mark + " ") if mark else " ", injection_mode)
    # Lowercase on after a comma or no mark; a sentence end starts afresh.
    rs.state.continue_mid_sentence = mark not in _SENTENCE_ENDS
    # "do you think ... feature" (pause) "to make it work better": the engine
    # heard a question before the pause and a sentence going on across it,
    # so the question ends where this phrase does.
    rs.state.question_open = pending["mark"] == "?" and mark not in _SENTENCE_ENDS
    return raw


def _flush_boundary(auto_period: bool, auto_space: bool, injection_mode: str) -> None:
    """The dictation ended in a pause: type the mark held back after its last
    chunk, or the auto-period when the engine wrote none, in its window only."""
    pending, rs.state.pending_boundary = rs.state.pending_boundary, None
    if pending is None:
        return
    now = focused_window.look()
    if focused_window.changed(pending["where"], now):
        return  # the end of this sentence isn't in the window that has focus
    kind = app_kind(now[0]) if live.adapt_to_app else ""
    mark = pending["mark"] or ("." if auto_period and not kind else "")
    _type_boundary(pending, mark + (" " if auto_space and not kind else ""), injection_mode)


def _start_afresh(ended=None) -> None:
    """Type at pause pressed Enter or Tab mid-recording, or "edit that"
    rewrote what was typed: what's said next reaches back across nothing (no
    held-back mark, correction, lowercase start or screen copy). After Enter
    or Tab the message *ended* starts over; a rewrite stays in the unsent one."""
    if ended is not None:
        ended.start_over()
    rs.state.pending_boundary = None
    rs.state.question_open = False
    rs.state.held_cue = ""
    rs.state.continue_mid_sentence = False
    if rs.state.shown_text is not None and rs.state.shown_serial == rs.state.recording_serial:
        rs.state.shown_text = ""


def _press_said(key: str, chunk_role, after_words: bool = False, message=None) -> None:
    """Press the key said at the end of a dictation ("press enter"). In Type
    at pause that ends the message, and what's said next starts afresh; not
    pressed (a key stuck down), the message is still in the box, to carry
    on in and for "edit that" to reach whole. In Claude Desktop "press tab"
    types four spaces, as a tab said mid-sentence does there: its Tab key
    jumps to the Send button, and the next space typed would press it; after
    words, they go where the Tab would. A password field there gets the
    real Tab: spaces would be refused.

    *after_words*: words of *message* (the recording's) were delivered
    first, or tried, and the key goes only where they went, and only if all
    of them did (send_countdown.press_where_sent), as an automatic Enter
    does. Said alone it goes wherever the focus is: people type a password
    by hand, then say "press enter" to log in."""
    why = f'said "press {key.lower()}"'
    if (key == "TAB" and focused_window.window_class() in ELECTRON_PASTE_BROKEN_CLASSES
            and not focused_window.is_password()):
        outcome = (send_countdown.type_where_sent("    ", key, message, _type_onto_dictation)
                   if after_words else _type_onto_dictation("    ", "type"))
        if outcome not in (None, "ok"):
            logger.warning(f"Tab not typed as four spaces ({outcome})")
            if outcome == "key_held":
                notify_module.notify(*delivery_notices.key_not_pressed(key))
        return
    if after_words:
        pressed = send_countdown.press_where_sent(key, why, message, keyboard_output.press_key)
    else:
        pressed = keyboard_output.press_key(key, why)
    if pressed and chunk_role is not None:
        _start_afresh(message)


def _after_the_words(outcome: str, said: str, press: str, space: str, chunk_role,
                     beeps_on: bool, injection_mode: str, message) -> None:
    """What follows a dictation's words, by _inject_text's *outcome*: a
    notice when they didn't all go in; else the key *said* after them, or
    the automatic send's Enter when *press*. *space* is the auto-space left
    off before the key."""
    if outcome in delivery_notices.NOTICES:
        notices.report_undelivered(outcome, beeps_on)
    elif said:
        _press_said(said, chunk_role, after_words=True, message=message)
    elif press:
        _send_automatically(space, injection_mode, message)
        if chunk_role is not None:
            _start_afresh(message)


def _send_automatically(space: str, injection_mode: str, message) -> None:
    """"Send each dictation automatically": Enter where *message* went (send_countdown)."""
    send_countdown.send_automatically("Send each dictation automatically is on", message,
                                      keyboard_output.press_key, _type_onto_dictation, space,
                                      injection_mode)


# _inject_text's outcomes that are sure nothing was typed: it gave up before
# the first key (_delivery_stopped). "failed" may follow some of it (wtype
# timing out), and "password_field" earlier lines.
_GAVE_UP_UNTYPED = frozenset({"moved", "key_held"})


def _type_onto_dictation(text: str, injection_mode: str, into=None) -> str:
    """Type *text* right after the dictation, keeping undo's record of it
    whole; _inject_text's outcome. *into* is _inject_text's. When none of
    it went in (a space refused because the focus moved), the dictation is
    in the app as it was, and so is everything that remembers it."""
    kept = (rs.state.last_inserted_text, rs.state.continue_mid_sentence, rs.state.nav_context,
            rs.state.shown_text, rs.state.last_inserted_window, rs.state.last_inserted_where)
    outcome = _inject_text(text, injection_mode, into=into)
    if outcome == "ok":
        rs.state.last_inserted_text = kept[0] + text
    elif outcome in _GAVE_UP_UNTYPED:
        # The window too: typing takes it for the dictation's before the first key.
        (rs.state.last_inserted_text, rs.state.continue_mid_sentence, rs.state.nav_context,
         rs.state.shown_text, rs.state.last_inserted_window, rs.state.last_inserted_where) = kept
    return outcome


def _end_in_a_pause(message, beeps_on, auto_space, auto_period, injection_mode) -> None:
    """Type at pause's recording ended in a pause, with nothing said after
    it: a held "no wait" is typed as said, the last phrase gets its mark,
    and "Send each dictation automatically" sends *message* if all of it
    went in."""
    waiting = rs.state.edit_waiting
    # "edit that", a pause, and nothing more: what to change never came, and
    # the message isn't sent as it was.
    unfinished_edit = waiting is not None and waiting["serial"] == rs.state.recording_serial
    if unfinished_edit:
        rs.state.edit_waiting = None
        notices.edit_problem("edit_that_what", beeps_on)
        message.add("failed", None)
    if rs.state.held_cue:
        cue, rs.state.held_cue = rs.state.held_cue, ""
        if auto_period and cue[-1] not in ".?!":
            cue += "."
        _inject_text(cue + (" " if auto_space else ""), injection_mode, message=message)
    if (live.press_enter_after and message.has_words and message.complete
            and not is_document_window(focused_window.window_class())):
        _flush_boundary(auto_period, False, injection_mode)
        _send_automatically(" " if auto_space else "", injection_mode, message)
        _start_afresh(message)
    else:
        _flush_boundary(auto_period, auto_space, injection_mode)
    if not live.chunk_context_marks:
        rs.state.continue_mid_sentence = False   # it belongs to this recording
    if not unfinished_edit:                   # that one beeped its cancel
        beeps.beep(beeps_on, *READY_BEEP)


def _transcribe_and_inject(frames, rec_sr, beeps_on, smart_quotes, notify_on,
                           language=None, auto_space=True, auto_period=True,
                           injection_mode="type", chunk_role=None, message=None):
    """The slow half of stopping: transcribe the captured audio and inject the
    text. It touches no GTK (the indicator was already hidden in _finish_capture)
    and uses only CPU + audio + D-Bus + the output backend, so it is safe on a
    worker thread, which Backend B uses so the hotkey loop stays live.

    *chunk_role* is set for chunked dictation (chunking.py): "middle" for a
    chunk cut at a pause while recording goes on (no beep, no notice, silence
    dropped quietly), "last" for the rest after the stop (the usual READY beep,
    even when it holds no speech, since earlier chunks were typed).

    *message* is the recording's (send_countdown.Message), taken when its
    work was queued, so a recording started meanwhile can't change where
    this one's Enter may go. By default this recording's."""
    message = rs.state.message if message is None else message
    print("Recording stopped. Transcribing…")
    t0 = time.time()
    logger.info("Recording stopped, starting transcription")

    def _nothing_in_chunk(what: str) -> bool:
        """For a chunk, handle an empty or silent one; False for a whole dictation."""
        if chunk_role is None:
            return False
        logger.info(f"Chunk ({chunk_role}) {what}; nothing typed for it")
        if chunk_role == "last":
            _end_in_a_pause(message, beeps_on, auto_space, auto_period, injection_mode)
        return True

    try:
        audio_f32 = _frames_f32(frames)
        if audio_f32.size == 0:
            if _nothing_in_chunk("had no audio"):
                return
            print("(No audio captured)")
            return
        if rec_sr != engine.SAMPLE_RATE:
            audio_f32 = _resample_audio(audio_f32, rec_sr, engine.SAMPLE_RATE)

        # A second or more of pure digital silence: the microphone is muted,
        # unplugged or dead, not a quiet room. Say so instead of typing nothing.
        if silence.is_dead_mic(audio_f32, engine.SAMPLE_RATE):
            beeps.beep(beeps_on, *CANCEL_BEEP)
            notices.warn_silent_mic()
            return

        # A quick tap of the key with nothing said: speech engines can invent
        # a word ("Thank you.") in a fraction of a second of room noise.
        # Both stop with the cancel beep, as a too-short F8 tap and the
        # hands-free "nothing said" stop do: silence there read as broken.
        if silence.is_speechless_tap(audio_f32, engine.SAMPLE_RATE):
            if _nothing_in_chunk("had no speech"):
                return
            print("(Short tap with no speech; nothing typed)")
            logger.info(f"Skipped {len(audio_f32) / engine.SAMPLE_RATE:.2f}s with no speech in it")
            beeps.beep(beeps_on, *CANCEL_BEEP)
            return

        # Stage 1: Transcribe audio → raw text
        raw = engine.transcribe(audio_f32, language)
        logger.info(f"TIMING: Transcription pipeline took {time.time() - t0:.2f}s")
        if chunk_role is not None:
            logger.info(f"Chunk ({chunk_role}): {len(audio_f32) / engine.SAMPLE_RATE:.1f}s, "
                        f"raw: {live.loggable(raw)}")
        if not raw:
            if _nothing_in_chunk("was not recognised"):
                return
            print("(No speech recognized)")
            logger.info("Nothing recognized, so nothing was typed")
            beeps.beep(beeps_on, *CANCEL_BEEP)
            return

        rest, said = split_key_command(raw)
        key_only = not rest.strip() and said
        # A password field gets nothing: not the words, nor what Type at
        # pause would type first (the mark held back from the last phrase, a
        # held "No wait.", backspaces for a correction), and the local AI
        # never sees them. Only "press enter" or "press tab" on its own still
        # presses the key, there and not where the phrases before went:
        # people type the password by hand, then say it to log in, or move
        # between the boxes.
        in_password = focused_window.is_password()
        if in_password:
            rs.state.pending_boundary, rs.state.held_cue = None, ""
            if not key_only:
                notices.password_field_refused(beeps_on)
                return

        # Chunked dictation: the chunk before this one held back its final
        # mark; decide it now, before a command acts on what was typed.
        # Only "press tab" after the pause leaves the box without one.
        if chunk_role is not None and rs.state.pending_boundary is not None:
            if key_only == "ENTER":
                # Only "press enter" after the pause: the sentence before it
                # ends here (its held-back mark, or the auto-period), with no
                # space to go out in the sent message.
                _flush_boundary(auto_period, False, injection_mode)
            elif not key_only:
                raw = _resolve_boundary(audio_f32, language, injection_mode, raw, auto_period)

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

        # "edit that, make it shorter": voice editing without its key, on
        # what was just typed (TuxTalk Labs, with voice editing on).
        if _handle_spoken_edit(raw, chunk_role, beeps_on, injection_mode, message):
            return

        # "... press enter": type the words, then a real Enter that sends the
        # message or runs the command; "... press tab" moves on to the next
        # box (key_commands.py). Or Enter every time, when "Send each
        # dictation automatically" is on, but only at the end of the
        # recording, not each phrase of Type at pause.
        raw, said = split_key_command(raw)
        press, cut_off = said, ""
        if not said and live.press_enter_after and chunk_role != "middle":
            sending, cut_off = auto_send(raw, is_document_window(focused_window.window_class()))
            press = "ENTER" if sending else ""
        if said and not raw.strip():
            # Only "press enter" or "press tab" was said.
            if chunk_role != "middle":
                beeps.beep(beeps_on, *READY_BEEP)
            _press_said(said, chunk_role, after_words=message.has_words and not in_password,
                        message=message)
            return

        # Stage 3: Normalize text (voice commands, punctuation, spacing),
        # plainer for a terminal or code editor ("Plain text in terminals and
        # code editors").
        kind = app_kind(focused_window.window_class()) if live.adapt_to_app else ""
        # A pause is not a sentence end, so a middle chunk gets no auto-period
        # of its own; the last chunk (or _flush_boundary) ends the sentence.
        # "press enter" at the end of one does end it: the message is sent.
        # Cut off on "and": the sentence isn't over either, so no period, and
        # what's said next carries it on in lowercase. A form box left with
        # "press tab" gets none: "John", not "John.".
        chunk_period = (auto_period and (chunk_role != "middle" or said == "ENTER")
                        and not cut_off and said != "TAB")
        # A correction said after a pause ("meet at three" ... "no wait,
        # four") goes to the AI together with the phrase it corrects.
        across = _corrects_across_pause(raw, chunk_role, message)
        text = _prepare_text(raw, smart_quotes, chunk_period, auto_space, app_kind=kind,
                             ai=not across)
        if cut_off:
            # The engine may have put its own period after the "and".
            text = re.sub(r"\.(\s*)$", r"\1", text)
            rs.state.continue_mid_sentence = True
        taken_back = ""
        if across:
            from . import ai_cleanup
            if chunk_role == "middle" and ai_cleanup.is_bare_cue(text):
                # Only "No wait.": the corrected value is in the next phrase.
                rs.state.held_cue = text.strip()
                logger.info("Chunk is only a correction cue; waiting for the next phrase")
                return
            text, taken_back = _correct_across_pause(text)
        if rs.state.question_open and chunk_role is not None:
            rs.state.question_open = False
            text = carry_question(text)
        held_mark = None
        if chunk_role == "middle" and live.chunk_context_marks and not said:
            text, held_mark = hold_back_boundary(text, raw)
        space_left_off = ""
        if press:
            # No auto-space before Enter or Tab: it would end up in the sent
            # message, or at the end of the box.
            # Kept for a send that's held, which types it after all.
            core = text.rstrip(" ")
            space_left_off, text = text[len(core):], core

        # Again before the beep and the "Transcribed" notification, which
        # shows the words: the focus may have moved while the AI cleaned them.
        if text and notices.refuse_password_field(beeps_on):
            return

        if chunk_role != "middle":
            beeps.beep(beeps_on, *READY_BEEP)
            if notify_on: notify_module.notify("Transcribed", _shortened(text, 80))

        # Stage 4: Inject text into the active application. It goes into the
        # Recent Dictations list first, so it can be recovered even if the
        # injection lands in the wrong window or fails outright.
        if text:
            text = send_countdown.with_owed_space(text)
            chunked = chunk_role is not None
            said_before = last_dictation.remember_said(text, chunked, taken_back)
            try:
                outcome = _inject_text(text, injection_mode, message=message)
            except Exception as e:
                # The transcription worked; only getting it into the window
                # didn't, so this is reported as undelivered, not as the
                # "Transcription error" below.
                logger.error(f"Injection error: {e}", exc_info=True)
                outcome = "failed"
                message.add(outcome, None)
            if outcome == "password_field":
                last_dictation.unsay(said_before)
            last_dictation.remember_shown(rs.state.last_inserted_text, chunked, outcome == "ok")
            _after_the_words(outcome, said, press, space_left_off, chunk_role, beeps_on,
                             injection_mode, message)
            if chunk_role == "middle" and not live.chunk_context_marks and outcome == "ok" \
                    and not press:
                # Without the context pass the engine's own mark decides: after
                # a comma or none, the next chunk carries on lowercase.
                rs.state.continue_mid_sentence = not text.rstrip().endswith(_SENTENCE_ENDS)
            if held_mark is not None and outcome == "ok":
                # Only the end of the phrase is kept: the engine hears it with
                # the start of the next one to decide the mark between them.
                rs.state.pending_boundary = {"mark": held_mark,
                                          "audio": audio_f32[-_context_samples():],
                                          "raw": raw, "typed": rs.state.last_inserted_text,
                                          "where": rs.state.last_inserted_where,
                                          "message": message}
            if live.usage_stats:
                # After typing, so saving the counts never delays the text.
                # Counts only (words, seconds dictating); see stats.py.
                from .stats import count_words, record
                record(count_words(text), len(audio_f32) / engine.SAMPLE_RATE)

    except Exception as e:
        logger.error(f"Transcription error: {e}", exc_info=True)
        # The cancel beep and a notice whatever the notifications setting,
        # like _report_undelivered: nothing was typed, and the "done" beep
        # would sound like it worked.
        beeps.beep(beeps_on, *CANCEL_BEEP)
        notify_module.notify("Transcription failed", _shortened(str(e), 120))


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
    edit_mode, rs.state.edit_mode = rs.state.edit_mode, False
    cap = _finish_capture(beeps_on, notify_on)
    if cap is None:
        return
    frames, rec_sr = cap
    if edit_mode:
        _transcribe_and_edit(frames, rec_sr, beeps_on, language, injection_mode)
        return
    if rs.state.chunks_submitted:
        # Chunked dictation: the rest goes in after the chunks already queued,
        # and stopping waits for all of them, as it waits for one
        # transcription when nothing was chunked.
        params = (beeps_on, smart_quotes, notify_on, language, auto_space,
                  auto_period, injection_mode)
        frames = _drop_speechless_rest(frames)
        worker = _chunk_worker()
        worker.submit(lambda message=rs.state.message: _transcribe_and_inject(
            frames, rec_sr, *params, chunk_role="last", message=message))
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



def _focus_moved(focused_class) -> bool:
    """Whether another window has the focus than when the edit started. Only
    the class is known, so a second window of the same app is not noticed.
    A window that can't be found now counts as moved: it can't be checked."""
    now = focused_window.window_class()
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
    focused = focused_window.window_class()
    typed = rs.state.last_inserted_text
    if typed and rs.state.shown_text and rs.state.shown_text.endswith(typed):
        typed = rs.state.shown_text        # the whole recording, phrases and all
    if typed.strip() and not keyboard_output.not_where_typed(focused):
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
            rs.state.last_inserted_text = ""
            last_dictation.forget_shown()
            rs.state.nav_context = None
        except OSError as e:
            logger.debug(f"Fix a Word can't fix it in the text: {e}")
    try:
        from .explain import start_helper_window
        start_helper_window("tuxtalk.fix_word_dialog", *args)
        beeps.beep(beeps_on, *READY_BEEP)
    except Exception as e:
        logger.warning(f"Could not open Fix a Word: {e}")
        beeps.beep(beeps_on, *CANCEL_BEEP)
        notify_module.notify("Fix a Word didn't open", "Choose it in the TuxTalk menu instead.")
    return True


_UNKNOWN_WINDOW = object()


def _handle_navigation(raw: str, beeps_on: bool, focused_class=_UNKNOWN_WINDOW) -> bool:
    """Select and move by voice (TuxTalk Labs). True when the whole dictation
    was one of voice_navigation's commands, done or refused with a notice;
    False to type it as text."""
    if not live.voice_navigation:
        return False
    from . import voice_navigation as nav
    command = nav.parse(raw)
    if command is None:
        return False
    logger.info(f"Voice command: {command.name}")
    if focused_class is _UNKNOWN_WINDOW:
        focused_class = focused_window.window_class()
    if is_terminal_class(focused_class):
        notices.edit_problem("nav_terminal", beeps_on)
        return True
    # Right after a dictation the cursor is at its end; after an exact move,
    # nav_context says where in it. Only in the window it was typed into:
    # dictating into a chat, then saying "select the last sentence" in Writer
    # would otherwise select the chat message's length of Writer's text.
    if keyboard_output.not_where_typed(focused_class):
        left, right = None, ""
    elif rs.state.last_inserted_text:
        left, right = rs.state.last_inserted_text, ""
    else:
        left, right = rs.state.nav_context or (None, "")
    plan = nav.plan(command, left, right)
    if plan is None:
        # One message for "just restarted" and "dictated somewhere else since":
        # both come down to the one rule (it teaches the rule, too).
        notices.edit_problem("nothing_said", beeps_on)
        return True
    try:
        pressed = keyboard_output.output_backend().press_keys(plan.chords)
    except Exception as e:
        logger.error(f"Voice command: pressing the keys failed: {e}")
        pressed = False
    # The cursor has moved (or may have): undo's character count no longer
    # matches the document, and "delete last word" would eat the wrong text.
    rs.state.last_inserted_text = ""
    last_dictation.forget_shown()
    rs.state.nav_context = (plan.left, plan.right) if pressed and plan.left is not None else None
    if pressed:
        beeps.beep(beeps_on, *READY_BEEP)
    else:
        notices.edit_problem("shortcut_failed", beeps_on)
    return True


def _spoken_edit_target():
    """What "edit that" changes: this recording's text on screen (every
    phrase since the last Enter, with Type at pause), else the last
    dictation; "" when there's nothing fresh in the focused window."""
    typed = rs.state.last_inserted_text
    if not typed or not typed.strip():
        return ""
    focused = focused_window.window_class()
    if keyboard_output.not_where_typed(focused):
        return ""
    shown = rs.state.shown_text
    if shown and rs.state.shown_serial == rs.state.recording_serial and shown.endswith(typed):
        return shown
    return typed


# "edit that" this soon after TuxTalk pressed Enter meant the sent dictation.
EDIT_AFTER_SEND_S = 60


def _refused_as_sent(beeps_on: bool) -> bool:
    """"edit that" with nothing just typed: True when TuxTalk pressed Enter a
    moment ago, said with a notice (typing "Edit that, make it formal" into
    the box would only add to the confusion); False to type the words."""
    sent = rs.state.sent
    if sent is None or time.time() - sent["at"] >= EDIT_AFTER_SEND_S:
        return False
    notices.edit_problem("already_sent_auto" if sent["automatic"] else "already_sent", beeps_on)
    return True


def _handle_spoken_edit(raw: str, chunk_role, beeps_on: bool, injection_mode: str,
                        message) -> bool:
    """Voice editing by voice alone (TuxTalk Labs): a dictation that starts
    with "edit that" selects what TuxTalk just typed and changes it as the
    rest says, as Shift+F8 does for a selection. True when it was handled
    (done, or refused with a notice); False to type the words as text, which
    is also what happens when nothing was just typed to edit."""
    if not live.voice_editing:
        return False
    from . import voice_edit_rules
    from . import voice_navigation as nav
    waiting, rs.state.edit_waiting = rs.state.edit_waiting, None
    if waiting is not None and waiting["serial"] == rs.state.recording_serial:
        instruction, target = raw, waiting["target"]
    else:
        instruction = voice_edit_rules.spoken_edit(raw)
        if instruction is None:
            return False
        target = _spoken_edit_target()
        if not target:
            return _refused_as_sent(beeps_on)
    logger.info(f"Voice command: edit that ({len(target)} characters)")
    if not keyboard_output.is_wayland_session():
        notices.edit_problem("needs_wayland", beeps_on)
        return True
    focused_class = focused_window.window_class()
    if is_terminal_class(focused_class):
        notices.edit_problem("terminal", beeps_on)
        return True
    if not instruction.strip():
        if chunk_role == "middle":
            # "edit that", then a pause: the next phrase says what to change.
            rs.state.edit_waiting = {"serial": rs.state.recording_serial, "target": target}
            logger.info("Voice edit: waiting for the next phrase to say what to change")
        else:
            notices.edit_problem("edit_that_what", beeps_on)
            message.add("failed", None)     # asked to change it: not sent as it is
        return True
    plan = nav.plan(nav.parse("select that"), target, "")
    try:
        selected = plan is not None and keyboard_output.output_backend().press_keys(plan.chords)
    except Exception as e:
        logger.error(f"Voice edit: selecting the text failed: {e}")
        selected = False
    # The text is selected now (and then replaced): undo's count and the
    # screen copy no longer match it, and nothing in it reaches the next phrase.
    rs.state.last_inserted_text = ""
    _start_afresh()
    last_dictation.forget_shown()
    if not selected:
        notices.edit_problem("shortcut_failed", beeps_on)
    elif _edit_selection(lambda: instruction, beeps_on, injection_mode, focused_class,
                         time.time(), message, spoken=True):
        return True
    # Not changed as asked: no automatic Enter sends it as it was.
    message.add("failed", None)
    return True


def _transcribe_and_edit(frames, rec_sr, beeps_on, language, injection_mode):
    """Voice editing: the recording is an instruction ("make this more formal")
    for the text selected in the focused app, which the AI rewrites and the
    rewrite replaces. Ctrl+Z in the app undoes it."""
    t0 = time.time()
    audio_f32 = _frames_f32(frames)
    if audio_f32.size == 0:
        return
    if rec_sr != engine.SAMPLE_RATE:
        audio_f32 = _resample_audio(audio_f32, rec_sr, engine.SAMPLE_RATE)
    if silence.is_speechless_tap(audio_f32, engine.SAMPLE_RATE):
        return

    if not keyboard_output.is_wayland_session():
        # The selection is read with wl-copy / wl-paste, which have no X11
        # side; without this every edit would say "select some text first".
        notices.edit_problem("needs_wayland", beeps_on)
        return
    focused_class = focused_window.window_class()
    if is_terminal_class(focused_class):
        notices.edit_problem("terminal", beeps_on)
        return
    heard = []

    def instruction():
        if not heard:
            heard.append(engine.transcribe(audio_f32, language))
        return heard[0]
    _edit_selection(instruction, beeps_on, injection_mode, focused_class, t0, rs.state.message)


def _delete_selection(focused_class, beeps_on) -> bool:
    """"Delete this": Delete on the selection, still selected in
    *focused_class*. True when it was pressed. Transcribing can take
    seconds, and Delete must not reach a window the user has switched to
    meanwhile, so it needs a window to compare with."""
    if focused_class is None:
        logger.info("Voice edit: the focused window is unknown, so Delete can't be checked")
        notices.edit_problem("cant_check_window", beeps_on)
        return False
    if _focus_moved(focused_class):
        notices.edit_problem("focus_moved", beeps_on)
        return False
    if notices.refuse_password_field(beeps_on):
        return False
    if not keyboard_output.send_keys([(111, 1), (111, 0)], what="delete"):     # KEY_DELETE
        notices.edit_problem("shortcut_failed", beeps_on)
        return False
    logger.info("Voice edit: deleted the selection")
    beeps.beep(beeps_on, *READY_BEEP)
    return True


def _format_selection(fmt, focused_class, beeps_on) -> bool:
    """Bold, italic or underline (*fmt*, voice_edit_rules.formatting) with
    the app's own shortcut on the selection, still selected. True when it
    was pressed."""
    from . import voice_edit_rules
    if not voice_edit_rules.has_formatting_shortcuts(focused_class):
        logger.info(f"Voice edit: no formatting shortcuts in {focused_class!r}")
        notices.edit_problem("no_shortcuts", beeps_on)
        return False
    if _focus_moved(focused_class):
        notices.edit_problem("focus_moved", beeps_on)
        return False
    if notices.refuse_password_field(beeps_on):
        return False
    if not all(keyboard_output.send_format_shortcut(kind) for kind in fmt):
        notices.edit_problem("shortcut_failed", beeps_on)
        return False
    logger.info(f"Voice edit: {', '.join(fmt)} with the app's shortcut ({focused_class})")
    beeps.beep(beeps_on, *READY_BEEP)
    return True


def _edit_selection(get_instruction, beeps_on, injection_mode, focused_class, t0, message,
                    spoken=False):
    """Change the text selected in *focused_class* as the instruction
    *get_instruction()* says (called only once a selection is found), and
    type the result over it, as part of *message*. *spoken*: the instruction
    came after "edit that" in a dictation, which selected the text itself.
    True when the change went in."""
    from . import ai_cleanup, clipboard, history, voice_edit_rules
    # Before copying the selection or hearing the instruction; checked
    # again before the rewrite replaces it.
    if notices.refuse_password_field(beeps_on):
        return
    # The selection first, so "select some text first" comes up even when
    # nothing was said.
    try:
        selected, put_back = clipboard.copy_selection(keyboard_output.send_copy_keystroke)
    except clipboard.ClipboardUnavailable:
        notices.edit_problem("no_clipboard", beeps_on)
        return
    if not selected:
        # "select the last sentence" needs nothing selected: it makes the selection the
        # next edit works on. The clipboard is already back (copy_selection does that).
        if live.voice_navigation and not spoken:
            said = get_instruction()
            if said and _handle_navigation(said, beeps_on, focused_class):
                return
        notices.edit_problem("no_selection", beeps_on)
        return
    result = None
    try:
        instruction = get_instruction()
        if not instruction:
            notices.edit_problem("no_instruction", beeps_on)
            return
        if _handle_navigation(instruction, beeps_on, focused_class):
            return
        logger.info(f"Voice edit: {live.loggable(instruction)} ({len(selected)} characters)")
        # Deleting, formatting and exact jobs need no AI (voice_edit_rules.py).
        if voice_edit_rules.is_delete(instruction):
            return _delete_selection(focused_class, beeps_on)
        fmt = voice_edit_rules.formatting(instruction)
        if fmt == voice_edit_rules.CANT_FORMAT:
            notices.edit_problem("cant_format", beeps_on)
            return
        if fmt:
            return _format_selection(fmt, focused_class, beeps_on)
        # Deleting and formatting press keys only, so any length will do; a
        # rewrite is typed back, and the AI's context holds only so much.
        if len(selected) > ai_cleanup.MAX_EDIT_CHARS:
            notices.edit_problem("too_long", beeps_on)
            return
        # "make it formal and remove the commas" is two changes, made one
        # after the other, each in code or by the AI (split_steps).
        steps = voice_edit_rules.split_steps(instruction)
        if len(steps) > 1:
            logger.info(f"Voice edit: {len(steps)} changes, one at a time")
        engine = live.ai_engine
        text = selected
        for step in steps:
            done = voice_edit_rules.mechanical_edit(text, step)
            if done is not None:
                logger.info("Voice edit done in code, without the AI"
                            + (f" ({live.loggable(step)})" if len(steps) > 1 else ""))
            elif engine is None:
                notices.edit_problem("not_installed", beeps_on)
                return
            elif not engine.is_ready():
                notices.edit_problem("not_ready", beeps_on)
                return
            else:
                done = engine.edit(text, step)
                if done and done.split() == text.split():
                    # Only spacing changed ("select all" got the line ends
                    # trimmed): not an edit anyone asked for.
                    done = None
                if done is None and len(steps) > 1:
                    # One change the AI had nothing for needn't cost the others.
                    logger.info(f"Voice edit: no change for {live.loggable(step)}")
            if done is not None:
                text = done
        result = text if text != selected else None
        if result and live.swiss_spelling:
            result = swiss_spelling(result)
        if result:
            result = _numbers_to_digits(result)
        # Exact: for an edit done in code, spacing may be the whole change
        # (indenting one line); the AI's spacing-only answers are dropped above.
        if not result or result == selected:
            notices.edit_problem("no_answer", beeps_on)
            return
        if notices.refuse_password_field(beeps_on):
            return
        logger.info(f"Voice edit result: {live.loggable(result)} "
                    f"(took {time.time() - t0:.2f}s)")
        history.add_entry(result)
        if focused_class is None:
            # Typed unchecked, as ordinary dictation is: refusing here would
            # turn voice editing off wherever the window can't be found.
            logger.info("Voice edit: the focused window is unknown, so typing goes ahead unchecked")
        elif _focus_moved(focused_class):
            # Typing now would put the rewrite into whatever window has focus.
            notices.edit_problem("window_changed", beeps_on)
            return
        # The selection is still active, so typing or pasting replaces it, in
        # one block (see _inject_text).
        try:
            outcome = _inject_text(result, injection_mode, one_block=True, message=message)
        except Exception as e:
            logger.error(f"Voice edit: typing the rewrite failed: {e}", exc_info=True)
            outcome = "failed"
            message.add(outcome, None)      # what's in the box isn't known now
        if outcome == "password_field":
            history.forget(result)
        if outcome in delivery_notices.NOTICES:
            notices.report_undelivered(outcome, beeps_on)
        else:
            beeps.beep(beeps_on, *READY_BEEP)
        return outcome == "ok"
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


def _held_shift(held_modifiers) -> set:
    """The Shift keys among *held_modifiers*: Shift + the dictation key starts
    a voice edit instead of a dictation."""
    return set(held_modifiers) & _SHIFT_CODES


def _someone_elses_shortcut(held_modifiers) -> bool:
    """A dictation key pressed with modifiers the desktop doesn't hold back
    for us (kwin_hotkeys.wanted_shortcuts: the plain key, and Shift with it
    when voice editing is on). Such a press reaches the app (Alt+F8 resizes
    the window on KDE and GNOME), so it mustn't start a dictation as well."""
    allowed = _SHIFT_CODES if live.voice_editing else frozenset()
    return bool(set(held_modifiers) - allowed)


def _paste_last_dictation(cfg) -> None:
    """Type the newest Recent Dictations entry again, the way a dictation is
    typed (line breaks, injection mode, the trailing space)."""
    from .history import get_entries, _LINE_BREAK_MARKER
    entries = get_entries()
    if not entries:
        logger.info("Paste last dictation: nothing dictated yet")
        beeps.beep(cfg.beeps, *CANCEL_BEEP)
        return
    text = entries[0].replace("\n", _LINE_BREAK_MARKER)
    if getattr(cfg, "auto_space", True):
        text += " "
    logger.info("Paste last dictation")
    outcome = _inject_text(text, cfg.injection_mode)
    if outcome in delivery_notices.NOTICES:
        notices.report_undelivered(outcome, cfg.beeps)


def _report_test_press(code, hold_key, toggle_key) -> None:
    """In the hotkey test, tell the tray which dictation key *code* is."""
    if code == hold_key:
        tray_link.notify_hotkey_pressed("hold")
    elif toggle_key and code == toggle_key:
        tray_link.notify_hotkey_pressed("toggle")


def _hide_modifier_hotkey(event, toggle_key, devices, held_modifiers) -> None:
    """Hide a Right Ctrl or Right Alt dictation key from the desktop as soon
    as it's down: what TuxTalk types meanwhile (the dictation a tap stops, a
    phrase of hands-free, a send counting down) would otherwise arrive as
    Ctrl or Alt shortcuts (Ctrl+W closes the tab). The hold key hides itself
    when it starts a dictation (_handle_key_event). Kept until it's let go,
    or until something lets go of every grab sooner; the desktop has been
    told it's up either way. Not with another modifier held: that press is
    the user's own shortcut (Ctrl+Alt+T with Right Ctrl), which hiding the
    key would break."""
    if (event.value == 1 and not keyboard_grab.grabbed_devices
            and (event.code == toggle_key or rs.state.is_recording)
            and not _someone_elses_shortcut(held_modifiers - {event.code})):
        keyboard_grab.grab_keyboards_hiding(devices, {event.code})
        rs.state.hidden_key = event.code
    elif event.value == 0:
        modifier_hotkeys.note_released(event.code)
        if event.code == rs.state.hidden_key:
            rs.state.hidden_key = None
            keyboard_grab.release_all()


def _toggle_tap_holds_send(event, toggle_key, held_modifiers) -> bool:
    """A press of the toggle key while a send counts down holds it, and does
    nothing else. Asked before a Right Ctrl or Right Alt toggle is hidden
    (_hide_modifier_hotkey): once hidden, the send's Enter, which waits for
    the key, could go out before the hold."""
    return (event.code == toggle_key and event.value == 1 and not rs.state.is_recording
            and not _someone_elses_shortcut(held_modifiers - {toggle_key})
            and not (live.voice_editing and _held_shift(held_modifiers))
            and send_countdown.hold(announce=True))


def _start_hold_recording(hold_key, held_modifiers, devices, cfg, input_device_idx) -> None:
    """The hold key went down: hold a send counting down, then hide the key
    (and Shift, for voice editing) from the desktop, and record.

    The send goes first: once the key is hidden, an Enter that waits for it
    to come up goes out. The grab keeps the hotkey from the focused app,
    without leaving the desktop thinking it's still held (see
    _grab_keyboards_hiding); start_recording lets go of it if the mic fails
    to open. Shift unhidden, the Ctrl+C that copies the selection would
    arrive as Ctrl+Shift+C."""
    held = send_countdown.hold(announce=False)
    shift = _held_shift(held_modifiers) if live.voice_editing else set()
    keyboard_grab.grab_keyboards_hiding(devices, {hold_key, *shift})
    if (start_recording(cfg.beeps, cfg.notify, input_device_idx, edit_mode=bool(shift),
                        held_send=held) and rs.state.held_send):
        # The hold time counts from the press to when its release is read,
        # which waits for the start: 0.15 to 0.2 s in the test VMs, so a
        # quick tap to hold the send came to over MIN_HOLD_MS on Pop!_OS and
        # recorded instead. A key already up after a start that quick was a
        # tap; after a slower one it could have been a short word, so the
        # hold time decides.
        rs.state.released_while_starting = (
            time.time() - rs.state.press_t0 < 2 * MIN_HOLD_MS / 1000
            and keyboard_grab.wait_for_keys_up(devices, {hold_key}, 0))


def _handle_key_event(event, hold_key, toggle_key, voice_cmds_combo, held_modifiers,
                      devices, cfg, input_device_idx, paste_last_combo=None):
    """Handle a single keyboard event. Both hold (F8) and toggle (F9) are always active.

    Grabbed devices are tracked in module state (see _grab_all_devices), not
    returned, so that every way recording can end — release, toggle, ESC, or an
    exception inside start_recording — releases the keyboard.
    """
    if modifier_hotkeys.right_alt_alone(event, (hold_key, toggle_key), held_modifiers):
        modifier_hotkeys.guard_alt_tap()

    # --- Hotkey test mode: report presses via D-Bus instead of recording ---
    if event.value == 1 and _hotkey_test_active():
        _report_test_press(event.code, hold_key, toggle_key)
        return  # Don't process hotkeys normally in test mode

    # --- Paste Last Dictation combo (e.g. Ctrl+Alt+P) ---
    # Typed only once every key of the combo is let go: typing while Ctrl and
    # Alt are still down would turn each letter into a Ctrl+Alt shortcut.
    pending = rs.state.paste_last
    if pending is not None:
        if time.time() - pending["at"] > PASTE_LAST_EXPIRY_S:
            rs.state.paste_last = pending = None
        elif event.value == 0 and (event.code == pending["key"] or event.code in MODIFIER_CODES):
            # Only the combo's own keys are waited for. Any other release
            # goes on to the handlers below: swallowing it would lose a hold
            # key's key-up and leave that recording running.
            if event.code == pending["key"]:
                pending["key_up"] = True
            if pending["key_up"] and not held_modifiers:
                rs.state.paste_last = None
                if not rs.state.is_recording:
                    _paste_last_dictation(cfg)
            return
    if paste_last_combo and event.value == 1 and not rs.state.is_recording:
        required_mods, main_key = paste_last_combo
        if event.code == main_key and check_modifiers_held(required_mods, held_modifiers):
            # Same brief grab as the Voice Commands combo below, so the key
            # press doesn't reach the focused app.
            keyboard_grab.grab_keyboards_hiding(devices, {main_key, *held_modifiers})
            keyboard_grab.release_all()
            rs.state.paste_last = {"key": main_key, "key_up": False, "at": time.time()}
            return

    # --- Voice Commands combo hotkey (e.g. Ctrl+Shift+H) ---
    # Not while recording: its brief grab ends by releasing every grab, which
    # would also drop the ones a hold recording depends on.
    if voice_cmds_combo and event.value == 1 and not rs.state.is_recording:
        required_mods, main_key = voice_cmds_combo
        if event.code == main_key and check_modifiers_held(required_mods, held_modifiers):
            # Briefly grab the keyboards (not the pointer) to stop the keypress
            # leaking into the focused app. The same tracked grabs as recording,
            # and a finally that always releases, so a failing tray call can't
            # strand them. The modifiers are balanced too: a stuck Ctrl in the
            # desktop would be far worse than a stuck F8.
            keyboard_grab.grab_keyboards_hiding(devices, {main_key, *held_modifiers})
            try:
                tray_link.show_voice_commands()
            finally:
                keyboard_grab.release_all()
            return

    if toggle_key and _toggle_tap_holds_send(event, toggle_key, held_modifiers):
        return   # the tap only held the send: no recording
    if event.code in MODIFIER_CODES and event.code in (hold_key, toggle_key):
        _hide_modifier_hotkey(event, toggle_key, devices, held_modifiers)

    # --- Hold-to-talk: hold key down to record, release to stop ---
    if event.code == hold_key:
        if (event.value == 1 and not rs.state.is_recording
                and not _someone_elses_shortcut(held_modifiers - {hold_key})):
            _start_hold_recording(hold_key, held_modifiers, devices, cfg, input_device_idx)
        elif event.value == 0 and rs.state.is_recording:
            # Let go of the keyboards before typing, so the user's own keys
            # reach the app again
            keyboard_grab.release_all()
            stop_recording(cfg.beeps, cfg.smart_quotes, cfg.notify, cfg.language,
                           cfg.auto_space, cfg.auto_period, cfg.injection_mode)

    # --- Tap-to-toggle: press once to start, press again to stop ---
    if (toggle_key and event.code == toggle_key and event.value == 0
            and send_countdown.owes_space() and not rs.state.is_recording):
        send_countdown.type_owed_space()   # the tap that held the send is over

    if (toggle_key and event.code == toggle_key and event.value == 1
            and not _someone_elses_shortcut(held_modifiers - {toggle_key})):
        shift = _held_shift(held_modifiers) if live.voice_editing else set()
        if not rs.state.is_recording:
            if shift:
                # An edit: no double-tap hands-free, so not "from_toggle".
                start_recording(cfg.beeps, cfg.notify, input_device_idx, edit_mode=True)
            else:
                start_recording(cfg.beeps, cfg.notify, input_device_idx, from_toggle=True)
        elif rs.state.edit_mode:
            # A hold edit holds the keyboards (brushing the toggle key mid-hold
            # lands here): let go first, or they stay grabbed for the whole edit.
            keyboard_grab.release_all()
            # Stopping an edit. If Shift is down again, wait for the user to let go, or
            # the Ctrl+C that copies the selection would arrive as Ctrl+Shift+C. Hiding
            # it with injected key events, as the hold key does, would replay them
            # after a slow edit and start a new recording by itself.
            if shift and not keyboard_grab.wait_for_keys_up(devices, _SHIFT_CODES,
                                                            EDIT_SHIFT_WAIT_S):
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
            keyboard_grab.release_all()
            stop_recording(cfg.beeps, cfg.smart_quotes, cfg.notify, cfg.language,
                           cfg.auto_space, cfg.auto_period, cfg.injection_mode)

    # --- ESC cancels in any mode ---
    if event.code == ecodes.KEY_ESC and rs.state.is_recording and event.value == 1:
        cancel_recording(cfg.beeps, cfg.notify, "Cancelled by ESC")
        keyboard_grab.release_all()



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
    keeps loop locals (the evdev loop's hold_key and toggle_key) can rebind them; None
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
        if not rs.state.is_recording:
            start_recording(cfg.beeps, cfg.notify, svc.input_device_idx, from_toggle=from_toggle)
            svc.last_activity = now
    if _cmd_stop_recording.is_set():
        _cmd_stop_recording.clear()
        if rs.state.is_recording:
            keyboard_grab.release_all()
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
                    if live.chunk_pause_seconds > 0:
                        # Chunked dictation: through the chunk worker, so the
                        # rest is typed after this recording's chunks and
                        # before the next recording's. No wait, as below.
                        role = "last" if rs.state.chunks_submitted else None
                        if role:
                            frames = _drop_speechless_rest(frames)
                        _chunk_worker().submit(
                            lambda frames=frames, rec_sr=rec_sr, cfg=cfg, role=role,
                            message=rs.state.message: _transcribe_and_inject(
                                frames, rec_sr, cfg.beeps, cfg.smart_quotes,
                                cfg.notify, cfg.language, cfg.auto_space,
                                cfg.auto_period, cfg.injection_mode, chunk_role=role,
                                message=message))
                    else:
                        def _worker(frames=frames, rec_sr=rec_sr, cfg=cfg,
                                    message=rs.state.message):
                            with _inject_lock:
                                _transcribe_and_inject(
                                    frames, rec_sr, cfg.beeps, cfg.smart_quotes,
                                    cfg.notify, cfg.language, cfg.auto_space,
                                    cfg.auto_period, cfg.injection_mode, message=message)
                        threading.Thread(target=_worker, daemon=True,
                                         name="tt-inject").start()
            else:
                stop_recording(cfg.beeps, cfg.smart_quotes, cfg.notify, cfg.language,
                               cfg.auto_space, cfg.auto_period, cfg.injection_mode)
            svc.last_activity = now
    # Not while a dictation is still being transcribed or typed: every worker
    # holds _inject_lock, and the idle clock starts when the key comes up.
    if (svc.timeout_enabled and not rs.state.is_recording and engine.model is not None
            and not _inject_lock.locked() and now - svc.last_activity > svc.timeout_seconds):
        engine.rest(svc.timeout_minutes)

    # Pick up Preferences changes without a restart — indicator style/position,
    # typing delay, etc. The evdev loop (Backend A) does this inline; Backend B
    # (the portal path used by the Flatpak) runs ONLY this tick, so without the
    # same recheck live settings never apply there — the recording indicator
    # stays on whatever it was built with. Idle-only and throttled, mirroring the
    # evdev path. _reload_live_settings updates cfg in place and pushes the new
    # values into the running recording indicator.
    if not rs.state.is_recording and now - svc.last_config_check >= CONFIG_RECHECK_SECONDS:
        svc.last_config_check = now
        # Custom commands live in their own file and are checked on the same
        # tick. Unconditionally, and before the early return below: an edit to
        # the Commands tab alone leaves config.toml untouched, so gating this on
        # a config change would be gating it on the thing that did not happen.
        if live.commands_file_changed():
            live.reload_commands()
        if live.words_file_changed():
            live.reload_words()
        if live.config_file_changed():
            # Saving settings is activity too. Switching the auto-timeout on
            # must not stop the service a second later because the clock still
            # counts from the last D-Bus command.
            svc.last_activity = now
            reloaded = _reload_live_settings(cfg, recording_indicator)
            # cfg.mic is fresh now; the device number behind it may not be.
            svc.refresh_input_device()
            return reloaded
    return None


def _helper_watch(devices, hold_key, toggle_key, voice_cmds_combo, paste_last_combo):
    """Tell the hotkey helper, when it stands in for the keyboards, which keys
    to report: the dictation keys, Esc, and the two combos."""
    for dev in devices:
        if hasattr(dev, "watch"):
            from .hotkey_helper_client import refused_combo, watch_specs
            for what, combo in (("Voice Commands", voice_cmds_combo),
                                ("Paste Last Dictation", paste_last_combo)):
                why = combo and refused_combo(*combo)
                if why:
                    logger.warning(f"The {what} shortcut won't work with the hotkey helper "
                                   f"({why}). Pick one with Ctrl+Alt, Ctrl+Super or Alt+Super.")
            dev.watch(watch_specs(hold_key, toggle_key, ecodes.KEY_ESC,
                                  (voice_cmds_combo, paste_last_combo)))


def _loop_evdev(cfg: Settings, input_device_idx, keyboards=None):
    """Main event loop: monitor keyboard for hotkey presses and dispatch to recording.

    *keyboards* is the hotkey helper (hotkey_helper_client.HelperKeyboard) when
    it is installed: then no keyboard is opened here at all."""
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    logger.info(f"Session type: {session}, Wayland: {session=='wayland'}")
    # Both hotkeys work whatever the mode setting says.
    logger.info(f"Hold key: {cfg.hotkey}, Toggle key: {cfg.toggle_hotkey or 'none'}")

    # Auto-timeout setup
    svc = _ServiceState(cfg, input_device_idx)
    logger.info(f"Auto-timeout: enabled={svc.timeout_enabled}, minutes={svc.timeout_minutes}")

    # evdev opens these non-blocking, which the read loop below relies on.
    use_helper = keyboards is not None
    devices = list(keyboards) if use_helper else []
    if use_helper:
        logger.info("Hotkeys come from the TuxTalk hotkey helper; no keyboard is opened here")
    else:       # keyboards only; one that vanishes meanwhile is skipped
        _rediscover_devices(devices)
    last_device_scan = time.time()
    # Establish both files' current mtimes so the first poll does not report a
    # spurious change and re-apply what the service just started with.
    # The recheck timer itself lives on _ServiceState — _service_tick owns it
    # for both backends.
    live.config_file_changed()
    live.commands_file_changed()
    live.words_file_changed()

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

    hold_key = keycode_from_name(cfg.hotkey)
    toggle_key = keycode_from_name(cfg.toggle_hotkey) if cfg.toggle_hotkey else None

    # Voice commands hotkey supports combos like "Ctrl+Shift+H"
    vc_hotkey_str = getattr(cfg, 'voice_commands_hotkey', '')
    voice_cmds_combo = parse_hotkey_combo(vc_hotkey_str)
    voice_cmds_main_key = voice_cmds_combo[1] if voice_cmds_combo else None
    paste_last_combo = parse_hotkey_combo(getattr(cfg, "paste_last_hotkey", ""))

    if hold_key is None:
        logger.info("No hotkey configured - service will not monitor any keys")
        print("No hotkey configured. Complete onboarding to activate dictation.")
        return

    # A hotkey is configured and onboarding is done, so from here on every
    # keypress is supposed to reach us. If no device could be opened, say so
    # now rather than running a poll loop over an empty device list forever.
    if not devices:
        diagnosis = secure_input.input_access_diagnosis()
        logger.error(diagnosis)
        print(diagnosis, file=sys.stderr)

    if voice_cmds_combo:
        logger.info(f"Voice Commands hotkey: {vc_hotkey_str}")
    _helper_watch(devices, hold_key, toggle_key, voice_cmds_combo, paste_last_combo)
    modifier_hotkeys.note(devices, hold_key, toggle_key)

    # Have the desktop hold the hotkeys back from the focused app (see
    # _claim_desktop_hotkeys). They are released by stop_dictation_service,
    # the tray, or this exit hook; registered even if the claim failed, since
    # a later hotkey change can still succeed.
    from .service_launcher import release_desktop_hotkeys
    _claim_desktop_hotkeys(cfg)
    atexit.register(release_desktop_hotkeys)

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
        # This loop keeps the hotkeys in locals, so it rebinds them
        # from what the tick reloaded. It must not check _config_file_changed()
        # itself: that check is edge-triggered, and the tick has already
        # consumed the change.
        reloaded = _service_tick(cfg, svc)
        if reloaded is not None:
            hold_key = reloaded.hold_key
            toggle_key = reloaded.toggle_key
            vc_hotkey_str = reloaded.vc_hotkey_str
            voice_cmds_combo = reloaded.voice_cmds_combo
            voice_cmds_main_key = reloaded.voice_cmds_main_key
            paste_last_combo = reloaded.paste_last_combo
            # Re-claim even if the hotkeys look unchanged: claim() is cheap,
            # and comparing would mean a second copy of the hotkey fields.
            _claim_desktop_hotkeys(cfg)
            _helper_watch(devices, hold_key, toggle_key, voice_cmds_combo, paste_last_combo)
            modifier_hotkeys.note(devices, hold_key, toggle_key)
            logger.info("Applied settings change without restarting")

        for dev in _devices_with_input(devices, INPUT_WAIT_S):
            try:
                for event in dev.read():
                    if event.type == ecodes.EV_KEY:
                        # Our own injected key events (see _grab_keyboards_hiding)
                        # come back through this same read; they are not input.
                        if keyboard_grab.is_own_injected_event(dev, event):
                            continue
                        track_modifier(held_modifiers, event)

                        # Any hotkey counts as activity, whether or not the
                        # auto-timeout is on now: it can be switched on later,
                        # and must then count from the last real use.
                        if event.code in (hold_key, toggle_key, ecodes.KEY_ESC, voice_cmds_main_key,
                                          paste_last_combo[1] if paste_last_combo else None):
                            svc.last_activity = current_time
                        _handle_key_event(
                            event, hold_key, toggle_key,
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

        if dead_devices:
            # Their modifiers' key-ups can't come now, and a Ctrl left held
            # here would stop F8 dictating (_someone_elses_shortcut).
            held_modifiers.clear()
        _drop_dead_devices(dead_devices, devices, cfg)

        # Pick up keyboards that have (re)appeared. Only while idle: rescanning
        # mid-recording would add an ungrabbed device to a grabbed set, letting
        # the hotkey leak into the focused app. Throttled because it opens
        # every node under /dev/input.
        if (not use_helper and not rs.state.is_recording
                and current_time - last_device_scan >= DEVICE_RESCAN_SECONDS):
            last_device_scan = current_time
            _rediscover_devices(devices)

        # Backstop: we must never hold a keyboard grab while not recording.
        keyboard_grab.release_if_not_recording()

        pass_time = time.time() - current_time
        if pass_time < INPUT_MIN_PASS_S:
            time.sleep(INPUT_MIN_PASS_S - pass_time)


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
    send, status = control.act_now(action, rs.state.is_recording)
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
        tray_link.get_proxy().NotifyServiceReady(
            dbus.UInt32(os.getpid()),
            dbus_interface='io.github.ronb1964.TalkType',
            timeout=focused_window.TRAY_TIMEOUT_S,
        )
    except Exception as e:
        logger.debug(f"Could not tell the tray the service is ready: {e}")



def main():
    from . import desktop_identity, ui_style
    desktop_identity.apply()        # TuxTalk's icon on its windows, not KDE's generic one
    ui_style.prepare_windows()   # emoji that show, buttons clear of panels and docks
    # The loaded speech model. Usually built after the GTK loop starts; built
    # earlier when it must first be downloaded (see the GTK section below).
    _acquire_single_instance()
    _install_signal_handlers()

    cfg = load_config()
    engine.guard_large_v3_without_cuda(cfg)

    keyboard_output.key_delay_ms = cfg.key_delay
    # Made now, so the desktop has taken it on long before the first
    # dictation is typed. The Flatpak types through the portal instead.
    if not os.environ.get("FLATPAK_ID"):
        virtual_keyboard.start()

    # A service that stopped mid-dictation may have left other apps quiet.
    try:
        from .audio_duck import restore_left_over
        restore_left_over()
    except Exception as e:
        logger.debug(f"Could not check for sounds left turned down: {e}")
    # The live options; the AI engine (if on) loads in the background.
    live.apply(cfg)

    live.commands = load_custom_commands()
    if live.commands:
        logger.info(f"Loaded {len(live.commands)} custom voice command(s)")
    from . import custom_words
    live.words = custom_words.load()
    if live.words:
        logger.info(f"Loaded your words ({len(live.words)})")

    input_device_idx = _pick_input_device(cfg.mic)

    # The D-Bus service and the recording indicator both need GTK's main loop.
    global dbus_service, recording_indicator
    dbus_service = None
    recording_indicator = None
    gtk_ok = False
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk

        # Initialize D-Bus service for GNOME extension integration
        try:
            from .dbus_service import TuxTalkDBusService

            class AppInstance:
                def __init__(self, cfg):
                    self.config = cfg
                    self.service_running = True
                    self.dbus_service = None

                @property
                def is_recording(self):
                    """Live recording state from the dictation engine."""
                    return rs.state.is_recording

                def show_preferences(self):
                    """Open preferences window"""
                    import subprocess
                    subprocess.Popen([sys.executable, "-m", "tuxtalk.prefs"])

                def start_recording(self):
                    """Signal the evdev loop to start recording (thread-safe)."""
                    if not rs.state.is_recording:
                        _cmd_start_recording.set()

                def stop_recording(self):
                    """Signal the evdev loop to stop recording (thread-safe)."""
                    if rs.state.is_recording:
                        _cmd_stop_recording.set()

                def toggle_recording(self):
                    """Toggle recording state (thread-safe)."""
                    if rs.state.is_recording:
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

            logger.info("D-Bus service started successfully (no tray running)")
        except Exception as e:
            dbus_service = None
            if type(e).__name__ == "NameExistsException":
                # The normal case: the tray already owns the name.
                logger.info("The tray owns the D-Bus name; the service doesn't register its own")
            else:
                logger.error(f"D-Bus service initialization failed: {e}", exc_info=True)

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
                    sensitivity=cfg.indicator_sensitivity,
                    follow_window=getattr(cfg, "indicator_follow_window", False),
                )
                # Spectrum processor for the bars style; _feed_indicator uses it.
                global _spectrum_processor
                from .indicator_dsp import SpectrumProcessor
                _spectrum_processor = SpectrumProcessor(bins=20)
                logger.info(f"Recording indicator initialized at position: {cfg.indicator_position}, size: {cfg.indicator_size}")
            except Exception as e:
                logger.error(f"Recording indicator initialization failed: {e}", exc_info=True)
        gtk_ok = True
    except Exception as e:
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
            engine.model = engine.build_or_exit(cfg)

        # Start single GTK main loop in a background thread for both D-Bus and recording indicator
        def run_gtk_loop():
            print("Starting GTK main loop...")
            Gtk.main()

        gtk_thread = threading.Thread(target=run_gtk_loop, daemon=True)
        gtk_thread.start()
        engine.gtk_loop_running = True
        print("GTK main loop started in background thread")

    if engine.model is None:
        engine.model = engine.build_or_exit(cfg)
    logger.info(f"Configuration: model={cfg.model}, device={cfg.device}, language={cfg.language or 'auto'}, auto_space={cfg.auto_space}, auto_period={cfg.auto_period}")
    engine.notify_tray()
    # A toggle sent while the model was loading would otherwise start a
    # recording seconds later, out of the blue, when the input loop starts.
    # One asked for on the command line waits in the tray instead (control.py).
    _cmd_start_recording.clear()
    _cmd_stop_recording.clear()
    _cmd_start_from_toggle_key.clear()
    _mark_service_ready()

    # Run the input backend chosen by FLATPAK_ID: evdev everywhere
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
