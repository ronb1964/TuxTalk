# TuxTalk Project Rules for Claude

**The app was renamed from TalkType to TuxTalk in 0.15.0** ("TALKTYPE" is
another company's registered trademark for dictation software). Some names
deliberately stay TalkType because changing them would hurt users:
- the project folder `/home/ron/Projects/TalkType` and its `.venv`;
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

**READ THIS ENTIRE FILE BEFORE DOING ANYTHING. THESE RULES ARE MANDATORY.**

---

## STOP — MANDATORY RELEASE PROCESS

**You MUST follow this exact sequence. No skipping steps. No exceptions.**

### Step 1: Code Changes
- Make and test code changes in `/home/ron/Projects/TalkType/src/`
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

### Step 3: Build the AppImage
```bash
cd /home/ron/Projects/TalkType
./build-release.sh
```

### Step 4: Copy to ~/AppImages/
```bash
cp TuxTalk-v*.AppImage ~/AppImages/
chmod +x ~/AppImages/TuxTalk-v*.AppImage
```

### Step 5: Ask Permission for Fresh Start
- Say: "I need to run `./fresh-start-for-testing.sh` which will kill all TuxTalk processes, remove AppImage config/data, CUDA libs, cached models, and the GNOME extension. Your dev environment stays untouched. Is that okay?"
- WAIT for explicit "yes" or "go ahead"

### Step 6: Run Fresh Start and Verify
```bash
cd /home/ron/Projects/TalkType
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
- Then tail the log to confirm startup:
```bash
sleep 3 && cat /tmp/tuxtalk-output.log
```
- Continue monitoring output as Ron tests — check the log after each reported issue

### Step 8: Ron Tests — WAIT for Results
Ron tests against this checklist. Do NOT proceed until he reports back.
Claude checks every item against the log too
(`~/.config/tuxtalk/tuxtalk.log`), not only Ron's report. In 0.14.1 the
"GPU" test had really run on the processor.

First run (since 0.14.2 it recommends one setup; CUDA is not offered here):
- [ ] Welcome screen appears, and its card names the graphics card and language ("Parakeet on your NVIDIA ..., For English")
- [ ] Other options: changing the language, model or graphics-card tickbox updates the card and its download size. The language list reads "Spanish (Español)", "Japanese (日本語)" and so on, with no flags; Preferences → General → Change model, device or language → Language shows the same list
- [ ] Welcome screen's "How It Works" reads 1. Hold F8 (or a key you pick next), 2. Talk, 3. Let go. Hotkey test, then straight to the downloads: no "You're All Set!" screen before them. The hotkey test works without a mouse: Tab to Change Key, Space, press the new key, Tab on to Continue
- [ ] Downloads show progress bars: the Vulkan engine and Parakeet for the graphics card. The log must show a real download, not "cached": "Parakeet Ultra downloaded for vulkan"
- [ ] The speed check runs, and the log shows its result (`Speed check (parakeet-v3): graphics …s, processor …s`)
- [ ] Default settings are correct (model=parakeet-v3, device=vulkan when the card won, auto_period=True, auto_timeout 5 min, language_mode=auto)
- [ ] Dictation works on the graphics card (log: `Parakeet on GPU`, and "Parakeet on the graphics card from ggml-parakeet-ultra-q8_0.bin")
- [ ] Parakeet Ultra offer (a VM with the original Parakeet from 0.16, updated to this version without fresh-start): about 20 s after start, "Parakeet got more accurate" with Not Now and Download (669 MB, or 668 MB on the processor). Download shows a progress bar, dictation restarts by itself (log: "Parakeet Ultra downloaded", "Parakeet on the graphics card from ggml-parakeet-ultra-q8_0.bin") and dictates. Restart TuxTalk: no second offer. On another VM, Not Now: dictation keeps working on the original Parakeet and the offer never comes back, but Preferences → General → Speech Recognition shows "A more accurate Parakeet is available." with Update Parakeet (669 MB, or 668 MB on the processor); it downloads, dictation restarts onto Ultra, and the row is gone next time Preferences opens. About TuxTalk credits Moondream, NVIDIA and Olicorne
- [ ] Hotkey helper (default since 0.16): first-run's Fix Typing asks for the password once, says no restart is needed, and F8 works straight away without the user joining the input group (`systemctl is-active tuxtalk-hotkeys`, `getent group input`; log: "Hotkeys come from the TuxTalk hotkey helper"). After a reboot it starts by itself. In a VM, also once on an immutable distro (Bazzite or Silverblue) and once without systemd, where it falls back to the input group (log: "Hotkey helper not used") and the setup screen and Preferences say why, with the 🔒 line about programs reading the keyboard

- [ ] No system tray (Silverblue VM, plain GNOME): the welcome screen's "Install GNOME Extension" is ticked, with the amber "this desktop has no system tray" line. Untick it and finish setup: TuxTalk's menu window appears (log: "No system tray and no extension running"). Opening TuxTalk from the apps again brings it back (log: "Opened the menu window (no system tray here)"); its buttons work, and the switch turns dictation off and on. Right-click TuxTalk in the dash: Preferences, Transcribe a File, Help, Quit TuxTalk. On KDE, opening TuxTalk again shows the same window without the no-tray note
- [ ] Press enter: in a chat box (or KWrite) dictate "see you at noon, press enter": types "See you at noon." then a real Enter (log: 'Pressed Enter (said "press enter")'). Saying only "press enter" just presses Enter. "press enter to continue" mid-sentence is typed as words; "new line" still makes a line without sending. Preferences → Advanced → Text → "Send each dictation automatically (press Enter)" (off, "Send: After 2 seconds" greyed until ticked; choices Right away to After 4 seconds). Ticked, in a chat box: "Sending in 2 s. Tap F8 to hold it." floats over the lower part of the window, just above the message box, counts down 2, 1 (with 4 seconds: 4, 3, 2, 1), then Enter (log: "Sending in 2s", "Pressed Enter"). Tap F8 during the wait: "Not sent. Press Enter when you're ready." and nothing is sent (log: "Held the send"); tap F9 the same, with no recording started. Hold F8 during the wait and keep talking: that dictation is added after a space ("delay. All right", not "delay.All") and then sent. Switch windows during the wait: the countdown stops at once with "Not sent: you switched windows." and nothing is sent (log: "Not sent: another window has the focus now"); on KDE and GNOME also between two KWrite windows maximized on one screen, while moving the window during the wait still sends. Click into another chat window while a long dictation is still going in: the text stays and nothing is sent, with the same notice (log: "Not sent: the focus moved while the words were delivered"). "Right away" sends at once; with Right Ctrl as the toggle key, a tap while the Enter waits for the key holds it there too. Open Preferences during the wait: nothing is sent ("Not sent: a TuxTalk window has the focus."). On GNOME updated from 0.18, before logging out and in, nothing is sent automatically ("Not sent: this desktop doesn't say which window has the focus. Press Enter when you're ready."), as on COSMIC, while "..., press enter" still presses it. In KWrite (or LibreOffice Writer) nothing is sent and no new line appears (log: "Not sending: Enter only starts a new line in this app"); saying "press enter" there still presses it. "it inserted a carriage return" is typed as words. Let a dictation send in KWrite, then say "edit that, make it more formal": nothing is typed, and "Already sent. Tap the hotkey sooner" floats over the window (log: "Voice edit not done: already_sent_auto"). "meet me at noon and" (let go after "and") is typed with no period and not sent (log: 'Held back the Enter: the dictation ended on "and"'); the next dictation carries on in lowercase
- [ ] Press tab: in KWrite dictate "name press tab address": types "Name", a real tab, then "address." "every tab shows", "the tabs in Claude Desktop" and "press the tab key" mid-sentence are typed as words; "literal press tab" types "press tab". At the end it presses the Tab key: in a web form (Firefox) dictate "John, press tab", then "Smith, press tab": "John" and "Smith" land in two boxes with no period and the focus moves on each time (log: 'Pressed Tab (said "press tab")'); saying only "press tab" just moves on. In Claude Desktop "John, press tab" types "John" and four spaces and the focus stays in the box (no "Pressed Tab" in the log), with Keystroke Typing (Fine-tuning) too, and "name press tab address" mid-sentence there types four spaces as well; saying only "press tab" there types four spaces, and in a password box there it presses the real Tab. With "Send each dictation automatically" on nothing is sent after it. In `zenity --password --username`, saying only "press tab" in the Password box moves the focus on
- [ ] Your Words for Parakeet: with TuxTalk in Preferences → Words, on Parakeet say "I dictate everything with TuxTalk": log "Your Words for Parakeet on the graphics chip: N spellings" (Vulkan) or "Your Words for Parakeet: N spellings" (processor), and "Your Words: Parakeet wrote one of your words" when it had to steer (never the word itself). Ordinary sentences ("tax talks went on", "the cat clawed the couch") come out unchanged
- [ ] Own virtual keyboard (0.18): the log shows "Made TuxTalk's virtual keyboard" when the service starts and `pgrep ydotoold` finds nothing. Preferences → Advanced → Show fine-tuning → Text Injection → Keystroke Typing (or Mint, X11, which always types): "Typed N chars in …s (2 ms a key event)" and the text is right, capitals and punctuation included; also on a German or Swiss layout (Y and Z, umlauts). A VM updated from 0.17 that had run Fix Typing: `~/.config/systemd/user/ydotoold.service` is gone after the first start (log: "Removed the ydotoold service")
- [ ] Silent microphone: mute the mic (or pick a dead input) and hold F8 for two seconds: cancel beep and the notification "No sound from the microphone" (always, even with notifications off); every TuxTalk notification's title says what happened, never just "TuxTalk". A quiet room with a working mic does not say this
- [ ] Password fields: run `zenity --password --username` (a GTK window), click into its Password box, hold F8 and talk: nothing is typed, the cancel beep plays and "TuxTalk doesn't type into password fields" floats over the window (log: "Not typed: the cursor is in a password field", without the words). Recent Dictations doesn't list it, and Ctrl+Alt+P there types nothing either. Its Username box gets the dictation as usual. `pkexec true` in a terminal opens the desktop's own password prompt (GNOME, KDE, Cinnamon, COSMIC, Xfce): dictating there types nothing either (same log line)
- [ ] Turn other sounds down: Preferences → Audio → "Other sound while you dictate" reads "Pause music and videos"; its other choices are "Pause music and videos, turn the rest down", "Turn everything down" and "Leave it alone". Pick "Turn everything down". With a YouTube video or a Discord call playing, hold F8: the sound drops to half its volume setting (quieter, still audible) and comes back after (log: "Turned 1 other sound(s) down", "Put 1 sound(s) back"). Change that app's volume during a dictation: it keeps your change
- [ ] Turn other sounds down with a video already paused: pause a YouTube video in Brave, pick "Turn everything down", hold F8, then play the video: its sound comes back at full volume (log: "Put 1 sound(s) back" or nothing turned down)
- [ ] Setup Complete (after the downloads) shows the three steps, the F9 and Ctrl+Alt+P tips, and with the extension installed an amber "Log out and back in" line; with a restart owed its button reads "Restart computer now"
- [ ] Welcome and Setup Complete on a short screen: the scrollbar shows, the text fades at the bottom and "More below" sits above the button until the end

Preferences and the rest:
- [ ] Dictation works in CPU mode (Device → CPU downloads the processor copy of Parakeet with a progress bar)
- [ ] CUDA: Preferences → General → Change model, device or language → Device lists "CUDA (1.4 GB download, …)" on NVIDIA before anything is downloaded. Picking it asks, then shows a progress bar, then "Downloaded!". On Parakeet it then asks which Whisper model to use on CUDA, Large-v3 preselected (Keep Parakeet changes nothing, and the dropdown goes back); on a Whisper model it switches to "cuda" by itself. Either switch restarts dictation onto CUDA (check the log). Advanced shows only the status line
- [ ] With Parakeet selected, the CUDA entry reads "CUDA (Parakeet runs on the processor)" and the warning under the settings is amber and bold
- [ ] Dictation works on CUDA with a Whisper model (e.g. small)
- [ ] AI self-corrections: turning it on downloads the model; "meet at three, no wait, four" types "meet at four"
- [ ] Auto-punctuation works consistently
- [ ] Power Saving: Preferences → General → "Save power when dictation isn't used for a while", ticked, After 1 minute, OK. Wait a minute without dictating (log: "Power saving: no dictation for 1 minute; let go of the speech model"); the tray still shows dictation on. Hold F8 and talk: it types, a few seconds later than usual (log: "loading the speech model again for this dictation"). With Vulkan, `pgrep -f whisper-server` finds nothing while it rests. Untick it afterwards
- [ ] Hands-free: double-tap F9 and say nothing. It stops by itself after 10 seconds with the cancel beep and types nothing (log: "Auto-stop: nothing said"). With the bars indicator, the bars stay low while it's quiet
- [ ] Hands-free beeps: double-tap F9: the start beep and the higher hands-free beep right after it are both heard in full, every time (log once per start: "Beeps: the output's latency is …s"). The VM check `handsfree_beeps` does the same on a 0.17 s output and names the latency TuxTalk measured (skipped when the slow setting didn't reach TuxTalk's output)
- [ ] Start/stop beeps play
- [ ] With a YouTube video playing, dictating pauses it and it plays again after (log: "Paused … while dictating", "Resumed …"). A video that was already paused stays paused. Preferences → Audio → "Other sound while you dictate": "Leave it alone": nothing pauses
- [ ] Preferences mic test works (level meter, record, replay: the last word is heard too)
- [ ] Advanced tab: Text (Send each dictation automatically, Numbers, Remove "um", Fix self-corrections with AI), a "Show fine-tuning" button ("formatting, inserting text, privacy" beside it) and "Show system check" with "✓ everything is set up" beside it, both closed, then Labs. Each button opens its settings right below and turns into "Hide …". With typing not set up (fresh VM, before Fix Typing), System check opens by itself and reads "⚠ something needs your attention" in amber
- [ ] Help (tray → Help...): the same dark look and outlined tabs as Preferences, with "TuxTalk Help" above them: tabs Start Here, Chatting, Writing, Terminals, Hands-free, Mistakes, Tips, Problems, Commands, all showing without arrows (Mint too), and the feedback link at the bottom readable. Left and Right change tab; Tab goes into that section's text (the section stays the same), where Page Down, the arrows, Home and End scroll it, and Shift+Tab goes back to the tabs. Preferences → Advanced → TuxTalk Labs → How these work opens it at Writing. On a light theme (Breeze) it is dark all the same, title and link included. Tips names your own keys (dictation key choices, double-tap for hands-free, Paste Last Dictation). Start Here opens on "Hold F8 and talk" with your own keys (change the hold key to F6, open Help again: it says F6). Voice Commands... in the menu lists Clear the Whole Box apart from Undo, and Editing by Voice
- [ ] Report a problem: Help → Problems → Report a problem opens a window that shows the report before anything else: TuxTalk version, distro, desktop and session, keyboard layout, graphics card, hotkey helper, the settings that matter and the end of the log, with the username replaced and no dictated words (an amber line says so if the log has some). Copy puts it on the clipboard, Save writes a .txt where you choose, Open a GitHub issue opens the bug form with it filled in. Nothing is sent by itself
- [ ] Recording indicator: Preferences → Audio has "Show recording indicator" with Customize… (greyed while unticked). Customize… opens a "Recording Indicator" window: Size, Style, Color, Custom color, Animation response, Position and "Show fine position" ("not moved" beside it, or how far it moved) opening the two offsets and Reset to 0. Change the style there, Done, OK: the next dictation shows the new style. Cancel in Preferences after changing it there: nothing changes
- [ ] Mic follows the system default: with Preferences → Audio → Microphone on System Default, switch the input in KDE's volume applet (or GNOME Quick Settings) and dictate: it records from the new mic without a restart (log: "The system's default microphone changed to …"). Switch back afterwards
- [ ] TuxTalk Labs: Preferences → Advanced ends with the Labs section (note, Voice editing, How these work, Send feedback). How these work opens Help at Writing, where the Labs part starts; Send feedback opens Discussions
- [ ] Indicator follows the window (Labs): turn it on, put KWrite in a corner of the screen and dictate: the indicator appears near the bottom of KWrite, then in another window near its bottom. Off: back to the position in Preferences → Audio → Customize…. A voice edit with nothing selected shows "Select some text first" over the middle of the window (and the corner notification)
- [ ] Voice editing: Preferences → Advanced → TuxTalk Labs → Voice editing (downloads the AI if self-corrections hasn't). In KWrite select a sentence, hold Shift+F8, say "make this more formal": the selection is replaced, Ctrl+Z restores it, and the clipboard still holds what it held before (log: "Voice edit:"). Nothing selected: the notice says to select text first. Shift+F9 tap-to-start/stop works too. With it off, Shift+F8 is left to the app and dictates nothing. Alt+F8 (KDE: resize the window) and Ctrl+F8 never start a dictation, on or off
- [ ] Voice editing, two selected lines in KWrite: "make this all caps", "remove the commas" and "remove the word testing" come out exactly with both lines kept (log: "Voice edit done in code"); "add an exclamation point" keeps both lines. "Make this bold" in KWrite says LibreOffice; in LibreOffice Writer it bolds the selection, and italics and underline work too (log: "with the app's shortcut"). "Make this text larger" says it can't change font, size or color. Two changes at once: select a sentence with commas, "make this formal and remove the commas" rewords it and the commas are gone (log: "Voice edit: 2 changes, one at a time"); "make it shorter and simpler" stays one change
- [ ] "Edit that" (voice editing on): in KWrite dictate a sentence, then F8 "edit that, make it more formal": the sentence is selected and rewritten, Ctrl+Z restores it (log: "Voice command: edit that"). "Edit that file tonight" is typed as words, and so is "edit that, make it shorter" when nothing was just dictated
- [ ] Type at pause (Labs) with hands-free: tick it, double-tap F9 in a chat box, say "see you at noon, press enter", pause: "See you at noon." is sent while still recording (log: 'Pressed Enter (said "press enter")'); go on with "how are you", pause, "press enter": a second message, capitalised. Then "edit that, make it friendlier" before "press enter" rewrites the whole message. "Send each dictation automatically" presses Enter once, at the stop, not after each phrase; dictate a phrase into one Brave window, click into a second Brave window and end on a pause: the second gets neither the period nor an Enter ("Not sent: you switched windows.")
- [ ] Select and move by voice (Labs): Preferences → Advanced → TuxTalk Labs has "Select and move by voice", off, and the Labs note asks for feedback. Turn it on; in KWrite dictate a sentence, then say only "select the last word": the word is selected without the period after it (log: "Voice command: select the last word"). "go to the start of the line", "select the line", "go to the end" and "unselect" work. "I want to go to the end" is typed as text. With voice editing on: Shift+F8 "select the last sentence" with nothing selected selects it, then Shift+F8 "make it more formal" rewrites it. In Konsole: a small window "Select and move don't work in a terminal" with "Don't explain this again" and Got it, every time; tick the box and next time only the floating label "Can't select or move in a terminal". Labs → Show tips again brings the window back. After dictating in another window, "select the last sentence" explains "Select works right after you dictate"
- [ ] Pastes on KDE (Type at pause on, Send automatically off): in KWrite dictate three phrases with pauses, "this is a test", "of pasting in", "KWrite": all three arrive, and the log never shows `Paste: Ctrl+V (class='io.github.bugaevc.wl-clipboard')`
- [ ] Paste Last Dictation: dictate, click into another window, press Ctrl+Alt+P. The same text is typed there once the keys are let go, with no stray Ctrl+Alt shortcut (log: "Paste last dictation"). Preferences → General → More shortcuts → Paste Last Dictation: None turns it off
- [ ] Swiss German spelling: on an English system, Preferences → Advanced → Fine-tuning has no "Swiss German spelling". Pick German in General → Change model, device or language → Language, OK, open Preferences again: Fine-tuning has "Swiss German spelling (ß → ss)", off. Tick it, Apply, and dictate German with Parakeet ("die Straße ist groß"): it types "strasse" and "gross". Untick it: ß comes back
- [ ] Numbers as digits: Preferences → Advanced → Text has "Numbers: As the speech model writes them". Pick "As digits (3 cats, 25 chickens)", Apply, and dictate on Parakeet "I have three cats and twenty five chickens, meet me at seven thirty on october fifteenth": it types "3 cats", "25 chickens", "7:30", "15th". Pick "Digits from 10 up": "three cats" comes back, 25 stays. "One of them" and "two thirds" stay words. Back to "As the speech model writes them": everything as Parakeet wrote it
- [ ] Letters with numbers: with "Digits from 10 up", dictate "let go of F eight, then go to B five A seven, a five minute walk": types "F8", "B5 A7" and "a five minute walk"
- [ ] Fix that word: in KWrite (window off to one side), dictate a sentence, then say only "fix that word": Fix a Word opens over KWrite showing just that dictation (no dropdown) (log: "Voice command: fix that word"). "Also fix it in my text" is ticked: pick a word, type the fix, Always fix this: the window closes and the word is fixed in KWrite, cursor back at the end (log: "Fix a Word: fixed it in the text"); the next dictation spells it right. The fix shows under its word in Preferences → Words; remove the test word afterwards there. Tray → Fix a Word reads "Also copy the whole corrected dictation, to paste over the old one". "We need to fix that word" is typed as text
- [ ] Plain text in terminals and code editors: Preferences → Advanced → Fine-tuning has it, ticked. In Konsole dictate "git status": types git status (no capital, no period). In KWrite the same words type "Git status." Untick it: Konsole gets "Git status." again
- [ ] Words tab: Preferences has a Words tab (no Snippets tab) with Your Words above Snippets. On first open, word fixes saved as snippets ("clawed claw" → ClaudeClaw) are listed under their word and the snippets list keeps only real snippets (email, address); custom_words.txt.before-words-tab is kept. Add Word: "ClaudeClaw", one line "clawed claw", Save: saying "clawed claw" types ClaudeClaw. Edit (or double-click) changes it; a phrase a snippet already uses, or "the", is refused with a message. Remove asks first and takes its fixes too. Fix a Word from the tray adds to the open list without closing Preferences. A snippet phrase that already fixes a word is refused. --tab=snippets opens the Words tab
- [ ] Your Words: Preferences → Words → Add Word, "TuxTalk", Save. On Parakeet, saying "tux talk" types TuxTalk. On a Whisper model (e.g. small) the name comes out right more often (log: "Loaded your words" / "Reloaded your words"). Saying nothing with a Whisper model types nothing
- [ ] Several languages: Preferences → General → Change model, device or language. On a Whisper model the Language dropdown (Detect automatically, the 35 languages) ends with "Several languages…"; on Parakeet that entry isn't there. Pick it: the list of 35 opens; tick English and Spanish, OK: the dropdown reads "Several: English, Spanish…", a "Change list…" button appears beside it, and the line at the top of the section ends "· English, Spanish". Cancel leaves the dropdown as it was; one language ticked selects that language. On a Whisper model (e.g. small, CPU and Vulkan) English and Spanish dictations come out in their own language; dictating German comes out in English (log: "Language: Whisper heard de, which isn't on your list; using en"). Change list…, untick all: "Detect automatically" again
- [ ] Transcribe a File: Tray → Transcribe a File... (and the GNOME menu, right under Fix a Word) opens the window. Choose a File… shows the desktop's file dialog with "Audio and video"; pick an MP3 or a video: a progress bar, then the text in paragraphs and "Done: … of sound in …" (log: "Transcribed a file"). Dropping a file anywhere on the window works, the big text area included; an empty window says "Drop an audio or video file here". Show times adds [m:ss]. Copy All, Save as Text… and Save as Subtitles… (.srt next to the file) work, and the .srt plays in a video player. Dictating while a long file runs still works. Stop keeps what's done. Opening it again brings the open window to the front. Close it and open it again: the newest transcript is back with every button ("Transcribed Oct … It's kept under Earlier Transcripts"), so Save as Subtitles still works. Earlier Transcripts lists the last 10, newest first; picking one shows it; a file done again replaces its copy; Clear This List… asks, then empties it. Remove ums and repeats (beside Show times) takes out um, uh and doubled words, untick shows it as heard, and Copy, Save as Text and Save as Subtitles use what's shown. Closing with text not saved to a file asks Save as Text… / Don't Save / Cancel (after Copy All too). Closing while a file runs asks Keep Going / Stop and Close
- [ ] Tray menu (and the GNOME extension's, identical): Dictation on/off, Recent Dictations, Fix a Word, Transcribe a File; one greyed line "Parakeet on the graphics card (Vulkan)" (or "… on the processor"), Performance; Preferences … Check for Updates, Restart Service; Quit. No Text Injection Mode. Switch Device to CPU in Preferences: the line reads "Parakeet on the processor" in both menus
- [ ] Tray → Performance shows Recommended / Lightest / Battery saver, with the dot on the active one
- [ ] Preferences → General opens on Dictation Keys (Hold, Toggle, hands-free, Test Hotkeys, "Show more shortcuts" with the two keys beside it, opening Commands cheat sheet and Paste Last Dictation), then Speech Recognition: one bold line "Parakeet on your … · detects the language" and a "Change model, device or language" button that opens them below (no arrow anywhere). Changing the model, device or language there updates the bold line. Off the recommended setup, the row says "Recommended for this computer: …" with Use Recommended, which sets Model and Device (Vulkan: download and speed check first) and turns into "✓ You're on the recommended setup". "What works with what?" shows the models-by-device grid and a line about this computer
- [ ] Backup: Preferences → Updates & Backup fits without scrolling. Save Backup opens the desktop's file dialog, writes the .zip, and "Last saved" turns from amber "never" to the date. Change the hotkey, then Load Backup puts it back, restarts dictation (log) and closes Preferences. Earlier settings lists both "— your backup" files from the folder the backup was saved to and "— automatic" copies, newest first, and Restore undoes the load. A second backup the same day gets its own name (the time is in it). A non-backup file is turned away with a message. Backup folder → Change… opens a folder browser; after picking one, Save Backup and Load Backup open in it and Earlier settings lists its backups. Before any is chosen the row says "the folder you save your first backup to"; saving a backup into another folder makes that the backup folder, and the row shows it. Tray → Check for Updates still opens this tab
- [ ] Uninstall, in a VM only (on Ron's machine it would also take the hotkey helper and models his dev version uses): Preferences → Updates & Backup ends with "Uninstall TuxTalk…". It asks first, listing only what that computer has (the program, the hotkey helper, the downloads with their size, start at login and the menu entry, the GNOME extension) with "Also delete my settings, Your Words, snippets and stats" unticked; on a VM that had 0.15 there is also "Take me out of the 'input' group", ticked. Cancel changes nothing. Uninstall: one password prompt, "Uninstalling TuxTalk…", then "TuxTalk is uninstalled" (log: "Uninstalled TuxTalk"). After it: `systemctl status tuxtalk-hotkeys` says not found; no 70-tuxtalk or 99-talktype rule in /etc/udev/rules.d; ~/.local/share/TuxTalk holds only stats.json and recent-transcripts.json; the models in ~/.cache/huggingface/hub are kept (the question has "Also delete speech models in the shared download folder (other apps may use them)", unticked, with their size, only when there are some); no ~/AppImages/TuxTalk.AppImage, autostart or menu entry; ~/.config/tuxtalk is kept, and installing again starts first-run setup with the Words still there. Again with both boxes ticked: nothing of TuxTalk is left but saved backup files, and another app's models in the shared folder stay. On the .deb and .rpm the uninstall removes the package too (`dpkg -l tuxtalk` / `rpm -q tuxtalk`: not installed), and a plain `sudo apt remove tuxtalk` / `sudo dnf remove tuxtalk` removes the helper and rules while an upgrade keeps them. `tuxtalk --uninstall` with TuxTalk not running asks and does the same
- [ ] F8 and F9 leave no stray character in a terminal (KDE, GNOME, and X11 on the Mint VM: no "^[[19~"; log: "The X server now holds the hotkeys back")
- [ ] COSMIC (Pop!_OS VM): in a terminal "git status" is typed plain, with no "^[[19~" (log: "COSMIC now holds the hotkeys back from the focused app"). While dictation is on, Settings → Keyboard → Custom shortcuts lists TuxTalk's entries for F8 and F9; turning dictation off or quitting takes them out. With "Send each dictation automatically" on, a dictation in a chat box is typed and not sent: "Not sent: TuxTalk can't tell which window has the focus. Press Enter when you're ready." floats over it, the first time with a notification too, and a space follows the words, so the next dictation doesn't start with one (log: "Not sent: which window has the focus isn't known"), with no countdown; "see you at noon, press enter" still presses Enter (log: 'Pressed Enter (said "press enter")'). At 1280x800 every window (About, Setup Complete, Preferences, Help...) opens with its buttons above the dock
- [ ] Dropdowns on X11 (MintCinnamon VM): in Preferences each dropdown opens as a list right under its button, not over it (since 0.17's fix; 0.14.3 to 0.17 used the Wayland path there). General → Change model, device or language → Language: the long list scrolls, and picking Spanish (Español) works. The Audio and Advanced tab dropdowns too
- [ ] Hotkeys: the dropdowns label F1, F5, F11 and F12 with what apps use them for. Change the hold key and click Apply: "Try your new hotkey?" replaces "Settings applied", and Test Now opens the hotkey test. On KDE, pick a key KDE already uses and the question says so (log: "KDE already uses …"). Give an app a launch shortcut in System Settings (Emoji Selector on F6), make F6 the hold key: the question names the app (log: "KDE opens Emoji Selector with F6")
- [ ] Keys without Fn (0.19): Preferences → General → Hold Hotkey and Toggle Hotkey list "Right Ctrl (stops working as Ctrl)", "Right Alt (stops working as Alt)" and "Scroll Lock" after F12; on a German layout Right Alt reads "Right Alt (your AltGr key: stops typing @ and €)", in the first-run hotkey test too after Change Key, Right Alt. A VM still on helper 1.4: picking Right Ctrl, Apply says it "needs the hotkey helper this TuxTalk brings", the hotkey test doesn't call it Working, and System check offers the helper update (1.4 → 1.6, and 1.5 → 1.6 on a VM that had a test build); after it, `journalctl -u tuxtalk-hotkeys` says "version 1.6". Hold Right Ctrl, talk, let go: it types (log: "Hold key: Right Ctrl"); a Ctrl+C with Right Ctrl doesn't copy. Right Alt as the hold key, in Firefox on a US layout: the text lands in the page and the menu bar never opens. Right Alt as the toggle key: a tap starts, the next tap stops, and the text is typed with no Ctrl or Alt shortcut fired, also when the stopping tap is held a second; double-tap for hands-free works. Right Ctrl as the toggle key while a send counts down: the tap holds the send. Type at pause (Labs) on, Right Ctrl as the hold key: hold it, say a sentence, pause, say another: the first is typed at the pause while the key is still held, with no Ctrl shortcut. Dictate a long paragraph with Right Ctrl as the hold key and press it again while the text is still being typed: the rest waits until it's let go, and no Ctrl shortcut fires (what's said while it's held isn't recorded: a start and a cancel beep after, until typing moves off the hotkey loop, CODE_HEALTH.md task 16). Held over 30 seconds, typing stops instead, with the "saved in Recent Dictations" notice (log: "Right Ctrl has been down for 30 s"). Scroll Lock as the hold key in a terminal: no stray characters on KDE, GNOME (extension), COSMIC (Settings → Custom shortcuts lists it while dictation is on) and the Mint X11 VM. Test Hotkeys shows "✓ Working!" for each and closing it keeps the key. Once in a VM without the helper (input group) too. Put F8 and F9 back afterwards

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
updater downloads it to verify the AppImage before installing — without it,
updates still work but skip the integrity check.

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

---

## PARAKEET ULTRA — HOSTED AS ITS OWN PRE-RELEASE

Parakeet's files (`src/tuxtalk/parakeet_ultra.py`) come from the GitHub
pre-release `parakeet-ultra-1`: the processor's int8 ONNX files (Olicorne's
export, unchanged) and the graphics card's q8_0 file (converted with
whisper.cpp's own script). Released versions check every file's SHA256, so a
published file is never replaced: a new conversion gets a new tag and new
hashes in `parakeet_ultra.py`. Upload with `gh release create ... --prerelease`
(a normal release would become "latest" for the updater), and only with Ron's OK:
it is public. How the files were made and checked: the release's README and
Obsidian "TalkType/2026-10-19 - Parakeet-ultra bake-off".

---

## TYPING — TUXTALK'S OWN VIRTUAL KEYBOARD

Since 0.18 every key TuxTalk presses (typed text, Enter, Ctrl+V, backspaces,
select and move) goes through one uinput keyboard the dictation service makes
when it starts (`src/tuxtalk/virtual_keyboard.py`). There is no ydotool or
ydotoold any more; `keyboard_layout.py` works out the keys for the active
layout, US included. The keyboard is shaped like a keyboard and a pointer on
purpose: TuxTalk's hotkey reader and the installed hotkey helper skip pointers,
so neither grabs or listens to it. Access to /dev/uinput comes from the hotkey
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

## VULKAN GRAPHICS ENGINE — BUILT AND HOSTED SEPARATELY

The "Vulkan (any GPU)" device (config value `vulkan`; AMD, Intel and NVIDIA)
runs whisper.cpp's whisper-server with its Vulkan backend (`src/tuxtalk/whisper_vulkan.py`). whisper.cpp publishes no
Linux Vulkan build, so TuxTalk builds one. It is NOT part of the AppImage
and is NOT rebuilt for each release. It only changes when whisper.cpp does,
or when its contents change (a new build number for the same version).

To update it:
1. `./build-vulkan-engine.sh <whisper.cpp version> <build number>`, e.g.
   `./build-vulkan-engine.sh 1.9.4 3`. Both are required. The build number
   must be higher than `ENGINE_BUILD` for the same version: a published
   build is never replaced (released versions check its SHA256), and the
   script refuses one that is. It builds in Ubuntu 22.04 and writes
   `talktype-whisper-vulkan-<ver>-b<build>-x64.tar.gz` + `.sha256`.
2. Upload it to a GitHub release tagged `whisper-vulkan-<ver>-b<build>`, marked
   **pre-release** (`gh release create ... --prerelease`). A normal release
   would become "latest", and the in-app updater and the AppImage's update
   information would treat it as a TuxTalk version.
3. Put the version, build number and SHA256 into `ENGINE_VERSION`,
   `ENGINE_BUILD` and `ENGINE_SHA256` in `whisper_vulkan.py` (and
   `ENGINE_SIZE_TEXT` if the download size changed).
4. Ask Ron before uploading. It is public.

The setup dialogs (download, speed check, Light/Full choice) live in
`vulkan_setup_dialogs.py`, shared by Preferences and the tray's presets.

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
- **Source code**: `/home/ron/Projects/TalkType/src/tuxtalk/`

### Autostart Configuration:
```
Exec=env DEV_MODE=1 PYTHONPATH=/home/ron/Projects/TalkType/src:/usr/lib64/python3.14/site-packages:/usr/lib/python3.14/site-packages /home/ron/Projects/TalkType/.venv/bin/python -m tuxtalk.tray
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

## SAYING HOW TUXTALK IS MADE — EVERY PUBLIC POST

Ron can't code; Claude writes TuxTalk and Ron designs, tests and uses it
daily. **Every** post, pitch, forum thread or listing says so up front, in
plain words, never buried and never left for someone to discover in the
commit history (the Lemmy thread on 2026-10-16 went bad for exactly that).
Pair it with the proof that a person is responsible: thousands of automated
tests and CI, testing on several distros in VMs, the hotkey helper's review by
an outside developer (guelz), guelz's PR merged, Ron dictating nearly everything he writes with it.
**Always** say, too, that the code is held to the standard a human developer
would respect: readable, tested, no leftovers, not an AI mess (Ron, 2026-10-20:
"I'd like you to always remind or say that"). Link the About page's "Keeping
the code clean" section (tuxtalk.app/about.html) where a link fits.
Never apologise for it and never call it "vibe coded". Draft in Ron's voice
(ron-voice skill). Don't post where AI-written code is banned (Flathub,
extensions.gnome.org, GNOME Circle, Gentoo).
