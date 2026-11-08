"""What Help says, section by section; help_dialog.py shows it, each topic
a card. The text is Pango markup with marks where cards begin ("#" the
title, "##" a feature, "###" a card; help_outline reads them). Lines are
broken by hand at about 85 characters, and flow on to the card's width.

Help is sorted by what you're doing (chatting, writing, in a terminal,
hands-free, fixing mistakes, something wrong), not by feature, so it can be
opened with a situation in mind; Tips gathers the practical advice. Labs
features are explained where they're used, marked as Labs. Each section
starts with what to do, then what's optional, then what to try if it
doesn't work. Keys are named as the
settings have them ({hold}, {toggle}), never a fixed F8.

The text is format-aware: TuxTalk ships as a Flatpak, an AppImage, and
.deb/.rpm/AUR packages, and some instructions differ (hotkeys, typing method,
file paths, updates, troubleshooting). Rather than maintain separate help files,
this branches on how TuxTalk is installed: the same runtime detection
(FLATPAK_ID / get_install_type) that onboarding, Preferences, and the updater
use. Change it once, every format stays correct.
"""
import os

from gi.repository import GLib

from .ai_download import DOWNLOAD_SIZE_TEXT as AI_SIZE_TEXT
from .config import CTRL_SPACE
from .cuda_download import DOWNLOAD_SIZE_TEXT as CUDA_SIZE_TEXT
from .whisper_vulkan import ENGINE_SIZE_TEXT

# Where the Help window's feedback link goes; the Setup Complete screen
# points to the same page.
FEEDBACK_URL = "https://github.com/ronb1964/TuxTalk/discussions"
ISSUES_URL = "https://github.com/ronb1964/TuxTalk/issues"
# A link in Help that opens Report a Problem instead of a web page.
REPORT_LINK = "tuxtalk:report"
# The section that opens with a Report a Problem button (help_dialog).
PROBLEMS_TITLE = "Problems"
# Help's Commands tab is drawn as cards (commands_cards): its intro here, the
# cards from commands_reference, then COMMANDS_EXAMPLES.
COMMANDS_TITLE = "Commands"


def install_facts():
    """Format-specific strings the help text interpolates. One place to keep the
    Flatpak vs non-Flatpak differences straight."""
    is_flatpak = bool(os.environ.get("FLATPAK_ID"))
    from .config import APP_ID as app_id
    if is_flatpak:
        cfg_dir = f"~/.var/app/{app_id}/config/tuxtalk/"
        cache_dir = f"~/.var/app/{app_id}/cache/huggingface/"
    else:
        cfg_dir = "~/.config/tuxtalk/"
        cache_dir = "~/.cache/huggingface/"
    return {
        "is_flatpak": is_flatpak,
        "cfg_dir": cfg_dir,
        "cache_dir": cache_dir,
        "log_path": cfg_dir + "tuxtalk.log",
    }


def _keys(is_flatpak):
    """(hold, toggle, paste) as Help names them: the keys in the settings, so
    Help never says F8 to someone who picked F6. On the Flatpak the desktop
    assigns the two dictation keys and there is no Paste Last shortcut."""
    if is_flatpak:
        return "your Hold-to-talk key", "your Toggle key", ""
    from .config import load_config, saved_hold_key
    cfg = load_config()
    return (saved_hold_key(), cfg.toggle_hotkey or "F9",
            getattr(cfg, "paste_last_hotkey", ""))


def _no_hotkey_tips(is_flatpak, keys_where):
    """What to try when no dictation key works, as Problems lists it. It
    differs completely by format: on the Flatpak the desktop assigns the keys.
    *keys_where* says where the keys are set."""
    if is_flatpak:
        return (
            "• On the Flatpak your keys are assigned by the desktop, not by TuxTalk.\n"
            f"• Open {keys_where}\n  and make sure a key is set for both "
            "<b>Hold to talk</b> and <b>Toggle dictation</b>.\n"
            "• If the assign prompt never appeared on first run, choose Restart Service in the\n"
            "  TuxTalk menu and it will ask again.")
    return (
        "• Open <b>Preferences → Advanced → System check</b>. If Typing setup says something\n"
        "  needs fixing, click its button. You'll be asked for your password once. TuxTalk's\n"
        "  hotkey helper then reads the dictation keys, and nothing else, with no restart\n"
        "  needed.\n"
        "• On a system without systemd the helper can't be used, and TuxTalk joins you to\n"
        "  the \"input\" group instead. Then the computer needs a restart: logging out is\n"
        "  often not enough. Typing setup says when this applies.\n"
        "• Another app may use the same key: pick another in Preferences → General and try\n"
        "  it with Test Hotkeys.\n"
        "• On many laptops and small keyboards the F-keys work only with Fn held. Hold Fn\n"
        "  with your key, or pick Right Ctrl, Right Alt, Scroll Lock or Left Ctrl+Space in\n"
        "  Preferences → General: where a laptop has them, they need no Fn.")


def _ctrl_space_line(keys):
    """Terminals' note on what Left Ctrl+Space costs, empty unless the hold or
    toggle key (the first two of *keys*) is that key."""
    if CTRL_SPACE not in keys[:2]:
        return ""
    return (
        "\n  • With <b>Left Ctrl+Space</b> as your key, Ctrl+Space (and Right Ctrl+Space)\n"
        "    doesn't reach your apps while dictation is on, so code completion on it waits\n"
        "    until you turn dictation off. On a desktop where TuxTalk can't hold keys back\n"
        "    (anything but KDE, GNOME with the TuxTalk extension, COSMIC and X11), the app\n"
        "    gets Ctrl+Space as well.")


def tabs():
    """[(title, Pango markup)] for each of Help's sections, in order. For
    Commands it is the text over the cards, which help_dialog adds."""
    values = _values()
    return [(title, text.format(**values))
            for title, text in _TABS]


def _mouse_tip(hold: str, is_flatpak: bool) -> str:
    """Tips' line on the mouse grid, with its line break; none on the
    Flatpak, which has no TuxTalk Labs to turn it on in."""
    if is_flatpak:
        return ""
    return (f"  • No mouse? Hold <b>{hold}</b>, say <b>mouse grid</b>, let go, then say a "
            "number and\n    <b>click</b> (Writing → The mouse).\n")


