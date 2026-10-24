"""Preferences' microphone test: record yourself, watch the level as you
talk, get a grade for it, and hear the recording played back.

The replay goes through the same player as the beeps (beeps.play), on the
sound thread, so it is heard to the end (sounddevice.play() lost about one
output latency at the end of what it played, issue #21) and the window
stays free while it plays.
"""
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

import numpy as np  # noqa: E402
import sounddevice as sd  # noqa: E402

from . import beeps, mic_stream  # noqa: E402
from .logger import setup_logger  # noqa: E402

logger = setup_logger(__name__)


class SegmentedVUMeter(Gtk.DrawingArea):
    """
    Custom segmented VU meter widget that looks like a classic LED audio meter.

    Segments are colored based on their position:
    - Green: Safe/normal levels (0-60%)
    - Yellow: Getting loud (60-80%)
    - Red: Danger/clipping zone (80-100%)

    Unlit segments are shown in dark gray.
    """

    def __init__(self, num_segments=20):
        """A meter of *num_segments* LEDs, all unlit."""
        super().__init__()
        self.num_segments = num_segments
        self.level = 0.0  # 0.0 to 1.0
        self.set_size_request(200, 20)
        self.connect("draw", self._on_draw)

        # Colors for segments based on position (as RGB tuples, 0-1 range)
        # Bright, vivid colors like a real VU meter / LED display
        # Green zone: 0-60% of segments
        # Yellow zone: 60-80% of segments
        # Red zone: 80-100% of segments
        self.color_green = (0.0, 0.90, 0.46)     # #00E676 - bright lime green
        self.color_yellow = (1.0, 0.84, 0.0)     # #FFD600 - bright yellow
        self.color_red = (1.0, 0.09, 0.27)       # #FF1744 - bright red
        self.color_off = (0.18, 0.18, 0.18)      # #2E2E2E - dark gray for unlit
        self.color_border = (0.12, 0.12, 0.12)   # #1F1F1F - darker border

    def set_value(self, level):
        """Set the meter level (0.0 to 1.0)."""
        self.level = max(0.0, min(1.0, level))
        self.queue_draw()

    def _get_segment_color(self, segment_index, is_lit):
        """Get the color for a segment based on its position and lit state."""
        if not is_lit:
            return self.color_off

        position = segment_index / self.num_segments

        if position < 0.60:
            return self.color_green   # Green zone (0-60%)
        elif position < 0.80:
            return self.color_yellow  # Yellow zone (60-80%)
        else:
            return self.color_red     # Red zone (80-100%)

    def _on_draw(self, widget, cr):
        """Draw the segmented meter."""
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()

        segment_gap = 2
        total_gaps = (self.num_segments - 1) * segment_gap
        segment_width = (width - total_gaps) / self.num_segments
        segment_height = height - 4  # Leave some margin

        lit_segments = int(self.level * self.num_segments)
        for i in range(self.num_segments):
            x = i * (segment_width + segment_gap)
            y = 2  # Top margin

            is_lit = i < lit_segments
            color = self._get_segment_color(i, is_lit)

            cr.set_source_rgb(*color)
            cr.rectangle(x, y, segment_width, segment_height)
            cr.fill()

            # A subtle border round each segment, like separate LEDs.
            cr.set_source_rgb(*self.color_border)
            cr.rectangle(x, y, segment_width, segment_height)
            cr.set_line_width(1)
            cr.stroke()

        return False


class _Take:
    """One recording of the test, from Start Recording to Stop: its rate and
    what came in. Its stream's callback adds to this take only, so a stream
    from an earlier one, still being closed, adds nothing to a newer one."""

    def __init__(self, rate):
        self.rate = rate
        self.frames = []
        self.levels = []
        self.peak = 0.0
        self.stream = None


def after_close(closing, then):
    """Call *then* once the microphone's last stream is closed: *closing* is
    the Event mic_stream.close(wait=False) returned for it, or None. At once
    if it is closed, else on the GTK thread, with the wait on a thread of its
    own: until a stream is closed it holds an ALSA hw: device, and opening
    that again fails as busy. A close that never ends is given up on after
    STOP_TIMEOUT_S, so this waits no longer."""
    if closing is None or closing.is_set():
        then()
        return

    def wait():
        closing.wait(mic_stream.STOP_TIMEOUT_S + 1)
        GLib.idle_add(lambda: then() and False)
    threading.Thread(target=wait, daemon=True, name="tt-mic-wait").start()


