"""Preferences, Audio tab, Customize…: the Recording Indicator window, with
the indicator's size, style, color, animation response and place on the
screen. recording_indicator.py draws it from these settings."""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk

from . import ui_style
from .config import CLASSIC_CYAN_HEX
from .prefs_tab import Tab, dropdown, fold

_SIZES = (("small", "Small (60%)"), ("medium", "Medium (100%)"), ("large", "Large (140%)"))
_STYLES = (("orb", "Orb"), ("waveform", "Waveform"), ("bars", "Frequency bars"),
           ("radial", "Radial"))
_POSITIONS = (("center", "Center"), ("top-left", "Top Left"), ("top-center", "Top Center"),
              ("top-right", "Top Right"), ("bottom-left", "Bottom Left"),
              ("bottom-center", "Bottom Center"), ("bottom-right", "Bottom Right"),
              ("left-center", "Left Center"), ("right-center", "Right Center"))

# The picker's preset swatches. The classic cyan is first on purpose: one
# click away without knowing a hex code.
_PRESET_COLORS = (CLASSIC_CYAN_HEX, "#ffffff", "#ff5252", "#ffb300",
                  "#4caf50", "#7c4dff", "#ff4081", "#00bcd4")

# How each offset box shows its saved value. The vertical box shows UP as
# positive, the way people expect, while indicator_offset_y keeps screen
# coordinates (positive = down) so no saved setting or backup changes meaning.
OFFSET_DISPLAY_SIGN = {"indicator_offset_x": 1, "indicator_offset_y": -1}


def _rgba(hex_color):
    """*hex_color* ("#rrggbb") as a Gdk.RGBA."""
    color = Gdk.RGBA()
    color.parse(hex_color)
    return color


def _row_label(grid, row, text, control, tooltip=None):
    """The name of a setting, in the left column of *row*; a screen reader
    names *control* by it."""
    label = Gtk.Label(label=text, xalign=0)
    if tooltip:
        label.set_tooltip_text(tooltip)
    label.set_mnemonic_widget(control)
    grid.attach(label, 0, row, 1, 1)
    return label


