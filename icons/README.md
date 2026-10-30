# TuxTalk Icons

| File | What it's for |
|---|---|
| `OFFICIAL_ICON_DO_NOT_CHANGE.svg` | **The official icon.** The .deb, the .rpm, the Flatpak manifest, start at login and the splash screen all install or show this file. Read-only on purpose. |
| `TT_retro_square_light-Inkscape.svg` | The Inkscape working copy, to edit the icon. |
| `TT_retro_square_light_transparent.png` | The splash screen's picture when the SVG can't be loaded. |
| `TT_square_light.png` | The icon the updater installs for the desktop's menu. |

The AppImage carries its own copy, `io.github.ronb1964.TuxTalk.png` at the
project root, which `container-build.sh` puts in the AppImage.

## Changing the icon

1. Edit `TT_retro_square_light-Inkscape.svg` in Inkscape.
2. Save it as "Plain SVG" over the official icon:
   ```bash
   chmod 644 OFFICIAL_ICON_DO_NOT_CHANGE.svg
   # in Inkscape: File, Save a Copy, Plain SVG, OFFICIAL_ICON_DO_NOT_CHANGE.svg
   chmod 444 OFFICIAL_ICON_DO_NOT_CHANGE.svg
   ```
3. Make the PNGs again from it (the root `io.github.ronb1964.TuxTalk.png`, and
   the two PNGs here) so every build shows the same icon.

Earlier designs (round, dark and gradient versions, and the icons from before
the rename to TuxTalk) are in git's history.
