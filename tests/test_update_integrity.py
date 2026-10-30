"""The in-app updater must not install an AppImage it could not verify.

The updater downloads the release's SHA256SUMS.txt and checks the AppImage
against it. fetch_expected_sha256() returns None for *any* problem — the asset
404s, the body is malformed, or it simply doesn't list the AppImage — so
download_update() must not treat None as "no checksum published, carry on" and
install the file after only a length check.

Releases genuinely published before checksums existed pass checksums_url=None.
That is the only case where skipping the hash is legitimate.
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


def test_a_release_with_no_checksums_asset_still_updates(no_real_downloads):
    """Backward compatibility: releases published before SHA256SUMS existed."""
    result = update_install.download_update(
        "https://example.invalid/TalkType.AppImage",
        "TalkType.AppImage",
        checksums_url=None,
    )

    assert result is not None
    assert no_real_downloads[0]["expected_sha256"] is None
