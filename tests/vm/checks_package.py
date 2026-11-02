"""A .deb or .rpm update installed the way Download & Install does it
(update_install.install_package_update): root's script copies the package
out of the user's updates folder, checks its SHA256 and size, and installs
that copy with the VM's own package manager. The VM's passwordless sudo
stands in for pkexec's password prompt; the script, its arguments and the
package manager's command are TuxTalk's own. The package is the one built
beside the AppImage, or --package's. Last in a run, since it installs
TuxTalk as a package. CLAUDE.md Step 4b.
"""
import hashlib
import re
import shlex
import subprocess
import sys
from pathlib import Path

from harness import Outcome, check

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))
from tuxtalk import update_install  # noqa: E402

SECTION = "Package update"
UPDATES = ".local/share/TuxTalk/updates"
# What the guest has, asked once: which install type, and the managers there.
PROBE = ("for c in rpm-ostree apt apt-get dpkg dnf zypper rpm; do "
         "command -v $c >/dev/null && echo $c; done")
LEFT_IN_VAR_TMP = "ls -d /var/tmp/tuxtalk-update.* 2>/dev/null | wc -l"


def project_folders():
    """This checkout and, when it's a git worktree, the main one: the build
    scripts write the packages where they're run."""
    folders = [SRC.parent]
    common = subprocess.run(["git", "-C", str(SRC.parent), "rev-parse", "--git-common-dir"],
                            capture_output=True, text=True, check=False).stdout.strip()
    if common:
        main = (SRC.parent / common).resolve().parent
        if main not in folders:
            folders.append(main)
    return folders


def package_for(run, kind):
    """The .deb or .rpm of the AppImage's version: beside it, or in the
    project folder where build-deb.sh and build-rpm.sh write them."""
    found = re.search(r"TuxTalk-v([\d.]+)-", run.appimage.name)
    if not found:
        return None
    version = found.group(1)
    name = (f"tuxtalk_{version}_amd64.deb" if kind == "deb"
            else f"tuxtalk-{version}-1.x86_64.rpm")
    for folder in (run.appimage.parent, *project_folders()):
        if (folder / name).is_file():
            return folder / name
    return None


def guest_kind(run):
    """("deb" or "rpm", the commands the guest has), or (None, why not)."""
    have = set(run.vm.ssh(PROBE, check=False).stdout.split())
    if "rpm-ostree" in have:
        return None, "an immutable system: TuxTalk updates there through the AppImage"
    if have & {"apt", "apt-get", "dpkg"}:
        return "deb", have
    if have & {"dnf", "zypper", "rpm"}:
        return "rpm", have
    return None, "no package manager TuxTalk knows"


def as_root(run, path, sha256, size, manager):
    """Root's install script as Download & Install sends it, through sudo:
    (exit code, what it printed)."""
    args = " ".join(shlex.quote(a) for a in [path, sha256, str(size), *manager])
    result = run.vm.ssh(f"sudo /bin/bash -s -- {args}", input=update_install._ROOT_INSTALL_SCRIPT,
                        check=False, timeout=900)
    return result.returncode, (result.stdout + result.stderr).strip()


def installed_version(run, kind):
    query = ("dpkg-query -W -f='${Version}' tuxtalk" if kind == "deb"
             else "rpm -q --qf '%{VERSION}' tuxtalk")
    result = run.vm.ssh(f"{query} 2>/dev/null", check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


@check(SECTION, "package_update",
       "A .deb or .rpm update: root installs its own checked copy, and refuses a swapped one")
def package_update(run):
    kind, have = guest_kind(run)
    if kind is None:
        return Outcome("skip", have)
    package = package_for(run, kind)
    if package is None:
        return Outcome("skip", f"no .{kind} of this version beside the AppImage or in the project")
    manager = update_install.package_install_argv(
        kind, which=lambda command: command if command in have else None)
    data = package.read_bytes()
    sha256, size = hashlib.sha256(data).hexdigest(), len(data)
    path = f"/home/ron/{UPDATES}/{package.name}"
    run.vm.ssh(f"mkdir -p ~/{UPDATES}")
    run.vm.put(package, f"{UPDATES}/{package.name}")
    problems = []

    code, said = as_root(run, path, "0" * 64, size, manager)
    if code != 3 or "changed after TuxTalk checked it" not in said:
        problems.append(f"a package that doesn't match its SHA256 wasn't refused "
                        f"({code}: {said[-200:]})")
    if installed_version(run, kind):
        problems.append("the refused package was installed anyway")

    code, said = as_root(run, path, sha256, size, manager)
    version = installed_version(run, kind)
    if code != 0 or not version:
        problems.append(f"{' '.join(manager)} didn't install it ({code}): {said[-400:]}")
    left = run.vm.ssh(LEFT_IN_VAR_TMP, check=False).stdout.strip()
    if left not in ("", "0"):
        problems.append(f"{left} copy folder(s) left in /var/tmp")

    remove = {"deb": "apt-get remove -y tuxtalk", "rpm": "rpm -e tuxtalk"}[kind]
    run.vm.ssh(f"sudo {remove}", check=False, timeout=600)
    detail = "; ".join(problems) or (
        f"{' '.join(manager)}: the swapped package refused, {package.name} installed "
        f"({version}) from root's checked copy, nothing left in /var/tmp")
    return Outcome("fail" if problems else "pass", detail)