def _values():
    """What the sections' {placeholders} say on this computer."""
    f = install_facts()
    is_flatpak = f["is_flatpak"]
    keys = _keys(is_flatpak)
    hold, toggle, paste_key = (GLib.markup_escape_text(k) for k in keys)

    # --- format-aware phrasing --------------------------------------------------
    keys_where = ("your desktop's <b>System Settings → Keyboard → Shortcuts</b> "
                  "(find <b>TuxTalk</b> under Applications)" if is_flatpak
                  else "<b>Preferences → General</b>")

    # A desktop shortcut or script can run the toggle itself (control.py).
    shortcut_line = ""
    if not is_flatpak:
        from .control import shortcut_command
        shortcut_line = (
            "\n\n### Your own shortcut\nA desktop shortcut or script can run "
            f"<tt>{GLib.markup_escape_text(shortcut_command())}</tt> (or --start, --stop). "
            "It starts TuxTalk if needed and waits until the speech model is loaded.")

    if is_flatpak:
        typing_para = (
            "TuxTalk types straight into the focused app through your desktop's secure\n"
            "    input portal (libei): there's no typing setup to configure, and clipboard\n"
            "    paste isn't used.")
    else:
        typing_para = (
            "Still nothing in one particular app? <b>Preferences → Advanced → Fine-tuning →\n"
            "    Text Injection</b>: Auto picks paste or typing for each app; try\n"
            "    <b>Keystroke Typing</b> or <b>Clipboard Paste</b> there.")

    no_hotkey_block = _no_hotkey_tips(is_flatpak, keys_where)

    # KDE-on-Flatpak: the per-dictation "remote control" popup.
    kde_notice = ""
    if is_flatpak and "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        kde_notice = (
            "\n\n### Turning off KDE's per-dictation popup\n"
            "KDE shows a \"Remote control session started\" popup each time TuxTalk "
            "types. To stop it, use the <b>🔔</b> button in <b>Preferences → General</b>, "
            "or: click the sliders icon on that popup → under <b>Started Remote "
            "Desktop</b> uncheck <b>Show a message in a pop-up</b> → Apply.")

    # Update guidance per install type.
    if is_flatpak:
        update_block = (
            "TuxTalk updates through Flatpak. Run <tt>flatpak update\n"
            "    io.github.ronb1964.TuxTalk</tt>, or use your software center: the built-in\n"
            "    \"Check for Updates\" points you at the same command.")
    else:
        update_block = (
            "Updating matches how you installed TuxTalk:\n"
            "   – <b>.deb / .rpm:</b> click \"Download &amp; Install\". It downloads the new\n"
            "     package and installs it through your system package manager (you'll be\n"
            "     asked for your password once), then offers to restart.\n"
            "   – <b>AppImage:</b> click \"Download &amp; Install\". It swaps in the new version\n"
            "     and restarts automatically.\n"
            "   – <b>From source:</b> Check for Updates shows the command that installs the\n"
            "     new version.\n"
            "   – <b>Arch (AUR):</b> update with your AUR helper, e.g.\n"
            "     <tt>yay -S tuxtalk-appimage</tt> or <tt>yay -S tuxtalk</tt>.")

    paste_tip = (f"Press <b>{paste_key}</b> in the right window and your last dictation is "
                 "typed again. " if paste_key else "")
    paste_line = (f"<b>{paste_key}</b> types your last dictation again, and\n    "
                  if paste_key else "")
    if is_flatpak:
        key_choice_tip = f"Pick a dictation key that's easy to reach in\n    {keys_where}."
    else:
        key_choice_tip = (
            "Pick <b>Right Alt</b>, <b>Right Ctrl</b>, <b>Scroll Lock</b> or <b>Left "
            "Ctrl+Space</b> as your key in <b>Preferences →\n    General</b>: on a laptop "
            "they need no Fn, where it has them. Where Right Alt is AltGr,\n    the list "
            "says which characters you'd lose, like @ or €.")

    # The Flatpak's shortcuts portal never hears Esc.
    esc_tip = "" if is_flatpak else (
        "### Changed your mind as you let go\n"
        f"Press <b>Esc</b> right after you let go of <b>{hold}</b>: nothing more is typed,\n"
        "and nothing is sent after it. Esc can only stop what hasn't happened yet: on a\n"
        "fast graphics card a short dictation is usually in before you can reach it.\n"
        "A line already going in finishes, and with Type at pause the phrases typed\n"
        f"before stay where they are. To take back the last one, hold <b>{hold}</b>, say\n"
        "<b>delete last sentence</b>, let go (after a line cut short, your app's own\n"
        "Ctrl+Z).\n\n")

    return {"hold": hold, "toggle": toggle, "paste_tip": paste_tip, "esc_tip": esc_tip,
            "ctrl_space_line": _ctrl_space_line(keys),
            "paste_line": paste_line, "key_choice_tip": key_choice_tip,
            "mouse_tip": _mouse_tip(hold, is_flatpak),
            "shortcut_line": shortcut_line, "typing_para": typing_para,
            "no_hotkey_block": no_hotkey_block, "kde_notice": kde_notice,
            "update_block": update_block, "log_path": f["log_path"],
            "cfg_dir": f["cfg_dir"], "cache_dir": f["cache_dir"],
            "FEEDBACK_URL": FEEDBACK_URL, "ISSUES_URL": ISSUES_URL, "REPORT_LINK": REPORT_LINK,
            "ENGINE_SIZE_TEXT": ENGINE_SIZE_TEXT, "AI_SIZE_TEXT": AI_SIZE_TEXT,
            "CUDA_SIZE_TEXT": CUDA_SIZE_TEXT}


