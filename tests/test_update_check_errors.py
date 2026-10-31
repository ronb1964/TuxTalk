"""The update check says what went wrong, not always "check your internet".

When GitHub answered 404 (the repository was renamed on release day), Check
for Updates told an online user to check their internet connection.
"""
import threading
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
    _fail_with(monkeypatch,
               urllib.error.HTTPError(uc.GITHUB_API_URL, 403, "rate limit exceeded", {}, None))
    error = uc.check_for_updates()["error"]
    assert "internet connection" not in error and "later" in error


def test_two_checks_at_once_each_say_what_went_wrong_for_them(monkeypatch):
    """The tray's daily check and one from Preferences overlapping: the reason
    was one shared value, so one check could say the other's error."""
    reasons = {"rate": urllib.error.HTTPError("u", 429, "slow down", {}, None),
               "offline": urllib.error.URLError("no route")}

    def urlopen(request, timeout=None):
        raise reasons[threading.current_thread().name]
    monkeypatch.setattr(uc.urllib.request, "urlopen", urlopen)
    both_failed, said = threading.Barrier(2), {}

    def check():
        assert uc.fetch_latest_release() is None
        both_failed.wait(5)                    # each has failed before either asks why
        said[threading.current_thread().name] = uc._fetch_failure_message()
    runs = [threading.Thread(target=check, name=name) for name in reasons]
    for run in runs:
        run.start()
    for run in runs:
        run.join(10)
    assert "limiting" in said["rate"] and "internet connection" in said["offline"]
