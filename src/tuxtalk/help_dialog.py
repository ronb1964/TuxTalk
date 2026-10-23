"""
TuxTalk Help Dialog - Shared help window for tray and preferences.

Help is sorted by what you're doing (chatting, writing, in a terminal,
hands-free, fixing mistakes, something wrong), not by feature, so it can be
opened with a situation in mind. Labs features are explained where they're
used, marked as Labs.

The content is format-aware: TuxTalk ships as a Flatpak, an AppImage, and
.deb/.rpm/AUR packages, and some instructions differ (hotkeys, typing method,
file paths, updates, troubleshooting). Rather than maintain separate help files,
this one dialog branches on how TuxTalk is installed — the same runtime
detection (FLATPAK_ID / get_install_type) that onboarding, Preferences, and the
updater use. Change it once, every format stays correct.
"""
import os

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk

from . import ui_style
from .whisper_vulkan import ENGINE_SIZE_TEXT

# Where the Help window's feedback link goes; the Setup Complete screen
# points to the same page.
FEEDBACK_URL = "https://github.com/ronb1964/TuxTalk/discussions"
ISSUES_URL = "https://github.com/ronb1964/TuxTalk/issues"


def _install_facts():
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
    from .config import load_config
    cfg = load_config()
    return (cfg.hotkey or "F8", cfg.toggle_hotkey or "F9",
            getattr(cfg, "paste_last_hotkey", ""))


def _no_hotkey_tips(is_flatpak, keys_where):
    """What to try when no dictation key works, as Problems lists it. It
    differs completely by format: on the Flatpak the desktop assigns the keys.
    *keys_where* says where the keys are set."""
    if is_flatpak:
        return (
            "• On the Flatpak your keys are assigned by the desktop, not by TuxTalk.\n"
            f"• Open {keys_where} and make sure a key is set for both "
            "<b>Hold to talk</b> and <b>Toggle dictation</b>.\n"
            "• If the assign prompt never appeared on first run, choose Restart Service "
            "in the TuxTalk menu and it will ask again.")
    return (
        "• Open <b>Preferences → Advanced → System check</b>. If Typing setup says "
        "something needs fixing, click its button. You'll be asked for your password "
        "once. TuxTalk's hotkey helper then reads the dictation keys, and nothing else, "
        "with no restart needed.\n"
        "• On a system without systemd the helper can't be used, and TuxTalk joins you "
        "to the \"input\" group instead. Then the computer needs a restart: logging out "
        "is often not enough. Typing setup says when this applies.\n"
        "• Another app may use the same key: pick another in Preferences → General and "
        "try it with Test Hotkeys.\n"
        "• On many laptops and small keyboards the F-keys work only with Fn held. Hold Fn "
        "with your key, or pick Right Ctrl, Right Alt or Scroll Lock in Preferences → "
        "General: they never need Fn.")


