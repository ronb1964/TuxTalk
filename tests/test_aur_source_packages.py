"""The AUR packages that build TuxTalk from source (aur/python-onnx-asr, aur/tuxtalk).

makepkg isn't needed here: the PKGBUILD is sourced with bash, whose functions
are only defined, never run, and its values compared with the .SRCINFO that
the AUR reads, since a .SRCINFO that disagrees makes the AUR show a package
other than the one makepkg builds.
"""
import pathlib
import subprocess

from tuxtalk import config

ROOT = pathlib.Path(__file__).resolve().parent.parent
ONNX_ASR = ROOT / "aur/python-onnx-asr"
TUXTALK = ROOT / "aur/tuxtalk"

# Printed as NUL-separated name, value pairs: arrays and plain strings alike
# come back as lists, and a variable the PKGBUILD doesn't set as none.
_PRINT_VALUES = r"""
source "$1"; shift
for name in "$@"; do
    declare -n ref="$name"
    for value in "${ref[@]}"; do printf '%s\0%s\0' "$name" "$value"; done
done
"""

SRCINFO_KEYS = ("pkgver", "pkgrel", "arch", "license", "source", "sha256sums", "depends",
                "makedepends", "optdepends", "conflicts", "install")


def pkgbuild_values(pkgbuild: pathlib.Path, *names: str) -> dict[str, list[str]]:
    """The values `names` have once the PKGBUILD is sourced, each as a list."""
    out = subprocess.run(["bash", "-c", _PRINT_VALUES, "bash", str(pkgbuild), *names],
                         check=True, capture_output=True, cwd=pkgbuild.parent).stdout
    fields = out.decode().split("\0")[:-1]
    values: dict[str, list[str]] = {name: [] for name in names}
    for name, value in zip(fields[::2], fields[1::2], strict=True):
        values[name].append(value)
    return values


def srcinfo(folder: pathlib.Path) -> dict[str, list[str]]:
    """The `key = value` lines of the folder's .SRCINFO, each key as a list."""
    values: dict[str, list[str]] = {}
    for line in (folder / ".SRCINFO").read_text().splitlines():
        key, sep, value = line.strip().partition(" = ")
        if sep:
            values.setdefault(key, []).append(value)
    return values


def assert_srcinfo_matches(folder: pathlib.Path) -> None:
    """The folder's .SRCINFO says what its PKGBUILD says."""
    from_pkgbuild = pkgbuild_values(folder / "PKGBUILD", *SRCINFO_KEYS)
    from_srcinfo = srcinfo(folder)
    for key in SRCINFO_KEYS:
        assert from_srcinfo.get(key, []) == from_pkgbuild[key], key


def lock_version(name: str) -> str:
    """The version poetry.lock pins for a package."""
    lock = config._parse_toml_text((ROOT / "poetry.lock").read_text())
    return next(p["version"] for p in lock["package"] if p["name"] == name)


def test_onnx_asr_package_is_the_version_tuxtalk_locks():
    version = pkgbuild_values(ONNX_ASR / "PKGBUILD", "pkgver")["pkgver"]
    assert version == [lock_version("onnx-asr")]


def test_onnx_asr_builds_offline_from_arch_packages():
    needs = pkgbuild_values(ONNX_ASR / "PKGBUILD", "makedepends")
    assert {"python-hatchling", "python-hatch-vcs", "python-onnxscript",
            "python-ml-dtypes"} <= set(needs["makedepends"])
    text = (ONNX_ASR / "PKGBUILD").read_text()
    assert "--no-isolation" in text and "pip " not in text


def test_onnx_asr_srcinfo_says_what_the_pkgbuild_says():
    assert_srcinfo_matches(ONNX_ASR)
