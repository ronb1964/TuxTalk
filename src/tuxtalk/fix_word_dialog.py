"""
"Fix a Word" window: teach TuxTalk a word from a recent dictation.

Opened from Recent Dictations in the tray menu (or the GNOME panel menu via
D-Bus). The user clicks the misheard word(s), types the right spelling, and
the fix is saved as a custom voice command (see vocabulary.py), so every
later dictation gets it right.

Saying "fix that word" opens it too (voice_commands.handle_fix_word), as its own
process (main() below) placed over the app being dictated into, on the
newest dictation only: no dropdown, which XWayland would break.
"""
import time

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib
from .ui_style import message_dialog, show_message

from . import vocabulary
from .config import ConfigNotLoadedError
from .history import get_entries, preview
from .logger import setup_logger

logger = setup_logger(__name__)

# Singleton guard, same as the Voice Commands window: a second menu click
# brings the open window forward instead of stacking another.
_active_dialog = None

_WARNING_COLOR = "#e5a50a"


def show_fix_word_dialog(index=0, copy_text=None):
    """Open the window on recent dictation *index* (0 = newest).

    *copy_text* puts a string on the clipboard; by default clipboard.py's
    Wayland-safe copier, which works from the tray and from Preferences.
    """
    if copy_text is None:
        from .clipboard import copy_text
    global _active_dialog
    entries = get_entries()
    if _active_dialog is not None:
        # *index* points into the history as it is now, which may have grown
        # since the window opened.
        if entries:
            _active_dialog.set_entries(entries)
        _active_dialog.select_entry(index)
        from .raise_window import raise_window
        raise_window(_active_dialog.window)
        return
    if not entries:
        show_message(None,
                     Gtk.MessageType.INFO,
                     "No recent dictations to fix yet",
                     "Dictate something first. Then, if TuxTalk gets a word wrong, "
                     "choose Fix a Word in the TuxTalk menu to teach it.",
                     keep_above=True)
        return
    _active_dialog = _FixWordDialog(entries, index, copy_text)



def _named(text, widget):
    """A label for *widget*: Alt and the underlined letter go to it, and a
    screen reader names it by the label."""
    label = Gtk.Label.new_with_mnemonic(text)
    label.set_mnemonic_widget(widget)
    return label