class MicTest:
    """The level meter, the grade under it and the Start, Stop and Replay
    buttons, and the recording they share. *device* gives the chosen
    microphone's device index, and *show_error*(title, text) shows what went
    wrong in a dialog."""

    def __init__(self, device, show_error):
        """Nothing recorded yet, and the widgets made for build() to place."""
        self._device = device
        self._show_error = show_error
        self._take = None           # the recording under way
        self.recorded_audio = None
        self._rate = 16000          # of recorded_audio
        self._closing = None        # the last stream's close, from Stop
        self._closed = False
        self._replaying = None
        self.meter = SegmentedVUMeter(num_segments=20)
        self.status = Gtk.Label(label="Click 'Start Recording' and speak to test your mic levels")
        self.start_btn = Gtk.Button(label="● Start Recording")
        self.stop_btn = Gtk.Button(label="■ Stop Recording")
        self.replay_btn = Gtk.Button(label="▶ Replay")
        # Without the symbols, which a screen reader reads out ("black circle").
        for button, name in ((self.start_btn, "Start Recording"),
                             (self.stop_btn, "Stop Recording"), (self.replay_btn, "Replay")):
            button.get_accessible().set_name(name)

    @property
    def recording(self):
        """Whether a recording is under way."""
        return self._take is not None

    def build(self, box):
        """Add the meter, the grade and the buttons to *box*, a vertical Gtk.Box."""
        level_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        level_label = Gtk.Label(label="Input Level:")
        level_label.set_mnemonic_widget(self.meter)     # what a screen reader names it by
        level_box.pack_start(level_label, False, False, 0)
        self.meter.set_hexpand(True)
        self.meter.set_tooltip_text(
            "Green = safe levels, Yellow = getting loud, Red = too loud!\n"
            "Aim to stay mostly in the green with occasional yellow peaks."
        )
        level_box.pack_start(self.meter, True, True, 0)
        box.pack_start(level_box, False, False, 0)

        self.status.set_xalign(0)
        self.status.set_margin_start(75)  # Align with level bar
        self.status.set_line_wrap(True)
        box.pack_start(self.status, False, False, 0)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        for button, clicked in ((self.start_btn, self.start), (self.stop_btn, self.stop),
                                (self.replay_btn, self.replay)):
            button.get_child().set_xalign(0.5)
            button.connect("clicked", clicked)
            button_box.pack_start(button, False, False, 0)
        self.stop_btn.set_sensitive(False)
        self.replay_btn.set_sensitive(False)
        box.pack_start(button_box, False, False, 0)

    def _show_level(self, take, level):
        """Show one block's level on the meter (GTK thread, via GLib.idle_add),
        unless *take* has been stopped since."""
        if take is self._take:
            self.meter.set_value(level)
            take.levels.append(level)
            take.peak = max(take.peak, level)
        return False

    def start(self, _button=None):
        """Start recording the chosen microphone, once the last recording's
        stream is closed."""
        self._call_off_replay()
        self.start_btn.set_sensitive(False)
        after_close(self._closing, self._open)

    def _open(self):
        """Open the chosen microphone and record from it. On a stream that
        won't open or start, the test is as it was, and says why."""
        if self._closed:
            return
        stream = None
        try:
            device_idx = self._device()
            take = _Take(mic_stream.input_samplerate(device_idx, 16000))

            def audio_callback(indata, frames, time, status):
                if take is self._take:
                    take.frames.append(indata.copy())
                    rms = np.sqrt(np.mean(indata**2))
                    GLib.idle_add(self._show_level, take, min(rms * 10, 1.0))

            stream = sd.InputStream(callback=audio_callback, channels=1, samplerate=take.rate,
                                    device=device_idx, dtype='float32')
            self._take = take
            stream.start()
        except Exception as e:
            self._take = None
            if stream is not None:
                self._closing = mic_stream.close(stream, wait=False)  # opened, not started
            self.start_btn.set_sensitive(True)
            logger.warning(f"The microphone test couldn't record: {e}")
            self._show_error("Recording failed!", str(e))
            return
        take.stream = stream
        self.status.set_text("🎤 Recording... speak normally")
        self._set_grade_style(None)
        self.stop_btn.set_sensitive(True)
        self.replay_btn.set_sensitive(False)

    def stop(self, _button=None):
        """Stop recording, and grade it."""
        take, self._take = self._take, None
        if take is None:
            return
        self._closing = mic_stream.close(take.stream, wait=False)
        if take.frames:
            self.recorded_audio = np.concatenate(take.frames, axis=0)
            self._rate = take.rate
        self.start_btn.set_sensitive(True)
        self.stop_btn.set_sensitive(False)
        self.replay_btn.set_sensitive(bool(take.frames))
        self.meter.set_value(0.0)
        self._show_grade(take)

    def close(self):
        """The window closes: stop a recording or a replay, and free the
        microphone."""
        self._closed = True
        self._call_off_replay()
        take, self._take = self._take, None
        if take is not None:
            mic_stream.close(take.stream)

    def _set_grade_style(self, grade):
        """Colour the grade line: "level-too-quiet", "level-good",
        "level-too-loud", or none."""
        style = self.status.get_style_context()
        for name in ("level-too-quiet", "level-good", "level-too-loud"):
            style.remove_class(name)
        if grade:
            style.add_class(grade)

    def _show_grade(self, take):
        """Grade *take* on the peak and the average level while speaking,
        with thresholds tuned for typical speech: under 5 speaking samples
        is too quiet, a peak over 0.95 clips, and an average under 0.20 is
        quiet."""
        if not take.levels:
            self.status.set_text("No audio detected")
            self._set_grade_style("level-too-quiet")
            return
        speaking = [level for level in take.levels if level > 0.08]
        speaking_avg = sum(speaking) / len(speaking) if speaking else 0
        if len(speaking) < 5:
            text, grade = ("⚠️ Too quiet - speak louder or move closer to the mic",
                           "level-too-quiet")
        elif take.peak > 0.95:
            text, grade = "⚠️ Too loud - reduce volume to avoid distortion", "level-too-loud"
        elif speaking_avg < 0.20:
            text, grade = ("⚠️ A bit quiet - try speaking louder for best results",
                           "level-too-quiet")
        else:
            text, grade = "✓ Good levels - should transcribe well!", "level-good"
        self.status.set_text(text)
        self._set_grade_style(grade)

    def replay(self, _button=None):
        """Play the test recording back, three times louder, on the sound
        thread. Replay and Start Recording are greyed out until it is over:
        a second click would queue the recording again behind it, and a new
        recording would record the replay. A sound server that hangs as the
        stream opens never says it is over, so they come back by themselves
        once the player itself would have given up on it."""
        if self.recorded_audio is None:
            return
        louder = np.clip(self.recorded_audio[:, 0] * 3.0, -1.0, 1.0).astype(np.float32)
        self.replay_btn.set_sensitive(False)
        self.start_btn.set_sensitive(False)
        self._replaying = replay = threading.Event()
        beeps.play(louder, self._rate, called_off=replay, finished=lambda error: GLib.idle_add(
            self._replay_over, replay, error))
        GLib.timeout_add(int(self.replay_limit(len(louder)) * 1000), self._replay_over, replay,
                         "the sound output isn't answering")

    def replay_limit(self, samples):
        """Seconds a replay of *samples* may take: its length, the longest a
        beep is waited for standing in for the output's latency, the time
        the player lets a stuck output have, and the most it then waits for
        the sound to be heard."""
        return samples / self._rate + beeps.WAIT_LIMIT_S + beeps.STUCK_S + beeps.MAX_AHEAD_S

    def _replay_over(self, replay, error):
        """Once for each *replay*: played, failed or given up on, whichever is first."""
        if replay is not self._replaying:
            return False
        self._call_off_replay()
        self.replay_btn.set_sensitive(True)
        self.start_btn.set_sensitive(True)
        if error:
            self._show_error("Playback failed!", error)
        return False

    def _call_off_replay(self):
        """A replay that hasn't started never plays, and one playing stops:
        it was given up on, a new recording starts, or the window closes.
        Given up on, it would otherwise play the user's voice later, maybe
        into the next recording."""
        if self._replaying is not None:
            self._replaying.set()
            self._replaying = None
