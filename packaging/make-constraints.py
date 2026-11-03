#!/usr/bin/env python3
"""Write packaging/source-install-constraints.txt from poetry.lock.

A source install (README) runs `pip install -c <this file> <release archive>`,
so it gets the versions the tests ran on instead of whatever PyPI has newest.
Only the lock's `main` group goes in: pytest, ruff and the other dev tools
are never installed there. Run it again after poetry.lock changes;
tests/test_source_install_constraints.py fails until you do:

    python3 packaging/make-constraints.py
"""
import pathlib

import toml

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCK = ROOT / "poetry.lock"
OUTPUT = pathlib.Path(__file__).resolve().parent / "source-install-constraints.txt"

# No wheel for it on PyPI, so pip would have to compile it. The README installs
# the distro's python3-evdev; a requirement that has no constraint accepts that
# one through the venv's --system-site-packages.
FROM_THE_DISTRO = ("evdev",)

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
                if "main" in p["groups"] and p["name"] not in FROM_THE_DISTRO]
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