class IndicatorSettings(Tab):
    """The Recording Indicator window that the Audio tab's Customize… opens.

    Built with the Audio tab and shown on demand: its controls write the
    settings like every other, so Apply, OK and Cancel treat them the same.
    """

    def __init__(self, win):
        super().__init__(win)
        self.dialog = None      # made the first time Customize… is clicked

    def build(self):
        """The window's settings, in a grid."""
        # Imported here: recording_indicator pulls in cairo and the drawing code.
        from .recording_indicator import positioning_available
        can_move = positioning_available()
        grid = self.grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        for side in ("start", "end", "top", "bottom"):
            getattr(grid, f"set_margin_{side}")(20)
        row = self._build_look(grid, 0)
        row = self._build_color(grid, row)
        row = self._build_response(grid, row)
        row = self._build_position(grid, row, can_move)
        self._build_fine_position(grid, row, can_move)
        return grid

    def show(self):
        """Open the window over Preferences."""
        if self.dialog is None:
            self.dialog = Gtk.Dialog(
                title="Recording Indicator", transient_for=self.window, modal=True)
            self.dialog.add_button("Done", Gtk.ResponseType.CLOSE)
            self.dialog.get_content_area().pack_start(self.grid, True, True, 0)
            # Closing only hides it: its controls stay part of Preferences.
            self.dialog.connect("response", lambda w, _r: w.hide())
            self.dialog.connect("delete-event", lambda w, _e: w.hide_on_delete())
        self.dialog.show_all()
        self.dialog.present()

    def _dropdown(self, choices, active):
        """A dropdown of *choices* ((id, label) pairs) showing *active*."""
        combo = dropdown(Gtk.ComboBoxText())
        for choice_id, label in choices:
            combo.append(choice_id, label)
        combo.set_active_id(active)
        return combo

    def _setting_dropdown(self, key, default, choices):
        """A dropdown showing setting *key* and saving the choice to it."""
        combo = self._dropdown(choices, self.config.get(key, default))
        combo.connect("changed", lambda c: self.update_config(key, c.get_active_id()))
        return combo

    def _build_look(self, grid, row):
        """Size and Style. Returns the next row."""
        # The tooltips are on the labels: on a dropdown they get in the way
        # of its popup.
        size = self._setting_dropdown("indicator_size", "medium", _SIZES)
        _row_label(grid, row, "Size 💡:", size,
                   "Choose the size of the recording indicator.\nMedium is the default size.")
        grid.attach(size, 1, row, 1, 1)
        style = self._setting_dropdown("indicator_style", "orb", _STYLES)
        _row_label(grid, row + 1, "Style 💡:", style,
                   "Choose how the recording indicator looks.\nOrb is the default.")
        grid.attach(style, 1, row + 1, 1, 1)
        return row + 2

    def _build_color(self, grid, row):
        """Color: the desktop's accent or a color picked, and the swatch that
        picks it. Returns the next row."""
        # An older config can still hold 'cyan'; it shows as Custom.
        mode = self.config.get("indicator_color_mode", "custom")
        self._color_mode_combo = self._dropdown(
            (("system", "Use system accent color"), ("custom", "Custom color")),
            mode if mode in ("system", "custom") else "custom")
        _row_label(grid, row, "Color:", self._color_mode_combo)
        grid.attach(self._color_mode_combo, 1, row, 1, 1)

        # The swatch is always shown: in Custom mode the chosen color, in
        # System mode the live desktop accent (a continuity cue). Clicking it
        # always opens the picker and adopts the result as a custom color, so
        # changing color is one click without the dropdown.
        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._color_btn = Gtk.ColorButton()
        _row_label(grid, row + 1, "Custom color:", self._color_btn)
        self._color_btn.set_halign(Gtk.Align.START)
        self._color_btn.set_size_request(64, 30)
        self._color_btn.set_tooltip_text("Click to choose a custom color")
        self._color_btn.add_palette(Gtk.Orientation.HORIZONTAL, len(_PRESET_COLORS),
                                    [_rgba(c) for c in _PRESET_COLORS])
        self._color_btn.connect("color-set", self._on_color_set)
        color_box.pack_start(self._color_btn, False, False, 0)

        color_hint = Gtk.Label(label="← click to choose", xalign=0)
        color_hint.get_style_context().add_class("dim-label")
        color_box.pack_start(color_hint, False, False, 0)

        # The classic cyan is the one color whose identity matters, and a grid
        # of unlabeled swatches can't announce it. A plainly worded link
        # returns to it in one click, no hex knowledge needed.
        reset_btn = Gtk.Button(label="Reset to classic cyan")
        reset_btn.set_relief(Gtk.ReliefStyle.NONE)
        reset_btn.get_style_context().add_class("dim-label")
        reset_btn.set_tooltip_text("Set the color back to TuxTalk's original cyan")
        reset_btn.connect("clicked", self._reset_color_to_classic)
        color_box.pack_start(reset_btn, False, False, 0)
        grid.attach(color_box, 1, row + 1, 1, 1)

        # Wired now that the swatch exists. The flag stops a change of mode
        # made in code (a picked color flips the mode to Custom) from opening
        # the picker again.
        self._suppress_color_popup = False
        self._color_mode_combo.connect("changed", self._on_color_mode_changed)
        self._sync_color_swatch()
        return row + 2

    def _on_color_mode_changed(self, combo):
        """Choosing Custom color opens the picker at once, no hunting for the
        swatch; either mode keeps the swatch in step."""
        mode = combo.get_active_id()
        self.update_config("indicator_color_mode", mode)
        self._sync_color_swatch()
        if mode == "custom" and not self._suppress_color_popup:
            # A ColorButton's click opens its chooser, as pressing it would.
            self._color_btn.clicked()

    def _on_color_set(self, btn):
        """Picking a color implies using it, so the mode becomes Custom."""
        c = btn.get_rgba()
        self.update_config("indicator_color", "#%02x%02x%02x" % (
            int(c.red * 255), int(c.green * 255), int(c.blue * 255)))
        self._set_color_mode_silently("custom")
        self.update_config("indicator_color_mode", "custom")

    def _set_color_mode_silently(self, mode):
        """Set the color mode dropdown without opening the picker: a color
        just picked must not open a second picker over the first."""
        self._suppress_color_popup = True
        try:
            self._color_mode_combo.set_active_id(mode)
        finally:
            self._suppress_color_popup = False

    def _reset_color_to_classic(self, _button):
        """Back to TuxTalk's classic cyan. With that color the orb draws its
        original hand-tuned gradient, the look it has out of the box."""
        self.update_config("indicator_color", CLASSIC_CYAN_HEX)
        self._set_color_mode_silently("custom")
        self.update_config("indicator_color_mode", "custom")
        self._sync_color_swatch()

    def _sync_color_swatch(self):
        """The swatch shows the color the indicator paints: the chosen one,
        or in System mode the desktop's accent."""
        if self._color_mode_combo.get_active_id() == "system":
            self._color_btn.set_rgba(Gdk.RGBA(*ui_style.desktop_accent_rgb(), 1.0))
        else:
            self._color_btn.set_rgba(
                _rgba(self.config.get("indicator_color", CLASSIC_CYAN_HEX)))

    def _build_response(self, grid, row):
        """How strongly the animation follows the voice. Returns the next row."""
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 2.0, 0.1)
        _row_label(grid, row, "Animation response:", scale,
                   "How strongly the indicator's animation moves with your voice.\n"
                   "It doesn't change what the microphone hears.")
        scale.set_value(self.config.get("indicator_sensitivity", 1.0))
        scale.set_hexpand(True)
        scale.connect("value-changed", lambda s: self.update_config(
            "indicator_sensitivity", round(s.get_value(), 2)))
        grid.attach(scale, 1, row, 1, 1)
        return row + 1

    def _build_position(self, grid, row, can_move):
        """Position, greyed with a warning where the indicator can't be moved.
        Returns the next row."""
        combo = self._setting_dropdown("indicator_position", "center", _POSITIONS)
        _row_label(grid, row, "Position:", combo)
        # Moving the indicator needs XWayland, not a session type or the GNOME
        # extension: the tray starts the dictation service with
        # GDK_BACKEND=x11,wayland, so this works on a Wayland desktop with no
        # extension installed.
        if can_move:
            combo.set_tooltip_text("Choose where the recording indicator appears on screen.")
        else:
            combo.set_tooltip_text(
                "Position cannot be set on this system: it needs XWayland, which\n"
                "is not available here. The indicator appears centred instead.")
            combo.set_sensitive(False)
        grid.attach(combo, 1, row, 1, 1)
        if can_move:
            return row + 1
        warning = Gtk.Label(
            label="⚠️ XWayland is not available, so the indicator always appears centred",
            xalign=0)
        warning.set_line_wrap(True)
        warning.set_max_width_chars(60)
        warning.get_style_context().add_class("dim-label")
        grid.attach(warning, 0, row + 1, 2, 1)
        return row + 2

    def _build_fine_position(self, grid, row, can_move):
        """The offsets from the chosen position and Reset to 0. So rarely
        wanted that they're in a closed fold."""
        self.position_fold = fold(grid, row, "Show fine position",
                                            "Hide fine position")
        fine = self.position_fold.content
        across = self._offset_row(fine, 0, "Horizontal offset (pixels):",
                                  "indicator_offset_x", "+ moves it right, − moves it left")
        up = self._offset_row(fine, 1, "Vertical offset (pixels):",
                              "indicator_offset_y", "+ moves it up, − moves it down")
        for spin in (across, up):
            spin.set_sensitive(can_move)
        # Setting both boxes fires their value-changed handlers, which save.
        reset = Gtk.Button(label="Reset to 0")
        reset.set_tooltip_text("Put the indicator back exactly at the chosen position.")
        reset.set_halign(Gtk.Align.START)
        reset.set_sensitive(can_move)
        reset.connect("clicked", lambda _b: (across.set_value(0), up.set_value(0)))
        fine.attach(reset, 1, 2, 1, 1)
        self._update_position_summary()

    def _offset_row(self, fine, row, label, key, hint):
        """One offset's number box, with which way it moves the indicator
        written right under it, where people look, rather than in a tooltip."""
        # Lined up with the box, not with the box and its hint.
        name = Gtk.Label(label=label, xalign=0, valign=Gtk.Align.START)
        name.set_margin_top(6)
        fine.attach(name, 0, row, 1, 1)
        sign = OFFSET_DISPLAY_SIGN[key]
        value = sign * int(self.config.get(key, 0))
        spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(
            value=value, lower=-1000, upper=1000, step_increment=10))
        spin.set_value(value)
        name.set_mnemonic_widget(spin)         # what a screen reader calls the box
        spin.connect("value-changed", lambda w: (
            self.update_config(key, sign * int(w.get_value())),
            self._update_position_summary()))
        spin.connect("scroll-event", lambda *a: True)  # no accidental scroll-wheel changes
        hint_label = Gtk.Label(label=hint, xalign=0)
        hint_label.get_style_context().add_class("dim-label")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.pack_start(spin, False, False, 0)
        box.pack_start(hint_label, False, False, 0)
        box.set_halign(Gtk.Align.START)
        fine.attach(box, 1, row, 1, 1)
        return spin

    def _update_position_summary(self):
        """How far the offsets move the indicator, beside Show fine position."""
        x = int(self.config.get("indicator_offset_x", 0))
        y = int(self.config.get("indicator_offset_y", 0))   # screen pixels: + is down
        moves = [f"{abs(x)} px {'right' if x > 0 else 'left'}"] if x else []
        moves += [f"{abs(y)} px {'down' if y > 0 else 'up'}"] if y else []
        self.position_fold.set_summary(", ".join(moves) or "not moved")
