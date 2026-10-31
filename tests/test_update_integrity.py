"""The in-app updater must not install an AppImage it could not verify.

The updater downloads the release's SHA256SUMS.txt and checks the AppImage
against it. fetch_expected_sha256() returns None for *any* problem — the asset
404s, the body is malformed, or it simply doesn't list the AppImage — so
download_update() must not treat None as "no checksum published, carry on" and
install the file after only a length check.

A release without SHA256SUMS.txt (checksums_url=None) isn't installed either:
every release since 0.5.17 has one, so its absence is a publishing mistake.
"""

import pytest

from tuxtalk import update_install


@pytest.fixture
def no_real_downloads(monkeypatch, tmp_path):
    """Record what download_file was asked to do, without touching the network."""
    calls = []

    def fake_download_file(url, dest, **kwargs):
        calls.append({"url": url, "dest": dest, **kwargs})
        with open(dest, "w") as f:
            f.write("pretend AppImage")
        return True

    monkeypatch.setattr("tuxtalk.download_utils.download_file", fake_download_file)
    monkeypatch.setattr(update_install, "UPDATE_DIR", str(tmp_path))
    return calls


def test_a_published_checksums_file_that_omits_the_appimage_aborts(no_real_downloads,
                                                                   monkeypatch):
    """The release advertises SHA256SUMS.txt but has no line for our file."""
    monkeypatch.setattr(update_install, "fetch_expected_sha256",
                        lambda url, filename: None)

    result = update_install.download_update(
        "https://example.invalid/TalkType.AppImage",
        "TalkType.AppImage",
        checksums_url="https://example.invalid/SHA256SUMS.txt",
    )

    assert result is None, "installed an AppImage whose checksum was never verified"
    assert not no_real_downloads, "downloaded before establishing the expected hash"


def test_an_unreachable_checksums_file_aborts(no_real_downloads, monkeypatch):
    """A 404 or network error on the checksums asset must not fail open."""
    def unreachable(url, filename):
        raise OSError("connection reset")

    monkeypatch.setattr(update_install, "fetch_expected_sha256", unreachable)

    result = update_install.download_update(
        "https://example.invalid/TalkType.AppImage",
        "TalkType.AppImage",
        checksums_url="https://example.invalid/SHA256SUMS.txt",
    )

    assert result is None


def test_a_published_checksum_is_passed_through_to_the_downloader(no_real_downloads,
                                                                  monkeypatch):
    monkeypatch.setattr(update_install, "fetch_expected_sha256",
                        lambda url, filename: "a" * 64)

    result = update_install.download_update(
        "https://example.invalid/TalkType.AppImage",
        "TalkType.AppImage",
        checksums_url="https://example.invalid/SHA256SUMS.txt",
    )

    assert result is not None
    assert no_real_downloads[0]["expected_sha256"] == "a" * 64


def test_a_release_without_a_checksums_file_isnt_installed(no_real_downloads):
    """Every release since 0.5.17 publishes SHA256SUMS.txt and only a newer
    one is offered, so one without it was published by mistake: its .deb or
    .rpm went in as root unchecked."""
    said = []
    result = update_install.download_update(
        "https://example.invalid/tuxtalk_9.9.9_amd64.deb",
        "tuxtalk_9.9.9_amd64.deb",
        lambda message, percent: said.append((message, percent)),
        checksums_url=None,
    )

    assert result is None
    assert not no_real_downloads
    assert said[-1] == ("This release has no checksums to check the update against", 0)


def test_a_failed_download_says_why(monkeypatch, tmp_path):
    """It said only "Download failed or file was corrupted", whatever the
    reason the log had."""
    from tuxtalk import download_utils

    def not_found(url, dest, **kwargs):
        raise download_utils.DownloadError("HTTP 404 Not Found")
    monkeypatch.setattr(download_utils, "fetch_file", not_found)
    monkeypatch.setattr(update_install, "UPDATE_DIR", str(tmp_path))
    monkeypatch.setattr(update_install, "fetch_expected_sha256", lambda url, filename: "a" * 64)
    said = []
    assert update_install.download_update(
        "https://example.invalid/TuxTalk.AppImage", "TuxTalk.AppImage",
        lambda message, percent: said.append((message, percent)),
        checksums_url="https://example.invalid/SHA256SUMS.txt") is None
    assert said[-1] == ("Download failed: HTTP 404 Not Found", 0)
