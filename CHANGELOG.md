# Changelog

All notable changes to TuxTalk are documented here. It was called TalkType
before 0.15.0, so older entries use that name.

## [Unreleased]

- With some microphones, hands-free never stopped by itself with a clothes
  dryer running nearby. Those microphones send the sound in very small
  pieces, and TuxTalk judged each piece on its own. With others, which send
  bigger pieces, it stopped in the middle of a sentence more often. It now
  listens the same way whatever the microphone sends. The flip side: with
  the microphones that send small pieces, in a loud room hands-free can now
  stop during a long pause, as it always has with other microphones.
- With Type at pause, a short word said after a pause, like "yes", was
  lost if you stayed quiet for a moment before letting go of the key (or
  before hands-free stopped). TuxTalk took it for a cough and threw it away.
  It's typed now, though in a noisy room it can still be missed now and
  then. The last word after a pause could also be lost when you let go of
  the key just as you said it. The flip side: a cough or a creaking chair
  after the last pause now reaches the speech engine too, and once in a
  while it types a "Yeah." for it.
- When the sound system stopped answering and a microphone stream couldn't
  be closed, the sound it kept sending also went into your next dictation,
  which could garble its start. Each dictation only takes its own
  microphone's sound now.
- Hands-free stopping one dictation could also stop the next one, if you
  started it right away, and what you said in it was lost.
- With Type at pause, pressing Esc while the AI was still fixing a "no wait"
  in a phrase didn't stop that phrase: it was typed anyway. Now nothing more
  is typed once you press Esc.
- "Edit that" said right after a Type at pause dictation changes all of it
  since its last Enter or Tab, the same text "fix that word" works on,
  instead of only its last phrase. If you tapped to hold an automatic send,
  it still changes only the last phrase. If you've typed or moved the cursor
  since, it leaves the text alone and says so.
- The CUDA download for NVIDIA cards is 580 MB now instead of 1.4 GB. It
  included NVIDIA's cuDNN, which the speech engine hasn't needed since 0.16,
  and dictation on CUDA types exactly the same without it. If you downloaded
  CUDA before, TuxTalk deletes that unused part when it starts, which gives
  you back nearly 1 GB.
- On desktops that still run PulseAudio rather than PipeWire (Mint 21,
  Ubuntu 22.04), System Default records from the microphone PulseAudio is set
  to. Before, with a USB headset plugged in, TuxTalk took the headset even
  when you'd picked the built-in mic, and recorded silence or the wrong room.
- Downloading CUDA again over a copy that already works can't break it any
  more. The new files are unpacked to one side and only swapped in once
  every one of them is there, so running out of disk space partway, or
  TuxTalk being closed in the middle, leaves the CUDA you had.