class _FixWordDialog:
    def __init__(self, entries, index, copy_text, before_show=None, in_place=False):
        self.entries = entries
        self.copy_text = copy_text
        # "fix that word" right after the dictation: fix it in the app's text
        # too (main() does it once the window is gone), not just copy it.
        self.in_place = in_place
        self.fixed_for_text = None
        self.text = ""
        self.first = self.last = None

        win = self.window = Gtk.Window(title="Fix a Word")
        win.set_default_size(560, -1)
        win.set_position(Gtk.WindowPosition.CENTER)
        win.set_keep_above(True)
        win.set_border_width(16)
        win.connect("destroy", self._on_destroy)
        win.connect("key-press-event", self._on_key)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        win.add(box)

        # Which dictation. Hidden when there is only one to choose from.
        from .ui_style import apply_dropdown_list_style
        apply_dropdown_list_style()
        self.combo = Gtk.ComboBoxText()
        for text in entries:
            self.combo.append_text(preview(text, 70))
        self.combo.connect("changed", lambda c: self._load(c.get_active()))
        self.combo_row = Gtk.Box(spacing=8)
        self.combo_row.pack_start(_named("_Dictation:", self.combo), False, False, 0)
        self.combo_row.pack_start(self.combo, True, True, 0)
        box.pack_start(self.combo_row, False, False, 0)

        hint = Gtk.Label(xalign=0)
        hint.set_line_wrap(True)
        hint.set_text("Click the word it got wrong, or drag across it if it's "
                      "more than one word.")
        box.pack_start(hint, False, False, 0)

        # Opened from the tray: point to the quicker way (voice_commands.handle_fix_word).
        if not in_place:
            tip = Gtk.Label(xalign=0)
            tip.set_line_wrap(True)
            tip.set_markup('<span alpha="70%">Tip: right after dictating, you can also just '
                           'say \u201cfix that word\u201d. It fixes the word in your text '
                           'for you.</span>')
            box.pack_start(tip, False, False, 0)

        # A read-only text box rather than a button per word: it reads like
        # the sentence it is, and click-or-drag is how people select text
        # everywhere else. The selection is snapped to whole words.
        self.view = Gtk.TextView()
        self.view.set_editable(False)
        # Its cursor stays visible: hidden, the arrow keys scroll instead of moving.
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        for side in ("left", "right", "top", "bottom"):
            getattr(self.view, f"set_{side}_margin")(8)
        # The picked words get their own highlight: the normal selection fades
        # to nearly nothing once focus moves to the "Should be" box.
        self.picked_tag = self.view.get_buffer().create_tag(
            "picked", background="#3584e4", foreground="#ffffff")
        # False: the text box must still get the release itself. idle_add's
        # return value (a source id) read as "handled", so the box never saw
        # the button come up and ignored every click after the first pick.
        self.view.connect("button-release-event", self._pick_soon, True)
        self.view.connect("key-release-event", self._pick_soon, False)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        scroller.set_min_content_height(70)
        scroller.set_max_content_height(220)
        scroller.set_propagate_natural_height(True)
        scroller.add(self.view)
        box.pack_start(scroller, True, True, 0)

        self.heard_label = Gtk.Label(xalign=0)
        box.pack_start(self.heard_label, False, False, 0)

        box.pack_start(self._fix_row(), False, False, 0)

        self.warning_label = Gtk.Label(xalign=0)
        self.warning_label.set_line_wrap(True)
        self.warning_label.set_no_show_all(True)
        box.pack_start(self.warning_label, False, False, 0)

        # "Copy the fixed text" read as the fixed word, and the whole
        # dictation was pasted over one word. Say what it is.
        self.copy_check = Gtk.CheckButton(
            label="Also fix it in my text" if in_place
            else "Also copy the whole corrected dictation, to paste over the old one")
        self.copy_check.set_active(True)
        box.pack_start(self.copy_check, False, False, 0)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: win.destroy())
        self.save_button = Gtk.Button(label="Always fix this")
        self.save_button.get_style_context().add_class("suggested-action")
        self.save_button.connect("clicked", lambda _b: self._on_save())
        buttons.pack_start(cancel, False, False, 0)
        buttons.pack_start(self.save_button, False, False, 0)
        box.pack_start(buttons, False, False, 0)

        self.select_entry(index)
        if before_show:
            before_show(win)
        win.show_all()
        self.combo_row.set_visible(len(entries) > 1)

    # --- which dictation --------------------------------------------------

    def set_entries(self, entries):
        """Show *entries*, the recent dictations as they are now."""
        self.entries = entries
        # remove_all() leaves nothing active, so the next select_entry()
        # always loads its dictation (_load ignores the -1 in between).
        self.combo.remove_all()
        for text in entries:
            self.combo.append_text(preview(text, 70))
        self.combo_row.set_visible(len(entries) > 1)

    def select_entry(self, index):
        """Show dictation *index*, or the newest when it is out of range."""
        index = index if 0 <= index < len(self.entries) else 0
        if self.combo.get_active() == index:
            self._load(index)
        else:
            self.combo.set_active(index)  # fires "changed" -> _load

    def _load(self, index):
        if index < 0:
            return
        self.text = self.entries[index]
        self.first = self.last = None
        self.view.get_buffer().set_text(self.text)
        self._update()

    def _fix_row(self):
        """"Should be:" and the box for the right spelling (self.entry)."""
        row = Gtk.Box(spacing=8)
        self.entry = Gtk.Entry()
        row.pack_start(_named("_Should be:", self.entry), False, False, 0)
        self.entry.set_placeholder_text("The right spelling, e.g. BambuStudio")
        self.entry.connect("changed", lambda _e: self._update())
        self.entry.connect("activate", lambda _e: self._on_save())
        row.pack_start(self.entry, True, True, 0)
        return row

    # --- picking words ----------------------------------------------------

    def _pick_soon(self, _view, _event, by_mouse):
        """After a click, drag or key in the text box, once GTK has moved the
        cursor: pick the words. Never stops the event."""
        GLib.idle_add(self._on_pick, by_mouse)
        return False

    def _on_pick(self, by_mouse=True):
        """Snap whatever was clicked or dragged in the text box to whole
        words. After a click or drag the "Should be" box takes the focus;
        keys keep it in the text, with the selection as it is, so the arrows
        (and Shift with them) can move on past the first word."""
        buf = self.view.get_buffer()
        bounds = buf.get_selection_bounds()
        if bounds:
            start, end = (it.get_offset() for it in bounds)
        else:
            start = end = buf.get_iter_at_mark(buf.get_insert()).get_offset()
        picked = vocabulary.word_range(self.text, start, end)
        buf.remove_tag(self.picked_tag, buf.get_start_iter(), buf.get_end_iter())
        if picked is None:
            self.first = self.last = None
        else:
            self.first, self.last = picked
            # The words alone, without punctuation around them ("Goul's",
            # not "Goul's?"): that's what gets replaced.
            a, b = vocabulary.core_span(self.text, *picked)
            buf.apply_tag(self.picked_tag, buf.get_iter_at_offset(a), buf.get_iter_at_offset(b))
            if by_mouse:
                # Drop the native selection so only the snapped words show.
                end_iter = buf.get_iter_at_offset(b)
                buf.select_range(end_iter, end_iter)
                self.entry.grab_focus_without_selecting()
        self._update()
        return False  # one-shot idle callback

    def _phrase(self):
        if self.first is None:
            return ""
        return vocabulary.phrase_for(self.text, self.first, self.last)

    # --- labels and the save button ----------------------------------------

    def _update(self):
        """Refresh the "It heard" line, the warnings and the save button."""
        phrase = self._phrase()
        replacement = self.entry.get_text().strip()
        if phrase:
            self.heard_label.set_markup(
                f"It heard: <b>{GLib.markup_escape_text(phrase)}</b>")
        else:
            self.heard_label.set_markup("<i>No word picked yet</i>")

        warnings = []
        if phrase and vocabulary.is_risky(phrase):
            warnings.append(
                f"“{phrase}” is a common word. Saving this changes it every "
                "time you say it, not just when TuxTalk mishears you.")
        if phrase:
            try:
                previous = vocabulary.existing_fix(phrase)
            except Exception as e:
                # Only the warning is missed; saving reads the commands again.
                logger.warning(f"Fix a Word: couldn't check for an earlier fix: {e}")
                previous = None
            if previous is not None and previous != replacement:
                warnings.append(
                    f"“{phrase}” is already fixed to “{previous}”. "
                    "Saving replaces that.")
        self._show_warning(" ".join(warnings))
        self.save_button.set_sensitive(bool(phrase and replacement and replacement != phrase))

    def _show_warning(self, text):
        if text:
            self.warning_label.set_markup(
                f'<span foreground="{_WARNING_COLOR}">{GLib.markup_escape_text(text)}</span>')
            self.warning_label.show()
        else:
            self.warning_label.hide()

    # --- saving -----------------------------------------------------------

    def _on_save(self):
        if not self.save_button.get_sensitive():
            return
        phrase = self._phrase()
        replacement = self.entry.get_text().strip()
        if vocabulary.is_risky(phrase) and not self._confirm_risky(phrase, replacement):
            return
        try:
            vocabulary.save_fix(phrase, replacement)
        except ConfigNotLoadedError:
            self._show_warning(
                "Your snippets file couldn't be read, so nothing was saved. "
                "Open Preferences, Words, to check it.")
            return
        except Exception as e:
            logger.error(f"Could not save word fix: {e}")
            self._show_warning(f"Couldn't save the fix: {e}")
            return
        logger.info("Saved a word fix to custom voice commands")
        if self.copy_check.get_active():
            if self.in_place:
                # Only the picked words change in the app: where they are in
                # the text, and what goes there.
                start, end = vocabulary.core_span(self.text, self.first, self.last)
                self.fixed_for_text = (start, end, replacement)
            else:
                self.copy_text(vocabulary.fixed_text(self.text, self.first, self.last,
                                                     replacement))
        self.window.destroy()

    def _confirm_risky(self, phrase, replacement):
        """Ask before fixing a common word everywhere; True to go ahead."""
        msg = message_dialog(
            transient_for=self.window, modal=True,
            message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.NONE,
            text=f"Always change “{phrase}” to “{replacement}”?")
        msg.format_secondary_text(
            f"“{phrase}” is a common word, so this will change it in every "
            "dictation, even when TuxTalk heard you right.")
        msg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        msg.add_button("Fix it anyway", Gtk.ResponseType.OK)
        answer = msg.run()
        msg.destroy()
        return answer == Gtk.ResponseType.OK

    # --- closing ------------------------------------------------------------

    def _on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()
            return True
        return False

    def _on_destroy(self, _widget):
        global _active_dialog
        _active_dialog = None


