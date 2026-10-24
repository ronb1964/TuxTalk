"""Opening a microphone at a rate it records at, and closing its stream
without hanging on an audio system that stopped answering. Shared by the
dictation service and Preferences' microphone tests.
"""
import threading

import sounddevice as sd

from .logger import setup_logger

logger = setup_logger(__name__)

# How long stopping a stream may take before the caller carries on without
# it. A stream that vanished from PipeWire mid-recording (the microphone
# muted) made PortAudio's stop() wait forever: in the service the indicator
# stayed up and no hotkey worked again until it was restarted.
STOP_TIMEOUT_S = 2.0
# Streams whose stop() never returned. Kept so nothing closes them again
# from another thread; a few kilobytes each.
_stuck = []


def input_samplerate(device, preferred):
    """*preferred* where input *device* records mono at it, else the
    device's own rate: some ALSA hw: devices take only theirs (often
    48000 Hz), and the recording is resampled afterwards. Where neither
    checks out, *preferred*, so that opening the stream says what's wrong."""
    try:
        sd.check_input_settings(device=device, samplerate=preferred, channels=1)
        return preferred
    except sd.PortAudioError:
        pass
    try:
        own = int(sd.query_devices(device)["default_samplerate"])
        sd.check_input_settings(device=device, samplerate=own, channels=1)
    except Exception as e:
        logger.info(f"Microphone {device} takes neither {preferred} Hz nor its own rate: {e}")
        return preferred
    logger.info(f"Microphone {device} doesn't take {preferred} Hz; recording at {own} Hz")
    return own


def close(stream, wait=True):
    """Stop and close *stream* on a thread of its own; close() runs even
    when stop() fails (an unplugged microphone), so the device is free to
    be opened again.

    With *wait*, for at most STOP_TIMEOUT_S: False if it hadn't stopped by
    then. Without, it returns at once (a window's thread), and a stream
    that never stops is logged all the same."""
    if not wait:
        threading.Thread(target=close, args=(stream,), daemon=True, name="tt-mic-close").start()
        return None
    closer = threading.Thread(target=_stop_and_close, args=(stream,), daemon=True,
                              name="tt-mic-stop")
    closer.start()
    closer.join(STOP_TIMEOUT_S)
    if closer.is_alive():
        _stuck.append(stream)
        logger.error(f"The microphone stream didn't stop within {STOP_TIMEOUT_S:g}s "
                     "(the audio system stopped answering); carrying on without it")
        return False
    return True


def _stop_and_close(stream):
    try:
        stream.stop()
    except Exception as e:
        logger.warning(f"Could not stop the microphone stream: {e}")
    finally:
        try:
            stream.close()
        except Exception as e:
            logger.warning(f"Could not close the microphone stream: {e}")
