"""The local AI: self-corrections and voice editing (TuxTalk Labs).
CLAUDE.md Step 8: "AI self-corrections", "Voice editing".

The AI engine and model are copied in from this computer (1.1 GB, the
pinned commit hub_pins names) rather than downloaded in each VM.
"""
import subprocess
import time
from pathlib import Path

from harness import Abort, Outcome, check, heard_in
from vmlib import TOOLS

SECTION = "AI features"
LLAMA_TAG = "b11200"
MODEL_REPO_DIR = "models--Qwen--Qwen2.5-1.5B-Instruct-GGUF"
MODEL_COMMIT = "91cad51170dc346986eccefdc2dd33a9da36ead9"
MODEL_FILE = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
LOCAL_ENGINE = Path.home() / ".local/share/TuxTalk-dev/ai-engine" / LLAMA_TAG
LOCAL_MODEL = (Path.home() / ".cache/huggingface/hub" / MODEL_REPO_DIR / "snapshots"
               / MODEL_COMMIT / MODEL_FILE)


def _put_ai(run):
    """The AI engine and model, where TuxTalk's own download leaves them."""
    if not (LOCAL_ENGINE.is_dir() and LOCAL_MODEL.exists()):
        raise Abort(f"no AI engine or model on this computer to copy ({LOCAL_ENGINE})")
    archive = TOOLS / "ai-engine.tar"
    if not archive.exists():
        subprocess.run(["tar", "-cf", str(archive), "-C", str(LOCAL_ENGINE.parent), LLAMA_TAG],
                       check=True)
    run.vm.ssh("mkdir -p ~/.local/share/TuxTalk/ai-engine")
    run.vm.put(archive, ".local/share/TuxTalk/ai-engine/engine.tar")
    run.vm.ssh("cd ~/.local/share/TuxTalk/ai-engine && tar -xf engine.tar && rm engine.tar")
    snap = f".cache/huggingface/hub/{MODEL_REPO_DIR}/snapshots/{MODEL_COMMIT}"
    run.vm.ssh(f"mkdir -p ~/{snap} ~/.cache/huggingface/hub/{MODEL_REPO_DIR}/refs && "
               f"printf %s {MODEL_COMMIT} > ~/.cache/huggingface/hub/{MODEL_REPO_DIR}/refs/main")
    run.vm.put(LOCAL_MODEL.resolve(), f"{snap}/{MODEL_FILE}")


def _ai_on(run, **settings):
    """Turn the AI on with *settings*; returns once its engine is ready."""
    mark = run.log_mark()
    run.set_config(**settings)
    if run.wait_log(mark, "AI engine ready", timeout=180) is None:
        raise Abort("the AI engine didn't start")


@check(SECTION, "ai_self_correct", "AI self-corrections: “three, no wait, four” types four")
def ai_self_correct(run):
    _put_ai(run)
    _ai_on(run, ai_corrections=True)
    try:
        run.sink_ready(fresh=True)
        typed, heard, log = run.say("ai_self_correct")
    finally:
        run.set_config(ai_corrections=False)
    typed = (typed or "").rstrip()
    if "four" in typed.lower() and "three" not in typed.lower():
        return Outcome("pass", "log: “AI cleanup fixed a self-correction”"
                       if "fixed a self-correction" in log else "typed with four only",
                       typed=typed, heard=heard)
    return Outcome("fail", "the correction wasn't made", typed=typed, heard=heard)


@check(SECTION, "ai_voice_edit", "Voice editing: select, Shift+F8, “make this more formal”")
def ai_voice_edit(run):
    if "x11" in run.vm.spec.tags:
        return Outcome("skip", "Voice editing works only in a Wayland session, by design "
                       "(it reads the selection with wl-paste)")
    _ai_on(run, ai_corrections=True, voice_editing=True)
    try:
        run.sink_ready(fresh=True)
        before, _heard, _log = run.say("ai_edit_base")
        run.vm.tap("ctrl", "a")
        time.sleep(0.5)
        mark = run.log_mark()
        run.vm.key_down("shift")
        try:
            run.say("ai_edit_formal", key="f8", wait=False)
        finally:
            run.vm.key_up("shift")
        # The AI on a VM's processor can take a while over the rewrite.
        deadline = time.monotonic() + 60
        after = run.sink_text()
        while after.strip() == (before or "").strip() and time.monotonic() < deadline:
            time.sleep(1)
            after = run.sink_text()
        log = run.log_since(mark)
    finally:
        run.set_config(ai_corrections=False, voice_editing=False)
    shot = run.shot("voice-edit")
    if after and after.strip() != (before or "").strip() and "Voice edit" in log:
        return Outcome("pass", "the selection was rewritten", typed=after.strip(),
                       screenshot=shot)
    return Outcome("fail", "the selection wasn't rewritten", typed=after,
                   heard=heard_in(log), screenshot=shot)
