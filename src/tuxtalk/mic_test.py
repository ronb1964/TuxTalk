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

        # Set minimum size
        self.set_size_request(200, 20)

        # Connect draw signal
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
        self.queue_draw()  # Trigger redraw

    def _get_segment_color(self, segment_index, is_lit):
        """Get the color for a segment based on its position and lit state."""
        if not is_lit:
            return self.color_off

        # Calculate position as percentage (0.0 to 1.0)
        position = segment_index / self.num_segments

        if position < 0.60:
            return self.color_green   # Green zone (0-60%)
        elif position < 0.80:
            return self.color_yellow  # Yellow zone (60-80%)
        else:
            return self.color_red     # Red zone (80-100%)

    def _on_draw(self, widget, cr):
        """Draw the segmented meter."""
        # Get widget dimensions
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()

        # Calculate segment dimensions
        segment_gap = 2  # Gap between segments
        total_gaps = (self.num_segments - 1) * segment_gap
        segment_width = (width - total_gaps) / self.num_segments
        segment_height = height - 4  # Leave some margin

        # Calculate how many segments should be lit
        lit_segments = int(self.level * self.num_segments)

        # Draw each segment
        for i in range(self.num_segments):
            x = i * (segment_width + segment_gap)
            y = 2  # Top margin

            is_lit = i < lit_segments
            color = self._get_segment_color(i, is_lit)

            # Draw segment background
            cr.set_source_rgb(*color)
            cr.rectangle(x, y, segment_width, segment_height)
            cr.fill()

            # Draw subtle border around each segment
            cr.set_source_rgb(*self.color_border)
            cr.rectangle(x, y, segment_width, segment_height)
            cr.set_line_width(1)
            cr.stroke()

        return False




class MicTest:
    """The level meter, the grade under it and the Start, Stop and Replay
    buttons, and the recording they share. *device* gives the chosen
    microphone's device index, and *show_error*(title, text) shows what went
    wrong in a dialog."""

    def __init__(self, device, show_error):
        """Nothing recorded yet, and the widgets made for build() to place."""
        self._device = device
        self._show_error = show_error
        self.recording = False
        self.recorded_audio = None
        self._frames = []
        self._rate = 16000
        self._stream = None
        self._replaying = None
        self._levels = []
        self._peak = 0.0
        self.meter = SegmentedVUMeter(num_segments=20)
        self.status = Gtk.Label(label="Click 'Start Recording' and speak to test your mic levels")
        self.start_btn = Gtk.Button(label="● Start Recording")
        self.stop_btn = Gtk.Button(label="■ Stop Recording")
        self.replay_btn = Gtk.Button(label="▶ Replay")
        # Without the symbols, which a screen reader reads out ("black circle").
        for button, name in ((self.start_btn, "Start Recording"),
                             (self.stop_btn, "Stop Recording"), (self.replay_btn, "Replay")):
            button.get_accessible().set_name(name)

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

    def _show_level(self, level):
        """Show one block's level on the meter (GTK thread, via GLib.idle_add)."""
        self.meter.set_value(level)
        self._levels.append(level)
        self._peak = max(self._peak, level)
        return False

    def start(self, _button=None):
        """Start recording the chosen microphone."""
        self._call_off_replay()
        try:
            import sounddevice as sd
            device_idx = self._device()
            self._rate = mic_stream.input_samplerate(device_idx, 16000)
            self.recording = True
            self._frames = []
            self._levels = []
            self._peak = 0.0
            self.status.set_text("🎤 Recording... speak normally")
            self._set_grade_style(None)

            def audio_callback(indata, frames, time, status):
                if self.recording:
                    self._frames.append(indata.copy())
                    rms = np.sqrt(np.mean(indata**2))
                    GLib.idle_add(self._show_level, min(rms * 10, 1.0))

            self._stream = sd.InputStream(callback=audio_callback, channels=1,
                                          samplerate=self._rate, device=device_idx,
                                          dtype='float32')
            self._stream.start()
            self.start_btn.set_sensitive(False)
            self.stop_btn.set_sensitive(True)
            self.replay_btn.set_sensitive(False)
        except Exception as e:
            logger.warning(f"The microphone test couldn't record: {e}")
            self._show_error("Recording failed!", str(e))

    def stop(self, _button=None):
        """Stop recording, and grade it."""
        try:
            self.recording = False
            if self._stream is not None:
                mic_stream.close(self._stream, wait=False)
            if self._frames:
                self.recorded_audio = np.concatenate(self._frames, axis=0)
            self.start_btn.set_sensitive(True)
            self.stop_btn.set_sensitive(False)
            self.replay_btn.set_sensitive(len(self._frames) > 0)
            self.meter.set_value(0.0)
            self._show_grade()
        except Exception as e:
            logger.warning(f"The microphone test couldn't stop: {e}")
            self._show_error("Stop recording failed!", str(e))

    def close(self):
        """The window closes: stop a recording or a replay, and free the
        microphone."""
        self._call_off_replay()
        if self.recording:
            self.recording = False
            if self._stream is not None:
                mic_stream.close(self._stream)

    def _set_grade_style(self, grade):
        """Colour the grade line: "level-too-quiet", "level-good",
        "level-too-loud", or none."""
        style = self.status.get_style_context()
        for name in ("level-too-quiet", "level-good", "level-too-loud"):
            style.remove_class(name)
        if grade:
            style.add_class(grade)

    def _show_grade(self):
        """Grade the recording on the peak and the average level while
        speaking, with thresholds tuned for typical speech: under 5 speaking
        samples is too quiet, a peak over 0.95 clips, and an average under
        0.20 is quiet."""
        if not self._levels:
            self.status.set_text("No audio detected")
            self._set_grade_style("level-too-quiet")
            return
        speaking = [level for level in self._levels if level > 0.08]
        speaking_avg = sum(speaking) / len(speaking) if speaking else 0
        if len(speaking) < 5:
            text, grade = ("⚠️ Too quiet - speak louder or move closer to the mic",
                           "level-too-quiet")
        elif self._peak > 0.95:
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
