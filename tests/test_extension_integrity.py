"""The GNOME extension is JavaScript that runs inside the user's shell, so a
tampered or truncated zip matters. Its checksum lookup must not fail open: any
problem, a release without SHA256SUMS.txt included, stops the install.
"""

import urllib.error

import pytest

from conftest import UrlResponse
from tuxtalk import extension_helper as E


def _http_error(code):
    return urllib.error.HTTPError("url", code, "err", {}, None)


def test_a_release_without_checksums_aborts(monkeypatch):
    """Every release since 0.5.17 has SHA256SUMS.txt, and the extension comes
    from the latest: a 404 is a publishing mistake, and its JavaScript went
    into GNOME Shell unchecked."""
    monkeypatch.setattr(E.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(404)))

    with pytest.raises(E.ChecksumUnavailableError, match="404"):
        E._fetch_extension_sha256()


def test_a_checksums_file_missing_our_entry_aborts(monkeypatch):
    """The release publishes checksums but not for the extension zip."""
    body = b"abc123  some-other-file.AppImage\n"
    monkeypatch.setattr(E.urllib.request, "urlopen",
                        lambda *a, **k: UrlResponse(body))

    with pytest.raises(E.ChecksumUnavailableError):
        E._fetch_extension_sha256()


def test_a_server_error_aborts(monkeypatch):
    """500 is not "no checksums published" — it is an unknown state."""
    monkeypatch.setattr(E.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(500)))

    with pytest.raises(E.ChecksumUnavailableError):
        E._fetch_extension_sha256()


def test_a_published_checksum_is_returned(monkeypatch):
    body = (b"a" * 64) + b"  " + E.EXTENSION_ZIP_NAME.encode() + b"\n"
    monkeypatch.setattr(E.urllib.request, "urlopen",
                        lambda *a, **k: UrlResponse(body))

    assert E._fetch_extension_sha256() == "a" * 64
