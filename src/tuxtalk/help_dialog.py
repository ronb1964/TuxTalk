"""
TuxTalk Help Dialog - Shared help window for tray and preferences.

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
from gi.repository import Gtk

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


def show_help_dialog(tab=None):
    """Show help dialog with TuxTalk features and instructions. *tab* opens
    it on the tab whose title contains that word ("labs" from Preferences'
    TuxTalk Labs section). Returns the dialog."""
    from gi.repository import Gdk

    f = _install_facts()
    is_flatpak = f["is_flatpak"]

    # --- format-aware phrasing --------------------------------------------------
    if is_flatpak:
        keys_line = ("the two keys you assigned during setup — one to "
                     "<b>Hold to talk</b>, one to <b>Toggle dictation</b>")
        keys_where = ("your desktop's <b>System Settings → Keyboard → Shortcuts</b> "
                      "(find <b>TuxTalk</b> under Applications)")
        hotkey_hold = "your <b>Hold-to-talk</b> key"
        hotkey_toggle = "your <b>Toggle-dictation</b> key"
    else:
        keys_line = "<b>F8</b> to hold-to-talk and <b>F9</b> to tap-to-toggle"
        keys_where = "<b>Preferences → General</b>"
        hotkey_hold = "<b>F8</b>"
        hotkey_toggle = "<b>F9</b>"

    # A desktop shortcut or script can run the toggle itself (control.py).
    shortcut_line = ""
    if not is_flatpak:
        from .control import shortcut_command
        from gi.repository import GLib
        shortcut_line = (
            "\n• <b>Your own shortcut:</b> a desktop shortcut or script can run "
            f"<tt>{GLib.markup_escape_text(shortcut_command())}</tt>\n  (or --start, --stop). "
            "It starts TuxTalk if needed and waits until the speech model is loaded")

    if is_flatpak:
        typing_para = (
            "TuxTalk types straight into the focused app through your desktop's "
            "secure input portal (libei) — there's no typing setup to configure, "
            "and clipboard paste isn't used.")
    else:
        typing_para = (
            "• <b>Auto (default):</b> picks for you: usually Clipboard Paste, and "
            "typing where paste doesn't work.\n"
            "• <b>Keyboard Typing:</b> types text using ydotool/wtype.\n"
            "• <b>Clipboard Paste:</b> copies to the clipboard then simulates "
            "Ctrl+V — use it if typing doesn't work in a particular app.")

    # "no hotkey works" troubleshooting differs completely by format.
    if is_flatpak:
        no_hotkey_block = (
            "<b>No hotkey works at all (dictation never starts):</b>\n"
            "• On the Flatpak your keys are assigned by the desktop, not by TuxTalk.\n"
            f"• Open {keys_where} and make sure a key is set for both "
            "<b>Hold to talk</b> and <b>Toggle dictation</b>.\n"
            "• If the assign prompt never appeared on first run, restart TuxTalk "
            "(tray → Restart Service) and it will ask again.")
    else:
        no_hotkey_block = (
            "<b>No hotkey works at all (dictation never starts):</b>\n"
            "• TuxTalk reads your keyboard directly, which requires your user "
            "account to be in the system's \"input\" group. Without it, no key can "
            "be detected.\n"
            "• Go to <b>Preferences → Advanced → Typing Setup</b> and click "
            "<b>\"Fix Typing Permissions\"</b>. You'll be asked for your admin password.\n"
            "• <b>Then restart your computer.</b> Logging out and back in is often "
            "not enough — a background session keeps the old permissions alive.\n"
            "• Most common on Fedora, where the setting that lets TuxTalk type is "
            "granted separately from the one that lets it read your keys.")

    # KDE-on-Flatpak: the per-dictation "remote control" popup.
    kde_notice = ""
    if is_flatpak and "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        kde_notice = (
            "\n\n<b>Turning off KDE's per-dictation popup:</b>\n"
            "KDE shows a \"Remote control session started\" popup each time TuxTalk "
            "types. To stop it, use the <b>🔔</b> button in <b>Preferences → General</b>, "
            "or: click the sliders icon on that popup → under <b>Started Remote "
            "Desktop</b> uncheck <b>Show a message in a pop-up</b> → Apply.")

    # Update guidance per install type.
    if is_flatpak:
        update_block = (
            "TuxTalk updates through Flatpak. Run <tt>flatpak update "
            "io.github.ronb1964.TuxTalk</tt>, or use your software center — the "
            "built-in \"Check for Updates\" points you at the same command.")
    else:
        update_block = (
            "Updating matches how you installed TuxTalk:\n"
            "   – <b>.deb / .rpm:</b> click \"Download &amp; Install\" — it downloads "
            "the new package and installs it through your system package manager "
            "(you'll be asked for your password once), then offers to restart.\n"
            "   – <b>AppImage:</b> click \"Download &amp; Install\" — it swaps in the "
            "new version and restarts automatically.\n"
            "   – <b>Arch (AUR):</b> update with your AUR helper, e.g. "
            "<tt>yay -S tuxtalk-appimage</tt>.")

    first_run_hotkey_bullet = (
        "" if is_flatpak
        else "• Tests your hotkeys (F8 and F9) to ensure they work\n")

    dialog = Gtk.Dialog(title="TuxTalk Help")
    dialog.set_default_size(650, 550)

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
        tab_label.set_hexpand(True)             # Expand to fill tab width
        notebook.append_page(scrolled, tab_label)

    # Tab: Getting Started (tabs appear in the order they are created)
    create_tab("🚀 Getting Started", f'''<span size="large"><b>Quick Start Guide</b></span>

<b>✨ TuxTalk is ready to use!</b>
The dictation service starts automatically when you launch TuxTalk.

<b>🎉 First-Run Setup</b>
On first launch, TuxTalk shows a welcome dialog that:
• Recommends a setup for your computer: the speech model, and whether to use
  your graphics card (NVIDIA, AMD or Intel), for the language you dictate in.
  "Change language, model or graphics card" lets you change any of it,
  with each choice explained
{first_run_hotkey_bullet}• Offers to install the GNOME extension (if on GNOME desktop)

<b>1. Begin Dictating</b>
Both keys work at the same time: {keys_line}.
• <b>Recording Indicator:</b> an indicator appears on screen while you're recording
  (choose its style and color in Preferences → Audio).

<b>2. Configure Settings</b>
Open the TuxTalk menu → "Preferences" to customize the AI model, language, GPU acceleration,
beeps, and more. (Dictation keys are set in {keys_where}.)

<b>3. Dictate!</b>
Press your key and speak clearly at a normal pace.
Text is inserted where your cursor is.

<b>💡 Auto-Timeout Feature:</b>
The service automatically pauses after 5 minutes of inactivity to save
system resources. Adjust this in Preferences → Advanced.

<b>Need more help?</b> Check the other tabs for detailed information.''')

    # Tab: Features
    create_tab("✨ Features", f'''<span size="large"><b>Key Features</b></span>

<b>Two Keys, Always Active</b>
• Hold-to-talk AND tap-to-toggle work simultaneously ({keys_line})
• Hands-free option: double-tap the toggle key and the recording stops by
  itself when you stop talking. If you say nothing at all for 10 seconds, it
  stops without typing anything. A single tap stays a normal on/off toggle
  (turn it on in Preferences → General, under the Toggle Hotkey)
• Visual recording indicator on screen during active recording
• Audio beeps for start/stop feedback

<b>Performance Mode Presets</b>
Quick changes from the tray menu (Performance):
• <b>Recommended for this computer:</b> the setup TuxTalk picks for your graphics card
  and language, usually Parakeet on your graphics card
• <b>Lightest:</b> Whisper Base on the processor, the smallest download and the least work
• <b>Battery saver:</b> Whisper Tiny on the processor, stops after 2 idle minutes
  (choosing another preset puts that back to 5 minutes)
After choosing one, the tray's Device line shows where it really runs.

<b>Smart Text Processing</b>
• Auto-punctuation for natural text flow
• Smart quotes (\u201c \u201d instead of " ")
• Swiss German spelling: ss instead of ß (on by itself on Swiss German systems)
• Numbers as digits: "twenty-five" becomes 25 (Preferences → Advanced)
• Plain text in terminals and code editors: no capital or closing period in a terminal,
  no closing period in a code editor (on by default)
• Auto-spacing before new text
• Optional auto-period at end of sentences
• Voice-activated undo (word, sentence, paragraph, or everything)

<b>Dictation Cleanup</b> (optional, Preferences → Advanced)
• Remove "um", "uh" and accidentally repeated words. Instant, nothing to download
• Fix self-corrections with AI: "meet at 3, no wait, 4" becomes "meet at 4"
• The AI runs on your computer (one-time 1.1 GB download) and only sees
  sentences with a correction phrase. If its answer changes anything besides
  the correction, your words are typed exactly as you said them

<b>Recent Dictations</b>
• Tray menu → Recent Dictations: your last 20 dictations
• Hover over one to read it, click to copy it, then paste with Ctrl+V
• Kept in memory only, never saved to disk, gone when you log out

<b>Graphics cards through Vulkan</b> (Preferences → General → Device)
• Choose "Vulkan (any GPU)" to run Whisper or Parakeet on an AMD, Intel or NVIDIA graphics chip
• On NVIDIA it's a light alternative to CUDA: about the same speed, a 24 MB download instead of 1.4 GB
• A one-time download, then a quick test against your processor
• TuxTalk only uses the graphics chip if it's clearly faster, and tells you the result
• Parakeet is already fast on the processor, and about 10 times faster again on a graphics card
• With any other device, Parakeet runs on the processor (CUDA can't run it)

<b>Fix a Word</b> (teach TuxTalk a word it keeps getting wrong)
• Say <b>fix that word</b> right after a dictation: Fix a Word opens over your window
  on what you just said
• Or tray menu → Fix a Word..., or Preferences → Snippets → Fix a Word...
• Pick a recent dictation, click the wrong word (or drag across a few),
  type the right spelling, then click Always fix this
• From then on it's fixed in every dictation, with any speech model
• Your fixes live in Preferences → Snippets, where you can edit or remove them
• Handy for names, brands and technical terms, like "bamboo studio" → BambuStudio

<b>Transcribe a File</b> (tray menu → Transcribe a File...)
• Turns a recording or a video into text: MP3, M4A, OGG, WAV, FLAC, MP4, MKV, WebM and more
• Choose a file or drop one on the window. It uses the model and device you dictate with
• Copy the text, save it as a text file, or save subtitles (.srt) for a video
• "Show times" starts each paragraph with where it is in the file
• It runs on its own, so you can keep dictating while it works. Stop keeps what's done
• Nothing leaves your computer

<b>Your Stats</b> (tray menu → Your Stats..., or Preferences → Stats)
• How many words you've dictated today, this week and all time
• How long you spent dictating, and roughly how much typing time that saved
• A chart of the last two weeks, and how fast you talk compared to typing
• Only the numbers are kept, on your computer. TuxTalk never saves what you said
• Set your own typing speed there, so time saved fits you
• Turn it off or reset it from the same tab

<b>Language Support</b>
• Auto-detect language from speech
• Auto-detect sometimes takes your language for another? Preferences → General →
  "Only these languages": tick the ones you speak and it picks only from those
  (Whisper models; Parakeet always picks by itself)
• Or choose one of 35 common languages by hand. Whisper models know 99 languages;
  Parakeet knows English and 24 European languages and always detects which by itself
• Great for multilingual users

<b>Audio Control</b>
• Microphone selection and testing
• Audio level monitoring
• Volume adjustment support

<b>System Integration</b>
• Launch at login option
• System tray integration (GTK or GNOME extension)
• Notification sounds (optional)
• Desktop notifications (optional)''')

    # Tab: AI Models
    create_tab("🤖 AI Models", '''<span size="large"><b>Choosing the Right AI Model</b></span>

Configure in: Preferences → General → Model
Sizes are the download for the processor. On a graphics card (Vulkan) the
Whisper models download in a smaller format, about half the size.

<b>Available Models:</b>

<b>• tiny (78 MB)</b>
  Speed: ⚡⚡⚡⚡⚡ Fastest
  Accuracy: ⭐⭐ Basic
  Best for: Quick notes, casual use

<b>• base (148 MB)</b>
  Speed: ⚡⚡⚡⚡ Fast
  Accuracy: ⭐⭐⭐ Good
  Best for: Casual dictation

<b>• small (486 MB)</b>
  Speed: ⚡⚡⚡ Balanced
  Accuracy: ⭐⭐⭐⭐ Very good
  Best for: General use in any of 99 languages

<b>• medium (1.5 GB)</b>
  Speed: ⚡⚡ Slower
  Accuracy: ⭐⭐⭐⭐⭐ Excellent
  Best for: Professional dictation

<b>• large-v3 (3.1 GB)</b>
  Speed: ⚡ Slowest
  Accuracy: ⭐⭐⭐⭐⭐⭐ Best possible
  Best for: Technical/professional work
  ⚠️ Takes 30-60 seconds to load initially, 10-20 seconds after

<b>• Parakeet (670 MB)</b> — a different engine from NVIDIA, not Whisper
  Speed: ⚡⚡⚡⚡ Fast, even without a graphics card
  Accuracy: ⭐⭐⭐⭐⭐⭐ On par with large-v3
  Best for: English and 24 European languages on any computer
  ⚠️ Does not support Chinese, Japanese, Korean, Arabic and other
  non-European languages. Use a Whisper model for those.

<b>Benefits of Larger Models:</b>
• Better recognition of uncommon words and technical terms
• More accurate with proper nouns and acronyms
• Improved punctuation and capitalization
• Better handling of accents and background noise
• Superior context awareness (e.g., "their" vs "there")
• More natural sentence structure

<b>Recommendation:</b>
The easiest way: <b>Use Recommended</b> in Preferences → General, or tray menu →
Performance → <b>Recommended for this computer</b>. Both pick the model and
device for your graphics card and language, the same as first run does.
<b>What works with what?</b>, also in Preferences → General, shows which models
run on the processor, Vulkan and CUDA, and which of those your computer has.
Use Parakeet if you dictate in English or one of the 24 European languages
it knows. It makes fewer mistakes than any Whisper model on English, and
it's fast even without a graphics card. With a graphics card (NVIDIA, AMD
or Intel), set the Device to "Vulkan (any GPU)" in Preferences for even
faster results.
For any other language, use Whisper: "small" on the processor, or
"large-v3" on a graphics card for the best accuracy.''')

    # Tab: Advanced
    create_tab("⚙️ Advanced", f'''<span size="large"><b>Advanced Features</b></span>

<b>🎮 GPU Acceleration</b>
A graphics card makes transcription several times faster. Choose it in
Preferences → General → Device:
• <b>Vulkan (any GPU):</b> AMD, Intel or NVIDIA, a 24 MB download. It's the only
  way to run Parakeet on the graphics card. TuxTalk checks your graphics card is
  really faster than your processor before it switches.
• <b>CUDA (NVIDIA):</b> NVIDIA only, for the Whisper models, a ~1.4 GB download
  that starts when you choose it. Parakeet can't use it.
• If the graphics card can't start, TuxTalk uses the processor instead and tells
  you, and the tray's Device line shows it, for example "CPU (Vulkan didn't start)".

<b>🔋 Power Management</b>
TuxTalk includes intelligent timeout to save system resources:
• <b>Auto-timeout:</b> Service stops automatically after inactivity
• <b>Configurable:</b> Set duration in Preferences → Advanced
• <b>Smart detection:</b> Timer resets when you use hotkeys
• <b>Battery friendly:</b> Reduces CPU/GPU usage when idle

<b>💾 Backup</b>
In Preferences → Updates &amp; Backup:
• <b>Save Backup:</b> your settings, snippets, Your Words and stats in
  one file, to keep or to load on another computer with <b>Load Backup</b>. If that
  computer lacks the backup's graphics device, it keeps its own.
• <b>Backup folder:</b> pick it once with <b>Change…</b>, and Save Backup and
  Load Backup open there.
• <b>Earlier settings:</b> one list of your backups in the backup folder and the
  copies TuxTalk saves by itself when it starts (your last 10 settings changes),
  so a change or an update that went wrong can be undone. Going back to an
  automatic copy leaves your stats as they are.

<b>📝 Text Injection</b>
{typing_para}

<b>🎛️ Audio Settings</b>
Fine-tune audio in Preferences → Audio tab:
• Select specific microphone
• Test audio levels
• Adjust input volume (PipeWire/PulseAudio systems)
• Enable/disable audio beeps''')

    # Tab: TuxTalk Labs (experimental features), opened directly from
    # Preferences → Advanced → TuxTalk Labs → How these work.
    if is_flatpak:
        labs_text = '''<span size="large"><b>🧪 TuxTalk Labs</b></span>

Labs is where new features are tried out before they're finished. They are
<b>experimental</b>: they work, but they're still being refined, may change, and
won't always get it right.

There are no Labs features in this version of TuxTalk yet. Voice editing needs
the regular (non-Flatpak) install, because it reads the keyboard directly.'''
    else:
        labs_text = f'''<span size="large"><b>🧪 TuxTalk Labs</b></span>

Labs is where new features are tried out before they're finished. They are
<b>experimental</b>: they work, but they're still being refined, may change, and
won't always get it right. Turn them on in <b>Preferences → Advanced → TuxTalk Labs</b>.
Your feedback decides what gets finished: <a href="{FEEDBACK_URL}">TuxTalk Discussions</a>.

<span size="large"><b>Type at Pause</b></span>
Each phrase is typed as soon as you pause, while you go on talking, instead of
all of it when you stop. Nothing that was typed changes afterwards.

<b>How it works</b>
  • A pause of about a second ends a phrase, and it's typed right away.
  • Whether that pause ends a sentence is decided once your next words are heard:
    then a period goes in, or a comma, or the sentence just carries on.
  • "delete last sentence" and the other commands work on the phrase before them.

<b>Fine-tuning</b>
  • <b>Pause length</b>: how long a silence ends a phrase.
  • <b>Noise filter</b>: raise it in a noisy room, lower it for a soft voice or a quiet
    microphone.
  • <b>Punctuation from context</b>: off types each phrase as it was heard, which is
    quicker on a slow graphics chip but puts a period at more pauses.
  • <b>Test</b>: talk and stop now and then, and nothing is typed. The bar moves with
    your voice, which should go past the green line, and it flashes green wherever a
    phrase would be typed. If it never flashes when you pause, raise the noise filter.
    If it flashes while you're still talking, lower it.

<span size="large"><b>Voice Editing</b></span>
Change text by saying how you want it changed.

<b>How to use it</b>
1. Select some text in any app: a sentence, a paragraph, a list.
2. Hold <b>Shift</b> together with your dictation key (<b>Shift+F8</b>) and say what you
   want. Let go of the keys when you're done talking.
   (Or tap <b>Shift+F9</b> to start, say it, and tap <b>Shift+F9</b> again to finish.)
3. A moment later the selection is replaced with the rewritten text.
4. Not what you wanted? Press <b>Ctrl+Z</b> in the app to get your original back.

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
  • The rewrite is also saved in <b>Recent Dictations</b>, and <b>Ctrl+Alt+P</b> types it again.
  • It doesn't work in a terminal: there's no selection there to replace.
  • It uses the same small AI as <b>Fix self-corrections with AI</b>: one download of
    about 1.1 GB, shared. Everything runs on this computer; nothing is sent online.

<b>If nothing happens</b>, TuxTalk tells you why in a notification:
  • <i>Select the text to change first</i>: nothing was selected.
  • <i>I didn't catch what to change</i>: it heard no instruction. Hold the keys while you talk.
  • <i>The AI is still starting</i>: right after turning it on or starting TuxTalk, wait a few seconds.
  • <i>The AI didn't come up with a change</i>: try saying it another way.
  • <i>You switched windows</i>: the rewrite wasn't typed; it's in Recent Dictations.

<span size="large"><b>Select and Move by Voice</b></span>
Select text or move the cursor by saying so. When a dictation is <b>only</b> one of these
commands, TuxTalk does it instead of typing it. Said inside a longer sentence, the words
are typed as usual. Counts go up to 10 ("three" or "3").

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
  • With voice editing on, Shift+F8 understands these too, so you can do it all on one
    key: Shift+F8 <i>"select the last sentence"</i>, then Shift+F8 <i>"make it more formal"</i>.
  • The commands around the cursor press the usual keys (Home, End, Ctrl+arrows), so
    they do what those keys do in each app.
  • It doesn't work in a terminal, where those keys mean other things.
  • After a select or move, <b>delete last word</b> won't run, since the cursor is
    somewhere else now. Use Ctrl+Z in the app instead.

<span size="large"><b>Indicator Follows Your Window</b></span>
The recording indicator shows up in the window you're dictating into, wherever
that window is on the screen, instead of always in the same spot.

  • Turn it on in <b>Preferences → Advanced → TuxTalk Labs</b>.
  • It's placed each time you start talking, so it goes wherever your window is.
  • The position you pick in <b>Preferences → Audio</b> (bottom center, top right, center…)
    applies inside the window. The offset there is for the whole screen, so it's skipped.
  • When TuxTalk can't tell where the window is, the indicator uses its usual
    position from <b>Preferences → Audio</b>.
  • Works on KDE, on GNOME with the TuxTalk extension, on Sway and Hyprland, and on
    X11 desktops like Cinnamon and XFCE. On a scaled screen it may sit a little off.'''
    create_tab("🧪 TuxTalk Labs", labs_text)

    # Tab: Voice Commands
    create_tab("🗣️ Voice Commands", '''<span size="large"><b>Voice Commands Reference</b></span>

Use these spoken commands during dictation to insert punctuation and formatting.
The commands are English words, whatever language you dictate in.

<b>💡 Quick Access:</b> Set a Voice Commands hotkey in Preferences → General
to pop up a compact cheat sheet anytime. Also available from the tray menu.

<b>Punctuation:</b>
• Say <b>comma</b> for ,
• Say <b>period</b> or <b>full stop</b> for .
• Say <b>question mark</b> for ?
• Say <b>exclamation point</b> or <b>exclamation mark</b> for !
• Say <b>semicolon</b> for ;
• Say <b>colon</b> for :
• Say <b>apostrophe</b> for '
• Say <b>quote</b> for regular "
• Say <b>open quote</b> or <b>open quotes</b> for \u201c
• Say <b>close quote</b> or <b>close quotes</b> for \u201d
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
• Say <b>tab</b> for a tab character (indent)
• Say <b>soft break</b> or <b>soft line</b> for three spaces

<b>Literal Words:</b>
To output the word instead of executing the command, say <b>literal</b> or <b>the word</b> before it:

• <b>literal period</b> or <b>the word period</b> → "period" (not .)
• <b>literal comma</b> or <b>the word comma</b> → "comma" (not ,)
• <b>literal tab</b> or <b>the word tab</b> → "tab" (not a tab character)
• <b>literal new line</b> → "new line", <b>the word newline</b> → "newline" (not a line break)
• <b>literal question mark</b> → "question mark" (not ?)
• <b>literal exclamation point</b> → "exclamation point" (not !)

Works with: period, comma, colon, semicolon, question mark, exclamation point, exclamation mark, apostrophe, quote, hyphen, dash, ellipsis, dot dot dot, tab, return, new line, newline, line break, new paragraph and paragraph break.

<b>Usage Examples:</b>
Say: <i>Hello world comma how are you question mark</i>
Result: Hello world, how are you?

Say: <i>First sentence period new line Second sentence exclamation point</i>
Result: First sentence.
Second sentence!

<b>Snippets:</b>
Say a short phrase and TuxTalk types the full text. Add your own in Preferences → Snippets.

Examples:
• "my email" → "user@example.com"
• "my address" → "123 Main Street, City, ST 12345"

To type a snippet exactly as written — bypassing auto-capitalization
and punctuation — wrap its text in double quotes.

<b>Your Words:</b>
Names and terms TuxTalk should spell your way, in Preferences → Snippets → Your Words.
• Whisper models are told about them before they listen
• Every model fixes them when they come out split up or with the wrong
  capitals: "tux talk" → TuxTalk
• Parakeet can't be told about words ahead of time, so for a word it hears as
  something else entirely, use Fix a Word
• Words you fix with Fix a Word are added here too

<b>Undo Commands:</b>
• Say <b>undo last word</b> to delete the last word you dictated
• Say <b>undo last sentence</b> to delete back to the previous sentence
• Say <b>undo last paragraph</b> to delete back to the last line break
• Say <b>undo everything</b> or <b>delete everything</b> to clear the field
• Also works: <b>delete last word</b>, <b>remove last word</b>, <b>clear all</b>, etc.

<b>Voice Editing</b> (experimental): select text, hold Shift+F8 and say how to change it.
See the <b>TuxTalk Labs</b> tab.

<b>Changing Your Mind:</b>
• Say <b>no wait</b>, <b>I mean</b>, <b>make that</b>, <b>scratch that</b> or <b>or rather</b>
  to correct yourself: "meet at 3, no wait, 4" → "meet at 4"
• Needs <b>Fix self-corrections with AI</b> in Preferences → Advanced

<b>Smart Features:</b>
• Auto-capitalization after sentences
• Auto-period added if a sentence has no punctuation
• Smart quote placement and spacing''')

    # Tab: Tips
    create_tab("💡 Tips", f'''<span size="large"><b>Tips &amp; Troubleshooting</b></span>

<b>Keyboard Shortcuts:</b>
• {hotkey_hold}: push-to-talk (hold to record, release to stop)
• {hotkey_toggle}: toggle recording (press once to start, again to stop)
• <b>Voice Commands shortcut:</b> set in Preferences → General to pop up the cheat sheet
• <b>Paste Last Dictation</b> (Ctrl+Alt+P): types your last dictation again where the cursor
  is, for when it went to the wrong window. Change or turn it off in Preferences → General{shortcut_line}
• Keys work globally in any application
• Change your dictation keys in {keys_where}

<b>Getting Best Results:</b>
• Speak clearly at a normal pace
• Use a quality microphone for better accuracy
• Minimize background noise
• Pause briefly at sentence ends for better punctuation

<b>Status Indicators:</b>
• Tray icon shows service status (a muted microphone means it's stopped)
• A recording indicator appears on screen during dictation (Preferences → Audio)
• Audio beeps indicate recording start/stop (can be disabled)

<b>Installation &amp; Updates:</b>
• <b>Check for updates:</b> the tray/panel menu, or Preferences → Updates &amp; Backup.
• {update_block}
• <b>Config files:</b> {f["cfg_dir"]}
• <b>AI models:</b> {f["cache_dir"]}

<b>Common Issues:</b>

<b>Hotkey not working:</b>
• Check if another app is using the same key
• Re-assign it in {keys_where}
• Ensure the service is running

{no_hotkey_block}

<b>Text not inserting:</b>
• Make sure the cursor is in a text field
• Give the target app a moment to focus before speaking

<b>Transcription too slow:</b>
• Use your graphics card: Preferences → General → Device (NVIDIA, AMD or Intel)
• Try Parakeet, or a smaller Whisper model (tiny/base/small)
• Tray menu → Performance → Recommended for this computer

<b>Service won't start:</b>
• Check logs: {f["log_path"]}
• Use Restart Service in the tray menu, or turn Dictation Service off and on{kde_notice}

<b>Bug Reports &amp; Feedback:</b>
Found a bug or have a feature request? We'd love to hear from you!

• <b>Report bugs:</b> <a href="{ISSUES_URL}">TuxTalk issues on GitHub</a>
• <b>Questions, ideas, or just how it's working for you:</b> <a href="{FEEDBACK_URL}">TuxTalk Discussions</a>
• <b>Include:</b> your Linux distro, desktop environment, and TuxTalk version
• <b>Log file:</b> {f["log_path"]}

Your feedback helps make TuxTalk better for everyone!''')

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

    dialog.show_all()
    if tab:
        for i in range(notebook.get_n_pages()):
            if tab.lower() in (notebook.get_tab_label_text(notebook.get_nth_page(i)) or "").lower():
                notebook.set_current_page(i)
                break
    dialog.present()  # Bring to front and focus
    return dialog