def show_help_dialog(tab=None):
    """Show help dialog with TuxTalk features and instructions. *tab* opens
    it on the tab whose title contains that word ("writing" from Preferences'
    TuxTalk Labs section). Returns the dialog."""
    from gi.repository import Gdk

    f = _install_facts()
    is_flatpak = f["is_flatpak"]
    hold, toggle, paste_key = (GLib.markup_escape_text(k) for k in _keys(is_flatpak))

    # --- format-aware phrasing --------------------------------------------------
    keys_where = ("your desktop's <b>System Settings → Keyboard → Shortcuts</b> "
                  "(find <b>TuxTalk</b> under Applications)" if is_flatpak
                  else "<b>Preferences → General</b>")

    # A desktop shortcut or script can run the toggle itself (control.py).
    shortcut_line = ""
    if not is_flatpak:
        from .control import shortcut_command
        shortcut_line = (
            "\n\n<b>Your own shortcut</b>\nA desktop shortcut or script can run "
            f"<tt>{GLib.markup_escape_text(shortcut_command())}</tt> (or --start, --stop). "
            "It starts TuxTalk if needed and waits until the speech model is loaded.")

    if is_flatpak:
        typing_para = (
            "TuxTalk types straight into the focused app through your desktop's "
            "secure input portal (libei): there's no typing setup to configure, "
            "and clipboard paste isn't used.")
    else:
        typing_para = (
            "Still nothing in one particular app? <b>Preferences → Advanced → Fine-tuning → "
            "Text Injection</b>: Auto picks paste or typing for each app; try "
            "<b>Keystroke Typing</b> or <b>Clipboard Paste</b> there.")

    no_hotkey_block = _no_hotkey_tips(is_flatpak, keys_where)

    # KDE-on-Flatpak: the per-dictation "remote control" popup.
    kde_notice = ""
    if is_flatpak and "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        kde_notice = (
            "\n\n<b>Turning off KDE's per-dictation popup</b>\n"
            "KDE shows a \"Remote control session started\" popup each time TuxTalk "
            "types. To stop it, use the <b>🔔</b> button in <b>Preferences → General</b>, "
            "or: click the sliders icon on that popup → under <b>Started Remote "
            "Desktop</b> uncheck <b>Show a message in a pop-up</b> → Apply.")

    # Update guidance per install type.
    if is_flatpak:
        update_block = (
            "TuxTalk updates through Flatpak. Run <tt>flatpak update "
            "io.github.ronb1964.TuxTalk</tt>, or use your software center: the "
            "built-in \"Check for Updates\" points you at the same command.")
    else:
        update_block = (
            "Updating matches how you installed TuxTalk:\n"
            "   – <b>.deb / .rpm:</b> click \"Download &amp; Install\". It downloads "
            "the new package and installs it through your system package manager "
            "(you'll be asked for your password once), then offers to restart.\n"
            "   – <b>AppImage:</b> click \"Download &amp; Install\". It swaps in the "
            "new version and restarts automatically.\n"
            "   – <b>Arch (AUR):</b> update with your AUR helper, e.g. "
            "<tt>yay -S tuxtalk-appimage</tt>.")

    paste_tip = (f"Press <b>{paste_key}</b> in the right window and your last dictation is "
                 "typed again. " if paste_key else "")

    dialog = Gtk.Dialog(title="TuxTalk Help")
    dialog.set_default_size(760, 600)

    # Set minimum size but allow resizing larger
    geo = Gdk.Geometry()
    geo.min_width = 500
    geo.min_height = 400
    dialog.set_geometry_hints(None, geo, Gdk.WindowHints.MIN_SIZE)

    dialog.set_resizable(True)  # Allow resizing
    dialog.set_modal(False)  # Non-modal so it works without parent window
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)  # Ensure it appears on top

    content = dialog.get_content_area()
    content.set_margin_top(10)
    content.set_margin_bottom(10)
    content.set_margin_start(10)
    content.set_margin_end(10)

    # Create notebook (tabbed interface)
    notebook = Gtk.Notebook()
    notebook.set_tab_pos(Gtk.PositionType.TOP)
    content.pack_start(notebook, True, True, 0)

    # Helper function to create a tab with scrolled content
    def create_tab(title, markup_text):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_margin_top(15)
        scrolled.set_margin_bottom(15)
        scrolled.set_margin_start(20)
        scrolled.set_margin_end(20)

        label = Gtk.Label()
        label.set_markup(markup_text)
        label.set_line_wrap(True)
        label.set_xalign(0)
        label.set_valign(Gtk.Align.START)

        scrolled.add(label)
        tab_label = Gtk.Label(label=title)
        tab_label.set_halign(Gtk.Align.CENTER)  # Center text within the tab
        notebook.append_page(scrolled, tab_label)

    # Each tab starts with what to do, then what's optional, then what to try
    # if it doesn't work. Tabs appear in the order they are created.
    create_tab("🚀 Start Here", f'''<span size="large"><b>Dictating in Three Steps</b></span>

1. Click where you want the text.
2. Hold <b>{hold}</b> and talk.
3. Let go: your words are typed where the cursor is.

Say punctuation as you go: <b>period</b>, <b>comma</b>, <b>question mark</b>,
<b>new line</b>. The Commands list, last in Help, has them all.

<b>Without holding a key</b>
Tap <b>{toggle}</b> to start, talk as long as you like, and tap it again to stop.
The Hands-free tab has a way to make it stop by itself when you stop talking.

<b>How you know it's listening</b>
• A beep when it starts and when it stops.
• The recording indicator, an orb on the screen while it records. Change its
  look and place in <b>Preferences → Audio → Customize…</b>
• If the microphone heard nothing but silence, TuxTalk says so and types nothing.

<b>Music, videos and calls</b>
While you talk, TuxTalk pauses music and videos and plays them again after.
<b>Preferences → Audio → Other sound while you dictate</b> can turn other sounds
down instead (Discord calls, games), do both, or leave them alone.

<b>Where things are</b>
• <b>The TuxTalk menu</b>: the tray icon, TuxTalk in GNOME's top bar, or open TuxTalk
  again from your apps. It has Preferences, Help, Recent Dictations and Fix a Word,
  and the line under the switch says which speech model runs, and where.
• <b>Preferences → General</b>: your keys, and the speech setup. On first run TuxTalk
  recommends a setup for your computer (the speech model, on your graphics card if
  it's faster) for the language you speak. "Change model, device or language" is
  there if you ever want something else.

<span size="large">🧪 <b>Indicator Follows Your Window</b></span>
The recording indicator shows up in the window you're dictating into, wherever
that window is on the screen, instead of always in the same spot.

  • Turn it on in <b>Preferences → Advanced → TuxTalk Labs</b>.
  • It's placed each time you start talking, so it goes wherever your window is.
  • The position you pick in <b>Preferences → Audio → Customize…</b> (bottom center, top
    right, center…) applies inside the window. The offset there is for the whole screen,
    so it's skipped.
  • When TuxTalk can't tell where the window is, the indicator uses its usual
    position from <b>Preferences → Audio → Customize…</b>.
  • Works on KDE, on GNOME with the TuxTalk extension, on Sway and Hyprland, and on
    X11 desktops like Cinnamon and XFCE. On a scaled screen it may sit a little off.''')

    create_tab("💬 Chatting", f'''<span size="large"><b>Chats and Messages</b></span>

<b>Sending a message</b>
Hold <b>{hold}</b>, say your message ending with <b>press enter</b>, let go:
"see you at noon, press enter" types "See you at noon." and presses Enter, which
sends it. Hold <b>{hold}</b> and say only "press enter" to just press Enter.
<b>new line</b> starts a new line and never sends anything.

<b>Optional: send every dictation</b>
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
  • If what you said ends on a word like "and" or "the", it was probably cut
    off, so it isn't sent.

<b>Changing it before it's sent</b> 🧪 Labs
With voice editing on, right after you dictate: hold <b>{hold}</b>, say <b>edit that,
make it friendlier</b>, let go. TuxTalk selects what it just typed and rewrites it.
A message that was already sent can't be changed, so with the automatic send:
  1. Tap <b>{hold}</b> during the countdown, so it isn't sent.
  2. Hold <b>{hold}</b>, say <b>edit that, make it friendlier</b>, let go.
  3. Press Enter when it reads right.
The Writing tab has more.

<b>On a call</b>
<b>Preferences → Audio → Other sound while you dictate → Turn everything down</b>
lowers the call while you talk, and puts it back after. The call still hears
your microphone, so mute yourself there.

<b>It went into the wrong window</b>
{paste_tip}<b>Recent Dictations</b> in the TuxTalk menu keeps your last 20:
hover over one to read it, click to copy it, then paste with Ctrl+V.''')

    create_tab("📝 Writing", f'''<span size="large"><b>Documents and Email</b></span>

<b>Punctuation and paragraphs</b>
Say <b>period</b>, <b>comma</b>, <b>question mark</b>, <b>new line</b>, <b>new paragraph</b>
and so on. TuxTalk adds the capitals, and a period at the end if the speech model
forgot one. The Commands list, last in Help, has them all.

<b>Numbers</b>
<b>Preferences → Advanced → Numbers</b>: as the speech model writes them, as digits
("twenty-five chickens" → 25 chickens), or digits from 10 up ("three cats",
"25 chickens"). With digits, "seven thirty" after at becomes 7:30 and "october
fifteenth" becomes october 15th; "one of them" and fractions stay words.

<b>Taking it back</b>
Each of these is a dictation of its own, said right after the one it takes back.
Hold <b>{hold}</b>, say "meet me at noon", let go: it's typed. Changed your mind?
Hold <b>{hold}</b> again, say <b>delete last sentence</b>, let go: it's gone.
  • <b>delete last word</b>, <b>delete last sentence</b>, <b>delete last paragraph</b>
    remove what you just dictated.
  • Ctrl+Z in the app works too.
  • <b>delete everything</b> clears the whole box, text you typed yourself included.

<b>Optional: cleaner text</b> (Preferences → Advanced)
  • <b>Remove "um", "uh" and accidentally repeated words.</b> Instant, nothing to download.
  • <b>Fix self-corrections with AI.</b> "meet at 3, no wait, 4" types "meet at 4".
    The AI runs on your computer (a one-time 1.1 GB download) and only sees
    sentences with a correction phrase. If its answer changes anything besides the
    correction, your words are typed exactly as you said them.
  • Smart quotes, the closing period and spacing are under Fine-tuning.

<b>TuxTalk Labs</b>
Labs features are experimental: they work, but they're still being refined, may
change, and won't always get it right. Turn them on in <b>Preferences → Advanced →
TuxTalk Labs</b>. Voice editing and select and move are below; Type at pause is on
the Hands-free tab, and the indicator that follows your window on Start Here. Your
feedback decides what gets finished: <a href="{FEEDBACK_URL}">TuxTalk Discussions</a>.

<span size="large">🧪 <b>Voice Editing</b></span>
Change text by saying how you want it changed.

<b>How to use it</b>
1. Turn it on once: <b>Preferences → Advanced → TuxTalk Labs → Voice editing</b>. The first
   time, it downloads the AI (1.1 GB), unless self-corrections already did.
2. Select some text in any app: a sentence, a paragraph, a list.
3. Hold <b>Shift</b> together with your dictation key (<b>Shift+{hold}</b>) and say what you
   want. Let go of the keys when you're done talking.
   (Or tap <b>Shift+{toggle}</b> to start, say it, and tap <b>Shift+{toggle}</b> again to finish.)
4. A moment later the selection is replaced with the rewritten text.
5. Not what you wanted? Press <b>Ctrl+Z</b> in the app to get your original back.

<b>Without the keys: "edit that"</b>
Right after a dictation, say the change as your next one, all in one go: hold <b>{hold}</b>,
say <b>edit that, make it shorter</b>, let go. TuxTalk selects what it just typed and
rewrites it, with no Shift needed. Saying only "edit that" gets a reminder to say what to
change, and "edit that file tonight" is typed as words.
With hands-free (double-tap {toggle}) and Type at pause, you can say a message, look it
over, say <b>edit that, make it friendlier</b>, then <b>press enter</b> to send it, all
without touching the keyboard.

<b>What to say</b>
Say it in your own words; there's no fixed list. Some that work well:
  • <b>make this more formal</b>  •  <b>make it friendlier</b>  •  <b>make it sound more professional</b>
  • <b>shorten this</b>  •  <b>fix the grammar</b>  •  <b>fix the spelling</b>
  • <b>turn this into a bullet list</b>  •  <b>make this a complete sentence</b>
  • <b>translate this to Spanish</b> (or another language)

These are done exactly, without the AI, so they always come out right:
  • <b>make this all caps</b>  •  <b>make this lowercase</b>  •  <b>capitalize each word</b>
  • <b>remove the commas</b> (or periods, dashes, punctuation)  •  <b>remove the bullets</b>
  • <b>remove the word ...</b> (a word or a few words, everywhere in the selection)
  • <b>make this a bulleted list</b> or <b>a numbered list</b> (several lines: one point per line)
  • <b>indent this</b>  •  <b>remove the indent</b>  •  <b>delete this</b>

<b>Two or three changes at once</b>
Join changes with "and" or "then": <b>make this formal and remove the commas</b>, or
<b>translate this to Spanish, then make it shorter</b>. They're done one after the other,
up to three. Each one has to start like a command (make, remove, add, change...), so
"make it shorter and simpler" is still one change.
Bold, italics, underline and delete press keys in the app, so they can't be combined with
other changes: do those as an edit of their own.

<b>Bold, italics and underline</b>
Say <b>make this bold</b>, <b>italicize this</b> or <b>underline this</b> in LibreOffice and
other word processors, and TuxTalk presses the app's own Ctrl+B, Ctrl+I or Ctrl+U for you.
Plain-text apps like KWrite and Kate have no formatting to apply. Font, size and color
can't be changed by voice.

<b>What it's good at, and what it isn't</b>
  • Quick, clear changes to your own text work best: tone, length, grammar, lists.
  • Several lines stay several lines. If the AI runs them together when you didn't
    ask for that, TuxTalk does the edit again one line at a time.
  • It's a small AI that runs on your computer, not a big online one. Asked for
    something large, like "write a whole email from this one line", it can make
    up details. Always read the result.
  • "Shorten this" works on text that has a point to keep. A sentence that only
    talks about itself has nothing to cut down to.
  • Up to about 3,000 characters at a time (roughly a page).

<b>Good to know</b>
  • Your clipboard is put back afterwards, the way you left it.
  • The rewrite is also saved in <b>Recent Dictations</b>, and Paste Last Dictation types it again.
  • It doesn't work in a terminal: there's no selection there to replace.
  • It uses the same small AI as <b>Fix self-corrections with AI</b>: one download of
    about 1.1 GB, shared. Everything runs on this computer; nothing is sent online.

<b>If nothing happens</b>, TuxTalk tells you why in a notification:
  • <i>Select the text to change first</i>: nothing was selected.
  • <i>I didn't catch what to change</i>: it heard no instruction. Hold the keys while you talk.
  • <i>The AI is still starting</i>: right after turning it on or starting TuxTalk, wait a few seconds.
  • <i>No change</i>: the text may already read that way ("make it formal" on a
    sentence that's formal already). Try other words.
  • <i>You switched windows</i>: the rewrite wasn't typed; it's in Recent Dictations.
<span size="large">🧪 <b>Select and Move by Voice</b></span>
Select text or move the cursor by saying so. When a dictation is <b>only</b> one of these
commands, TuxTalk does it instead of typing it: hold <b>{hold}</b>, say only <b>select the
last word</b>, let go. Said inside a longer sentence, the words are typed as usual.
Counts go up to 10 ("three" or "3").

<b>What you just said</b>: these work <b>right after you dictate</b>, on what you just said.
  • <b>select that</b>  •  <b>select what I said</b>
  • <b>select the last sentence</b>  •  <b>select the last word</b>  •  <b>select the last 3 words</b>
  For any other text, use the commands below (<b>select the previous 3 words</b>,
  <b>select the line</b>) or the mouse.
<b>Around the cursor</b>
  • <b>select all</b>  •  <b>select the line</b>
  • <b>select the next word</b>  •  <b>select the previous 2 words</b>  •  <b>select the next line</b>
  • <b>select to the end of the line</b>  •  <b>select to the start of the line</b>
  • <b>select to the end</b>  •  <b>select to the top</b>
<b>Moving</b>
  • <b>go to the end</b>  •  <b>go to the top</b>  •  <b>go to the end of the line</b>  •  <b>go to the start of the line</b>
  • <b>go back 3 words</b>  •  <b>go forward a word</b>  •  <b>next word</b>  •  <b>previous word</b>
  • <b>go back 5 letters</b>  •  <b>go up 2 lines</b>  •  <b>go down a line</b>
  • <b>unselect</b>

<b>Good to know</b>
  • With voice editing on, Shift+{hold} understands these too, so you can do it all on one
    key: Shift+{hold} <i>"select the last sentence"</i>, then Shift+{hold} <i>"make it more formal"</i>.
  • The commands around the cursor press the usual keys (Home, End, Ctrl+arrows), so
    they do what those keys do in each app.
  • It doesn't work in a terminal, where those keys mean other things.
  • After a select or move, <b>delete last word</b> won't run, since the cursor is
    somewhere else now. Use Ctrl+Z in the app instead.''')

    create_tab("💻 Terminals", f'''<span size="large"><b>Terminals and Code Editors</b></span>

<b>Plain text, by itself</b>
In a terminal a dictation starts without a capital and ends without a period, so
"git status" is typed as git status. In code editors (VS Code, Zed, JetBrains
IDEs…) no period is added at the end. Everywhere else, chat apps included,
punctuation is as usual. (Preferences → Advanced → Fine-tuning → Plain text in
terminals and code editors.)

<b>Running a command</b>
Hold <b>{hold}</b>, say <b>git status, press enter</b>, let go: the command is typed and
run. Send each dictation automatically presses Enter in a terminal too.

<b>Long commands</b>
Snippets: say a short phrase and TuxTalk types the full text. Add one in
<b>Preferences → Words → Add Snippet</b>, then hold <b>{hold}</b>, say its phrase, let go.
Wrap the text in "quotes" to type it exactly as written, without capitals or a
period.

<b>What's different in a terminal</b>
  • <b>delete everything</b> clears the line you're typing (Ctrl+E, Ctrl+U).
  • Select and move and voice editing don't work there: a terminal has no
    selection to change, and those keys mean other things.
  • Pasting uses Shift+Ctrl+V, which TuxTalk picks by itself.{shortcut_line}''')

    create_tab("🙌 Hands-free", f'''<span size="large"><b>Hands-free and Long Talks</b></span>

<b>Double-tap {toggle}</b>
Turn it on in Preferences → General ("Double-tap {toggle} for hands-free"). Then
double-tap the toggle key, talk, and when you stop talking the recording ends and
your text is typed. A short high beep confirms the double-tap. Say nothing at all
for 10 seconds and it stops without typing anything. A single tap stays a plain
on/off toggle. The seconds box beside it sets how long a pause ends it.

<span size="large">🧪 <b>Type at Pause</b></span>
Each phrase is typed as soon as you pause, while you go on talking, instead of
all of it when you stop. Nothing that was typed changes afterwards.
Turn it on in <b>Preferences → Advanced → TuxTalk Labs → Type at pause</b>. Then hold
<b>{hold}</b> (or double-tap <b>{toggle}</b>), say "see you at noon", pause: it's typed, and
you can go on talking.

<b>How it works</b>
  • A pause of about a second ends a phrase, and it's typed right away.
  • Whether that pause ends a sentence is decided once your next words are heard:
    then a period goes in, or a comma, or the sentence just carries on.
  • "delete last sentence" and the other commands work on the phrase before them.

<b>Fine-tuning</b> (under Type at pause → Adjust Settings)
  • <b>Pause length</b>: how long a silence ends a phrase.
  • <b>Noise filter</b>: raise it in a noisy room, lower it for a soft voice or a quiet
    microphone.
  • <b>Punctuation from context</b>: off types each phrase as it was heard, which is
    quicker on a slow graphics chip but puts a period at more pauses.
  • <b>Test</b>: talk and stop now and then, and nothing is typed. The bar moves with
    your voice, which should go past the green line, and it flashes green wherever a
    phrase would be typed. If it never flashes when you pause, raise the noise filter.
    If it flashes while you're still talking, lower it.

<span size="large"><b>Transcribe a File</b></span> (TuxTalk menu → Transcribe a File...)
  • Turns a recording or a video into text: MP3, M4A, OGG, WAV, FLAC, MP4, MKV, WebM
    and more.
  • Choose a file or drop one on the window. It uses the model and device you
    dictate with.
  • Copy the text, save it as a text file, or save subtitles (.srt) for a video.
  • "Show times" starts each paragraph with where it is in the file.
  • It runs on its own, so you can keep dictating while it works. Stop keeps
    what's done.
  • Nothing leaves your computer.''')

    create_tab("🩹 Mistakes", f'''<span size="large"><b>Fixing Mistakes</b></span>

<b>A word it keeps getting wrong</b>
  • Right after a dictation, hold <b>{hold}</b>, say only <b>fix that word</b>, let go:
    Fix a Word opens over your window on what you just said.
  • Or TuxTalk menu → Fix a Word..., or Preferences → Words → Fix a Word...
  • Click the wrong word (or drag across a few), type the right spelling, then
    click Always fix this. From then on it's fixed in every dictation, with any
    speech model. Handy for names, brands and technical terms, like
    "bamboo studio" → BambuStudio.

<b>Your Words: names and terms spelled your way</b>
In <b>Preferences → Words</b>.
  • Whisper models are told about them before they listen.
  • Parakeet leans toward them while it listens, on the processor and the
    graphics card: a name it would write as "Tuck's talk" comes out TuxTalk.
  • Every model fixes them when they come out split up or with the wrong
    capitals: "tux talk" → TuxTalk.
  • Beside each word, list what TuxTalk hears instead ("clawed claw" for
    ClaudeClaw) and it fixes that too, whatever the model.
  • Fix a Word adds to this list: the word, and what it heard.

<b>Snippets</b>
Say a short phrase and TuxTalk types the full text: "my email" →
user@example.com. Add your own in <b>Preferences → Words → Add Snippet</b>, then hold
<b>{hold}</b>, say the phrase, let go.

<b>Getting it back</b>
<b>Recent Dictations</b> in the TuxTalk menu keeps your last 20. {paste_tip}

<b>When it mishears a lot</b>
  • Speak at a normal pace, with the microphone close and the room quiet.
  • Check the microphone: <b>Preferences → Audio</b>, Start Recording, and talk. The
    level should move well past the middle. Input Volume there is the same as your
    desktop's microphone volume.
  • Use the setup TuxTalk recommends: <b>Use Recommended</b> in Preferences → General,
    or TuxTalk menu → Performance → Recommended for this computer.
  • Speak more than one language and it picks the wrong one? Preferences → General
    → Change model, device or language → Language: pick yours, or "Several
    languages" to tick the ones you speak (Whisper models).''')

    create_tab("🛠️ Problems", f'''<span size="large"><b>When Something's Wrong</b></span>

<b>No key works (dictation never starts)</b>
{no_hotkey_block}

<b>"No sound from the microphone"</b>
The microphone sent nothing but silence: it's muted, turned all the way down, or
it's the wrong one. Check its mute switch, then <b>Preferences → Audio →
Microphone</b> and the test beside it.

<b>Nothing gets typed</b>
  • Click into a text box first, and give the app a moment to take the focus.
  • <b>Preferences → Advanced → System check</b> opens by itself when typing needs
    fixing; its button fixes it.
  • {typing_para}

<b>Too slow</b>
  • Use your graphics card: TuxTalk menu → Performance → Recommended for this
    computer, or Use Recommended in Preferences → General.
  • <b>Vulkan (any GPU)</b>: AMD, Intel or NVIDIA, a {ENGINE_SIZE_TEXT} download. It's the
    only way to run Parakeet on the graphics card. TuxTalk checks your graphics card is
    really faster than your processor before it switches.
  • <b>CUDA (NVIDIA)</b>: NVIDIA only, for the Whisper models, a ~1.4 GB download.
    Parakeet can't use it.
  • If the graphics card can't start, TuxTalk uses the processor instead, and the
    menu's line says so, for example "Parakeet on the processor (Vulkan didn't start)".
  • <b>Power Saving</b> (on by default on laptops): after 5 minutes without dictating,
    TuxTalk lets the speech model rest. Your keys keep working; the first dictation
    after a break takes a few seconds longer. Preferences → General → Power Saving.

<b>Performance presets</b> (TuxTalk menu → Performance)
  • <b>Recommended for this computer:</b> the setup TuxTalk picks for your graphics
    card and language, usually Parakeet on your graphics card.
  • <b>Lightest:</b> Whisper Base on the processor, a small download and little work.
  • <b>Battery saver:</b> Whisper Tiny on the processor, and the speech model rests
    after 2 idle minutes (choosing another preset puts that back).

<b>Speech models</b> (Preferences → General → Change model, device or language)
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

<b>Dictation stopped working</b>
Choose <b>Restart Service</b> in the TuxTalk menu, or turn Dictation on/off off and
on. The log is {f["log_path"]}.{kde_notice}

<b>A settings change went wrong</b>
<b>Preferences → Updates &amp; Backup → Earlier settings</b> lists the copies TuxTalk
saves by itself when it starts (your last 10 settings changes) and your own
backups. Restore goes back to one. <b>Save Backup</b> puts your settings, snippets,
Your Words and stats in one file, to keep or to load on another computer with
<b>Load Backup</b>; the next backup goes to the same folder.

<b>Updates</b>
  • Check for updates: the TuxTalk menu, or Preferences → Updates &amp; Backup.
  • {update_block}
  • <b>Config files:</b> {f["cfg_dir"]}
  • <b>AI models:</b> {f["cache_dir"]}

<b>Privacy</b>
Everything runs on your computer; nothing you say is sent anywhere. Your Stats keeps
only numbers (words, dictations, time), never the text. Recent Dictations keeps
your last 20 in memory, gone when you log out. The log has your words only if you
turn on "Log transcribed text" (Preferences → Advanced → Fine-tuning), for a bug report;
then it has what you said in a password box too, though nothing was typed there.

<b>Password fields</b>
TuxTalk doesn't type into a password box. Hold <b>{hold}</b> and talk there, let go: you
hear the low beep, "TuxTalk doesn't type into password fields" shows over the window,
and nothing is typed or kept in Recent Dictations. Type passwords on the keyboard.
Saying only <b>press enter</b> or <b>press tab</b> there still works, to log in or move on.
  • It usually knows a password box in GTK apps (most GNOME, Cinnamon and Xfce apps).
  • In KDE apps and Firefox only while accessibility is on (a screen reader turns it on).
  • In Chrome, Brave and apps built on them (Discord, Slack, VS Code) only if
    accessibility was already on when they started.
  • The desktop's own prompt, when an app asks for your admin password, counts too.
    A terminal asking for one (sudo) doesn't tell it: there, don't dictate.

<b>Bug reports and feedback</b>
  • <b>Report bugs:</b> <a href="{ISSUES_URL}">TuxTalk issues on GitHub</a>
  • <b>Questions, ideas, or just how it's working for you:</b> <a href="{FEEDBACK_URL}">TuxTalk Discussions</a>
  • <b>Include:</b> your Linux distro, desktop environment, and TuxTalk version
  • <b>Log file:</b> {f["log_path"]}''')

    # Plain string, not an f-string: the brace commands below contain { and }.
    create_tab("🗣️ Commands", '''<span size="large"><b>Voice Commands Reference</b></span>

There are two kinds:
• <b>Inside what you say</b>: punctuation and formatting, said as you talk. Hold your
  dictation key and say "see you tomorrow comma bring snacks period".
• <b>On their own</b>: <b>delete last sentence</b>, <b>edit that</b>…, <b>fix that word</b>. Say
  them right after the dictation they're about, as a dictation by themselves: hold
  your dictation key, say only the command, let go.
The commands are English words, whatever language you dictate in. A shorter list
is in the TuxTalk menu → Voice Commands....

<b>Punctuation:</b>
• Say <b>comma</b> for ,
• Say <b>period</b> or <b>full stop</b> for .
• Say <b>question mark</b> for ?
• Say <b>exclamation point</b> or <b>exclamation mark</b> for !
• Say <b>semicolon</b> for ;
• Say <b>colon</b> for :
• Say <b>apostrophe</b> for '
• Say <b>quote</b> for regular "
• Say <b>open quote</b> or <b>open quotes</b> for “
• Say <b>close quote</b> or <b>close quotes</b> for ”
• Say <b>hyphen</b> or <b>dash</b> for -
• Say <b>em dash</b> for —
• Say <b>dot dot dot</b> or <b>ellipsis</b> for …

<b>Brackets &amp; Parentheses:</b>
• Say <b>open parenthesis</b> for (
• Say <b>close parenthesis</b> for )
• Say <b>open bracket</b> for [
• Say <b>close bracket</b> for ]
• Say <b>open brace</b> for {
• Say <b>close brace</b> for }

<b>Formatting:</b>
• Say <b>new line</b>, <b>newline</b>, <b>return</b>, or <b>line break</b> for a line break
• Say <b>new paragraph</b> or <b>paragraph break</b> for a double line break
• Say <b>press tab</b> for a tab; <b>tab</b> alone is the word. At the end of a dictation it
  presses the Tab key, so in a form you go to the next box
• Say <b>soft break</b> or <b>soft line</b> for three spaces
• End with <b>press enter</b> or <b>hit enter</b> to type the dictation and press Enter: it
  sends a chat message or runs a command. <b>literal press enter</b> types the words.

<b>Literal Words:</b>
To output the word instead of executing the command, say <b>literal</b> or <b>the word</b> before it:

• <b>literal period</b> or <b>the word period</b> → "period" (not .)
• <b>literal comma</b> or <b>the word comma</b> → "comma" (not ,)
• <b>literal press tab</b> → "press tab" (not a tab character)
• <b>literal new line</b> → "new line", <b>the word newline</b> → "newline" (not a line break)
• <b>literal question mark</b> → "question mark" (not ?)
• <b>literal exclamation point</b> → "exclamation point" (not !)

Works with: period, comma, colon, semicolon, question mark, exclamation point, exclamation mark, apostrophe, quote, hyphen, dash, ellipsis, dot dot dot, press tab, return, new line, newline, line break, new paragraph and paragraph break.

<b>Usage Examples:</b>
Say: <i>Hello world comma how are you question mark</i>
Result: Hello world, how are you?

Say: <i>First sentence period new line Second sentence exclamation point</i>
Result: First sentence.
Second sentence!

<b>Undo Commands:</b>
• Say <b>undo last word</b> to delete the last word you dictated
• Say <b>undo last sentence</b> to delete back to the previous sentence
• Say <b>undo last paragraph</b> to delete back to the last line break
• Also works: <b>delete last word</b>, <b>remove last word</b>, <b>delete last 3 words</b>

<b>Clearing the Whole Box:</b>
• Say <b>delete everything</b>, <b>delete all</b>, <b>clear everything</b>, <b>clear all</b>,
  <b>undo everything</b> or <b>undo all</b>
• It empties the whole box, including text TuxTalk didn't type. In a terminal it
  clears the line you're typing

<b>Fixing a Word:</b>
• Say <b>fix that word</b> right after a dictation to open Fix a Word on it

<b>Editing by Voice</b> (🧪 Labs, see the Writing tab):
• Select text, hold Shift with your dictation key, and say the change:
  <b>make this more formal</b>, <b>fix the grammar</b>, <b>make this all caps</b>
• Right after dictating: <b>edit that, make it shorter</b>
• With Select and move on: <b>select the last sentence</b>, <b>go to the end</b>, <b>unselect</b>

<b>Changing Your Mind:</b>
• Say <b>no wait</b>, <b>I mean</b>, <b>make that</b>, <b>scratch that</b> or <b>or rather</b>
  to correct yourself: "meet at 3, no wait, 4" → "meet at 4"
• Needs <b>Fix self-corrections with AI</b> in Preferences → Advanced''')

    # A passive feedback link under the tabs, so it's there whichever tab is
    # open. Help never asks for feedback on its own; this is only for people
    # who go looking. Not an action-area button: those emit a response, and
    # the response handler below closes Help.
    feedback_link = Gtk.LinkButton.new_with_label(
        FEEDBACK_URL, "Questions or feedback? Leave a note on TuxTalk Discussions")
    feedback_link.set_halign(Gtk.Align.START)
    feedback_link.set_margin_top(6)
    content.pack_start(feedback_link, False, False, 0)

    # Close button
    close_button = Gtk.Button(label="Close")
    close_button.connect("clicked", lambda w: dialog.destroy())
    dialog.add_action_widget(close_button, Gtk.ResponseType.CLOSE)

    # Connect response to destroy (for non-blocking operation)
    dialog.connect("response", lambda d, r: d.destroy())

    # Every tab in view, without GTK's scroll arrows: Mint's wider font needed
    # more than 760 px for the tab row (VM, 2026-10-22). As wide as the tabs
    # need, unless the screen is narrower, where the arrows are the lesser evil.
    notebook.set_scrollable(False)
    content.show_all()      # a hidden widget asks for no width
    needed = dialog.get_preferred_width()[0]
    screen = ui_style.workarea_width()
    if screen and needed > screen:
        notebook.set_scrollable(True)
    else:
        dialog.set_default_size(max(760, needed), 600)

    dialog.show_all()
    if tab:
        for i in range(notebook.get_n_pages()):
            if tab.lower() in (notebook.get_tab_label_text(notebook.get_nth_page(i)) or "").lower():
                notebook.set_current_page(i)
                break
    dialog.present()  # Bring to front and focus
    return dialog
