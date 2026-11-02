"""Preferences, Advanced tab, TuxTalk Labs: Type at pause (chunking.py), its
settings, and a live test of what the pause detector hears."""
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk
import sounddevice as sd

from . import mic_stream
from .chunk_meter import PauseTest, level_to_fraction
from .mic_test import MicReopen, SegmentedVUMeter
from .logger import setup_logger
from .prefs_tab import LABS_INDENT, Tab, fold, subheading
from .voice_detector import Unavailable

logger = setup_logger(__name__)

DETECTOR_MISSING = ("The test can't run: TuxTalk's voice detector didn't load, so Type at "
                    "pause can't hear pauses either. The log says why.")
DETECTOR_FAILED = ("The test stopped: TuxTalk's voice detector failed while it listened. "
                   "The log says why.")


class PauseMeter(SegmentedVUMeter):
    """The mic test's meter for Type at pause (TuxTalk Labs): the level, as a
    0-1 position (chunk_meter), and a green frame flashing where the pause
    detector would cut. What counts as speech is the voice detector's call,
    not a level, so no line is marked on it."""

    FLASH_S = 0.3

    def __init__(self, num_segments=20):
        super().__init__(num_segments=num_segments)
        self._flash_until = 0.0
        self.set_size_request(300, 20)

    def show_snapshot(self, level, cut=False):
        if cut:
            self._flash_until = time.monotonic() + self.FLASH_S
        self.set_value(level)

    def flashing(self):
        return time.monotonic() < self._flash_until

    def _on_draw(self, widget, cr):
        super()._on_draw(widget, cr)
        self._draw_marks(cr, widget.get_allocated_width(), widget.get_allocated_height())
        return False

    def _draw_marks(self, cr, width, height):
        if self.flashing():
            # A cut: a green frame round the whole meter, easy to catch from
            # the corner of the eye while speaking.
            cr.set_source_rgb(0x04 / 255, 0xB5 / 255, 0x03 / 255)
            cr.set_line_width(3)
            cr.rectangle(1.5, 1.5, width - 3, height - 3)
            cr.stroke()


