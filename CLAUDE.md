# TuxTalk Project Rules for Claude

**The app was renamed from TalkType to TuxTalk in 0.15.0** ("TALKTYPE" is
another company's registered trademark for dictation software). Some names
deliberately stay TalkType because changing them would hurt users:
- the GNOME extension UUID `talktype@ronb1964.github.io` and its schema (a new
  UUID is a new extension: a logout and enabling it again);
- the D-Bus name, path and interface `io.github.ronb1964.TalkType` (an old,
  still-loaded extension calls them until the next logout);
- the runtime lock files (an old TalkType and TuxTalk must never run at once);
- the Vulkan engine's `talktype-whisper-vulkan-*` release files (already published);
- the compatibility copies in every release: `TalkType-vX.X.X-x86_64.AppImage.zsync`
  and `talktype-gnome-extension.zip` (0.14.x installs look for those names).
Old folders, launchers and KDE shortcuts are moved or cleaned on start by
`src/tuxtalk/rename_migration.py`.

The project folder has been `/home/ron/Projects/TuxTalk` since 2026-11-01.
`/home/ron/Projects/TalkType` beside it is a link to it, so older sessions,
worktrees and notes that name it still work; don't delete the link.

## WHERE THINGS ARE

- `src/tuxtalk/`: the app (its own README.md maps the modules)
- `tests/`: the suite; `tests/vm/` is the VM release harness
- `gnome-extension/`: the GNOME Shell extension
- `packaging/`: deb, rpm, flatpak, licenses, the emoji font script
- `aur/`: the AUR package sources, in git
- `aur-repo-tuxtalk/` and `aur-publish/`: the AUR publishing clones (gitignored)
- `vulkan-engine-patches/`: patches `build-vulkan-engine.sh` applies to whisper.cpp
- `docs/`: the tuxtalk.app website; `docs/superpowers/` holds specs and plans
- `screenshots/`: the README's images
- `CODE_HEALTH.md`, `CHANGELOG.md`, `ROADMAP.md`, `DEV_SETUP.md`
- `RELEASE_CHECKLIST.md`: Ron's checks at Step 8
- `PUBLISHING.md`: Step 12, engine uploads, and public posts

**READ THIS ENTIRE FILE BEFORE DOING ANYTHING. THESE RULES ARE MANDATORY.**

---

## STOP — MANDATORY RELEASE PROCESS

**You MUST follow this exact sequence. No skipping steps. No exceptions.**

### Step 1: Code Changes
- Make and test code changes in `/home/ron/Projects/TuxTalk/src/`
- NEVER edit files in `.claude/worktrees/` — Ron's dev version runs from the main project

### Step 1b: The Code Health Gate, Two Reviews, Then Fix
Ron, 2026-10-23: "When I asked that our app be something a human coder would
be proud of, I meant it." What went wrong before: reviews read only the diff,
so nobody saw app.py reach 5,900 lines; the checklist had no size rule; and an
audit's findings ("three huge files", "restructure after 0.15.0") stayed lines
in a report while three releases went out. This procedure closes each gap.

1. **The machine gate** (`tests/test_code_health.py`, part of the test suite
   here and in CI, so it can't be skipped). It measures all of src/tuxtalk,
   subfolders included: file length, each function's length and decision
   points, broad excepts that neither log (at info or above) nor raise,
   modules without a docstring and functions or classes of 15+ lines
   without one, comments with names or dates, lines over 100. New code meets
   the limits. What was over them is listed finding by finding in
   `tests/code_health_baseline.json` and may only go down (a ratchet); code
   moved to another file keeps its entry. After an improvement, lower the
   baseline with the command in the test file's docstring; the suite fails
   until that's done. Raising a number, or loosening a limit or a check,
   needs Ron's OK first ("Exceptions" below).
2. **Two reviews of every file the change touches, read in full.** Claude and
   Codex (the codex-direct agent), each independently and read-only: before a
   release, everything since the last release tag; before a merge, the whole
   branch. Start from the complete list of changed files, tests, scripts, CI
   and docs included, and untracked ones too:
   `git diff --name-only vX.Y.Z; git status --short`. Each reviewer gets the
   prompt under "THE REVIEW PROMPT" word for word.
3. **Verify, fix, verify the fix.** Check each finding yourself before
   acting on it. Fix what's real in the same round, with a test that fails
   without the fix where it's behaviour. Run the gate and the suite again,
   and have the reviewer who found a high or medium finding check its fix.
4. **What can't be fixed now goes in `CODE_HEALTH.md`** (the open list in the
   repository: finding, severity, the release it lands in, status, and the
   commit that closed it), and Ron hears about it. Never only a line in a
   report, and never only in memory.
5. **Every release pays some down:** it closes at least one `CODE_HEALTH.md`
   task or takes at least one file or function off the baseline. Raise it
   yourself if a release is about to go out without that; skipping needs
   Ron's OK.
6. **Once a month, a full sweep** of the whole codebase, not a diff: the
   gate's numbers, `ruff check --select B,SIM,C90,ERA src/tuxtalk`, vulture
   (dead code), pylint's duplicate-code check, and both reviewers on the
   largest and the worst-graded files. Raise it yourself when the date in
   `CODE_HEALTH.md` is 30 days old. Its findings go into `CODE_HEALTH.md`
   ranked, and Ron gets them in plain words.
7. Tell Ron in plain words what was found and fixed, each round.

Ron, 2026-10-20: "I'm relying on you to ensure that it remains that way ...
periodic code reviews and include Codex in there to catch anything that you
may miss."

### Step 2: Ask Before Building
- **NEVER run `./build-release.sh` without asking Ron first**
- Say: "Ready to build the AppImage?" and WAIT for confirmation
- There may be other fixes needed. Don't assume the work is done.
- The version bump (`pyproject.toml`, `src/tuxtalk/__init__.py`) moves, in
  the same commit, the README's install-from-source command and
  `aur/tuxtalk`'s pkgver with its `.SRCINFO` (`sha256sums` stays SKIP until
  the tag): tests pin all three to pyproject, so the suite passes at the bump.

