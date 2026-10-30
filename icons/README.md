# TuxTalk Icons

| File | What it's for |
|---|---|
| `OFFICIAL_ICON_DO_NOT_CHANGE.svg` | **The official icon.** The .deb and .rpm install it, start at login uses it, and the dev version's splash screen shows it. |
| `TT_retro_square_light-Inkscape.svg` | The Inkscape working copy, to edit the icon. |

Copies of it live outside this folder:
- `io.github.ronb1964.TuxTalk.png` at the project root (2606x2563): the
  AppImage carries it (`container-build.sh`), the AUR package takes it from
  there, the .deb and .rpm put it in `/usr/share/pixmaps`, the updater
  installs it for the desktop's menu, the splash screen falls back to it, and
  the top of the README shows it.
- `packaging/flatpak/io.github.ronb1964.TuxTalk.svg`: the Flatpak's, on a
  square canvas, since Flatpak refuses icons that aren't square and the
  official one is 782x769.
- The website's `docs/favicon.svg`, `docs/img/icon-64.png` and
  `docs/img/share.png`.

## Changing the icon

1. Edit `TT_retro_square_light-Inkscape.svg` in Inkscape and save it.
2. Write the official icon from it as plain SVG:
   ```bash
   cd /home/ron/Projects/TalkType/icons
   inkscape TT_retro_square_light-Inkscape.svg --export-plain-svg --export-filename=OFFICIAL_ICON_DO_NOT_CHANGE.svg
   ```
3. Make the PNG at the project root from it:
   ```bash
   cd /home/ron/Projects/TalkType
   inkscape icons/OFFICIAL_ICON_DO_NOT_CHANGE.svg --export-type=png --export-width=2606 --export-filename=io.github.ronb1964.TuxTalk.png
   ```
4. Copy the official icon to `packaging/flatpak/io.github.ronb1964.TuxTalk.svg`
   and put its square canvas back: in its first lines, make `height` the same
   as `width` (220.60793mm) and set
   `viewBox="0 -1.815935 220.60793 220.60793"`.
5. Update the website's three copies listed above.

Earlier designs (round, dark and gradient versions, and the icons from before
the rename to TuxTalk) are in git's history.
