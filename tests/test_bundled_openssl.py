"""The AppImage's bundled OpenSSL 3 is loaded only when the system has none.

The AppImage's Python takes libssl.so.3 from the system. On Fedora 45, which
ships only OpenSSL 4, the ssl module didn't load and every download failed
with "unknown url type: https".
"""

import pytest

import tuxtalk


@pytest.fixture(autouse=True)
def no_certificate_file(monkeypatch):
    # setenv first records each variable's real state, so what the code under
    # test sets is undone after the test too.
    for name in ("SSL_CERT_FILE", tuxtalk.SSL_CERT_FILE_SET_BY_TUXTALK):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


def _bundle(tmp_path):
    lib_dir = tmp_path / "openssl3"
    lib_dir.mkdir()
    for name in ("libcrypto.so.3", "libssl.so.3"):
        (lib_dir / name).write_bytes(b"")
    return lib_dir


def _system_list(tmp_path):
    ca = tmp_path / "tls-ca-bundle.pem"
    ca.write_text("certificates")
    return str(ca)


def _use(lib_dir, ca_bundles, ssl_loads):
    loaded = []
    used = tuxtalk._use_bundled_openssl(
        str(lib_dir), ca_bundles, ssl_loads=lambda: ssl_loads,
        load=loaded.append)
    return used, loaded


def test_a_system_without_openssl_3_gets_the_bundled_copy(tmp_path):
    lib_dir = _bundle(tmp_path)
    used, loaded = _use(lib_dir, (_system_list(tmp_path),), ssl_loads=False)

    assert used
    # libssl needs libcrypto, so libcrypto is loaded first.
    assert loaded == [str(lib_dir / "libcrypto.so.3"), str(lib_dir / "libssl.so.3")]


def test_the_bundled_copy_is_pointed_at_the_systems_certificate_list(tmp_path, monkeypatch):
    ca = _system_list(tmp_path)
    _use(_bundle(tmp_path), (str(tmp_path / "missing.crt"), ca), ssl_loads=False)

    assert tuxtalk.os.environ["SSL_CERT_FILE"] == ca
    assert tuxtalk.os.environ[tuxtalk.SSL_CERT_FILE_SET_BY_TUXTALK] == "1"


def test_a_certificate_list_of_the_users_own_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/home/u/company.pem")
    _use(_bundle(tmp_path), (_system_list(tmp_path),), ssl_loads=False)

    assert tuxtalk.os.environ["SSL_CERT_FILE"] == "/home/u/company.pem"
    assert tuxtalk.SSL_CERT_FILE_SET_BY_TUXTALK not in tuxtalk.os.environ


def test_the_systems_openssl_3_still_wins(tmp_path):
    used, loaded = _use(_bundle(tmp_path), (_system_list(tmp_path),), ssl_loads=True)

    assert not used and loaded == []
    assert "SSL_CERT_FILE" not in tuxtalk.os.environ


def test_no_bundle_means_no_change(tmp_path):
    used, loaded = _use(tmp_path / "missing", (_system_list(tmp_path),), ssl_loads=False)

    assert not used and loaded == []


def test_a_working_system_openssl_is_recognised():
    # Against this computer's real libssl: were the check always False, every
    # system would get the bundled copy.
    assert tuxtalk._system_ssl_loads()