### Step 3: Build the AppImage
```bash
cd /home/ron/Projects/TuxTalk
./build-release.sh
```

### Step 4: Copy to ~/AppImages/
```bash
cp TuxTalk-v*.AppImage ~/AppImages/
chmod +x ~/AppImages/TuxTalk-v*.AppImage
```

### Step 4b: The VM Run, BEFORE Ron Is Asked for Anything
Ron, 2026-10-31: the fresh start with him must not begin until everything
Claude can test alone is done. First build the .deb and .rpm from the new
AppImage (`./build-deb.sh; ./build-rpm.sh`): the last check of a run
(`package_update`) installs them through Download & Install's root script
with apt, dnf and zypper, and skips without them. Then run the harness on
all eight VMs, two runs side by side (each VM uses 8 GB):
```bash
cd /home/ron/Projects/TuxTalk/tests/vm
python3 -u run.py test kde suse mint debian --appimage ~/AppImages/TuxTalk-vX.X.X-x86_64.AppImage
python3 -u run.py test silverblue zorin ubuntu pop --appimage ~/AppImages/TuxTalk-vX.X.X-x86_64.AppImage
```
- The two ways in without a package file, in the CachyOS VM: the README's
  From source steps from snapshot tuxtalk-clean (part 1's Task 8 driver,
  the archive served inside the VM), and `makepkg` of `aur/python-onnx-asr`
  and `aur/tuxtalk` from snapshot tuxtalk-aur-deps. The tag doesn't exist
  yet: `aur/tuxtalk` builds from a test copy whose source is the local
  `git archive --prefix=TuxTalk-X.X.X/ HEAD`, its sha256 in the copy only.
  Then the suite on Arch's current library versions
  (`~/.cache/tuxtalk-arch-venv`, `PYTHONPATH=./src` only; CODE_HEALTH row 82 b).