START_HERE = '''# Dictating in Three Steps

### How to dictate
1. Click where you want the text.
2. Hold <b>{hold}</b> and talk.
3. Let go: your words are typed where the cursor is.

Say punctuation as you go: <b>period</b>, <b>comma</b>, <b>question mark</b>,
<b>new line</b>. Help's Commands tab has them all.

### Without holding a key
Tap <b>{toggle}</b> to start, talk as long as you like, and tap it again to stop.
The Hands-free section has a way to make it stop by itself when you stop talking.

### How you know it's listening
• A beep when it starts and when it stops.
• The recording indicator, an orb on the screen while it records. Change its
  look and place in <b>Preferences → Audio → Customize…</b>
• If the microphone heard nothing but silence, TuxTalk says so and types nothing.

### Indicator Follows Your Window
The recording indicator shows up in the window you're dictating into, wherever
that window is on the screen, instead of always in the same spot. It's on from
the start.
• It's placed each time you start talking, so it goes wherever your window is.
• The position you pick in <b>Preferences → Audio → Customize…</b> (bottom center, top
  right, center…) applies inside the window. The offset there is for the whole screen,
  so it's skipped.
• To keep it in one place on the screen, untick <b>Follows the window you're dictating
  into</b> in that same window.
• When TuxTalk can't tell where the window is, the indicator uses its usual
  position from <b>Preferences → Audio → Customize…</b>.
• Works on KDE, on GNOME with the TuxTalk extension, on Sway and Hyprland,
  and on X11 desktops like Cinnamon and XFCE. On a scaled screen it may sit
  a little off.

### Music, videos and calls
While you talk, TuxTalk pauses music and videos and plays them again after.
<b>Preferences → Audio → Other sound while you dictate</b> can turn other sounds
down instead (Discord calls, games), do both, or leave them alone.

### Where things are
• <b>The TuxTalk menu</b>: the tray icon, TuxTalk in GNOME's top bar, or open TuxTalk
  again from your apps. It has Preferences, Help, Recent Dictations and Fix a Word,
  and the line under the switch says which speech model runs, and where.
• <b>Preferences → General</b>: your keys, and the speech setup. On first run TuxTalk
  recommends a setup for your computer (the speech model, on your graphics card if
  it's faster) for the language you speak. "Change model, device or language" is
  there if you ever want something else.'''

CHATTING = '''# Chats and Messages

### Sending a message
Hold <b>{hold}</b>, say your message ending with <b>press enter</b>, let go:
"see you at noon, press enter" types "See you at noon." and presses Enter, which
sends it. Hold <b>{hold}</b> and say only "press enter" to just press Enter.

<b>new line</b> starts a new line and never sends anything.

### Optional: send every dictation
<b>Preferences → Advanced → Send each dictation automatically.</b> TuxTalk waits
2 seconds (you can pick none to four) and counts down over the window, then
presses Enter.
  • Tap <b>{hold}</b> during the wait to hold the send. The text stays in the box,
    to fix or to add a screenshot; press Enter yourself when you're ready.
  • Hold <b>{hold}</b> during the wait to keep talking. It's added after a space,
    and sent when you're done.
  • Switch windows during the wait, and nothing is sent.
  • In text editors, word processors and code editors it never presses Enter:
    there, Enter only starts a new line.
  • In a terminal it never presses Enter either, so nothing runs before you've
    read it. End with <b>press enter</b> to run what you said.
  • If what you said ends on a word like "and" or "the", it was probably cut
    off, so it isn't sent.

### 🧪 Changing it before it's sent
With voice editing on, right after you dictate: hold <b>{hold}</b>, say <b>edit that,
make it friendlier</b>, let go. TuxTalk selects what it just typed and rewrites it.
A message that was already sent can't be changed, so with the automatic send:
  1. Tap <b>{hold}</b> during the countdown, so it isn't sent.
  2. Hold <b>{hold}</b>, say <b>edit that, make it friendlier</b>, let go.
  3. Press Enter when it reads right.

The Writing section has more.

### On a call
<b>Preferences → Audio → Other sound while you dictate → Turn everything down</b>
lowers the call while you talk, and puts it back after. The call still hears
your microphone, so mute yourself there.

### It went into the wrong window
{paste_tip}<b>Recent Dictations</b> in the TuxTalk menu keeps your last 20:
hover over one to read it, click to copy it, then paste with Ctrl+V.'''

