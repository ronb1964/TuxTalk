"""Turns a run's results.json into report.html: every check per VM with its
verdict, what was typed against what was expected, the log lines it made and
its screenshot, and then the checks only a person can do.

Usage: python3 report.py RUN_FOLDER
"""
import html
import json
import sys
from collections import Counter
from pathlib import Path

# What the harness can't do in a VM, for Ron on his own computer.
MANUAL = [
    ("Graphics card", "Dictation on the NVIDIA card with Vulkan (log: “Parakeet on GPU” and "
     "“Parakeet on the graphics card from ggml-parakeet-ultra-q8_0.bin”), the speed check, the "
     "Parakeet update offer on the graphics card (Download (669 MB)), and CUDA with a Whisper "
     "model. The VMs have no graphics card."),
    ("Password", "Type the real password once at Fix Typing. The VM run only shows that the "
     "prompt appears once, then uses a test-only stand-in."),
    ("Your own voice", "A few dictations in your own voice and room, until your recordings "
     "replace the computer voice (record_phrases.py)."),
    ("KDE things", "Konsole and KWrite, KDE's volume applet, the KDE shortcut questions "
     "(F6 and Emoji Selector). The run types into a plain window of its own."),
    ("No systemd", "The fallback to the input group on a system without systemd."),
    ("Real apps", "A YouTube video in Brave and a Discord call for pause and sounds-down. The "
     "run uses a pretend player and a test tone, which test TuxTalk's side only."),
    ("AI features", "AI self-corrections, voice editing, “edit that”, select and move by voice "
     "and Type at pause. Not automated yet (the AI download is 1.1 GB)."),
    ("Transcribe a File and Backup", "These use the desktop's file dialog. Not automated yet."),
    ("Feel", "Whether it feels quick, the indicator looks right, and the wording reads well."),
]

STATUS_LABEL = {"pass": "Pass", "fail": "Fail", "misheard": "Misheard", "manual": "Manual",
                "skip": "Skipped", "error": "Harness error"}

STYLE = (Path(__file__).parent / "report.css").read_text()


def esc(text):
    return html.escape(str(text), quote=True)


def visible(text):
    """Typed text with its line breaks and spaces made visible."""
    return esc(text).replace("\n", "⏎\n") if text else "(nothing)"


def row(result):
    status = result["status"]
    parts = []
    if result.get("expected") is not None or result.get("typed") is not None:
        parts.append('<dl class="cmp">')
        if result.get("expected") is not None:
            parts.append(f"<dt>Expected</dt><dd>{visible(result['expected'])}</dd>")
        if result.get("typed") is not None:
            parts.append(f"<dt>Typed</dt><dd>{visible(result['typed'])}</dd>")
        if result.get("heard"):
            parts.append(f"<dt>Parakeet heard</dt><dd>{esc(result['heard'])}</dd>")
        parts.append("</dl>")
    if result.get("screenshot"):
        shot = esc(result["screenshot"])
        alt = esc(f"Screenshot: {result['name']}")
        parts.append(f'<a class="shot" href="shots/{shot}" target="_blank" rel="noopener">'
                     f'<img src="shots/{shot}" alt="{alt}" loading="lazy"></a>')
    if result.get("log"):
        parts.append(f'<pre class="log">{esc(result["log"])}</pre>')
    is_open = " open" if status in ("fail", "error", "misheard") else ""
    return (f'<li class="row is-{status}"><details{is_open}><summary>'
            f'<span class="pill {status}">{STATUS_LABEL[status]}</span>'
            f'<span class="name">{esc(result["name"])}</span>'
            f'<span class="secs">{result.get("seconds", 0):.0f} s</span>'
            f'<span class="detail">{esc(result.get("detail") or "")}</span>'
            f'</summary><div class="body">{"".join(parts)}</div></details></li>')


def tally(results):
    counts = Counter(r["status"] for r in results)
    order = ["pass", "fail", "misheard", "manual", "error", "skip"]
    return "".join(f'<span class="pill {s}">{counts[s]} {STATUS_LABEL[s]}</span>'
                   for s in order if counts[s])


def build(run_dir: Path):
    data = json.loads((run_dir / "results.json").read_text())
    results = data["results"]
    blocks = []
    for vm in data["vms"]:
        mine = [r for r in results if r["vm"] == vm["key"]]
        groups, sections = [], []
        for r in mine:
            if r["section"] not in sections:
                sections.append(r["section"])
        for section in sections:
            rows = "".join(row(r) for r in mine if r["section"] == section)
            groups.append(f'<div class="group"><h3>{esc(section)}</h3>'
                          f'<ul class="rows">{rows}</ul></div>')
        extra = "".join(f'<a class="shot" href="shots/{esc(s)}" target="_blank" rel="noopener">'
                        f'<img src="shots/{esc(s)}" alt="Preferences tab" loading="lazy"></a>'
                        for s in vm.get("gallery", []))
        if extra:
            groups.append('<div class="group"><h3>Preferences tabs</h3>'
                          f'<div class="gallery">{extra}</div></div>')
        blocks.append(f'<section class="vm"><div class="group"><h2>{esc(vm["label"])}</h2>'
                      f'<p class="meta"><span>{esc(vm["os"])}</span>'
                      f'<span>{esc(vm["desktop"])}</span></p>'
                      f'<div class="tally">{tally(mine)}</div></div>{"".join(groups)}</section>')
    manual = "".join(f"<li><b>{esc(t)}</b>{esc(d)}</li>" for t, d in MANUAL)
    voice_note = ("Phrases were spoken by " + ", ".join(data["voices"]) + ". “Misheard” "
                  "means Parakeet heard other words than the ones played, so the check says "
                  "nothing about TuxTalk either way.")
    page = f"""<title>TuxTalk VM Release Test</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;600;700&display=swap">
<style>{STYLE}</style>
<div class="wrap">
  <header>
    <h1>{esc(data["appimage"])} in the test VMs</h1>
    <p class="meta"><span>Run {esc(data["started"])}</span><span>{esc(data["duration"])}</span>
      <span>{len(data["vms"])} VM{"s" if len(data["vms"]) != 1 else ""}</span></p>
    <div class="tally">{tally(results)}</div>
    <p class="note">{esc(voice_note)}</p>
  </header>
  {"".join(blocks)}
  <section class="group">
    <h2>Still Ron's to check</h2>
    <p class="note">What a VM can't do. The checks above ran by themselves.</p>
    <ul class="manual-list">{manual}</ul>
  </section>
</div>
"""
    out = run_dir / "report.html"
    out.write_text(page)
    return out


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
