"""Voice editing in Preferences and Help: the switch on the Advanced tab,
TuxTalk Labs, and what Help says about it."""

from tuxtalk import ai_download

# --- the Preferences switch ----------------------------------------------------------------

def _prefs_stub(saved):
    import types
    from tuxtalk.prefs_advanced import AdvancedTab
    stub = types.SimpleNamespace(update_config=lambda k, v: saved.append((k, v)), window=None)
    stub._untick_ai_feature = lambda check, key, handler: AdvancedTab._untick_ai_feature(
        stub, check, key, handler)
    return stub, AdvancedTab


class Box:
    def __init__(self, active):
        self.active = active

    def get_active(self):
        return self.active

    def set_active(self, v):
        self.active = v

    def handler_block_by_func(self, f):
        pass

    def handler_unblock_by_func(self, f):
        pass


def test_switching_on_with_the_ai_present_just_saves_it(monkeypatch):
    monkeypatch.setattr(ai_download, "is_installed", lambda: True)
    saved = []
    stub, P = _prefs_stub(saved)
    P._toggle_ai_feature(stub, Box(True), "voice_editing", None, "?")
    assert saved == [("voice_editing", True)]


def test_switching_off_saves_off(monkeypatch):
    saved = []
    stub, P = _prefs_stub(saved)
    P._toggle_ai_feature(stub, Box(False), "voice_editing", None, "?")
    assert saved == [("voice_editing", False)]


def test_declining_the_download_unticks_only_this_box(monkeypatch):
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    monkeypatch.setattr(ai_download, "is_installed", lambda: False)
    monkeypatch.setattr(Gtk.Dialog, "run", lambda self: Gtk.ResponseType.CANCEL)
    saved = []
    stub, P = _prefs_stub(saved)
    box = Box(True)
    P._toggle_ai_feature(stub, box, "voice_editing", None, "Download the AI for voice editing?")
    assert box.active is False and saved == [("voice_editing", False)]


def test_downloading_the_ai_for_voice_editing_leaves_self_corrections_off(prefs_window,
                                                                          monkeypatch):
    """Self-corrections was on before the AI files went missing, so its box
    shows unticked. Turning on voice editing downloads them again: the box
    that said off must still mean off."""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from tuxtalk import download_progress_dialog
    installed = []
    monkeypatch.setattr(ai_download, "is_installed", lambda: bool(installed))
    monkeypatch.setattr(Gtk.Dialog, "run", lambda self: Gtk.ResponseType.OK)

    class Download:
        def __init__(self, **kwargs):
            pass

        def add_task(self, task):
            pass

        def run(self):
            installed.append(True)
            return {"AI engine": {"success": True}, "AI model": {"success": True}}
    monkeypatch.setattr(download_progress_dialog, "UnifiedDownloadDialog", Download)
    from tuxtalk import config
    saved = config.load_config()
    saved.ai_corrections = True
    config.save_config(saved)
    window = prefs_window(ai_corrections=True)
    advanced = window.advanced_tab
    assert not advanced.ai_corrections_check.get_active()

    advanced.voice_editing_check.set_active(True)
    window.on_cancel(None)          # the download stays

    assert window.config["voice_editing"] is True
    assert config.load_config().ai_corrections is False


def test_an_off_that_cant_be_saved_at_once_is_left_for_apply(prefs_window, monkeypatch):
    from tuxtalk import config
    window = prefs_window(ai_corrections=True)

    def read_only(cfg):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(config, "save_config", read_only)
    window.save_now({"ai_corrections": False})
    assert "ai_corrections" in window._changed_since_open()      # Apply saves it


def test_the_ai_downloads_in_the_flatpak_without_voice_editing(prefs_window, monkeypatch):
    """The Flatpak has no Labs, so no voice editing box: the download for
    self-corrections finished, then failed on it."""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from tuxtalk import download_progress_dialog
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    installed = []
    monkeypatch.setattr(ai_download, "is_installed", lambda: bool(installed))
    monkeypatch.setattr(Gtk.Dialog, "run", lambda self: Gtk.ResponseType.OK)

    class Download:
        def __init__(self, **kwargs):
            pass

        def add_task(self, task):
            pass

        def run(self):
            installed.append(True)
            return {"AI engine": {"success": True}, "AI model": {"success": True}}
    monkeypatch.setattr(download_progress_dialog, "UnifiedDownloadDialog", Download)
    window = prefs_window()
    advanced = window.advanced_tab
    box = advanced.ai_corrections_check
    box.handler_block_by_func(advanced._on_ai_corrections_toggled)
    box.set_active(True)
    box.handler_unblock_by_func(advanced._on_ai_corrections_toggled)

    advanced._on_ai_corrections_toggled(box)    # directly: GTK swallows a handler's error

    assert window.config["ai_corrections"] is True