WRITING = '''# Documents and Email

### Punctuation and paragraphs
Say <b>period</b>, <b>comma</b>, <b>question mark</b>, <b>new line</b>, <b>new paragraph</b>
and so on. TuxTalk adds the capitals, and a period at the end if the speech model
forgot one. Help's Commands tab has them all.

### Numbers
<b>Preferences → Advanced → Numbers</b>: as the speech model writes them, as digits
("twenty-five chickens" → 25 chickens), or digits from 10 up ("three cats",
"25 chickens"). With digits, "seven thirty" after at becomes 7:30 and "october
fifteenth" becomes october 15th; "one of them" and fractions stay words.

### Taking it back
Each of these is a dictation of its own, said right after the one it takes back.
Hold <b>{hold}</b>, say "meet me at noon", let go: it's typed. Changed your mind?
Hold <b>{hold}</b> again, say <b>delete last sentence</b>, let go: it's gone.
  • <b>delete last word</b>, <b>delete last sentence</b>, <b>delete last paragraph</b>
    remove what you just dictated.
  • Ctrl+Z in the app works too.
  • <b>delete everything</b> clears the whole box, text you typed yourself included.

### Filling in a form
One box at a time, each box a dictation of its own:
  1. Click into the first box.
  2. Hold <b>{hold}</b>, say "John Smith, press tab", let go: John Smith is typed,
     with no period, and the cursor moves to the next box.
  3. The next box the same way: hold <b>{hold}</b>, say "Hamilton, press tab", let go.
  4. When every box is filled, send the form as you would by hand: click its
     button, or press Enter. Ending a box with <b>press enter</b> types it with a
     period, as at the end of a sentence, then presses Enter: right for a search
     box or a message, not for a name or an email address.
  • Said in the middle of a dictation, <b>press tab</b> types a tab character
    instead, for lining things up in a document. A form box isn't the place for
    one, so in a form keep <b>press tab</b> for the end, one box per dictation.
  • <b>tab</b> on its own is just the word: "open a new tab". <b>literal press tab</b>
    types the words "press tab".
  • In Claude Desktop <b>press tab</b> types four spaces: there the Tab key would
    jump to the Send button.
  • In a password box TuxTalk types nothing. Type the password yourself, then hold
    <b>{hold}</b>, say only "press tab", let go: the cursor moves to the next box.

### Optional: cleaner text (Preferences → Advanced)
  • <b>Remove "um", "uh" and accidentally repeated words.</b> Instant, nothing
    to download.
  • <b>Fix self-corrections with AI.</b> "meet at 3, no wait, 4" types "meet at 4".
    The AI runs on your computer (a one-time {AI_SIZE_TEXT} download) and only sees
    sentences with a correction phrase. If its answer changes anything besides the
    correction, your words are typed exactly as you said them.
  • Smart quotes, the closing period and spacing are under Fine-tuning.

### TuxTalk Labs
Labs features are experimental: they work, but they're still being refined, may
change, and won't always get it right. Turn them on in <b>Preferences → Advanced →
TuxTalk Labs</b>. Voice editing, select and move, and opening apps (and windows, keys
and the mouse) are below; Type at pause is in the
Hands-free section. Your feedback decides what gets finished:
<a href="{FEEDBACK_URL}">TuxTalk Discussions</a>.

## 🧪 Voice Editing
Change text by saying how you want it changed.

### How to use it
1. Turn it on once: <b>Preferences → Advanced → TuxTalk Labs → Voice editing</b>. The first
   time, it downloads the AI ({AI_SIZE_TEXT}), unless self-corrections already did.
2. Select some text in any app: a sentence, a paragraph, a list.
3. Hold <b>Shift</b> together with your dictation key (<b>Shift+{hold}</b>) and say what you
   want. Let go of the keys when you're done talking.
   (Or tap <b>Shift+{toggle}</b> to start, say it, and tap <b>Shift+{toggle}</b> again to finish.)
4. A moment later the selection is replaced with the rewritten text.
5. Not what you wanted? Press <b>Ctrl+Z</b> in the app to get your original back.

### Without the keys: "edit that"
Right after a dictation, say the change as your next one, all in one go: hold <b>{hold}</b>,
say <b>edit that, make it shorter</b>, let go. TuxTalk selects what it just typed and
rewrites it, with no Shift needed. Saying only "edit that" gets a reminder to say what to
change, and "edit that file tonight" is typed as words.

With hands-free (double-tap {toggle}) and Type at pause, you can say a message, look it
over, say <b>edit that, make it friendlier</b>, then <b>press enter</b> to send it, all
without touching the keyboard.

### What to say
Say it in your own words; there's no fixed list. Some that work well:
  • <b>make this more formal</b>  •  <b>make it friendlier</b>
  • <b>make it sound more professional</b>
  • <b>shorten this</b>  •  <b>fix the grammar</b>  •  <b>fix the spelling</b>
  • <b>turn this into a bullet list</b>  •  <b>make this a complete sentence</b>
  • <b>translate this to Spanish</b> (or another language)

These are done exactly, without the AI, so they always come out right:
  • <b>make this all caps</b>  •  <b>make this lowercase</b>  •  <b>capitalize each word</b>
  • <b>remove the commas</b> (or periods, dashes, punctuation)  •  <b>remove the bullets</b>
  • <b>remove the word ...</b> (a word or a few words, everywhere in the selection)
  • <b>make this a bulleted list</b> or <b>a numbered list</b> (several lines: one point per line)
  • <b>indent this</b>  •  <b>remove the indent</b>  •  <b>delete this</b>

### Two or three changes at once
Join changes with "and" or "then": <b>make this formal and remove the commas</b>, or
<b>translate this to Spanish, then make it shorter</b>. They're done one after the other,
up to three. Each one has to start like a command (make, remove, add, change...), so
"make it shorter and simpler" is still one change.

Bold, italics, underline and delete press keys in the app, so they can't be combined with
other changes: do those as an edit of their own.

### Bold, italics and underline
Say <b>make this bold</b>, <b>italicize this</b> or <b>underline this</b> in LibreOffice and
other word processors, and TuxTalk presses the app's own Ctrl+B, Ctrl+I or
Ctrl+U for you.

Plain-text apps like KWrite and Kate have no formatting to apply. Font, size and color
can't be changed by voice.

### What it's good at, and what it isn't
  • Quick, clear changes to your own text work best: tone, length, grammar, lists.
  • Several lines stay several lines. If the AI runs them together when you didn't
    ask for that, TuxTalk does the edit again one line at a time.
  • It's a small AI that runs on your computer, not a big online one. Asked for
    something large, like "write a whole email from this one line", it can make
    up details. Always read the result.
  • "Shorten this" works on text that has a point to keep. A sentence that only
    talks about itself has nothing to cut down to.
  • Up to about 3,000 characters at a time (roughly a page).

### Good to know
  • Your clipboard is put back afterwards, the way you left it.
  • The rewrite is also saved in <b>Recent Dictations</b>, and Paste Last
    Dictation types it again.
  • It doesn't work in a terminal: there's no selection there to replace.
  • It uses the same small AI as <b>Fix self-corrections with AI</b>: one download of
    about {AI_SIZE_TEXT}, shared. Everything runs on this computer; nothing is sent online.

### If nothing happens
TuxTalk tells you why in a notification:
  • <i>Select the text to change first</i>: nothing was selected.
  • <i>I didn't catch what to change</i>: it heard no instruction. Hold the keys while you talk.
  • <i>The AI is still starting</i>: right after turning it on or starting TuxTalk, wait
    a few seconds.
  • <i>No change</i>: the text may already read that way ("make it formal" on a
    sentence that's formal already). Try other words.
  • <i>You switched windows</i>: the rewrite wasn't typed; it's in Recent Dictations.
## 🧪 Select and Move by Voice
Select text or move the cursor by saying so. When a dictation is <b>only</b> one of these
commands, TuxTalk does it instead of typing it: hold <b>{hold}</b>, say only <b>select the
last word</b>, let go. Said inside a longer sentence, the words are typed as usual.

Counts go up to 10 ("three" or "3").

### What you just said
These work <b>right after you dictate</b>, on what you just said.
  • <b>select that</b>  •  <b>select what I said</b>
  • <b>select the last sentence</b>  •  <b>select the last word</b>
  • <b>select the last 3 words</b>

  For any other text, use the commands below (<b>select the previous 3 words</b>,
  <b>select the line</b>) or the mouse.
### Around the cursor
  • <b>select all</b>  •  <b>select the line</b>
  • <b>select the next word</b>  •  <b>select the previous 2 words</b>
  • <b>select the next line</b>
  • <b>select to the end of the line</b>  •  <b>select to the start of the line</b>
  • <b>select to the end</b>  •  <b>select to the top</b>
### Moving
  • <b>go to the end</b>  •  <b>go to the top</b>  •  <b>go to the end of the line</b>
  • <b>go to the start of the line</b>
  • <b>go back 3 words</b>  •  <b>go forward a word</b>
  • <b>next word</b>  •  <b>previous word</b>
  • <b>go back 5 letters</b>  •  <b>go up 2 lines</b>  •  <b>go down a line</b>
  • <b>unselect</b>

### Good to know
  • With voice editing on, Shift+{hold} understands these too, so you can do it all on one
    key: Shift+{hold} <i>"select the last sentence"</i>, then
    Shift+{hold} <i>"make it more formal"</i>.
  • The commands around the cursor press the usual keys (Home, End, Ctrl+arrows), so
    they do what those keys do in each app.
  • It doesn't work in a terminal, where those keys mean other things.
  • After a select or move, <b>delete last word</b> won't run, since the cursor is
    somewhere else now. Use Ctrl+Z in the app instead.

## 🧪 Apps, Windows and Keys by Voice
Open apps, close or switch windows, snap and move them, press everyday keys, change
desktops and volume, control music, lock or take a screenshot, go to an address or search
the web, all by saying it. Turn it on in
<b>Preferences → Advanced → TuxTalk Labs → Apps, windows, keys and the mouse by voice</b>.

The Commands tab lists every one of these commands.

### How to use it
Hold <b>{hold}</b>, say <b>open Firefox</b>, let go. Firefox opens, or comes to the front
if it's already open. Nothing is typed. <b>launch</b> and <b>start</b> work like <b>open</b>,
and the name is whatever the app is called in your menu: <b>open the calculator</b>,
<b>open Konsole</b>.
  • <b>open new Firefox</b> (or <b>open another Firefox</b>) always starts a second one.
  • <b>open the browser</b>, <b>open email</b>, <b>open files</b>, <b>open the text editor</b>
    and <b>open the terminal</b> open the app your desktop uses for that.
  • Two copies of one app, say a Flatpak and a package from your distro: you get the
    distro's. Say <b>open Firefox Flatpak</b> (or <b>Snap</b>, or <b>AppImage</b>) for the
    other.
  • <b>literal open Firefox</b> types the words "open Firefox".

### Windows
Hold <b>{hold}</b>, say <b>close Firefox</b>, let go. Nothing is typed.
  • <b>close Firefox</b> closes its front window (the one you used last), <b>close all
    Firefox</b> closes every one, <b>close window</b> closes the one you're in.
  • Say the app's name as your menu shows it. For an open app, one word of it is enough:
    <b>close Chrome</b> for Google Chrome.
  • <b>switch to Firefox</b> brings it to the front. If Firefox isn't open, a note says
    so; say <b>open Firefox</b> to start it.
  • <b>minimize window</b>, <b>maximize window</b> and <b>restore window</b>.

### Snapping and moving windows
Hold <b>{hold}</b>, say <b>snap left</b>, let go. The window you're in fills that side.
  • Halves: <b>snap left</b>, <b>snap right</b>, <b>snap top</b>, <b>snap bottom</b>
  • Quarters: <b>snap top left</b>, <b>snap top right</b>, <b>snap bottom left</b>,
    <b>snap bottom right</b>
  • <b>center window</b> (or <b>centre window</b>) puts it in the middle of the screen.
  • <b>move window to desktop two</b>, <b>move window to the next desktop</b> and
    <b>move window to the other screen</b> send it there.
  • <b>minimize Firefox</b>, <b>maximize Firefox</b>, <b>restore Firefox</b>: any open app by
    name.

### The mouse
Hold <b>{hold}</b>, say <b>mouse grid</b>, let go. Numbers 1 to 9 appear over the screen.
  • Say a number, like <b>five</b>: that part fills with a new grid. Say another to go
    in further. Where the desktop's own zoom can be used (KDE's Zoom effect, or the
    whole-screen magnifier on GNOME and Cinnamon), the whole desktop zooms in about
    three times on that part, so you can see what you are aiming at. It goes back to
    normal after the click, or when the grid closes.
  • The pointer hides while the grid shows, so it doesn't cover a number, and comes
    back when the grid closes. From the second grid on, thin lines mark the parts, their
    numbers sit small in the corners, out of the way, and a dot marks where a click will
    land.
  • Say <b>left</b>, <b>right</b>, <b>up</b> or <b>down</b> to move the dot a little, or
    add a number, like <b>left 3</b>, to move it more. Say a number first.
  • Say <b>click</b> to click the middle of what the grid shows. <b>right click</b>,
    <b>double click</b> and <b>middle click</b> work the same way.
  • Or do both at once: <b>click five</b> clicks part 5 straight away. <b>move here</b>
    (or <b>move five</b>) puts the pointer there without clicking.
  • A second screen carries on the numbers from 10. <b>cancel</b> closes the grid, and so
    does <b>Esc</b>, a minute of waiting, a click, or anything else you dictate.
  • With no grid showing, <b>left click</b>, <b>right click</b>, <b>double click</b> and
    <b>middle click</b> click where the pointer already is. A bare number or <b>click</b>
    is typed as a word then. <b>literal mouse grid</b> and <b>literal left click</b> type
    the words; while a grid shows, <b>literal five</b> and <b>literal click</b> do too.
  • If TuxTalk can't put the pointer exactly there, nothing is clicked and a note says
    so. On Wayland desktops the grid needs X (XWayland), which most have, and on X11
    a desktop that draws see-through windows (compositing).

### Desktops
Hold <b>{hold}</b>, say <b>show desktop</b>, let go. Say it again and the windows come back.
  • <b>show all windows</b> spreads them out so you can pick one.
  • <b>next desktop</b>, <b>previous desktop</b>, <b>switch to desktop two</b> (one to nine).

### Sound and music
Hold <b>{hold}</b>, say <b>volume up</b>, let go.
  • <b>volume up</b>, <b>volume down</b>; add a number for more: <b>volume up 3</b>.
  • <b>mute sound</b> mutes the computer, TuxTalk's beeps too. Say <b>unmute sound</b> to undo.
  • <b>pause music</b>, <b>play music</b>, <b>next track</b>, <b>previous track</b> control the
    player that is playing, or the one TuxTalk paused while you talked.
  • <b>pause music</b> pauses every player that is playing. With two players paused,
    <b>play music</b> names them in a note and starts neither: you pick one.

### Lock, screenshot and help
Hold <b>{hold}</b>, say <b>lock screen</b>, let go. Your desktop's own lock comes up, so you
need your password to come back. TuxTalk then checks that the computer says it's locked:
the ready beep means it is. If that can't be confirmed, a note says so: check the screen.

Hold <b>{hold}</b>, say <b>take a screenshot</b>, let go. On KDE it is saved in
Pictures/Screenshots; elsewhere, where your desktop keeps them. GNOME may ask once.

Hold <b>{hold}</b>, say <b>take a screenshot of this window</b>, let go. Just the window
you're working in is saved. On GNOME and Pop!_OS your desktop's screenshot tool opens
instead: choose Window in it (on GNOME, press W), click the window, then press Enter.
Where a desktop can't take just one window, a note says so.

In a hands-free or Type at pause recording, the recording indicator may show in either
picture.

Hold <b>{hold}</b>, say <b>what can I say</b>, let go. The Voice Commands window opens with
this list.

### Keys
Hold <b>{hold}</b>, say <b>new tab</b>, let go. The key is pressed in the window you're in.
  • Tabs: <b>new tab</b>, <b>close tab</b>, <b>next tab</b>, <b>previous tab</b>,
    <b>reopen tab</b>
  • Pages: <b>go back</b>, <b>go forward</b>, <b>reload the page</b> (or <b>refresh the page</b>),
    <b>page down</b>, <b>page up</b>, <b>go to the top</b>, <b>go to the bottom</b>
  • Scrolling: <b>scroll down</b>, <b>scroll up</b>; add a number to scroll more:
    <b>scroll down 5</b>. It scrolls what's under the mouse pointer, like your own wheel.
  • Editing: <b>find on page</b>, <b>save the file</b>, <b>select all</b>,
    <b>copy the selection</b>, <b>paste it</b> (or <b>paste that</b>), <b>press escape</b>

### Addresses and web search
Hold <b>{hold}</b>, say <b>go to youtube.com</b> (or <b>open youtube dot com</b>), let go.
In a browser it uses the address bar, so it replaces the page you're on; anywhere else
your default browser opens it.

Hold <b>{hold}</b>, say <b>web search cheap tires</b>, let go: your browser's own search
engine shows the results. In a browser that replaces the page you're on too; from any
other app a new tab opens.

Put <b>literal</b> before any of these to type the words: <b>literal new tab</b>.

### Good to know
  • Only a dictation that is just the command counts. "I opened Firefox yesterday" and
    "open the door and come in" are typed as usual.
  • A name that fits no app is typed as usual, and after <b>start</b> only an app's whole
    name counts, so a sentence like "Start over." is never lost. Two apps that fit equally well: the cancel beep sounds, a note floats over your
    window naming them, and nothing opens.
  • Where it works: KDE has everything. GNOME has it with the TuxTalk extension (log out and
    back in once after updating). X11 desktops like Cinnamon and Xfce have all but
    <b>show all windows</b>. On COSMIC, Sway and Hyprland, windows and desktops can't be
    done (TuxTalk says so; use the window's own buttons), and an open app is started
    again, which most then answer by showing the window they already have. Sound, music,
    screenshots and the lock work there; a screenshot of just the window only on COSMIC.
  • In a hands-free or Type at pause recording, a command ends the recording.
  • One word alone (copy, paste, save, pause, next...) is typed as a word: people say those
    as answers in chats.
  • Closing asks to save unsaved work, just like clicking the X.
  • Apps open in a terminal too. The keys are the terminal's own there, so <b>select all</b>,
    <b>save the file</b>, <b>find on page</b>, <b>reopen tab</b>, <b>go back</b>,
    <b>go forward</b> and <b>reload the page</b> don't apply in a terminal. On GNOME
    without the TuxTalk extension, TuxTalk can't tell a terminal, so those and copy, paste
    and the tab keys aren't pressed.
  • <b>go to</b> needs a real web address, like youtube.com. Only <b>web search</b> searches.
  • An AppImage can be opened only if it has a menu entry (a tool like Gear Lever
    or AppImageLauncher makes one).
  • The command words (open, close, new tab, web search, literal…) are English, like every
    voice command here, whatever language you dictate in. App names can be said as the
    menu shows them.'''