# How long the app gets to take the focus back after the window closes.
_REFOCUS_S = 0.5

# How long the desktop gets to read the last keys of a fix before this
# process, and its keyboard, go.
LAST_KEYS_S = 0.1


def _as_tuples(saved):
    """A window id read back from JSON, in tuples again as focused_window
    compares them (("tray", "{id}"), or ("own", ("tray", "{id}")))."""
    if not isinstance(saved, list):
        return saved
    return tuple(_as_tuples(part) for part in saved)


def fix_in_text(typed, fix, window, where=None):
    """Swap just the picked words in what TuxTalk *typed*, in the focused
    app. *fix* is (start, end, replacement), offsets in the typed text
    without its outer spaces, as the window showed it. The cursor, taken to
    be at the end of the dictation, steps back to the words and selects
    them; only if the app's selection reads back as those words
    (_selection_now) is the replacement typed and the cursor stepped forward
    again, nothing else retyped. The keys go only into *window* (its class)
    and, where the desktop says, *where* (its id, focused_window.look's),
    checked right before each batch of them goes down (the keys of one
    word go out in milliseconds). Never in a terminal, where Shift+Left
    selects nothing, nor a password field. True if done."""
    from . import keyboard_output, virtual_keyboard
    start, end, replacement = fix
    time.sleep(_REFOCUS_S)
    if why := _not_here(window, where):
        return _left_alone(why)
    core = typed.strip()
    # From the cursor back to the end of the picked words: the spaces after
    # the dictation, then whatever followed the words in it.
    back = (len(typed) - len(typed.rstrip())) + (len(core) - end)
    backend = keyboard_output.output_backend()
    keyboard_output.this_thread.into = (window, where)
    try:
        if not backend.press_keys([((), "LEFT")] * back + [(("SHIFT",), "LEFT")] * (end - start)):
            return _left_alone("keys")
        selected = _selection_now(core[start:end])
        # Moved since the dictation (a click, the arrow keys): the same count
        # of keys selected other words. Or the focus moved while it was read.
        why = ("unchecked" if selected is None else "moved" if selected != core[start:end]
               else _not_here(window, where))
        if why:
            backend.press_keys([((), "RIGHT")] * (back + 1))      # the cursor back
            return _left_alone(why)
        done = backend.type_text(replacement) and backend.press_keys([((), "RIGHT")] * back)
    except (virtual_keyboard.StoppedPartway, virtual_keyboard.GaveUp):
        done = False
    finally:
        keyboard_output.this_thread.into = None
    logger.info("Fix a Word: fixed it in the text" if done else "Fix a Word: couldn't fix the text")
    return bool(done)


