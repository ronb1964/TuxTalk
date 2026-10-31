"""A dictation's recording, from the key going down to its audio handed on:
starting, cancelling and finishing it (with the start and stop requests,
the double-tap to hands-free and the recording indicator), the audio
callback, which input device to record from (following the system's
default as it changes), pausing music and turning other sound down
meanwhile, stopping a stream safely, and trimming what a recording ends
with (the auto-stop's silence, a speechless rest) before it is
transcribed."""
import atexit
import functools
import threading
import time

import numpy as np
import sounddevice as sd

from . import (
    beeps,
    chunking,
    dictation_worker,
    engine,
    keyboard_grab,
    mic_stream,
    notices,
    send_countdown,
    silence,
    tray_link,
    type_at_pause,
)
from . import live_settings as live
from . import microphones
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
            atexit.register(_audio_ducker.close)
        _audio_ducker.duck()
    if not live.pause_media:
        return
    if _media_pauser is None:
        from .media_pause import MediaPauser
        _media_pauser = MediaPauser()
        # Quitting mid-dictation would otherwise leave the music paused.
        atexit.register(_media_pauser.close)
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

    Delegates to microphones, which finds the desktop's default microphone
    when no mic name is configured. This avoids the broken ALSA "default"
    virtual device that returns garbage audio on PipeWire systems.
    """
    global _system_default_mic
    if mic_substring:
        _system_default_mic = None      # a mic chosen by name stays put
        return microphones.find_input_device(mic_substring)
    return _use_system_default(microphones.default_source())


# (PipeWire default source name, device index) while the mic is on System
# Default; None when a mic was chosen by name. See follow_system_default.
_system_default_mic = None


def _use_system_default(source):
    """The device for the default source *source* (one wpctl answer), kept
    with its name so follow_system_default can notice a switch."""
    global _system_default_mic
    idx = microphones.default_microphone(source)
    _system_default_mic = (microphones.source_name(source), idx)
    return idx


def follow_system_default(input_device_idx):
    """The device to record from, following a switched desktop default.

    The default mic is turned into a device number when it's resolved, so
    switching inputs in GNOME's Quick Settings or KDE's applet used to need a
    restart (issue #14). Asking PipeWire costs about a millisecond, so it's
    done on every recording, and devices are re-resolved only on a change,
    from the same answer. Anything that goes wrong keeps the device already
    in use.
    """
    tracked = _system_default_mic
    if tracked is None:
        return input_device_idx
    source = microphones.default_source()
    name = microphones.source_name(source)
    if name is None or name == tracked[0]:
        return tracked[1]
    try:
        idx = _use_system_default(source)
    except Exception as e:
        logger.warning(f"Could not follow the new default microphone {name!r}: {e}")
        return tracked[1]
    logger.info(f"The system's default microphone changed to {name!r} (device {idx})")
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


def stop_stream_safely(rec):
    """Stop and close recording *rec*'s microphone stream. An audio system
    that stops answering costs mic_stream.STOP_TIMEOUT_S, not the whole
    service."""
    stream, rec.stream = rec.stream, None
    if stream is not None:
        mic_stream.close(stream)


def trim_auto_stop_tail(rec, frames):
    """After an auto-stop, drop the silence *rec*'s detector measured at the
    end of *frames*.

    Only whole trailing blocks are removed, and only up to what was measured
    as silent (less a small margin kept for the last word), so speech is never
    touched. Runs after the stream stops, so no block is still being added.
    """
    detector, rec.silence = rec.silence, None
    chunking.drop_tail_samples(frames, detector.tail_to_trim() if detector is not None else 0)


def drop_speechless_rest(rec, frames):
    """The audio after *rec*'s last cut, or none when the pause detector
    heard no speech in it: room noise longer than the tap check would
    otherwise reach the engine, which invents a word for it ("Yeah."). A word
    too short to cut a phrase of its own ("yes") is speech here."""
    if rec.last_chunk_speech == 0.0:
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
# thread or the portal's shortcuts, and taken by the service tick. Two Events
# rather than one flag, so a start and a stop can't overwrite each other.
# Hands-free's own stop is the recording's (Recording.went_quiet).
start_requested = threading.Event()
stop_requested = threading.Event()
# Set by the SIGTERM handler (Quit, Restart Service, an update), taken by the
# service tick, which finishes what's being typed before the process exits.
quit_asked = False
# Counted up by the SIGUSR1 handler (the tray's toggle); the service tick
# acts on a change (service_tick._take_toggle).
toggles_asked = 0

# Set alongside start_requested when the Flatpak's toggle SHORTCUT (not a
# menu) started it, so a quick second press can switch it to hands-free.
started_from_toggle_key = threading.Event()


def _sd_callback(rec, indata, frames_count, time_info, status):
    """sounddevice's audio callback for recording *rec*'s stream: collect
    its frames while it records. A stream that didn't stop in time
    (mic_stream.close gave up on it) may still call this, and a call can
    still be under way when its recording stops and the next begins: what
    it does stays with *rec*, never the newer recording."""
    if not (rs.state.is_recording and rec is rs.state.current):
        return
    block = indata.tobytes()
    with type_at_pause.chunk_lock:
        if rec.captured:
            return          # finish_capture has taken its audio
        rec.frames.append(block)

    # Chunked dictation: at a pause, the phrase so far goes off to be typed.
    chunker = rec.chunker
    if chunker is not None and chunker.feed(indata):
        type_at_pause.cut_chunk(rec, chunker)

    # Hands-free auto-stop: when the speaker has finished, ask the service
    # loop to stop it, exactly as a second press of the toggle key would.
    detector = rec.silence
    if detector is not None and detector.feed(indata):
        if detector.nothing_said:
            logger.info("Auto-stop: nothing said, stopping without typing anything")
        else:
            logger.info("Auto-stop: speaker went quiet, stopping the recording")
        rec.went_quiet = True

    # Feed the recording indicator whatever its active style needs.
    if indicator:
        feed_indicator(indicator, indata, spectrum_processor)


def open_input_stream(rec, device_idx) -> None:
    """Open and start recording *rec*'s microphone stream. Raises if the
    device is unusable."""
    # Use the device's native sample rate (may differ from 16kHz on ALSA hw: devices)
    global spectrum_processor
    rec.samplerate = mic_stream.input_samplerate(device_idx, engine.SAMPLE_RATE)
    # The bars' bands are worked out for one rate; built before the stream
    # starts, so its callback never sees one half made.
    if spectrum_processor is not None and spectrum_processor.sample_rate != rec.samplerate:
        from .indicator_dsp import SpectrumProcessor
        spectrum_processor = SpectrumProcessor(sample_rate=rec.samplerate,
                                               bins=spectrum_processor.bins)
    sd.default.channels = CHANNELS
    sd.default.samplerate = rec.samplerate
    rec.stream = sd.InputStream(callback=functools.partial(_sd_callback, rec), dtype='int16',
                                device=device_idx)
    rec.stream.start()


def start_recording(beeps_on: bool, notify_on: bool, input_device_idx,
                    from_toggle: bool = False, edit_mode: bool = False,
                    held_send: bool = False):
    """Begin capturing audio: the new Recording, or None when no microphone
    would open.

    *from_toggle* marks a start by the toggle key, so a quick second tap can
    switch it to hands-free (see toggle_pressed_while_recording).
    *held_send*: the press that starts it already held a pending send.

    On failure this releases any input-device grabs before returning. The
    keyboard is grabbed just before this is called, so without that release a
    microphone error would leave the user unable to type anywhere at all.
    """
    # Pressing a hotkey while a send counts down holds it: a tap leaves the
    # text unsent, a hold dictates more onto it. Counted before the
    # microphone opens, so a send set up meanwhile waits for this recording.
    serial, held = send_countdown.recording_begins()
    rec = rs.state.current = rs.Recording(
        rs.Message(serial=serial), edit_mode=edit_mode,
        held_send=held or held_send, press_t0=time.time())
    # Set before the stream starts — _sd_callback drops frames unless it's set.
    rs.state.is_recording = True
    if engine.model is None:
        # Power Saving let the model go: load it while the user talks.
        threading.Thread(target=engine.wake, kwargs={"fresh": True}, daemon=True,
                         name="tt-wake-model").start()
    input_device_idx = follow_system_default(input_device_idx)
    try:
        open_input_stream(rec, input_device_idx)
    except Exception as first_error:
        # The mic is stored as a device NUMBER, resolved once at startup. Unplug
        # a USB device and the numbering shifts, leaving that number pointing at
        # nothing. Re-resolve by name and try once more before giving up.
        logger.warning(f"Microphone {input_device_idx} failed ({first_error}); re-resolving")
        stop_stream_safely(rec)
        try:
            fresh_idx = pick_input_device(load_config().mic)
            open_input_stream(rec, fresh_idx)
            logger.info(f"Microphone re-resolved to device {fresh_idx}")
            if fresh_idx != input_device_idx:
                notices.warn_mic_switched(input_device_idx, fresh_idx)
        except Exception as e:
            # Genuinely unavailable: unplugged, or held exclusively by another
            # app. Undo everything and tell the user; failing silently would
            # leave the keyboard grabbed and the machine unusable.
            logger.error(f"Could not start recording: {e}", exc_info=True)
            rs.state.is_recording = False
            # Counted already (recording_begins), so a send set up meanwhile
            # was held for it: this one ended, cancelled, and that's said.
            rec.message.cancelled = True
            stop_stream_safely(rec)
            keyboard_grab.release_all()
            beeps.beep(beeps_on, *CANCEL_BEEP)
            if notify_on:
                notify_module.notify("Microphone unavailable",
                                     "Check that no other app is using it.")
            _leave_held_send_unsent(rec)
            return None

    type_at_pause.arm_chunker(rec)
    pause_media_players()
    rec.start_beep = beeps.beep(beeps_on, *START_BEEP)
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
    if from_toggle and rec.start_beep is not None:
        beeps.wait_until_played(over=rec.start_beep)
    rec.toggle_started_at = time.time() if from_toggle else None
    logger.info(f"Recording started in {time.time() - rec.press_t0:.2f}s")
    return rec


def cancel_recording(rec, beeps_on: bool, notify_on: bool, reason="Cancelled"):
    """End recording *rec*, the current one (this clears is_recording),
    without transcribing it, telling the user *reason*. A send it held stays
    unsent, and that's said (_leave_held_send_unsent): Esc, a tap,
    hands-free hearing nothing."""
    rs.state.is_recording = False
    rec.message.cancelled = True
    stop_stream_safely(rec)
    resume_media_players()
    beeps.beep(beeps_on, *CANCEL_BEEP)
    logger.info(f"Recording cancelled: {reason}")
    if notify_on:
        notify_module.notify(reason, "")

    if indicator:
        indicator.hide_indicator()
    tray_link.notify_recording_state(False)
    _leave_held_send_unsent(rec)


def _leave_held_send_unsent(rec) -> None:
    """Recording *rec* ended with nothing to add to a send it held, when it
    began (rec.held_send) or one set up for it since: say "Not sent",
    once, and give back the space the send left off. That goes in now, or
    when the dictation key that's still down comes up (hotkeys): after Esc
    during a hold, or a microphone that didn't open. A start without a key
    (the tray, tuxtalk --start) has none to wait for."""
    said = send_countdown.say_held_for(rec.message.serial)
    if not said and rec.held_send:
        send_countdown.say_held()
        said = True
    if said:
        # After what's still being typed, not into the middle of it.
        dictation_worker.submit(send_countdown.give_back_space)


def toggle_pressed_while_recording(beeps_on: bool) -> bool:
    """A toggle-key press arrived during a recording. Returns True if it was
    the second tap of a double-tap and switched the recording to hands-free
    (the caller must then NOT stop it); False means a normal stop.

    A single tap keeps the toggle a plain on/off switch. Only a double-tap,
    with the option on, arms auto-stop. A word said before the second tap
    counts as speech; the start beep the mic may pick up first does not.
    """
    rec = rs.state.current
    started = rec.toggle_started_at
    if (live.auto_stop_seconds <= 0 or started is None or rec.silence is not None
            or time.time() - started > DOUBLE_TAP_S):
        return False
    detector = silence.SilenceDetector(live.auto_stop_seconds, rec.samplerate)
    beep_hz = START_BEEP[0] if rec.start_beep is not None else None
    if silence.has_a_word(frames_f32(list(rec.frames)), rec.samplerate, ignoring_hz=beep_hz):
        detector.heard_speech()
    rec.silence = detector
    logger.info("Double-tap: this recording will stop by itself when you go quiet")
    beeps.beep(beeps_on, *HANDS_FREE_BEEP)
    return True


def finish_capture(rec, beeps_on: bool, notify_on: bool):
    """End recording *rec*, the current one, quickly and hand back its audio
    (the fast half of stop).

    Validates the hold time, stops the stream, hides the indicator, and takes
    the captured frames, marking the recording captured under chunk_lock so
    no block or cut is added to them afterwards. Returns (frames, rec_sr) or
    None if the recording was cancelled or too short. The next recording is a
    Recording of its own, so the frames returned stay this one's even if it
    begins at once, which is what lets the dictation worker transcribe and
    type them while the next recording records, without dropping or mixing
    audio."""
    if _dropped_as_silent(rec, beeps_on, notify_on) or _dropped_as_a_tap(rec, beeps_on, notify_on):
        return None
    with type_at_pause.chunk_lock:
        # No more cuts or blocks: from here on the audio is the last chunk,
        # and a callback still under way adds nothing to it.
        rec.captured = True
        chunker, rec.chunker = rec.chunker, None
        rec.last_chunk_speech = chunker.speech_since_cut if chunker is not None else None
        frames = rec.frames
    if chunker is not None and not rec.chunks_submitted:
        logger.info(f"Chunked dictation: no pause found ({chunker.describe()})")
    rs.state.is_recording = False
    stop_stream_safely(rec)
    resume_media_players()
    tray_link.notify_recording_state(False)  # the tray icon shows recording stopped
    trim_auto_stop_tail(rec, frames)
    if indicator:
        indicator.hide_indicator()
    return frames, rec.samplerate


def _dropped_as_silent(rec, beeps_on: bool, notify_on: bool) -> bool:
    """Hands-free with nothing said: cancel *rec*, and say so if the mic is
    dead. Transcribing room noise can type a stray "Thank you." (see
    silence.is_speechless_tap)."""
    if rec.silence is None or not rec.silence.nothing_said:
        return False
    rec.silence = None
    dead_mic = frames_are_dead_mic(rec.frames, rec.samplerate)
    cancel_recording(rec, beeps_on, notify_on and not dead_mic,
                     "Nothing heard, so nothing was typed")
    if dead_mic:
        notices.warn_silent_mic()
    return True


def _dropped_as_a_tap(rec, beeps_on: bool, notify_on: bool) -> bool:
    """A press too short to be a dictation: cancel *rec*. A tap that held a
    pending send leaves that text unsent, as it is."""
    held_ms = int((time.time() - (rec.press_t0 or time.time())) * 1000)
    if rec.held_send and send_countdown.owes_space() and not rec.chunks_submitted:
        # The hotkey that held the send is up now; a tap leaves the text as
        # it is, a dictation goes after this space. On the dictation worker,
        # after what's still being typed.
        dictation_worker.submit(send_countdown.give_back_space)
    if held_ms >= MIN_HOLD_MS and not (rec.held_send and rec.released_while_starting):
        return False
    # A tap that held a send is told as "Not sent" alone (cancel_recording).
    if rec.held_send or send_countdown.held_for(rec.message.serial):
        cancel_recording(rec, beeps_on, False, send_countdown.HELD_NOTICE)
    else:
        cancel_recording(rec, beeps_on, notify_on, f"Cancelled (held {held_ms} ms)")
    return True