- Every fail is fixed (back to Step 2) or explained to Ron before Step 5.
  "Misheard" is the computer voice, not TuxTalk.
- Then give Ron only what the VMs can't do (the report's last section: the
  graphics card, the real password, his own voice, real apps, the AI features,
  file dialogs, and how it feels), as a short list.

### Step 5: Ask Permission for Fresh Start
- Say: "I need to run `./fresh-start-for-testing.sh` which will kill all TuxTalk processes, remove AppImage config/data, CUDA libs, cached models, and the GNOME extension. Your dev settings and data stay, but the speech and AI models are shared with dev, so dev downloads them again afterwards. Is that okay?"
- WAIT for explicit "yes" or "go ahead"

### Step 6: Run Fresh Start and Verify
```bash
cd /home/ron/Projects/TuxTalk
./fresh-start-for-testing.sh
```
- Check that ALL items show "✓ Removed" in the output, including
  "✓ TuxTalk models removed". Since 0.14.2 the script clears Parakeet, the
  Vulkan models and the AI model too, so first run really downloads them
- Show Ron the AppImage file size (must be under 1GB)

### Step 7: Launch the AppImage from a Terminal (Claude runs this)
- **Claude MUST launch the AppImage using the Bash tool** so terminal output is visible
- NEVER give Ron a command to run himself — Claude launches it directly
- **ALWAYS launch to a log file AND monitor it** — this is a hard requirement, not optional
- Launch in background and capture output:
```bash
cd ~/AppImages && env -u GDK_BACKEND ./TuxTalk-vX.X.X-x86_64.AppImage > /tmp/tuxtalk-output.log 2>&1 &
```
- Then read both logs to confirm startup. The terminal output holds only
  crashes and command-line messages; TuxTalk's own progress (the engine
  loading, each dictation) is in its log file:
```bash
sleep 3 && cat /tmp/tuxtalk-output.log && tail -n 40 ~/.config/tuxtalk/tuxtalk.log
```
- Continue monitoring both as Ron tests — check them after each reported issue

### Step 8: Ron Tests — WAIT for Results
Before Ron starts, show him his part as a checklist panel in the chat (the
visualize show_widget tool): only what the VM run (Step 4b) can't check,
numbered, one feature per check (never two under one heading: he missed
Backup tucked under Transcribe a File), each with its steps and Pass /
Problem buttons, so he can work
down it and dictate results as he goes (Ron, 2026-11-01). Keep it to what
needs him: his graphics card, his password, his voice, file dialogs, feel.
Ron tests against this checklist. Do NOT proceed until he reports back.
Claude checks every item against the log too
(`~/.config/tuxtalk/tuxtalk.log`), not only Ron's report. In 0.14.1 the
"GPU" test had really run on the processor.

The full list of checks is in RELEASE_CHECKLIST.md. Read it at Step 8 and
build Ron's panel from it.

### Step 9: Fix and Rebuild if Needed
If ANY test fails: fix the issue, go back to Step 2, and repeat.

### Step 10: Package the GNOME Extension and the distro packages
```bash
./package-extension.sh
./build-deb.sh
./build-rpm.sh
```

Both `build-deb.sh` and `build-rpm.sh` take no arguments — they read the
version from `pyproject.toml` and repackage the **already-built, already-tested**
AppImage, so they must run AFTER Step 3 and after Ron approves in Step 8.
They need `fpm` installed (`gem install --user-install fpm`); the .rpm also
needs `rpmbuild`.

Order among these three does not matter — each one removes only its OWN line
from `SHA256SUMS.txt` and appends a fresh one. But `./build-release.sh` DELETES
`SHA256SUMS.txt` and regenerates it from scratch, so re-running it (Step 9)
after this step silently drops the .deb and .rpm checksums. If you go back to
Step 2 for a fix, re-run all of Step 10 afterwards.

