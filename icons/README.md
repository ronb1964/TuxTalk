# TuxTalk Icons

| File | What it's for |
|---|---|
| `OFFICIAL_ICON_DO_NOT_CHANGE.svg` | **The official icon.** The .deb and .rpm install it, start at login uses it, and the dev version's splash screen shows it. Read-only on purpose. |
| `TT_retro_square_light-Inkscape.svg` | The Inkscape working copy, to edit the icon. |
| `TT_retro_square_light_transparent.png` | The dev version's splash screen, when the official SVG isn't there. |
| `TT_square_light.png` | What the dev version's updater installs for the desktop's menu when the root PNG isn't there. |

Two more copies live outside this folder:
- `io.github.ronb1964.TuxTalk.png` at the project root: `container-build.sh`
  puts it in the AppImage, and the updater and splash screen prefer it.
- `packaging/flatpak/io.github.ronb1964.TuxTalk.svg`: the Flatpak's, on a
  square canvas, since Flatpak refuses icons that aren't square and the
  official one is 782x769.

## Changing the icon

1. Edit `TT_retro_square_light-Inkscape.svg` in Inkscape.
2. Save it as "Plain SVG" over the official icon:
   ```bash
   chmod 644 OFFICIAL_ICON_DO_NOT_CHANGE.svg
   # in Inkscape: File, Save a Copy, Plain SVG, OFFICIAL_ICON_DO_NOT_CHANGE.svg
   chmod 444 OFFICIAL_ICON_DO_NOT_CHANGE.svg
   ```
3. Make the other copies again from it so every build shows the same icon:
   the root `io.github.ronb1964.TuxTalk.png`, the two PNGs here, and the
   Flatpak's square SVG (`packaging/flatpak/`).

Earlier designs (round, dark and gradient versions, and the icons from before
the rename to TuxTalk) are in git's history.
