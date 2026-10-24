# Code health: the open list

What the code-health work still owes, so a finding never lives only in a
report (CLAUDE.md, Step 1b). Each task names the release it lands in; when it
lands, its status says the commit. The measured part (file and function size,
swallowed errors, docstrings, dated comments, long lines) is enforced by
`tests/test_code_health.py`, whose baseline may only go down.

Last full sweep: 2026-10-23 (Claude and Codex on every file, then a verifier
per area: about 860 confirmed findings, 12 high, 117 medium, 731 low). The
next is due 2026-11-22.

## 0.18 (done)

| Task | Status |
|---|---|
| Fix Typing's fallback script goes to root on stdin, not through /tmp | ec96ec3 |
| Hotkey test, Your Words and untypable characters stay out of the log | ead59dc, 67a7f36, 1d126e2 |
| Ctrl+A, C and V by letter on the user's layout | 9583bab |
| Quoted "false" in config.toml; Clear log says when it couldn't | 206934e |
| License notices for PyAV's 39 libraries and libgomp | c0f10aa and after |
| Subtitles crash, auto-stop after digital silence, stuck update window, startup device blip, AI engine restart | 9f3d813 to 670aeac |
| Tests stay off the user's desktop, Recent Dictations, stats and live TuxTalk | 2f39472 |
| The code-health check and the whole-file review procedure | 84778a2 |

## 0.19, the code-health release

Merge the more-hotkey-choices branch first: it changes the same files.

| # | Task | Size | Status |
|---|---|---|---|
| 1 | Tests that read source text become behaviour tests (about 60); shared test helpers in conftest; the check and ruff cover tests/ too | M | open |
| 2 | The remaining medium user-facing bugs (about 40), each with a failing test first: Preferences update and settings loader, Transcribe a File frames and Your Words, download retry and re-fetch, send and undo timing, password-field timeouts, Budgie/Flashback detection, tray update freeze, soft break, KDE editors when typing (not pasting) | L | open |
| 3 | app.py into about 21 modules with one Recording object per dictation; the three typing loops become one | L | open (hallucinations.py split off, d8f9c45) |
| 4 | prefs.py into a shell and about 15 modules, one per tab | L | open (the microphone test in mic_test.py, c983fe6) |
| 5 | welcome_dialog.py into 5 modules; Help's text into its own module with one command table shared with Voice Commands; tray.py into 6 | L | open (Help's text in help_text.py, 8c936ad; the shared command table still to do) |
| 6 | One helper per job: message dialog (64 copies), download window (5), model downloader (2), runtime folder (13), the pkexec runner and engine launcher thread (3 each), .deb/.rpm staging | M | open (the microphone's rate probe and bounded stop in mic_stream.py, f01c4ee; the exit guard for a stuck stream, shared with the beeps, 634bc9d) |
| 7 | Error handling and logging: the 235 swallowed errors, 187 print() calls, Preferences writing to the log | M | open |
| 8 | 100 missing docstrings, 27 dated comments, 332 long lines, about 60 dead-code items | M | open |
| 9 | CI installs from poetry.lock; appimagetool, its runtime and the Vulkan SDK pinned with checksums; more ruff families under the same ratchet | S | open |
| 10 | GNOME extension fixes in one version (late replies after disable, notification titles, unused stylesheet, menu split) | S | open |
| 11 | Hotkey helper code findings in one version bump (8 small items) | S | open |
| 12 | Docs: README, ROADMAP, icons/README; delete unused icons and the Flathub manifest (Ron's OK) | S | open |
| 13 | Try CUDA without cuDNN on an NVIDIA card; if it works, drop cuDNN from the download | S | open |
| 14 | Tests get a throwaway home folder before anything is imported: importing app.py runs the CUDA setup against the real data folder, and child processes the tests start see the real home | S | open |
| 15 | Flatpak only: Setup Complete says the TuxTalk menu changes the keys, but there the desktop's settings do. Goes with the decision to keep or drop the Flatpak code | S | open |
| 16 | Typing off the hotkey loop: outside the Flatpak, stop_recording transcribes and types on the hotkey loop's thread, so a dictation key pressed while text is still typing is read only once typing is done. With Right Ctrl or Right Alt the rest of the text waits while it's held and what's said meanwhile isn't recorded (a start and a cancel beep after); with F8 the start of it is lost. Transcribe and type on a worker, as the Flatpak path already does (_service_tick), and record the press while typing waits. Goes with task 3 | M | open |
| 17 | GTK set up on one thread and run on another: main() builds the RecordingIndicator before Gtk.main() moves to a thread of its own; keep construction and the loop on one thread and move the evdev loop to a worker. Goes with task 3 | S | open |
| 18 | Keyboard options: keyboard_layout reads the layout and variant, never the XKB options, so the Right Alt dropdown's AltGr warning (and typing a layout's AltGr characters) assume the layout's own Right Alt. lv3:ralt_switch on US, compose:ralt or lv3:ralt_alt are misjudged. Read the options where each desktop keeps them, as the layout is read | S | open |
| 19 | Which window on KDE and GNOME: the KWin script and the extension push the focused window's class and frame, not its id, so a window moved or resized while typing waits for Right Ctrl or Right Alt (or during an automatic send's countdown) counts as another window, and two maximized windows of one app on one screen as the same. Push KWin's internalId and Mutter's get_id() with the class (an extension version), and use them in window_geometry.which_window as X11's, Sway's and Hyprland's ids are | S | done (1de8f7e; COSMIC still names only the class) |
| 20 | Beeps on real PulseAudio: in the Debian 13 VM switched from PipeWire to PulseAudio 17, PortAudio's ALSA path through the pulse plugin played every beep as 20 to 30 ms scraps or not at all, with an underrun storm, at any buffer size; sounddevice.play() (0.18.2's bundled PortAudio too) the same, while `aplay -D pulse` and paplay played them whole. Distros still on PulseAudio (Mint 21, Ubuntu 22.04) may hear no beeps. Reproduce on a real PulseAudio install first; then try PortAudio's PulseAudio host API or paplay for sounds there | M | open |

Full findings, with each one's user effect, fix, risk and effort, are in the
audit's verified tables (kept outside the repository; they name Ron's machine).
