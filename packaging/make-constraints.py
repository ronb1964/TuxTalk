#!/usr/bin/env python3
"""Write packaging/source-install-constraints.txt from poetry.lock.

A source install (README) runs `pip install -c <this file> <release archive>`,
so it gets the versions the tests ran on instead of whatever PyPI has newest.
Only the lock's `main` group goes in: pytest, ruff and the other dev tools
are never installed there. Run it again after poetry.lock changes;
tests/test_source_install_constraints.py fails until you do:

    .venv/bin/python packaging/make-constraints.py

(the venv's Python: it imports toml, which the system's may not have).
"""
import pathlib

import toml

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCK = ROOT / "poetry.lock"
OUTPUT = pathlib.Path(__file__).resolve().parent / "source-install-constraints.txt"

# Packages the constraints file leaves out, so pip accepts what the system has:
# - evdev: PyPI has no wheel for it, so pip would have to compile it; the README
#   installs the distro's python3-evdev, which the venv sees through
#   --system-site-packages.
# - typing-extensions: system packages seen the same way (pydantic on CachyOS)
#   may need a newer one than the lock's, and pip ends with an ERROR if a pin
#   is below that. Newer releases stay backward compatible.
LEFT_TO_THE_SYSTEM = ("evdev", "typing-extensions")

HEADER = ("# The versions a source install uses: poetry.lock's, written by\n"
          "# packaging/make-constraints.py. Run it again after poetry.lock changes.\n")


def _marker(package):
    """The environment marker the lock gives this package for the main group, or ''."""
    markers = package.get("markers", "")
    if isinstance(markers, dict):
        return markers.get("main", "")
    return markers


def render(lock_text):
    """The constraints file's text for a poetry.lock's text."""
    packages = [p for p in toml.loads(lock_text)["package"]
                if "main" in p["groups"] and p["name"] not in LEFT_TO_THE_SYSTEM]
    lines = []
    for package in sorted(packages, key=lambda p: (p["name"], p["version"])):
        line = f"{package['name']}=={package['version']}"
        marker = _marker(package)
        lines.append(f"{line} ; {marker}" if marker else line)
    return HEADER + "\n".join(lines) + "\n"


def main():
    OUTPUT.write_text(render(LOCK.read_text(encoding="utf-8")), encoding="utf-8")


if __name__ == "__main__":
    main()
