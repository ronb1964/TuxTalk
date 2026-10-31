# TuxTalk GNOME Extension

Stock GNOME has no tray, so on GNOME TuxTalk's menu lives in this extension: a microphone icon in the top bar that turns red while you're recording, with the same menu the tray has everywhere else. It also tells TuxTalk which window has focus, which GNOME doesn't let regular apps find out.

It works on GNOME Shell 45 through 51.

## Installing it

You normally don't have to. On GNOME, TuxTalk's first run offers to install it, and Preferences → Advanced → Show system check → Install Extension does the same later. Both download `tuxtalk-gnome-extension.zip` from the latest release. GNOME only loads a new extension after you log out and back in.

To try a copy from this folder instead:

```bash
cd gnome-extension
cp -r talktype@ronb1964.github.io ~/.local/share/gnome-shell/extensions/
```

Then log out and back in, and run `gnome-extensions enable talktype@ronb1964.github.io`. On Wayland there's no restarting GNOME Shell in place, so after every change to `extension.js` it's another log out and in.

`./package-extension.sh` at the top of the project builds the release zip.

## The old names

The folder is still `talktype@ronb1964.github.io` and it still talks to TuxTalk over D-Bus as `io.github.ronb1964.TalkType`. Both are on purpose. A new UUID would be a brand new extension that people have to enable again, and an extension that's still loaded from before an update keeps calling the old D-Bus name until the next logout.

## When something's wrong

```bash
# Is it installed and enabled?
gnome-extensions info talktype@ronb1964.github.io

# GNOME Shell's log, where the extension's errors end up
journalctl -f -o cat /usr/bin/gnome-shell

# Is TuxTalk answering on D-Bus?
gdbus call --session --dest io.github.ronb1964.TalkType \
  --object-path /io/github/ronb1964/TalkType \
  --method io.github.ronb1964.TalkType.GetStatus
```

If TuxTalk isn't running, the last one fails with a ServiceUnknown error, and the extension hides its icon until TuxTalk starts again.

## License

MIT, same as TuxTalk.
