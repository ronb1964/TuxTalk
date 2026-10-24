"""The GNOME menu's Performance presets come from the tray (GetPresets).

Asking the tray means the extension keeps no copy of the preset table or the
matcher that could drift from the tray's and put the dot on the wrong preset.
"""
import json
import pathlib

EXT = pathlib.Path(__file__).resolve().parent.parent / "gnome-extension/talktype@ronb1964.github.io"
JS = (EXT / "extension.js").read_text()


def test_interface_declares_getpresets():
    assert '<method name="GetPresets">' in JS
    assert '<arg type="a(sss)" direction="out" name="presets"/>' in JS


def test_no_hard_coded_preset_table():
    assert "PERFORMANCE_PRESETS" not in JS and "PRESET_EXTRA_KEYS" not in JS
    assert "_getCurrentPreset" not in JS


def test_menu_is_built_from_the_tray():
    assert "GetPresetsRemote" in JS
    assert "status.preset" in JS


def test_version_is_26():
    """The version is bumped with every extension.js change so the in-app
    updater offers the new extension."""
    assert json.loads((EXT / "metadata.json").read_text())["version"] == 26


CUSTOM_LABEL = "Custom (opens Preferences)"


def test_both_menus_end_the_presets_with_the_same_custom_item(tray_menu):
    """Both menus offer the same Custom item, which opens Preferences, so a
    custom setup shows its dot in either menu."""
    performance = next(item for item in tray_menu.get_children()
                       if item.get_label() == "Performance")
    assert performance.get_submenu().get_children()[-1].get_label() == CUSTOM_LABEL
    assert f"'{CUSTOM_LABEL}'" in JS
    assert "this._presetItems['custom']" in JS


def test_gnome_custom_item_opens_preferences():
    start = JS.index(f"'{CUSTOM_LABEL}'")
    assert "OpenPreferencesRemote" in JS[start:start + 400]
