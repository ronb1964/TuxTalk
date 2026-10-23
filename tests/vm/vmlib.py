"""Host-side control of the libvirt test VMs: snapshots, ssh, the VM's own
virtual keyboard (through QEMU, so the guest kernel sees real key events, the
same as a USB keyboard), screenshots, and the guest helpers in guest/.
"""
import functools
import json
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

TOOLS = Path.home() / ".local/share/talktype-vm-tools"
SSH_KEY = TOOLS / "vmkey"
GUEST_DIR = Path(__file__).parent / "guest"
SNAPSHOT = "tuxtalk-clean"
REMOTE = "tt"           # folder in the guest's home for the harness's files


@dataclass(frozen=True)
class VMSpec:
    key: str            # short name used on the command line
    domain: str         # libvirt name
    ip: str
    width: int
    height: int
    tags: tuple         # what the checks can ask for: desktop, "no-tray", ...
    terminal: list      # command that opens a terminal running the rest of the line
    expected_device: str = '"cpu"'   # config.toml's device after first run (no GPU in a VM)

    @property
    def tray(self):
        return "no-tray" not in self.tags


VMS = {v.key: v for v in (
    VMSpec("silverblue", "Fedora Silverblue 44", "192.168.122.198", 1920, 1200,
           ("gnome", "no-tray", "immutable"), ["ptyxis", "--new-window", "--"]),
    # Ubuntu 26.04 ships Ptyxis as its terminal, and no gnome-terminal.
    VMSpec("ubuntu", "ubuntu26.04", "192.168.122.32", 1920, 1200, ("gnome",),
           ["ptyxis", "--new-window", "--"]),
    # Cinnamon opens a terminal small in a corner, where clicking the middle
    # of the screen would land on the test window behind it.
    VMSpec("mint", "MintCinnamon", "192.168.122.199", 1920, 1080, ("cinnamon", "x11"),
           ["gnome-terminal", "--maximize", "--wait", "--"]),
    VMSpec("kde", "fedora44-KDE", "192.168.122.227", 1920, 1080, ("kde",),
           ["konsole", "-e"]),
    # Zorin's own taskbar and AppIndicator extension on GNOME 46 (the Zorin
    # forum user who couldn't reach TuxTalk 0.15.1, 2026-10).
    VMSpec("zorin", "ZorinOS18", "192.168.122.88", 1280, 800, ("gnome",),
           ["gnome-terminal", "--wait", "--"]),
    # COSMIC (System76's own desktop, Pop's default since 24.04). cosmic-term
    # can't be given a command to run, so the checks use gnome-terminal.
    VMSpec("pop", "Pop!_OS", "192.168.122.40", 1280, 800, ("cosmic",),
           ["gnome-terminal", "--maximize", "--wait", "--"]),
    # Rolling release with the newest Plasma, and the .rpm outside Fedora.
    VMSpec("suse", "OpenSUSE_Tumbleweed", "192.168.122.182", 1280, 800, ("kde",),
           ["konsole", "-e"]),
    # The oldest libraries of the .deb distros, and Xfce on X11.
    VMSpec("debian", "debian13", "192.168.122.168", 1280, 800, ("xfce", "x11"),
           ["xfce4-terminal", "--maximize", "-x"]),
)}


def _virsh(*args, check=True):
    return subprocess.run(["virsh", "-c", "qemu:///system", *args],
                          check=check, capture_output=True, text=True)


