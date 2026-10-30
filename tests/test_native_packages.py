"""The .deb and the .rpm install the same tree, staged by one shared script
(packaging/stage-from-appimage.sh) from the release AppImage.

These run the real build scripts on a stand-in AppImage in a copy of the
project, with stand-ins for fpm and rpmbuild that record the tree they were
handed, so what each package would install is checked, not the scripts' text.
"""
import hashlib
import os
import pathlib
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = "9.9.9"

# Writes the extracted tree an AppImage leaves in the current folder, with
# its licenses (unless TUXTALK_NO_LICENSES) and a file only its owner can read.
FAKE_APPIMAGE = """#!/bin/sh
[ "$1" = --appimage-extract ] || exit 2
mkdir -p squashfs-root/usr/lib squashfs-root/usr/share/licenses/tuxtalk \
         squashfs-root/usr/share/licenses/wl-clipboard
printf '#!/bin/sh\\n' > squashfs-root/AppRun
chmod 755 squashfs-root/AppRun
echo private > squashfs-root/usr/lib/update_checker.py
chmod 600 squashfs-root/usr/lib/update_checker.py
if [ -z "$TUXTALK_NO_LICENSES" ]; then
    echo "MIT License" > squashfs-root/usr/share/licenses/tuxtalk/LICENSE
    echo "# Third-party" > squashfs-root/usr/share/licenses/tuxtalk/THIRD_PARTY_LICENSES.md
    echo "GPL" > squashfs-root/usr/share/licenses/wl-clipboard/COPYING
fi
"""

# fpm: copies the staged tree (-C) and its after-install script to
# $RECORD, and writes the package (-p).
FAKE_FPM = """#!/bin/bash
while [ $# -gt 0 ]; do
    case "$1" in
        -C) stage="$2"; shift ;;
        -p) out="$2"; shift ;;
        --after-install) post="$2"; shift ;;
    esac
    shift
done
cp -a "$stage" "$RECORD/tree"
cp "$post" "$RECORD/postinst.sh"
echo package > "$out"
"""


@pytest.fixture
def project(tmp_path):
    """A copy of what the build scripts read, with a stand-in AppImage, fpm
    and rpmbuild: (the project folder, its environment, the record)."""
    copy = tmp_path / "project"
    copy.mkdir()
    for name in ("build-deb.sh", "build-rpm.sh", "pyproject.toml",
                 "io.github.ronb1964.TuxTalk.desktop", "io.github.ronb1964.TuxTalk.png"):
        shutil.copy2(ROOT / name, copy / name)
    shutil.copytree(ROOT / "packaging", copy / "packaging")
    (copy / "icons").mkdir()
    shutil.copy2(ROOT / "icons/OFFICIAL_ICON_DO_NOT_CHANGE.svg", copy / "icons")
    (copy / "pyproject.toml").write_text(f'[tool.poetry]\nversion = "{VERSION}"\n')
    appimage = copy / f"TuxTalk-v{VERSION}-x86_64.AppImage"
    appimage.write_text(FAKE_APPIMAGE)
    appimage.chmod(0o755)
    home_bin = tmp_path / "home" / "bin"
    home_bin.mkdir(parents=True)
    (home_bin / "fpm").write_text(FAKE_FPM)
    (home_bin / "rpmbuild").write_text("#!/bin/sh\n")
    for tool in home_bin.iterdir():
        tool.chmod(0o755)
    record = tmp_path / "record"
    record.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "RECORD": str(record)}
    return copy, env, record


def _build(project, script, **extra):
    copy, env, _record = project
    return subprocess.run(["bash", str(copy / script)], cwd=copy, env={**env, **extra},
                          capture_output=True, text=True, timeout=60)


def _desktop_exec_lines(tree):
    entry = tree / "usr/share/applications/io.github.ronb1964.TuxTalk.desktop"
    return [line for line in entry.read_text().splitlines() if line.startswith("Exec=")]


@pytest.mark.parametrize("script", ["build-deb.sh", "build-rpm.sh"])
def test_both_packages_install_the_appimage_tree_and_its_launchers(project, script):
    built = _build(project, script)
    assert built.returncode == 0, built.stderr
    tree = project[2] / "tree"
    # The folder keeps its pre-rename name: an upgrade replaces files in
    # place instead of deleting what a running 0.14.x tray imports from.
    assert (tree / "opt/talktype/AppRun").is_file()
    launcher = tree / "usr/bin/tuxtalk"
    assert os.access(launcher, os.X_OK)
    assert 'exec /opt/talktype/AppRun "$@"' in launcher.read_text()   # --toggle reaches the tray
    assert os.readlink(tree / "usr/bin/talktype") == "tuxtalk"         # the pre-rename command
    # Every Exec runs the launcher; the shortcuts keep their own arguments.
    assert _desktop_exec_lines(tree) == [
        "Exec=tuxtalk tray", "Exec=tuxtalk --open preferences", "Exec=tuxtalk --open transcribe",
        "Exec=tuxtalk --open help", "Exec=tuxtalk --quit"]
    assert (tree / "usr/share/icons/hicolor/scalable/apps/io.github.ronb1964.TuxTalk.svg").is_file()
    assert (tree / "usr/share/pixmaps/io.github.ronb1964.TuxTalk.png").is_file()


