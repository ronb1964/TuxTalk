"""Preferences, General tab: the Language dropdown.

One dropdown stands for three settings (language_mode, language,
auto_languages), which keep their meaning, so older configs and backups read
the same. Its last entry, Several languages, holds auto-detect to a list
ticked in a window of its own, for the Whisper models.
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from .prefs_tab import WARNING_AMBER, Tab

# The Language dropdown's entry for auto-detect held to a list of languages.
SEVERAL_LANGUAGES = "several"


def language_choice(config):
    """The Language dropdown's entry for these settings: "auto", a language
    code, or SEVERAL_LANGUAGES."""
    if config.get("language_mode", "auto") == "manual":
        return config.get("language") or "en"
    return SEVERAL_LANGUAGES if config.get("auto_languages") else "auto"


def apply_language_choice(config, choice):
    """Write a Language dropdown entry (not SEVERAL_LANGUAGES, which needs the
    list) into *config*."""
    if choice == "auto":
        config["language_mode"] = "auto"
        config["auto_languages"] = ""
    else:
        config["language_mode"] = "manual"
        config["language"] = choice


def several_languages_label(setting):
    """"Several: English, Spanish…", or "Several languages…" before any are picked."""
    from . import language_limit
    codes = language_limit.parse(setting)
    if not codes:
        return "Several languages…"
    return f"Several: {language_limit.describe(codes)}…"


def language_summary(config):
    """The language part of the setup line: "English", "English, Spanish",
    or "detects the language"."""
    from . import language_limit
    from .recommend import language_name
    choice = language_choice(config)
    if choice == "auto":
        return "detects the language"
    if choice == SEVERAL_LANGUAGES:
        return language_limit.describe(language_limit.parse(config.get("auto_languages", "")))
    return language_name(choice)


class LanguageRow(Tab):
    """The Language dropdown and its "Change list…" button, in the speech
    settings. *selected_model* tells it the model the Model dropdown shows;
    *on_picked* runs once a new list of several languages is chosen, for the
    warnings that depend on it."""

    def __init__(self, win, selected_model, on_picked):
        super().__init__(win)
        self._selected_model = selected_model
        self._on_picked = on_picked
        self.choice = language_choice(self.config)
        # The model the entries were made for: with Parakeet, Several
        # languages is left out.
        self.filled_for = None
        # Set while the entries are rebuilt or relabelled: the "changed" that
        # fires then is no pick.
        self.filling = False

    def build(self, grid, row, model):
        """The label and the dropdown at *row* of *grid*, filled for *model*."""
        label = Gtk.Label(label="Language 💡:", xalign=0)
        label.set_tooltip_text(
            "The language you speak.\n"
            "Detect automatically works for most people. Picking your language\n"
            "makes it a little faster, and it never takes you for another one.\n"
            "Several languages: it picks only from the ones you tick (Whisper models).")
        grid.attach(label, 0, row, 1, 1)
        self.combo = Gtk.ComboBoxText()
        self.combo.set_can_focus(True)
        self.combo.connect("button-press-event", self.win._on_combo_button_press)
        self.win._block_combo_scroll(self.combo)
        label.set_mnemonic_widget(self.combo)
        self.fill(model)
        self.combo.connect("changed", self._on_changed)
        # Picking the entry already selected does nothing, so the list of
        # several languages gets its own button, shown only with that entry.
        self.several_button = Gtk.Button(label="Change list…")
        self.several_button.set_no_show_all(True)
        self.several_button.connect("clicked", lambda _b: self.pick_several())
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.pack_start(self.combo, True, True, 0)
        box.pack_start(self.several_button, False, False, 0)
        grid.attach(box, 1, row, 1, 1)

    def follow_model(self, model):
        """Rebuild the entries when *model* changes between Parakeet and
        Whisper, and show Change list… with Several languages."""
        from .recommend import PARAKEET
        if (model == PARAKEET) != (self.filled_for == PARAKEET):
            self.fill(model)
        self.several_button.set_visible(self.choice == SEVERAL_LANGUAGES)

    def fill(self, model):
        """(Re)build the entries for *model* and select the current choice.

        Several languages is for the Whisper models: Parakeet always picks the
        language itself, so with Parakeet the entry is left out, unless it is
        the current choice (its warning then says it does nothing)."""
        from . import recommend
        self.filling = True
        try:
            self.combo.remove_all()
            self.combo.append("auto", "Detect automatically")
            for code, _name in recommend.LANGUAGES:
                self.combo.append(code, recommend.language_label(code))
            if model != recommend.PARAKEET or self.choice == SEVERAL_LANGUAGES:
                self.combo.append(SEVERAL_LANGUAGES,
                                  several_languages_label(self.config.get("auto_languages", "")))
            if not self.combo.set_active_id(self.choice):
                # A hand-edited code the list doesn't have: show it rather than
                # pretend it's another language.
                self.combo.append(self.choice, recommend.language_label(self.choice))
                self.combo.set_active_id(self.choice)
            self.filled_for = model
        finally:
            self.filling = False

    def _select(self, choice):
        """Show *choice* in the dropdown without acting on it."""
        self.filling = True
        try:
            self.combo.set_active_id(choice)
        finally:
            self.filling = False
        self.choice = choice

    def _on_changed(self, combo):
        """A language picked: written into the settings; Several languages
        opens its list first."""
        if self.filling:
            return
        choice = combo.get_active_id()
        if choice is None:
            return
        if choice == SEVERAL_LANGUAGES:
            if not self.pick_several():
                self._select(self.choice)   # cancelled
            return
        apply_language_choice(self.config, choice)
        self.choice = choice

    def pick_several(self):
        """Pick the languages auto-detect may choose from. True once the
        choice is made (OK), False when cancelled. One language ticked is the
        same as choosing it, and none is Detect automatically, so the dropdown
        then shows that instead."""
        from . import language_limit
        codes = self._ask_languages(
            set(language_limit.parse(self.config.get("auto_languages", ""))))
        if codes is None:
            return False
        if len(codes) < 2:
            choice = codes[0] if codes else "auto"
            apply_language_choice(self.config, choice)
        else:
            choice = SEVERAL_LANGUAGES
            self.update_config("language_mode", "auto")
            self.update_config("auto_languages", language_limit.to_setting(codes))
            self._relabel_several()
        self._select(choice)
        self._on_picked()
        return True

    def _relabel_several(self):
        """Name the languages now ticked in the Several entry."""
        label = several_languages_label(self.config["auto_languages"])
        # A new label on the selected entry fires "changed", which would open
        # the picker again (as the CUDA entry's label looped).
        self.filling = True
        try:
            for entry in self.combo.get_model():   # ComboBoxText rows are (text, id)
                if entry[1] == SEVERAL_LANGUAGES and entry[0] != label:
                    entry[0] = label
        finally:
            self.filling = False

    def _ask_languages(self, chosen):
        """The Several Languages window, with *chosen* ticked: the codes
        ticked at OK, or None when cancelled."""
        from . import language_limit
        dialog = Gtk.Dialog(title="Several Languages", transient_for=self.window, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("OK", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(16)
        box.set_spacing(10)
        checks = self._fill_language_window(box, chosen)
        dialog.show_all()
        try:
            if dialog.run() != Gtk.ResponseType.OK:
                return None
            return language_limit.parse(language_limit.to_setting(
                c for c, chk in checks.items() if chk.get_active()))
        finally:
            dialog.destroy()

    def _fill_language_window(self, box, chosen):
        """The Several Languages window's text and ticks, in *box*: {code: tick}."""
        from . import recommend

        def wrapped(markup):
            label = Gtk.Label(xalign=0)
            label.set_line_wrap(True)
            label.set_max_width_chars(60)
            label.set_markup(markup)
            box.pack_start(label, False, False, 0)

        # First thing in the window, in the warning amber: with Parakeet the
        # ticks change nothing, and a note at the bottom was easy to miss.
        if self._selected_model() == recommend.PARAKEET:
            wrapped(f'<span color="{WARNING_AMBER}" weight="bold">⚠️ You’re using '
                    "Parakeet, which always picks the language by itself. These ticks do "
                    "nothing with Parakeet. They only work with the Whisper models.</span>")
        wrapped("Tick the languages you dictate in. Auto-detect then picks only from these.\n"
                "To let it pick any language, leave them all unticked (<b>Untick all</b>).")

        # Three columns, in the pickers' order, each with its own name for itself.
        checks = {}
        table = Gtk.Grid(column_spacing=18, row_spacing=2)
        per_column = -(-len(recommend.LANGUAGES) // 3)
        for i, (code, _name) in enumerate(recommend.LANGUAGES):
            check = Gtk.CheckButton(label=recommend.language_label(code))
            check.set_active(code in chosen)
            checks[code] = check
            table.attach(check, i // per_column, i % per_column, 1, 1)
        box.pack_start(table, False, False, 0)

        clear = Gtk.Button(label="Untick all")
        clear.set_halign(Gtk.Align.START)
        clear.connect("clicked", lambda _b: [c.set_active(False) for c in checks.values()])
        box.pack_start(clear, False, False, 0)

        wrapped("<small>This is for the Whisper models. When Whisper thinks it hears a "
                "language that isn’t ticked, it listens again in the likeliest one that "
                "is. Tick just one and it’s the same as choosing that language by hand."
                "</small>")
        return checks
