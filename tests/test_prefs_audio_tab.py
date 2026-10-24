"""Preferences, Audio tab: the microphone dropdown and the feedback settings
save what is picked, in the settings the service reads."""
import gi
import pytest
import sounddevice

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from gtk_text import beside, find  # noqa: E402
from tuxtalk import mic_volume  # noqa: E402

AUDIO_TAB = 1
MICS = [{"name": "Sennheiser Profile", "max_input_channels": 1},
        {"name": "HDMI Output", "max_input_channels": 0}]


@pytest.fixture
def audio(prefs_window, monkeypatch):
    """Build Preferences with two sound devices, one of them a microphone;
    returns (window, the Audio tab's page)."""
    monkeypatch.setattr(sounddevice, "query_devices", lambda: MICS)
    monkeypatch.setattr(mic_volume, "get_volume", lambda source: None)

    def build(**settings):
        window = prefs_window(**settings)
        return window, window.notebook.get_nth_page(AUDIO_TAB)
    return build


def test_the_microphone_list_holds_only_inputs_and_saves_the_choice(audio):
    window, page = audio(mic="")
    mics = beside(find(page, Gtk.Label, "Microphone:"))
    assert [row[0] for row in mics.get_model()] == ["System Default", "Sennheiser Profile"]
    mics.set_active_id("Sennheiser Profile")
    assert window.config["mic"] == "Sennheiser Profile"


def test_a_microphone_not_plugged_in_shows_as_system_default(audio):
    _window, page = audio(mic="USB Mic Unplugged")
    assert beside(find(page, Gtk.Label, "Microphone:")).get_active_id() == ""


def test_other_sound_saves_both_settings_behind_it(audio):
    window, page = audio()
    label = find(page, Gtk.Label, "Other sound while you dictate 💡:")
    find(label.get_parent(), Gtk.ComboBoxText).set_active_id("duck")
    assert (window.config["pause_media"], window.config["duck_audio"]) == (False, True)


def test_beeps_and_notifications_save_when_unticked(audio):
    window, page = audio(beeps=True, notify=True)
    find(page, Gtk.CheckButton, "Play beeps for start/stop/ready").set_active(False)
    find(page, Gtk.CheckButton, "Show desktop notifications").set_active(False)
    assert (window.config["beeps"], window.config["notify"]) == (False, False)


def test_no_indicator_greys_its_customize_button(audio):
    window, page = audio(recording_indicator=True)
    customize = find(page, Gtk.Button, "Customize…")
    assert customize.get_sensitive()
    find(page, Gtk.CheckButton, "Show recording indicator").set_active(False)
    assert window.config["recording_indicator"] is False
    assert not customize.get_sensitive()
