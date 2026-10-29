"""Preferences, Advanced tab: the Text options, Fine-tuning, the System check
(prefs_system_check.py) and TuxTalk Labs (Type at pause in
prefs_type_at_pause.py)."""
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from .prefs_system_check import SystemCheck
from .prefs_tab import LABS_INDENT, Tab, dropdown, fold, subheading
from .prefs_type_at_pause import TypeAtPause
from .ui_style import message_dialog, show_message

# The Numbers dropdown: (label, numbers_as_digits, numbers_small_as_words).
# "As written" leaves the one-to-nine choice as it was (None), for next time.
NUMBER_CHOICES = {
    "written": ("As the speech model writes them", False, None),
    "digits": ("As digits (3 cats, 25 chickens)", True, False),
    "from-ten": ("Digits from 10 up (three cats, 25 chickens)", True, True),
}


def number_choice(config):
    """The NUMBER_CHOICES entry for numbers_as_digits and numbers_small_as_words."""
    if not config.get("numbers_as_digits", False):
        return "written"
    return "from-ten" if config.get("numbers_small_as_words", False) else "digits"


def swiss_spelling_offered(config, env=None):
    """Whether Swiss German spelling belongs in Preferences: German is the
    computer's language or one the user dictates in, or it's already on."""
    from . import language_limit
    from .recommend import system_language
    if config.get("swiss_spelling", False):
        return True
    languages = {system_language(env), config.get("dictation_language", "")}
    if config.get("language_mode", "auto") == "manual":
        languages.add(config.get("language", ""))
    languages.update(language_limit.parse(config.get("auto_languages", "")))
    return "de" in languages


