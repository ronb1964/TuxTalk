"""Voice commands through the real dictation pipeline, shared by the voice
command tests (windows, desktops, sound, music, screenshots, the lock, web
commands, the mouse grid): the setting on, and a whole dictation said.
Registered as a plugin in conftest.py."""
import pytest

from tuxtalk import notify, screen_message
from tuxtalk import live_settings as live


@pytest.fixture
def actions_on(chat, monkeypatch):
    """Voice commands on, with notices kept off the real desktop. Returns
    the labels floated over the window."""
    floated = []
    monkeypatch.setattr(live, "open_apps", True)
    monkeypatch.setattr(screen_message, "show_over_active_window",
                        lambda text, *a, **k: floated.append(text))
    monkeypatch.setattr(notify, "notify", lambda *a, **k: None)
    return floated


@pytest.fixture
def say(chat, let_go):
    """say(words): a whole dictation of *words*, through the real pipeline."""
    def said(words):
        chat[1].append(words)
        return let_go()
    return said
