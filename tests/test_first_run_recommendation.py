"""First run recommends one complete setup instead of asking about CUDA.

One card says what will be set up, model and graphics card included (AMD and
Intel too), and Other options change it with every choice explained.
"""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from tuxtalk import recommend as r  # noqa: E402
from tuxtalk import welcome_dialog as wd  # noqa: E402


@pytest.fixture
def dialog(welcome_window):
    return welcome_window


def test_card_shows_the_recommendation(dialog):
    s = dialog._current_setup()
    assert (s.model, s.device) == ("parakeet-v3", "vulkan")
    assert "Parakeet on your NVIDIA GeForce RTX 4070 SUPER" in dialog.rec_title.get_text()


def test_changing_the_language_updates_the_card(dialog):
    dialog.lang_combo.set_active_id("ja")
    s = dialog._current_setup()
    assert s.model == "large-v3" and "Whisper Large-v3" in dialog.rec_title.get_text()
    assert not dialog.model_radios["parakeet-v3"].get_sensitive()


def test_unticking_the_graphics_card_uses_the_processor(dialog):
    dialog.gpu_check.set_active(False)
    assert dialog._current_setup().device == "cpu"
    assert "processor" in dialog.rec_title.get_text()


def test_run_result_carries_the_setup(dialog, monkeypatch):
    monkeypatch.setattr(dialog.dialog, "run", lambda: Gtk.ResponseType.OK)
    monkeypatch.setattr(dialog, "_fade_in_dialog", lambda o: None)
    result = dialog.run()
    assert (result["model"], result["device"], result["dictation_language"]) == \
        ("parakeet-v3", "vulkan", "en")
    assert "download_cuda" not in result and "use_vulkan" not in result


def test_first_run_never_mentions_cuda(dialog):
    from gtk_text import shown_text
    dialog.options_fold.button.clicked()          # every choice in view
    assert not [text for text in shown_text(dialog.dialog) if "CUDA" in text]


def test_options_button_says_what_can_be_changed(dialog):
    """A plain "Other options" expander or a small "change" link on the card
    is easy to miss, so one button names the choices."""
    assert dialog.options_fold.button.get_label() == "Change language, model or graphics card"
    assert "<a href" not in dialog.rec_explanation.get_label()
    assert not dialog.options_fold.is_open()
    dialog.options_fold.button.clicked()
    assert dialog.options_fold.is_open()
    assert dialog.options_fold.button.get_label() == "Hide language, model and graphics card"


def _find_button(widget, label):
    if isinstance(widget, Gtk.Button) and widget.get_label() == label:
        return widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            found = _find_button(child, label)
            if found:
                return found
    return None


def test_set_it_up_is_pinned_outside_the_scroll(dialog):
    """Inside the scrolling area, KDE's title bar would leave "Set it up" cut
    off at the window's bottom edge until the user scrolled. Pinned below it,
    the button is always on screen whole."""
    button = _find_button(dialog.dialog, "Set it up")
    assert button is not None
    assert button.get_ancestor(Gtk.ScrolledWindow) is None


@pytest.fixture
def flatpak(monkeypatch):
    monkeypatch.setenv("FLATPAK_ID", "io.github.ronb1964.TuxTalk")


def test_flatpak_footer_no_longer_promises_a_model_choice(flatpak, welcome_window):
    """The model is chosen on this screen; there is no later picker."""
    from gtk_text import shown_text
    shown = "\n".join(shown_text(welcome_window.dialog))
    assert "choose your speech model" not in shown
    assert "downloads your speech model, then starts" in shown


def test_no_pulse_timer_without_anything_to_pulse(dialog, monkeypatch):
    """With no extension checkbox and no Flatpak warning there is nothing to
    pulse. A timer that stops itself at once and is then removed again when
    the window closes makes GLib warn "Source ID … was not found"."""
    started, removed = [], []
    monkeypatch.setattr(wd.GLib, "timeout_add", lambda *a: started.append(a) or 7)
    monkeypatch.setattr(wd.GLib, "source_remove", lambda sid: removed.append(sid))
    monkeypatch.setattr(dialog.dialog, "run", lambda: Gtk.ResponseType.OK)
    monkeypatch.setattr(dialog, "_fade_in_dialog", lambda o: None)
    assert dialog._pulse_targets() == []
    dialog.run()
    assert not any(a[1] == dialog._pulse_checkboxes for a in started)
    assert removed == []


def test_the_card_shows_on_a_computer_with_nothing_to_fix(monkeypatch):
    """KDE, AMD or Intel graphics, typing and audio already working: the card
    must not sit inside a section shown only for GNOME, NVIDIA or something
    to fix, or the user is never asked their language or offered their
    graphics card."""
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    monkeypatch.setattr(r, "detect_hardware",
                        lambda cfg=None: r.Hardware("AMD Radeon 780M", False))
    monkeypatch.setattr(r, "system_language", lambda env=None: "de")
    monkeypatch.setattr(wd, "detect_uinput_access", lambda: (True, ""))
    monkeypatch.setattr(wd, "detect_portaudio_status", lambda: {"needs_install": False})
    d = wd.WelcomeDialog(force_gnome=False)
    try:
        assert hasattr(d, "lang_combo") and d._language() == "de"
        assert d._current_setup().model
    finally:
        d.destroy()