- With the key remapper kanata running, F8 and F9 did nothing, and the
  hotkey test didn't see them either. kanata takes your keyboards for itself
  and passes every key on through a virtual keyboard of its own, which is a
  mouse as well, and TuxTalk skipped it as a mouse. It listens to it now.
  TuxTalk never holds that keyboard back from your apps, since your mouse
  and the text TuxTalk types come through it too, so on some desktops a
  terminal may show a stray character for F8 while kanata runs.
  Thanks to Viktor for tracking it down (issue #23).
- You can pick Right Ctrl, Right Alt or Scroll Lock as a dictation key now,
  in Preferences, General, next to F1 to F12. On a lot of laptops and small
  keyboards the F-keys only work with Fn held, and these three never need it.
  Thanks to Tony for asking, on the Mint forum and in Discussion #18.
- Pick Right Ctrl or Right Alt and it's TuxTalk's key, so it doesn't work as
  Ctrl or Alt any more. Where your keyboard layout uses Right Alt as AltGr
  (German, French and most of Europe) the list says which characters you'd
  lose, like @ and €. TuxTalk hides the key from your apps the moment it goes
  down, so nothing it types while you hold it turns into a Ctrl shortcut.
- TuxTalk reads your desktop's keyboard options now, not just the layout.
  If you've set Right Alt to be AltGr on a US keyboard, or Compose or plain
  Alt on a German one, the Right Alt choice in the hotkey list says so
  correctly, and characters like @ on a German keyboard with Right Alt as
  Compose are typed right instead of coming out as a Compose sequence.
- Pause isn't on the list. Most keyboards send it as let go the moment it's
  pressed, so you can't hold it.
- The hotkey helper is version 1.10, because it has to report Right Ctrl and
  Right Alt on their own now and hear keys through a key remapper (1.10 also
  tidies its code after a review, and
  limits how much another program can make it read, which you won't notice).
  TuxTalk asks about the update by itself a few seconds after it starts, and
  keeps asking each start until it's done. It takes your password once and no
  restart. Until then Right Ctrl and Right
  Alt do nothing, and if you pick one in Preferences it says so and offers
  the update right there.
- With "Send each dictation automatically" on, a dictation that ends on "and"
  or "the" isn't sent, because you probably let go of the key mid-sentence.
  It used to hold back in silence, so it looked like sending had stopped
  working. Now it says so over the window: Not sent: it ended on “and”.
  Press Enter when you're ready. And "with" and "of" are off that list: "something
  to play around with" is a whole sentence, and it gets sent.
- "Fix that word" fixes the word in your text only if the cursor is still
  where the dictation ended. If you'd clicked somewhere else or used the
  arrow keys first, it used to replace whatever words were at that spot.
  Now it saves the fix to Your Words and leaves your text alone. In a
  terminal it never changes the text, since it can't select words there.
- Quitting TuxTalk, Restart Service, an update or an uninstall now lets a
  dictation that's still being transcribed or typed finish first. It used
  to throw it away. A recording that's still going gets stopped and typed,
  same as letting go of the key. It gets up to 20 seconds, and if it takes
  longer than that it's lost and a notification tells you. Music that was
  paused for it plays again and other sounds come back up, unless a player
  or the sound server doesn't answer in time. If TuxTalk gets cut off before
  it can turn the other sounds back up, it tries again the next time it
  starts.
- Quit takes the icon away right away and TuxTalk finishes up in the
  background. Open it again in that time and it starts once the old one is
  done, instead of telling you it's already running.
- After an update the new version started about 10 seconds after you
  clicked Restart, whether the old one had quit or not. If the old one was
  still typing out a long dictation, you ended up with nothing running. The
  new one waits for the old one now.
- With no tray running, turning dictation off from the GNOME extension
  didn't stop it. It does now.
- If you turned Start at login off while first-run setup was still copying
  the AppImage, and the copy failed, TuxTalk turned it back on. Now it
  stays off.
- On a desktop started without a login manager, two copies of TuxTalk
  could run at once and type everything twice. Now they share a lock in
  /tmp, so only one runs, unless /tmp itself can't be used.
- Uninstalling with Preferences open asks about changes you haven't applied
  yet, the same as closing the window does. They used to just disappear.
- If the first-run copy into ~/AppImages failed and the AppImage you ran
  had a space in its name, like "TuxTalk (1).AppImage", the menu entry
  didn't open anything. It does now.
- The links in About's "What's New in This Version", and in the Update
  Available window, work now. They showed as their raw text ("[vote on the
  AUR page](https://...)") and couldn't be clicked. Click one, or move to it
  with the arrow keys and press Enter, and it opens in your browser.
- Saying "tab" in a sentence ("every tab shows", "the tabs in Claude
  Desktop") typed a tab character instead of the word, almost every time.
  Now only "press tab" makes a tab, and "tab" on its own is just the word.
  Between form fields, say "name press tab address". "literal press tab"
  types the words.
- At the end of a dictation "press tab" presses the real Tab key now, so you
  can fill in a form one box at a time. "John, press tab" puts John in the
  box with no period and goes to the next one. Before, it typed nothing at
  all there, and a tab character pasted into a web form wouldn't have moved
  you anyway. "Send each dictation automatically" doesn't send a box you
  left with "press tab".
- With "Send each dictation automatically" and Type at pause, a recording
  that ended in a pause after you'd clicked into another window could press
  Enter in that window. The Enter only goes where the text went now. On KDE
  and GNOME that tells two windows of one app apart too, so two maximized
  browser windows aren't mixed up, and moving a window while a send counts
  down doesn't stop it any more. On GNOME that needs extension version 28,
  which is loaded once you log out and back in after the update; until then
  TuxTalk knows only which app has the focus there, as on COSMIC, and
  doesn't send automatically.
- Sending automatically now checks where the Enter would go one last time,
  right before it goes, whatever it waited for first. Click into another
  window while a long dictation is still going in and the text stays where it
  is, but nothing gets sent. If TuxTalk was typing something else during
  that last check, it doesn't send either, and it tells you. A tap of your
  hotkey holds a "Right away" send now too; before, it often came too late.
  On KDE or GNOME under X11, a busy tray could make TuxTalk think you'd
  switched windows when you hadn't. That's fixed.
- "See you at noon, press enter" goes by the same rule. If you click
  somewhere else while the words go in, or right after, the Enter isn't
  pressed and you're told why. "Press tab" too. Saying only "press enter"
  still presses it wherever you are, so you can still log in after typing a
  password yourself. With Type at pause, once phrases of the same recording
  went into one window, "press enter" said on its own in another window is
  pressed only in a password field: anywhere else it would send whatever is
  in that box. That holds after "edit that" too, which used to forget where
  the message was.
- "Edit that" that couldn't change your text (the AI had no answer, or the
  text couldn't be selected) left the message to be sent automatically as it
  was, the part you wanted changed included. Now it waits for you to press
  Enter.
- An automatic Enter never goes into a TuxTalk window now, and neither does
  "press enter" said after words that went somewhere else. If Preferences or
  the Parakeet offer came up during the countdown, the Enter could press its
  OK button while your message sat there unsent. Dictating into TuxTalk's
  own boxes still works: "ClaudeClaw, press tab" in Add Word moves on.
- Where TuxTalk can't tell which window has the focus, "Send each dictation
  automatically" types your words and doesn't send them. You get "Not sent:
  TuxTalk can't tell which window has the focus. Press Enter when you're
  ready." over the window each time, and as a notification once. That's
  COSMIC, GNOME without TuxTalk's extension or with the old one until you
  log out and back in after the update, and Plasma 5.27 on Wayland. A
  message sent into the wrong chat can't be taken back. Saying "..., press
  enter" still presses it there, because you asked for it and you're
  looking at the window. The space after your words still goes in; before,
  the next dictation stuck to the last word ("noon.How"). A tray too busy to answer
  for a moment no longer calls off a countdown; the last check before the
  Enter decides.
- When a send doesn't go, you get a notification as well as the note over
  the window, so a screen reader tells you. Holding a send with a tap of
  your hotkey says so in a notification too, and so does a hotkey press that
  held one when the microphone then couldn't open. And if a phrase in Type at
  pause only got partly typed, that message isn't sent, not at the end and
  not with "press enter", even when the phrases after it go in whole.
- In Claude Desktop a tab said in the middle of a dictation is four spaces
  with Keystroke Typing too. It pressed the real Tab, which jumps to the Send
  button. Saying only "press tab" in a password box there presses the real
  Tab.
- On some computers the beeps got cut short, and when you double-tap F9 for
  hands-free you often heard neither the start beep nor the short one after
  it (#21). TuxTalk closed each beep while the end of it was still waiting in
  the sound system, so the slower the sound output, the more of the beep was
  lost. Now each beep plays to the end, also on a slow output (tested on
  PipeWire, and through PulseAudio's plugin to PipeWire), and the beeps play
  on their own, so a sound system that stops answering can't hold up the
  typing, or your hotkeys for more than a second. The double-tap is timed
  from when you hear the end of the start beep. The microphone test in
  Preferences, Audio, plays your recording back to the end too, and Replay
  waits until it's done.
- If your microphone picks up the start beep and it got there late, on a
  slow sound output or behind another sound, a double-tap with nothing said
  could take the beep for a word, and hands-free transcribed it instead of
  dropping the recording. TuxTalk now tells the beep from a word by its
  pitch, wherever in the recording it lands, even when it's loud enough to
  distort the microphone. A word you say before the second tap still counts,
  said softly too. With the beeps turned off nothing is filtered. In a room
  as loud as talking, say a fan right next to the microphone, the room
  itself still counts as a word, beeps on or off. The beeps
  fade in and out over a few thousandths of a second now, so they don't
  click.
- On a desktop where TuxTalk can't make its own keyboard and types with
  wtype instead, your dictation was on wtype's command line for a moment,
  where anyone else logged in to the computer could read it. It goes to
  wtype privately now, and every letter arrives on a system set to a plain
  C locale too. The key that keeps other people's programs off the AI
  self-corrections engine was on its command line too, and is now hidden
  the same way.
- Hands-free often stops by itself now with a very steady machine running,
  like a clothes dryer or a gusty fan. Before, each thump or gust counted as
  you talking and started the wait over, so it never stopped. Steady noise is learned as the room once it
  has gone on for about three seconds after you stop talking, and its
  thumps and gusts then just pause the wait. So it stops, a second or two
  later than your setting. In a quiet room nothing changes, and neither does
  it in a real kitchen, café or street, which aren't that steady: there,
  with the noise close to your voice, tap the key when you're done.
- With a loud dryer and pauses almost as long as your setting, it can stop
  at the soft start of your next sentence, the way a loud fan already
  could. A longer setting, or the mic closer, helps.
- Now and then, in a steady fan's noise close to your voice with a 4 second
  wait, hands-free that used to stop by itself waits for your tap instead.
- The microphone test in Preferences, Audio, tells you when your room is
  noisy for your voice, under the level grade: a headset, or the mic closer
  to your mouth, will help. It measures how far your voice stands above the
  room's usual noise in the recording you just made, so a kitchen, a cafe or
  traffic counts as noisy even when it goes quiet now and then. Say a
  sentence or more: from a single quick word it can't tell.
- Preferences opens again when a number in the settings file was edited by
  hand into something that isn't one, or into nan or inf. It shows that
  setting's default, which is what TuxTalk uses for it.
- Updating from Preferences saves your snippets first too, and doesn't start
  if your unsaved changes couldn't be saved (a full disk, say). Before, the
  update's restart lost them.
- Help looks like Preferences now: the same dark window, the same title,
  and its sections as the same outlined tabs along the top. Before, the two
  windows looked like they came from different apps. Left and Right move
  between the tabs, and Tab takes you into the text, where Page Up and Page
  Down scroll it. On a small screen the tabs scroll instead of running off
  the edge, and the feedback link at the bottom is readable now (it was dark
  blue on dark grey).
- Help has a new Tips section with the practical stuff in one place:
  getting good results (the mic close or a headset matters most, and noise
  filters on the microphone make dictation worse), names and unusual words,
  keys that are easier on the hands, and everyday shortcuts like "press
  enter" and typing your last dictation again. It names your own keys.
- Help's Problems tab opens with a "Report a Problem…" button. It puts together
  what I'd ask for anyway: your TuxTalk version, distro, desktop, keyboard
  layout, graphics card, the settings that matter and the end of the log,
  with your username taken out and no dictated words. You see all of it
  first, then copy it, save it to a file, or open a GitHub issue with it
  filled in. Nothing is sent anywhere unless you do that yourself.
- Preferences' tabs each have a faint outline now. On Breeze dark the tabs
  you weren't on were just words in a row, and it was hard to see they were
  tabs at all.
- With Type at pause and "Send each dictation automatically" both on, a
  recording that ended in a pause right after "and" ("meet me at noon and")
  got a period and was sent, half finished. Now it's held back with no
  period, same as without Type at pause, and what you say next carries on
  in lowercase.
- When a send was called off because you clicked into another window, the
  space after your words could end up in front of the next dictation in
  that other window. That space only goes back into the window it belongs
  to now.
- TuxTalk won't type into a password field, but it asks the accessibility
  bus to find one and only waits a quarter of a second. If a few apps on
  that bus had stopped answering, the time ran out and the dictation was
  typed anyway, even when the password field was in the first app it
  asked. Now it checks each app the moment it finds it.
- Voice editing by voice (TuxTalk Labs): saying "edit that", pausing, then
  clicking into another app before you said what to change selected text in
  that other app and rewrote it. Ctrl+Z got it back, but it shouldn't have
  happened. Now nothing is changed and it says you switched windows.
- Undo ("delete that", "delete last word"), select and move by voice, "edit
  that" and "fix that word" only work on what you just dictated, in the
  window you dictated it into. But they only checked it was the same app,
  so with two KWrite documents or two browser windows, undo in the other
  one deleted its text. On KDE and GNOME, and wherever else the desktop
  says which window it is, they tell the two windows apart now.
- In a terminal, if pasting a dictation with line breaks failed, TuxTalk
  typed it instead, with spaces for the line breaks. Undo still counted the
  dictation as it was said, so it could be a few characters longer than what
  was on screen, and "undo that" deleted some of your own typing too. It
  counts what was typed now.
- With Type at pause, pressing Esc stops the recording, but a phrase that
  had already been cut at a pause still got typed if it was waiting its
  turn or being transcribed. Esc drops those now. Phrases already typed
  stay where they are.
- Power Saving lets go of the speech model after a while without
  dictation, and loads it again when you next press the key. It loaded the
  model named in your settings, which isn't always the one that was running:
  if your model wouldn't load at startup and TuxTalk used another one, it
  tried the broken one again after every rest. Now it loads the one that
  was running. And if it fails, you get one notice for that dictation, not
  two.
- Transcribe a File stopped at the first damaged spot in a recording and
  still said Done, so the rest of the file was just missing from the
  transcript. Now it skips the bit that won't play and carries on.
- Downloads from Preferences and the first-run window (speech models, the
  graphics card engine's models, the AI model) gave up on the first network
  hiccup, so one timeout partway through a 3 GB download meant starting
  over. Each file is tried three times now, like the downloads at startup.
- If a model's files on disk were damaged, TuxTalk said it would download it
  again, then found the same files already there and fetched nothing, so it
  failed the same way. It really downloads them again now.
- Installed from the .deb or .rpm, Preferences' Download & Install
  restarted TuxTalk after the update without saving what you'd changed in
  Preferences, so those settings and any new snippets were lost. It saves
  them first now, as the AppImage update always did.
- In KWrite and Kate, Shift+Enter is "smart new line", which copies the
  symbols at the start of the line above: "$45", new line, "$25" came out
  as "$$25". Pasting already got around it, but with Keystroke Typing (or
  when a paste failed and TuxTalk typed instead) it still happened. Typed
  line breaks there are a pasted newline now, so they go through the
  clipboard, even with Keystroke Typing.
- With "Turn everything down" picked for other sounds, if putting an app's
  volume back failed even once when you finished dictating, TuxTalk forgot
  about it, and the app stayed at half volume (your desktop remembers each
  app's volume, so it stayed that way). It keeps trying now, the next time
  it looks and the next time TuxTalk starts.
- On Budgie and GNOME Flashback, TuxTalk thought it was on GNOME, because
  those desktops say so too. It offered the GNOME Shell extension, which
  can't run there, and on X11 it left F8 and F9 to an extension that
  wasn't there, so they typed junk like "^[[19~" into terminals. It tells
  them apart now.
- First-run setup copies the AppImage into ~/AppImages. If that copy
  failed (a full disk, say), TuxTalk in your app menu still pointed at the
  copy that never arrived and opened nothing. It points at the AppImage
  you ran instead now, as starting at login already did.
- Transcribe a File on Parakeet now leans toward Your Words while it
  listens, as dictation does. Before, it only fixed their spelling
  afterwards, so names came out wrong more often in a file than when you
  dictated them.
- Select and move by voice (TuxTalk Labs): after "..., press enter", a
  move like "select the last word" could count on where the cursor was in
  an older dictation and select the wrong text. Once Enter or Tab is
  pressed it knows there's nothing fresh to work on and says so.
- Preferences, System check: if you cancelled the password prompt for
  "Switch to Hotkey Helper" (or "Update Hotkey Helper"), the button came
  back as "Fix Typing Permissions", and clicking it ran the older fix,
  which leaves you in the input group. The button comes back as it was now.
- Saying "soft break" (or "soft line") is meant to type three spaces, as
  Help and Voice Commands say, but it typed one. It types three now.
- The recording indicator's "Animation response" slider did nothing for
  the orb, which is the style you get unless you pick another, or for the
  bars. It works for every style now. And when you changed the indicator's
  size, the orb kept its old size inside the resized window until TuxTalk
  restarted.
- Installing an AppImage update from the tray froze the "Installing
  Update..." window and the tray menu while the new version was copied
  into place. The copy runs in the background now.
- If Parakeet's helper on the graphics card froze halfway through an answer,
  dictation hung until you restarted TuxTalk. Now it gives up after about two
  minutes, and Parakeet runs on the processor until TuxTalk restarts.
- "He said quote hello quote" came out as He said " hello ". with spaces
  inside the quotes. It's He said “hello.” now. And "open quote ... close
  quote" uses smart quotes like everything else and gets a capital when it
  starts a sentence.
- If your home folder has a space in its name, TuxTalk never started at
  login, and the shortcut command in Help didn't work either. Both are fixed,
  and a login entry that's already broken gets fixed the next time the
  AppImage starts.
- In a terminal or a code editor, spoken quotes are always straight quotes
  now, so "git commit -m open quote fix the bug close quote" works.
- With "Restore clipboard" on, something you copied between two quick
  dictations got replaced by what you'd copied before them. It's kept now.
- On X11 desktops (Mint's Cinnamon, Xfce, MATE), "Put back what I'd copied
  after pasting a dictation" works too now. TuxTalk only pastes there for
  what your keyboard layout can't type, like an emoji, and the dictation
  used to stay on the clipboard instead of what you'd copied. TuxTalk holds
  on to what it put back, so when it quits or restarts, copied text goes to
  your clipboard manager if you have one, but a copied password, picture or
  files are gone from the clipboard.
- "Unindent" took every bit of indentation off. It takes off one level now,
  the same step "indent this" adds.
- A graphics engine download that failed to unpack left its 22 MB file
  behind. It cleans up after itself now, and two downloads of it at once
  don't trip over each other.
- Help, Problems has a new part for letters that come out wrong. Mostly
  that's Caps Lock being on, which TuxTalk can't see.
- The first item in the tray menu says what clicking it does now: "Turn
  Dictation Off" (with the checkmark, since it's on) or "Turn Dictation On".
  It used to read "Dictation on/off" with only the checkmark to tell you,
  which a screen reader couldn't say.
- On GNOME, the TuxTalk button in the top bar tells a screen reader whether
  dictation is on now: "TuxTalk, dictation on", "dictation off" or
  "recording". Before, it only ever said "TuxTalk", so you had to open the
  menu to find out.
- Without the hotkey helper, the hotkey test on the welcome screen now
  notices a keyboard you plug in while it's open, within a few seconds,
  even if no keyboard was there when it opened. It only read the keyboards
  that were there at the start, so on a new one a key your desktop keeps
  for itself could look like it didn't work. And if you unplug the keyboard
  it reads, it stops calling a key Working when only the window saw it.
- Picking large-v3 on an NVIDIA card and downloading CUDA with it now
  switches dictation over as soon as the downloads are done, the same as
  picking CUDA under Device always did. Before, it waited for you to click
  Apply or OK first.
- When the Parakeet, graphics engine or AI engine download fails, the
  download window says why now, like no internet connection or a download
  that got cut short, instead of just "Download failed".
- A Whisper model download that was cut off partway isn't refused for disk
  space any more when the rest of it fits.
- If the speech model has to be downloaded when TuxTalk starts, that window
  says "Loading model..." again once the files are in. It used to keep
  showing the last file's name for the seconds the model took to load.
- Help and the Voice Commands window list the same "literal" words now.
  Each had its own list, and they didn't quite agree.
- With "Send each dictation automatically" on, if you pressed your hold key
  to add more and the microphone wouldn't open, the message stayed unsent
  and the space after it was left for your next dictation, so a message you
  then sent yourself could start the next one with a space. Now TuxTalk says
  "Not sent" and puts the space in once you let go of the key. It says so
  too when the last dictation finished typing during your next recording
  and that one then added nothing (you pressed Esc, only tapped the key, or
  said nothing hands-free). Before, nothing told you the message wasn't sent.
- If you read TuxTalk's state over D-Bus in a script of your own: the
  IsRecording, IsServiceRunning, GetCurrentModel and GetDeviceType calls are
  gone. Nothing in TuxTalk used them any more. GetStatus still gives you all
  four, as recording, service_running, model and device. SetModel is gone
  too: change the model in Preferences, or in config.toml followed by
  RestartService.
- If you start your desktop with startx, TuxTalk now types for the keyboard
  layout you have switched on. It didn't recognise that kind of X11 session
  before, so it fell back to the system's default layout.
- If TuxTalk's tray icon crashed and you opened TuxTalk again, the
  dictation that was still running couldn't reach the new one: the GNOME
  panel stopped showing when you were recording, Test Hotkeys never heard a
  key, tuxtalk --start never started, and on GNOME F8 could leak into the
  app you were typing in. That lasted until dictation restarted. Now it
  finds the new tray straight away.
- If you turned Dictation off, picking a Performance preset, applying a
  Preferences change that needs a restart, or the Parakeet Ultra download
  turned it back on. So did loading a settings backup and Fix Typing. Now
  it stays off, even while your last dictation is still being typed, and
  the change is used when you turn Dictation on. Restart Service in the
  menu still turns it on, since you asked for it.
- Clearing Recent Dictations at the same moment a dictation finished could
  bring the cleared ones back. Now one waits for the other, so cleared ones
  stay gone.
- TuxTalk's log could be read by other accounts on the same computer. That
  matters if you switched on "Log transcribed text", which puts what you
  dictate in it. Only you can read it now, an old log included.
- A wrong "ready" report reaching the tray while a tuxtalk --start waited
  for the speech model could make TuxTalk send a signal that ends one of
  your other programs. TuxTalk now checks it's its own dictation service
  first.
- If the desktop's notification service hung, a notice from TuxTalk could
  hold up dictation for 25 seconds. It gives up after 2 now, as intended.
- The tray menu no longer freezes for a moment on KDE when dictation stops.
- The KDE "turn off the notification" guide closes with Escape, Enter or
  Space now.
- tuxtalk --uninstall run while TuxTalk was quitting said nothing and did
  nothing, as if it had worked. It says it couldn't now. --open does the
  same.

## [0.18.2] - 2026-10-23

- Hands-free: on a computer where starting a recording takes a moment
  (finding the microphone, opening it and playing the start beep), the
  second tap of a double-tap was read only after all that, and by then it
  counted as too late, so the recording stopped instead. The double-tap is
  now timed from when the recording is ready. The log says how long each
  start took. Thanks to Tony for sticking with it (#21).

## [0.18.1] - 2026-10-23

- Hands-free: a double-tap of the toggle key only counted when the second
  tap came within 0.4 seconds, so at a relaxed pace the second tap stopped
  the recording instead (the light came on and went straight off). The taps
  can now be up to 0.7 seconds apart. Thanks to Tony for reporting it (#21).
- Hands-free: a word said between the two taps of a double-tap, with nothing
  after it, was thrown away as "nothing said" when the recording stopped
  itself. It's typed now.

## [0.18.0] - 2026-10-23

- Security: on systems where the hotkey helper can't run (no systemd), Fix
  Typing wrote its setup script to a temporary file and then ran that file as
  root once the password was typed. Another program running as you could
  have swapped the file while the password box was open. The script now goes
  straight to root's shell without touching the disk, as the hotkey helper's
  setup already did.
- Privacy: the first-run hotkey test, on systems without the hotkey helper,
  wrote every key pressed anywhere to the log while it was open, letters
  included. It now logs only the keys it tests (F1 to F24, Esc, Pause,
  Scroll Lock).
- Privacy: with Your Words steering Parakeet on the processor, the log named
  the word Parakeet wrote even with transcript logging off. It now only says
  that one of your words was used.
- On a French (AZERTY) keyboard, "clear the whole box" pressed Ctrl+Q, which
  quits most apps, because TuxTalk pressed Ctrl+A by key position. Paste,
  copy and select all now use the key that has that letter on your layout,
  so pasting works on Dvorak too.
- Privacy: when the keyboard layout had no key for a character (an emoji, say),
  the log named that character even with transcript logging off.
- A setting hand-edited in config.toml as "false" in quotes was read as on;
  for log_transcripts that meant every dictation was written to the log. The
  words true and false now count either way, and anything else is ignored.
- Preferences → Advanced → Clear log now said "Log cleared." even when the
  log couldn't be erased. It says so now when it couldn't.
- The Text Injection tooltip said TuxTalk can't detect password fields. It
  can, since this release.
- The .rpm now brings pactl (pulseaudio-utils on Fedora), which turning other
  sounds down needs; the .deb already did.
- Help and Preferences said the Vulkan graphics engine is a 24 MB download;
  it's 22 MB, and they now take the size from the engine itself.
- Transcribe a File: Save as Subtitles failed on a long sentence with only one
  space in it, as in Chinese or Japanese with an English word.
- Hands-free: when the microphone started with a moment of digital silence,
  as many do, TuxTalk took the room to be perfectly quiet for the rest of the
  recording. A fan or air conditioner then counted as speech, and the
  recording never stopped by itself.
- Updating the .deb or .rpm from inside TuxTalk: an unexpected error while
  installing left the "Updating TuxTalk" window on screen, and it couldn't be
  closed. It now closes and says what went wrong.
- Without the hotkey helper (the input group), the dictation service could
  stop at login if a device such as a wireless receiver disappeared while it
  was starting. Startup now opens keyboards the way a rescan does, skipping
  one that's gone, and no longer watches mice and power buttons.
- AI self-corrections and voice editing: if the AI engine ever crashed,
  TuxTalk started it again but the new one was stopped as soon as that
  dictation finished, so the AI stayed off until TuxTalk was restarted. It now
  comes back for good.
- AI self-corrections are stricter about what they let through: an answer
  that changes a number's punctuation ("2.5" to "2,5") or drops a "hmm" is
  now refused, and the dictation is typed as you said it.
- Licenses: Transcribe a File's audio library (PyAV) brings FFmpeg, x264,
  x265 and 36 other libraries, and TuxTalk shipped them without their license
  notices. Every one of their license files is now included, with each
  library's exact version and where its source is
  (usr/share/licenses/pyav/), and THIRD_PARTY_LICENSES.md describes them.
- Saying "new line" in KWrite, Kate or KDevelop no longer copies the start
  of the line above. TuxTalk pressed Shift+Enter between the lines, which
  those editors take as Insert Smart Newline: after a line starting with
  "$45", "new line, $25" came out as "$$25", and the same with "- ", "#" or
  "//". The line breaks now go in with the pasted text, as in a terminal.

- TuxTalk no longer types into password fields. Dictate with the cursor in
  a password box and nothing is typed: you hear the low beep and "TuxTalk
  doesn't type into password fields" shows over the window. The words aren't
  kept in Recent Dictations either, since they may be the password. The same
  goes for Paste Last Dictation and voice editing. Saying only "press enter"
  there still presses Enter, so you can log in after typing the password.
  TuxTalk asks the desktop's accessibility service, so it usually works in
  GTK apps (most GNOME, Cinnamon and Xfce apps), and in KDE apps and Firefox
  only while accessibility is on.
  Chrome, Brave and Electron apps (Discord, VS Code) only say so if
  accessibility was already on when they started. The desktop's own
  prompt for your admin password counts too, on GNOME, KDE, Cinnamon,
  COSMIC and Xfce, and on GNOME its keyring and Wi-Fi prompts as well.
- Typing is about six times faster. TuxTalk now types with its own virtual
  keyboard instead of ydotool: a 25-letter sentence that took 0.6 seconds to
  appear now takes about 0.1. Undo and select by voice are as quick. Paste
  stays the way most dictations arrive on Wayland.
- No more "TuxTalk's typing helper had stopped". The ydotool program and its
  ydotoold daemon are gone, so there's nothing to start or keep running, and
  first-run setup no longer asks to install ydotool. The ydotoold an older
  version started, and the service file it set up, are removed when this
  version starts.
- Typing works right after first-run setup on computers that hadn't
  loaded the kernel's uinput module yet. There setup's typing permission
  only took effect after a restart, and every dictation said TuxTalk had
  no permission to type until then. Thanks to sparcher on the Fedora forum
  for the log that showed it.
- If a slow computer drops letters, `key_delay` in config.toml slows the
  keyboard down again (6 or 12 are safe values; 2 is the new default).
- COSMIC (Pop!_OS 24.04 and later) works as the other desktops do. TuxTalk
  now knows which window you're in, so a terminal gets plain text and the
  paste shortcut that works there, and "Send each dictation automatically"
  sends nothing if you switch to another app during the wait. F8 and F9 no longer
  reach the app as well ("^[[19~" in a terminal): while dictation is on they
  are COSMIC shortcuts, listed as TuxTalk's in Settings → Keyboard → Custom
  shortcuts, and they go again when dictation stops. A key COSMIC or you
  already use for something else is left alone, and the log says so.
  Typing follows the keyboard layout set in COSMIC Settings rather than the
  one picked when the system was installed, which on a German keyboard
  swapped Y and Z.
- Emoji show on every computer. Without an emoji font installed, the hint
  markers in Preferences, Help's tab names and the first-run screens showed
  boxes with a code in them instead. TuxTalk now carries the 36 emoji it uses
  in a small font of its own (51 KB). Thanks to Tony for reporting it (#19).
- Notifications say what happened in their title, like "No sound from the
  microphone", with what to do underneath. They used to be titled "TuxTalk",
  which the desktop already shows, and the pop-up cut the message off.
- Uninstall no longer deletes the speech models in Hugging Face's shared
  download folder (~/.cache/huggingface/hub) unless you ask: other apps may
  use the same models. The question has a box for them, unticked, with their
  size, when there are any. Only the folders TuxTalk downloads into are
  removed from there.
- Uninstall and linked folders: if your TuxTalk settings or downloads folder
  is a link into another folder (a synced folder, a bigger disk), only
  TuxTalk's own files are removed from there. Before, everything in that
  folder went.
- Uninstall says what it couldn't remove. It used to finish with "TuxTalk
  is uninstalled" even when the hotkey helper, a file or the input group
  change hadn't gone, and asked you to log out to leave the input group when
  it hadn't managed to. If removing what needs the password runs past its
  time limit, the rest is still uninstalled and the message says it couldn't
  confirm the helper and package went; an error partway still ends with a
  message instead of a half-closed TuxTalk. GNOME's Alt+F7 and Alt+F8, if a
  hotkey test had left them cleared and they won't come back, are named and
  their backup kept, and so are KDE shortcut entries that stay. The question
  lists only what this computer has.
- Power Saving: if the speech model's files were deleted while it rested,
  the next dictation tried to open the download window from the wrong place
  and could crash the dictation service. It now says the model wouldn't
  load, and your choice of model stays as it was.
- On X11 desktops other than KDE and GNOME (Linux Mint Cinnamon, Xfce,
  MATE), the dictation keys no longer reach the app you're in as well.
  Dictating into a terminal typed "^[[19~" (what F8 sends) before the words.
  While dictation is on, the X server now keeps the keys for TuxTalk, as KDE
  and GNOME already did; if another program already uses one of them, the
  log says so.
- On a keyboard that isn't US QWERTY (German, French, Dvorak), a shortcut
  with a letter in it, like Ctrl+Alt+P for Paste Last Dictation, is now the
  key with that letter on it. TuxTalk used to listen to the key in the US
  position while the desktop held back the one with the letter, so the
  labelled key did nothing and the other one also reached the app.
- F8 or F9 pressed with Ctrl, Alt or Super held (Alt+F8 resizes a window
  on KDE and GNOME), or with Shift while voice editing is off, is left to
  the desktop and the app. It used to do that and start a dictation too.
- Every TuxTalk window leaves room for panels and docks. Wayland doesn't
  tell apps where those are, so on a 1280x800 screen Setup Complete and
  About were as tall as the screen and their buttons sat behind Pop!_OS's
  dock. Windows now open at most three quarters of the screen tall, and
  one whose content needs more scrolls it, with its buttons kept in view.
  With two monitors of different heights, a window opened from the tray
  fits the shorter one, so it fits whichever it opens on.
- On X11 desktops (Linux Mint Cinnamon and other X11 sessions), dropdowns
  open as a list under the button again. Since 0.14.3 a wrong check gave X11
  the workaround meant for Wayland.
- Download windows and notices call a model what the menus call it
  ("Whisper Small", not "Small").
- The "Parakeet got more accurate" offer pops up by itself, so Enter there
  now means Not Now. An Enter meant for another window no longer starts the
  670 MB download.
- The waveform, bars and radial indicators all draw the standard soft
  backing again. A backing picked before 0.16 (Off, Soft or Strong) had no
  control left to change it; that setting is gone.
- A dictation pasted into a terminal could come out as a lone ^V. The
  clipboard tool TuxTalk pastes with can open a tiny window of its own for a
  moment, and TuxTalk sometimes took it for the window you were in, so the
  terminal got an ordinary app's paste key instead of its own. "Sending in
  2 s" could be placed over that tiny window too.
- The welcome screen said "Hold F8" as if F8 were the only choice. It now
  says the key can be picked in the next step, and Setup Complete says where
  to change the keys later.

## [0.17.0] - 2026-10-20

- Parakeet is more accurate. TuxTalk now downloads Parakeet Ultra,
  Moondream's retrained version of NVIDIA's Parakeet: the same size and
  speed, with fewer mistakes. In our tests on 2,400 recordings in English,
  German, Spanish and Italian it made 8 to 21% fewer mistakes on the
  graphics card, and 20 to 30% fewer on the processor, where TuxTalk's old
  copy had lost some accuracy to compression. On clips of silence and noise
  it typed nothing, where the old one now and then typed "Thank you.", and
  it writes numbers as digits more often ("8:30", "12 feet"). If you already
  use Parakeet, TuxTalk offers the update once (a 670 MB download); your
  Parakeet keeps working until then, and if you say Not Now. After a Not
  Now, Preferences → General shows an Update Parakeet button until you take
  it.
- New: Uninstall TuxTalk, at the bottom of Preferences → Updates & Backup,
  or `tuxtalk --uninstall` when TuxTalk won't open. Removing the package or
  deleting the AppImage only ever took the program away. The hotkey helper
  kept running, start at login and the menu entry stayed, and the speech
  models, engines and NVIDIA libraries it downloaded sat in your home folder,
  a few GB of them. This removes all of it after one password prompt: the
  program (it removes the .deb, .rpm or AUR package for you), the hotkey
  helper and the typing permission, every download, the GNOME extension and
  KDE's shortcut entries, and the input group if an older version put you
  in it (it asks). Your settings, Your Words, snippets and stats stay unless
  you tick the box, and backups you saved are never touched.
- Removing the .deb or .rpm with apt or dnf now takes the hotkey helper and
  its rules with it. Updating keeps them.
- Fixed: with "Pause music and videos, turn the rest down", a video could
  come back almost silent after dictating (Brave closes its sound while
  paused and reopens it at the lowered level). It now plays at its own
  volume again.
- Numbers, "Digits from 10 up": Parakeet Ultra sometimes writes a small
  count as a digit itself, so "three cats" came out "3 cats". In English
  TuxTalk now writes those as words, but leaves "Python 3", "page 3",
  "3 GB", "3 PM" and "2 to 4" as they are.
- Turning other sounds down is gentler: they go to half their volume
  setting while you dictate, so a call or a video stays audible. A quarter
  of it, as before, sounded almost muted.
- Turning other sounds down now works on Zorin OS: the .deb brings the small
  pulseaudio-utils package it needs, which Zorin doesn't install.
- Fixed: with the GNOME extension, dictating into GNOME Terminal (Ubuntu
  24.04, Zorin, Debian) typed a stray ^V instead of your words, because
  TuxTalk didn't recognize it as a terminal. It now pastes there the way
  terminals need, in plain text.
- Fixed: a quick tap of F8 to hold a send could count as a dictation, so
  "Not sent. Press Enter when you're ready." didn't show.
- Fixed: with Send each dictation automatically on, a dictation you cut off
  on "and" in a text editor like KWrite still got a period ("Meet me at noon
  and."). It now stops without one there too, as it does in a chat box. And
  the next dictation carries that sentence on in lowercase only in the same
  window; elsewhere it starts with a capital.
- Power Saving no longer turns dictation off. After the idle time TuxTalk
  lets go of the speech model, which frees the processor and graphics card
  as before, but F8 keeps working: the next dictation loads the model again,
  a few seconds slower than usual. Before, F8 did nothing until you turned
  dictation back on from the menu, which looked like TuxTalk had stopped
  working. It's on by default on laptops.
- Help is sorted by what you're doing: Start Here, Chatting, Writing,
  Terminals, Hands-free, Mistakes, Problems, and the Commands list. Labs
  features are explained where you'd use them. It uses your own keys, so it
  never says F8 to someone who picked F6. Fixed what it got wrong: it said
  dictation always turns off after 5 minutes (from the Power Saving change
  above, it never does), and that fixing the keys needs a restart (with the
  hotkey helper it doesn't). It now covers turning other sounds down, the
  silent-microphone warning and the hotkey helper. The Voice Commands list
  keeps "delete everything" apart from the undo commands, since it clears
  text you typed yourself too, and adds the voice-editing commands.
- Shorter first run. The welcome screen says it all in three steps: hold
  F8, talk, let go. The "You're All Set!" screen is gone; it came before the
  speech model had downloaded, so it said ready when it wasn't. Its useful
  tips (tap F9, Ctrl+Alt+P for the wrong window, log out for the GNOME
  extension) are on the Setup Complete screen at the end, and its restart
  button now says "Restart computer now".
- Simpler Preferences, General tab. It now starts with your dictation keys.
  The model, device and language sit behind one line that says what you're
  running, like "Parakeet on your graphics card · detects the language", with
  Change model, device or language under it for anyone who wants to. Language
  is one dropdown now: Detect automatically, one language, or Several
  languages (that one is for the Whisper models). It used to be three
  controls. The cheat sheet and Paste Last Dictation shortcuts moved into
  More shortcuts. Your settings carry over as they were.
- Simpler Audio tab. "Pause music and videos" and "Turn other sounds down"
  are one question now, Other sound while you dictate, with four answers.
  The recording indicator is a tickbox and a Customize button; its style,
  size, color and position are in their own window. "Sensitivity" is called
  Animation response there, because it never changed what the microphone
  hears. The backing setting is gone (the default Orb never used it); a
  backing you already picked stays.
- Simpler Advanced tab. On top is what people actually turn on: Send each
  dictation automatically, Numbers (one dropdown now: as the speech model
  writes them, as digits, or digits from 10 up), removing "um" and the AI
  self-corrections. The options that already do the right thing by
  themselves, like smart quotes, the closing period, plain text in
  terminals, how text gets typed and the transcript log, are folded into
  Fine-tuning. The graphics card, typing setup and GNOME extension panels
  are one line, "System check: everything is set up", that opens by itself
  when something needs fixing. Swiss German spelling only shows up when
  German is your computer's language or one you dictate in (or you already
  ticked it).
- Saving a backup into a folder makes it the backup folder, so the next
  Save Backup and Load Backup open there. Backup folder → Change… still
  picks it outright.
- Shorter TuxTalk menu, the same in the tray and the GNOME panel. "Dictation
  Service" is now Dictation on/off. The model and device are one line, like
  "Parakeet on the graphics card (Vulkan)". Text Injection Mode left the
  menu (it's still in Preferences, Advanced, Fine-tuning), and Restart
  Service moved down next to Help, since it's for when something's wrong.
  GNOME extension version 25.
- Every folded part of Preferences is now a real button that says what it
  opens, like "Show fine-tuning", with a short note beside it of what's
  inside or how it's set ("✓ everything is set up", "not moved"). The small
  arrow they had was easy to miss. The welcome screen's "Change language,
  model or graphics card" works the same way.
- Type at pause no longer makes up a sentence for a very short phrase.
  Alone, under two seconds of audio, Parakeet could turn a single "it" into
  "It's a little bit more than a little bit." TuxTalk already hears each
  phrase again with the one before it to place the punctuation; for a phrase
  that short it now types the words it heard there when they're fewer.

## [0.16.0] - 2026-10-18

- New: TuxTalk no longer puts you in the `input` group. That group lets every
  program you run read every key you press, passwords included, and it's how
  TuxTalk, like a lot of Linux tools that read keys, heard its hotkey on
  Wayland. Now a small system service, the hotkey helper, is the only thing
  that reads the keyboard. It tells TuxTalk about F-keys, Esc and shortcuts
  with Ctrl, Alt or Super, nothing else, and only for whoever's at the screen.
  It also means no restart after setup. If you're already set up,
  Preferences, Advanced, Typing Setup has Switch to Hotkey Helper: one
  password, it takes you out of the group, and your hotkeys keep working. On
  a system without systemd it still uses the group, and setup now says so and
  why. It's been through three security reviews, with the attacks tried for
  real on a test machine. Thanks to guelz for pushing on this and for the
  third review (Discussion #12).
- New in Labs: Type at pause, from guelz (PR #13). It types each phrase when
  you pause instead of waiting until you're done. Put it together with
  hands-free (double-tap F9) and you can say a message, watch it come in, say
  "edit that, make it friendlier" if you want it changed, and say "press
  enter" to send it, without touching the keyboard.
- New: say "press enter" at the end of a dictation and TuxTalk types it and
  then presses a real Enter, so a chat message goes out or a terminal command
  runs. Saying only "press enter" just presses Enter. There's also Send each
  dictation automatically (off), which presses Enter after every dictation.
  It waits two seconds first (you can pick none to four) and says so on
  screen: tap F8 in that time and it isn't sent, so you can fix it or add a
  screenshot, or hold F8 to keep talking. It also doesn't send when you
  stopped on a word like "and" or "the", since that sentence was probably
  cut off, or in a text editor or word processor, where Enter would only
  start a new line.
- New: with voice editing on (Labs), start a dictation with "edit that" right
  after dictating something, like "edit that, make it shorter", and it
  rewrites what you just said. No Shift needed. "Edit that file tonight" is
  still typed as words.
- New: Your Words now work with Parakeet too, on the processor and on the
  graphics card, so names come out spelled your way more often. On the
  graphics card this comes with a new engine (whisper.cpp 1.9.5), a one-time
  22 MB download after you update.
- New: a Words tab in Preferences, where Snippets used to be. Your Words is
  the top list now, each name with what TuxTalk hears instead ("bamboo
  studio" for BambuStudio), so the fixes you make with Fix a Word sit with
  their word and not mixed in with your snippets. The first time you open it
  your old word fixes move up there by themselves, and a copy of your old
  list is kept.
- New: Turn other sounds down while dictating (Preferences, Audio, off). It's
  for what pausing can't reach, like a Discord call or a game. It only
  changes what you hear, so a call still hears your microphone.
- New: if the microphone sends nothing but silence (muted, unplugged, wrong
  input), TuxTalk tells you instead of typing nothing.
- New: on a desktop with no system tray, like plain GNOME without the
  extension, opening TuxTalk from your apps shows its menu in a small window.
- Changed: the timeout that turns dictation off after a few idle minutes is
  in Preferences, General now, under Power Saving, and it says plainly that
  F8 stops working until you turn dictation back on. It's on by default only
  on laptops. When it does turn dictation off you get a notice, even with
  notifications off.
- Changed: the AppImage no longer carries NVIDIA's cuDNN (CTranslate2 4.8.3).
  CUDA still downloads what it needs when you pick it.
- Fixed: "delete last sentence" left the next dictation stuck to the period
  before it.
- Fixed: in Fix a Word some clicks didn't pick the word, and the highlight
  took in the punctuation next to it.
- Fixed: talking about a mark, like "it should end with a question mark",
  typed "a?". With "a" or "the" in front, the words stay words.
- Fixed: in Type at pause, a question you paused in the middle of ended with
  a period.
- Fixed: "F eight" sometimes came out as words. A capital letter and a
  number now always go together, so you get F8, vitamin B12, row E9 and grid
  squares like B5 A7, whatever Write numbers as digits is set to.
- Fixed: on KDE Plasma 6.7 the AppImage, .deb and .rpm could lose the start
  of a dictation it pasted. The clipboard tool TuxTalk carries didn't know
  the clipboard protocol newer KDE uses, so it opened a window of its own
  that took the keyboard for a moment, and the paste went there.
- Fixed: TuxTalk's notices never showed up from the AppImage, .deb or .rpm on
  Fedora and similar systems, the "only silence from the microphone" warning
  included. They go straight to the desktop's notification service now.
- Fixed: with the microphone on System Default, switching inputs in GNOME's
  Quick Settings or KDE's volume applet needed a restart before TuxTalk used
  the new one. Thanks to Stefano Scipioni (issue #14).
- Fixed: a microphone that stops answering mid-dictation (a USB headset
  locking up) froze TuxTalk until it was restarted. Now it gives up on that
  mic after two seconds, and if it has to record from another input it says
  which.
- Fixed: "select the last word" took the period after it along, and "it
  inserted a carriage return" typed a line break.
- TuxTalk has its own website now, tuxtalk.app. The About window links to it,
  and so do software centers and the .deb and .rpm packages.

## [0.15.1] - 2026-10-15

- New: `tuxtalk --toggle`, plus `--start` and `--stop`, for your own desktop
  shortcut or a script. AppImage users run the AppImage file with it. If
  TuxTalk isn't running it starts it, and if the speech model is still loading
  the recording starts the moment it's ready, so a press right after login
  isn't lost. Before this you had to call TuxTalk over D-Bus yourself and
  guess when it was ready. Thanks to guelz for asking (Discussion #12).
- New: Transcribe a File, in the tray menu. Give it a recording or a video
  (MP3, M4A, WAV, MP4, MKV and the rest) and you get the text back, to copy,
  save as a text file, or save as subtitles for the video. It uses the same
  model you dictate with, so there's nothing new to download, and it runs on
  its own so dictation keeps working while it goes. On my NVIDIA card with
  Parakeet, three and a half minutes of talking took about nine seconds,
  loading included. Subtitle timing is shared out by sentence length inside
  each half minute, so a line can land a second or two off. Good enough for
  most things, not for a film.
  It keeps your last 10 transcripts under Earlier Transcripts, so you can
  close the window and still save one as subtitles later. Remove ums and
  repeats takes out the um, uh and "this is this is" without touching anything
  else, and unticking it shows the text exactly as it was heard. Closing asks
  first unless you saved it to a file, and you can drop a file anywhere on the
  window.
- Auto-detect can be held to the languages you actually speak. Whisper picks
  from 99 languages, and on a short dictation it sometimes picks wrong, so you
  say something in Spanish and get French back. Preferences, General, Only
  these languages: tick English and Spanish, say, and it only ever picks one of
  those. If Whisper thinks it heard something else it listens again in the
  likeliest language you ticked. It costs nothing when it guesses right. This
  is for the Whisper models. Parakeet has no language setting at all, it picks
  by itself, and Preferences says so.
- Whisper on built-in graphics is faster. The graphics engine was working out
  how likely every language was on every dictation, and TuxTalk never used
  those numbers. On my Ryzen's built-in graphics that extra work was about a
  fifth of a short dictation (0.63 seconds down to 0.49 with the tiny model).
  On a big NVIDIA card you won't notice.
- Numbers can be typed as digits. Parakeet likes to write "twenty-five" and
  "october fifteenth" out in words, so there's a new option in Preferences,
  Advanced, Write numbers as digits. You get 25, 15th, 7:30 and 1984, and zip
  codes read out one digit at a time come out as one number. You can also have
  it spell out one to nine the way a lot of style guides do. Works in English,
  German, Spanish, French, Italian, Portuguese, Dutch and Danish. Thanks to
  thecommedore64 for asking (Discussion #11).
- On KDE, picking a hotkey that already opens an app now warns you. I gave
  the Emoji Selector F6 in System Settings, then made F6 my dictation key, and
  pressing it opened the Emoji Selector and started dictating with no warning
  at all. Now the question after changing the key says which app KDE opens with
  it.
- Say "fix that word" right after a dictation and Fix a Word opens over your
  window with what you just said, so you can click the word it got wrong and
  teach it the right spelling. It fixes the word in your text too. Before, it
  was only in the tray menu, and I don't think many people found it there.
  From the menu, the copy option now says plainly that it copies the whole
  corrected dictation. I mixed that up myself and pasted three lines over one
  word.
- Dictating into a terminal no longer types "Git status." with a capital and a
  period. Terminals get no capital at the start and no period at the end, and
  code editors like VS Code get no period at the end. Everywhere else
  punctuation stays as it was, chat apps included. It's on by default and
  there's a tickbox in Preferences, Advanced if you'd rather not.
- "Delete everything" works in a terminal now. It pressed Ctrl+A, which in a
  terminal jumps to the start of the line, so nothing got deleted. In a
  terminal it now clears the line you're typing.
- New in TuxTalk Labs: select and move by voice. Say "select the last
  sentence", "select all", "go to the end" or "go back three words" on its own
  and TuxTalk does it instead of typing it. Said in the middle of a sentence,
  the words are just typed. Together with voice editing it's hands-free:
  select the last sentence, then "make it more formal". Turn it on in
  Preferences, Advanced, TuxTalk Labs. The Labs section now asks for your
  feedback too. I'd really like to hear how these work for you.
- When voice editing or select and move can't do something because of how it
  works (like "select that" when you haven't just dictated, or voice editing
  in a terminal), a small window now explains it and stays until you click
  Got it. The notification was gone before you could read it. Tick "Don't
  explain this again" and you just get a short reminder over your window
  instead. Show tips again, under TuxTalk Labs in Preferences, brings them all
  back.
- Voice editing (TuxTalk Labs) can do two or three changes at once. Say "make
  this formal and remove the commas" and it does both, one after the other.
  Before, only one of them happened.
- The hands-free option in Preferences now names your actual key ("Double-tap
  F9 for hands-free") and sits right under the toggle key it belongs to, so
  it's easier to notice that double-tapping is a thing.
- The language lists show each language in its own words too, like "Spanish
  (Español)" and "Japanese (日本語)", so you can find yours even if you don't
  read English. Preferences had little flags in its list, and those are gone,
  since a language isn't a country. Its list was also missing Maltese. The
  first-run screen now says "(the language you speak)" next to "I dictate in".
- Quit now also stops the typing helper (ydotoold) if TuxTalk started it. It
  was left running after you quit. One started by a system service is left
  alone.
- GNOME: after Uninstall Extension, the Install button no longer still says
  "Installed", and installing it again later starts it properly on the next
  login. Uninstalling had left it on GNOME's list of disabled extensions.
- Saying "question mark" at the end of a sentence sometimes typed ". ?".
  Parakeet puts its own period there before it hears the command. Now the
  question mark takes the period's place, and "exclamation point" works the
  same way.

## [0.15.0] - 2026-10-13

TalkType is now TuxTalk.

- Turns out another company already owns the TalkType name for dictation
  software, so the app has a new one. Same app, just renamed.
- Updating doesn't lose anything. Your settings, models, voice commands, stats
  and backups move over on their own, and so do the menu entry and start at
  login. Backups you saved with TalkType still load.
- The first time TuxTalk starts after updating from TalkType, a short message
  says so, once, so the new name in your menu doesn't come out of nowhere.
- On KDE the old TalkType hotkeys get cleaned up, so F8 isn't grabbed twice.
- The .deb and .rpm are called tuxtalk now and replace the old talktype package
  when you install them. On Arch it's tuxtalk-appimage, which replaces
  talktype-appimage. The talktype command still works.
- If something still says TalkType somewhere, let me know.

Also new in this one:

- Music and videos pause while you dictate. I had a YouTube video going and it
  turned "isn't it" into "Venezuela". So now anything that's playing pauses when
  you start talking, and when you're done only what TuxTalk paused starts back
  up. If you hit play yourself in the middle, it leaves that alone. It's on by
  default, and you can turn it off in Preferences under Audio & Visual Feedback.
- The Commands tab in Preferences is called Snippets now. Same list, same
  entries. "Commands" kept getting mixed up with the built-in ones like "new
  line" and "period".
- Your Words, in the Snippets tab. Put in the names and terms you use, like
  TuxTalk or Kubernetes, one per line. Whisper models get told about them
  before they listen, so they spell them your way. And every model fixes them
  when they come out split up or with the wrong capitals, so "tux talk" turns
  into TuxTalk. It also catches spellings that sound the same, which is how
  Parakeet kept writing it, "Tuck's talk" and "Tuckstalk". Heads up, Parakeet
  can't be told about words ahead of time, so if it hears one as something
  else entirely, Fix a Word is still the way.
  Words you fix with Fix a Word get added to Your Words too.
- TuxTalk Labs, a new spot at the bottom of Preferences, Advanced, for
  features that work but are still being worked on. There's a Labs page in
  Help that explains them, and a link to tell me how they're working for you.
- Also in Labs: the recording indicator can follow the window you're
  dictating into, showing up near the bottom of it wherever it is on the
  screen. And when voice editing can't do something, like when nothing's
  selected, the message now pops up right over the window you're working in
  instead of only down in the corner where it's easy to miss.
- Voice editing, the first thing in Labs. Select some text, hold Shift+F8 and say how to change it,
  like "make this more formal", "shorten this" or "turn this into a bullet
  list". Say it however you want, there's no fixed wording. The text gets
  replaced with the rewrite, and Ctrl+Z puts it back. It uses the same small AI
  as the self-corrections, so if you already have that, there's nothing more to
  download. It's off unless you turn it on in TuxTalk Labs. Heads up,
  it's a small model: quick changes come out well, but ask it to write a whole
  email from one line and it'll make stuff up.
  Some jobs skip the AI completely because it's bad at them. All caps,
  lowercase, taking out the commas or a word, removing bullets, that's all done
  exactly in code, and so are indenting, "delete this" and turning a few
  lines into a bulleted or numbered list. Bold, italics and underline work in word processors like
  LibreOffice, where TuxTalk just presses Ctrl+B or whatever for you. KWrite has
  no bold at all, so there it tells you so. And select a few lines and you get
  a few lines back, the AI doesn't get to squash them into one.
- Swiss German spelling. Swiss Standard German never writes ß, but the
  speech models do, so there's a new option in Preferences, Advanced that types
  ss instead (Strasse, gross). It's on by itself if your system is set to Swiss
  German. Thanks to BatmansGehilfe for asking (issue #10).
- Ctrl+Alt+P types your last dictation again, wherever your cursor is. Handy
  when a dictation lands in the wrong window because something stole the
  focus. You can change the key or turn it off in Preferences, General.
- Times like "6:41 p.m." sometimes came out as "641 p.m.", with no colon. Turns
  out Parakeet writes the same time two different ways, and only one of them
  was getting fixed. Both come out as 6:41 PM now.
- Preferences has a gear next to it in the tray menu now, so it's easier to
  spot. On KDE the Dictation Service checkmark moved into the same column so
  everything still lines up.
- Close Preferences with the X after changing something and it asks if you
  want to apply it first. Before, it just threw your changes away without a
  word. I found that one the hard way.

Fixed, mostly found by testing on five different distros:

- Tap F9 and say nothing and Parakeet could type "Mm-hmm." It hears that in
  silence, apparently. It doesn't get typed anymore, and a dictation that
  comes up empty gives you the cancel beep so you know it heard you stop.
- Test Hotkeys could close the second it opened.
- Preferences fits on a 1366x768 or 1280x800 laptop screen with its buttons
  showing.
- If an old TalkType was still running, TuxTalk quietly didn't start. Now the
  old one steps aside. An old TalkType Flatpak gets stopped too, otherwise both
  of them typed every dictation twice.
- The hotkey test only says Working when TuxTalk can actually read your
  keyboard. Before, it could say Working and then the dictation key did
  nothing.
- A line break going into a terminal becomes a space, so a dictation can't run
  a command by itself.
- Model downloads try again on a bad connection, and ask before falling back
  to a smaller model.
- Cancel the password prompt for the typing fix or an update and it says
  cancelled instead of failed.
- On GNOME without the extension, dictating into a terminal pastes again.
- On KDE the setup windows show TuxTalk's icon instead of a generic W.
- The "You're All Set" window fits without scrolling.
- Restoring the clipboard puts the middle-click selection back too.

## [0.14.6] - 2026-10-11

Settings backup, CUDA where you'd look for it, and a first run fix for AMD and
Intel computers.

- On some computers the first run screen never showed the setup it
  recommends, the card that names your graphics card and asks your language.
  It only showed up on GNOME, with an NVIDIA card, or when something needed
  fixing first. So on KDE with AMD or Intel graphics and nothing to fix, you
  got the default setup without being asked anything, and if you speak a
  language Parakeet doesn't know, dictation came out wrong from the start.
  It shows on every computer now. This one had been there since 0.14.2.
- You can back up your settings. Preferences has a new Updates & Backup tab.
  Save Backup puts your settings, voice commands, Fix a Word words and stats
  in one file you can keep or load on another computer. Pick a backup folder
  once and Save and Load open right there.
- TalkType also keeps a copy of your settings each time it starts, if they
  changed, and holds on to the last 10. If a change or an update goes wrong,
  pick one under Earlier settings and go back. Your own backups are in that
  same list, newest first.
- Loading a backup on a computer that doesn't have the same graphics card
  keeps that computer's own Device setting. A backup from an NVIDIA machine
  won't leave you set to CUDA with no NVIDIA card.
- CUDA is listed under Device on NVIDIA computers now, same as Vulkan. Before,
  it only showed up after you found Download CUDA Libraries under Advanced.
  Picking it asks first, downloads NVIDIA's 1.4 GB of files with a progress
  bar and switches over.
- With Parakeet picked, the CUDA choice says "Parakeet runs on the processor",
  because Parakeet can't use CUDA. The warning under the settings is amber
  and bold now. In grey it was easy to miss.
- The Updates tab got tighter so the backup fits on it too. The update
  status only shows up once you've checked.
- If you're on Parakeet and pick CUDA, it now always asks which Whisper
  model you want, even when CUDA is already downloaded. Before, it only
  asked right after the download, and otherwise you ended up on CUDA with
  Parakeet still running on the processor.
- There's a Use Recommended button under Model and Device, so you can get
  back to what first run picked without going through the tray. Next to it,
  "What works with what?" shows which models run on the processor, Vulkan
  and CUDA, and which of those your computer has.
- Change a hotkey and TalkType offers to test it right away. On a lot of
  laptops the F keys change brightness or volume unless you hold Fn, and the
  test shows you that. If KDE already uses the key for one of its own
  shortcuts, it tells you. That warning only went in the log before.
- The hotkey lists say what F1, F5, F11 and F12 already do in most apps, so
  nobody picks F5 and has the browser reload every time they dictate.
- AI corrections understand stacked phrases. "Meet me at three o'clock.
  No, actually, make that four o'clock." types "Meet me at four o'clock."
  I checked it on the real model against ordinary sentences that start
  with "No", "Sorry" or "Actually", and it left them alone.
- Double-tap for hands-free and then say nothing, and it used to record
  until you pressed the key again. It stops after 10 seconds now and
  doesn't type anything.
- The bars indicator kept growing in silence until the room noise looked
  like talking. It stays low now and still fills up when you speak.
- Preferences scrollbars sit beside the page instead of on top of it, and a
  page that has more below fades at the bottom so you know to scroll.

## [0.14.5] - 2026-10-10

Smarter corrections, addresses you can say out loud, and rounded message boxes
on KDE.

- TalkType's small message boxes, like "You're Up to Date!", had square
  corners on KDE while every other TalkType window had KDE's rounded frame.
  They get the desktop's own frame now, with rounded corners and a title bar,
  on KDE, GNOME and X11 desktops alike.
- The hotkey test on the welcome screen never saw your keyboards directly.
  It called a function the keyboard library doesn't have, which skipped every
  device. The test still worked through the window itself, so nobody noticed,
  but now it reads the keys the same way dictation does.
- The Stats graph said "most: 1 words". It says "1 word" now.
- Saying an address with "dot" works every time. "Email me at john at gmail
  dot com" types john@gmail.com, and "look at github dot com" types
  github.com. Parakeet writes the word "dot" about one time in three, and
  then you got the words instead. It only happens where it reads as an
  address, so "a dot com company" stays as it is.
- A spoken email address comes out in lower case. Parakeet capitalizes names,
  so "send it to John at gmail.com" typed John@gmail.com.
- Addresses with more than one dot were split like sentences:
  "www.example.org" came out as "www. Example.org", and an email like
  bob@mail.example.com broke the same way. Fixed.
- When the speech model wrote "New line:" with a colon, the colon was left
  at the start of the new line. It goes with the command now.
- AI self-corrections now handle saying the whole thing again. "Send the
  report to Bob. Scratch that, send it to Jim." types "Send the report to
  Jim." Before, the AI got these right but the safety check that guards
  against its mistakes threw the answer away, so it was typed as spoken. In
  testing with the real model, restated sentences went from 2 out of 8 fixed
  to 6 out of 8, and the check still refuses an answer that keeps the wrong
  part or drops something you said.
- "Actually" works as a correction without a pause. Parakeet often writes
  "Actually book it for Tuesday" with no comma, and TalkType only counted
  "actually" with one, since it's usually just a word. Now a sentence opening
  with it counts when it repeats a word from the sentence before. Tested on
  the real model: 15 ordinary sentences like "Pick up milk. Actually pick up
  eggs too." were all left alone, and with one more example for the AI, 15
  of 16 corrections across all of today's tests were fixed.
- Switching the device to CPU, cancelling the download, then clicking OK
  again kept dictating on the graphics card, though Preferences said CPU.
  Cancelling a download now puts the device back as well as the model, so
  the next OK really switches.
- Downloading the CUDA libraries saved the switch to CUDA but never moved
  dictation onto it. Now it really switches. And since Parakeet can't use
  CUDA, switching it would have made it slower: on Parakeet you're asked
  which Whisper model to use on CUDA, from Tiny to Large-v3 with their
  download sizes, or you keep Parakeet as it is.
- If the device was CUDA but the CUDA files were gone (an interrupted
  download, a cleaned-up folder), every dictation crashed the service. Now it
  uses the processor and tells you to download CUDA again.

## [0.14.4] - 2026-10-10

Mostly things that came out of testing 0.14.3, plus a few older bugs that a
second review turned up.

- When a dictation can't be typed into the window, TalkType now tells you why
  and what fixes it. Usually it's the typing setup: after Fix Typing you have
  to restart the computer before typing works, and nothing said so. A Fedora
  user dictated for minutes with every word transcribed and none typed, and
  figured his laptop was too weak. If the typing helper has stopped, TalkType
  starts it again.
- The welcome screen said you could skip the typing setup and use Clipboard
  Paste instead. That was wrong, paste needs the same setup. It now says your
  dictations are still kept in Recent Dictations.
- Whisper models were loaded twice every time the dictation service started,
  once just to check the model was downloaded. Large-v3 starts a couple of
  seconds faster now and doesn't need the extra memory.
- Correcting yourself works better when you pause before "no wait". The
  speech model puts a period there and the AI read it as two sentences, so
  "Meet me at three. No wait, four o'clock." mostly came out unchanged. In
  testing it went from 4 out of 10 fixed to 8 out of 10. It's also stricter
  now, so it won't drop the "no wait" and keep the wrong time.
- "look at github.com" and the like turned into an email address. Fixed.
- A failed download of the AI model could be reported as finished.
- The Stats page fits without the Reset button half hidden under the edge,
  and so does Updates.
- Performance > Custom opens Preferences now, in the tray and in the GNOME
  menu. The GNOME menu didn't have Custom at all, so a custom setup showed no
  dot there. That needs extension version 16, and Check for Updates offers it.
- Long dropdown lists like the language list open as three columns, so they
  fit on the screen with your current choice showing.
- On the welcome screen, "Other options" was easy to miss. It's now a button
  that says what it's for: Change language, model or graphics card. The
  "Set it up" button isn't cut off at the bottom anymore either.
- In the AppImage, every emoji was blank on newer distros like Fedora,
  including the flags in the language list. It brought along an old copy of
  the library that draws the text, and that one can't draw today's color emoji
  font. It uses your system's copy now.
- Cancelling a model download in Preferences left the dropdown showing the
  model you cancelled, though the old one was still the one in use. It goes
  back now.
- After a model download, OK left Preferences open, so you had to click OK
  twice. OK closes now; Apply is the one that keeps the window open.

## [0.14.3] - 2026-10-10

Small one, mostly fixes. The main one came from Stefano on GNOME.

- On GNOME under Wayland, the dropdowns in Preferences couldn't be changed if
  the window was on a second monitor. The list was opening off screen where you
  couldn't see it. That's a GTK bug that never got fixed, so on Wayland the
  dropdowns open a different way now that the desktop itself places, and it
  works on any monitor. Thanks to Stefano Scipioni for tracking it down (#9).
- While fixing that I found GTK's normal dropdown is no good on KDE either. The
  first time you click one it opens squashed, with scroll arrows and the items
  cut in half, until you move the mouse. The new way doesn't do that. The list
  just drops down under the button.
- Ordinary sentences could get turned into email addresses. "Look at this. My
  car broke." came out as "Look@this.My car broke." because "my", "no", "it"
  and "in" are also the endings of some countries' web addresses. Spoken
  addresses like "john at gmail.com" still work.
- On KDE, TalkType's windows showed a generic "W" icon in the title bar and
  taskbar instead of TalkType's own. Every window was calling itself "python3",
  so KDE couldn't match it to the launcher.
- Opening Preferences straight to a tab, like Your Stats from the menu, showed
  the right page but left General highlighted on KDE.
- After a service restart on KDE, the log could say F8, F9 and Ctrl+Alt+V were
  already taken by KDE when they weren't. It checks again for about a second
  now before warning.
- Picking the Performance preset you're already on doesn't rerun the speed
  check and restart the service anymore. The GNOME menu sends a click even for
  the one with the dot next to it.
- First run on a graphics card shows one download window instead of two.
- New screenshots in the README and the software store listing. The old store
  ones were a year out of date.

## [0.14.2] - 2026-10-06

This one's mostly about setup. TalkType has a lot of settings now and it was
way too easy to end up on a slow combination without knowing it. I did it
myself, I ran Parakeet on the processor for weeks with a graphics card sitting
there doing nothing.

- First run picks a setup for you now. It looks at your graphics card and
  your language and shows one card that says what you'll get, something like
  "Parakeet on your NVIDIA GeForce RTX 4070 SUPER", and how big the download
  is. Click Set it up and that's exactly what gets downloaded. Other options
  lets you change the language, the model, or whether it uses the graphics
  card, and anything that won't work on your computer is greyed out with the
  reason. The separate model picker that came after the hotkey test is gone.
- AMD and Intel graphics get used at first run too now, through Vulkan.
  TalkType still checks the graphics card is really faster than your processor
  before it switches, and stays on the processor if it isn't. The big CUDA
  download isn't offered at first run anymore. It's still in Preferences.
- Parakeet is the default model for new installs.
- The Performance menu went from seven presets down to three: Recommended for
  this computer, Lightest and Battery saver. The old ones were named after
  models and half the time did something other than what the label said.
  Going back from Battery saver puts the auto-stop back to 5 minutes. The
  GNOME menu gets its presets from TalkType itself now, so it needs extension
  version 15, and Check for Updates will offer it.
- If you're already set up and there's a better setup for your computer, you
  get one notification about it after updating. Just the once, and nothing
  changes unless you pick it.
- The tray and GNOME menus show what's really running instead of just what the
  settings say. That turned up a few things that were quietly wrong. Large-v3
  on Vulkan was actually running as medium. AMD and Intel users were told
  large-v3 wouldn't run for them when it does. And Preferences warns you now
  when Parakeet can't do what your settings ask for.
- If a dictation doesn't make it into the window, TalkType tells you instead
  of failing silently. It's saved in Recent Dictations so you can paste it.
- The Voice Commands window and Help were behind. They never mentioned "no
  wait" or "scratch that" for correcting yourself, and Help still talked about
  a red indicator and menu items that don't exist anymore. Both are caught up,
  and they say the commands are English words whatever language you dictate in.
- The download sizes for the Whisper models were about half of what they
  really are. Small said 244 MB and downloads 486 MB. They're the real numbers
  now, everywhere they show up.

## [0.14.1] - 2026-10-04

Mostly keyboard fixes, and a lot of them came from guelz testing on a Swiss
keyboard.

- Typing mode works on keyboard layouts other than US English now. It used to
  send US key positions, so on a German or Swiss keyboard Y and Z came out
  swapped and umlauts couldn't be typed at all. TalkType reads the layout you
  have on and presses the keys that layout actually uses, accented letters
  included. If the layout has no way to type something, like Latin words on a
  Ukrainian keyboard, that dictation gets pasted instead. (#8)
- On X11, TalkType read the first layout in your list instead of the one you'd
  switched to, so Ukrainian came out as a row of 9s. It reads the active one
  now, and on X11 it can paste the text a layout can't type, which it couldn't
  do before.
- Pressing F8 also reached whatever app you were in, so a terminal showed a
  stray "~". On KDE and GNOME your hotkeys stay with TalkType while dictation is
  on, and go back to being normal keys when you turn it off. GNOME needs the
  updated extension (version 14), and Check for Updates will offer it.
- Dictations are marked so clipboard managers like Klipper leave them out of
  their history. There's also a new option to put back whatever you'd copied
  before the dictation. It's in Preferences, Advanced, under Text Injection,
  and it's off by default because I like being able to paste a dictation again
  when it lands in the wrong window. (#7)

## [0.14.0] - 2026-10-03

Parakeet can use your graphics card now, and dictating into a terminal works
on a lot more desktops.

- With the device set to Vulkan (any GPU), Parakeet runs on the graphics
  card. On my RTX 4070 Super a minute of talking takes about 0.2 seconds
  instead of 2, and it's just as accurate. Choosing Vulkan in Preferences
  downloads a copy of Parakeet made for the graphics engine (669 MB) and
  checks that your graphics card is really faster than your processor
  before it switches. The processor copy stays, so if the graphics card ever
  has trouble, TalkType goes back to it.
- Terminals need Ctrl+Shift+V to paste, so TalkType has to know when you're
  in one. That only worked on GNOME and KDE. Now it also works on Sway, i3,
  Hyprland and niri, and on X11 desktops like XFCE, MATE and Cinnamon.
  Hyprland is the one I couldn't try on a real machine, so if you're on
  Hyprland I'd like to hear whether dictating into a terminal works for you.
- Ghostty, GNOME Console, xfce4-terminal, Guake and more than a dozen other
  terminals were missing from TalkType's list and got the wrong paste. That's
  fixed on every desktop.
- On Vulkan, clicking OK in Preferences never closed the window. That's been
  there since 0.12.0 and it's fixed. The Device dropdown also closes now
  before the speed check pops up, instead of staying drawn on top of it.
- On the first-run screen, the Full and Light choices for an NVIDIA card
  stayed greyed out until you ticked the box above them, so it looked like
  Light couldn't be picked. Picking either one ticks the box for you now.
- A quick tap of the key with nothing said doesn't get typed out anymore.
  Parakeet would sometimes hear "Thank you." in that fraction of a second of
  background noise.
- Help has a link to the TalkType Discussions page, for questions or just
  telling me how it's going.

## [0.13.1] - 2026-10-03

The Performance presets know about Vulkan now.

- Picking Most Accurate from the tray menu used to need NVIDIA's CUDA
  libraries, and without them it either offered the 4.4 GB download or said
  it couldn't be done. Now an NVIDIA card gets the same Light (Vulkan) or Full
  (CUDA) choice as setup, and AMD or Intel graphics get offered Vulkan.
- If you're already set up with Vulkan, picking Balanced, Quality or Most
  Accurate keeps you on Vulkan. Before, it quietly moved you back to the
  processor because it didn't find CUDA.

## [0.13.0] - 2026-10-03

A much lighter way to use an NVIDIA graphics card.

- Until now, using an NVIDIA card meant downloading NVIDIA's CUDA libraries,
  about 1.4 GB. The Vulkan engine from 0.12.0 works on NVIDIA cards too, and
  on my RTX 4070 Super it was just as fast as CUDA: half a second for 11
  seconds of speech with the Large model, either way. It's a 24 MB download.
- When TalkType first sets up and finds an NVIDIA card, you now get to pick:
  Full (CUDA, 1.4 GB) like before, or Light (Vulkan, 24 MB). Full is still the
  default for now, since it's been in use a lot longer.
- In Preferences the device is now called Vulkan (any GPU), and it shows up
  on NVIDIA, AMD and Intel. If you have a graphics card plus graphics built
  into the processor, it uses the card.
- Picking the Large model without CUDA now offers Light (Vulkan) or Full
  (CUDA), instead of only the 4.4 GB CUDA download. The Light route is about
  1 GB all in, because the Large model is smaller in Vulkan's format too.
- If you're on AMD or Intel graphics, picking the Large model now offers to
  set up Vulkan, instead of saying it can't be done.

## [0.12.0] - 2026-10-03

TalkType keeps score now, if you want it to, and Whisper can use AMD and
Intel graphics.

### AMD and Intel graphics
- Until now only NVIDIA graphics cards could speed up the Whisper models. If
  your computer has AMD or Intel graphics, there's a new choice under Device
  in Preferences: AMD / Intel graphics.
- Picking it downloads a small graphics engine (24 MB) and the model in the
  format that engine uses, then times your graphics chip against your
  processor. TalkType only switches if the graphics chip is clearly faster,
  and tells you the numbers either way. The graphics built into some desktop
  processors is too small to help, and in that case it says so and keeps
  using the processor.
- Laptops with recent AMD or Intel graphics, and separate AMD or Intel
  graphics cards, are where this helps most.
- Parakeet isn't affected. It already runs fast on the processor.

### Your Stats
- Pick Your Stats from the tray menu (or Preferences, Stats tab) to see how
  many words you've dictated today, in the last 7 days and all time, how many
  dictations that was, how long you spent dictating, and about how much time
  it saved you over typing.
- There's a chart of the last two weeks, and it tells you how much faster you
  dictate than you type. Most people talk a lot faster than they type.
- Type in your own typing speed if you know it. Time saved goes by that
  instead of the usual 40 words a minute, so a fast typist doesn't get told
  they saved more time than they did.
- It only keeps the numbers, on your computer. It never saves what you said.
- You can turn it off or reset it from the same tab.

## [0.11.2] - 2026-10-02

A couple of fixes I found while testing Fix a Word on GNOME.

- If you set TalkType up before 0.10.1, launch at login might still be
  pointing at the AppImage you first downloaded, usually the one sitting in
  your Downloads folder. That means it started an old version at login, or
  nothing at all if you'd cleaned out Downloads, and on Ubuntu 26.04 that old
  version doesn't start at all. TalkType now notices this when it starts and
  points launch at login at the installed copy in ~/AppImages instead. If
  you turned launch at login off, it leaves it off.
- GNOME users: if TalkType's tray restarted while dictation kept running, the
  panel menu could stop working properly, Fix a Word included, and the panel
  icon could stop showing when you're recording. Fixed.
- The message you get when there's nothing to fix yet now points you to the
  right place in the menu.

## [0.11.1] - 2026-09-29

A small one, nothing changes in how TalkType works.

- The AppImage now has update information built in, so if you use
  AppImageUpdate or a manager like Gear Lever or AppImageLauncher, it can
  update TalkType for you and only downloads the parts that changed.
  TalkType's own Check for Updates works the same as before.

## [0.11.0] - 2026-09-29

You can teach TalkType words it keeps getting wrong now.

### Fix a Word
- If TalkType keeps writing "bamboo studio" when you mean BambuStudio, open
  Fix a Word from the tray menu. Pick the dictation, click the wrong word (or
  drag across a few), type the right spelling and click Always fix this. From
  then on it gets it right in every dictation.
- It works with every speech model, Parakeet included, because it fixes the
  words after they're heard.
- Your fixes show up in Preferences under Commands, where you can edit or
  remove them. There's a Fix a Word button there too.
- It warns you before you "fix" an everyday word like "four", since that
  would change it every single time you say it.
- If you want to paste the corrected sentence over the one that came out
  wrong, it can copy that for you too.

### Also
- If Preferences was already open behind another window, picking it from the
  tray again did nothing, and it looked like it wasn't opening at all. It
  comes to the front now. Same for Voice Commands and Fix a Word.
- Saving in Preferences could wipe out a voice command that was added while
  Preferences was open. Fixed.
- The text in Preferences was small in a few places and hard to read. It's
  normal size now.
- The smart quotes option in Preferences was missing the quotes it's supposed
  to show as an example.
- Preferences no longer shows GNOME extension settings on desktops that
  aren't GNOME.

## [0.10.3] - 2026-09-26

One more fix for systems that were missing a library.

### Fixes
- If your system didn't have PortAudio installed, the library TalkType uses to
  hear your microphone, TalkType popped up a window asking you to install it.
  A copy of PortAudio comes inside the AppImage now, along with the JACK
  library it needs, so it just works. If your system does have PortAudio,
  TalkType keeps using that one like it always has.

## [0.10.2] - 2026-09-26

The AppImage should now start on more systems.

### Fixes
- On Ubuntu 26.04 the AppImage wouldn't start at all unless you ran it with a
  special flag, and the menu entry and launch at login just silently did
  nothing. It was built with an old tool that needs a library called libfuse2,
  and 26.04 doesn't ship it anymore. It's built with the current tool now,
  which doesn't need libfuse2. I tested it on a clean 26.04 and it starts
  normally. It's also a bit smaller, 124 MB instead of 139.
- If your system didn't have the tray icon library (libayatana-appindicator3)
  installed, TalkType crashed as soon as it started. That library comes inside
  the AppImage now, and in the .deb and .rpm too.

## [0.10.1] - 2026-09-26

Mostly a fix for a stuck key I ran into while testing in a VM.

### Fixes
- Holding the dictation key could leave your desktop thinking that key was
  still held down, and it got worse by one every time you dictated. Most apps
  never notice. But a virtual machine window, a remote desktop session or a
  game does notice, and it starts repeating the key by itself. With F8 that
  meant a VM terminal filling up with tildes. TalkType now tells the desktop
  the key came back up before it takes over the keyboard, so it never gets
  out of step. If you already have a stuck key from an older version, logging
  out and back in clears it.
- Launch at login was pointing at whatever AppImage you ran setup from, which
  is usually the one sitting in Downloads. Clean out Downloads and TalkType
  quietly stopped starting at login. It points at the installed copy in
  ~/AppImages now.
- If you use Parakeet, the Device line in the menu says CPU now. Parakeet
  always runs on the CPU, so it was wrong for it to say cuda.
- The AI cleanup engine could get left running in the background after
  TalkType closed, sometimes for hours, holding on to about a gigabyte of
  memory. It gets shut down properly now, and anything left over from before
  gets cleaned up the next time TalkType starts.
- The welcome screen and the model tooltip in Preferences mention Parakeet
  now, not just Whisper.

## [0.10.0] - 2026-09-26

A hands-free option for the toggle key.

### Hands-free
- **Double-tap F9 (or whatever your toggle key is), talk, and just stop
  talking.** After a couple of seconds of quiet the recording ends and your
  text gets typed. No second key press needed. A short high beep lets you
  know the double-tap took.
- **A single tap works exactly like before.** It keeps recording until you
  tap again, however long you pause to think. So if you dictate long stuff,
  nothing changes for you unless you double-tap.
- It waits until you've actually started talking, a cough or a click doesn't
  count, and it learns how noisy your room is as it goes.
- It never cuts anything off the start or middle of what you said. The only
  audio it drops is the quiet bit at the end after you stopped.
- Off until you turn it on in Preferences under General, right below the
  toggle key. You can set the quiet time anywhere from half a second to ten
  seconds. Hold-to-talk isn't affected at all.

### Also
- On the Flatpak, if a toggle recording ended some other way (like pressing
  Esc), the next press of the toggle shortcut could do nothing. Fixed.

## [0.9.0] - 2026-09-26

New Dictation Cleanup options, and a handful of fixes for things that could go
wrong when a speech model wasn't downloaded yet.

### Dictation Cleanup
Both of these are off until you turn them on, in Preferences under Advanced.

- **Remove "um", "uh" and repeated words.** "Um, so the the van is ready"
  comes out as "So the van is ready". It's instant and there's nothing to
  download. It leaves alone the doubles people actually mean, like "that
  that" or "no, no, no".
- **Fix self-corrections with AI.** Say "order two boxes of screws, no wait,
  three boxes" and you get "Order three boxes of screws." A small AI model
  runs on your own computer to do it. It's a one-time 1.1 GB download when
  you turn it on, and it uses your graphics card if you have one (NVIDIA, AMD
  or Intel) or your processor if you don't.
- The AI only ever sees sentences where you corrected yourself, and TalkType
  doesn't just trust what it sends back. If the answer changes anything
  besides the correction, like rewording something or answering a question
  you dictated, it gets thrown out and your words go in exactly as you said
  them.

### Fixes
- **Cancel now means cancel.** If TalkType starts up and your speech model
  isn't downloaded, it asks first. On computers with an NVIDIA card, clicking
  Cancel was ignored and it downloaded anyway.
- **No more crash after that download.** When the model had to be downloaded
  at startup, the dictation service crashed right after the download
  finished.
- **Cancelling no longer leaves you without dictation.** TalkType switches to
  a model you already have, tells you which one, and keeps going. It only
  stops if you have no model downloaded at all.
- **Picking a preset or a text injection mode from the tray restarted
  dictation twice**, and briefly switched back to what you had before. It's
  once now.

## [0.8.0] - 2026-09-26

TalkType can now use NVIDIA's Parakeet model instead of Whisper. If you don't
have an NVIDIA graphics card this is the one to try. It's about as accurate as
Whisper large-v3, which was never usable without a GPU, and it runs on a plain
processor in well under a second.

### Parakeet
- New model choice in Preferences and on the first-run setup screen, and a new
  "Fast & Accurate" preset under Performance in the tray and the GNOME menu.
- It's a 670 MB download, same progress bar as the Whisper models. Nothing
  downloads unless you pick it.
- It handles English and 24 European languages and figures out which one you're
  speaking. It doesn't do Chinese, Japanese, Korean, Arabic and so on, so stick
  with a Whisper model for those.
- It always runs on the processor, even if you have an NVIDIA card, because it's
  already fast there.
- Spoken punctuation, custom commands and undo all work the same as with Whisper.

### Recent Dictations
- New "Recent Dictations" submenu right under Restart Service. It holds your last
  20 dictations. Hover over one to read the whole thing and click it to copy it,
  then paste wherever you want. Handy when a dictation lands in the wrong window
  or nowhere at all.
- The list is kept in memory only. It never touches the disk and it's gone when
  you log out. There's a Clear History item at the bottom if you want it gone
  sooner.
- In the GNOME panel menu each entry shows the first couple of lines and one
  click copies it.

### Also
- The last setup screen now has a link to the Discussions page. I'd like to hear
  how TalkType is working for people, good or bad.
- The GNOME extension is now version 10.

## [0.7.4] - 2026-09-05

Dictation into a terminal now works on KDE. It previously did nothing at all —
no text, no error, and TalkType played its usual "finished" beep as though it had
worked.

### Dictating into a terminal
- **Text now actually appears.** Terminals need Ctrl+Shift+V; plain Ctrl+V means
  something else entirely at a shell prompt, so nothing was inserted. TalkType
  knew this and had always sent the right keys — but only on GNOME, because the
  GNOME extension was the only thing that ever told it which window you were
  typing into. On KDE it never knew, so it always guessed "not a terminal".
  TalkType now asks KWin directly, so KDE gets the same treatment GNOME always
  had.
- **"new line" and "new paragraph" work in a terminal too.** Terminals have no
  "soft" Enter — Enter runs the command — so the keystroke TalkType sends for a
  line break was simply ignored and everything ran together on one line. In a
  terminal the line break is now part of the pasted text instead, which terminals
  insert normally. Everywhere else is unchanged: in a chat box a real line break
  would send your message, so those still get the keystroke.
- **Dictating into Claude Desktop is more reliable on KDE**, for the same reason —
  TalkType can now recognise it and use the input method that works with it.

### Note for terminal users
The line break relies on your shell's "bracketed paste" setting, which is on by
default in current versions of bash and zsh. If you have deliberately turned it
off, a dictated line break will run the command at that point instead of breaking
the line. Only affects dictation containing "new line" aimed at a terminal.

### Also
- Terminal recognition no longer depends on exact capitalisation, so terminals
  are identified consistently however your desktop reports them.

## [0.7.3] - 2026-09-05

This release is about settings that said they had been applied when they hadn't.
Several things you change in Preferences were never reaching the running
dictation service, and TalkType told you they had.

### Settings you change now actually take effect
- **Changing your hotkey works immediately again.** Picking a new record key (or
  switching between hold and toggle) saved correctly but the running service
  kept listening on the old key, with no hint anything was wrong. It now takes
  effect right away, and the old key stops working — as it always should have.
- **Custom voice commands take effect on your next dictation.** Adding or editing
  a command used to need a service restart; the Commands tab said so, while the
  Apply dialog claimed the opposite. No restart is needed now, and the tab says
  what is true.
- **Switching microphone actually switches microphone.** Choosing a different mic
  applied to the settings file but not to the running service, so dictation kept
  recording from the old device until the next restart.
- **Turning the auto-timeout on or off is respected immediately.** Unticking it
  used to leave the service still shutting itself down after the old interval.

### Custom commands are easier to get right
- **A command with a number in it now works either way it is heard.** Speech
  recognition decides on its own whether you said "one" or "1", and it is not
  consistent — so a command typed as "test phrase one" would quietly fail
  whenever it came through as "test phrase 1". Both spellings now match.

### Fixed
- **Cancelling a model download no longer leaves the wrong model saved.** If you
  picked a new model and cancelled or lost the download, Preferences kept the new
  name while the service went on using the old one — and trying again reported
  "your changes are already in effect" without switching.
- **Quitting no longer risks closing other TalkType windows.** The quit action
  matched too broadly and could take down the tray and Preferences along with the
  dictation service.
- **Dictating into Claude Desktop works again.** The app changed its identifier,
  so the workaround that makes text appear reliably in it had stopped running.
  (GNOME only — see below.)
- **Text typing can no longer hang forever** on setups that use `wtype`, and a
  package update that gets stuck waiting on your package manager now reports back
  instead of leaving the window frozen.
- **The update check no longer reverts settings.** Its once-a-day check could
  quietly undo anything you changed in Preferences while it was running.

### GNOME
- **Ready for GNOME 51.** The panel extension now declares support for GNOME 51,
  which ships on 16 September. Without this the extension would be disabled and,
  because stock GNOME has nowhere to show the fallback icon, you would have been
  left with no TalkType icon at all.

### Known limitation
- Terminal paste and the Claude Desktop fix rely on knowing which window has
  focus, which only the GNOME extension currently reports. On KDE and other
  desktops they stay inactive; support for those desktops is in progress.

## [0.7.2] - 2026-08-19

A small polish release for the AppImage, `.deb`, and `.rpm` builds. (A sandboxed
Flatpak edition is on its way to **Flathub** — see the README.)

### Fixed
- **The recording-indicator color picker now shows a visible checkmark** on the
  white and other light-colored swatches, so you can tell which color is selected.
- **Scrolling a Preferences or setup window no longer stalls** when the pointer is
  over a dropdown — the page scrolls as expected and the dropdown value is left
  alone (instead of you having to move the mouse off the dropdown first).

### Changed
- **Clearer update wording on `.deb`/`.rpm` installs:** the Updates tab now says
  plainly that TalkType downloads *and* installs updates for you (with your
  password), rather than implying you have to update through your distro yourself.

## [0.7.1] - 2026-08-16

This release fixes how TalkType updates itself, so that updating works correctly
no matter how you installed it — and makes the whole update experience
consistent across every desktop.

### Updating now matches how you installed
- **`.deb` / `.rpm` installs update through your package manager, in one click.**
  Before, "Check for Updates" on a Fedora or Debian install tried to download and
  run an AppImage — which failed with a confusing `libfuse.so.2` error and left
  you stuck. Now TalkType knows it was installed as a package: it downloads the
  new package, asks for your password once, and installs it properly with your
  system's own tools, so dependencies stay correct.
- **Arch (AUR) installs are pointed to the AUR helper** (`yay -S talktype-appimage`)
  instead of trying to update the wrong way.
- **AppImage installs keep updating in place**, exactly as before.
- TalkType picks the right method automatically based on how you installed — you
  don't have to think about it.

### One clear, consistent update window
- **"Check for Updates" goes straight to a single window** with your choices —
  Download & Install, or View on GitHub — instead of *also* opening the
  Preferences window on top of it.
- **The GNOME panel menu and the system-tray menu now behave identically.** Both
  open the same window; neither detours through Preferences.
- **The automatic daily check shows that same window** when an update is found,
  instead of a notification that was easy to miss.

### Fixed
- **TalkType now reliably restarts after an update.** The old restart could leave
  the app half-started and unresponsive — menus and windows would stop opening.
  It now shuts down cleanly and launches a fresh copy.
- **About, Help and Voice Commands windows now always open.** On some Wayland
  setups they could silently fail to appear.
- **The update window no longer hides the About / Help / Voice Commands windows**
  behind it.

## [0.7.0] - 2026-08-15

This release is about getting TalkType onto your computer the normal way and
making the very first run work reliably — whichever Linux you use.

### Install it the way your distribution expects
- **New `.deb` package** for Debian, Ubuntu and Linux Mint: `sudo apt install ./talktype_*_amd64.deb`
- **New `.rpm` package** for Fedora, RHEL and openSUSE: `sudo dnf install ./talktype-*.x86_64.rpm`
- **The Arch (AUR) package now pulls in the library it needs** so it no longer fails to start on a clean install.
- Each package adds a `talktype` command and an Applications menu entry and brings its own copies of the helper tools — nothing to install by hand first.

### Less than half the size it was
- **The download dropped from 306 MB to 135 MB.** TalkType was shipping PyTorch, an enormous library it never actually used — transcription runs on faster-whisper, and GPU acceleration uses its own CUDA libraries. Removing it cut ~170 MB with no change to how anything works, on CPU or GPU.

### A first run that just works, then gets out of your way
- **TalkType now starts itself when you log in.** After setup it's simply there in your system tray, ready — no launching it by hand. You can turn this off in Preferences → General, and the final setup screen tells you so.
- **The restart reminder waits until the end.** Some permissions only take effect after a restart, but you're now reminded once at the very end of setup — with a **"Restart Now"** button — instead of being nudged to reboot in the middle, before setup is finished.
- **On GNOME, the panel icon now appears on its own** after you install the extension and restart. Previously the extension was installed but never actually switched on, so there was no icon and no clue why.
- **If setup can't finish cleanly, it tells you** and doesn't leave you stuck part-way through.

### Fixed
- **TalkType would not start at all on Fedora.** The tray was built against an old system library Fedora doesn't ship, so the app quit the moment it launched. It now uses the current, maintained library every supported distribution provides.
- **Hotkeys did nothing on Fedora, with no error to explain why.** First-run setup checked only whether TalkType could *type*. On Fedora that permission is granted automatically while permission to *read your keyboard* is not, so setup was skipped and no key could ever be detected — while the app looked healthy. Setup now checks both, and says so plainly if it can't read the keyboard instead of failing silently.
- **Setup told you to log out when that often isn't enough.** A background session can keep old permissions alive, so hotkeys would still do nothing. It now tells you to restart, everywhere it asks.
- **Setup could run all over again after a restart.** Rebooting from the last setup screen could leave onboarding un-finished, so the next launch started the whole wizard over. It now records that setup is done before restarting.
- **Scrolling the mouse wheel over the model dropdown changed your model** — and could land on a GPU-only model and pop an error you never asked for. The dropdown now ignores the wheel, and the model you pick is always the one you get.
- **About and "Check for Updates" windows ignored your dark theme**, showing up light while the rest of the app was dark. They now match.
- **The setup window was taller than some screens**, leaving its buttons unreachable. It now fits the screen and scrolls when it has to.
- **Preferences could not be scrolled with the mouse wheel** over dropdowns and sliders.
- **The splash icon was missing** on installed (non-AppImage) versions.
- **"Check for Updates" flashed its result and vanished** before it could be read. The result window now stays until dismissed.
- **Some installed files had the wrong permissions**, so TalkType could fail to start for anyone but the user who installed it.

## [0.6.2] - 2026-08-14

### The recording indicator can now look four different ways
- **Alongside the original glowing orb, there are three new styles that react to your voice as you speak**: a Waveform, Frequency bars, and a Radial burst. Choose one under Preferences → Audio.
- **One colour control governs every style.** Match your desktop's accent colour, or pick your own from a colour picker that opens the moment you choose "Custom colour". The classic cyan is the first preset swatch, and a "Reset to classic cyan" link puts the original colour back in one click.
- **A soft dark backing** (Off / Soft / Medium / Strong) keeps the waveform, bars and radial styles readable over any wallpaper. The orb has its own background and ignores this — the setting greys out while the orb is selected, so that's clear at a glance.
- **A sensitivity slider** tunes how strongly the indicator reacts to your voice.
- All of these apply instantly, with no need to restart dictation.

### Your dictation stays private in the log
- **TalkType no longer writes what you dictate to its log file.** Previously every transcription was saved to the log in plain text, quietly building up a record on disk of everything you had said. The log now records only that a transcription happened and how long it was — never the words themselves.
- If you are troubleshooting a dictation problem, full logging can be turned back on under Preferences → Advanced → Privacy. It asks you to confirm in a warning first, so switching it on is always a deliberate, informed choice.
- A **"Clear log now"** button wipes the existing log whenever you want.
- The Text Injection help text no longer claims the app can avoid typing into password fields — no Linux app can reliably detect them on Wayland, so the honest guidance is simply to avoid dictating into them.

### Settings apply without restarting the service
- **Changing settings in Preferences no longer restarts dictation.** Almost any change used to trigger a ten-second model reload; now only changing the model or device does. Everything else — the indicator, punctuation, timeouts and the rest — takes effect on your very next dictation.

### Fixed
- **The recording indicator ignored its position setting.** The dictation service was being started from two different places that did not agree on settings, so where the indicator appeared depended on which one launched it. It now launches from a single place and honours the position you chose.

## [0.6.1] - 2026-08-13

### Preferences styling
- **The Preferences window's stylesheet had never actually applied.** It was attached in a way that could not reach the widgets inside the window, so around 70 style rules covering buttons, tabs, text fields, switches, sliders and scrollbars did nothing at all — the window fell back to the plain system theme. Those styles now render.
- **Accent colours follow your desktop.** They were fixed to a blue that clashed with any system not using a blue accent. Highlights — the selected tab, section headings, focused text fields, switches and checkboxes — now use whatever accent colour your desktop is set to.

## [0.6.0] - 2026-08-13

The largest release so far: three months of reliability work across dictation,
settings, and text handling. Several features that were advertised but quietly
did nothing now actually work.

**GNOME users should update the extension too.** Its version number was stuck at
5 through five rewrites, so the app told everyone they were up to date and never
offered the update. This release fixes that, and the newer extension is required
for the Claude Desktop paste fix below to reach you.

### Every dropdown in the app was unusable on Wayland
- **Dropdowns closed the instant you released the mouse button** — so you could not pick a model on the first-run setup screen, or change the model, device, language or hotkeys in Preferences. Only click-and-drag selected anything, which is not how anyone expects a dropdown to work. TalkType forced itself onto XWayland for the whole app, and under XWayland a GTK dropdown never latches open on a plain click. XWayland is now used only by the part that needs it (the recording indicator, which cannot position itself without it), so the windows you actually click run natively on Wayland.
- **Dropdown lists opened on top of the row above them** instead of below the button, which read as a rendering glitch. They now drop down as an ordinary list.

### Recording indicator position, and other desktops
- **The recording indicator's position could not be changed on Wayland.** The position dropdown and both offset boxes were greyed out with a note saying positioning "requires the GNOME extension". That was never true — positioning has nothing to do with the extension, and it works on any desktop that has XWayland, including KDE Plasma. The controls are now enabled whenever positioning genuinely works, and the warning only appears when it genuinely cannot.
- **"Restart Info" stayed clickable on desktops that are not GNOME**, where it explained how to press Alt+F2 and restart GNOME Shell — advice for software that isn't running. It is now greyed out alongside Install and Uninstall.
- **TalkType no longer refuses to dictate on systems without XWayland.** The dictation service asks for XWayland so the indicator can position itself, but now falls back to native Wayland instead of failing to start, giving a centred indicator rather than no dictation at all.

### Model and preset choices
- **The first-run setup screen offered fewer models than Preferences.** "Base" was missing from setup entirely, so new users picked from four models and later found five, with nothing indicating one had been hidden. Both screens now build from one list, and the models are ordered by size in both.
- **"Battery Saver" could never show as the selected preset in the GNOME menu.** It and "Fastest" are both tiny/CPU, and the extension compared only the model and device, so the first match always won. The setting applied correctly; only the dot was wrong. The extension now distinguishes them the same way the tray does.

### Onboarding
- **Added an "Open Preferences" button to the final setup screen**, so the settings are one click away instead of only being described in text.

### Dictation reliability
- **Hotkey silently stopped working after a USB blip, wireless-receiver glitch, or resume from suspend** — keyboards were detected once at startup and never again, so a device that dropped and came back was gone for the life of the process. TalkType now rescans every 3 seconds while idle. It never rescans mid-recording, which would let the hotkey leak into the app you're typing into.
- **System-wide keyboard and mouse lockups during recording** — TalkType takes an exclusive grab on input devices while recording. Several error paths lost the handle needed to release them, leaving the keyboard dead everywhere until the app was killed. Every exit path now releases, including when the microphone is busy, unplugged, or was changed over USB.
- **A keyboard unplugged mid-sentence left recording stuck on**, which also disabled the auto-timeout and the stranded-grab safety net.
- **The hotkey test in Preferences detected nothing** and pegged a CPU core while open.
- **The hotkey test could permanently disable GNOME's Alt+F8 / Alt+F7** window resize and move shortcuts — as a saved setting that survived reboots, with nothing pointing back at TalkType as the cause.
- **The hotkey dropdown showed F8 while the saved hotkey was actually blank**, so clicking OK saved a blank hotkey. The one screen you'd use to fix dead dictation couldn't fix it.

### Your settings stay put
A whole class of "TalkType reset itself and dictation stopped working" faults:
- **A microphone with a quote mark in its name wiped every setting**, hotkey included. A blank hotkey means dictation is silently dead.
- **An unreadable config file (wrong permissions) was mistaken for a damaged one and overwritten** — turning a working setup into a fresh install with nothing to recover from. An unreadable file is no longer treated as a damaged one.
- **Repairing a damaged config destroyed the backup it had just recovered from.**
- **A single transient read error could cache defaults and write them over a perfectly good settings file** five seconds after launch.
- **Custom commands were deleted** on any Apply in Preferences if the commands file was damaged.
- **TalkType's three processes could truncate each other's settings file** when writing at the same time.
- **Tray changes made while Preferences was open** are no longer discarded on Apply/OK.

### Text corrections
- `report.pdf` no longer becomes "report. Pdf" — filenames are protected.
- "meet at 9 a.m. tomorrow" no longer becomes "9 AM Tomorrow".
- "8 a.m. Tuesday" is no longer split into two sentences before a proper noun.
- "use it, i.e. the new one" no longer becomes "I.e.".
- "2024 was a good year" no longer becomes "2024 Was a good year" — same for prices, and email addresses are no longer capitalized.
- "here is the plan:" no longer collects a stray full stop.
- Silence no longer produces a lone ".".
- **"Ensure period at end of sentences" now works when unchecked** — it previously did nothing at all.
- Any dictation ending in "subscribe" no longer loses the word.
- Decimals, prices and abbreviations ("3.5", "$19.99", "U.S.") stay intact.
- After "undo that", the next words keep their capitals — "NASA" no longer becomes "nASA".
- **"Undo that" could delete an extra sentence** — often the whole dictation — when the previous sentence ended inside a quote or bracket. Those backspaces land in your live document, so this one mattered.

### Voice commands
- **A backslash in a custom command's replacement crashed every dictation**, even when that command's trigger phrase wasn't spoken.
- **One custom command could rewrite another's output.** Replacements are now final.
- **"delete everything" / "clear all"** wipes the whole input field.
- **"delete last 3 words"** — counted undo, using digits or number words, for words, sentences and paragraphs.
- "em dash" works again; "return" and "tab" no longer false-trigger mid-sentence.

### Text injection
- **Injection reported success even when it failed.** The two worst outcomes of that: the wrong text landing in your document, and the undo buffer recording text that never arrived — so a later "undo that" backspaced over writing you'd typed yourself.
- **On an X11 login, paste could insert whatever you'd copied earlier** — a URL, a password, a whole document — while reporting success. It now falls back to typing.
- **A failure partway through a long dictation** no longer leaves a partial copy followed by a complete second copy.
- **Claude Desktop paste works again.** An Electron update started rejecting synthetic Ctrl+V; affected apps are now routed to a fast type path. Within it, "tab" no longer submits the chat mid-dictation.
- Pasting no longer waits on two unbounded 25-second timeouts between releasing the hotkey and the text appearing.

### Features that didn't actually work
- **Changing the model from the tray or GNOME menu did nothing.** The change was written to a file the app has never read, and was gone by the restart it told you was required.
- **Model downloads failed in full if any single file failed** — including `README.md`, which is never loaded. A flaky fetch of a text file discarded a finished multi-gigabyte download.
- **"Battery Saver" could never show as the active preset** — the dot always jumped back to "Fastest".
- **"Launch at login" wrote its autostart file the moment you ticked the box**, so Cancel left it behind and the checkbox disagreed with reality from then on. It now applies on save.
- **Closing the download window left downloads running** with no window, no progress, and no way to stop them.
- **A failed CUDA download deleted the CUDA you already had** — including failures that happened before anything was downloaded.
- The tray no longer freezes for a second on Restart Service, or when switching presets.

### Security and integrity
- **The update check installed an AppImage whose checksum was missing** from a release that did publish checksums.
- **The GNOME extension installed an unverifiable zip** the same way — and that extension is JavaScript running in your shell. A missing checksum file (true of every release through v0.5.16) is now distinguished from a failed lookup.

### Under the hood
- The release build now fails loudly instead of shipping an AppImage whose PyTorch crashes on every machine without an NVIDIA card.
- Test suite grew from 223 to 361 tests.
- Removed two dead, non-working build scripts; the release toolchain is now tracked in the repository.

## [0.5.16] - 2026-05-09

### Bug Fixes
- **Words vanishing from longer dictations** — Disabled Whisper's VAD pre-filter that was trimming speech onsets after natural sentence-ending pauses. Phrases like "Eight hours later, we were standing in a kitchen with collapsed ceilings" no longer disappear after pauses. See [faster-whisper#925](https://github.com/SYSTRAN/faster-whisper/issues/925).
- **"period of time" mangled into "period. Of time"** — Command words like *period*, *comma*, *return*, *dash*, *quote* no longer get corrupted when used as ordinary English nouns. Phrases like "period of time", "in return", "tax return", "dash of salt", "great quote", and "comma operator" now transcribe correctly.
- **Standalone "i" not capitalized mid-sentence** — Whisper transcribes the pronoun "I" as lowercase when it appears mid-sentence; TalkType now automatically capitalizes it (also catches "i'll", "i'm", "i've", "i'd").
- Fixed *literal return* restoring as 'newline' instead of 'return'.
- Fixed time normalization to handle hours without minutes (e.g., "11 PM").

### New Features
- **Voice Commands Dialog** — Press Ctrl+Alt+V (configurable) to see a quick reference of all voice commands. Supports combo hotkeys.
- **Quoted Replacement Support for Custom Commands** — Custom commands can now use quoted strings for literal replacement (bypasses normalization).
- Enabled GitHub Discussions on the project repository.

### Improvements
- Increased Whisper `beam_size` from 1 to 5 (faster-whisper's default) for noticeably better decoding accuracy on the trade of ~0.5s extra inference time.
- D-Bus service refactor (internal).
- Added 18 new test cases for normalization patterns.

## [0.5.15] - 2026-03-22

### New Features
- **Performance Presets** — Choose from Battery Saver, Light, Balanced, Quality, or Most Accurate via the tray menu or GNOME extension
- **Unified CUDA + Model Download** — When selecting a preset that needs both CUDA libraries and a new model, a single download window handles both with clear progress bars and explanatory text
- **Smart Model Selection** — Large-v3 model is no longer grayed out; clicking it explains what's needed (CUDA download for NVIDIA users, or "NVIDIA required" for AMD/Intel) and offers to set it up

### Improvements
- **Onboarding** — Streamlined Setup Complete page with constrained model picker width, shorter labels, and dynamic button text ("Download and Get Started!" vs "Get Started!" based on cache state)
- **Hotkey Test** — Redesigned to be phantom-proof: keys show yellow "Holding..." on press and green "✓ Working!" on release, eliminating false positives from synthetic key events
- **CUDA Crash Loop Prevention** — Performance presets now verify CUDA availability before saving device=cuda, preventing repeated crashes when CUDA libraries aren't installed
- **CPU Fallback** — If CUDA fails at runtime, TalkType automatically falls back to CPU and persists the change to config so it doesn't crash again on restart
- **Preferences Consistency** — Model and preset selection in Preferences now behaves identically to the tray menu (click-to-explain instead of grayed-out)

### Bug Fixes
- Fixed large-v3 model selection showing a blocking confirmation dialog instead of offering CUDA download
- Fixed CUDA download from tray icon using a thread-blocking function that froze the UI
- Fixed double confirmation dialog when downloading CUDA from the tray menu
- Fixed GTK auto-repeat flooding the hotkey test with hundreds of key events when holding a key
- Fixed phantom F8 key events from evdev service termination appearing as false "Working!" results

## [0.5.14] - 2026-03-21

### Improvements
- Bumped version for internal testing

## [0.5.13] - 2026-02-18

### New Features
- **Always-Active Dual Hotkeys** — F8 (hold-to-talk) and F9 (tap-to-toggle) are now both active simultaneously
- Added "Restart Service" menu item to tray and GNOME extension

### Bug Fixes
- Fixed welcome dialog hotkey test false-positive from GNOME's Alt+F8 window-resize keybinding
- Fixed crash after model download (Python 3.10 f-string compatibility)
- Fixed "Setup Complete" screen text alignment

### Performance
- Config file cached — only re-read when changed on disk
- Regex patterns in auto-punctuation engine precompiled at startup

## [0.5.12] - 2026-02-17

### Bug Fixes
- Fixed audio device compatibility for non-standard sample rates

## [0.5.11] - 2026-02-13

### Bug Fixes
- Improved dictation accuracy and hallucination filtering
- Fixed longer paragraphs losing middle sentences during dictation
