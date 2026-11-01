#!/usr/bin/env python3
"""Runs a TuxTalk AppImage through the release checklist in the test VMs.

  run.py prepare VM            take TuxTalk off a running VM and save it as
                               the "clean" snapshot every run starts from
  run.py test VM... --appimage PATH [--keep-running]
                               revert each VM, run every check, write the report
  run.py test VM --appimage PATH --as-is --only ID,ID...
                               for writing checks: run just those checks on the
                               VM as it is now (no revert, no first run)
  run.py report RUN_FOLDER     rebuild a run's report.html

VMs: silverblue, ubuntu, mint, kde, zorin, pop, suse, debian (see vmlib.VMS).
Runs are kept in ~/.local/share/talktype-vm-tools/runs/. See README.md.
"""
import argparse
import itertools
import json
import subprocess
import sys
import time
import traceback
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import checks_ai  # noqa: F401
import checks_dictation  # noqa: F401  (each module registers its checks in import order)
import checks_extension  # noqa: F401
import checks_firstrun  # noqa: F401
import checks_hotkeys  # noqa: F401
import checks_prefs  # noqa: F401
import checks_send  # noqa: F401
import checks_type_at_pause  # noqa: F401
import checks_uninstall  # noqa: F401
import checks_update  # noqa: F401
import report
from harness import CHECKS, Abort, Result, Run
from vmlib import GUEST_DIR, REMOTE, TOOLS, VM, VMS

RUNS = TOOLS / "runs"
# First run must come before Preferences (its defaults), and Preferences
# before dictation, which changes settings. Uninstall takes it all away: last.
SECTION_ORDER = ["First run", "Preferences", "Dictation", "Words and numbers",
                 "Type at pause", "AI features", "Send automatically", "Sound and other windows",
                 "With the GNOME extension",
                 "Hotkeys and keyboard layouts", "Parakeet update", "Uninstall"]
LOG_LINES = 25


def prepare(key):
    vm = VM(VMS[key])
    if vm.state() != "running":
        subprocess.run(["virsh", "-c", "qemu:///system", "start", vm.spec.domain], check=True)
    vm.wait_for_desktop()
    print(vm.ssh("bash -s", input=(GUEST_DIR / "clean-tuxtalk.sh").read_text()).stdout.strip())
    vm.take_clean_snapshot()
    print(f"{key}: snapshot saved")


def set_up_vm(vm, appimage):
    """A freshly reverted VM with the guest helpers, the AppImage and the
    pretend microphones. Returns what the report says about the VM."""
    vm.revert_and_boot()
    vm.install_guest_helpers()
    # What's named TuxTalk or TalkType before anything is installed (old hand
    # testing on the VM): the uninstall checks only count what this run left.
    vm.ssh(f"find ~ -path ~/{REMOTE} -prune -o \\( -iname '*tuxtalk*' -o -iname '*talktype*' \\) "
           f"-print 2>/dev/null > ~/{REMOTE}/before-install.txt", check=False)
    if vm.ssh("command -v pactl", check=False).returncode:
        # Zorin ships without pactl. The pretend microphones need it, and so
        # does TuxTalk's "turn other sounds down", so a stock Zorin can't do
        # that: a finding of its own, not tested here.
        vm.ssh("sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y -q pulseaudio-utils",
               timeout=600)
    vm.ssh("mkdir -p ~/AppImages")
    vm.put(appimage, f"AppImages/{appimage.name}")
    vm.ssh(f"chmod +x ~/AppImages/{appimage.name}")
    tone = RUNS / "tone.wav"
    if not tone.exists():
        subprocess.run(["sox", "-n", "-r", "48000", "-c", "2", str(tone),
                        "synth", "30", "sine", "440", "vol", "0.2"], check=True)
    vm.put(tone, f"{REMOTE}/tone.wav")
    vm.ssh(f"~/{REMOTE}/vmic.sh")
    os_name = vm.ssh(". /etc/os-release; echo $PRETTY_NAME", check=False).stdout.strip()
    session = vm.ssh("loginctl show-session -p Type --value "
                     "$(loginctl list-sessions --no-legend | awk '$3==\"ron\"{print $1; exit}')",
                     check=False).stdout.strip()
    return {"key": vm.spec.key, "label": vm.spec.domain, "os": os_name,
            "desktop": f"{vm.spec.tags[0]} on {session or 'unknown session'}", "gallery": []}


def safe_shot(run, name):
    """A screenshot for a check that went wrong, if the VM can still take one."""
    try:
        return run.shot(name)
    except (OSError, subprocess.CalledProcessError):
        return None


def selected(spec, only=None):
    """The checks that run on the VM of *spec*, in run order: of *only*
    alone when it's given, and only those for that VM's desktop."""
    ordered = sorted(CHECKS, key=lambda c: SECTION_ORDER.index(c.section))
    return [c for c in ordered if (not only or c.id in only)
            and (not c.desktops or set(spec.tags) & set(c.desktops))]


