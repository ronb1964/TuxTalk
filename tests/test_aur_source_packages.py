"""The AUR packages that build TuxTalk from source (aur/python-onnx-asr, aur/tuxtalk).

makepkg isn't needed here: the PKGBUILD is sourced with bash, whose functions
are only defined, never run, and its values compared with the .SRCINFO that
the AUR reads, since a .SRCINFO that disagrees makes the AUR show a package
other than the one makepkg builds.
"""
import ast
import pathlib
import re
import subprocess
import sys

import pytest

from tuxtalk import config

ROOT = pathlib.Path(__file__).resolve().parent.parent
ONNX_ASR = ROOT / "aur/python-onnx-asr"
TUXTALK = ROOT / "aur/tuxtalk"


def _pyproject() -> dict:
    return config._parse_toml_text((ROOT / "pyproject.toml").read_text())


PYPROJECT_VERSION = _pyproject()["tool"]["poetry"]["version"]

# Printed as NUL-separated name, value pairs: arrays and plain strings alike
# come back as lists, and a variable the PKGBUILD doesn't set as none.
_PRINT_VALUES = r"""
source "$1"; shift
for name in "$@"; do
    declare -n ref="$name"
    for value in "${ref[@]}"; do printf '%s\0%s\0' "$name" "$value"; done
done
"""

SRCINFO_KEYS = ("pkgdesc", "url", "pkgver", "pkgrel", "arch", "license", "source",
                "sha256sums", "depends", "makedepends", "checkdepends", "optdepends", "conflicts",
                "install")

# The Arch package that provides each Python package TuxTalk needs, under the
# name pyproject.toml gives it and the name it is imported by. A name missing
# here fails the test, so a new dependency can't slip past the PKGBUILD.
ARCH_PACKAGE = {
    "faster-whisper": "python-faster-whisper",
    "faster_whisper": "python-faster-whisper",
    "onnx-asr": "python-onnx-asr",
    "onnx_asr": "python-onnx-asr",
    "onnxruntime": "python-onnxruntime",
    "sounddevice": "python-sounddevice",
    "evdev": "python-evdev",
    "numpy": "python-numpy",
    "toml": "python-toml",
    "text2num": "python-text2num",
    "text_to_num": "python-text2num",
    "gi": "python-gobject",
    "cairo": "python-cairo",
    "dbus": "python-dbus",
    "av": "python-av",
    "ctranslate2": "python-ctranslate2",
    "huggingface_hub": "python-huggingface-hub",
    "tqdm": "python-tqdm",
}

# The Arch package for each program TuxTalk runs and can't work without:
# Fix Typing and every other root step go through pkexec, and pasting on
# Wayland through wl-copy and wl-paste.
ARCH_PROGRAM = {
    "pkexec": "polkit",
    "wl-copy": "wl-clipboard",
    "wl-paste": "wl-clipboard",
}


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


def without_comments(text: str) -> str:
    """The shell text with its whole-line comments left out."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def shell_function(pkgbuild: pathlib.Path, name: str) -> str:
    """The body of a PKGBUILD function, comments left out."""
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", pkgbuild.read_text(), re.M | re.S)
    assert match, f"{pkgbuild} has no {name}()"
    return without_comments(match.group(1))


def assert_builds_from_arch_packages(pkgbuild: pathlib.Path) -> None:
    """build() runs python -m build against the installed packages, and nothing
    in the PKGBUILD fetches from PyPI or bypasses the dependency check."""
    build = [line for line in shell_function(pkgbuild, "build").splitlines()
             if "python -m build" in line]
    assert len(build) == 1 and "--no-isolation" in build[0]
    assert not re.search(r"\b(pip|venv|virtualenv)\b|--skip-dependency-check",
                         without_comments(pkgbuild.read_text()))


def main_dependencies() -> set[str]:
    """The packages pyproject.toml says TuxTalk runs on, Python itself left out."""
    return set(_pyproject()["tool"]["poetry"]["dependencies"]) - {"python"}


def imported_modules(source: str) -> set[str]:
    """The first name of every absolute import in the source, function bodies included."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def third_party_imports() -> set[str]:
    """What src/tuxtalk imports from outside the standard library and itself."""
    names: set[str] = set()
    for path in (ROOT / "src/tuxtalk").rglob("*.py"):
        names |= imported_modules(path.read_text())
    return names - set(sys.stdlib_module_names) - {"tuxtalk"}