### Step 11: Commit, Push, and Create GitHub Release
First rewrite the README's "New in X.Y" section (and its link at the top)
for this version, in plain words, from the CHANGELOG.
```bash
git add <changed files>
git commit -m "..."
git push
gh release create vX.X.X \
  TuxTalk-vX.X.X-x86_64.AppImage \
  TuxTalk-vX.X.X-x86_64.AppImage.zsync \
  TalkType-vX.X.X-x86_64.AppImage.zsync \
  tuxtalk_X.X.X_amd64.deb \
  tuxtalk-X.X.X-1.x86_64.rpm \
  tuxtalk-gnome-extension.zip \
  talktype-gnome-extension.zip \
  SHA256SUMS.txt \
  --title "..." --notes "..."
```

Note the .deb and .rpm use the bare version (`0.7.2`), NOT the `v` prefix the
tag and AppImage use.

**NEVER create a GitHub release without the .deb and .rpm.**
They are how every Debian/Ubuntu/Mint and Fedora/RHEL user installs. Omitting
them ships an AppImage-only release and silently strands those users.

**NEVER create a GitHub release without the AppImage's `.zsync` file.**
`./build-release.sh` writes it next to the AppImage. The AppImage carries
update information pointing at the `.zsync` in the *latest* release, so a
release without it breaks AppImageUpdate and similar tools for everyone.

**NEVER create a GitHub release without the GNOME extension zip.**
The extension is downloaded from GitHub by users — if you forget it, users get a 404 error on first run.

**ALWAYS include SHA256SUMS.txt in the release** (generated automatically by
`./build-release.sh` and updated by `./package-extension.sh`). The in-app
updater checks the AppImage, .deb or .rpm against it before installing, and
refuses an update whose release has no SHA256SUMS.txt or doesn't list the file:
a release without it can't be installed from inside TuxTalk at all.

**NEVER create a GitHub release before Ron has tested and approved.**

