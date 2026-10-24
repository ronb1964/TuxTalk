"""The short tones that say a recording started, went hands-free, finished
or failed.

A beep has to be heard in full. sounddevice.play() aborts its stream as soon
as the last samples are handed over, and an abort throws away whatever the
sound system still holds in the stream's buffer: about its output latency.
Where that latency is as long as a beep (a slow desktop, issue #21) the beep
was cut short or never heard at all, the 0.07 s hands-free beep first. So a
beep is followed by silence a little longer than the buffer, and the abort
only throws away silence.
"""
import numpy as np
import sounddevice as sd

from .logger import setup_logger

logger = setup_logger(__name__)

# (frequency in Hz, length in seconds)
START_BEEP = (1200, 0.12)
HANDS_FREE_BEEP = (1600, 0.07)  # confirms a double-tap switched the recording to hands-free
CANCEL_BEEP = (500, 0.12)
READY_BEEP = (1000, 0.09)

SAMPLE_RATE = 44100
VOLUME = 0.2
# A stream's buffer holds one period more than the latency it reports.
# PipeWire's largest period is 2048 frames at 48 kHz, 43 ms: this covers it.
PERIOD_MARGIN_S = 0.05

_device = None
_device_found = False
_latency_logged = False


def _output_device():
    """The sound server's output device, found once: the device list doesn't
    change during a session. None means sounddevice's default."""
    global _device, _device_found
    if _device_found:
        return _device
    _device_found = True
    for name_hint in ("pipewire", "pulse", "sysdefault", "default"):
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0 and name_hint in d.get("name", "").lower():
                try:
                    sd.check_output_settings(device=i, samplerate=SAMPLE_RATE, channels=1)
                except sd.PortAudioError:
                    continue   # can't play a mono 44.1 kHz tone: try the next one
                _device = i
                return i
    return None


def tone(freq, duration, tail_s=0.0):
    """A sine tone of *duration* seconds followed by *tail_s* of silence."""
    t = np.arange(int(SAMPLE_RATE * duration)) / SAMPLE_RATE
    sound = (np.sin(2 * np.pi * freq * t) * VOLUME).astype(np.float32)
    return np.concatenate((sound, np.zeros(int(SAMPLE_RATE * tail_s), np.float32)))


def beep(enabled: bool, freq=1000, duration=0.15):
    """Play a tone, if beeps are on, and return once the sound system has all
    of it: a beep right after can't cut it off. Takes up to PERIOD_MARGIN_S
    longer than the tone itself, however slow the output.

    A beep is never worth a recording, so any audio error is logged and
    dropped."""
    if not enabled:
        return
    global _latency_logged
    try:
        stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                 device=_output_device())
    except Exception as e:
        logger.warning(f"Could not play a beep: {e}")
        return
    try:
        if not _latency_logged:
            _latency_logged = True
            logger.info(f"Beeps: the output's latency is {stream.latency:.3f}s")
        stream.start()
        # write() returns once the last samples are in the buffer, which then
        # holds only silence; close() discards that and nothing else.
        stream.write(tone(freq, duration, stream.latency + PERIOD_MARGIN_S))
    except Exception as e:
        logger.warning(f"Could not play a beep: {e}")
    finally:
        stream.close()
