"""Someone updating from 0.16 has the original Parakeet v3 (istupakov's files
in the Hugging Face cache) and no Parakeet Ultra. The VM is put in that state
after the run: the v3 files go in from this computer's own cache, Ultra's
come out, and TuxTalk starts again. It must keep dictating on v3, offer the
update once, remember Not Now, and still offer it in Preferences.
RELEASE_CHECKLIST.md, "Parakeet Ultra offer".
"""
import re
import time
from pathlib import Path

from harness import CONFIG, Abort, Outcome, check
from vmlib import TOOLS

V3_REPO = "models--istupakov--parakeet-tdt-0.6b-v3-onnx"
# The harness's own copy: fresh-start-for-testing.sh empties this computer's
# Hugging Face cache, and these checks ran without v3 after it (2026-10-20).
V3_CACHE = TOOLS / "hf-cache"
V3_FILES = ("config.json", "vocab.txt", "nemo128.onnx", "decoder_joint-model.int8.onnx",
            "encoder-model.int8.onnx")
ULTRA_DIR = "~/.local/share/TuxTalk/parakeet-ultra"
PREFS = "TuxTalk Preferences"


def put_original_v3(run):
    """The v3 download as 0.16 left it: refs/main naming a snapshot folder
    that holds the five files."""
    from huggingface_hub import snapshot_download
    local = Path(snapshot_download("istupakov/parakeet-tdt-0.6b-v3-onnx",
                                   allow_patterns=list(V3_FILES), cache_dir=V3_CACHE))
    remote = f".cache/huggingface/hub/{V3_REPO}"
    run.vm.ssh(f"mkdir -p ~/{remote}/refs ~/{remote}/snapshots/{local.name} && "
               f"printf %s {local.name} > ~/{remote}/refs/main")
    for name in V3_FILES:
        run.vm.put((local / name).resolve(), f"{remote}/snapshots/{local.name}/{name}")


def restart_tuxtalk(run):
    """Quit TuxTalk and start the AppImage again: the offer comes from the
    tray, once, about 20 s after it starts."""
    run.dbus("Quit")
    time.sleep(5)
    run.vm.ssh("pkill -f '[T]uxTalk-v'", check=False)
    time.sleep(2)
    mark = run.log_mark()
    run.vm.session_background("tuxtalk", f"{run.vm.home}/AppImages/{run.appimage.name}")
    if run.wait_log(mark, "Dictation service is ready", timeout=120) is None:
        raise Abort("TuxTalk didn't start again")
    return mark


@check("Parakeet update", "update_offer",
       "Updating from 0.16: Parakeet keeps working, the update is offered once")
def update_offer(run):
    put_original_v3(run)
    run.vm.ssh(f"rm -rf {ULTRA_DIR} && sed -i '/^parakeet_update_offered/d' {CONFIG}")
    mark = restart_tuxtalk(run)
    problems = []
    if not re.search(r"Loading Parakeet from \S*" + V3_REPO, run.log_since(mark)):
        problems.append("dictation didn't load the original Parakeet")
    offer = run.vm.ui("find", "label", "Parakeet got more accurate", timeout=60)
    shot = run.shot("update-offer")
    if offer is None:
        return Outcome("fail", "; ".join(problems + ["the offer never appeared"]), screenshot=shot)
    if run.vm.ui("find", "button", "Download (668 MB)", timeout=5) is None:
        problems.append("no “Download (668 MB)” button")
    run.vm.ui("click", "button", "Not Now", check=True)
    time.sleep(1)
    if run.config().get("parakeet_update_offered") != "true":
        problems.append("Not Now wasn't remembered")
    restart_tuxtalk(run)
    if run.vm.ui("find", "label", "Parakeet got more accurate", timeout=35) is not None:
        problems.append("the offer came back after Not Now")
        run.vm.ui("click", "button", "Not Now")
    detail = "; ".join(problems) or ("Original Parakeet kept working; “Parakeet got more accurate” "
                                     "with Not Now and Download (668 MB); Not Now remembered")
    return Outcome("fail" if problems else "pass", detail, screenshot=shot)


@check("Parakeet update", "update_in_prefs",
       "After Not Now, Preferences updates Parakeet and dictation moves onto it")
def update_in_prefs(run):
    run.dbus("OpenPreferences")
    if run.vm.ui("find", "page tab", "General", PREFS, timeout=30) is None:
        raise Abort("Preferences didn't open")
    problems = []
    if run.vm.ui("find", "label", "A more accurate Parakeet is available.", PREFS,
                 timeout=5) is None:
        problems.append("no “A more accurate Parakeet is available.” line")
    shot = run.shot("update-in-prefs")
    mark = run.log_mark()
    if run.vm.ui("click", "button", "Update Parakeet (668 MB)", PREFS) is None:
        run.vm.ui("click", "button", "Cancel", PREFS)
        return Outcome("fail", "; ".join(problems + ["no “Update Parakeet (668 MB)” button"]),
                       screenshot=shot)
    log = run.wait_log(mark, "Loading Parakeet from", timeout=300) or run.log_since(mark)
    if "Parakeet Ultra downloaded for cpu" not in log:
        problems.append("the log shows no download")
    if not re.search(r"Loading Parakeet from \S*/parakeet-ultra", log):
        problems.append("dictation didn't move onto Parakeet Ultra")
    run.vm.ui("click", "button", "Cancel", PREFS)
    detail = "; ".join(problems) or ("“A more accurate Parakeet is available.” with Update Parakeet "
                                     "(668 MB); downloaded, and dictation restarted on Ultra")
    return Outcome("fail" if problems else "pass", detail, screenshot=shot)
