"""RecordingIndicator dispatches to the selected style; orb path untouched."""
import numpy as np
import pytest


@pytest.fixture
def indicator():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    if not Gtk.init_check(None)[0]:
        pytest.skip("no display")
    from tuxtalk.recording_indicator import RecordingIndicator
    ind = RecordingIndicator(style="bars", size="medium")
    yield ind
    ind.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration()


def _apply(indicator, *args, **kwargs):
    """apply_settings, then GTK's loop runs the change it queued."""
    from gi.repository import Gtk
    indicator.apply_settings(*args, **kwargs)
    while Gtk.events_pending():
        Gtk.main_iteration()


def test_style_is_stored(indicator):
    assert indicator.style == "bars"


def test_new_attributes_default(indicator):
    # "Cyan" is not a mode; the classic look is custom + classic cyan.
    assert indicator.color_mode == "custom"
    assert indicator.sensitivity == 1.0


def test_setters_store_data(indicator):
    indicator.set_waveform(np.zeros(100, dtype=np.float32))
    indicator.set_spectrum(np.linspace(0, 1, 20).astype(np.float32))
    assert indicator.waveform is not None
    assert indicator.spectrum is not None


def test_apply_settings_changes_style_live(indicator):
    _apply(indicator, "center", "medium", 0, 0, style="radial")
    assert indicator.style == "radial"


def test_apply_settings_changes_sensitivity(indicator):
    _apply(indicator, "center", "medium", 0, 0, sensitivity=1.5)
    assert indicator.sensitivity == 1.5


def test_apply_settings_position_only_leaves_style_untouched(indicator):
    """Omitted style kwarg must not reset the style — existing callers pass 4 args."""
    _apply(indicator, "top-left", "large", 10, 10)
    assert indicator.style == "bars"


def test_orb_style_keeps_its_draw_path(indicator):
    _apply(indicator, "center", "medium", 0, 0, style="orb")
    assert indicator.style == "orb"
    assert hasattr(indicator, "draw_orb")