class TypeAtPause(Tab):
    """The Labs entry: the switch, and under it the fine-tuning with its test,
    which holds the microphone until it's stopped (stop_test)."""

    def __init__(self, win):
        super().__init__(win)
        self._pause_test = None             # the test running now, if any
        self._pause_test_stream = None
        self._tap_redraw_pending = False

    def build(self, grid, row):
        """The Labs entry: the switch, and under it the fine-tuning with a live
        test of what the pause detector hears. Returns the next grid row."""
        grid.attach(subheading("Type at pause"), 0, row, 2, 1)
        row += 1

        self.tap_check = Gtk.CheckButton(label="Type each phrase when you pause")
        self.tap_check.set_active(bool(self.config.get("chunked_dictation", False)))
        self.tap_check.set_tooltip_text(
            "While you dictate, each phrase is typed as soon as you pause, instead of\n"
            "all of it when you stop. Nothing typed is changed afterwards. Whether a\n"
            "pause ends a sentence is decided once the next words are heard.")
        self.tap_check.set_margin_start(LABS_INDENT)
        grid.attach(self.tap_check, 0, row, 2, 1)
        row += 1

        # Several settings, so a fold with their values beside its button (one
        # setting would sit by its tickbox, like Power Saving's minutes).
        self.tap_fold = fold(grid, row, "Adjust Settings", "Hide Settings")
        self.tap_fold.set_margin_start(LABS_INDENT)
        # Hiding the settings hides the test's meter, so the test stops.
        self.tap_fold.button.connect(
            "clicked", lambda _b: self.tap_fold.is_open() or self.stop_test())
        row += 1
        self.tap_fine = self.tap_fold.content
        r = self._build_settings(self.tap_fine)
        self._build_test(self.tap_fine, r)
        self._tap_update_summary()

        self.tap_check.connect("toggled", lambda c: (
            self.update_config("chunked_dictation", c.get_active()),
            self._tap_sync_sensitivity()))
        self.tap_pause_spin.connect("value-changed", lambda sp: (
            self.update_config("chunk_pause_seconds", round(sp.get_value(), 1)),
            self._tap_reconfigure(), self._tap_update_summary()))
        self.tap_context_check.connect("toggled", lambda c: (
            self.update_config("chunk_context_marks", c.get_active()),
            self._tap_update_summary()))
        self.tap_test_btn.connect("clicked", self._tap_toggle_test)
        self._tap_sync_sensitivity()
        return row

    def _build_settings(self, fine):
        """Punctuation from context and the pause length; returns the next
        row of *fine*."""
        r = 0
        self.tap_context_check = Gtk.CheckButton(
            label="Punctuation from context: decide the mark at a pause by hearing "
                  "both sides (slower on a weak graphics chip)")
        self.tap_context_check.get_child().set_line_wrap(True)
        self.tap_context_check.set_active(bool(self.config.get("chunk_context_marks", True)))
        fine.attach(self.tap_context_check, 0, r, 2, 1)
        r += 1

        pause_label = Gtk.Label(label="Pause length:", xalign=0)
        fine.attach(pause_label, 0, r, 1, 1)
        self.tap_pause_spin = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(value=float(self.config.get("chunk_pause_seconds", 0.8)),
                                      lower=0.3, upper=3.0, step_increment=0.1,
                                      page_increment=0.5),
            digits=1)
        self.tap_pause_spin.connect("scroll-event", lambda *a: True)
        pause_label.set_mnemonic_widget(self.tap_pause_spin)
        self.tap_pause_spin.set_tooltip_text(
            "How long you stay quiet before the phrase so far is typed.\n"
            "It's always kept shorter than the hands-free stop.")
        pause_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pause_box.pack_start(self.tap_pause_spin, False, False, 0)
        pause_box.pack_start(Gtk.Label(label="s"), False, False, 0)
        fine.attach(pause_box, 1, r, 1, 1)
        return r + 1

    def _build_test(self, fine, r):
        """The Test button, its meter and the line under it that says what
        it heard, at row *r* of *fine*."""
        self.tap_test_btn = Gtk.Button(label="Test")
        self.tap_test_btn.set_tooltip_text(
            "Try the pause length without typing anything. Talk, and stop now and\n"
            "then: the bar moves with your voice and flashes green wherever a phrase\n"
            "would be typed. It uses the microphone from the Audio tab.")
        self.tap_meter = PauseMeter(num_segments=20)
        self._pause_reopen = MicReopen()
        test_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        test_box.pack_start(self.tap_test_btn, False, False, 0)
        test_box.pack_start(self.tap_meter, True, True, 0)
        fine.attach(test_box, 0, r, 2, 1)
        r += 1

        self.tap_status = Gtk.Label(
            label="Press Test, then talk and stop now and then. A green flash means a "
                  "phrase would be typed.",
            xalign=0)
        self.tap_status.set_line_wrap(True)
        self.tap_status.set_opacity(0.7)
        fine.attach(self.tap_status, 0, r, 2, 1)

    def _tap_update_summary(self):
        """The values beside the Adjust Settings button, as they are now."""
        context = "on" if self.tap_context_check.get_active() else "off"
        self.tap_fold.set_summary(
            f"pause {self.tap_pause_spin.get_value():.1f} s"
            f" \u00b7 punctuation from context {context}")

    def _tap_sync_sensitivity(self):
        on = self.tap_check.get_active()
        self.tap_fine.set_sensitive(on)
        self.tap_fold.header.set_sensitive(on)
        if not on:
            self.stop_test()

    def _pause_stream_factory(self, **kwargs):
        return sd.InputStream(**kwargs)

    def _tap_toggle_test(self, _button):
        if self._pause_test_stream is not None:
            self.stop_test()
        else:
            self.tap_test_btn.set_sensitive(False)
            self._pause_reopen.when_closed(self._start_pause_test)

    def _start_pause_test(self):
        """Stream the chosen mic into a PauseTest of its own, so a stream of
        an earlier test still being closed feeds nothing to it; the callback
        only feeds it and asks for one redraw at a time."""
        self.tap_test_btn.set_sensitive(True)
        stream = None
        try:
            device_idx = self.win.get_selected_device_idx()
            sample_rate = mic_stream.input_samplerate(device_idx, 16000)
            # Before the stream: the voice model loads here, off the audio thread.
            self._pause_test = test = PauseTest(sample_rate, self.tap_pause_spin.get_value())
            self._tap_redraw_pending = False

            def callback(indata, frames, time_info, status):
                if test is not self._pause_test:
                    return
                test.feed(indata)
                if not self._tap_redraw_pending:
                    self._tap_redraw_pending = True
                    GLib.idle_add(self._tap_show, test)

            stream = self._pause_stream_factory(device=device_idx, channels=1,
                                                samplerate=sample_rate, dtype="int16",
                                                callback=callback)
            stream.start()
        except Unavailable:             # the log says why, from when it was loaded
            self.tap_status.set_text(DETECTOR_MISSING)
            return
        except Exception as e:
            self._pause_test = None
            if stream is not None:      # opened but not started: free the mic
                self._pause_reopen.closing = mic_stream.close(stream, wait=False)
            logger.warning(f"Type at pause's test couldn't open the microphone: {e}")
            self.tap_status.set_text(f"Couldn't open the microphone: {e}")
            return
        self._pause_test_stream = stream
        self.tap_test_btn.set_label("Stop")
        self.tap_status.set_text("Listening\u2026 talk, then pause.")

    def _tap_show(self, test):
        """Draw *test*'s latest snapshot, unless it was stopped since (GTK thread)."""
        self._tap_redraw_pending = False
        if test is not self._pause_test:
            return False
        if test.failed:
            self.stop_test()
            self.tap_status.set_text(DETECTOR_FAILED)
            return False
        self.tap_meter.show_snapshot(level_to_fraction(test.snapshot().level),
                                     cut=test.take_cut())
        self.tap_status.set_text(test.status_text())
        return False

    def _tap_reconfigure(self):
        if self._pause_test is not None:
            self._pause_test.configure(self.tap_pause_spin.get_value())

    def stop_test(self, wait=False):
        """Stop Type at pause's live test, or call off one still waiting to
        open the mic, and free the mic: off the window's thread, or with
        *wait* (the window closing) for a moment at most."""
        stream, self._pause_test_stream = self._pause_test_stream, None
        test, self._pause_test = self._pause_test, None
        if test is not None:
            # Not the live line frozen at its last words: what it found.
            self.tap_status.set_text(test.result_text())
        if stream is not None and wait:
            mic_stream.close(stream)
        elif stream is not None:
            self._pause_reopen.closing = mic_stream.close(stream, wait=False)
        self._pause_reopen.call_off()
        self.tap_test_btn.set_label("Test")
        self.tap_test_btn.set_sensitive(True)
        self.tap_meter.show_snapshot(0.0)
