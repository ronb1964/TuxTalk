"""Preferences, General tab: the dictation keys, Speech Recognition
(prefs_speech.py), start at login and Power Saving, and the question after
OK or Apply when a hotkey changed."""
import os
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from .config import HOTKEY_CHOICES
from .hotkey_labels import dropdown_entries, hands_free_label
from .logger import setup_logger
from .prefs_hotkey_test import run_hotkey_test
from .prefs_speech import SpeechSection
from .prefs_system_check import UPDATE_HELPER_LABEL
from .prefs_tab import Tab, dropdown, fold, scroll_into_view, section_header
from .ui_style import WARNING_AMBER, message_dialog, show_message

logger = setup_logger(__name__)

# The More shortcuts dropdowns: (key, what the entry says). These avoid the
# usual clashes: Ctrl+Shift combos are browsers', Super+H minimises on GNOME.
CHEAT_SHEET_KEYS = [
    ("", "None (disabled)"),
    ("Ctrl+Alt+V", "Ctrl+Alt+V (recommended)"),
    ("Ctrl+Alt+H", "Ctrl+Alt+H"),
    ("Ctrl+Super+V", "Ctrl+Super+V"),
    ("Ctrl+Super+H", "Ctrl+Super+H"),
]
PASTE_LAST_KEYS = [
    ("", "None (disabled)"),
    ("Ctrl+Alt+P", "Ctrl+Alt+P (recommended)"),
    ("Ctrl+Super+P", "Ctrl+Super+P"),
]


def _needs(keys):
    """"Right Ctrl needs" or "Right Ctrl and Right Alt need"."""
    return f"{' and '.join(keys)} need{'s' if len(keys) == 1 else ''}"


def hotkey_change_message(cfg, changed, refused, apps=None, unheard=()):
    """(title, text, offer the test, a key clashes) for the question after OK or
    Apply, or None.

    *changed*: the settings keys changed; *refused*: keys KDE already uses
    (kwin_hotkeys.refused_since), None when unknown; *apps*: {key: app} for
    those that open an app (kwin_hotkeys.app_shortcuts_since); *unheard*: new
    keys the running hotkey helper is too old to report. A changed key is
    offered the same test first run gives it; a key the helper won't report
    is offered the update first (Update Helper), and the test after it.

    A key KDE also uses comes first, in the title as well: someone who reads
    only the top of the window must still learn that the key does two things.
    """
    # KDE reports keys in Qt's spelling (Super is "Meta"): map back to ours.
    from .kwin_hotkeys import qt_key_sequence
    all_keys = ("hotkey", "toggle_hotkey", "voice_commands_hotkey", "paste_last_hotkey")
    ours = {qt_key_sequence(cfg.get(k)): cfg.get(k) for k in all_keys if cfg.get(k)}
    clashes = []        # (our name for the key, the app or None)
    if changed & set(all_keys):
        clashes = [(ours[key], (apps or {}).get(key)) for key in refused or [] if key in ours]
    lines = [_clash_line(name, app) for name, app in clashes]

    roles = (("hotkey", "hold-to-talk key"), ("toggle_hotkey", "tap-to-start key"))
    new_keys = [f"Your {what} is now {cfg.get(key)}." for key, what in roles
                if key in changed and cfg.get(key)]
    lines += new_keys
    if unheard:
        does = "it does" if len(unheard) == 1 else "they do"
        lines.append(f"The hotkey helper on this computer is older than this TuxTalk and "
                     f"doesn't report {' or '.join(unheard)}, so {does} nothing yet. Update "
                     f"Helper updates it now: one password prompt, no restart. Then try "
                     f"the key in the test.")
    offer_test = bool(new_keys) and not unheard
    if offer_test:
        lines.append("Try it once in the test to make sure it reaches TuxTalk. On some "
                     "laptops the F-keys change the brightness or volume unless you hold Fn; "
                     "Right Ctrl, Right Alt and Scroll Lock never need it.")
        # So Not Now isn't a dead end: the Test Hotkeys button can be below the fold.
        lines.append("You can also test it later with Test Hotkeys, under the hotkeys on "
                     "the General tab.")
    if not lines:
        return None
    return _question_title(clashes, unheard), "\n\n".join(lines), offer_test, bool(clashes)


