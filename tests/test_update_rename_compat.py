"""0.14.x installs must update across the rename (TalkType -> TuxTalk, 0.15.0).

These run against the updater as it stands BEFORE the rename. Passing proves
every installed TalkType finds, and checksum-verifies, a TuxTalk release.
"""
import json

from conftest import UrlResponse
from tuxtalk import update_install
from tuxtalk import update_checker as uc

RELEASE = {
    "tag_name": "v0.15.0", "name": "TuxTalk 0.15.0", "body": "", "published_at": "",
    "html_url": "https://github.com/ronb1964/TuxTalk/releases/tag/v0.15.0",
    "assets": [
        {"name": "TuxTalk-v0.15.0-x86_64.AppImage", "browser_download_url": "A"},
        {"name": "TuxTalk-v0.15.0-x86_64.AppImage.zsync", "browser_download_url": "Z"},
        {"name": "TalkType-v0.15.0-x86_64.AppImage.zsync", "browser_download_url": "Z2"},
        {"name": "tuxtalk_0.15.0_amd64.deb", "browser_download_url": "D"},
        {"name": "tuxtalk-0.15.0-1.x86_64.rpm", "browser_download_url": "R"},
        {"name": "tuxtalk-gnome-extension.zip", "browser_download_url": "E"},
        {"name": "SHA256SUMS.txt", "browser_download_url": "S"},
    ],
}


def test_old_client_picks_every_tuxtalk_asset(monkeypatch):
    monkeypatch.setattr(uc.urllib.request, "urlopen",
                        lambda *a, **k: UrlResponse(json.dumps(RELEASE).encode()))
    r = uc.fetch_latest_release()
    assert r["appimage_url"] == "A" and r["appimage_name"] == "TuxTalk-v0.15.0-x86_64.AppImage"
    urls = (r["deb_url"], r["rpm_url"], r["checksums_url"])
    assert urls == ("D", "R", "S")


def test_old_client_finds_the_tuxtalk_checksum(monkeypatch):
    sums = ("ab" * 32 + "  TuxTalk-v0.15.0-x86_64.AppImage\n"
            + "cd" * 32 + "  tuxtalk_0.15.0_amd64.deb\n")
    monkeypatch.setattr(uc.urllib.request, "urlopen", lambda *a, **k: UrlResponse(sums.encode()))
    assert update_install.fetch_expected_sha256(
        "https://example.invalid/SHA256SUMS.txt", "TuxTalk-v0.15.0-x86_64.AppImage") == "ab" * 32


def test_two_checks_at_once_each_say_what_went_wrong_for_them(monkeypatch):
    """The tray's daily check and one from Preferences overlapping: the reason
    was one shared value, so one check could say the other's error."""
    import threading
    import urllib.error
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
