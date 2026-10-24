"""Preferences, Words tab: Your Words (names and words the speech model
should spell your way, with how it mishears them) above the snippets
(something short to say for something longer to type)."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from .config import (ConfigNotLoadedError, load_custom_commands, merge_custom_command_edits,
                     save_custom_commands)
from .logger import setup_logger
from .prefs_tab import Tab
from .ui_style import show_message

logger = setup_logger(__name__)


def _section(vbox, title, text):
    """A heading in large bold, with the line under it that says what it's for."""
    header = Gtk.Label(xalign=0)
    header.set_markup(f'<span size="large"><b>{title}</b></span>')
    vbox.pack_start(header, False, False, 0)
    desc = Gtk.Label(label=text, xalign=0)
    desc.set_line_wrap(True)
    vbox.pack_start(desc, False, False, 0)


def _table(vbox, store, height):
    """A list of *store*'s rows, *height* pixels high, scrolling past that."""
    tree = Gtk.TreeView(model=store)
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_min_content_height(height)
    scrolled.set_max_content_height(height)
    scrolled.set_shadow_type(Gtk.ShadowType.IN)
    scrolled.add(tree)
    vbox.pack_start(scrolled, False, False, 0)
    return tree


def _buttons(vbox, *specs):
    """A row of buttons: (label, tooltip, handler, at the right end)."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    for label, tip, handler, at_end in specs:
        button = Gtk.Button(label=label)
        button.set_tooltip_text(tip)
        button.connect("clicked", handler)
        (box.pack_end if at_end else box.pack_start)(button, False, False, 0)
    vbox.pack_start(box, False, False, 0)


class WordsTab(Tab):
    """The Words tab. Snippets are saved by Apply and OK through save(), apart
    from the settings; the window asks changed() before it closes."""

    def __init__(self, win):
        super().__init__(win)
        self._word_rows = []        # (word, its mishearings), as on disk
        self._duplicate_warning_pending = False

    def build(self):
        """The tab's page: Your Words, each with the mishearings fixed to it,
        then the Snippets. Underneath, every mishearing and snippet is a
        custom voice command (custom_commands.toml); the ones that type one of
        Your Words (custom_words.txt) are that word's (vocabulary.py)."""
        from . import vocabulary
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        vbox.set_margin_top(15)
        vbox.set_margin_bottom(15)

        # Once: word fixes saved before this tab existed are listed with
        # their word, not among the snippets.
        try:
            if vocabulary.adopt_fixes():
                logger.info("Word fixes moved under Your Words")
        except Exception as e:
            logger.warning(f"Word fixes not moved under Your Words: {e}")

        self._build_words(vbox)
        spacer = Gtk.Box()
        spacer.set_size_request(-1, 6)
        vbox.pack_start(spacer, False, False, 0)
        self._build_snippets(vbox)

        # Load both lists, and follow the files while this window is open:
        # Fix a Word (tray or the button above) writes to them directly.
        self._fill_commands_store(load_custom_commands())
        self._watch_custom_commands_file()

        tips = Gtk.Label(xalign=0)
        tips.set_line_wrap(True)
        # A width to wrap at: without one the opening height counts this as one
        # line, it wraps to two, and the tab grows a scrollbar for the difference.
        tips.set_max_width_chars(90)
        tips.set_markup(
            '<small>Click a snippet to edit it. Use \\n in the text for a line break, and '
            'wrap it in "quotes" to type it exactly as written. Snippets are saved '
            'when you click Apply or OK; words are saved right away. Both work from '
            'your next dictation.</small>')
        vbox.pack_start(tips, False, False, 4)
        return vbox

    def _build_words(self, vbox):
        """Your Words: saved as soon as a word is saved or removed, like Fix a
        Word, which adds to this list from the tray too."""
        _section(vbox, "Your Words",
                 "Names and terms TuxTalk spells your way. Add what it hears them as, "
                 "and it fixes those too.")
        self.words_store = Gtk.ListStore(str, str)      # word, its mishearings
        # Fixed heights: a tree view measures its rows only once the window is
        # on screen, too late for the opening height (_fit_to_screen), so a
        # list sized by its rows opened squeezed. About six words; then it scrolls.
        self.words_tree = _table(vbox, self.words_store, 190)
        for i, (title, expand) in enumerate((("Spelled", False),
                                             ("Also fixes when it hears", True))):
            column = Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=i)
            column.set_expand(expand)
            column.set_min_width(150)
            self.words_tree.append_column(column)
        self.words_tree.connect("row-activated", lambda *_: self._on_edit_word(None))
        _buttons(vbox,
                 ("Add Word", "Add a name or term to spell your way", self._on_add_word, False),
                 ("Edit...", "Change the selected word or what TuxTalk hears it as",
                  self._on_edit_word, False),
                 ("Remove", "Remove the selected word", self._on_remove_word, False),
                 ("Fix a Word...", "Teach TuxTalk a word from one of your recent dictations",
                  self._on_fix_word, True))

    def _build_snippets(self, vbox):
        """The snippets: edited in place, saved with Apply or OK."""
        _section(vbox, "Snippets",
                 'Say a short phrase and TuxTalk types the full text. For example, '
                 'say "my email" and it types user@example.com.')
        self.commands_store = Gtk.ListStore(str, str)   # phrase, replacement
        self.commands_tree = _table(vbox, self.commands_store, 140)   # about four snippets
        # The hints are drawn only in an empty cell, never stored, so a row
        # added and left untouched saves nothing.
        for column_idx, (title, hint, edited, width) in enumerate((
                ("You Say", "new phrase", self._on_phrase_edited, 150),
                ("TuxTalk Types", "replacement text", self._on_replacement_edited, 200))):
            renderer = Gtk.CellRendererText()
            renderer.set_property("editable", True)
            renderer.set_property("placeholder-text", hint)
            renderer.connect("edited", edited)
            renderer.connect("editing-started", self._on_cell_editing_started, column_idx)
            column = Gtk.TreeViewColumn(title, renderer, text=column_idx)
            column.set_expand(True)
            column.set_min_width(width)
            self.commands_tree.append_column(column)
        _buttons(vbox,
                 ("Add Snippet", "Add a new snippet", self._on_add_command, False),
                 ("Edit...", "Change the selected snippet", self._on_edit_command, False),
                 ("Remove", "Remove the selected snippet", self._on_remove_command, False))

    def _on_cell_editing_started(self, renderer, editable, path, column_idx):
        """A snippet cell being edited: leaving it commits the text, and Tab
        moves to the next cell."""
        def on_focus_out(widget, event):
            text = widget.get_text()
            if column_idx == 0:
                self._on_phrase_edited(renderer, path, text)
            else:
                self._on_replacement_edited(renderer, path, text)
            return False

        editable.connect("focus-out-event", on_focus_out)

        def on_key_press(widget, event):
            """Tab keeps the edit and moves on to the next cell."""
            from gi.repository import Gdk
            if event.keyval == Gdk.KEY_Tab:
                text = widget.get_text()
                if column_idx == 0:
                    self._on_phrase_edited(renderer, path, text)
                else:
                    self._on_replacement_edited(renderer, path, text)

                widget.editing_done()
                widget.remove_widget()

                # Move to next column or next row
                if column_idx == 0:
                    # Move to replacement column (column 1)
                    GLib.idle_add(lambda: self.commands_tree.set_cursor(
                        Gtk.TreePath.new_from_string(path),
                        self.commands_tree.get_column(1),
                        True
                    ))
                else:
                    # Move to next row's phrase column (column 0)
                    current_path = Gtk.TreePath.new_from_string(path)
                    next_idx = current_path.get_indices()[0] + 1
                    if next_idx < len(self.commands_store):
                        GLib.idle_add(lambda: self.commands_tree.set_cursor(
                            Gtk.TreePath.new_from_indices([next_idx]),
                            self.commands_tree.get_column(0),
                            True
                        ))
                return True  # Consume the Tab event
            return False

        editable.connect("key-press-event", on_key_press)

    def _on_phrase_edited(self, renderer, path, new_text):
        """Handle editing of the phrase column.

        A phrase another row already has is refused: the snippets are saved
        by phrase, so one of the two rows would silently disappear.
        """
        phrase = new_text.strip().lower()
        if not phrase:
            return
        for word, phrases in self._word_rows:
            if phrase in phrases:
                show_message(self.window, Gtk.MessageType.WARNING,
                             "That phrase already fixes a word",
                             f"\u201c{phrase}\u201d is how TuxTalk hears {word}, under "
                             "Your Words. Change it there, or pick a different phrase.")
                return
        row_index = int(str(path))
        for i, row in enumerate(self.commands_store):
            if i != row_index and row[0].strip().lower() == phrase:
                self._warn_duplicate_phrase(phrase)
                return
        self.commands_store[path][0] = phrase

    def _warn_duplicate_phrase(self, phrase):
        """Say once that *phrase* already has a snippet.

        Focus-out and "edited" both commit the same edit, so the warning
        waits for the idle loop and a second call while it is pending adds
        nothing.
        """
        if self._duplicate_warning_pending:
            return
        self._duplicate_warning_pending = True

        def show():
            self._duplicate_warning_pending = False
            show_message(self.window, Gtk.MessageType.WARNING, "That phrase is already a snippet",
                         f"You already have a snippet for \u201c{phrase}\u201d. "
                         "Change that one, or pick a different phrase.")
            return False
        GLib.idle_add(show)

    def _on_replacement_edited(self, renderer, path, new_text):
        """A replacement typed into a snippet row."""
        self.commands_store[path][1] = new_text

    def _on_add_command(self, button):
        """Add an empty snippet row and start editing its phrase."""
        iter = self.commands_store.append(["", ""])
        path = self.commands_store.get_path(iter)
        self.commands_tree.set_cursor(path, self.commands_tree.get_column(0), True)

    def _apply_snippet_edit(self, path, phrase, text):
        """Put the Edit... window's *phrase* and *text* into snippet row *path*.
        False, with the row unchanged, for an empty phrase or one another row
        or word already has (_on_phrase_edited says which)."""
        wanted = phrase.strip().lower()
        if not wanted:
            return False
        self._on_phrase_edited(None, path, phrase)
        if self.commands_store[path][0] != wanted:
            return False
        self.commands_store[path][1] = text
        return True

    def _on_edit_command(self, _button):
        """Edit the selected snippet in a small window, the way Your Words'
        Edit... works. Like editing in the list, it's saved with Apply or OK."""
        model, it = self.commands_tree.get_selection().get_selected()
        if it is None:
            show_message(self.window, Gtk.MessageType.INFO, "Pick a snippet first",
                         "Click a snippet in the list, then Edit.")
            return
        path = str(model.get_path(it))
        dialog = Gtk.Dialog(title="Snippet", transient_for=self.window, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(16)
        box.set_spacing(8)
        entries = []
        for label, value, hint in (("You say", model[it][0], "my email"),
                                   ("TuxTalk types", model[it][1], "user@example.com")):
            box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
            entry = Gtk.Entry(text=value)
            entry.set_placeholder_text(hint)
            entry.set_width_chars(48)
            entry.set_activates_default(True)
            box.pack_start(entry, False, False, 0)
            entries.append(entry)
        note = Gtk.Label(xalign=0)
        note.set_line_wrap(True)
        note.set_max_width_chars(56)
        note.set_markup('<small>Use \\n in the text for a line break, and wrap it in '
                        '"quotes" to type it exactly as written.</small>')
        box.pack_start(note, False, False, 0)
        dialog.show_all()
        try:
            while dialog.run() == Gtk.ResponseType.OK:
                phrase, text = (e.get_text() for e in entries)
                if not phrase.strip():
                    show_message(self.window, Gtk.MessageType.WARNING, "Type what you'll say",
                                 "A snippet needs a phrase to say.")
                    continue
                if self._apply_snippet_edit(path, phrase, text):
                    break
        finally:
            dialog.destroy()

    def _on_remove_command(self, button):
        """Remove the selected snippet."""
        selection = self.commands_tree.get_selection()
        model, iter = selection.get_selected()
        if iter:
            model.remove(iter)

    def _watch_custom_commands_file(self):
        """Follow custom_commands.toml and custom_words.txt, which Fix a Word
        writes from outside this window."""
        from gi.repository import Gio
        from . import config, custom_words
        self._file_monitors = []
        for path in (config.CUSTOM_COMMANDS_PATH, custom_words.path()):
            try:
                monitor = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.NONE, None)
                monitor.connect("changed", lambda *_: GLib.idle_add(self._on_commands_file_changed))
                self._file_monitors.append(monitor)
            except Exception as e:
                logger.warning(f"Not watching {path} for outside changes: {e}")

    def _on_commands_file_changed(self):
        """Show what was saved from outside this window (Fix a Word), or by
        the word dialog.

        Your Words are saved as they're made, so that list is always shown as
        it is on disk. The snippets are left alone while the user has unsaved
        edits there: refilling would throw those away. Saving merges instead
        (save()), so nothing on disk is lost either way.
        """
        on_disk = load_custom_commands()
        rows, _snippets = self._split_commands(on_disk)
        if self._commands_from_store() == self._commands_loaded:
            self._fill_commands_store(on_disk)
        else:
            self._fill_words_store(rows)
        return False  # one-shot idle callback

    def _selected_word(self):
        """(word, its mishearings) for the selected Your Words row, or None."""
        model, it = self.words_tree.get_selection().get_selected()
        if it is None:
            return None
        index = model.get_path(it).get_indices()[0]
        rows = self._word_rows
        return rows[index] if index < len(rows) else None

    def _on_add_word(self, _button):
        self._edit_word_dialog(None, [])

    def _on_edit_word(self, _button):
        row = self._selected_word()
        if row is None:
            show_message(self.window, Gtk.MessageType.INFO, "Pick a word first",
                         "Click a word in the list, then Edit. Double-clicking it works too.")
            return
        self._edit_word_dialog(*row)

    def _on_remove_word(self, _button):
        """Remove the selected word, after asking, with what it fixes."""
        from . import vocabulary
        row = self._selected_word()
        if row is None:
            return
        word, phrases = row
        answer = show_message(
            self.window, Gtk.MessageType.QUESTION, f"Remove {word}?",
            "TuxTalk stops spelling it your way"
            + (f", and stops fixing what it hears instead ({', '.join(phrases)})."
               if phrases else "."),
            buttons=Gtk.ButtonsType.YES_NO)
        if answer != Gtk.ResponseType.YES:
            return
        try:
            vocabulary.remove_word(word)
        except Exception as e:
            # The type only: the message can hold the word, kept out of the log.
            logger.warning(f"A word wasn't removed: {type(e).__name__}")
            show_message(self.window, Gtk.MessageType.ERROR, "That word wasn't removed", str(e))
        self._on_commands_file_changed()

    def _edit_word_dialog(self, old, phrases):
        """Add a word (*old* None) or change one: its spelling and the phrases
        TuxTalk hears instead, one per line. Saved straight away."""
        from . import vocabulary
        dialog = Gtk.Dialog(title="Your Word" if old else "Add a Word",
                            transient_for=self.window, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(16)
        box.set_spacing(8)

        box.pack_start(Gtk.Label(label="Spelled", xalign=0), False, False, 0)
        entry = Gtk.Entry(text=old or "")
        entry.set_placeholder_text("TuxTalk")
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)

        heard = Gtk.Label(xalign=0)
        heard.set_markup("What TuxTalk hears instead, <b>one per line</b> (if anything)")
        heard.set_margin_top(6)
        box.pack_start(heard, False, False, 0)
        view = Gtk.TextView()
        view.set_left_margin(6)
        view.get_buffer().set_text("\n".join(phrases))
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(110)
        scrolled.set_min_content_width(380)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        scrolled.add(view)
        box.pack_start(scrolled, True, True, 0)

        how = Gtk.Label(xalign=0)
        how.set_line_wrap(True)
        how.set_max_width_chars(56)
        how.set_markup(
            "<small>Whisper models are told about your words before they listen. "
            "Every model, Parakeet included, fixes them when they come out split up "
            "or with the wrong capitals (\u201ctux talk\u201d becomes TuxTalk), and "
            "fixes what you list above.</small>")
        box.pack_start(how, False, False, 0)

        dialog.show_all()
        try:
            while dialog.run() == Gtk.ResponseType.OK:
                buf = view.get_buffer()
                lines = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).splitlines()
                try:
                    vocabulary.save_word(entry.get_text(), lines, old=old)
                except vocabulary.WordProblem as e:
                    show_message(self.window, Gtk.MessageType.WARNING, "That word wasn't saved",
                                 str(e))
                    continue
                except Exception as e:
                    logger.warning(f"A word wasn't saved: {type(e).__name__}")    # not the word
                    show_message(self.window, Gtk.MessageType.ERROR, "That word wasn't saved",
                                 str(e))
                break
        finally:
            dialog.destroy()
        self._on_commands_file_changed()

    def _on_fix_word(self, _button):
        from .fix_word_dialog import show_fix_word_dialog
        show_fix_word_dialog(0)

    def _commands_from_store(self):
        """The commands as shown in the list, in the form they are saved."""
        commands = {}
        for row in self.commands_store:
            phrase = row[0].strip().lower()
            if phrase:  # Only save non-empty phrases
                commands[phrase] = row[1]
        return commands

    def _split_commands(self, commands):
        """*commands* as (Your Words rows, snippets): see vocabulary.split_commands."""
        from . import custom_words, vocabulary
        return vocabulary.split_commands(custom_words.load(), commands)

    def _fill_words_store(self, rows):
        """Show Your Words, *rows* of (word, its mishearings)."""
        self._word_rows = rows
        self.words_store.clear()
        for word, phrases in rows:
            self.words_store.append([word, ", ".join(phrases)])

    def _fill_commands_store(self, commands):
        """Show *commands* in the two lists (the ones typing one of Your Words
        under it, the rest as snippets) and remember the snippets as the saved
        state, so that later only the user's own edits count as changes."""
        rows, snippets = self._split_commands(commands)
        self._fill_words_store(rows)
        self.commands_store.clear()
        for phrase, replacement in snippets.items():
            self.commands_store.append([phrase, replacement])
        self._commands_loaded = self._commands_from_store()

    def save(self):
        """Save custom commands from the list store.

        Only the user's edits are applied, on top of what is on disk right
        now, so a word fixed from the tray in the meantime is kept.

        Returns True if they were written; False if the existing file could
        not be read (the list on screen is then empty because of that, and
        writing it would delete every snippet); None if writing failed, which
        the user has been told about.
        """
        commands = merge_custom_command_edits(
            self._commands_loaded, self._commands_from_store(), load_custom_commands())
        try:
            save_custom_commands(commands)
            self._fill_commands_store(commands)
            return True
        except ConfigNotLoadedError as e:
            # Runs first in both on_apply and on_ok — letting this propagate
            # would abort the whole save and leave the window stuck open.
            logger.warning(f"Custom commands not saved: {e}")
            return False
        except OSError as e:
            # A full disk or read-only config folder: say so. Apply saves the
            # settings anyway; OK stays open so the edits aren't thrown away.
            logger.warning(f"Custom commands not saved: {e}")
            show_message(self.window, Gtk.MessageType.ERROR, "Your snippets weren't saved", str(e))
            return None

    def changed(self):
        """Whether the snippets were edited since they were loaded or saved."""
        return self._commands_from_store() != self._commands_loaded
