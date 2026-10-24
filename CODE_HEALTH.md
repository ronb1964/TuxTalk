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
| 15 | Flatpak only: Setup Complete says the TuxTalk menu changes the keys, but there the desktop's settings do. And the automatic Enter's last check, that no other key went out during its checks, counts only the virtual keyboard's keys: libei's typing (output_backends.LibeiOutputBackend) needs the same count, under a lock taken with the Enter's first key. Goes with the decision to keep or drop the Flatpak code | S | open |
| 16 | Typing off the hotkey loop: outside the Flatpak, stop_recording transcribes and types on the hotkey loop's thread, so a dictation key pressed while text is still typing is read only once typing is done. With Right Ctrl or Right Alt the rest of the text waits while it's held and what's said meanwhile isn't recorded (a start and a cancel beep after); with F8 the start of it is lost. Transcribe and type on a worker, as the Flatpak path already does (_service_tick), and record the press while typing waits. Goes with task 3 | M | open (the automatic Enter's "Right away" is on a thread of its own, 217b7f9; the typing itself is still on the loop) |
| 17 | GTK set up on one thread and run on another: main() builds the RecordingIndicator before Gtk.main() moves to a thread of its own; keep construction and the loop on one thread and move the evdev loop to a worker. Goes with task 3 | S | open |
| 18 | Keyboard options: keyboard_layout reads the layout and variant, never the XKB options, so the Right Alt dropdown's AltGr warning (and typing a layout's AltGr characters) assume the layout's own Right Alt. lv3:ralt_switch on US, compose:ralt or lv3:ralt_alt are misjudged. Read the options where each desktop keeps them, as the layout is read | S | open |
| 19 | Which window on KDE and GNOME: the KWin script and the extension push the focused window's class and frame, not its id, so a window moved or resized while typing waits for Right Ctrl or Right Alt (or during an automatic send's countdown) counts as another window, and two maximized windows of one app on one screen as the same. Push KWin's internalId and Mutter's get_id() with the class (an extension version), and use them in window_geometry.which_window as X11's, Sway's and Hyprland's ids are | S | done (1de8f7e; COSMIC still names only the class) |
| 20 | Beeps on real PulseAudio: in the Debian 13 VM switched from PipeWire to PulseAudio 17, PortAudio's ALSA path through the pulse plugin played every beep as 20 to 30 ms scraps or not at all, with an underrun storm, at any buffer size; sounddevice.play() (0.18.2's bundled PortAudio too) the same, while `aplay -D pulse` and paplay played them whole. Distros still on PulseAudio (Mint 21, Ubuntu 22.04) may hear no beeps. Reproduce on a real PulseAudio install first; then try PortAudio's PulseAudio host API or paplay for sounds there | M | open |
| 21 | The automatic Enter's safety in one rule (round 4's five highs, then rounds 5 and 6): the destination taken before the words are delivered and no send after a move during delivery; one record of the message per recording (where its words went, whether words came before a spoken key, whether every part went in, the last sticky); one claim right before the Enter (not held, same window before and after the password check, no password field, not TuxTalk's own window from any source, no other keys on the virtual keyboard during the checks, by a key-event count instead of a busy lock); "Right away" on a thread of its own, and the hold key's press holding a send before the key is hidden, so a tap still holds it; Type at pause's held mark, a key said after the words and Claude Desktop's four spaces through the same path, the mark's window checked right before its first key; tray and X11 ids never compared; the focus reported in one D-Bus call and asked with a time limit. Not covered: the Flatpak's portal typing (task 15), and wtype, used only where TuxTalk has no virtual keyboard, whose held mark is checked against its window once before typing starts; where the window isn't known, task 22 decides | M | done (080eb35, 05cd6b9, the round-5 fixes b029a43 to 554b1f1, then 4af88dd to f5f02cb) |
| 22 | Where the desktop names only the app, or nothing (COSMIC; GNOME with 0.18's extension until the next login; GNOME without the extension; Plasma 5.27 on Wayland, whose KWin the focus script doesn't support, as in Kubuntu 24.04 and Debian 12; a tray too busy to answer): an automatic Enter (the countdown and "Right away") isn't sent, and the notice says to press Enter; a key said after the words ("..., press enter", "..., press tab", Claude Desktop's four spaces) is still pressed. Either is still stopped where another window, a password field or TuxTalk's own window is known to have the focus. The switch (SEND_WHEN_ONLY_THE_APP_IS_KNOWN) is gone; both branches tested on each source | S | done (f5f02cb), decided by Ron |
| 23 | Type at pause in a dryer: its thumps (louder than speech's threshold for 40 ms or more) start every pause over, so nothing is typed until the recording ends. A 70 ms burst rule (1694a36) fixed some dryer pauses but cut phrases mid-sentence 10 to 19% more often in quiet rooms, so it was taken back. Do it the way hands-free does (3d4b47e): bumps are bumps only in steady noise that has them. Measure with the simulation in .superpowers/noise-sim-2026-10-24 (sim.py tap) on all 125 readers before and after; quiet rooms must not lose a single phrase | S | open |
| 24 | Hands-free in bumpy noise sends noise to the engine: the trimmed tail is the quiet since the last loud block, and each thump or gust restarts it, so up to the whole wait of a dryer's noise is transcribed after the last word, where Whisper can invent a "Thank you.". Trim from the last speech run instead, measured with the simulation (.superpowers/noise-sim-2026-10-24) so no word is ever cut | S | open |
| 25 | Each recording keeps its own record of where its words went (send_countdown.Message) and its held-back mark, correction and screen copy: today they live in app.state, and a recording started before the last one's phrases are typed (the Flatpak's tick, which doesn't wait; elsewhere only after CHUNK_DRAIN_TIMEOUT_S) gives them to the new one, so the old one's "press enter" can go where its words didn't. Goes with task 3's Recording object | M | Message done (each queued job carries its recording's; test a_phrase_queued_before_the_next_recording_keeps_its_own_message); the held-back mark carries it too, while the correction and screen copy stay in app.state until task 3 |
| 26 | One lookup of the desktop's accent color: prefs.py (_desktop_accent_rgba, fallback #4caf50) and recording_indicator.py (fallback 0.30, 0.72, 0.42) each build a label, read theme_selected_bg_color and swallow every error, and their fallbacks differ, so the Preferences preview can disagree with the indicator it promises to match. Goes with task 6 | S | open |
| 27 | The mic test's "Your room is noisy" misses real noisy rooms: on six recorded rooms (DEMAND, .superpowers/noise-sim-2026-10-24/REAL-ROOMS.md) with 2.5 s of talking it warned in 7 of 150 takes even with the noise 10 dB under the voice, as real rooms' quiet moments keep the 10th percentile low. Calibrate on the real rooms (the median of the room, or a voice detector, task 29) so it warns where hands-free can't stop by itself | S | open |
| 28 | Help's text scrolls on the arrows, Home and End instead of moving a caret: check with Orca that a screen-reader user can still read it line by line, and scroll on the Page keys only if not | S | open |
| 29 | A trained voice detector (Silero VAD, about 2 MB, ONNX) for hands-free, Type at pause and the mic test: in real rooms 20 dB or less under the voice, loudness rules can't tell clatter or other voices from the speaker (REAL-ROOMS.md). Check the license first (GitHub's sidebar says MIT, a README badge CC BY-NC 4.0); asked Ron 2026-10-24 whether to plan it after the file splits | M | open, Ron to decide |

Full findings, with each one's user effect, fix, risk and effort, are in the
audit's verified tables (kept outside the repository; they name Ron's machine).
