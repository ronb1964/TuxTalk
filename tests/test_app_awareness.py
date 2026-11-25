"""Plain text in terminals and code editors (Ron, 2026-10-14): no capital at
the start in a terminal, and no period at the end in either, so "ls dash la"
types `ls -la`, not `Ls -la.`. Chat apps keep full punctuation."""
import pytest

from tuxtalk import config, window_classes
from tuxtalk import dictation_text
from tuxtalk import live_settings as live
from tuxtalk.normalize import LINE_BREAK, plain_for_app


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
    (f"Echo{LINE_BREAK}PATH is set", "terminal", f"echo{LINE_BREAK}PATH is set"),
    (f"Echo hi{LINE_BREAK}I think", "terminal", f"echo hi{LINE_BREAK}I think"),  # "I" stays
    (f"Echo{LINE_BREAK}I, too", "terminal", f"echo{LINE_BREAK}I, too"),
    ("A test", "terminal", "a test"),
    ("I think so", "terminal", "I think so"),
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
                             app_kind=kind, settings=live)


def test_a_terminal_gets_no_capital_and_no_period(monkeypatch):
    assert _prepare(monkeypatch, "git status", "terminal") == "git status "


def test_in_a_terminal_the_word_after_a_spoken_new_line_is_small_too(monkeypatch):
    """A terminal gets the line break as a space, so the next word carries
    on the command: "git status git log", not "git status Git log"."""
    assert _prepare(monkeypatch, "Git status new line git log.", "terminal") == (
        "git status" + LINE_BREAK + "git log ")


def test_in_a_code_editor_a_new_line_keeps_its_capital(monkeypatch):
    assert _prepare(monkeypatch, "total price new line next item", "code") == (
        "Total price" + LINE_BREAK + "Next item ")


def test_a_code_editor_gets_no_period(monkeypatch):
    assert _prepare(monkeypatch, "total price", "code") == "Total price "


def test_anywhere_else_is_unchanged(monkeypatch):
    assert _prepare(monkeypatch, "git status", "") == "Git status. "


def test_on_by_default():
    assert config.Settings().adapt_to_app is True


def test_the_setting_decides(monkeypatch):
    monkeypatch.setattr(live, "ai_engine", None)
    live.apply(config.Settings(adapt_to_app=False))
    assert live.adapt_to_app is False
    live.apply(config.Settings())
    assert live.adapt_to_app is True


@pytest.mark.parametrize("kind", ["terminal", "code"])
@pytest.mark.parametrize("said, typed", [
    ("git commit -m open quote fix the bug close quote", 'git commit -m "fix the bug"'),
    ("echo quote hello world quote", 'echo "hello world"'),
])
def test_quotes_are_straight_in_terminals_and_code_editors(monkeypatch, kind, said, typed):
    """A shell or a compiler doesn't read “curly” quotes as quotes, so a
    spoken quote there is straight whatever the smart-quotes setting."""
    assert _prepare(monkeypatch, said, kind).strip().lower() == typed.lower()
