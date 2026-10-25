"""A dictation's recording, from the key going down to its audio handed on:
starting, cancelling and finishing it (with the start and stop requests,
the double-tap to hands-free and the recording indicator), the audio
callback, which input device to record from (following the system's
default as it changes), pausing music and turning other sound down
meanwhile, stopping a stream safely, and trimming what a recording ends
with (the auto-stop's silence, a speechless rest) before it is
transcribed."""
import atexit
import threading
import time

import numpy as np
import sounddevice as sd

from . import (
    beeps,
    chunking,
    engine,
    keyboard_grab,
    mic_stream,
    notices,
    send_countdown,
    silence,
    tray_link,
    type_at_pause,
)
from . import config as config_module
from . import live_settings as live
from . import notify as notify_module
from . import recording_state as rs
from .beeps import CANCEL_BEEP, HANDS_FREE_BEEP, START_BEEP
from .config import load_config
from .logger import setup_logger

logger = setup_logger(__name__)

_media_pauser = None            # media_pause.MediaPauser, created on the first pause

_audio_ducker = None            # audio_duck.AudioDucker, created on the first duck


def pause_media_players():
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


def resume_media_players():
    """Resume what pause_media_players paused and put back what it turned
    down. Runs even if an option was switched off mid-recording, so nothing
    is left paused or quiet."""
    if _media_pauser is not None:
        _media_pauser.resume()
    if _audio_ducker is not None:
        _audio_ducker.restore()


CHANNELS = 1


def feed_indicator(indicator, indata, spectrum_processor):
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


def pick_input_device(mic_substring: str | None):
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
# Default; None when a mic was chosen by name. See follow_system_default.
_system_default_mic = None


def follow_system_default(input_device_idx):
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
        idx = pick_input_device("")
    except Exception as e:
        logger.warning(f"Could not follow the new default microphone {source!r}: {e}")
        return tracked[1]
    logger.info(f"The system's default microphone changed to {source!r} (device {idx})")
    return idx


def resample(audio, orig_sr, target_sr):
    """Resample audio from orig_sr to target_sr using linear interpolation.

    Good enough for speech audio going to Whisper. No extra dependencies needed.
    """
    if orig_sr == target_sr:
        return audio
    # Calculate new length and interpolate
    duration = len(audio) / orig_sr
    target_len = int(duration * target_sr)
    orig_indices = np.linspace(0, len(audio) - 1, target_len)
    resampled = np.interp(orig_indices, np.arange(len(audio)), audio.astype(np.float64))
    return resampled.astype(audio.dtype)


def stop_stream_safely():
    """Stop and close the microphone stream. An audio system that stops
    answering costs mic_stream.STOP_TIMEOUT_S, not the whole service."""
    stream, rs.state.stream = rs.state.stream, None
    if stream is not None:
        mic_stream.close(stream)


def trim_auto_stop_tail(frames):
    """After an auto-stop, drop the silence the detector measured at the end.

    Only whole trailing blocks are removed, and only up to what was measured
    as silent (less a small margin kept for the last word), so speech is never
    touched. Runs after the stream stops, so no block is still being added.
    """
    detector, rs.state.silence = rs.state.silence, None
    chunking.drop_tail_samples(frames, detector.tail_to_trim() if detector is not None else 0)


def drop_speechless_rest(frames):
    """The audio after the last cut, or none when the pause detector heard no
    speech in it: room noise longer than the tap check would otherwise reach
    the engine, which invents a word for it ("Yeah.")."""
    if rs.state.last_chunk_speech == 0.0:
        logger.info("No speech after the last chunk; the rest isn't transcribed")
        return []
    return frames


def frames_f32(frames):
    """Raw int16 recording frames as float samples in -1..1."""
    return np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32) / 32768.0


def frames_are_dead_mic(frames, rec_sr) -> bool:
    """silence.is_dead_mic for raw int16 recording frames."""
    return silence.is_dead_mic(frames_f32(frames), rec_sr)


MIN_HOLD_MS = 200

# Two toggle taps closer than this make the first tap's recording hands-free.
# Generous: 0.4 s missed slower hands (#21); a quick word still comes, at auto-stop.
DOUBLE_TAP_S = 0.7