def selection_problem(keys, only):
    """Why --only can't be run on the VMs of *keys*, or None. Asked before
    any VM is touched: a mistyped id would otherwise run nothing and pass."""
    if not only:
        return None
    unknown = only - {c.id for c in CHECKS}
    if unknown:
        return f"no check called {', '.join(sorted(unknown))}"
    idle = [key for key in keys if not selected(VMS[key], only)]
    if idle:
        return f"{', '.join(sorted(only))} doesn't run on {', '.join(idle)}"
    return None


def new_run_folder(started):
    """A folder of this run's own, named after when it started: a quick
    rerun never mixes its results and screenshots with the last one's."""
    base = RUNS / started.strftime("%Y-%m-%d-%H%M%S")
    RUNS.mkdir(parents=True, exist_ok=True)
    for n in itertools.count(1):
        folder = base if n == 1 else base.with_name(f"{base.name}-{n}")
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        return folder


def run_checks(run, info, results, save, only=None):
    stopped = None
    for chk in selected(run.vm.spec, only):
        base = dict(id=chk.id, name=chk.name, section=chk.section, vm=run.vm.spec.key)
        if stopped:
            results.append(Result(status="skip", detail=stopped, **base))
            continue
        print(f"  {chk.section} / {chk.name} ...", end=" ", flush=True)
        started = time.monotonic()
        mark = run.log_mark()
        try:
            outcome = chk.func(run)
        except Abort as stop:
            stopped = f"skipped: an earlier check stopped the run ({stop})"
            outcome = Result(status="fail", detail=str(stop))
            outcome.screenshot = safe_shot(run, f"{chk.id}-stopped")
        except Exception as error:
            last = traceback.format_exception(error)[-1].strip()
            outcome = Result(status="error", detail=f"The harness hit a problem: {last}")
            outcome.screenshot = safe_shot(run, f"{chk.id}-error")
            traceback.print_exc()
        log = "\n".join(run.log_since(mark).splitlines()[-LOG_LINES:])
        result = Result(**{**asdict(outcome), **base, "log": log,
                           "seconds": time.monotonic() - started})
        results.append(result)
        print(result.status)
        save()
    info["gallery"] = getattr(run, "prefs_shots", [])


def test(keys, appimage, keep_running, as_is=False, only=None):
    started = datetime.now()
    out_dir = new_run_folder(started)
    results, vms, voices_used = [], [], set()

    def save():
        data = {"appimage": appimage.name, "started": started.strftime("%Y-%m-%d %H:%M"),
                "duration": f"{(datetime.now() - started).seconds // 60} min",
                "vms": vms, "voices": sorted(voices_used) or ["nobody (no dictation ran)"],
                "results": [asdict(r) for r in results]}
        (out_dir / "results.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))

    for key in keys:
        vm = VM(VMS[key])
        if as_is:
            vm.install_guest_helpers()
            info = {"key": key, "label": vm.spec.domain, "os": "as it was", "desktop": "",
                    "gallery": []}
        else:
            print(f"{key}: reverting to the clean snapshot and booting")
            info = set_up_vm(vm, appimage)
        vms.append(info)
        run = Run(vm, out_dir, appimage)
        try:
            run_checks(run, info, results, save, only)
        finally:
            voices_used |= run.voices_used
            save()
            if not keep_running:
                vm.power_off()
    save()
    page = report.build(out_dir)
    print(f"Report: {page}")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("vm", choices=VMS)
    t = sub.add_parser("test")
    t.add_argument("vms", nargs="+", choices=VMS)
    t.add_argument("--appimage", type=Path, required=True)
    t.add_argument("--keep-running", action="store_true", help="leave the VMs on afterwards")
    t.add_argument("--as-is", action="store_true", help="use the VM as it is: no revert")
    t.add_argument("--only", type=lambda text: set(text.split(",")), help="check ids to run")
    r = sub.add_parser("report")
    r.add_argument("folder", type=Path)
    args = parser.parse_args()
    if args.command == "test" and (problem := selection_problem(args.vms, args.only)):
        parser.error(problem)
    if args.command == "prepare":
        prepare(args.vm)
    elif args.command == "test":
        results = test(args.vms, args.appimage.expanduser().resolve(),
                       args.keep_running or args.as_is, args.as_is, args.only)
        # Whoever started the run (a person or an agent) sees a failure in
        # the exit status too, not only in the report; so does a run that
        # ran nothing.
        sys.exit(1 if not results or any(r.status in ("fail", "error") for r in results)
                 else 0)
    else:
        print(report.build(args.folder))


if __name__ == "__main__":
    main()
