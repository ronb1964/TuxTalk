"""Preferences, General tab: Speech Recognition.

One bold line says how dictation is set up, with the recommended setup a
click away; a fold holds the Model and Device dropdowns and the Language row
(prefs_language.py). Picking a model or device that needs files first sets
them up: CUDA's download, or Vulkan's download and speed check. The model
itself downloads on Apply or OK (download_selected_model), and a download
that never finishes is rolled back (rollback_download).
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from .config import Settings
from .logger import setup_logger
from .prefs_language import LanguageRow, language_summary
from .prefs_tab import WARNING_AMBER, Tab
from .ui_style import message_dialog, show_message
from .whisper_vulkan import ENGINE_SIZE_TEXT

logger = setup_logger(__name__)

# What the Model dropdown says about each model; large-v3's depends on the
# computer (SpeechSection._large_v3_text).
MODEL_TEXT = {
    "tiny": "tiny — fastest, lowest accuracy",
    "base": "base — fast, basic accuracy",
    "small": "small — good balance, 99 languages",
    "medium": "medium — better accuracy",
    "large-v3": "large-v3 — best accuracy",
    "parakeet-v3": "Parakeet — recommended: most accurate, English + 24 European languages",
}


def model_setting_warning(model, device, language_mode, language, language_name="",
                          auto_languages=()):
    """What's wrong with this model/device/language combination, or "".

    Parakeet can't run on CUDA: with that device it quietly uses the processor
    while Preferences says "CUDA (GPU)". And it knows 25 languages, detects the
    one spoken and ignores the language setting, so choosing Japanese with it
    gets nothing useful.
    """
    if model != "parakeet-v3":
        return ""
    from .parakeet_engine import PARAKEET_LANGUAGES
    notes = []
    if device == "cuda":
        notes.append("Parakeet can't use CUDA, so it runs on the processor. To use your "
                     "graphics card, set the Device to Vulkan (any GPU).")
    if language_mode == "manual" and language and language not in PARAKEET_LANGUAGES:
        lang = language_name or language
        notes.append(f"Parakeet doesn't understand {lang}. It knows English and 24 European "
                     f"languages. For {lang}, choose one of the Whisper models.")
    if language_mode == "auto" and auto_languages:
        notes.append("“Several languages” does nothing with Parakeet, which always "
                     "picks the language by itself. It only works with the Whisper models.")
    return "\n".join(notes)


def device_label(device, model, cuda_ready):
    """What the Device dropdown calls *device* with *model* selected.

    CUDA is listed on every NVIDIA computer, so until its files are
    downloaded the label says how big they are. Parakeet can't use CUDA and
    runs on the processor with it, and the label says that too instead of
    promising the graphics card.
    """
    if device == "vulkan":
        from .whisper_vulkan import DEVICE_LABEL
        return DEVICE_LABEL
    if device != "cuda":
        return "CPU"
    from .parakeet_engine import is_parakeet
    if is_parakeet(model):
        return ("CUDA (Parakeet runs on the processor)" if cuda_ready
                else "CUDA (1.4 GB download, Parakeet runs on the processor)")
    return "CUDA (NVIDIA)" if cuda_ready else "CUDA (NVIDIA, 1.4 GB download)"


def download_model(parent, model):
    """Download *model*'s files for the processor or CUDA, with a progress
    bar, unless they're here already: (here now, cancelled). Success is told
    only once every file is there."""
    from .download_progress_dialog import DownloadTask, UnifiedDownloadDialog
    from .model_helper import MODEL_DISPLAY_SIZES, is_model_cached_fast, make_model_download_func
    from .recommend import model_title
    # A file check, no model loading: this runs on every Apply and OK. A
    # partial cache left by a cancelled download reports "not cached".
    if is_model_cached_fast(model):
        logger.info(f"Model {model} already cached")
        return True, False
    logger.info(f"Model {model} not found, downloading...")
    dialog = UnifiedDownloadDialog(
        parent=parent, title="Downloading the speech model",
        description="This may take a few minutes depending on your connection.")
    task = DownloadTask(model_title(model), MODEL_DISPLAY_SIZES.get(model, "unknown size"),
                        make_model_download_func(model))
    dialog.add_task(task)
    dialog.run()
    # The downloader's word is trusted only once the cache holds every file.
    ok = task.success and not task.cancelled and is_model_cached_fast(model)
    if ok:
        logger.info(f"Model {model} downloaded successfully")
        show_message(parent, Gtk.MessageType.INFO, "Model Downloaded Successfully!",
                     f"The {model_title(model)} model has been downloaded and is ready to use.")
    return ok, task.cancelled


def ask_whisper_model_for_cuda(parent):
    """Which Whisper model to use CUDA with, or None to keep Parakeet.

    Every Whisper model, with its download, so nobody downloads Small first
    only to want another one. Large-v3 is preselected: getting CUDA for an
    NVIDIA card is usually about the best accuracy.
    """
    from .model_helper import MODEL_DISPLAY_SIZES
    names = [("tiny", "Tiny", "fastest, least accurate"),
             ("base", "Base", "fast"),
             ("small", "Small", "a good balance"),
             ("medium", "Medium", "more accurate"),
             ("large-v3", "Large-v3", "most accurate")]
    box = message_dialog(transient_for=parent, modal=True,
                         message_type=Gtk.MessageType.QUESTION,
                         buttons=Gtk.ButtonsType.NONE, text="CUDA is ready")
    box.format_secondary_text(
        "CUDA speeds up the Whisper models. Parakeet can't use it, and it "
        "stays as fast as it is now.\n\nSwitch to a Whisper model on CUDA?")
    radios, group = {}, None
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    column.set_margin_start(24)
    for model, name, what in names:
        radio = Gtk.RadioButton.new_with_label_from_widget(
            group, f"{name}, {what} ({MODEL_DISPLAY_SIZES[model]})")
        group = group or radio
        radios[model] = radio
        column.pack_start(radio, False, False, 0)
    radios["large-v3"].set_active(True)
    box.get_content_area().pack_start(column, False, False, 6)
    box.add_button("Keep Parakeet", Gtk.ResponseType.NO)
    box.add_button("Switch", Gtk.ResponseType.YES)
    box.set_default_response(Gtk.ResponseType.YES)
    box.show_all()
    answer = box.run()
    chosen = next(m for m, r in radios.items() if r.get_active())
    box.destroy()
    return chosen if answer == Gtk.ResponseType.YES else None


class SpeechSection(Tab):
    """The Speech Recognition section of the General tab: the setup line,
    the recommended setup, the Parakeet Ultra offer, and the model, device
    and language settings, with the downloads they need."""

    def __init__(self, win):
        super().__init__(win)
        # What the Model dropdown goes back to when a model's setup is refused.
        self._last_selected_model = self.config.get("model")
        # Set while the Model dropdown is changed by code, not by a pick.
        self._updating_model = False
        self._cuda_ready = False
        # The speed check just found the graphics chip slower (not saved yet).
        self._vulkan_slower_now = False

    def build(self, grid, row):
        """The section at *row* of *grid*; returns the next free row."""
        row = self.win._section_header(grid, row, "Speech Recognition")
        row = self._build_status(grid, row)
        # No summary: the bold line above already says how it's set.
        setup = self.win._fold(grid, row, "Change model, device or language",
                               "Hide model, device and language").content
        # Which models run on which device: too many combinations to remember.
        compat_btn = Gtk.Button(label="What works with what?")
        compat_btn.set_halign(Gtk.Align.START)
        compat_btn.connect("clicked", self._show_compatibility)
        setup.attach(compat_btn, 0, 0, 2, 1)
        self._build_model_row(setup, 1)
        self._build_device_row(setup, 2)
        self.language = LanguageRow(self.win, self.selected_model, self.update_hint)
        self.language.build(setup, 3, self.config.get("model", Settings.model))
        for combo in (self.model_combo, self.device_combo, self.language.combo):
            combo.connect("changed", lambda *_: self.update_hint())
        self.update_hint()
        return row + 1

    def _build_status(self, grid, row):
        """The setup line, the recommended row, the Parakeet Ultra offer and
        the warning; returns the next free row."""
        # One line for the setup first run picked; the dropdowns behind it are
        # for the few who want something else.
        self.setup_summary = Gtk.Label(xalign=0)
        self.setup_summary.set_line_wrap(True)
        grid.attach(self.setup_summary, 0, row, 2, 1)

        # The setup first run recommends, one click away (Tray → Performance →
        # Recommended does the same), so trying other settings has an easy way back.
        rec_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.recommended_label = Gtk.Label(xalign=0)
        self.recommended_label.set_line_wrap(True)
        rec_row.pack_start(self.recommended_label, True, True, 0)
        self.use_recommended_btn = Gtk.Button(label="Use Recommended")
        self.use_recommended_btn.connect("clicked", self._on_use_recommended_clicked)
        # Shown or hidden by _update_recommended_row; the window's show_all()
        # would otherwise bring it back next to the tick.
        self.use_recommended_btn.set_no_show_all(True)
        rec_row.pack_end(self.use_recommended_btn, False, False, 0)
        grid.attach(rec_row, 0, row + 1, 2, 1)

        # Parakeet Ultra for someone still on the original Parakeet: who said
        # Not Now to the tray's one-time offer, or came back to Parakeet later.
        # Hidden otherwise (_update_parakeet_row).
        update_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        update_label = Gtk.Label(label="A more accurate Parakeet is available.", xalign=0)
        update_label.show()
        update_row.pack_start(update_label, True, True, 0)
        self.parakeet_update_btn = Gtk.Button()
        self.parakeet_update_btn.connect("clicked", self._on_update_parakeet_clicked)
        self.parakeet_update_btn.show()
        update_row.pack_end(self.parakeet_update_btn, False, False, 0)
        update_row.set_no_show_all(True)
        self.parakeet_update_row = update_row
        grid.attach(update_row, 0, row + 2, 2, 1)

        # A warning for model/device/language combinations that don't do what
        # they say: Parakeet with CUDA runs on the processor, and Parakeet
        # ignores a language it doesn't know. Outside the fold, so a closed
        # fold can't hide it. Hidden while there's nothing to say.
        self.warning = Gtk.Label(xalign=0)
        self.warning.set_line_wrap(True)
        self.warning.set_max_width_chars(60)
        self.warning.set_no_show_all(True)
        grid.attach(self.warning, 0, row + 3, 2, 1)
        return row + 4

    def _build_model_row(self, setup, row):
        """The Model label and dropdown at *row* of the fold."""
        label = Gtk.Label(label="Model 💡:", xalign=0)
        label.set_tooltip_text(
            "Speech model - choose based on your needs:\n\n"
            "• Parakeet (670 MB): recommended. The most accurate for English\n"
            "  and 24 European languages, and fast even on the processor.\n"
            "  On a graphics card, set Device to Vulkan.\n\n"
            "Whisper models, for the other 75 or so languages:\n"
            "• tiny (78 MB): Fastest, basic accuracy - quick notes\n"
            "• base (148 MB): Fast, good accuracy - casual use\n"
            "• small (486 MB): Balanced - a good start on the processor\n"
            "• medium (1.5 GB): Slower, very accurate - professional use\n"
            "• large-v3 (3.1 GB): Whisper's best - needs a graphics card\n"
            "  (Sizes are for the processor; the Vulkan copies are about half.)\n"
            "  ⚠️ Takes 30-60 seconds to load initially\n\n"
            "Larger Whisper models provide:\n"
            "• Better word recognition (technical terms, proper nouns)\n"
            "• Improved punctuation and context awareness\n"
            "• Better handling of accents and background noise"
        )
        setup.attach(label, 0, row, 1, 1)
        from .model_helper import OFFERED_MODELS
        # Rows: [model id, what the dropdown says].
        self.model_store = Gtk.ListStore(str, str)
        for model in OFFERED_MODELS:
            text = self._large_v3_text() if model == "large-v3" else MODEL_TEXT[model]
            self.model_store.append([model, text])
        combo = self.model_combo = Gtk.ComboBox.new_with_model(self.model_store)
        # Column 0 holds the model id: without this, set_active_id() is a
        # silent no-op (plain ComboBox has no id column by default).
        combo.set_id_column(0)
        combo.set_can_focus(True)
        self.win._block_combo_scroll(combo)
        renderer = Gtk.CellRendererText()
        combo.pack_start(renderer, True)
        combo.add_attribute(renderer, "text", 1)
        label.set_mnemonic_widget(combo)
        if not combo.set_active_id(self.config.get("model", Settings.model)):
            combo.set_active_id(Settings.model)
        combo.connect("changed", self._on_model_changed)
        setup.attach(combo, 1, row, 1, 1)

    def _large_v3_text(self):
        """What the large-v3 entry says: what running it on this computer takes."""
        if self.win._check_cuda_availability():
            return MODEL_TEXT["large-v3"]
        if self.win._has_nvidia():
            # Selectable: picking it offers CUDA's download.
            return "large-v3 — click to download required CUDA libraries"
        if self.win._vulkan_offered():
            return "large-v3 — Whisper's best, on your graphics card (Vulkan)"
        return "large-v3 — needs a graphics card (none found)"

    def _relabel_large_v3(self):
        """CUDA is here now: large-v3 no longer needs its download note."""
        for row in self.model_store:
            if row[0] == "large-v3":
                row[1] = MODEL_TEXT["large-v3"]

    def _build_device_row(self, setup, row):
        """The Device label and dropdown at *row* of the fold."""
        label = Gtk.Label(label="Device:", xalign=0)
        setup.attach(label, 0, row, 1, 1)
        self.device_combo = Gtk.ComboBoxText()
        self.device_combo.set_can_focus(True)
        self.device_combo.connect("button-press-event", self.win._on_combo_button_press)
        self.win._block_combo_scroll(self.device_combo)
        label.set_mnemonic_widget(self.device_combo)
        self.refresh_device_options()
        # What _on_device_changed falls back to when a setup is refused.
        self._device_before = self.config.get("device", "cpu")
        self.device_combo.connect("changed", self._on_device_changed)
        setup.attach(self.device_combo, 1, row, 1, 1)

    def selected_model(self):
        """The model the Model dropdown shows."""
        return self.model_combo.get_active_id() or ""

    def update_hint(self):
        """The setup line, the recommended row and the warning, for what the
        dropdowns show now."""
        # Rebuilding the Language entries fires its "changed", which lands
        # here again before the rebuild is done: the call that started it
        # finishes the job. Without this, switching between Parakeet and
        # Whisper looped until Preferences crashed.
        if self.language.filling:
            return
        model = self.selected_model()
        self._relabel_cuda_option(model)
        self.language.follow_model(model)
        self._update_recommended_row(model)
        from . import language_limit
        from .recommend import language_name
        code = self.config.get("language", "")
        message = model_setting_warning(
            model, self.device_combo.get_active_id() or "",
            self.config.get("language_mode", "auto"), code, language_name(code),
            language_limit.parse(self.config.get("auto_languages", "")))
        if message:
            # Amber and bold, so it can't be missed.
            self.warning.set_markup(f'<span color="{WARNING_AMBER}" weight="bold">⚠️ '
                                    f'{GLib.markup_escape_text(message)}</span>')
            self.warning.show()
        else:
            self.warning.hide()

    # --- Recommended setup and "What works with what?" (recommend.py) ---------

    def _recommendation(self):
        """(hardware, the setup recommend.py picks) for the language chosen in
        this window right now, not only the saved one."""
        from . import recommend
        shown = self._shown_language()
        hw = recommend.detect_hardware(shown)
        return hw, recommend.recommend(recommend.effective_language(shown), hw)

    def _shown_language(self):
        """The language settings as this window shows them, for recommend.py."""
        from types import SimpleNamespace
        return SimpleNamespace(language_mode=self.config.get("language_mode", "auto"),
                               language=self.config.get("language", ""),
                               dictation_language=self.config.get("dictation_language", ""),
                               vulkan_slower=(self.config.get("vulkan_slower", False)
                                              or self._vulkan_slower_now))

    def _update_recommended_row(self, model):
        """The setup line, and 'Recommended for this computer: …  [Use
        Recommended]', or a tick once the dropdowns already show it."""
        from . import recommend
        label = self.recommended_label
        try:
            hw, rec = self._recommendation()
        except Exception as e:
            logger.warning(f"Could not work out the recommended setup: {e}")
            label.get_parent().set_no_show_all(True)
            label.get_parent().hide()
            return
        device = self.device_combo.get_active_id()
        self.setup_summary.set_markup("<b>{}</b> · {}".format(
            GLib.markup_escape_text(recommend.setup_title(model, device or "cpu", hw)),
            GLib.markup_escape_text(language_summary(self.config))))
        self._update_parakeet_row(model)
        if model == rec.model and device == rec.device:
            label.set_markup('<span color="#6db896">✓ You\'re on the recommended setup '
                             f'({GLib.markup_escape_text(rec.title)})</span>')
            self.use_recommended_btn.hide()
        else:
            label.set_markup('<span color="#888888">Recommended for this computer:</span> '
                             f'{GLib.markup_escape_text(rec.title)}')
            self.use_recommended_btn.show()

    def _parakeet_update_device(self, model):
        """Where the setup shown here would still run the original Parakeet
        (model_helper.parakeet_update_needed), or None."""
        from types import SimpleNamespace
        from .model_helper import parakeet_update_needed
        return parakeet_update_needed(SimpleNamespace(
            model=model, device=self.device_combo.get_active_id() or "cpu"))

    def _update_parakeet_row(self, model):
        """Offer Parakeet Ultra while the setup shown would run the original."""
        device = self._parakeet_update_device(model)
        if device is None:
            self.parakeet_update_row.hide()
            return
        from .model_helper import parakeet_update_size
        self.parakeet_update_btn.set_label(f"Update Parakeet ({parakeet_update_size(device)})")
        self.parakeet_update_row.show()

    def _on_update_parakeet_clicked(self, _button):
        """Download Parakeet Ultra, then restart dictation onto it."""
        from .model_helper import download_parakeet_update
        model = self.model_combo.get_active_id()
        device = self._parakeet_update_device(model)
        if device is None:
            return
        updated = download_parakeet_update(device, self.window)
        if updated is None:
            show_message(self.window, Gtk.MessageType.ERROR, "The download did not finish",
                         "Check your internet connection and try again.")
        self._update_parakeet_row(model)
        if updated:
            self.win.restart_service()

    def _on_use_recommended_clicked(self, _button):
        """Set the dropdowns to the recommended setup, as Tray → Performance →
        Recommended does: on Vulkan, its download and speed check first, and
        the processor version if the graphics chip turns out slower. OK or
        Apply puts it in effect, like any other choice."""
        from . import recommend
        from .vulkan_setup_dialogs import ensure_files, run_speed_check
        _hw, rec = self._recommendation()
        model, device = rec.model, rec.device
        if device == "vulkan":
            if not ensure_files(self.window, model, confirm=True):
                return                      # cancelled: nothing changes
            if not run_speed_check(self.window, model):
                # The speed check saved this itself; the row's recommendation
                # needs it too, or it keeps offering the graphics card.
                self._vulkan_slower_now = True
                device = "cpu"
                model = recommend.recommend(recommend.effective_language(
                    self._shown_language()), recommend.Hardware(None), model=model).model
        self.set_choice("model", model)
        self.set_choice("device", device)
        self.update_hint()

    def _show_compatibility(self, _button):
        """Which models run on the processor, Vulkan and CUDA, and which of
        those apply to this computer. Explains only; changes nothing."""
        from . import recommend
        hw, _rec = self._recommendation()
        slower = self._shown_language().vulkan_slower
        dialog = Gtk.Dialog(title="What works with what", transient_for=self.window, modal=True)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        box = dialog.get_content_area()
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(16)
        box.set_spacing(10)

        grid = Gtk.Grid(column_spacing=24, row_spacing=8)
        for col, head in enumerate(("", "Processor (CPU)", "Vulkan (any GPU)", "CUDA (NVIDIA)")):
            label = Gtk.Label(xalign=0)
            label.set_markup(f"<b>{head}</b>")
            grid.attach(label, col, 0, 1, 1)
        for row, (name, _models, *cells) in enumerate(recommend.COMPATIBILITY, start=1):
            grid.attach(Gtk.Label(label=name, xalign=0), 0, row, 1, 1)
            for col, (works, note) in enumerate(cells, start=1):
                mark, colour = ("✓", "#6db896") if works else ("✗", WARNING_AMBER)
                text = f"{mark} {note}".strip()
                cell = Gtk.Label(xalign=0)
                cell.set_markup(f'<span color="{colour}">{GLib.markup_escape_text(text)}</span>')
                grid.attach(cell, col, row, 1, 1)
        box.pack_start(grid, False, False, 0)

        for text in (*recommend.COMPATIBILITY_NOTES, recommend.this_computer(hw, slower)):
            note = Gtk.Label(label=text, xalign=0)
            note.set_line_wrap(True)
            note.set_max_width_chars(70)
            box.pack_start(note, False, False, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    # --- The Model dropdown -----------------------------------------------------

    def _on_model_changed(self, combo):
        """A model picked. On Vulkan, any model it runs is simply chosen.
        Elsewhere large-v3 needs CUDA's files (_set_up_large_v3) and a word
        about its 3 GB download first."""
        if self._updating_model:
            return
        # Several choices below open dialogs; close the list first so it isn't
        # left drawn on top of them.
        combo.popdown()
        model = combo.get_active_id()
        if model is None:
            return
        # On Vulkan, whisper.cpp runs every Whisper model, large-v3 included,
        # without NVIDIA's CUDA files, and its own model file is fetched on
        # Apply (download_selected_model).
        if self.config.get("device") == "vulkan":
            from . import whisper_vulkan
            if whisper_vulkan.supports_model(model):
                self._choose_model(model)
                return
        if model == "large-v3":
            if not self.win._check_cuda_availability():
                self.set_choice("model", self._last_selected_model)
                self._set_up_large_v3()
                return
            if not self._confirm_large_v3_download():
                self.set_choice("model", self._last_selected_model)
                return
        self._choose_model(model)

    def _choose_model(self, model):
        """*model* picked and kept: OK or Apply puts it in effect."""
        self._last_selected_model = model
        self.update_config("model", model)

    def _confirm_large_v3_download(self):
        """Whether to go on with large-v3, which isn't downloaded yet and is
        slow to load; True at once when it's already here (a file check, no
        model loading on the main thread)."""
        from .model_helper import is_model_cached_fast
        if is_model_cached_fast("large-v3"):
            return True
        return show_message(
            self.window, Gtk.MessageType.WARNING, "Large Model Selected",
            "The large-v3 model is approximately 3 GB in size.\n\n"
            "⏱️  First-time load: 30-60 seconds\n"
            "⏱️  Subsequent loads: 10-20 seconds\n\n"
            "The application will not respond to your dictation hotkey "
            "until the model has fully loaded.\n\n"
            "💡 Click OK here to confirm, then click Apply or OK in Preferences "
            "to start the download.\n\n"
            "Do you want to proceed with this model?",
            buttons=Gtk.ButtonsType.OK_CANCEL) != Gtk.ResponseType.CANCEL

    def _set_up_large_v3(self):
        """large-v3 picked without CUDA's files: on NVIDIA, Vulkan or CUDA;
        on AMD or Intel graphics, Vulkan; with no graphics card, why not.
        The Model dropdown already shows the model before, and only a
        finished setup selects large-v3."""
        if self.win._has_nvidia():
            self._large_v3_on_nvidia()
        elif self.win._vulkan_offered():
            if show_message(
                    self.window, Gtk.MessageType.QUESTION,
                    "Large-v3 needs your graphics card",
                    "Large-v3 can run on your AMD or Intel graphics through Vulkan. "
                    f"That's a one-time download of a {ENGINE_SIZE_TEXT} graphics "
                    "engine and Large-v3 in its format (1 GB), then a quick check that "
                    "your graphics chip is really faster than your processor.",
                    buttons=Gtk.ButtonsType.OK_CANCEL) == Gtk.ResponseType.OK:
                self._switch_to_vulkan_for_model("large-v3", confirm=False)
        else:
            show_message(
                self.window, Gtk.MessageType.WARNING, "Graphics Card Required",
                "The 'large-v3' model needs a graphics card to run at a usable speed.\n\n"
                "TuxTalk didn't find one it can use on this computer.",
                keep_above=True)

    def _large_v3_on_nvidia(self):
        """Two ways to run large-v3 on an NVIDIA card: Vulkan (light, about
        the same speed measured on an RTX 4070 Super) or CUDA (TuxTalk's
        long-standing NVIDIA setup). Without Vulkan, CUDA is asked about
        before its 4.4 GB download."""
        from .vulkan_setup_dialogs import FULL, LIGHT, choose_light_or_full
        if self.win._vulkan_offered():
            choice = choose_light_or_full(self.window, "large-v3")
        else:
            choice = FULL if show_message(
                self.window, Gtk.MessageType.QUESTION, "Downloads Required",
                "The 'large-v3' model needs two downloads:\n\n"
                "  • CUDA GPU Libraries   (~1.4GB)\n"
                "  • Large-v3 AI Model    (~3GB)\n\n"
                "Total: ~4.4GB, a one-time download.\n\n"
                "Would you like to download both now?",
                buttons=Gtk.ButtonsType.YES_NO) == Gtk.ResponseType.YES else None
        if choice == LIGHT:
            self._switch_to_vulkan_for_model("large-v3")
        elif choice == FULL:
            self._download_cuda_and_large_v3()

    def _download_cuda_and_large_v3(self):
        """CUDA's files and large-v3 in one window; both there selects them."""
        from .download_progress_dialog import show_unified_download_dialog
        results = show_unified_download_dialog(cuda=True, model="large-v3", parent=self.window)
        cuda_ok = results.get("CUDA Libraries", {}).get("success", False)
        model_ok = results.get("large-v3 AI Model", {}).get("success", False)
        if cuda_ok:
            self._relabel_large_v3()
            self.refresh_device_options()
        if cuda_ok and model_ok:
            self.set_choice("model", "large-v3")
            self.set_choice("device", "cuda")

    # --- The Device dropdown ----------------------------------------------------

    def refresh_device_options(self):
        """Rebuild the Device dropdown.

        CUDA is listed on every NVIDIA computer, like Vulkan, not only once
        its files are downloaded: people looking for their graphics card look
        here. Picking it before the download offers the download
        (_on_device_changed).
        """
        combo = self.device_combo
        combo.remove_all()
        combo.append("cpu", "CPU")
        tooltip = "Processing device for AI transcription:\n• CPU: works on all computers, slower"

        self._cuda_ready = self.win._check_cuda_availability()
        if self._cuda_ready or self.win._has_nvidia():
            combo.append("cuda", device_label("cuda", self.config.get("model"), self._cuda_ready))
            tooltip += ("\n• CUDA (NVIDIA): much faster for the Whisper models, a one-time "
                        "1.4 GB download. Parakeet can't use it")
        # Set to CUDA without its files, the service runs on the processor,
        # so the dropdown shows CPU rather than claim CUDA.
        if self.config["device"] == "cuda" and not self._cuda_ready:
            self.config["device"] = "cpu"
            # Only the display changes: a saved "cuda" stays on disk unless
            # the user picks another device, so an unrelated OK can't drop
            # the choice while CUDA's files are missing for a while.
            if self.win._config_at_open.get("device") == "cuda":
                self.win._config_at_open["device"] = "cpu"

        # Any graphics card through Vulkan (whisper_vulkan.py): the only GPU
        # option on AMD / Intel, and the light one on NVIDIA. Offered when a
        # graphics chip is present, and kept if it is already the setting.
        try:
            from . import whisper_vulkan
            if whisper_vulkan.is_offered() or self.config["device"] == "vulkan":
                combo.append("vulkan", whisper_vulkan.DEVICE_LABEL)
                tooltip += ("\n• Vulkan (any GPU): AMD, Intel or NVIDIA. On NVIDIA about "
                            f"as fast as CUDA for a {ENGINE_SIZE_TEXT} download (one-time, "
                            "with a speed check). Also runs Parakeet on the graphics chip")
        except Exception as e:
            logger.warning(f"Could not check for Vulkan graphics: {e}")

        combo.set_active_id(self.config["device"])
        combo.set_tooltip_text(tooltip)

    def _relabel_cuda_option(self, model):
        """Retell the CUDA entry for *model*: with Parakeet it runs on the
        processor. A new label on the selected entry fires "changed", which
        brings this back here: writing only a label that differs ends that
        (it looped until Preferences crashed)."""
        combo = self.device_combo
        label = device_label("cuda", model, self._cuda_ready)
        # Nor may the new label read as picking CUDA: with Parakeet that asks
        # which Whisper model to use on CUDA, unasked.
        combo.handler_block_by_func(self._on_device_changed)
        try:
            for row in combo.get_model():
                if row[1] == "cuda" and row[0] != label:   # ComboBoxText rows are (text, id)
                    row[0] = label
        finally:
            combo.handler_unblock_by_func(self._on_device_changed)

    def _on_device_changed(self, combo):
        """Switching to Vulkan downloads what it needs and checks it is
        actually faster than the processor before keeping it. CUDA without
        its files offers the download, and CUDA with Parakeet asks for a
        Whisper model. Any other device is simply chosen."""
        device = combo.get_active_id()
        if device is None:
            return
        from .parakeet_engine import is_parakeet
        if device == "cuda" and not self._cuda_ready:
            then = self._finish_switch_to_cuda
        elif device == "cuda" and is_parakeet(self.config.get("model")):
            then = self._finish_cuda_for_parakeet
        elif device == "vulkan" and self._device_before != "vulkan":
            then = self._finish_switch_to_vulkan
        else:
            self.update_config("device", device)
            self._device_before = device
            return
        # These show dialogs: run them once the dropdown has closed, or its
        # list stays drawn on top of them.
        combo.popdown()
        GLib.idle_add(then, combo)

    def _finish_switch_to_vulkan(self, combo):
        """Keep Vulkan if its setup succeeds, otherwise put the dropdown back."""
        if self._setup_vulkan(self.config.get("model")):
            self.update_config("device", "vulkan")
            self._device_before = "vulkan"
        else:
            self.set_choice("device", self._device_before)
        return False        # run once

    def _finish_switch_to_cuda(self, combo):
        """CUDA picked before its files are downloaded. The dropdown goes back
        to the device in use first: if the download is refused or fails, or
        Parakeet is kept, nothing changed. Once downloaded,
        _after_cuda_download() selects CUDA itself, through Apply."""
        self.set_choice("device", self._device_before)
        self._download_cuda()
        return False        # run once

    def _finish_cuda_for_parakeet(self, combo):
        """CUDA picked, already downloaded, with Parakeet selected. Choosing
        a Whisper model selects it and CUDA, applied by OK or Apply like any
        other choice (Apply downloads the model). Keep Parakeet puts the
        dropdown back: under "CUDA" it would only run on the processor."""
        self.set_choice("device", self._device_before)
        model = ask_whisper_model_for_cuda(self.window)
        if model is not None:
            self.set_choice("model", model)
            self.set_choice("device", "cuda")
        return False        # run once

    def _setup_vulkan(self, model, confirm=True):
        """Download and speed-check Vulkan for *model* (vulkan_setup_dialogs).
        True if TuxTalk should use the graphics chip."""
        from .vulkan_setup_dialogs import set_up
        return set_up(self.window, model, confirm=confirm)

    def _switch_to_vulkan_for_model(self, model, confirm=True):
        """Set up Vulkan for *model* (download plus speed check). If it's kept,
        select both the model and the Vulkan device; otherwise change nothing."""
        if not self._setup_vulkan(model, confirm=confirm):
            return
        self.set_choice("model", model)
        self.set_choice("device", "vulkan")

    def _download_cuda(self):
        """Ask, then download CUDA's 1.4 GB of files with a progress bar.

        On success the dropdowns are rebuilt and _after_cuda_download()
        switches to CUDA (asking for a Whisper model first on Parakeet).
        """
        import os
        from . import cuda_helper
        from .config import get_data_dir
        cuda_path = os.path.join(get_data_dir(), "cuda")
        if show_message(
                self.window, Gtk.MessageType.QUESTION, "Download CUDA Libraries?",
                "CUDA runs the Whisper models on your NVIDIA graphics card. It needs "
                "about 1.4 GB of NVIDIA's files, a one-time download.\n\n"
                f"The files will be stored in {cuda_path} and may take several minutes "
                "to download.\n\nContinue with download?",
                buttons=Gtk.ButtonsType.YES_NO) != Gtk.ResponseType.YES:
            return

        def on_success():
            self.win.advanced_tab.system_check.check_gpu_status()
            self.refresh_device_options()
            self._relabel_large_v3()
            self._after_cuda_download()

        cuda_helper.show_cuda_download_dialog(parent=self.window, on_success_callback=on_success)

    def _after_cuda_download(self):
        """Switch to CUDA now that it's downloaded, through Apply.

        Never by saving directly: that moves the baseline Apply diffs against,
        so the switch would be saved without restarting the service. Parakeet
        can't use CUDA, and switching its device would move it from the
        graphics card to the processor, so its user is asked first.
        """
        from .parakeet_engine import is_parakeet
        if is_parakeet(self.config.get("model")):
            model = ask_whisper_model_for_cuda(self.window)
            if model is None:
                return
            self.set_choice("model", model)
        self.set_choice("device", "cuda")
        self.win.on_apply(None)

    def set_choice(self, key, value):
        """Set the model or device and its dropdown without the dropdown's
        own reactions (model-picked dialogs, Vulkan's setup download)."""
        self.config[key] = value
        if key == "model":
            self._updating_model = True
            try:
                self.model_combo.set_active_id(value)
            finally:
                self._updating_model = False
            self._last_selected_model = value
        elif key == "device":
            # What _on_device_changed falls back to when a setup is refused.
            self._device_before = value
            self.device_combo.handler_block_by_func(self._on_device_changed)
            try:
                self.device_combo.set_active_id(value)
            finally:
                self.device_combo.handler_unblock_by_func(self._on_device_changed)

    # --- Apply and OK -----------------------------------------------------------

    def download_selected_model(self):
        """Download the model chosen, for the device chosen, if it isn't here
        yet. True once it is (or nothing was needed). A failed download says
        so; a cancelled one stops quietly."""
        # The model that was saved (the merged config): if the tray switched
        # models while this window was open, the dropdown may be stale.
        model = self.config.get("model")
        if not model:
            return True
        if self.config.get("device") == "vulkan":
            from . import whisper_vulkan
            if whisper_vulkan.supports_model(model):
                from .vulkan_setup_dialogs import ensure_files
                return ensure_files(self.window, model, False)
        success, cancelled = download_model(self.window, model)
        if not success and not cancelled:
            show_message(self.window, Gtk.MessageType.ERROR, "Model download failed!",
                         "Failed to download the speech model. "
                         "Please check your internet connection.")
        return success

    def rollback_download(self, previous):
        """Undo a model or device change whose download never completed.

        The config is saved *before* the download because the downloader reads
        the model and device out of the merged config. Left alone, a cancelled
        or failed download leaves config.toml naming a setup the service was
        never given, and these keys are excluded from the live-settings
        reload, so nothing corrects it.

        The second write also repairs the baseline. save_config() resets
        _config_at_open to whatever it just wrote, so without this a retry
        finds no change and skips the restart that would load the new setup.

        Only these keys are undone; the user's other edits saved fine and
        stay. The dropdowns go back too, quietly: no model-picked dialogs, and
        no device setup (Vulkan's starts a download).
        """
        undo = {k: v for k, v in previous.items()
                if v is not None and self.config.get(k) != v}
        if not undo:
            return
        for key, value in undo.items():
            self.set_choice(key, value)
        self.win.save_config()