def removed_paths(script: str) -> set[str]:
    """The absolute paths a shell script deletes with `rm -f` or `rmdir`."""
    code = without_comments(script.replace("\\\n", " "))
    paths: set[str] = set()
    for command in re.finditer(r"\b(?:rm -f|rmdir)\s+([^;|>\n]*)", code):
        paths.update(re.findall(r"(?<![\w.])/[\w./-]+", command.group(1)))
    return paths


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
    assert_builds_from_arch_packages(ONNX_ASR / "PKGBUILD")


def test_onnx_asr_srcinfo_says_what_the_pkgbuild_says():
    assert_srcinfo_matches(ONNX_ASR)


def test_an_import_inside_a_function_counts_as_a_dependency():
    source = "import os\nfrom . import x\ndef f():\n    import av\n    from ctranslate2.x import y\n"
    assert imported_modules(source) == {"os", "av", "ctranslate2"}


def test_a_comment_saying_no_isolation_doesnt_make_a_build_offline(tmp_path):
    pkgbuild = tmp_path / "PKGBUILD"
    pkgbuild.write_text("# python -m build --no-isolation\nbuild() {\n"
                        "    python -m build --wheel\n}\n")
    with pytest.raises(AssertionError):
        assert_builds_from_arch_packages(pkgbuild)


def test_tuxtalk_package_is_this_version():
    assert pkgbuild_values(TUXTALK / "PKGBUILD", "pkgver")["pkgver"] == [PYPROJECT_VERSION]


def test_every_dependency_tuxtalk_imports_has_its_arch_package():
    wanted = main_dependencies() | third_party_imports()
    assert wanted <= ARCH_PACKAGE.keys(), sorted(wanted - ARCH_PACKAGE.keys())
    depends = set(pkgbuild_values(TUXTALK / "PKGBUILD", "depends")["depends"])
    assert {ARCH_PACKAGE[name] for name in wanted} <= depends


def test_every_program_tuxtalk_cant_work_without_has_its_arch_package():
    source = "".join(path.read_text() for path in (ROOT / "src/tuxtalk").rglob("*.py"))
    for program in ARCH_PROGRAM:
        assert f'"{program}"' in source, program
    depends = set(pkgbuild_values(TUXTALK / "PKGBUILD", "depends")["depends"])
    assert set(ARCH_PROGRAM.values()) <= depends, sorted(set(ARCH_PROGRAM.values()) - depends)


def test_tuxtalk_builds_from_arch_packages_without_pip():
    assert_builds_from_arch_packages(TUXTALK / "PKGBUILD")
    needs = pkgbuild_values(TUXTALK / "PKGBUILD", "makedepends")["makedepends"]
    assert {"python-build", "python-installer", "python-poetry-core"} <= set(needs)


def test_a_system_install_gets_the_files_its_windows_and_menu_are_named_after():
    package = shell_function(TUXTALK / "PKGBUILD", "package")
    assert "usr/share/applications/io.github.ronb1964.TuxTalk.desktop" in package
    assert "s/^Exec=AppRun/Exec=tuxtalk/" in package
    assert "hicolor/scalable/apps/io.github.ronb1964.TuxTalk.svg" in package
    assert "hicolor-icon-theme" in pkgbuild_values(TUXTALK / "PKGBUILD", "depends")["depends"]
    for name in ("io.github.ronb1964.TuxTalk.desktop", "io.github.ronb1964.TuxTalk.png",
                 "icons/OFFICIAL_ICON_DO_NOT_CHANGE.svg", "LICENSE"):
        assert name in package and (ROOT / name).exists(), name


def test_tuxtalk_and_the_appimage_package_cant_be_installed_together():
    c = pkgbuild_values(TUXTALK / "PKGBUILD", "conflicts", "provides", "replaces")
    assert set(c["conflicts"]) == {"tuxtalk-appimage", "talktype-appimage", "talktype"}
    assert c["provides"] == [] and c["replaces"] == []


def test_removing_the_package_removes_the_hotkey_helper_and_an_upgrade_keeps_it():
    assert pkgbuild_values(TUXTALK / "PKGBUILD", "install")["install"] == ["tuxtalk.install"]
    hook = (TUXTALK / "tuxtalk.install").read_text()
    assert hook == (ROOT / "aur/tuxtalk-appimage.install").read_text()
    assert re.findall(r"^(\w+)\(\)", hook, re.M) == ["pre_remove"]
    helper_files = removed_paths((ROOT / "packaging/before-remove.sh").read_text())
    assert len(helper_files) == 5
    assert removed_paths(hook) == helper_files


def test_tuxtalk_srcinfo_says_what_the_pkgbuild_says():
    assert_srcinfo_matches(TUXTALK)
