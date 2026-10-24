"""The short tones that say a recording started, went hands-free, finished
or failed, and the player every TuxTalk sound goes through.

A sound has to be heard to the end. sounddevice.play() ended its stream once
the last samples were handed over, and on PortAudio's ALSA path that threw
away what the sound system still held in the stream's buffer: about its
output latency. Where that is as long as a beep (a slow desktop, issue #21)
the beep was cut short or never heard, the 0.07 s hands-free beep first.

So a sound plays on a stream of its own, followed by silence, and the stream
is closed only once two things are true. The stream has taken in more
silence after the sound than its buffer holds (the latency it reports plus
the most frames it asked for at once), so none of the sound is left in it.
And where PortAudio says when the samples it is given will be heard
(outputBufferDacTime), that moment has passed.

Measured by recording a VM's sound card: through PipeWire's ALSA plugin the
first was exactly where beeps stopped being cut, and the reported time came
0.2 s later, as it counts buffers PipeWire keeps on a close. Through the
PulseAudio plugin to PipeWire's PulseAudio server the two were 0.04 s
apart. Real PulseAudio reported no time, so only the first applies there.

Sounds play one after another on one thread of their own. A sound server
that stops answering holds up only that thread; whoever waits for a sound
(wait_until_played) waits a bounded time.
"""
import queue
import sys
import threading
import time

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
# The longest wait_until_played waits. Measured in a VM on PipeWire, the
# start beep took 0.39 s at its usual 0.035 s of latency and 0.53 s at 0.17 s.
WAIT_LIMIT_S = 1.0
# A beep that couldn't start for this long was held up by a stuck sound
# server; by then it would only confuse.
STALE_S = 2.0
# How much longer than its length and latency a sound may take to be handed
# over before it counts as stuck.
STUCK_S = 2.0
# PortAudio's word on when a sample is heard, further ahead than this, is no
# real time (a backend that reports none gives 0).
MAX_AHEAD_S = 2.0
# Where the sound server's own output is, best first. The default device can
# be a USB microphone with no speaker at all.
OUTPUT_HINTS = ("pipewire", "pulse", "sysdefault", "default", "analog")

_lock = threading.Lock()
_devices = {}            # the rate asked for: (device index or None, rate it plays)
_latency = 0.0
_latency_logged = False
_underflow_logged = False
_failure_logged = False

_jobs = queue.Queue()
_finished = threading.Condition()
_asked = 0               # sounds asked for so far, and how many of them are over
_over = 0
_worker = None


class Sound:
    """One sound asked for. The sound thread fills in when it is heard."""

    def __init__(self, length):
        self.length = length        # seconds
        self.heard_until = None     # time.monotonic() its last sample handed over is heard
        self.over = False           # played, failed or dropped


def output_device(samplerate):
    """The sound server's output device and a rate it plays mono: *samplerate*,
    else the device's own. (None, samplerate) is sounddevice's default.

    Found once per rate, as PortAudio's device list is fixed when it starts;
    a search that raised (sd.PortAudioError) is tried again next time."""
    with _lock:
        if samplerate not in _devices:
            _devices[samplerate] = _search(samplerate)
        return _devices[samplerate]


def _search(samplerate):
    devices = sd.query_devices()
    for hint in OUTPUT_HINTS:
        for i, d in enumerate(devices):
            if d["max_output_channels"] <= 0 or hint not in d.get("name", "").lower():
                continue
            for rate in dict.fromkeys((samplerate, int(d["default_samplerate"]))):
                try:
                    sd.check_output_settings(device=i, samplerate=rate, channels=1)
                except sd.PortAudioError:
                    continue
                return i, rate
    return None, samplerate


def output_latency():
    """The latency the last sound's stream reported, in seconds; 0.0 before
    the first one."""
    return _latency


def heard_in_recording(sound, recorded, rate):
    """How many samples at the start of a recording a microphone near the
    speakers may have heard *sound* in, when *recorded* samples at *rate*
    have come in so far and the last of them just now.

    That is up to the sound's last sample heard, and its length again for the
    microphone's own delay. A sound that was never played (None: beeps off)
    is in none of it. One that isn't over may be playing right now, so it
    may be in all of it."""
    if sound is None or (sound.over and sound.heard_until is None):
        return 0
    if not sound.over:
        return sys.maxsize
    recorded_from = time.monotonic() - recorded / rate
    return max(0, int((sound.heard_until + sound.length - recorded_from) * rate))


def tone(freq, duration, rate=SAMPLE_RATE):
    """A sine tone of *duration* seconds at *rate* samples a second."""
    t = np.arange(int(rate * duration)) / rate
    return (np.sin(2 * np.pi * freq * t) * VOLUME).astype(np.float32)


def beep(enabled: bool, freq, duration):
    """Play a tone, if beeps are on. Returns at once, with its Sound (None
    with beeps off); the tone plays on the sound thread after any sound
    before it."""
    if not enabled:
        return None
    asked_at = time.monotonic()
    sound = Sound(duration)

    def job():
        if time.monotonic() - asked_at > STALE_S:
            logger.info("Skipped a beep the sound output held up")
            return
        device, rate = output_device(SAMPLE_RATE)
        _play(tone(freq, duration, rate), rate, device, sound)
    _submit(job, "a beep", sound)
    return sound


