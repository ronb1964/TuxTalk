"""Plain text in terminals and code editors (Ron, 2026-10-14): no capital at
the start in a terminal, and no period at the end in either, so "ls dash la"
types `ls -la`, not `Ls -la.`. Chat apps keep full punctuation."""
import pytest

from tuxtalk import config, window_classes
from tuxtalk import dictation_text
from tuxtalk import live_settings as live
from tuxtalk.normalize import plain_for_app


@pytest.mark.parametrize("text, kind, expected", [
    ("Ls -la. ", "terminal", "ls -la "),
    ("Git status.", "terminal", "git status"),
    ("PATH is set.", "terminal", "PATH is set"),       # all caps stays
    ("Done... ", "terminal", "done... "),              # an ellipsis is the user's
    ("Is it running? ", "terminal", "is it running? "),
    ("Total price. ", "code", "Total price "),         # code: capitals stay
    ("One. Two. ", "code", "One. Two "),               # only the last period goes
    ("Hello world. ", "", "Hello world. "),            # anywhere else: unchanged
    ("", "terminal", ""),
])
def test_plain_for_app(text, kind, expected):
    assert plain_for_app(text, kind) == expected


@pytest.mark.parametrize("window_class, kind", [
    ("org.kde.konsole", "terminal"),
    ("kitty", "terminal"),
    ("code", "code"), ("Code", "code"), ("VSCodium", "code"), ("codium", "code"),
    ("dev.zed.Zed", "code"), ("jetbrains-pycharm", "code"), ("sublime_text", "code"),
    ("org.kde.kwrite", ""), ("libreoffice-writer", ""), ("Slack", ""), ("discord", ""),
    (None, ""),
])
def test_app_kind(window_class, kind):
    assert window_classes.app_kind(window_class) == kind


def _prepare(monkeypatch, raw, kind):
    monkeypatch.setattr(live, "words", [])
    monkeypatch.setattr(live, "remove_fillers", False)
    monkeypatch.setattr(live, "ai_corrections", False)
    monkeypatch.setattr(live, "swiss_spelling", False)
    monkeypatch.setattr(live, "numbers_mode", None)
    return dictation_text.prepare_text(raw, smart_quotes=True, auto_period=True, auto_space=True,
                             app_kind=kind)


def test_a_terminal_gets_no_capital_and_no_period(monkeypatch):
    assert _prepare(monkeypatch, "git status", "terminal") == "git status "


def test_a_code_editor_gets_no_period(monkeypatch):
    assert _prepare(monkeypatch, "total price", "code") == "Total price "


def test_anywhere_else_is_unchanged(monkeypatch):
    assert _prepare(monkeypatch, "git status", "") == "Git status. "


def test_on_by_default_and_live():
    assert config.Settings().adapt_to_app is True
    assert "adapt_to_app" in config.LIVE_APPLIED_KEYS


def test_the_setting_decides(monkeypatch):
    monkeypatch.setattr(live, "ai_engine", None)
    live.apply(config.Settings(adapt_to_app=False))
    assert live.adapt_to_app is False
    live.apply(config.Settings())
    assert live.adapt_to_app is True