# Why fix_in_text left the text alone, for the log and the notice.
_LEFT_ALONE = {
    "window": "another window had the focus",
    "password": "the cursor is in a password field",
    "terminal": "TuxTalk doesn't change text in a terminal",
    "moved": "the cursor wasn't where the dictation ended",
    "unchecked": "TuxTalk couldn't check that the cursor was still where the dictation ended",
    "keys": "the keys to get to the word couldn't be pressed",
}


def _not_here(window, where):
    """Why the fix mustn't go into the window that has the focus now (a key
    of _LEFT_ALONE), or None."""
    from . import focused_window
    from .window_classes import is_terminal_class
    now = focused_window.look()
    if focused_window.same_window((window, where), now) is False:
        return "window"
    if focused_window.is_password():
        return "password"
    return "terminal" if is_terminal_class(now[0] or window) else None


def _left_alone(why) -> bool:
    """Say why the text wasn't changed (the fix itself is saved); False."""
    from . import notify
    logger.info(f"Fix a Word: text left as it was ({_LEFT_ALONE[why]})")
    notify.notify("Your text wasn't changed", f"The fix is saved, but {_LEFT_ALONE[why]}."
                  if why != "terminal" else f"The fix is saved. {_LEFT_ALONE[why]}.")
    return False


# How long the app gets to report the keys' selection as its primary
# selection (the highlighted text).
SELECTION_WAIT_S = 0.5


