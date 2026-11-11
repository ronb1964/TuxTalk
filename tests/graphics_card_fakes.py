"""No test lists the real graphics cards: Preferences would run nvidia-smi
or the graphics engine for its Graphics card row (prefs_graphics_card.py).
Registered as a plugin in conftest.py."""
import pytest


@pytest.fixture(autouse=True)
def _no_real_graphics_cards(monkeypatch):
    """No cards, and listed at once: a thread's answer would land in the GTK
    queue some tests run by hand. The row's own tests bring their cards."""
    from tuxtalk import prefs_graphics_card as row
    monkeypatch.setattr(row, "cards_for", lambda device: [])
    monkeypatch.setattr(row, "in_background", lambda work, done: done(work()))