def play(samples, rate, failed=None):
    """Play mono float32 *samples* recorded at *rate*, after any sound
    before it. Returns at once. *failed* is called with the error's text,
    on the sound thread, if it couldn't be played."""
    sound = Sound(len(samples) / rate)

    def job():
        device, device_rate = output_device(rate)
        resampled = samples
        if device_rate != rate:
            positions = np.linspace(0, len(samples) - 1, int(len(samples) * device_rate / rate))
            resampled = np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)
        _play(resampled, device_rate, device, sound)
    _submit(job, "a sound", sound, failed)


def wait_until_played(limit=WAIT_LIMIT_S):
    """Wait until every sound asked for so far is over: played out to the
    speakers, as far as the stream can tell, or failed. At most *limit*
    seconds; False if it took longer."""
    with _finished:
        ticket = _asked
        if _finished.wait_for(lambda: _over >= ticket, limit):
            return True
    logger.warning(f"A sound took over {limit}s to play; carrying on without it")
    return False


def _submit(job, what, sound, failed=None):
    global _asked, _worker
    with _finished:
        _asked += 1
        _jobs.put((job, what, sound, failed))
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="tt-sounds", daemon=True)
            _worker.start()


def _work():
    """The sound thread: plays each sound asked for, in turn. A sound is
    never worth a recording, so any audio error is logged and dropped."""
    global _over, _failure_logged
    while True:
        job, what, sound, failed = _jobs.get()
        try:
            job()
        except Exception as e:
            # A missing output fails every sound: one warning says so.
            if not _failure_logged:
                _failure_logged = True
                logger.warning(f"Could not play {what}: {e} (later failures are not logged)")
            if failed:
                failed(str(e))
        finally:
            sound.over = True
            with _finished:
                _over += 1
                _finished.notify_all()


class _Playback:
    """One sound fed to a stream's callback, then silence until the stream's
    buffer can no longer hold any of the sound."""

    def __init__(self, samples, rate, sound):
        self.samples, self.rate, self.sound = samples, rate, sound
        self.fed = 0
        self.silence = 0            # frames of silence fed after the sound
        self.period = 0             # the most frames one callback asked for
        self.latency = 0.0          # the stream's, set before it starts
        self.reported = False       # PortAudio said when the last of it is heard
        self.underflows = 0
        self.out_of_buffer = threading.Event()

    def callback(self, outdata, frames, time_info, status):
        """PortAudio's call for the next *frames* to play: the sound's, then
        silence. Runs on PortAudio's own thread."""
        now = time.monotonic()
        if status.output_underflow:
            self.underflows += 1
        self.period = max(self.period, frames)
        chunk = self.samples[self.fed:self.fed + frames]
        outdata[:len(chunk), 0] = chunk
        outdata[len(chunk):] = 0
        if len(chunk):
            self.fed += len(chunk)
            self._note_heard_until(now, time_info, len(chunk))
        if self.fed < len(self.samples):
            return
        self.silence += frames - len(chunk)
        # PortAudio's ALSA output reports as latency its buffer less one
        # period, and asks for at most a period at a time. A buffer that has
        # taken in more silence after the sound than it can hold has none of
        # the sound left, and closing the stream throws away only silence.
        if self.silence >= self.latency * self.rate + self.period:
            self.out_of_buffer.set()

    def _note_heard_until(self, now, time_info, count):
        """When the last of *count* samples just handed over is heard: when
        PortAudio says this buffer starts playing, where it says, else one
        latency from now, about what the stream holds ahead of it."""
        ahead = time_info.outputBufferDacTime - time_info.currentTime
        self.reported = 0 < ahead < MAX_AHEAD_S
        if not self.reported:
            ahead = self.latency
        self.sound.heard_until = now + ahead + count / self.rate


def _play(samples, rate, device, sound):
    """Play *samples* to the end on a stream of their own, then close it.
    Raises on an audio error, or when the output stops taking samples."""
    global _latency, _latency_logged, _underflow_logged
    playback = _Playback(samples, rate, sound)
    stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32", device=device,
                             callback=playback.callback)
    try:
        playback.latency = _latency = stream.latency
        if not _latency_logged:
            _latency_logged = True
            logger.info(f"Beeps: the output's latency is {stream.latency:.3f}s")
        stream.start()
        if not playback.out_of_buffer.wait(len(samples) / rate + stream.latency + STUCK_S):
            raise TimeoutError("the sound output stopped taking samples")
        if playback.reported:
            time.sleep(min(max(0.0, sound.heard_until - time.monotonic()), MAX_AHEAD_S))
        if playback.underflows and not _underflow_logged:
            _underflow_logged = True    # a gap in a sound, logged once a session
            logger.info(f"The sound output ran dry {playback.underflows} time(s) during a sound")
    finally:
        try:
            stream.close(ignore_errors=False)
        except sd.PortAudioError as e:
            logger.warning(f"Could not close a sound stream: {e}")

