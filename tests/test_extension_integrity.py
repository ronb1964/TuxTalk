"""The GNOME extension is JavaScript that runs inside the user's shell, so a
tampered or truncated zip matters. Its checksum lookup must not fail open, with
any problem returning None and the install going ahead.

Two cases are told apart, because they are not the same risk:

  * the release publishes no SHA256SUMS.txt at all (404) — true of every
    release up to and including v0.5.16, so failing here would break the
    extension install for every existing user. Proceed, but say so.
  * the checksums file exists but the extension is not listed in it, or it
    could not be read for some other reason — that is a real integrity
    failure and must abort.
"""

import urllib.error

import pytest

from conftest import UrlResponse
from tuxtalk import extension_helper as E


def _http_error(code):
    return urllib.error.HTTPError("url", code, "err", {}, None)


def test_no_checksums_published_is_backward_compatible(monkeypatch):
    """A 404 means this release predates checksums — keep working."""
    monkeypatch.setattr(E.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(404)))

    assert E._fetch_extension_sha256() is None


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
