"""The update check says what went wrong, not always "check your internet".

Ubuntu VM test, 2026-10-13: GitHub answered 404 (the repo is renamed on
release day) and Check for Updates told an online user to check their
internet connection.
"""
import urllib.error


from tuxtalk import update_checker as uc


def _fail_with(monkeypatch, exc):
    def urlopen(*a, **k):
        raise exc
    monkeypatch.setattr(uc.urllib.request, "urlopen", urlopen)
    if isinstance(exc, urllib.error.HTTPError):
        monkeypatch.setattr(exc, "fp", None, raising=False)


def test_no_network_says_check_your_connection(monkeypatch):
    _fail_with(monkeypatch, urllib.error.URLError("Name or service not known"))
    assert "internet connection" in uc.check_for_updates()["error"]


def test_a_github_error_is_not_blamed_on_the_connection(monkeypatch):
    _fail_with(monkeypatch, urllib.error.HTTPError(uc.GITHUB_API_URL, 404, "Not Found", {}, None))
    error = uc.check_for_updates()["error"]
    assert "internet connection" not in error and "404" in error


def test_a_rate_limit_says_try_later(monkeypatch):
    _fail_with(monkeypatch, urllib.error.HTTPError(uc.GITHUB_API_URL, 403, "rate limit exceeded", {}, None))
    error = uc.check_for_updates()["error"]
    assert "internet connection" not in error and "later" in error