# The recording indicator, made by app.main when it's switched on; None if not.
indicator = None
# Built with the indicator; feed_indicator uses it for the bars style's
# spectrum. None until then (and for other styles).
spectrum_processor = None

# Requests to start or stop recording, set by the signal handler, the D-Bus
# thread or the audio callback, and taken by the service tick. Two Events
# rather than one flag, so a start and a stop can't overwrite each other.
start_requested = threading.Event()
stop_requested = threading.Event()

# Set alongside start_requested when the Flatpak's toggle SHORTCUT (not a
# menu) started it, so a quick second press can switch it to hands-free.
started_from_toggle_key = threading.Event()


def _sd_callback(indata, frames_count, time_info, status):
    """sounddevice's audio callback: collect frames while recording."""
    if rs.state.is_recording:
        rs.state.frames.append(indata.tobytes())

        # Chunked dictation: at a pause, the phrase so far goes off to be typed.
        chunker = rs.state.chunker
        if chunker is not None and chunker.feed(indata):
            type_at_pause.cut_chunk(chunker)

        # Hands-free auto-stop: when the speaker has finished, ask the service
        # loop to stop, exactly as a second press of the toggle key would.
        detector = rs.state.silence
        if detector is not None and detector.feed(indata):
            if detector.nothing_said:
                logger.info("Auto-stop: nothing said, stopping without typing anything")
            else:
                logger.info("Auto-stop: speaker went quiet, stopping the recording")
            stop_requested.set()

        # Feed the recording indicator whatever its active style needs.
        if indicator:
            feed_indicator(indicator, indata, spectrum_processor)



def open_input_stream(device_idx) -> None:
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
    switch it to hands-free (see toggle_pressed_while_recording).
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
    input_device_idx = follow_system_default(input_device_idx)
    try:
        open_input_stream(input_device_idx)
    except Exception as first_error:
        # The mic is stored as a device NUMBER, resolved once at startup. Unplug
        # a USB device and the numbering shifts, leaving that number pointing at
        # nothing. Re-resolve by name and try once more before giving up.
        logger.warning(f"Microphone {input_device_idx} failed ({first_error}); re-resolving")
        stop_stream_safely()
        try:
            fresh_idx = pick_input_device(load_config().mic)
            open_input_stream(fresh_idx)
            logger.info(f"Microphone re-resolved to device {fresh_idx}")
            if fresh_idx != input_device_idx:
                notices.warn_mic_switched(input_device_idx, fresh_idx)
        except Exception as e:
            # Genuinely unavailable: unplugged, or held exclusively by another
            # app. Undo everything and tell the user; failing silently would
            # leave the keyboard grabbed and the machine unusable.
            logger.error(f"Could not start recording: {e}", exc_info=True)
            rs.state.is_recording = False
            stop_stream_safely()
            keyboard_grab.release_all()
            beeps.beep(beeps_on, *CANCEL_BEEP)
            if notify_on:
                notify_module.notify("Microphone unavailable",
                                     "Check that no other app is using it.")
            if rs.state.held_send:
                send_countdown.say_held()   # nothing to dictate onto it: it stays unsent
            return False

    type_at_pause.arm_chunker(rs.state.recording_samplerate, edit_mode)
    pause_media_players()
    rs.state.start_beep = beeps.beep(beeps_on, *START_BEEP)
    if notify_on:
        notify_module.notify("Recording… speak now", "")
    tray_link.notify_recording_state(True)
    if indicator:
        try:
            indicator.show_at_position()
            indicator.start_recording()
        except Exception as e:
            logger.error(f"Failed to show recording indicator: {e}", exc_info=True)

    # The double-tap is timed from here, the start beep heard: keys wait on
    # this thread, so a tap made during a slow start still counts (#21).
    if from_toggle and rs.state.start_beep is not None:
        beeps.wait_until_played(over=rs.state.start_beep)
    rs.state.toggle_started_at = time.time() if from_toggle else None
    logger.info(f"Recording started in {time.time() - rs.state.press_t0:.2f}s")
    return True