TERMINALS = '''# Terminals and Code Editors

### Plain text, by itself
In a terminal a dictation starts without a capital and ends without a period, so
"git status" is typed as git status. In code editors (VS Code, Zed, JetBrains
IDEs…) no period is added at the end. Everywhere else, chat apps included,
punctuation is as usual. (Preferences → Advanced → Fine-tuning → Plain text in
terminals and code editors.)

### Running a command
Hold <b>{hold}</b>, say <b>git status, press enter</b>, let go: the command is typed and
run. Send each dictation automatically doesn't press Enter in a terminal, so nothing
runs before you've read it: end with <b>press enter</b> when you want it to run.

### Long commands
Snippets: say a short phrase and TuxTalk types the full text. Add one in
<b>Preferences → Words → Add Snippet</b>, then hold <b>{hold}</b>, say its phrase, let go.
Wrap the text in "quotes" to type it exactly as written, without capitals or a
period.

### What's different in a terminal
  • <b>delete everything</b> clears the line you're typing (Ctrl+E, Ctrl+U).
  • Select and move and voice editing don't work there: a terminal has no
    selection to change, and those keys mean other things.
  • Pasting uses Shift+Ctrl+V, which TuxTalk picks by itself.{ctrl_space_line}{shortcut_line}'''

HANDS_FREE = '''# Hands-free and Long Talks

### Double-tap {toggle}
Turn it on in Preferences → General ("Double-tap {toggle} for hands-free"). Then
double-tap the toggle key, talk, and when you stop talking the recording ends and
your text is typed. A short high beep confirms the double-tap. Say nothing at all
for 10 seconds and it stops without typing anything. A single tap stays a plain
on/off toggle. The seconds box beside it sets how long a pause ends it.

## 🧪 Type at Pause
Each phrase is typed as soon as you pause, while you go on talking, instead of
all of it when you stop. Nothing that was typed changes afterwards.

Turn it on in <b>Preferences → Advanced → TuxTalk Labs → Type at pause</b>. Then hold
<b>{hold}</b> (or double-tap <b>{toggle}</b>), say "see you at noon", pause: it's typed, and
you can go on talking.

### How it works
  • A pause of about a second ends a phrase, and it's typed right away.
  • Whether that pause ends a sentence is decided once your next words are heard:
    then a period goes in, or a comma, or the sentence just carries on.
  • "delete last sentence" and the other commands work on the phrase before them.

### Fine-tuning (under Type at pause → Adjust Settings)
  • <b>Pause length</b>: how long a silence ends a phrase. A dryer, a fan or a busy
    kitchen doesn't count as talking, so the pause is still heard; other people
    talking nearby does.
  • <b>Punctuation from context</b>: off types each phrase as it was heard, which is
    quicker on a slow graphics chip but puts a period at more pauses.
  • <b>Test</b>: talk and stop now and then, and nothing is typed. The bar moves with
    your voice and flashes green wherever a phrase would be typed. If it flashes while
    you're still talking, make the pause longer; if it never flashes when you pause,
    make it shorter.

## Transcribe a File

### Turning a recording into text (TuxTalk menu → Transcribe a File...)
  • Turns a recording or a video into text: MP3, M4A, OGG, WAV, FLAC, MP4, MKV, WebM
    and more.
  • Choose a file or drop one on the window. It uses the model and device you
    dictate with.
  • Copy the text, save it as a text file, or save subtitles (.srt) for a video.
  • "Show times" starts each paragraph with where it is in the file.
  • It runs on its own, so you can keep dictating while it works. Stop keeps
    what's done.
  • Nothing leaves your computer.'''