**ALWAYS post a release announcement** in Discussions → Announcements after the
release is up: what changed for the user in a few short paragraphs, a link to
the release notes, the Feature Requests invite, and the line on how TuxTalk is
made. Draft it in Ron's voice (ron-voice skill) and post only after he approves
it. Announcements is also where "coming next" posts go (first one: #15).

**ALWAYS end the release notes with the AUR vote ask.** The release page is
where people already have installing on their mind, so it converts far better
than the README does. Use this line verbatim:

```
Using TuxTalk on Arch? A [vote on the AUR page](https://aur.archlinux.org/packages/tuxtalk-appimage) helps other Arch users find it.
```

AUR votes only move when asked for — the package sat at zero from December
2025 to at least August 2026 while still being installed regularly.

### Step 12: Tell People (every release, the same day)

Before uploading Parakeet or Vulkan engine files, telling people about a
release, or posting anywhere public, read PUBLISHING.md first.

---

## PUBLISHING: ENGINE UPLOADS AND PUBLIC POSTS

Before uploading Parakeet or Vulkan engine files, telling people about a
release, or posting anywhere public, read PUBLISHING.md first. It holds Step 12
(Tell People), how the Parakeet Ultra and Vulkan engine files are hosted and
updated, and how to say how TuxTalk is made.

---

## TYPING — TUXTALK'S OWN VIRTUAL KEYBOARD

Since 0.18 every key TuxTalk presses (typed text, Enter, Ctrl+V, backspaces,
select and move) goes through one uinput keyboard the dictation service makes
when it starts (`src/tuxtalk/virtual_keyboard.py`). There is no ydotool or
ydotoold any more; `keyboard_layout.py` works out the keys for the active
layout, US included. TuxTalk's hotkey reader and the installed hotkey helper
skip it by its name (`virtual_keyboard.NAME`, the helper's
`OWN_KEYBOARD_NAME`), so neither grabs or listens to it; since 1.13 they read
keyboards shaped like a pointer too (Logitech's, issue #26). Access to /dev/uinput comes from the hotkey
helper's udev rule (`secure_input.py`) or the older input-group rule.

Timing (`KEY_DELAY_MS`, config `key_delay`, default 2 ms after each key
event) was measured in the VMs: 1 ms is clean in every app class tested, 0 ms
overflows the kernel's buffer, and the delay also sets how long a frozen
desktop survives (about 64 key events' worth). Never lower it without the
same VM measurements (`kbbench`, notes in Obsidian "TalkType/2026-10-20 - own
virtual keyboard"). Tests get a recording keyboard from `tests/conftest.py`;
never make a real one in a test, it types into Ron's focused window.

---

## EMOJI — TUXTALK CARRIES ITS OWN

Every emoji TuxTalk's windows show comes from its own small font,
`src/tuxtalk/fonts/tuxtalk-emoji.ttf` (Google's Noto Color Emoji cut down to
what TuxTalk uses), loaded in every process by `ui_style.prepare_windows()`.
Without it, a computer with no emoji font showed boxes with the character's
code in them (issue #19, Mint).

**Adding or removing an emoji anywhere in `src/tuxtalk`: run
`python3 packaging/build-emoji-font.py` and commit the font and its `.txt`
list with the change.** `tests/test_emoji_font.py` fails until you do, here
and on GitHub. A plain text symbol that every font has (like ✓ or →) goes in
the script's `TEXT_SYMBOLS` instead. Ron asked (2026-10-22) that this never
slip: a missing emoji is a box on someone's screen.

---

## VULKAN GRAPHICS ENGINE

Built and hosted separately, not part of the AppImage. How to update and
upload it (and the `ENGINE_*` values in `whisper_vulkan.py`) is in PUBLISHING.md.

---

## DESTRUCTIVE COMMANDS — ALWAYS ASK FIRST

**BEFORE running ANY of these, ASK and WAIT for permission:**
- `./fresh-start-dev.sh` — Deletes all dev config, data, models
- `./fresh-start-for-testing.sh` — Deletes AppImage config/data
- Any `rm -rf` commands
- `gsettings set` — Modifying GNOME settings
- Any script that deletes user files, configs, or data

**Format:** "I need to run [COMMAND] which will [EXPLAIN WHAT IT DELETES]. Is that okay?"

**Exception:** Ron explicitly requested it in his current message.

**PRESERVE USER DATA ABOVE ALL ELSE.**

---

## RON'S DAILY SETUP — DO NOT CHANGE

**Ron uses the DEVELOPMENT VERSION day-to-day, NOT the AppImage.**

### Development Version:
- **Desktop launcher**: "TuxTalk (Dev)" in `~/.local/share/applications/tuxtalk-dev.desktop`
- **Autostart**: Launches the dev version, NOT the AppImage
- **DEV_MODE=1**: Shows BOTH tray icons (GTK tray + GNOME extension)
- **Source code**: `/home/ron/Projects/TuxTalk/src/tuxtalk/`

### Autostart Configuration:
```
Exec=/home/ron/Projects/TuxTalk/run-dev.sh
```

### NEVER set GDK_BACKEND when launching the tray

Do not add `GDK_BACKEND=x11` to the launcher, the autostart file, `run-dev.sh`,
the AppImage's AppRun, or a test command — it was there for years and had to be
removed in v0.6.0.

Under XWayland a GTK3 combo popup does not stay open on a plain click; it only
responds to press-drag-release. That made **every dropdown in the app unusable**,
including the model picker on the first-run setup screen.

The recording indicator genuinely does need XWayland (`gtk_window_move()` is
honoured there and ignored on native Wayland, which centres the window). The
tray therefore sets it for the dictation service **alone**, in `tray.py`'s
`_launch_service()`. `tests/test_gdk_backend_scope.py` fails if it is ever set
globally again.

### AppImage is ONLY for:
- Testing before release
- Distribution to other users
- NEVER for Ron's daily use

### NEVER damage or delete:
- The `.venv/` directory
- The `src/tuxtalk/` directory
- The development desktop launcher
- Ron's config files without explicit permission

---

## GTK TRAY AND GNOME EXTENSION — KEEP IN SYNC

TuxTalk has TWO user interfaces that must stay synchronized:

1. **GTK Tray Icon** (`src/tuxtalk/tray.py`) — For all Linux desktops
2. **GNOME Shell Extension** (`gnome-extension/talktype@ronb1964.github.io/extension.js`) — For GNOME users

**When adding/modifying menu items or features:**
- Add to BOTH the GTK tray menu AND the GNOME extension menu
- Keep menu order identical between both
- If adding a D-Bus method, update:
  1. `src/tuxtalk/dbus_service.py` — Add the D-Bus method
  2. `src/tuxtalk/tray.py` — Add method to `TrayAppInstance` class
  3. `gnome-extension/.../extension.js` — Add to D-Bus interface AND menu

**Menu order (must match in both):**
```
Dictation on/off (toggle)
Recent Dictations ▸
Fix a Word...
Transcribe a File...
─────────────────────
[model] on [where it runs]   (one read-only line)
Performance ▸
─────────────────────
Preferences...
Voice Commands...
Your Stats...
Help...
About TuxTalk...
Check for Updates...
Restart Service
─────────────────────
Quit TuxTalk
```

---

## WORKING STYLE

Explain what code does in plain language. Always:
- Provide exact copy/paste commands (include `cd` commands)
- Be direct and honest
- Never assume the reader will debug code themselves

---

## CODE A HUMAN REVIEWER WOULD RESPECT — EVERY CHANGE

TuxTalk is written by Claude and says so publicly. Ron's standard (2026-10-18):
an experienced developer who opens any file should be impressed, not shaking
their head at "more AI slop". Working code is not enough. Before every commit,
check the change against this list:

- **No dead code.** Delete unused functions, imports, branches, settings and
  files in the same change that made them unused. Never leave code commented
  out "just in case"; git keeps history.
- **No duplicates.** Search for an existing helper before writing one. Two
  functions doing the same job is the clearest sign nobody read the codebase.
- **Small files and functions.** A module does one job, and a function one
  thing. New code stays within 1,500 lines a file, 100 lines and 15
  decision points a function; old code over that only shrinks (the gate in
  Step 1b measures it). A diff can't show this, since it shows the lines
  added and never how long the file has grown: the 2026-10-12 audit noted
  three huge files, and by 0.18 app.py was 5,900 lines and prefs.py 7,100.
- **Comments say why, not what.** No comment that restates the next line. Do
  explain the non-obvious: the desktop quirk, the bug it prevents, the issue
  number. Keep comments true when the code changes. Every module, and every
  function or class of 15 lines or more, has a docstring saying what it's
  for (more than its name says: never filler). A
  comment gives the reason, not the story: no names or dates in code ("Ron,
  2026-10-18: …"); who asked and when go in the commit message.
- **No speculative structure.** No classes, options, layers or parameters for
  a future that isn't here. The simplest code that handles the real cases.
- **Fit the file.** Match the surrounding names, style and idioms. A change
  should look like the rest of the file was written by the same person.
- **Tests that test something.** Each test pins a behaviour a user would
  notice, named in plain words. No tests that only re-run the code's own
  logic, and no mocking so heavy the test can't fail.
- **No AI tells in code or commits.** No emoji in comments, log messages or
  commit messages (icons in the app's own screens are a design choice), no
  "This function does X" docstrings that add nothing, no "Enhanced"/"Robust"/
  "Comprehensive" wording, no apologetic or chatty comments.
- **Errors handled where they can happen, not everywhere.** No blanket
  try/except that hides bugs; a caught error is logged with what went wrong.
- **Small, focused commits** with a message that says what changed for the
  user and why.

**Exceptions.** If a change can't meet this (a rushed hotfix, say), it needs
Ron's OK first, the shortfall goes in `CODE_HEALTH.md` with the release that
fixes it, and the commit message says so. The same goes for raising a number
in the gate's baseline or loosening a limit.

## THE REVIEW PROMPT

Both reviewers get this, word for word, plus what to review and the files:

> You are reviewing TuxTalk as a senior Python developer deciding whether this
> code is something its authors could be proud of. Read every file the change
> touches in full, not only the changed lines. Read CLAUDE.md's "CODE A HUMAN
> REVIEWER WOULD RESPECT" first and hold the code to it. Report, each with
> file:line, a concrete failing scenario or the exact problem, and the
> smallest fix: (1) bugs and regressions, including desktop- and
> distro-specific ones; (2) design: a module doing more than one job, a
> function doing more than one thing, logic in the wrong layer, state shared
> where it needn't be; (3) duplicates, here or anywhere else in src/; (4)
> dead code, unused parameters, speculative options; (5) error handling: an
> error caught where it can't happen, or swallowed without a log; (6)
> comments: missing where the code isn't obvious, restating the code, out of
> date, telling history instead of the reason; (7) names that mislead; (8)
> tests that can't fail or don't pin what a user would notice; (9) security
> and privacy, wherever the code runs as root (the hotkey helper), runs a
> program, downloads or unpacks a file, checks an update, listens on D-Bus or
> a socket, writes a file other users could read, or could log dictated text
> or audio; (10) threads: GTK touched only from its main thread, shared state
> guarded; (11) in windows, keyboard-only use, focus order and labels a
> screen reader can read. For each
> touched file give its length and its three longest functions. Verify each
> claim before reporting it (grep before calling something unused). Rank by
> harm to users first (lost text, a crash, a security or privacy hole), then
> by what an experienced developer opening the file would notice. Say
> plainly when a category is clean. Read-only: no edits.

## KEEP IT SIMPLE — EVERY NEW FEATURE

Ron's worry (2026-10-18): features piling up into a steep learning curve. The
default experience is three steps, hold F8, talk, let go, and nothing a new
user sees may make it look harder. The 0.16 simplicity review cut what
Preferences shows from 51 settings to about 26; keep it there:

- **No visible setting unless the feature can't work without one.** Prefer
  behaviour that adapts by itself: no automatic Enter in text editors, plain
  text in terminals, Power Saving on only with a battery.
- **A niche setting goes in a closed fold** (Preferences → Advanced →
  Fine-tuning, General → More shortcuts), never in the main flow.
- **Every fold is a button, never GTK's small expander arrow** (Ron,
  2026-10-18: the arrow is too easy to miss). A real button that names what
  it opens ("Show more shortcuts", "Adjust Settings"), turning into "Hide …"
  when open, with a short dim summary beside it of what's inside or how it's
  set, and the settings sliding open right below. Build it with
  `ui_style.Fold` (Preferences: `prefs_tab.fold`), never by hand; `tests/test_fold.py`
  fails on any Gtk.Expander. This holds for every new fold, in Preferences and
  in any other window.
- **An experiment goes in TuxTalk Labs**, off, until it's earned a place.
- **Several controls that answer one question become one dropdown** over the
  existing settings (the language, other sound and numbers dropdowns), so old
  configs and backups keep their meaning.
- **Status shows only when it needs action** (System check opens itself on a
  problem).
- **Help is by situation** (Start Here, Chatting, Writing, Terminals,
  Hands-free, Mistakes, Problems): a new feature goes where people would
  use it, and Help names keys from the settings, never hard-coded F8.

## SAYING HOW TUXTALK IS MADE

Where and how to say it: PUBLISHING.md.
