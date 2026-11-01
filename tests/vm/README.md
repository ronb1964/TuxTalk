# VM release tests

Runs a new TuxTalk AppImage through as much of the CLAUDE.md Step 8
checklist as a computer can do on its own, inside the libvirt test VMs, and
writes one report page. Nothing here touches the real desktop, and none of
it goes into the AppImage, .deb or .rpm. pytest doesn't collect these files
either (none are named `test_*.py`), because they need the VMs.

## What it does

1. **Clean start.** Each VM has a libvirt snapshot called `tuxtalk-clean`
   with no TuxTalk on it. A run reverts to it, boots, and copies the AppImage
   in over ssh. This takes the place of `fresh-start-for-testing.sh`.
2. **First run without a person.** `guest/ui.py` reads and presses TuxTalk's
   buttons through AT-SPI, the accessibility interface screen readers use,
   by their labels. F8 and F9 are pressed on the VM's own virtual keyboard
   (QEMU `input-send-event`), so the guest kernel sees real key events.
3. **The password prompt.** The harness clicks Fix Typing, checks that the
   desktop's password prompt appears exactly once, takes a screenshot and
   cancels it. Nobody ever types a password. Only then does it put
   `guest/pkexec-shim.sh` in `/usr/local/bin/pkexec`, which runs the same root
   script through the test VM's passwordless sudo. Fix Typing runs again and
   the rest of first run carries on. Typing the real password is listed as
   Ron's check.
4. **Dictation without a voice.** `guest/vmic.sh` makes two pretend
   microphones on PipeWire: `tt_mic` hears whatever is played into the
   `tt_voice` sink, and `tt_quiet_mic` hears nothing at all. Phrases from
   `phrases.toml` play into it while F8 is held. TuxTalk types into
   `guest/sink.py`, a plain window that saves every character to a file,
   and the harness compares that with what the checklist expects.
5. **GNOME extension phase.** Without the extension, GNOME can't tell
   TuxTalk which window has the focus or hold F8 back (by design, see
   `gnome_hotkeys.py`), so those checks are skipped at first. On GNOME the run
   then installs the extension from Preferences and reboots, which also checks
   that TuxTalk and the hotkey helper start by themselves, and runs them again.
6. **Updating from 0.16.** Last, the run puts the VM in the state of someone
   who set Parakeet up with 0.16: the original v3 files (copied from this
   computer's Hugging Face cache, so it must hold them) and no Parakeet
   Ultra. TuxTalk starts again and must keep dictating on v3, offer the
   update once, remember Not Now, and update from Preferences
   (`checks_update.py`).
7. **Uninstall.** Last, because it takes everything away: Preferences →
   Updates & Backup → Uninstall TuxTalk… must ask first (Cancel changes
   nothing), then remove the hotkey helper, the AppImage, the downloads, the
   launcher, autostart and the extension with one password prompt while
   keeping the settings. Installing again must start first-run setup, and
   `--uninstall` with "Also delete my settings" ticked must leave nothing
   named TuxTalk or TalkType in the home folder (`checks_uninstall.py`).
8. **Report.** `report.py` writes `report.html` with every check per VM:
   pass, fail, misheard, manual or skipped, with the typed text, the log
   lines and a screenshot. It ends with the checks only a person can do.

"Misheard" means Parakeet heard other words than the ones played (the log has
them, because the run turns on `log_transcripts`). That verdict says nothing
about TuxTalk either way. It disappears as the voice gets better.

## Running it

```bash
cd /home/ron/Projects/TuxTalk/tests/vm
python3 -u run.py test silverblue --appimage ~/AppImages/TuxTalk-v0.17.0-x86_64.AppImage
```

A Silverblue run takes about 10 minutes. Several VMs run one after the
other: `run.py test silverblue ubuntu ...`. Runs are kept in
`~/.local/share/talktype-vm-tools/runs/<date-time>/` with `results.json`,
`report.html` and `shots/`. `--keep-running` leaves the VM on afterwards, to
look at what happened.

## Setting up a VM (once)

The VM needs ssh with the shared test key
(`~/.local/share/talktype-vm-tools/vmkey`), auto-login to a desktop, and
passwordless sudo (`/etc/sudoers.d/90-nopasswd-testing`; test VMs only).
Add the key to an older VM by typing it in through QEMU:

```bash
cd ~/.local/share/talktype-vm-tools
VM=ubuntu26.04 python3 vmtype.py "echo '$(cat vmkey.pub)' >> ~/.ssh/authorized_keys\n"
```

Then, with the VM running, take TuxTalk off it and save the snapshot:

```bash
python3 run.py prepare ubuntu
```

Add the VM to `VMS` in `vmlib.py` if it's new: its libvirt name, address,
screen size, tags (`no-tray` for a desktop without a system tray) and the
command that opens its terminal.

## The voice

Until someone records the phrases, a computer voice reads them: piper when
its venv exists, otherwise espeak-ng, which Parakeet mishears more often.
Set up piper once:

```bash
T=~/.local/share/talktype-vm-tools/tts
python3 -m venv $T && $T/bin/pip install piper-tts
mkdir -p $T/voices && cd $T/voices
for f in en_US-lessac-medium.onnx{,.json}; do curl -sSfLO "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/$f"; done
for f in de_DE-thorsten-medium.onnx{,.json}; do curl -sSfLO "https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/$f"; done
```

To record them in a real voice (about ten minutes, once):

```bash
python3 record_phrases.py
```

## Adding a check

Checks are plain functions registered with `@check(section, id, name)` in the
`checks_*.py` files. They run in the order of `SECTION_ORDER` in `run.py`,
then in file order. Each returns an `Outcome`; raising `Abort` stops the run
for that VM when TuxTalk is unusable. `ui.py dump "Window title"` prints a
window's widget tree, which is the quickest way to find a label to press.
`ui.py pick "combo box" NAME ITEM` chooses an entry in a dropdown, as
`prefs_model_switch` does with the Model dropdown.

To try a new check without a whole run, leave a VM on after a run
(`--keep-running`) and run just that check on it as it is:

```bash
python3 -u run.py test silverblue --appimage ~/AppImages/TuxTalk-v0.17.0-x86_64.AppImage --as-is --only send_more
```

## What isn't automated yet

The GPU (no graphics card in a VM), KDE-only checks until the fedora44-KDE VM
is prepared, the AI features (a 1.1 GB download), Transcribe a File and Backup
(the desktop's file dialog), the no-systemd fallback, and real apps (the run
pauses a pretend MPRIS player, `guest/fake_player.py`, and turns down a test
tone). The report lists them under "Still Ron's to check".