MISTAKES = '''# Fixing Mistakes

### A word it keeps getting wrong
  • Right after a dictation, hold <b>{hold}</b>, say only <b>fix that word</b>, let go:
    Fix a Word opens over your window on what you just said.
  • Or TuxTalk menu → Fix a Word..., or Preferences → Words → Fix a Word...
  • Click the wrong word (or drag across a few), type the right spelling, then
    click Always fix this. From then on it's fixed in every dictation, with any
    speech model. Handy for names, brands and technical terms, like
    "bamboo studio" → BambuStudio.

### Your Words: names and terms spelled your way
In <b>Preferences → Words</b>.
  • Whisper models are told about them before they listen.
  • Parakeet leans toward them while it listens, on the processor and the
    graphics card: a name it would write as "Tuck's talk" comes out TuxTalk.
  • Every model fixes them when they come out split up or with the wrong
    capitals: "tux talk" → TuxTalk.
  • Beside each word, list what TuxTalk hears instead ("clawed claw" for
    ClaudeClaw) and it fixes that too, whatever the model.
  • Fix a Word adds to this list: the word, and what it heard.

### Snippets
Say a short phrase and TuxTalk types the full text: "my email" →
user@example.com. Add your own in <b>Preferences → Words → Add Snippet</b>, then hold
<b>{hold}</b>, say the phrase, let go.

{esc_tip}### Getting it back
<b>Recent Dictations</b> in the TuxTalk menu keeps your last 20. {paste_tip}

### When it mishears a lot
  • Speak at a normal pace, with the microphone close and the room quiet.
  • Check the microphone: <b>Preferences → Audio</b>, Start Recording, and talk. The
    level should move well past the middle. Input Volume there is the same as your
    desktop's microphone volume.
  • The test also says when the room is noisy for your voice. The Tips section says
    what helps.
  • Use the setup TuxTalk recommends: <b>Use Recommended</b> in Preferences → General,
    or TuxTalk menu → Performance → Recommended for this computer.
  • Speak more than one language and it picks the wrong one? Preferences → General
    → Change model, device or language → Language: pick yours, or "Several
    languages" to tick the ones you speak (Whisper models).'''