class AdvancedTab(Tab):
    """The Advanced tab. The AI options download the AI the first time one is
    ticked (_toggle_ai_feature); unticked, nothing is downloaded."""

    def __init__(self, win):
        super().__init__(win)
        # Labs' Type at pause and voice editing: built outside the Flatpak only.
        self.type_at_pause = None
        self.voice_editing_check = None

    def stop(self, closing=False):
        """Stop Type at pause's live test, where its Labs entry is built;
        closing, wait until the microphone is free."""
        if self.type_at_pause is not None:
            self.type_at_pause.stop_test(closing)

    def build(self):
        """The tab's page: Text, Fine-tuning, System check and TuxTalk Labs."""
        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        grid.set_margin_start(20)
        grid.set_margin_end(20)
        grid.set_margin_top(20)
        grid.set_margin_bottom(20)

        row = self._build_text(grid, 0)
        row = self._build_fine_tuning(grid, row)
        self.system_check = SystemCheck(self.win)
        row = self.system_check.build(grid, row)
        self._build_labs(grid, row)
        return grid

    def _build_text(self, grid, row):
        """The Text section; returns the next row."""
        # The text options people turn on. The ones that already do the right
        # thing by themselves are folded into Fine-tuning below.
        text_header = Gtk.Label()
        text_header.set_markup('<b>Text</b>')
        text_header.set_xalign(0)
        text_header.set_margin_bottom(10)
        grid.attach(text_header, 0, row, 2, 1)
        row += 1

        grid.attach(self._send_automatically_option(), 0, row, 2, 1)
        row += 1

        grid.attach(self._numbers_option(), 0, row, 2, 1)
        row += 1

        fillers_check = Gtk.CheckButton(
            label="Remove \u201cum\u201d, \u201cuh\u201d and accidentally repeated words")
        fillers_check.set_active(bool(self.config.get("remove_fillers", False)))
        fillers_check.connect("toggled", lambda x: self.update_config(
            "remove_fillers", x.get_active()))
        fillers_check.set_tooltip_text(
            "\u201cUm, so the the van is ready\u201d becomes \u201cSo the van is ready\u201d.\n\n"
            "Instant, and nothing to download. It only ever removes um/uh and words\n"
            "repeated right next to each other; doubles people mean, like\n"
            "\u201cthat that\u201d or \u201chad had\u201d, are left alone.")
        grid.attach(fillers_check, 0, row, 2, 1)
        row += 1

        self.ai_corrections_check = Gtk.CheckButton(
            label="Fix self-corrections with AI "
                  "(\u201cat 3, no wait, 4\u201d \u2192 \u201cat 4\u201d)")
        # Shown ticked only if its download is actually present. If the files
        # went missing, an unticked box is the truth, and ticking it downloads
        # them again (see _on_ai_corrections_toggled).
        from tuxtalk import ai_cleanup
        self.ai_corrections_check.set_active(
            bool(self.config.get("ai_corrections", False)) and ai_cleanup.is_installed())
        self.ai_corrections_check.set_tooltip_text(
            "When you correct yourself mid-sentence (\u201cno wait\u201d, \u201cI mean\u201d, "
            "\u201cscratch that\u201d),\n"
            "a small AI model keeps only what you meant.\n\n"
            "Runs entirely on this computer, using your graphics card if you have one.\n"
            "Only sentences with a correction phrase are sent to it, and its answer is\n"
            "checked: if it changed anything besides the correction, your words are\n"
            "typed exactly as spoken instead.\n\n"
            f"Needs a one-time download of about {ai_cleanup.DOWNLOAD_SIZE_TEXT} and roughly "
            "1.5 GB of memory while on.")
        self.ai_corrections_check.connect("toggled", self._on_ai_corrections_toggled)
        grid.attach(self.ai_corrections_check, 0, row, 2, 1)
        row += 1

        ai_note = Gtk.Label(xalign=0)
        ai_note.set_markup('<span>    Runs on your computer, nothing is sent online. '
                           f'One-time download of about {ai_cleanup.DOWNLOAD_SIZE_TEXT}.</span>')
        ai_note.set_opacity(0.7)
        grid.attach(ai_note, 0, row, 2, 1)
        row += 1
        return row

    def _build_fine_tuning(self, grid, row):
        """Fine-tuning, closed: formatting, inserting text and privacy; returns the next row."""
        # Each of these already does the sensible thing; they're here for the
        # day something looks off.
        fine_fold = fold(grid, row, "Show fine-tuning", "Hide fine-tuning")
        fine_fold.set_summary("formatting, inserting text, privacy")
        fine = fine_fold.content
        row += 1
        frow = self._fine_formatting(fine, 0)
        frow = self._fine_inserting(fine, frow)
        self._fine_privacy(fine, frow)
        return row

    def _fine_formatting(self, fine, frow):
        """Fine-tuning's Formatting rows; returns the next row."""
        fine.attach(subheading("Formatting"), 0, frow, 2, 1)
        frow += 1
        quotes_check = Gtk.CheckButton(label="Use smart quotes (\u201c \u201d)")
        quotes_check.set_active(self.config["smart_quotes"])
        quotes_check.connect("toggled", lambda x: self.update_config(
            "smart_quotes", x.get_active()))
        quotes_check.set_tooltip_text(
            "Convert spoken quotes to curved 'smart quotes' instead of straight \"quotes\".\n"
            "Say 'open quote' and 'close quote' when dictating.")
        fine.attach(quotes_check, 0, frow, 2, 1)
        frow += 1

        if swiss_spelling_offered(self.config):
            fine.attach(self._swiss_spelling_check(), 0, frow, 2, 1)
            frow += 1

        space_check = Gtk.CheckButton(label="Auto-space between utterances")
        space_check.set_active(self.config["auto_space"])
        space_check.connect("toggled", lambda x: self.update_config("auto_space", x.get_active()))
        space_check.set_tooltip_text(
            "Automatically add a space before each new dictation.\n"
            "Turn off if you want to control spacing manually.")
        fine.attach(space_check, 0, frow, 2, 1)
        frow += 1

        period_check = Gtk.CheckButton(label="Ensure period at end of sentences")
        period_check.set_active(bool(self.config.get("auto_period", True)))
        period_check.connect("toggled", lambda x: self.update_config("auto_period", x.get_active()))
        period_check.set_tooltip_text(
            "Ensure every sentence ends with punctuation.\n"
            "\n"
            "The speech model usually adds periods by itself, but sometimes misses one.\n"
            "This option adds the period when it does, so punctuation stays consistent.\n"
            "\n"
            "Unchecked: no period is added at the end; spoken commands and capitals still apply\n"
            "Checked: every sentence ends with punctuation")
        fine.attach(period_check, 0, frow, 2, 1)
        frow += 1

        # No closing period in terminals and code editors, no starting capital
        # in terminals (window_classes.app_kind, normalize.plain_for_app).
        plain_check = Gtk.CheckButton(label="Plain text in terminals and code editors")
        plain_check.set_active(bool(self.config.get("adapt_to_app", True)))
        plain_check.connect("toggled", lambda x: self.update_config("adapt_to_app", x.get_active()))
        plain_check.set_tooltip_text(
            "In a terminal, a dictation starts without a capital and ends without a period,\n"
            "so \u201cgit status\u201d is typed as git status, not Git status.\n"
            "In code editors (VS Code, Zed, JetBrains IDEs...) no period is added at the end.\n\n"
            "Everywhere else, including chat apps, punctuation is as usual.")
        fine.attach(plain_check, 0, frow, 2, 1)
        frow += 1
        return frow

    def _fine_inserting(self, fine, frow):
        """How text is put in (not on the Flatpak, which always types); returns the next row."""
        inject_label = Gtk.Label(label="Text Injection 💡:", xalign=0)
        inject_label.set_tooltip_text(
            "How to insert transcribed text:\n"
            "• Auto (Smart Detection): chooses paste or typing based on the focused app\n"
            "• Keystroke Typing: simulates typing character-by-character (most reliable)\n"
            "• Clipboard Paste: uses clipboard + Ctrl+V / Shift+Ctrl+V (much faster for long "
            "text)\n"
            "\n"
            "Auto mode uses paste for terminals and normal text fields, and falls back to\n"
            "typing where paste is unreliable.\n"
            "\n"
            "TuxTalk doesn't type into password fields it can recognise: most GTK apps,\n"
            "KDE apps and Firefox with accessibility on, and the desktop's own password prompt.\n"
            "\n"
            "Clipboard Paste works in most applications:\n"
            "✓ Text editors, word processors, terminals\n"
            "✓ Web browsers, email clients\n"
            "✓ Most standard text input fields\n"
            "\n"
            "Use Keystroke Typing if paste doesn't work in specialized applications.")
        inject_combo = dropdown(Gtk.ComboBoxText())
        inject_label.set_mnemonic_widget(inject_combo)

        inject_combo.append("auto", "Auto (Smart Detection)")
        inject_combo.append("type", "Keystroke Typing")
        inject_combo.append("paste", "Clipboard Paste")
        inject_combo.set_active_id(self.config["injection_mode"])
        inject_combo.connect("changed", lambda x: self.update_config(
            "injection_mode", x.get_active_id()))
        # The tooltip is on the label: on the combo it gets in the way of the popup
        # Host only: the Flatpak always types through the libei portal, so
        # Auto/Type/Paste don't apply — don't show the selector there.
        if not os.environ.get("FLATPAK_ID"):
            fine.attach(subheading("Inserting text"), 0, frow, 2, 1)
            frow += 1
            # The dropdown sits right beside its label at its own width, so
            # the two read as one setting.
            inject_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            inject_row.pack_start(inject_label, False, False, 0)
            inject_row.pack_start(inject_combo, False, False, 0)
            fine.attach(inject_row, 0, frow, 2, 1)
            frow += 1

            # Paste puts the dictation on the clipboard, replacing whatever was
            # copied (issue #7). Off by default: a dictation left there can be
            # re-pasted if it went into the wrong window.
            restore_check = Gtk.CheckButton(
                label="Put back what I'd copied after pasting a dictation")
            restore_check.set_active(bool(self.config.get("restore_clipboard", False)))
            restore_check.connect("toggled", lambda x: self.update_config(
                "restore_clipboard", x.get_active()))
            restore_check.set_tooltip_text(
                "Pasting a dictation puts it on the clipboard, replacing whatever you'd copied.\n"
                "With this on, TuxTalk puts your copied item back half a second after pasting.\n\n"
                "Leave it off if you like to re-paste a dictation that went into the wrong "
                "window.\n"
                "Recent Dictations in the tray menu keeps your last 20 either way.\n\n"
                "Dictations are always kept out of clipboard history (like KDE's Klipper).")
            fine.attach(restore_check, 0, frow, 2, 1)
            frow += 1
        return frow

    def _fine_privacy(self, fine, frow):
        """Logging what's dictated, and clearing the log."""
        fine.attach(subheading("Privacy"), 0, frow, 2, 1)
        frow += 1
        # Transcript logging is OFF by default so nothing you dictate is written
        # to disk. Enabling it is gated by a warning (see _on_log_transcripts_toggled).
        self.log_transcripts_check = Gtk.CheckButton(
            label="Log transcribed text (for troubleshooting)")
        self.log_transcripts_check.set_active(bool(self.config.get("log_transcripts", False)))
        self.log_transcripts_check.set_tooltip_text(
            "Off by default for your privacy — TuxTalk does not write what you dictate to its "
            "log.\n"
            "Turn this on only when troubleshooting a dictation problem: it saves the full text "
            "of\n"
            "everything you dictate to TuxTalk's log file on disk, in plain text, until you turn "
            "it off."
        )
        self.log_transcripts_check.connect("toggled", self._on_log_transcripts_toggled)
        fine.attach(self.log_transcripts_check, 0, frow, 2, 1)
        frow += 1

        # Clear whatever is already on disk — the log holds past transcriptions
        # until it rolls over, so give the user a one-click wipe.
        clear_log_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        clear_log_btn = Gtk.Button(label="Clear log now")
        clear_log_btn.set_halign(Gtk.Align.START)
        clear_log_btn.set_tooltip_text(
            "Erase the current TuxTalk log file (and its backup) from disk.")
        clear_log_btn.connect("clicked", self._on_clear_log_clicked)
        clear_log_box.pack_start(clear_log_btn, False, False, 0)
        fine.attach(clear_log_box, 0, frow, 2, 1)
        frow += 1

    def _build_labs(self, grid, row):
        """TuxTalk Labs, at *row* of *grid*."""
        # Experimental features: they work but are still being
        # refined, so they live here with a note saying so, the Help page about
        # them, and a feedback link. Hidden in the Flatpak: its portal input
        # can't see Shift or send Ctrl+C, so none of them work there yet.
        if os.environ.get("FLATPAK_ID"):
            return
        labs_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        labs_sep.set_margin_top(20)
        labs_sep.set_margin_bottom(15)
        grid.attach(labs_sep, 0, row, 2, 1)
        row += 1

        labs_header = Gtk.Label(xalign=0)
        labs_header.set_markup('<b>🧪 TuxTalk Labs</b>')
        labs_header.set_margin_bottom(4)
        grid.attach(labs_header, 0, row, 2, 1)
        row += 1

        labs_note = Gtk.Label(xalign=0)
        labs_note.set_markup(
            '<span>Experimental features we’re still shaping, so they may change '
            'and won’t always get it right. If you try one, we’d love to hear how it '
            'went or what you’d change: use Send feedback below. Your ideas help make '
            'TuxTalk better.</span>')
        labs_note.set_line_wrap(True)
        labs_note.set_opacity(0.7)
        labs_note.set_margin_bottom(8)
        grid.attach(labs_note, 0, row, 2, 1)
        row += 1

        row = self._labs_editing(grid, row)
        self.type_at_pause = TypeAtPause(self.win)
        row = self.type_at_pause.build(grid, row)

        self._labs_indicator_and_links(grid, row)

    def _labs_editing(self, grid, row):
        """Editing by voice: voice editing, select and move, Show tips again;
        returns the next row."""
        from . import ai_cleanup, explain
        grid.attach(subheading("Editing by voice"), 0, row, 2, 1)
        row += 1

        # Voice editing (voice_edit.transcribe_and_edit): the AI that fixes
        # self-corrections rewrites the selected text the way the user says.
        self.voice_editing_check = Gtk.CheckButton(
            label="Voice editing: select text, hold Shift with your dictation key, "
                  "say how to change it")
        self.voice_editing_check.set_active(
            bool(self.config.get("voice_editing", False)) and ai_cleanup.is_installed())
        hold = self.config.get("hotkey") or "F8"      # the key Help names too
        self.voice_editing_check.set_tooltip_text(
            f"Select some text in any app, hold Shift+{hold} (Shift with your hold key, or tap\n"
            "Shift with your toggle key), and say what you want: “make this more formal”,\n"
            "“shorten this”, “fix the grammar”, “turn this into a bullet list”.\n"
            "The selection is replaced with the rewrite. Ctrl+Z puts it back.\n"
            "Or, right after dictating, say “edit that, make it shorter”: no keys needed,\n"
            "handy with hands-free and Type at pause.\n\n"
            "Uses the same small AI as self-corrections (one shared "
            f"{ai_cleanup.DOWNLOAD_SIZE_TEXT} download),\n"
            "on this computer. Simple, clear changes work best.")
        self.voice_editing_check.connect("toggled", self._on_voice_editing_toggled)
        self.voice_editing_check.set_margin_start(LABS_INDENT)
        grid.attach(self.voice_editing_check, 0, row, 2, 1)
        row += 1

        # Select and move by voice (voice_navigation.py): a dictation that
        # is only "select the last sentence" or "go to the end" is done as
        # a command. Works with or without voice editing.
        nav_check = Gtk.CheckButton(
            label="Select and move: say \u201cselect the last sentence\u201d, "
                  "\u201cgo to the end\u201d")
        nav_check.set_active(bool(self.config.get("voice_navigation", False)))
        nav_check.set_tooltip_text(
            "When a dictation is only one of these commands, TuxTalk does it instead of\n"
            "typing it. Inside a longer sentence the words are typed as usual.\n\n"
            "• Right after you dictate, on what you just said: “select that”,\n"
            "  “select the last sentence”, “select the last 3 words”\n"
            "• Around the cursor: “select all”, “select the line”,\n"
            "  “select the next word”, “select to the end of the line”\n"
            "• Moving: “go to the end”, “go to the start of the line”,\n"
            "  “go back 3 words”, “go up 2 lines”\n"
            "• “unselect”\n\n"
            f"With voice editing on, the Shift key understands them too: Shift+{hold}\n"
            f"“select the last sentence”, then Shift+{hold} “make it more formal”.\n"
            "Not in terminals. The full list is in Help, on the Writing page.")
        nav_check.connect("toggled", lambda c: self.update_config(
            "voice_navigation", c.get_active()))
        nav_check.set_margin_start(LABS_INDENT)
        grid.attach(nav_check, 0, row, 2, 1)
        row += 1

        # Brings back the "how it works" windows of these two features that
        # were ticked "Don't explain this again" (explain.py).
        tips_again = Gtk.Button(label="Show tips again")
        tips_again.set_tooltip_text(
            "Bring back the small windows that explain how voice editing and select\n"
            "and move work, for any you ticked \u201cDon't explain this again\u201d.")

        def _tips_again(button):
            explain.reset()
            button.set_label("\u2713 Tips will show again")
            button.set_sensitive(False)
        tips_again.connect("clicked", _tips_again)
        tips_again.set_halign(Gtk.Align.START)
        tips_again.set_margin_start(LABS_INDENT + 4)
        grid.attach(tips_again, 0, row, 2, 1)
        row += 1
        return row

    def _labs_indicator_and_links(self, grid, row):
        """The indicator following the window, then How these work and Send feedback."""
        # Follows the window being dictated into (recording_indicator.
        # _follow_target), placed as the floating messages are.
        grid.attach(subheading("Recording indicator"), 0, row, 2, 1)
        row += 1
        follow_check = Gtk.CheckButton(
            label="Follows the window you\u2019re dictating into")
        follow_check.set_active(bool(self.config.get("indicator_follow_window", False)))
        follow_check.set_tooltip_text(
            "Shows the recording indicator in the window you're dictating into, wherever it\n"
            "is on the screen. The position set on the Audio tab (bottom center, top right...)\n"
            "applies inside that window; the offset doesn't. When TuxTalk can't tell where\n"
            "the window is, the usual position and offset are used.")
        follow_check.connect("toggled", lambda c: self.update_config(
            "indicator_follow_window", c.get_active()))
        follow_check.set_margin_start(LABS_INDENT)
        grid.attach(follow_check, 0, row, 2, 1)
        row += 1

        # How these work (Help, on its Writing tab) and Send feedback.
        from .help_dialog import FEEDBACK_URL, show_help_dialog
        labs_links = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        labs_links.set_margin_top(12)
        labs_help = Gtk.Button(label="How these work")
        labs_help.set_tooltip_text("Open Help at Writing, where Labs starts")
        labs_help.connect("clicked", lambda _b: show_help_dialog(tab="writing"))
        labs_links.pack_start(labs_help, False, False, 0)
        labs_feedback = Gtk.LinkButton.new_with_label(FEEDBACK_URL, "Send feedback")
        labs_feedback.set_tooltip_text("Tell us how the Labs features work for you "
                                       "(TuxTalk Discussions on GitHub)")
        labs_links.pack_start(labs_feedback, False, False, 0)
        grid.attach(labs_links, 0, row, 2, 1)
        row += 1

    def _swiss_spelling_check(self):
        """The "Swiss German spelling" checkbox, bound to swiss_spelling."""
        check = Gtk.CheckButton(label="Swiss German spelling (ß → ss)")
        check.set_active(bool(self.config.get("swiss_spelling", False)))
        check.connect("toggled", lambda x: self.update_config("swiss_spelling", x.get_active()))
        check.set_tooltip_text(
            "Write ss wherever the speech model writes ß, as Swiss Standard German does\n"
            "(Strasse, gross). On by default when the system is set to Swiss German.")
        return check

    def _numbers_option(self):
        """The Numbers dropdown: as the speech model writes them, as digits,
        or digits from 10 up. Three entries over the two settings
        numbers_as_digits and numbers_small_as_words (NUMBER_CHOICES)."""
        combo = dropdown(Gtk.ComboBoxText())
        for nid, (nlabel, _digits, _small) in NUMBER_CHOICES.items():
            combo.append(nid, nlabel)
        combo.set_active_id(number_choice(self.config))

        def changed(c):
            choice = c.get_active_id()
            if choice is None:
                return
            _label, digits, small = NUMBER_CHOICES[choice]
            self.update_config("numbers_as_digits", digits)
            if small is not None:
                self.update_config("numbers_small_as_words", small)
        combo.connect("changed", changed)

        label = Gtk.Label(label="Numbers 💡:", xalign=0)
        label.set_mnemonic_widget(combo)
        label.set_tooltip_text(
            "Digits: \"twenty-five chickens\" becomes \"25 chickens\", \"october fifteenth\"\n"
            "becomes \"october 15th\" and \"seven thirty\" after at becomes \"7:30\".\n"
            "\"One\" and \"two\" on their own stay words (\"one of them\"), and so do fractions.\n"
            "Many writing styles spell out one to nine and use digits from 10 up.\n"
            "Works in English, German, Spanish, French, Italian, Portuguese, Dutch and Danish.")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.pack_start(label, False, False, 0)
        box.pack_start(combo, False, False, 0)
        return box

    def _send_automatically_option(self):
        """The "Send each dictation automatically" tickbox with its wait
        beneath it, greyed while the tickbox is off (send_countdown).
        Saying "press enter" at the end sends one dictation at once."""
        check = Gtk.CheckButton(label="Send each dictation automatically (press Enter)")
        check.set_active(bool(self.config.get("press_enter_after", False)))
        key = self.config.get("hotkey") or "the hotkey"
        check.set_tooltip_text(
            "After each dictation TuxTalk presses Enter for you: a chat message is sent,\n"
            "a terminal command runs. Not in text editors and word processors, where\n"
            "Enter would only start a new line.\n\n"
            f"During the wait, tap {key} to hold the send: the text stays in the box, so you\n"
            f"can fix it or add a screenshot, then press Enter yourself. Hold {key} instead\n"
            "to keep talking; that message is sent when you're done. A sent dictation\n"
            f"can't be changed with \u201cedit that\u201d, so tap {key} first if you want to.\n\n"
            "If what you said ends on a word like \u201cand\u201d or \u201cthe\u201d, it was "
            "probably\n"
            "cut off, so it isn't sent. Where TuxTalk can't tell which window has the focus\n"
            "(COSMIC, GNOME without TuxTalk's extension, Plasma 5.27 on Wayland), nothing\n"
            "is sent by itself.\n\n"
            "With this off, end any dictation with \u201cpress enter\u201d to send just that one.")

        waits = dropdown(Gtk.ComboBoxText())
        for seconds in range(5):
            label = f"After {seconds} second{'s' * (seconds > 1)}" if seconds else "Right away"
            waits.append(str(seconds), label)
        current = int(round(float(self.config.get("press_enter_delay", 2.0))))
        waits.set_active_id(str(min(4, max(0, current))))
        waits.connect("changed", lambda c: self.update_config(
            "press_enter_delay", float(c.get_active_id())) if c.get_active_id() else None)

        choice = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        choice.set_margin_start(28)   # under the tickbox's label, not its box
        send_label = Gtk.Label(label="Send:", xalign=0)
        send_label.set_mnemonic_widget(waits)     # a screen reader names the list by it
        choice.pack_start(send_label, False, False, 0)
        choice.pack_start(waits, False, False, 0)
        choice.set_sensitive(check.get_active())

        def toggled(button):
            self.update_config("press_enter_after", button.get_active())
            choice.set_sensitive(button.get_active())

        check.connect("toggled", toggled)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.pack_start(check, False, False, 0)
        box.pack_start(choice, False, False, 0)
        return box

    def _on_ai_corrections_toggled(self, check):
        """Turn AI self-corrections on or off (see _toggle_ai_feature)."""
        self._toggle_ai_feature(check, "ai_corrections", self._on_ai_corrections_toggled,
                                "Download the AI for self-corrections?")

    def _on_voice_editing_toggled(self, check):
        """Turn voice editing on or off. It uses the same AI as self-corrections."""
        self._toggle_ai_feature(check, "voice_editing", self._on_voice_editing_toggled,
                                "Download the AI for voice editing?")

    def _toggle_ai_feature(self, check, key, handler, question):
        """Turn an option that needs the local AI on or off.

        Turning it on the first time downloads the engine and model, after the
        user agrees to the size. If they decline, or the download fails or is
        cancelled, the box goes back to unticked so the setting never claims to
        be on while it cannot work. Self-corrections and voice editing share
        the one download.
        """
        if not check.get_active():
            self.update_config(key, False)
            return

        from tuxtalk import ai_cleanup
        if ai_cleanup.is_installed():
            self.update_config(key, True)
            return

        confirm = message_dialog(
            transient_for=self.window, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=question,
        )
        confirm.format_secondary_text(
            f"This downloads a small AI model ({ai_cleanup.MODEL_SIZE_TEXT}) and the "
            f"llama.cpp engine that runs it ({ai_cleanup.LLAMA_SIZE_TEXT}). It is a "
            "one-time download.\n\n"
            "Everything runs on this computer. Your dictation is never sent anywhere.")
        confirm.add_button("Cancel", Gtk.ResponseType.CANCEL)
        confirm.add_button("Download", Gtk.ResponseType.OK)
        confirm.set_default_response(Gtk.ResponseType.OK)
        response = confirm.run()
        confirm.destroy()
        if response != Gtk.ResponseType.OK:
            self._untick_ai_feature(check, key, handler)
            return

        from tuxtalk.download_progress_dialog import DownloadTask, UnifiedDownloadDialog
        dialog = UnifiedDownloadDialog(
            parent=self.window, title="Downloading the AI",
            description="One-time download. Everything runs on this computer.")
        dialog.add_task(DownloadTask("AI engine", ai_cleanup.LLAMA_SIZE_TEXT,
                                     ai_cleanup.make_engine_download_func()))
        dialog.add_task(DownloadTask("AI model", ai_cleanup.MODEL_SIZE_TEXT,
                                     ai_cleanup.make_model_download_func()))
        results = dialog.run()

        if all(r.get("success") for r in results.values()) and ai_cleanup.is_installed():
            self.update_config(key, True)
            # The other option's box showed unticked while the files were
            # missing; the download mustn't turn it on behind that box, even
            # after Cancel (the download stays).
            for other_key, other in (("ai_corrections", self.ai_corrections_check),
                                     ("voice_editing", self.voice_editing_check)):
                if other_key != key and other is not None and not other.get_active():
                    self.win.save_now(other_key, False)
            return

        self._untick_ai_feature(check, key, handler)
        if not any(r.get("cancelled") for r in results.values()):
            show_message(self.window,
                         Gtk.MessageType.ERROR,
                         "The AI download did not finish",
                         "Check your internet connection and try turning the option on again.")

    def _untick_ai_feature(self, check, key, handler):
        """Put an AI box back to unticked without re-running its handler."""
        check.handler_block_by_func(handler)
        check.set_active(False)
        check.handler_unblock_by_func(handler)
        self.update_config(key, False)

    def _on_log_transcripts_toggled(self, check):
        """Toggle transcript logging. Turning it OFF is safe and silent; turning
        it ON writes every dictation to disk, so it must be confirmed via a
        warning dialog before the config is written. Cancelling reverts the box.
        """
        if not check.get_active():
            self.update_config("log_transcripts", False)
            return

        dialog = message_dialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="Log all transcribed text?",
        )
        dialog.format_secondary_text(
            "This saves the full text of EVERYTHING you dictate to TuxTalk's log "
            "file on disk, in plain text, and keeps it there until you turn this "
            "setting back off.\n\n"
            "Only enable it if you are troubleshooting a dictation problem."
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        enable_btn = dialog.add_button("Yes, log everything I dictate", Gtk.ResponseType.OK)
        enable_btn.get_style_context().add_class("destructive-action")
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.OK:
            self.update_config("log_transcripts", True)
        else:
            # Revert without re-entering this handler.
            check.handler_block_by_func(self._on_log_transcripts_toggled)
            check.set_active(False)
            check.handler_unblock_by_func(self._on_log_transcripts_toggled)

    def _on_clear_log_clicked(self, _button):
        """Erase the on-disk log after confirming — it can hold past dictation."""
        dialog = message_dialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="Clear the TuxTalk log?",
        )
        dialog.format_secondary_text(
            "This permanently deletes the current log file and its backup from "
            "disk, including any past transcriptions recorded there. This cannot "
            "be undone."
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        clear_btn = dialog.add_button("Clear log", Gtk.ResponseType.OK)
        clear_btn.get_style_context().add_class("destructive-action")
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.OK:
            from .logger import clear_log
            cleared = clear_log()
            show_message(self.window,
                         Gtk.MessageType.INFO if cleared else Gtk.MessageType.ERROR,
                         "Log cleared." if cleared else "Some of the log couldn't be erased.")
