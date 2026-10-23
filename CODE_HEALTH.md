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
| 3 | app.py into about 21 modules with one Recording object per dictation; the three typing loops become one | L | open |
| 4 | prefs.py into a shell and about 15 modules, one per tab | L | open |
| 5 | welcome_dialog.py into 5 modules; Help's text into its own module with one command table shared with Voice Commands; tray.py into 6 | L | open |
| 6 | One helper per job: message dialog (64 copies), download window (5), model downloader (2), runtime folder (13), the pkexec runner and engine launcher thread (3 each), .deb/.rpm staging | M | open |
| 7 | Error handling and logging: the 235 swallowed errors, 187 print() calls, Preferences writing to the log | M | open |
| 8 | 100 missing docstrings, 27 dated comments, 332 long lines, about 60 dead-code items | M | open |
| 9 | CI installs from poetry.lock; appimagetool, its runtime and the Vulkan SDK pinned with checksums; more ruff families under the same ratchet | S | open |
| 10 | GNOME extension fixes in one version (late replies after disable, notification titles, unused stylesheet, menu split) | S | open |
| 11 | Hotkey helper code findings in one version bump (8 small items) | S | open |
| 12 | Docs: README, ROADMAP, icons/README; delete unused icons and the Flathub manifest (Ron's OK) | S | open |
| 13 | Try CUDA without cuDNN on an NVIDIA card; if it works, drop cuDNN from the download | S | open |
| 14 | Tests get a throwaway home folder before anything is imported: importing app.py runs the CUDA setup against the real data folder, and child processes the tests start see the real home | S | open |
| 15 | Flatpak only: Setup Complete says the TuxTalk menu changes the keys, but there the desktop's settings do. Goes with the decision to keep or drop the Flatpak code | S | open |

Full findings, with each one's user effect, fix, risk and effort, are in the
audit's verified tables (kept outside the repository; they name Ron's machine).
