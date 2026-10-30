"""The GNOME menu's Performance presets come from the tray (GetPresets).

Asking the tray means the extension keeps no copy of the preset table or the
matcher that could drift from the tray's and put the dot on the wrong preset
(the extension's side runs in test_gnome_extension).
"""
from gnome_extension_js import run

CUSTOM_LABEL = "Custom (opens Preferences)"


def test_both_menus_end_the_presets_with_the_same_custom_item(tray_menu):
    """Both menus offer the same Custom item, which opens Preferences, so a
    custom setup shows its dot in either menu."""
    performance = next(item for item in tray_menu.get_children()
                       if item.get_label() == "Performance")
    assert performance.get_submenu().get_children()[-1].get_label() == CUSTOM_LABEL
    gnome, _logs = run("""
        const {button} = await t.enable();
        const tux = t.tuxtalkStarts();
        t.openMenu(button);
        tux.answer('GetPresets', [['light', 'Lightest', 'small']]);
        t.report(t.labels(button.menu.items[6].menu).at(-1));
    """)
    assert gnome == CUSTOM_LABEL