def _clash_line(name, app):
    """hotkey_change_message's paragraph on key *name*, which KDE uses: to
    open *app*, or (None) for one of its own shortcuts."""
    if app:
        return (f"KDE opens {app} with {name}, so pressing {name} does that too. "
                f"Pick another key, or change that shortcut in System Settings, "
                f"Keyboard, Shortcuts.")
    return (f"KDE already uses {name} for one of its own shortcuts, so "
            f"pressing it does that too. Pick another key if that gets in the way.")


def _question_title(clashes, unheard):
    """hotkey_change_message's title: a key KDE uses first, then keys the
    hotkey helper is too old for, else the offer of the test."""
    if len(clashes) > 1:
        return "KDE already uses some of these keys"
    if clashes and clashes[0][1]:
        return f"{clashes[0][0]} also opens {clashes[0][1]}"
    if clashes:
        return f"KDE already uses {clashes[0][0]}"
    if unheard:
        return f"{_needs(unheard)} a newer hotkey helper"
    return "Try your new hotkey?"


class GeneralTab(Tab):
    """The General tab. Its Speech Recognition section is .speech."""

    def build(self):
        """The tab's page: Dictation Keys, Speech Recognition, Startup
        Options and Power Saving."""
        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        grid.set_margin_start(20)
        grid.set_margin_end(20)
        grid.set_margin_top(20)
        grid.set_margin_bottom(20)

        # First on the first tab: the keys are the one thing most people change.
        header = Gtk.Label()
        header.set_markup('<b>Dictation Keys</b>')
        header.set_xalign(0)
        header.set_margin_bottom(10)
        grid.attach(header, 0, 0, 2, 1)
        row = self._build_keys(grid, 1)
        row = self._build_shortcuts(grid, row)
        self.speech = SpeechSection(self.win)
        row = self.speech.build(grid, row)
        row = self._build_startup(grid, row)
        self._build_power_saving(grid, row)
        return grid

    def _build_keys(self, grid, row):
        """The hold and toggle keys, hands-free and Test Hotkeys; returns the
        next free row."""
        self.hotkey_label = Gtk.Label(label="Hold Hotkey 💡:", xalign=0)
        self.hotkey_label.set_tooltip_text(
            "Hold this key while speaking — release to stop and transcribe.\n"
            "Both hotkeys are always active simultaneously.\n"
            "Recommended: F8 (default). Avoid: F1, F5, F11, F12.\n"
            "F-keys that need Fn held? Right Ctrl, Right Alt and Scroll Lock don't.")
        hotkeys = dropdown_entries()
        self.hotkey_combo = self._hotkey_combo(self.hotkey_label, hotkeys)
        grid.attach(self.hotkey_label, 0, row, 1, 1)
        grid.attach(self.hotkey_combo, 1, row, 1, 1)

        self.toggle_label = Gtk.Label(label="Toggle Hotkey 💡:", xalign=0)
        self.toggle_label.set_tooltip_text(
            "Press once to start recording, press again to stop.\n"
            "Double-tap it for hands-free (the option under this row):\n"
            "the recording stops by itself when you stop talking.\n"
            "Both hotkeys are always active simultaneously.\nRecommended: F9 (default).")
        self.toggle_combo = self._hotkey_combo(self.toggle_label, hotkeys)
        grid.attach(self.toggle_label, 0, row + 1, 1, 1)
        grid.attach(self.toggle_combo, 1, row + 1, 1, 1)
        # Both combos exist now, so set their selections together.
        self._apply_hotkey_fallbacks(HOTKEY_CHOICES)
        self.hotkey_combo.connect(
            "changed", lambda c: self.update_config("hotkey", c.get_active_id()))
        self.toggle_combo.connect(
            "changed", lambda c: self.update_config("toggle_hotkey", c.get_active_id()))

        grid.attach(self._hands_free_row(), 0, row + 2, 2, 1)

        test_btn = Gtk.Button(label="Test Hotkeys")
        test_btn.connect("clicked", lambda _b: run_hotkey_test(self.window, self.config))
        test_btn.get_child().set_xalign(0.5)
        test_btn.set_tooltip_text("Test your configured hotkeys to make sure they work")
        test_btn.set_halign(Gtk.Align.START)
        test_btn.set_margin_top(10)
        grid.attach(test_btn, 0, row + 3, 2, 1)
        row += 4
        if os.environ.get("FLATPAK_ID"):
            row = self._build_flatpak_note(grid, row, test_btn)
        return row

    def _hands_free_row(self):
        """Hands-free: double-tap the toggle key and the recording stops by
        itself when you go quiet (silence.py). A single tap stays a plain
        toggle. Shown on the Flatpak too: its toggle shortcut double-taps the
        same way."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        check = Gtk.CheckButton(label=hands_free_label(self.config.get("toggle_hotkey")))
        # Says the key itself, and follows the dropdown above.
        self.toggle_combo.connect("changed", lambda c: check.set_label(
            hands_free_label(c.get_active_id())))
        check.set_active(bool(self.config.get("auto_stop_silence", False)))
        adj = Gtk.Adjustment(value=float(self.config.get("auto_stop_seconds", 2.0)),
                             lower=0.5, upper=10.0, step_increment=0.5)
        spin = Gtk.SpinButton(adjustment=adj, digits=1)
        spin.connect("scroll-event", lambda *a: True)  # no accidental scroll changes
        spin.set_sensitive(check.get_active())
        check.connect("toggled", lambda x: (
            self.update_config("auto_stop_silence", x.get_active()),
            spin.set_sensitive(x.get_active())))
        spin.connect("value-changed",
                     lambda x: self.update_config("auto_stop_seconds", round(x.get_value(), 1)))
        box.pack_start(check, False, False, 0)
        box.pack_start(spin, False, False, 0)
        box.pack_start(Gtk.Label(label="seconds of silence"), False, False, 0)
        box.set_tooltip_text(
            "Double-tap your toggle key (two quick taps), talk, and when you stop\n"
            "talking the recording ends and your text is typed by itself. A short\n"
            "high beep confirms the double-tap.\n\n"
            "A single tap is still a plain on/off toggle that runs until you tap\n"
            "again, however long you pause. Hands-free waits until you have actually\n"
            "started talking, and a pause shorter than this setting does not stop it.\n"
            "Hold-to-talk is not affected.")
        # Indented under the toggle key's row, which it belongs to.
        box.set_margin_start(28)
        return box

    def _build_flatpak_note(self, grid, row, test_btn):
        """On the Flatpak the dictation keys belong to the desktop's
        global-shortcuts portal, not this config, and Test Hotkeys uses evdev,
        which the sandbox blocks: the key controls are hidden and a note says
        where the keys live. The dropdowns still exist, hidden, so saving
        reads them as everywhere else. Returns the next free row."""
        from .desktop_detect import flatpak_hotkey_hint, get_desktop_environment
        for widget in (self.hotkey_label, self.hotkey_combo,
                       self.toggle_label, self.toggle_combo, test_btn):
            widget.set_no_show_all(True)
            widget.hide()
        note = Gtk.Label()
        note.set_markup("🔒 <b>Dictation keys are set by your desktop, not here.</b>\n"
                        + flatpak_hotkey_hint())
        note.set_xalign(0)
        note.set_line_wrap(True)
        note.set_margin_top(4)
        grid.attach(note, 0, row, 2, 1)
        row += 1

        # On KDE, typing through the remote-control portal shows "Remote control
        # session started" after every dictation. This reopens the steps for
        # turning it off, the window shown once after first run.
        if get_desktop_environment() == "kde":
            from .notification_help import show_notification_help_window
            button = Gtk.Button(label="🔔 Turn off the “remote control” notification…")
            button.get_child().set_xalign(0.0)
            button.set_halign(Gtk.Align.START)
            button.set_margin_top(8)
            button.set_tooltip_text(
                "KDE shows a popup after every dictation. Opens step-by-step "
                "instructions in a window that stays on top while you do it.")
            button.connect("clicked", lambda _b: show_notification_help_window())
            grid.attach(button, 0, row, 2, 1)
            row += 1
        return row

    def _build_shortcuts(self, grid, row):
        """The More shortcuts fold: the commands cheat sheet and Paste Last
        Dictation. Both work out of the box, so they stay folded and the
        section shows only the keys people dictate with. Returns the next
        free row."""
        self.shortcuts_fold = fold(grid, row, "Show more shortcuts",
                                             "Hide more shortcuts")
        shortcuts = self.shortcuts_fold.content

        label = Gtk.Label(label="Commands cheat sheet:", xalign=0)
        label.set_tooltip_text(
            "Press this key combo to pop up a quick reference of all voice commands.\n"
            "Leave set to 'None' to disable the shortcut.\n"
            "The reference is also available from the tray menu.")
        shortcuts.attach(label, 0, 0, 1, 1)
        self.vc_hotkey_combo = self._shortcut_combo(label, CHEAT_SHEET_KEYS,
                                                    "voice_commands_hotkey")
        shortcuts.attach(self.vc_hotkey_combo, 1, 0, 1, 1)

        # Types the last dictation again, for when it went to the wrong window
        # (hotkeys._paste_last_dictation).
        label = Gtk.Label(label="Paste Last Dictation:", xalign=0)
        label.set_tooltip_text(
            "Press this key combo to type your last dictation again where the cursor is,\n"
            "for when it went to the wrong window. It types once you let go of the keys.\n"
            "Leave set to 'None' to turn it off.")
        shortcuts.attach(label, 0, 1, 1, 1)
        self.pl_hotkey_combo = self._shortcut_combo(label, PASTE_LAST_KEYS, "paste_last_hotkey")
        shortcuts.attach(self.pl_hotkey_combo, 1, 1, 1, 1)
        self._update_shortcuts_summary()
        return row + 1

    def _build_startup(self, grid, row):
        """Start at login; returns the next free row."""
        row = section_header(grid, row, "Startup Options")
        check = Gtk.CheckButton(label="Launch TuxTalk at login")
        check.set_active(self.config.get("launch_at_login", False))
        check.connect("toggled", lambda x: self.update_config("launch_at_login", x.get_active()))
        check.set_tooltip_text("Automatically start TuxTalk when you log in to your desktop.\n"
                               "Useful for having dictation always available.")
        grid.attach(check, 0, row, 2, 1)
        return row + 1

    def show_saved(self):
        """Show what was just saved: the tray may have changed the model and
        device, or Power Saving (Battery saver), while this window was open,
        and the save kept that."""
        # Without the tickbox's own reaction, which scrolls the tab to show
        # the minutes: nobody ticked it here.
        self.timeout_check.handler_block_by_func(self._on_auto_timeout_toggled)
        self.timeout_check.set_active(self.config.get("auto_timeout_enabled", False))
        self.timeout_check.handler_unblock_by_func(self._on_auto_timeout_toggled)
        self.update_timeout_ui_state()
        self.timeout_spin.set_value(self.config.get("auto_timeout_minutes", 5))
        self.speech.show_saved()

    def _build_power_saving(self, grid, row):
        """Power Saving: the tickbox, and under it the minutes and what
        happens then, hidden with it off (update_timeout_ui_state)."""
        row = section_header(grid, row, "Power Saving")
        check = self.timeout_check = Gtk.CheckButton(
            label="Save power when dictation isn't used for a while")
        check.set_active(self.config.get("auto_timeout_enabled", False))
        check.connect("toggled",
                      lambda x: self.update_config("auto_timeout_enabled", x.get_active()))
        check.connect("toggled", self._on_auto_timeout_toggled)
        check.set_tooltip_text(
            "Saves battery on a laptop: after the minutes below without dictating, the\n"
            "speech model lets go of the processor and the graphics card. Your keys keep\n"
            "working, and the next dictation loads it again.\n\n"
            "On by default on laptops, off on desktop computers.")
        grid.attach(check, 0, row, 2, 1)

        self.timeout_label = Gtk.Label(label="After", xalign=0)
        self.timeout_spin = Gtk.SpinButton()
        self.timeout_spin.set_range(1, 60)
        self.timeout_spin.set_increments(1, 5)
        self.timeout_spin.set_value(self.config.get("auto_timeout_minutes", 5))
        self.timeout_spin.connect("value-changed", lambda x: self.update_config(
            "auto_timeout_minutes", int(x.get_value())))
        self.timeout_spin.connect("scroll-event", lambda *a: True)  # no accidental changes
        self.timeout_spin.set_tooltip_text(
            "Minutes without dictating before the speech model rests.")
        self.timeout_minutes_label = Gtk.Label(label="minutes without dictating", xalign=0)
        minutes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        minutes.set_margin_start(28)
        minutes.pack_start(self.timeout_label, False, False, 0)
        minutes.pack_start(self.timeout_spin, False, False, 0)
        minutes.pack_start(self.timeout_minutes_label, False, False, 0)
        grid.attach(minutes, 0, row + 1, 2, 1)

        self.timeout_note = Gtk.Label(xalign=0)
        self.timeout_note.set_text(
            "Your keys keep working. The first dictation after a rest takes a few "
            "seconds longer while the speech model loads again.")
        self.timeout_note.set_line_wrap(True)
        self.timeout_note.set_max_width_chars(70)
        self.timeout_note.set_opacity(0.7)
        self.timeout_note.set_margin_start(28)
        grid.attach(self.timeout_note, 0, row + 2, 2, 1)

    def _hotkey_combo(self, label, entries):
        """A key dropdown of *entries* [(key, its label)], which a screen
        reader names by *label*. The tooltip stays on the label: on the
        combo it gets in the way of the popup."""
        combo = dropdown(Gtk.ComboBoxText())
        for key, text in entries:
            combo.append(key, text)
        label.set_mnemonic_widget(combo)
        return combo

    def _shortcut_combo(self, label, entries, setting):
        """A More shortcuts dropdown for *setting*. A saved key the list
        doesn't have (a hand edit) is added to it: it's the key in use, and
        shown as None, picking None would change nothing."""
        current = self.config.get(setting, "")
        if current not in dict(entries):
            entries = [*entries, (current, current)]
        combo = self._hotkey_combo(label, entries)
        combo.set_active_id(current)
        combo.connect("changed", lambda c: (
            self.update_config(setting, c.get_active_id() or ""),
            self._update_shortcuts_summary()))
        return combo

    def _apply_hotkey_fallbacks(self, hotkeys):
        """Select the configured hotkeys, falling back to F8/F9, and record
        that fallback in the settings, so what the dropdown shows is what OK
        saves.

        A blank hotkey (first run closed early) would otherwise show F8 but
        save the blank, and picking F8 again emits no 'changed' to correct it.
        """
        for key, combo, default in (
            ("hotkey", self.hotkey_combo, "F8"),
            ("toggle_hotkey", self.toggle_combo, "F9"),
        ):
            current = self.config.get(key, default)
            chosen = current if current in hotkeys else default
            combo.set_active_id(chosen)
            self.config[key] = chosen

    def _update_shortcuts_summary(self):
        """The two keys beside the More shortcuts button, as they are now."""
        def key(combo):
            return combo.get_active_id() or "off"
        self.shortcuts_fold.set_summary(GLib.markup_escape_text(
            f"cheat sheet {key(self.vc_hotkey_combo)}"
            f" · paste last {key(self.pl_hotkey_combo)}"))

    def _on_auto_timeout_toggled(self, checkbox):
        """Show or hide the minutes with the Power Saving tickbox."""
        self.update_timeout_ui_state()
        if checkbox.get_active():
            # The last thing on the General tab: what it just showed (the
            # minutes and what happens then) would sit below the window's edge.
            scroll_into_view(self.timeout_note)

    def update_timeout_ui_state(self):
        """The Power Saving minutes show only with its tickbox ticked."""
        shown = self.config.get("auto_timeout_enabled", False)
        for widget in (self.timeout_label, self.timeout_spin, self.timeout_minutes_label,
                       self.timeout_note):
            widget.set_visible(shown)

    # --- After OK or Apply ------------------------------------------------------

    def after_hotkey_change(self, changed, since):
        """After OK or Apply with a changed hotkey: offer the hotkey test,
        and say so if KDE already uses the key. True when it asked, so Apply
        doesn't also say "Settings applied"."""
        from .config import HOTKEY_KEYS
        if not set(changed) & {*HOTKEY_KEYS, "voice_commands_hotkey", "paste_last_hotkey"}:
            return False
        from .kwin_hotkeys import app_shortcuts_since
        from .secure_input import unheard_keys
        refused = self._kde_refused(since)
        unheard = unheard_keys([self.config.get(k) for k in HOTKEY_KEYS if k in changed])
        message = hotkey_change_message(self.config, set(changed), refused,
                                        app_shortcuts_since(since) if refused else None,
                                        unheard)
        if message is None:
            return False
        if self._ask_hotkey_question(*message, update=bool(unheard)) != Gtk.ResponseType.OK:
            return True
        if not unheard or self._update_helper(unheard):
            run_hotkey_test(self.window, self.config)
        return True

    def _update_helper(self, unheard) -> bool:
        """Update Helper in the hotkey question (update_offers.update_helper),
        waiting for it with the window drawn: pkexec waits for the password,
        and after OK the window closes as soon as this returns. True when it
        was updated; otherwise it's said why, and where to update it later."""
        from . import pkexec_runner, update_offers
        answer, gave_up = {}, []

        def done(ok, message):
            answer.update(ok=ok, message=message)
            if gave_up:             # the window stopped waiting: a notification says it
                update_offers.say_helper_updated(ok, message)
        update_offers.update_helper(done)
        finished = self._while_drawn(lambda: answer, pkexec_runner.SCRIPT_TIMEOUT_S + 10)
        if answer.get("ok"):
            logger.info("Updated the hotkey helper from the hotkey question")
            return True
        later = (f"{_needs(unheard)} it. You can update it later: Advanced tab, "
                 f"Show system check, {UPDATE_HELPER_LABEL}.")
        if not finished:
            # Past pkexec's own time-out the script can still be running as root.
            gave_up.append(True)
            show_message(self.window, Gtk.MessageType.WARNING, "The update is taking a while",
                         "It may still finish by itself, and a notification says when. "
                         f"If not, update it later: Advanced tab, Show system check, "
                         f"{UPDATE_HELPER_LABEL}.")
            return False
        message = answer["message"]
        show_message(self.window, Gtk.MessageType.WARNING, "The hotkey helper wasn't updated",
                     later if "cancelled" in message.lower() else f"{message}\n\n{later}")
        return False

    def _while_drawn(self, finished, seconds) -> bool:
        """Run the main loop until *finished*() or *seconds* have passed, so
        the window stays drawn while it waits (and can't be used: a second
        Apply, OK or Cancel would run inside the first). True when finished."""
        deadline = time.monotonic() + seconds
        with self.win.busy():
            while time.monotonic() < deadline:
                if finished():
                    return True
                while Gtk.events_pending():
                    Gtk.main_iteration()
                time.sleep(0.1)
        return False

    def _ask_hotkey_question(self, title, text, offer_test, clash, update=False):
        """The question hotkey_change_message words; returns the answer.
        With *update*, its main button is Update Helper."""
        dialog = message_dialog(transient_for=self.window, modal=True,
                                message_type=Gtk.MessageType.QUESTION
                                if offer_test and not clash else Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.NONE, text=title)
        if clash:
            # A key that does two things must not read like an ordinary
            # heading: the title and the clash paragraph are amber, the
            # colour of TuxTalk's other warnings.
            esc = GLib.markup_escape_text
            dialog.set_markup(f'<span color="{WARNING_AMBER}" weight="bold" size="large">'
                              f'⚠️ {esc(title)}</span>')
            first, _, rest = text.partition("\n\n")
            dialog.format_secondary_markup(
                f'<span color="{WARNING_AMBER}" weight="bold">{esc(first)}</span>'
                + (f"\n\n{esc(rest)}" if rest else ""))
        else:
            dialog.format_secondary_text(text)
        if update or offer_test:
            dialog.add_button("Not Now", Gtk.ResponseType.CANCEL)
            dialog.add_button("Update Helper" if update else "Test Now", Gtk.ResponseType.OK)
        else:
            dialog.add_button("OK", Gtk.ResponseType.CANCEL)
        dialog.set_default_response(Gtk.ResponseType.OK if update or offer_test
                                    else Gtk.ResponseType.CANCEL)
        answer = dialog.run()
        dialog.destroy()
        return answer

    def _kde_refused(self, since):
        """Keys KDE already uses, once the service has registered the new
        ones (kwin_hotkeys saves its verdict), or None: not KDE, dictation
        off, or no answer within a few seconds."""
        from . import kwin_hotkeys
        try:
            from .service_launcher import find_service_pids
            if not kwin_hotkeys.should_claim() or not find_service_pids():
                return None
        except Exception as e:
            logger.warning(f"Could not tell whether KDE holds the new keys: {e}")
            return None
        found = []

        def answered():
            found[:] = [kwin_hotkeys.refused_since(since)]
            return found[0] is not None
        return found[0] if self._while_drawn(answered, 6) else None
