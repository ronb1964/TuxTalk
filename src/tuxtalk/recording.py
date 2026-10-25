"""The microphone side of a dictation: which input device to record from
(following the system's default as it changes), pausing music and turning
other sound down while recording, stopping a stream safely, and trimming
what a recording ends with (the auto-stop's silence, a speechless rest)
before it is transcribed."""
import atexit

import numpy as np

from . import config as config_module
from . import live_settings as live
from . import mic_stream, silence
from . import recording_state as rs
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
    drop_tail_samples(frames, detector.tail_to_trim() if detector is not None else 0)


def drop_speechless_rest(frames):
    """The audio after the last cut, or none when the pause detector heard no
    speech in it: room noise longer than the tap check would otherwise reach
    the engine, which invents a word for it ("Yeah.")."""
    if rs.state.last_chunk_speech == 0.0:
        logger.info("No speech after the last chunk; the rest isn't transcribed")
        return []
    return frames


def drop_tail_samples(frames, to_trim):
    """Remove whole trailing blocks (int16 mono) holding at most *to_trim*
    samples. A block is never split, so speech is never touched."""
    while frames and to_trim > 0:
        block_samples = len(frames[-1]) // 2          # int16 mono
        if block_samples > to_trim:
            break
        frames.pop()
        to_trim -= block_samples


def frames_f32(frames):
    """Raw int16 recording frames as float samples in -1..1."""
    return np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32) / 32768.0


def frames_are_dead_mic(frames, rec_sr) -> bool:
    """silence.is_dead_mic for raw int16 recording frames."""
    return silence.is_dead_mic(frames_f32(frames), rec_sr)