def _selection_now(words):
    """The app's selection, read as the primary selection: no Ctrl+C, so
    the clipboard isn't touched, and it works on X11 too. *words* is what
    it should be, waited for a moment while the app catches up. None when
    it can't be read (the Flatpak has no wl-paste)."""
    deadline = time.monotonic() + SELECTION_WAIT_S
    while True:
        text = _read_primary_selection()
        if text == words or time.monotonic() >= deadline:
            return text
        time.sleep(0.05)


def _read_primary_selection():
    """The primary selection as text, or None."""
    from . import clipboard, keyboard_output
    if keyboard_output.is_wayland_session():
        return clipboard.read_primary()
    return Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY).wait_for_text()


def main(argv=None):
    """"fix that word": Fix a Word on the newest dictation, over the app
    being dictated into, as its own process (see the module docstring).
    With --in-place FILE (right after the dictation, in its window), FILE
    holds what was typed, and the fix goes into the app's text too."""
    import json
    import os
    import sys
    argv = sys.argv[1:] if argv is None else argv
    try:
        from . import desktop_identity, ui_style
        desktop_identity.apply()        # TuxTalk's icon on the window
        ui_style.prepare_windows()   # emoji that show, buttons clear of panels and docks
    except Exception as e:
        logger.warning(f"Couldn't set up the window's icon, emoji or size: {e}")
    from .clipboard import copy_text
    from .explain import place_over_active_window
    typed = window = where = None
    if len(argv) == 2 and argv[0] == "--in-place":
        try:
            with open(argv[1]) as f:
                data = json.load(f)
            typed, window, where = data["text"], data.get("window"), _as_tuples(data.get("where"))
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.debug(f"Fix a Word: no text to fix in place: {e}")
        finally:
            try:
                os.remove(argv[1])
            except OSError:
                pass
    entries = [typed.strip()] if typed and typed.strip() else get_entries()[:1]
    if not entries:
        show_fix_word_dialog(0, copy_text=copy_text)    # says there's nothing yet
        return 0
    in_place = bool(typed and typed.strip())
    if in_place and not os.environ.get("FLATPAK_ID"):
        # This process types the fix on a virtual keyboard of its own. Made
        # now, the desktop has taken it on (and, on X11, given it the user's
        # layout) long before the window closes. The service sets the user's
        # typing speed in its own main(); this process has to read it.
        from . import keyboard_output, virtual_keyboard
        from .config import load_config
        keyboard_output.key_delay_ms = load_config().key_delay
        virtual_keyboard.start()
    dialog = _FixWordDialog(entries, 0, copy_text, before_show=place_over_active_window,
                            in_place=in_place)
    dialog.window.connect("destroy", lambda _w: Gtk.main_quit())
    dialog.window.present()
    Gtk.main()
    if dialog.fixed_for_text is not None and fix_in_text(typed, dialog.fixed_for_text, window,
                                                         where):
        # The keyboard goes when this process ends, and with it any keys the
        # desktop hasn't read yet.
        time.sleep(LAST_KEYS_S)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
