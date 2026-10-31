"""The updater finds every file of a TuxTalk release and the AppImage's
checksum, with the TalkType-named compatibility copies (the .zsync 0.14.x
installs look for) in the same release taking nothing's place.
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


def test_the_updater_picks_every_tuxtalk_file(monkeypatch):
    monkeypatch.setattr(uc.urllib.request, "urlopen",
                        lambda *a, **k: UrlResponse(json.dumps(RELEASE).encode()))
    r = uc.fetch_latest_release()
    assert r["appimage_url"] == "A" and r["appimage_name"] == "TuxTalk-v0.15.0-x86_64.AppImage"
    urls = (r["deb_url"], r["rpm_url"], r["checksums_url"])
    assert urls == ("D", "R", "S")


def test_the_updater_finds_the_appimages_checksum(monkeypatch):
    sums = ("ab" * 32 + "  TuxTalk-v0.15.0-x86_64.AppImage\n"
            + "cd" * 32 + "  tuxtalk_0.15.0_amd64.deb\n")
    monkeypatch.setattr(uc.urllib.request, "urlopen", lambda *a, **k: UrlResponse(sums.encode()))
    assert update_install.fetch_expected_sha256(
        "https://example.invalid/SHA256SUMS.txt", "TuxTalk-v0.15.0-x86_64.AppImage") == "ab" * 32
