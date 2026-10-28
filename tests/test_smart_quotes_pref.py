"""The "Use smart quotes" preference must decide every quote in the output.

A lone "open quote" or "close quote" is turned into a curly quote inside
normalize_text, after the preference is applied to the raw text, so the
preference must cover those too or curly quotes reach the document with it
off.
"""
from tuxtalk import dictation_text
from tuxtalk import live_settings as live


def _prep(text, smart_quotes):
    return dictation_text.prepare_text(text, smart_quotes, True, False)


def test_lone_open_quote_is_straight_with_smart_quotes_off(monkeypatch):
    monkeypatch.setattr(live, "ai_engine", None)
    out = _prep("he said open quote hello", False)
    assert "“" not in out and '"hello' in out


def test_lone_close_quote_is_straight_with_smart_quotes_off(monkeypatch):
    monkeypatch.setattr(live, "ai_engine", None)
    out = _prep("hello close quote", False)
    assert "”" not in out and out.endswith('"')


def test_lone_quote_stays_curly_with_smart_quotes_on(monkeypatch):
    monkeypatch.setattr(live, "ai_engine", None)
    assert _prep("he said open quote hello", True) == "He said “hello."


def test_spoken_quote_pairs_are_straight_with_smart_quotes_off(monkeypatch):
    monkeypatch.setattr(live, "ai_engine", None)
    assert _prep("he said quote hello quote", False) == 'He said "hello."'
    assert _prep("he said open quote hello close quote", False) == 'He said "hello."'
