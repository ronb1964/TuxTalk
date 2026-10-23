"""Preferences → Audio → Customize…: the recording indicator's controls save
what they show, and the color choices are the two the indicator knows."""
import gi
import pytest

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from gtk_text import combo_ids, find, tree_text, widgets  # noqa: E402
from tuxtalk.config import CLASSIC_CYAN_HEX  # noqa: E402


@pytest.fixture
def window(prefs_window):
    return prefs_window(indicator_color="#ff00ff")


def _combo(window, entry):
    """The indicator dropdown that offers *entry*."""
    return next(w for w in widgets(window.indicator_grid)
                if isinstance(w, Gtk.ComboBoxText) and entry in combo_ids(w))


def _hex(rgba):
    return "#%02x%02x%02x" % (round(rgba.red * 255), round(rgba.green * 255),
                              round(rgba.blue * 255))


def _open_pickers():
    return [w for w in Gtk.Window.list_toplevels()
            if isinstance(w, Gtk.ColorChooserDialog) and w.get_visible()]


@pytest.fixture
def pickers():
    """The color pickers opened during the test, closed after it."""
    yield _open_pickers
    for picker in _open_pickers():
        picker.destroy()


def test_controls_write_the_new_config_keys(window):
    _combo(window, "bars").set_active_id("bars")
    _combo(window, "system").set_active_id("system")
    find(window.indicator_grid, Gtk.Scale).set_value(1.5)
    assert window.config["indicator_style"] == "bars"
    assert window.config["indicator_color_mode"] == "system"
    assert window.config["indicator_sensitivity"] == 1.5


def test_a_picked_color_is_saved_as_the_custom_color(window, pickers):
    """Picking a color implies using it, so the mode flips to Custom without
    opening a second picker over the one just used."""
    _combo(window, "system").set_active_id("system")
    swatch = find(window.indicator_grid, Gtk.ColorButton)
    red = Gdk.RGBA()
    red.parse("#ff5252")
    swatch.set_rgba(red)
    swatch.emit("color-set")
    assert window.config["indicator_color"] == "#ff5252"
    assert window.config["indicator_color_mode"] == "custom"
    assert pickers() == []


def test_color_dropdown_has_only_two_modes(window):
    """Two choices: match the desktop accent, or pick a color. Cyan is not a
    third option; it is the classic color in the swatch's preset palette."""
    color = _combo(window, "system")
    assert combo_ids(color) == ["system", "custom"]
    assert not [text for text in tree_text(color) if "yan" in text]


def test_an_old_cyan_setting_shows_as_custom(prefs_window):
    assert _combo(prefs_window(indicator_color_mode="cyan"), "system").get_active_id() == "custom"


def test_custom_color_selection_opens_the_picker(window, pickers):
    """Choosing 'Custom color' opens the picker at once, no second click on
    the swatch."""
    color = _combo(window, "system")
    color.set_active_id("system")
    assert pickers() == []
    color.set_active_id("custom")
    assert len(pickers()) == 1


def test_swatch_mirrors_the_desktop_accent_in_system_mode(window):
    """In system mode the swatch shows the live accent color, a continuity cue;
    in Custom mode the color picked."""
    swatch = find(window.indicator_grid, Gtk.ColorButton)
    assert _hex(swatch.get_rgba()) == "#ff00ff"
    found, accent = Gtk.Label().get_style_context().lookup_color("theme_selected_bg_color")
    if not found:
        accent.parse("#4caf50")
    _combo(window, "system").set_active_id("system")
    assert _hex(swatch.get_rgba()) == _hex(accent)


def test_color_picker_seeds_a_preset_palette_with_classic_cyan_first(prefs_window, monkeypatch):
    """The picker offers preset swatches so the classic cyan is one click away
    without knowing a hex code; it must be the first preset."""
    palettes = []
    monkeypatch.setattr(Gtk.ColorButton, "add_palette",
                        lambda self, orientation, per_line, colors: palettes.append(colors))
    prefs_window()
    assert len(palettes) == 1 and len(palettes[0]) > 1
    assert _hex(palettes[0][0]) == CLASSIC_CYAN_HEX


def test_reset_to_classic_cyan_control_exists(window):
    """A plainly-labeled control snaps the color back to the classic cyan, the
    one color whose identity matters, without needing to know a hex code."""
    _combo(window, "system").set_active_id("system")
    find(window.indicator_grid, Gtk.Button, "Reset to classic cyan").clicked()
    assert window.config["indicator_color"] == CLASSIC_CYAN_HEX
    assert window.config["indicator_color_mode"] == "custom"
    assert _hex(find(window.indicator_grid, Gtk.ColorButton).get_rgba()) == CLASSIC_CYAN_HEX


def test_uses_american_spelling_for_color(window):
    assert not [text for text in tree_text(window.window, window.indicator_grid)
                if "olour" in text], "UI text must use American 'Color'"


def test_style_options_present(window):
    assert combo_ids(_combo(window, "orb")) == ["orb", "waveform", "bars", "radial"]


def test_offset_boxes_show_up_and_right_as_positive():
    """Typing a negative number to move the indicator UP feels backwards, so
    the vertical box flips the sign it shows; the saved value keeps screen
    coordinates (positive = down) so old settings and backups still mean the
    same place."""
    from tuxtalk.prefs import OFFSET_DISPLAY_SIGN
    assert OFFSET_DISPLAY_SIGN == {"indicator_offset_x": 1, "indicator_offset_y": -1}
    # Showing +30 ("up 30") saves -30, which the indicator reads as 30 px up.
    shown_up = 30
    assert OFFSET_DISPLAY_SIGN["indicator_offset_y"] * shown_up == -30


def _offset_box(window, hint):
    """The number box written *hint* sits under."""
    label = find(window.indicator_grid, Gtk.Label, hint)
    return find(label.get_parent(), Gtk.SpinButton)


def test_offset_direction_is_shown_beside_the_boxes_with_a_reset(prefs_window):
    """The direction is written under each box, not only in a hover tooltip
    that is easy to miss, and Reset to 0 puts the indicator back."""
    window = prefs_window(indicator_offset_x=40, indicator_offset_y=-30)
    across = _offset_box(window, "+ moves it right, − moves it left")
    up = _offset_box(window, "+ moves it up, − moves it down")
    assert (across.get_value(), up.get_value()) == (40, 30)
    up.set_value(50)
    assert window.config["indicator_offset_y"] == -50
    reset = find(window.indicator_grid, Gtk.Button, "Reset to 0")
    if not reset.get_sensitive():
        pytest.skip("positioning needs XWayland, which this display lacks")
    reset.clicked()
    assert (window.config["indicator_offset_x"], window.config["indicator_offset_y"]) == (0, 0)