def _labs(window):
    """What Preferences shows under its TuxTalk Labs heading."""
    from gi.repository import Gtk
    from gtk_text import below, find
    return below(find(window.window, Gtk.Label, "🧪 TuxTalk Labs"))


def test_the_advanced_tab_has_the_switch(prefs_window, monkeypatch):
    from gi.repository import Gtk
    monkeypatch.setattr(ai_download, "is_installed", lambda: True)
    window = prefs_window()
    switch = next(w for w in _labs(window) if isinstance(w, Gtk.CheckButton)
                  and w.get_label().startswith("Voice editing"))
    switch.set_active(True)
    assert window.config["voice_editing"] is True


# --- TuxTalk Labs: experimental features, with Help and feedback ---------------------------

def test_help_explains_labs_and_voice_editing_on_the_writing_tab():
    """Help is sorted by situation since the 0.16 simplicity review: voice
    editing is explained where people write, marked as Labs."""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from gtk_text import find, shown_text
    from tuxtalk import help_dialog
    dialog = help_dialog.show_help_dialog(tab="writing")
    try:
        notebook = find(dialog, Gtk.Notebook)
        page = notebook.get_nth_page(notebook.get_current_page())
        assert notebook.get_tab_label_text(page) == "Writing"
        text = " ".join(shown_text(page))
        for must in ("experimental", "Shift", "Ctrl+Z", "make this more formal", "terminal"):
            assert must in text, must
    finally:
        dialog.destroy()


def test_preferences_has_a_labs_section_with_both_links(prefs_window, monkeypatch):
    from gi.repository import Gtk
    from gtk_text import shown_text
    from tuxtalk import help_dialog
    opened = []
    monkeypatch.setattr(help_dialog, "show_help_dialog", lambda tab=None: opened.append(tab))
    window = prefs_window()
    window.select_tab("advanced")
    labs = _labs(window)
    assert any("Experimental" in text for w in labs for text in shown_text(w))
    assert any(isinstance(w, Gtk.CheckButton) and w.get_label().startswith("Voice editing")
               for w in labs)
    next(w for w in labs if isinstance(w, Gtk.Button) and w.get_label() == "How these work").clicked()
    assert opened == ["writing"]
    feedback = next(w for w in labs if isinstance(w, Gtk.LinkButton))
    assert feedback.get_label() == "Send feedback" and feedback.get_uri() == help_dialog.FEEDBACK_URL


def test_the_flatpak_has_no_labs(prefs_window, monkeypatch):
    """Its portal input can't see Shift or send Ctrl+C, so none of them work there yet."""
    from gtk_text import tree_text
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")
    shown = tree_text(prefs_window().window)
    assert "🧪 TuxTalk Labs" not in shown
    assert not [text for text in shown if text.startswith("Voice editing")]


def test_voice_editing_is_in_labs_not_with_the_cleanup_options(prefs_window):
    window = prefs_window()
    assert window.advanced_tab.voice_editing_check in _labs(window)


def test_the_labs_tips_name_your_own_key_and_the_writing_page(prefs_window):
    """With F6 as the hold key, "hold Shift+F8" sent people to the wrong key,
    and Help has no "TuxTalk Labs" page: the list is under Writing."""
    from gi.repository import Gtk
    window = prefs_window(hotkey="F6")
    tips = [w.get_tooltip_text() or "" for w in _labs(window)
            if isinstance(w, Gtk.CheckButton)]
    editing = next(t for t in tips if "Select some text" in t)
    moving = next(t for t in tips if "select the last sentence" in t)
    assert "Shift+F6" in editing and "Shift+F8" not in editing
    assert "Shift+F6" in moving and "on the Writing page" in moving
