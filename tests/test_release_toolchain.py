"""The repository must be able to build and release itself from a clean clone.

The scripts build-release.sh calls are the release toolchain, not dev scripts.
If any of them is left untracked (a .gitignore pattern can catch them), a fresh
clone dies immediately with "No such file or directory", and the ability to
ship lives only as untracked files on one machine.
"""

import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Everything the documented release process in CLAUDE.md needs in order to run.
REQUIRED_IN_GIT = [
    "build-release.sh",        # step 3
    "container-build.sh",      # called by build-release.sh
    "package-extension.sh",    # step 10
    "build-deb.sh",            # step 10
    "build-rpm.sh",            # step 10
    "packaging/checksums.sh",  # sourced by the three step 10 scripts
    "packaging/pinned-download.sh",  # sourced by container-build.sh
    "packaging/collect-licenses.sh",  # run by container-build.sh
    "packaging/stage-from-appimage.sh",  # sourced by build-deb.sh and build-rpm.sh
    "packaging/before-remove.sh",  # handed to fpm by build-deb.sh and build-rpm.sh
    "vulkan-engine-container.sh",  # run by build-vulkan-engine.sh (the Vulkan engine)
    "fresh-start-for-testing.sh",  # step 6
]


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    return set(out.stdout.split())


@pytest.mark.parametrize("path", REQUIRED_IN_GIT)
def test_release_toolchain_is_tracked(path):
    assert path in tracked_files(), (
        f"{path} is needed to build or release TuxTalk but is not in git. "
        f"A fresh clone cannot ship a release without it."
    )


def test_build_script_calls_nothing_that_is_missing_from_git():
    """Catches a new dependency being added and left untracked."""
    tracked = tracked_files()
    missing = []

    for script in ("build-release.sh", "container-build.sh"):
        script_path = ROOT / script
        if not script_path.exists():
            continue
        text = script_path.read_text()
        # Files referenced as /build/<name> inside the container, and as
        # ./<name> or bare <name>.sh / <name>.py from the project root.
        for name in re.findall(r"/build/([A-Za-z0-9_.-]+\.(?:sh|py))", text):
            if (ROOT / name).exists() and name not in tracked:
                missing.append(f"{script} -> {name}")

    assert not missing, (
        "the build calls files that exist locally but are not in git:\n  "
        + "\n  ".join(missing)
    )
