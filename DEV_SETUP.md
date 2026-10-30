# Working on TuxTalk

This is how to run TuxTalk from the source code and run its tests.

## What you need

TuxTalk is Python with GTK 3. GTK's Python bindings (PyGObject) can't be pip installed into a venv without a pile of build headers, so they come from your system and everything else goes in a venv in the project folder.

On Fedora or Nobara:

```bash
sudo dnf install -y portaudio-devel wl-clipboard \
                    python3-gobject libayatana-appindicator-gtk3 libnotify
```

Other distros have the same packages under similar names. On Debian and Ubuntu it's `python3-gi`, `gir1.2-ayatanaappindicator3-0.1` and `libportaudio2`, among others.

## Setting it up

```bash
git clone https://github.com/ronb1964/TuxTalk.git
cd TuxTalk
python3 -m venv .venv
.venv/bin/pip install poetry
.venv/bin/poetry install
```

Use your system's `python3` for the venv. The PyGObject your distro ships only works with that same Python version.

## Running it

```bash
./run-dev.sh
```

That starts the tray with `DEV_MODE=1`, which keeps its settings and downloads apart from an installed copy (`~/.config/tuxtalk-dev/` and `~/.local/share/TuxTalk-dev/`) and shows the GTK tray icon even on GNOME, next to the extension's. The script points Python at `src/` and at your system's site-packages, so don't set PYTHONPATH yourself. If you'd rather have a menu entry, make a launcher that runs the same command `run-dev.sh` ends with.

Don't use `poetry run dictate-tray`. Poetry's environment can't see the system PyGObject and it stops on `import gi`.

To stop it, use Quit TuxTalk in the tray menu. If something is stuck, this stops only TuxTalk's own Python processes and nothing else that happens to have "tuxtalk" in its command line:

```bash
pkill -f -- '-m tuxtalk\.'
```

The single-instance lock is released when the process ends, so there's no lock file to delete.

## Running the tests

Swap 3.14 for your Python version (`python3 --version` shows it):

```bash
xvfb-run -a dbus-run-session --config-file=tests/dbus-session.conf -- env \
    PYTHONPATH=./src:/usr/lib64/python3.14/site-packages:/usr/lib/python3.14/site-packages \
    .venv/bin/python -m pytest tests -q -p no:cacheprovider
```

`xvfb-run` and `dbus-run-session` give the tests a screen and a session bus of
their own, as CI does: some tests open windows, and on your own desktop they
would take the keyboard focus from whatever you're typing in. The bus's
configuration, `tests/dbus-session.conf`, has no services that start when asked
for: with the desktop's, every run started a portal and a secret service that
outlived the bus, and on a KDE desktop they piled up into gigabytes. The tests
refuse to start on a bus that has them.

Plain `.venv/bin/pytest` won't work, for the same PyGObject reason. On Debian and Ubuntu the system folder is `/usr/lib/python3/dist-packages`.

The GNOME extension's tests run its JavaScript under Node (20.6 or newer, no
npm packages), with stand-ins for GNOME Shell in `tests/gnome_shell`. Without
Node they're skipped, and say so.

CI also runs two linters, and they should pass before you push. These are the same versions CI uses (`poetry install` put the lock's ruff in the venv), and the shell check covers only the scripts git tracks, so a private script of your own in the folder doesn't trip it:

```bash
.venv/bin/ruff check src/tuxtalk tests
git ls-files -z '*.sh' | xargs -0 uvx --from shellcheck-py==0.11.0.1 shellcheck -S warning
```

## If the venv gets into a bad state

```bash
rm -rf .venv
python3 -m venv .venv
.venv/bin/pip install poetry
.venv/bin/poetry install
```
