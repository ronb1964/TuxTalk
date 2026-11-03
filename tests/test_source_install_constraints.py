"""A source install gets poetry.lock's versions, and none of them needs a compiler.

The README installs TuxTalk with `pip install -c packaging/source-install-constraints.txt`.
If a pinned version has no wheel for the user's Python (numpy 2.2.6 had none for 3.14,
evdev none at all), pip builds it from source and fails on a computer without a
compiler. These tests fail as soon as the lock moves without the constraints file, or
pins a version with no wheel for a Python TuxTalk supports.
"""
import importlib.util
import pathlib

import pytest
import toml
from packaging.markers import Marker
from packaging.utils import canonicalize_name, parse_wheel_filename

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCK = ROOT / "poetry.lock"
CONSTRAINTS = ROOT / "packaging" / "source-install-constraints.txt"

_spec = importlib.util.spec_from_file_location("make_constraints",
                                               ROOT / "packaging" / "make-constraints.py")
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)


def _pins():
    """(name, version, marker text) of each line of the constraints file."""
    for line in CONSTRAINTS.read_text().splitlines():
        if line and not line.startswith("#"):
            requirement, _, marker = line.partition(" ; ")
            name, version = requirement.split("==")
            yield name, version, marker


def _environment(python):
    """Marker values for Linux x86_64 and a CPython of this version."""
    return {"python_version": python, "python_full_version": f"{python}.0",
            "implementation_name": "cpython", "platform_python_implementation": "CPython",
            "sys_platform": "linux", "platform_system": "Linux", "os_name": "posix",
            "platform_machine": "x86_64"}


def _has_wheel_for(filename, minor):
    """Whether this wheel installs on CPython 3.<minor>, Linux x86_64, without compiling."""
    _, _, _, tags = parse_wheel_filename(filename)
    for tag in tags:
        if not (tag.platform == "any"
                or (tag.platform.startswith("manylinux") and tag.platform.endswith("x86_64"))):
            continue
        if tag.interpreter == "py3" and tag.abi == "none":
            return True
        if tag.interpreter == f"cp3{minor}" and tag.abi in (f"cp3{minor}", "none"):
            return True
        if tag.abi == "abi3" and tag.interpreter.startswith("cp3") \
                and int(tag.interpreter[3:]) <= minor:
            return True
    return False


def missing_wheels(python):
    """The pinned packages that apply on this Python and have no installable wheel."""
    lock = {(canonicalize_name(p["name"]), p["version"]): p["files"]
            for p in toml.loads(LOCK.read_text())["package"]}
    minor = int(python.split(".")[1])
    missing = []
    for name, version, marker in _pins():
        if marker and not Marker(marker).evaluate(_environment(python)):
            continue
        files = [f["file"] for f in lock[(canonicalize_name(name), version)]
                 if f["file"].endswith(".whl")]
        if not any(_has_wheel_for(filename, minor) for filename in files):
            missing.append(f"{name} {version}")
    return missing


def test_the_constraints_file_matches_poetry_lock():
    assert mc.render(LOCK.read_text()) == CONSTRAINTS.read_text(), (
        "poetry.lock changed: run python3 packaging/make-constraints.py and commit the file")


def test_evdev_comes_from_the_distro_and_dev_tools_stay_out():
    names = {name for name, _, _ in _pins()}
    assert "evdev" not in names
    assert not names & {"pytest", "ruff", "black", "fonttools", "pillow"}


@pytest.mark.parametrize("python", ["3.10", "3.11", "3.12", "3.13", "3.14"])
def test_every_pinned_package_installs_from_a_wheel_on(python):
    """No compiler needed: each lock entry whose marker applies on Linux x86_64 with this
    Python has a py3-none-any wheel, a cp3XX manylinux x86_64 wheel, or an abi3 manylinux
    x86_64 wheel built for this Python or older (markers via packaging.markers.Marker)."""
    assert missing_wheels(python) == []
