"""Preferences, Audio tab: the microphone, its volume and test, and what
TuxTalk does while you dictate (beeps, other sound, notifications, the
recording indicator)."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk
import sounddevice as sd

from . import mic_volume
from .logger import setup_logger
from .mic_test import MicTest
from .prefs_indicator import IndicatorSettings
from .prefs_tab import Tab
from .ui_style import show_message

logger = setup_logger(__name__)

# Other sound while dictating: (label, pause_media, duck_audio). Both on
# pauses players and turns the rest down, which some people use for calls.
SOUND_CHOICES = {
    "pause": ("Pause music and videos", True, False),
    "pause-duck": ("Pause music and videos, turn the rest down", True, True),
    "duck": ("Turn everything down", False, True),
    "leave": ("Leave it alone", False, False),
}


def sound_choice(config):
    """The SOUND_CHOICES entry for pause_media and duck_audio."""
    wanted = (bool(config.get("pause_media", True)), bool(config.get("duck_audio", False)))
    return next(sid for sid, (_label, *flags) in SOUND_CHOICES.items() if tuple(flags) == wanted)


class AudioTab(Tab):
    """The Audio tab. Its microphone test holds the mic while it runs, so
    the window stops it (mic_test) when another tab or window is shown."""

    def build(self):
        """The tab's page."""
        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        for side in ("start", "end", "top", "bottom"):
            getattr(grid, f"set_margin_{side}")(20)
        row = self._build_microphone(grid, 0)
        self._build_feedback(grid, row)
        return grid

    def _build_microphone(self, grid, row):
        """Microphone Setup: the dropdown, the input volume and the test.
        Returns the next row."""
        header = Gtk.Label(xalign=0)
        header.set_markup("<b>Microphone Setup</b>")
        header.set_margin_bottom(10)
        grid.attach(header, 0, row, 2, 1)

        mic_label = Gtk.Label(label="Microphone:", xalign=0)
        grid.attach(mic_label, 0, row + 1, 1, 1)
        mic_combo = self._mic_dropdown()
        mic_label.set_mnemonic_widget(mic_combo)    # what a screen reader names it by
        grid.attach(mic_combo, 1, row + 1, 1, 1)

        grid.attach(Gtk.Label(label="Microphone Test:", xalign=0), 0, row + 2, 1, 1)
        mic_test_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self._build_volume(mic_test_box)
        self.mic_test = MicTest(
            self.win.get_selected_device_idx,
            lambda title, text: show_message(self.window, Gtk.MessageType.ERROR, title, text))
        self.mic_test.build(mic_test_box)
        grid.attach(mic_test_box, 1, row + 2, 1, 1)
        return row + 3

    def _mic_dropdown(self):
        """System Default and each microphone plugged in now, showing the
        saved one."""
        combo = Gtk.ComboBoxText()
        combo.set_can_focus(True)
        combo.connect("button-press-event", self.win._on_combo_button_press)
        self.win._block_combo_scroll(combo)
        combo.append("", "System Default")
        self._input_device_names = []
        try:
            devices = sd.query_devices()
        except Exception as e:
            logger.warning(f"Could not list audio devices: {e}")
            devices = []
        for i, device in enumerate(devices):
            if device.get("max_input_channels", 0) > 0:
                name = device.get("name", f"Device {i}")
                self._input_device_names.append(name)
                combo.append(name, name)    # the name is the setting
        # The saved microphone, or System Default when it isn't plugged in now.
        combo.set_active_id(self.config["mic"])
        if combo.get_active_id() is None:
            combo.set_active_id("")
        combo.connect("changed", self._on_mic_changed)
        return combo

    def _build_volume(self, box):
        """The Input Volume slider, and the note shown instead when the
        microphone's volume can't be set from here."""
        self._volume_timer = None
        volume_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        volume_label = Gtk.Label(label="Input Volume:")
        self.volume_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        volume_label.set_mnemonic_widget(self.volume_scale)
        self.volume_scale.set_range(0, 100)
        self.volume_scale.set_hexpand(True)
        self.volume_scale.set_tooltip_text(
            "Input volume of the microphone chosen above (PipeWire/PulseAudio)")
        self.volume_scale.connect("value-changed", self.on_volume_changed)
        volume_box.pack_start(volume_label, False, False, 0)
        volume_box.pack_start(self.volume_scale, True, True, 0)
        box.pack_start(volume_box, False, False, 0)
        self.volume_note = Gtk.Label(xalign=0)
        self.volume_note.set_markup(
            "<small>Set this microphone's volume in your desktop's sound settings.</small>")
        self.volume_note.set_no_show_all(True)
        box.pack_start(self.volume_note, False, False, 0)
        self._refresh_volume_slider()

    def _on_mic_changed(self, combo):
        """Another microphone chosen: the slider follows it."""
        self.update_config("mic", combo.get_active_id())
        self._refresh_volume_slider()

    def _refresh_volume_slider(self):
        """Point the Input Volume slider at the mic TuxTalk records from.

        A mic whose source can't be found, or whose volume can't be read,
        disables the slider with a note, rather than letting it change the
        system default mic instead or show a volume that isn't the mic's.
        """
        mic = mic_volume.recorded_mic(self.config.get("mic") or "", self._input_device_names)
        if mic.lower() in mic_volume.DEFAULT_MIC_NAMES:
            source = mic_volume.DEFAULT_SOURCE
        else:
            source = mic_volume.source_for_mic(mic, mic_volume.list_sources())
        volume = None if source is None else mic_volume.get_volume(source)
        self._volume_source = source if volume is not None else None
        self.volume_scale.set_sensitive(volume is not None)
        self.volume_note.set_visible(volume is None)
        if volume is not None:
            # Showing the mic's current volume must not write it back.
            self.volume_scale.handler_block_by_func(self.on_volume_changed)
            try:
                self.volume_scale.set_value(volume)
            finally:
                self.volume_scale.handler_unblock_by_func(self.on_volume_changed)

    def on_volume_changed(self, scale):
        """The slider moved. A drag fires this dozens of times and each
        change runs wpctl or pactl, so only the value the slider rests on is
        applied."""
        self._pending_volume = int(scale.get_value())
        if self._volume_timer is None:
            self._volume_timer = GLib.timeout_add(150, self._apply_pending_volume)

    def _apply_pending_volume(self):
        """Apply the slider's latest value once the drag has paused."""
        self._volume_timer = None
        if (self._volume_source is not None
                and not mic_volume.set_volume(self._pending_volume, self._volume_source)):
            logger.warning("Could not set the microphone's volume")
        return False

    def _build_feedback(self, grid, row):
        """Audio & Visual Feedback: beeps, other sound, notifications and
        the recording indicator."""
        row = self.win._section_header(grid, row, "Audio &amp; Visual Feedback")
        grid.attach(self._setting_check(
            "beeps", "Play beeps for start/stop/ready",
            "Play audio beeps to confirm when recording starts, stops, and when "
            "transcription is ready.\nHelps you know the system is responding."),
            0, row, 2, 1)
        grid.attach(self._other_sound_row(), 0, row + 1, 2, 1)
        grid.attach(self._setting_check(
            "notify", "Show desktop notifications",
            "Show desktop notifications with transcribed text preview.\n"
            "Useful to confirm what was transcribed without switching windows."),
            0, row + 2, 2, 1)
        grid.attach(self._indicator_row(), 0, row + 3, 2, 1)

    def _setting_check(self, key, label, tooltip):
        """A checkbox showing setting *key*, saving each change to it."""
        check = Gtk.CheckButton(label=label)
        check.set_active(self.config[key])
        check.connect("toggled", lambda x: self.update_config(key, x.get_active()))
        check.set_tooltip_text(tooltip)
        return check

    def _other_sound_row(self):
        """What happens to other sound while you talk: one question, one
        dropdown over the two settings (sound_choice), which keep their meaning."""
        sound_label = Gtk.Label(label="Other sound while you dictate 💡:", xalign=0)
        sound_label.set_tooltip_text(
            "Pause: YouTube, Spotify and other players pause while you talk, so the\n"
            "microphone hears only you. Only what TuxTalk paused starts again.\n\n"
            "Turn down: every other app's sound goes down while you talk (to half on\n"
            "its volume slider) and comes back after: Discord calls, games and anything\n"
            "pausing can't reach. A call still hears your microphone, so mute yourself there.\n"
            "A volume you change yourself in the meantime is left as you set it.")
        combo = Gtk.ComboBoxText()
        combo.connect("button-press-event", self.win._on_combo_button_press)
        self.win._block_combo_scroll(combo)
        for sid, (label, _pause, _duck) in SOUND_CHOICES.items():
            combo.append(sid, label)
        combo.set_active_id(sound_choice(self.config))
        combo.connect("changed", self._on_sound_changed)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.pack_start(sound_label, False, False, 0)
        row.pack_start(combo, False, False, 0)
        return row

    def _on_sound_changed(self, combo):
        """Save the two settings behind the Other sound choice."""
        choice = combo.get_active_id()
        if choice is not None:
            _label, pause, duck = SOUND_CHOICES[choice]
            self.update_config("pause_media", pause)
            self.update_config("duck_audio", duck)

    def _indicator_row(self):
        """Show recording indicator, and Customize… for how it looks, in a
        window of its own (IndicatorSettings, built now so its controls are
        part of Preferences)."""
        self.indicator = IndicatorSettings(self.win)
        self.indicator.build()
        check = Gtk.CheckButton(label="Show recording indicator")
        check.set_active(self.config.get("recording_indicator", True))
        check.set_tooltip_text(
            "Show an animated visual indicator while recording.\n"
            "Helps you see that dictation is active and responding to your voice.")
        customize = Gtk.Button(label="Customize…")
        customize.set_tooltip_text("Its style, size, color and where it appears")
        customize.set_sensitive(check.get_active())
        customize.connect("clicked", lambda _b: self.indicator.show())
        check.connect("toggled", lambda x: (
            self.update_config("recording_indicator", x.get_active()),
            customize.set_sensitive(x.get_active())))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.pack_start(check, False, False, 0)
        row.pack_start(customize, False, False, 0)
        return row