def cancel_recording(beeps_on: bool, notify_on: bool, reason="Cancelled"):
    """End a recording without transcribing it, telling the user *reason*."""
    rs.state.is_recording = False
    rs.state.was_cancelled = True
    rs.state.edit_mode = False
    stop_stream_safely()
    resume_media_players()
    beeps.beep(beeps_on, *CANCEL_BEEP)
    print(reason)
    logger.debug(f"Recording cancelled: {reason}")
    if notify_on:
        notify_module.notify(reason, "")

    if indicator:
        indicator.hide_indicator()
    tray_link.notify_recording_state(False)


def toggle_pressed_while_recording(beeps_on: bool) -> bool:
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
    rate = rs.state.recording_samplerate
    detector = silence.SilenceDetector(live.auto_stop_seconds, rate)
    beep_hz = START_BEEP[0] if rs.state.start_beep is not None else None
    if silence.has_a_word(frames_f32(list(rs.state.frames)), rate, ignoring_hz=beep_hz):
        detector.heard_speech()
    rs.state.silence = detector
    logger.info("Double-tap: this recording will stop by itself when you go quiet")
    beeps.beep(beeps_on, *HANDS_FREE_BEEP)
    return True


def finish_capture(beeps_on: bool, notify_on: bool):
    """End a recording quickly and hand back its audio (the fast half of stop).

    Validates the hold time, stops the stream, hides the indicator, and SNAPSHOTS
    the captured frames. Returns (frames, rec_sr) or None if the recording was
    cancelled or too short. start_recording() reassigns state.frames to a fresh
    list, so the returned reference stays valid even if a new recording begins
    immediately, which is what lets Backend B run the slow transcription/typing
    on a worker thread without dropping or mixing audio."""
    if _dropped_as_silent(beeps_on, notify_on) or _dropped_as_a_tap(beeps_on, notify_on):
        return None
    with type_at_pause.chunk_lock:
        # No more cuts: from here on the audio is the last chunk.
        chunker, rs.state.chunker = rs.state.chunker, None
        rs.state.last_chunk_speech = chunker.speech_time if chunker is not None else None
        frames = rs.state.frames  # snapshot the reference before it can be reassigned
    if chunker is not None and not rs.state.chunks_submitted:
        logger.info(f"Chunked dictation: no pause found ({chunker.describe()})")
    rec_sr = rs.state.recording_samplerate
    rs.state.is_recording = False
    stop_stream_safely()
    resume_media_players()
    tray_link.notify_recording_state(False)  # the tray icon shows recording stopped
    trim_auto_stop_tail(frames)
    if rs.state.was_cancelled:
        return None
    if indicator:
        indicator.hide_indicator()
    return frames, rec_sr


def _dropped_as_silent(beeps_on: bool, notify_on: bool) -> bool:
    """Hands-free with nothing said: cancel it, and say so if the mic is dead.
    Transcribing room noise can type a stray "Thank you." (see
    silence.is_speechless_tap)."""
    if rs.state.silence is None or not rs.state.silence.nothing_said:
        return False
    rs.state.silence = None
    rate = rs.state.recording_samplerate
    dead_mic = frames_are_dead_mic(rs.state.frames, rate)
    cancel_recording(beeps_on, notify_on and not dead_mic, "Nothing heard, so nothing was typed")
    if dead_mic:
        notices.warn_silent_mic()
    return True


def _dropped_as_a_tap(beeps_on: bool, notify_on: bool) -> bool:
    """A press too short to be a dictation: cancel it. A tap that held a
    pending send leaves that text unsent, as it is."""
    # Before the space below: the time typing it takes isn't part of the press.
    held_ms = int((time.time() - (rs.state.press_t0 or time.time())) * 1000)
    if rs.state.held_send and send_countdown.owes_space() and not rs.state.chunks_submitted:
        # The hotkey that held the send is up now; a tap leaves the text as
        # it is, a dictation goes after this space.
        send_countdown.type_owed_space()
    if held_ms >= MIN_HOLD_MS and not (rs.state.held_send and rs.state.released_while_starting):
        return False
    if rs.state.held_send:
        cancel_recording(beeps_on, False, send_countdown.HELD_NOTICE)
        send_countdown.say_held()
    else:
        cancel_recording(beeps_on, notify_on, f"Cancelled (held {held_ms} ms)")
    return True