class VM:
    def __init__(self, spec: VMSpec):
        self.spec = spec

    # Power and snapshots

    @functools.cached_property
    def home(self):
        """The test user's home folder: /var/home/ron on Silverblue, /home/ron elsewhere."""
        return self.ssh("echo $HOME").stdout.strip()

    def state(self):
        return _virsh("domstate", self.spec.domain).stdout.strip()

    def power_off(self, timeout=90):
        if self.state() == "shut off":
            return
        self.ssh("sudo -n systemctl poweroff", check=False)
        deadline = time.monotonic() + timeout
        while self.state() != "shut off":
            if time.monotonic() > deadline:
                _virsh("destroy", self.spec.domain, check=False)   # a test VM: nothing to save
                break
            time.sleep(2)

    def reboot(self):
        """Restart the guest and wait for the auto-login desktop again."""
        if self.ssh("sudo -n systemctl reboot", check=False).returncode != 0:
            _virsh("reboot", self.spec.domain)
        deadline = time.monotonic() + 60
        while self.ssh("true", check=False, timeout=10).returncode == 0:   # still going down
            if time.monotonic() > deadline:
                break
            time.sleep(2)
        self.wait_for_desktop()

    def take_clean_snapshot(self):
        self.power_off()
        _virsh("snapshot-delete", self.spec.domain, SNAPSHOT, check=False)
        _virsh("snapshot-create-as", self.spec.domain, SNAPSHOT,
               "No TuxTalk, no hotkey helper (release-test harness base)")

    def revert_and_boot(self):
        if self.state() != "shut off":
            _virsh("destroy", self.spec.domain, check=False)
        _virsh("snapshot-revert", self.spec.domain, SNAPSHOT)
        _virsh("start", self.spec.domain)
        self.wait_for_desktop()

    def wait_for_desktop(self, timeout=240):
        """Until ssh answers and the auto-login desktop has settled."""
        deadline = time.monotonic() + timeout
        while self.ssh("true", check=False, timeout=10).returncode != 0:
            if time.monotonic() > deadline:
                raise TimeoutError(f"{self.spec.key}: no ssh after {timeout}s")
            time.sleep(3)
        shells = "gnome-shell|plasmashell|cinnamon|cosmic-panel|xfce4-session"
        while self.ssh(f"pgrep -u $(id -u) -x '{shells}'", check=False).returncode != 0:
            if time.monotonic() > deadline:
                raise TimeoutError(f"{self.spec.key}: the desktop never started")
            time.sleep(3)
        time.sleep(10)   # the shell is up before its panel and audio are

    # ssh

    def ssh(self, command, input=None, check=True, timeout=600):
        result = subprocess.run(
            ["ssh", "-i", str(SSH_KEY), "-o", "BatchMode=yes", "-o",
             "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=8",
             f"ron@{self.spec.ip}", command],
            input=input, capture_output=True, text=True, timeout=timeout)
        if check and result.returncode != 0:
            raise RuntimeError(f"{self.spec.key}: {command!r} failed ({result.returncode}): "
                               f"{result.stderr.strip() or result.stdout.strip()}")
        return result

    def put(self, local, remote):
        subprocess.run(["scp", "-q", "-i", str(SSH_KEY), "-o", "BatchMode=yes", str(local),
                        f"ron@{self.spec.ip}:{remote}"], check=True)

    def get(self, remote, local):
        subprocess.run(["scp", "-q", "-i", str(SSH_KEY), "-o", "BatchMode=yes",
                        f"ron@{self.spec.ip}:{remote}", str(local)], check=True)

    def read(self, remote):
        """A guest file's text, or "" when it isn't there."""
        return self.ssh(f"cat {remote} 2>/dev/null", check=False).stdout

    def install_guest_helpers(self):
        self.ssh(f"mkdir -p ~/{REMOTE}")
        for path in GUEST_DIR.iterdir():
            self.put(path, f"{REMOTE}/{path.name}")
        self.ssh(f"chmod +x ~/{REMOTE}/*")

    # The desktop session

    def session(self, *command, check=True, timeout=120):
        """Run *command* in the desktop session and wait for it."""
        line = " ".join(shlex.quote(c) for c in command)
        return self.ssh(f"~/{REMOTE}/session-run.sh {line}", check=check, timeout=timeout)

    def session_background(self, name, *command):
        """Start *command* in the desktop session and leave it running; its
        output goes to ~/tt/NAME.out."""
        line = " ".join(shlex.quote(c) for c in command)
        self.ssh(f"setsid -f ~/{REMOTE}/session-run.sh {line} "
                 f"> ~/{REMOTE}/{name}.out 2>&1 < /dev/null")

    def ui(self, *args, timeout=20, check=False, hidden=False):
        """Run guest/ui.py; its JSON answer, or None when it found nothing."""
        options = ["--timeout", str(timeout)] + (["--hidden"] if hidden else [])
        line = " ".join(shlex.quote(a) for a in options + list(args))
        result = self.ssh(f"~/{REMOTE}/session-run.sh python3 ~/{REMOTE}/ui.py {line}",
                          check=False, timeout=timeout + 60)
        try:
            answer = json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            answer = {"error": result.stderr.strip() or result.stdout.strip()}
        if result.returncode != 0 or (isinstance(answer, dict) and "error" in answer):
            if check:
                raise RuntimeError(f"ui.py {line}: {answer}")
            return None
        return answer

    # Keyboard and mouse through QEMU

    def _qmp_input(self, events):
        command = {"execute": "input-send-event", "arguments": {"events": events}}
        _virsh("qemu-monitor-command", self.spec.domain, json.dumps(command))

    @staticmethod
    def _key(qcode, down):
        return {"type": "key", "data": {"down": down, "key": {"type": "qcode", "data": qcode}}}

    def key_down(self, *qcodes):
        self._qmp_input([self._key(q, True) for q in qcodes])

    def key_up(self, *qcodes):
        self._qmp_input([self._key(q, False) for q in reversed(qcodes)])

    def tap(self, *qcodes, hold=0.08):
        self.key_down(*qcodes)
        try:
            time.sleep(hold)
        finally:
            self.key_up(*qcodes)

    def move(self, x, y):
        """Put the pointer at screen pixel (x, y); QEMU's tablet counts 0-32767."""
        def axis(name, value, size):
            return {"type": "abs", "data": {"axis": name, "value": int(value * 32767 / (size - 1))}}
        self._qmp_input([axis("x", x, self.spec.width), axis("y", y, self.spec.height)])

    def click(self, x, y):
        self.move(x, y)
        time.sleep(0.05)
        for down in (True, False):
            self._qmp_input([{"type": "btn", "data": {"down": down, "button": "left"}}])
            time.sleep(0.05)

    def click_centre(self):
        self.click(self.spec.width // 2, self.spec.height // 2)

    def screenshot(self, path):
        ppm = Path(path).with_suffix(".ppm")
        _virsh("screenshot", self.spec.domain, str(ppm))
        image = Image.open(ppm).convert("RGB")
        image.thumbnail((1280, 1280))
        image.save(path, quality=72)
        ppm.unlink()
        return Path(path)
