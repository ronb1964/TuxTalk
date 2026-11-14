"""TuxTalk - voice dictation for Linux"""

# First of all: move a TalkType install's folders to TuxTalk's names, before
# any module (the logger above all) creates the new ones. What was moved, or
# couldn't be, is logged by the tray. See rename_migration.py.
from . import rename_migration as _rename_migration
MIGRATION_LOG = _rename_migration.run_at_startup()

import ctypes.util  # noqa: E402
import os  # noqa: E402

__version__ = "0.23.0"

# Where the AppImage (and the .deb/.rpm made from it) keeps its own PortAudio:
# <root>/usr/src/tuxtalk/ -> <root>/usr/lib/portaudio/. In a dev checkout or
# the Flatpak this folder doesn't exist and nothing changes.
_BUNDLED_PORTAUDIO_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "lib", "portaudio")


def _use_bundled_portaudio(lib_dir=_BUNDLED_PORTAUDIO_DIR):
    """Let sounddevice fall back to the bundled PortAudio.

    sounddevice finds PortAudio with ctypes.util.find_library("portaudio"),
    which only consults the system's linker cache, so a copy inside the
    AppImage is invisible to it and a system without libportaudio would have
    no microphone at all.

    The system's PortAudio still wins when there is one. Runs on package
    import, which is always before sounddevice is imported.
    """
    bundled = os.path.join(lib_dir, "libportaudio.so.2")
    if not os.path.isfile(bundled):
        return
    system_find_library = ctypes.util.find_library

    def find_library(name):
        found = system_find_library(name)
        if found is None and name == "portaudio":
            return os.path.normpath(bundled)
        return found

    ctypes.util.find_library = find_library


_use_bundled_portaudio()

# The AppImage's own OpenSSL 3, beside its PortAudio: <root>/usr/lib/openssl3/.
_BUNDLED_OPENSSL_DIR = os.path.join(os.path.dirname(_BUNDLED_PORTAUDIO_DIR), "openssl3")
# Where distributions keep the certificates they trust, most common first.
_SYSTEM_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",                 # Debian, Arch, Fedora 45
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",                             # openSUSE
    "/etc/ssl/cert.pem",
)
# Set beside SSL_CERT_FILE when TuxTalk set it, so launch_env can tell it
# apart from a value of the user's own and keep it out of the apps it starts.
SSL_CERT_FILE_SET_BY_TUXTALK = "TUXTALK_SET_SSL_CERT_FILE"


def _system_ssl_loads():
    """Whether Python's ssl module loads with the system's libssl.so.3."""
    try:
        import _ssl  # noqa: F401
    except ImportError:
        return False
    return True


def _use_bundled_openssl(lib_dir=_BUNDLED_OPENSSL_DIR, ca_bundles=_SYSTEM_CA_BUNDLES,
                         ssl_loads=_system_ssl_loads, load=ctypes.CDLL):
    """Load the bundled OpenSSL 3 when the system has none; True if it did.

    The AppImage's Python takes libssl.so.3 from the system. Fedora 45 ships
    only OpenSSL 4, so there the ssl module didn't load and every download
    failed with "unknown url type: https". Loaded first, and globally, the
    bundled libraries are found by name when the ssl module is imported.

    The bundled copy is Ubuntu's, which looks for trusted certificates where
    Ubuntu keeps them, so SSL_CERT_FILE points it at the system's own list.
    Where the system's OpenSSL 3 works, nothing changes.
    """
    crypto = os.path.join(lib_dir, "libcrypto.so.3")
    if not os.path.isfile(crypto) or ssl_loads():
        return False
    load(crypto, mode=ctypes.RTLD_GLOBAL)
    load(os.path.join(lib_dir, "libssl.so.3"), mode=ctypes.RTLD_GLOBAL)
    if "SSL_CERT_FILE" not in os.environ:
        found = next((path for path in ca_bundles if os.path.isfile(path)), None)
        if found:
            os.environ["SSL_CERT_FILE"] = found
            os.environ[SSL_CERT_FILE_SET_BY_TUXTALK] = "1"
    return True


# Read by the tray, which logs it once its logger exists.
USING_BUNDLED_OPENSSL = _use_bundled_openssl()