@pytest.mark.parametrize("script", ["build-deb.sh", "build-rpm.sh"])
def test_nothing_installs_readable_only_by_root(project, script):
    """A 0600 update_checker.py once landed as root:root 0600 and broke
    Preferences, About and Check for Updates for the user."""
    assert _build(project, script).returncode == 0
    mode = (project[2] / "tree/opt/talktype/usr/lib/update_checker.py").stat().st_mode
    assert mode & stat.S_IROTH and not mode & stat.S_IXOTH


@pytest.mark.parametrize("script, licenses", [
    ("build-deb.sh", {"usr/share/doc/tuxtalk/copyright",
                      "usr/share/doc/tuxtalk/THIRD_PARTY_LICENSES.md"}),
    ("build-rpm.sh", {"usr/share/licenses/tuxtalk/LICENSE",
                      "usr/share/licenses/tuxtalk/THIRD_PARTY_LICENSES.md"}),
])
def test_each_package_files_the_license_where_its_distro_looks(project, script, licenses):
    assert _build(project, script).returncode == 0
    tree = project[2] / "tree"
    for path in licenses:
        assert (tree / path).is_file(), path
    if script == "build-deb.sh":
        assert (tree / "usr/share/doc/tuxtalk/copyright").read_text().startswith("MIT License")


@pytest.mark.parametrize("script", ["build-deb.sh", "build-rpm.sh"])
def test_an_appimage_without_its_licenses_is_refused(project, script):
    built = _build(project, script, TUXTALK_NO_LICENSES="1")
    assert built.returncode != 0
    assert "rebuild it with ./build-release.sh" in built.stderr
    assert not (project[2] / "tree").exists()


@pytest.mark.parametrize("script", ["build-deb.sh", "build-rpm.sh"])
def test_the_after_install_script_never_fails_an_install(project, script):
    assert _build(project, script).returncode == 0
    post = project[2] / "postinst.sh"
    assert subprocess.run(["sh", "-n", str(post)]).returncode == 0
    assert post.read_text().rstrip().endswith("exit 0")


@pytest.mark.parametrize("script", ["build-deb.sh", "build-rpm.sh"])
def test_without_the_appimage_it_says_to_build_it_first(project, script):
    (project[0] / f"TuxTalk-v{VERSION}-x86_64.AppImage").unlink()
    built = _build(project, script)
    assert built.returncode != 0 and "Run ./build-release.sh first" in built.stderr


@pytest.mark.parametrize("script, package", [("build-deb.sh", f"tuxtalk_{VERSION}_amd64.deb"),
                                             ("build-rpm.sh", f"tuxtalk-{VERSION}-1.x86_64.rpm")])
def test_the_package_gets_its_checksum_for_the_updater(project, script, package):
    assert _build(project, script).returncode == 0
    sums = (project[0] / "SHA256SUMS.txt").read_text()
    assert sums.split()[1] == package


def _listing(tree, license_folder):
    """Every path in *tree* with its mode and what it holds (a link's target,
    a file's SHA256), leaving out the package's own license folder, the one
    place the two may differ."""
    listed = {}
    for path in sorted(tree.rglob("*")):
        relative = str(path.relative_to(tree))
        if relative == license_folder or relative.startswith(license_folder + "/"):
            continue
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            held = os.readlink(path)
        elif stat.S_ISREG(mode):
            held = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            held = None
        listed[relative] = (oct(mode), held)
    return listed


def test_the_deb_and_the_rpm_install_the_same_tree(project):
    """Staged by one script, so nothing drifts between them (the copies
    had begun to: a dependency, a comment, the order of two steps)."""
    record = project[2]
    trees = {}
    for script, licenses in (("build-deb.sh", "usr/share/doc"),
                             ("build-rpm.sh", "usr/share/licenses")):
        assert _build(project, script).returncode == 0
        trees[script] = _listing(record / "tree", licenses)
        shutil.rmtree(record / "tree")
    assert trees["build-deb.sh"] == trees["build-rpm.sh"]
    assert "opt/talktype/AppRun" in trees["build-deb.sh"]
