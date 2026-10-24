"""Opening a microphone at a rate it records at, and closing its stream
without hanging on an audio system that stopped answering, or quitting
while one is stuck. Shared by the dictation service, Preferences'
microphone tests and the beeps.
"""
import atexit
import threading

import sounddevice as sd

from .logger import setup_logger

logger = setup_logger(__name__)

# How long stopping a stream may take before the caller carries on without
# it. A stream that vanished from PipeWire mid-recording (the microphone
# muted) made PortAudio's stop() wait forever: in the service the indicator
# stayed up and no hotkey worked again until it was restarted.
STOP_TIMEOUT_S = 2.0
# How long a stream being closed as TuxTalk quits may take to close.
EXIT_WAIT_S = 0.5
# Streams being stopped and closed, with those whose stop() never returned,
# which stay: nothing closes them again from another thread (a few kilobytes
# each). _closed is notified as each one is closed.
_closing = set()
_closed = threading.Condition()


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
    then. Without, it returns at once (a window's thread) with an Event set
    once the stream is closed or given up on, and a stream that never stops
    is logged all the same."""
    with _closed:
        _closing.add(stream)
    if not wait:
        over = threading.Event()

        def close_then_say_so():
            try:
                close(stream)
            finally:
                over.set()
        threading.Thread(target=close_then_say_so, daemon=True, name="tt-mic-close").start()
        return over
    closer = threading.Thread(target=_stop_and_close, args=(stream,), daemon=True,
                              name="tt-mic-stop")
    closer.start()
    closer.join(STOP_TIMEOUT_S)
    if closer.is_alive():
        logger.error(f"The microphone stream didn't stop within {STOP_TIMEOUT_S:g}s "
                     "(the audio system stopped answering); carrying on without it")
        return False
    return True


def _stop_and_close(stream):
    # sounddevice ignores PortAudio's errors on these unless told not to.
    try:
        stream.stop(ignore_errors=False)
    except Exception as e:
        logger.warning(f"Could not stop the microphone stream: {e}")
    finally:
        try:
            stream.close(ignore_errors=False)
        except Exception as e:
            logger.warning(f"Could not close the microphone stream: {e}")
        with _closed:
            _closing.discard(stream)
            _closed.notify_all()


def leave_portaudio_running():
    """Keep sounddevice's exit handler from ending PortAudio under a stream
    that is stuck as TuxTalk quits: ending it while another thread is still
    in that stream can hang, or crash. sounddevice is told PortAudio was
    never started, so its handler leaves it, and the system closes the
    stream with the process. For exit handlers registered after
    sounddevice's own, which run before it (atexit.unregister can't stop a
    handler once exit has begun)."""
    sd._initialized = 0


def _at_exit():
    """Let a microphone stream being closed as TuxTalk quits finish closing,
    briefly. One still in PortAudio by then is stuck in it, and left to the
    system (leave_portaudio_running)."""
    with _closed:
        if not _closed.wait_for(lambda: not _closing, EXIT_WAIT_S):
            leave_portaudio_running()


def _check_exit_handler(module):
    """Whether *module* (sounddevice) counts the times it started PortAudio
    in _initialized, which its exit handler ends PortAudio by and
    leave_portaudio_running sets. A sounddevice that doesn't is logged: a
    stream stuck as TuxTalk quits can then hold up quitting."""
    if isinstance(getattr(module, "_initialized", None), int):
        return True
    logger.warning("This sounddevice keeps no _initialized count: a sound or microphone stuck "
                   "as TuxTalk quits can hold up quitting")
    return False


_check_exit_handler(sd)
atexit.register(_at_exit)