PROBLEMS = '''# When Something's Wrong

### No key works (dictation never starts)
{no_hotkey_block}

### "No sound from the microphone"
The microphone sent nothing but silence: it's muted, turned all the way down, or
it's the wrong one. Check its mute switch, then <b>Preferences → Audio →
Microphone</b> and the test beside it.

### Nothing gets typed
  • Click into a text box first, and give the app a moment to take the focus.
  • <b>Preferences → Advanced → System check</b> opens by itself when typing needs
    fixing; its button fixes it.
  • {typing_para}

### Letters come out wrong
  • Capitals and small letters swapped ("hELLO"): Caps Lock is on. TuxTalk can't
    see it, so turn it off before dictating.
  • On COSMIC with more than one keyboard layout, TuxTalk types for the first one
    in COSMIC's settings; with another switched on, letters come out wrong.
  • With the key remapper kanata, TuxTalk's typing goes through kanata's rules too
    (home-row keys can turn letters into Ctrl or Shift). Add
    <tt>linux-dev-names-exclude ("TuxTalk virtual keyboard")</tt> to the defcfg
    of your kanata config and restart kanata. If the config lists its keyboards
    with <tt>linux-dev</tt>, leave TuxTalk's out of that list instead.

### Too slow
  • Use your graphics card: TuxTalk menu → Performance → Recommended for this
    computer, or Use Recommended in Preferences → General.
  • <b>Vulkan (any GPU)</b>: AMD, Intel or NVIDIA, a {ENGINE_SIZE_TEXT} download. It's the
    only way to run Parakeet on the graphics card. TuxTalk checks your graphics card is
    really faster than your processor before it switches.
  • <b>CUDA (NVIDIA)</b>: NVIDIA only, for the Whisper models, a ~{CUDA_SIZE_TEXT} download.
    Parakeet can't use it.
  • If the graphics card can't start, TuxTalk uses the processor instead, and the
    menu's line says so, for example "Parakeet on the processor (Vulkan didn't start)".
  • <b>Power Saving</b> (on by default on laptops): after 5 minutes without dictating,
    TuxTalk lets the speech model rest. Your keys keep working; the first dictation
    after a break takes a few seconds longer. Preferences → General → Power Saving.

### Performance presets (TuxTalk menu → Performance)
  • <b>Recommended for this computer:</b> the setup TuxTalk picks for your graphics
    card and language, usually Parakeet on your graphics card.
  • <b>Lightest:</b> Whisper Base on the processor, a small download and little work.
  • <b>Battery saver:</b> Whisper Tiny on the processor, and the speech model rests
    after 2 idle minutes (choosing another preset puts that back).

### Speech models (Preferences → General → Change model, device or language)
Sizes are the download for the processor; on a graphics card (Vulkan) the Whisper
models are about half that.
  • <b>Parakeet (670 MB)</b>: NVIDIA's engine. Very accurate for English and 24 European
    languages, and fast even without a graphics card. Not for Chinese, Japanese,
    Korean, Arabic and other non-European languages.
  • <b>Whisper tiny (78 MB)</b>, <b>base (148 MB)</b>, <b>small (486 MB)</b>: 99 languages,
    faster and less accurate as they get smaller. Small is a good start on the processor.
  • <b>Whisper medium (1.5 GB)</b> and <b>large-v3 (3.1 GB)</b>: more accurate and slower;
    large-v3 wants a graphics card.

<b>What works with what?</b>, in the same place, shows which models run on the
processor, Vulkan and CUDA, and which of those your computer has.

### Dictation stopped working
Choose <b>Restart Service</b> in the TuxTalk menu, or turn dictation off and on
again there. The log is {log_path}.{kde_notice}

### A settings change went wrong
<b>Preferences → Updates &amp; Backup → Earlier settings</b> lists the copies TuxTalk
saves by itself when it starts (your last 10 settings changes) and your own
backups. Restore goes back to one. <b>Save Backup</b> puts your settings, snippets,
Your Words and stats in one file, to keep or to load on another computer with
<b>Load Backup</b>; the next backup goes to the same folder.

### Updates
  • Check for updates: the TuxTalk menu, or Preferences → Updates &amp; Backup.
  • {update_block}
  • <b>Config files:</b> {cfg_dir}
  • <b>AI models:</b> {cache_dir}

### Privacy
Everything runs on your computer; nothing you say is sent anywhere. Your Stats keeps
only numbers (words, dictations, time), never the text. Recent Dictations keeps
your last 20 in memory, gone when you log out. The log has your words only if you
turn on "Log transcribed text" (Preferences → Advanced → Fine-tuning), for a bug
report; then it has what you said in a password box too, though nothing was
typed there.

### Password fields
TuxTalk doesn't type into a password box. Hold <b>{hold}</b> and talk there, let go: you
hear the low beep, "TuxTalk doesn't type into password fields" shows over the window,
and nothing is typed or kept in Recent Dictations. Type passwords on the keyboard.
Saying only <b>press enter</b> or <b>press tab</b> there still works, to log in or move on.
  • It usually knows a password box in GTK apps (most GNOME, Cinnamon and
    Xfce apps).
  • In KDE apps and Firefox only while accessibility is on (a screen reader turns it on).
  • In Chrome, Brave and apps built on them (Discord, Slack, VS Code) only if
    accessibility was already on when they started.
  • The desktop's own prompt, when an app asks for your admin password, counts too.
    A terminal asking for one (sudo) doesn't tell it: there, don't dictate.

### Bug reports and feedback
  • <b>Something not working?</b> <a href="{REPORT_LINK}">Report a problem</a> gathers
    what helps find it: your system, your settings, and the end of TuxTalk's log
    and of its hotkey helper's.
    You see all of it first, then copy it, save it, or open a GitHub issue with it.
  • <b>Report bugs:</b> <a href="{ISSUES_URL}">TuxTalk issues on GitHub</a>
  • <b>Questions, ideas, or just how it's working for you:</b>
    <a href="{FEEDBACK_URL}">TuxTalk Discussions</a>
  • <b>Log file:</b> {log_path}'''

