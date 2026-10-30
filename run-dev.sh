#!/bin/bash
# TuxTalk development runner: starts the tray from this source tree, with the
# Python packages from .venv and GTK's Python bindings (PyGObject) from the
# system, which pip cannot install into a venv.

ROOT="$(cd "$(dirname "$0")" && pwd)" || exit 1
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "No .venv found in $ROOT. See DEV_SETUP.md." >&2
    exit 1
fi

# Enable dev mode to show both GTK and extension trays
export DEV_MODE=1

# NOTE: GDK_BACKEND is deliberately NOT set here. The recording indicator does
# need XWayland to position itself, but the tray sets that for the dictation
# service alone (see tray.py _launch_service). Exporting it here would reach the
# tray, and under XWayland GTK3 dropdowns do not stay open on a plain click —
# which made the model picker and every Preferences combo unusable.

# The tuxtalk package, then the system site-packages for the venv's Python
# version: lib64/lib on Fedora and Arch, dist-packages on Debian and Ubuntu.
PYVER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
export PYTHONPATH="$ROOT/src:/usr/lib64/python$PYVER/site-packages:/usr/lib/python$PYVER/site-packages:/usr/lib/python3/dist-packages"

# ctranslate2 (faster-whisper's engine) loads its own bundled libraries from
# this folder; TuxTalk preloads the CUDA libraries it downloaded itself
# (torch_init.py).
CT2_LIBS="$ROOT/.venv/lib64/python$PYVER/site-packages/ctranslate2.libs"
export LD_LIBRARY_PATH="$CT2_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

exec "$PY" -m tuxtalk.tray "$@"