_COMMANDS_INTRO = '''# Voice Commands Reference

There are two kinds:
• <b>Inside what you say</b>: punctuation and formatting, said as you talk. Hold your
  dictation key and say "see you tomorrow comma bring snacks period".
• <b>On their own</b>: <b>delete last sentence</b>, <b>edit that</b>…, <b>fix that word</b>. Say
  them right after the dictation they're about, as a dictation by themselves: hold
  your dictation key, say only the command, let go.

The commands are English words, whatever language you dictate in. The same list,
a tab per group, is in the TuxTalk menu → Voice Commands..., or, with <b>Apps, windows,
keys and the mouse by voice</b> ticked in TuxTalk Labs, say <b>what can I say</b>.'''

COMMANDS_EXAMPLES = '''<b>Usage Examples:</b>
Say: <i>Hello world comma how are you question mark</i>
Result: Hello world, how are you?

Say: <i>First sentence period new line Second sentence exclamation point</i>
Result: First sentence.
Second sentence!'''


TIPS = '''# Tips

### Getting good results
  • Have the microphone close to your mouth, or use a headset, above all in a noisy
    room (a fan, a dryer, a busy street). Nothing helps more.
  • Turn off noise filters on the microphone for dictation (noise suppression in
    EasyEffects, NoiseTorch or an RNNoise plugin). In TuxTalk's tests none of the
    filters helped, and RNNoise made dictation clearly worse.
  • Speak at a normal pace, the way you'd talk to someone. No need to slow down.
  • To check your room: <b>Preferences → Audio</b>, Start Recording, talk, Stop. The
    test says when the room is noisy for your voice.

### Names and unusual words
  • Add them in <b>Preferences → Words → Add Word</b>, and they're spelled your way.
  • One came out wrong? Right after, hold <b>{hold}</b>, say only <b>fix that word</b>, let go.

### Easier on the hands
  • {key_choice_tip}
{mouse_tip}  • Hands-free: turn on "Double-tap {toggle} for hands-free" in <b>Preferences →
    General</b>. Then double-tap <b>{toggle}</b> and talk without holding a key, and it
    stops when you do.

### Everyday shortcuts
  • To send a message: hold <b>{hold}</b>, say it and end with <b>press enter</b>, let go.
    "see you at noon, press enter" is typed and sent.
  • To fill in a form box by box: hold <b>{hold}</b>, say what goes in the box and end
    with <b>press tab</b>, let go (Writing → Filling in a form).
  • {paste_line}<b>Recent Dictations</b> in the TuxTalk menu keeps your last 20.'''

# Commands right after Start Here: once someone can dictate, "what can I
# say?" comes next. Problems last.
_TABS = [
    ("Start Here", START_HERE),
    (COMMANDS_TITLE, _COMMANDS_INTRO),
    ("Chatting", CHATTING),
    ("Writing", WRITING),
    ("Terminals", TERMINALS),
    ("Hands-free", HANDS_FREE),
    ("Mistakes", MISTAKES),
    ("Tips", TIPS),
    (PROBLEMS_TITLE, PROBLEMS),
]
