"""Preferences, read through the accessibility tree: the tabs, the new 0.16
options and their defaults. Nothing here is saved: each check leaves with
Cancel. CLAUDE.md Step 8, the Preferences lines.
"""
import time

from checks_firstrun import FLAGS
from harness import OUT, Abort, Outcome, check

PREFS = "TuxTalk Preferences"
TABS = ["General", "Audio", "Advanced", "Words", "Stats", "Updates & Backup"]


def widgets(run):
    return run.vm.ui("dump", PREFS) or []


def find(run, role, name):
    return run.vm.ui("find", role, name, PREFS, timeout=5)


def states_of(run, role, name, hidden=False):
    found = run.vm.ui("find", role, name, PREFS, timeout=5, hidden=hidden)
    return set(found["states"]) if found else None


def open_fold(run, show_label, window=PREFS):
    """Open a closed fold by its button ("Show fine-tuning"). A fold that
    opened by itself (System check, on a problem) already reads "Hide …"."""
    if run.vm.ui("find", "button", show_label, window, timeout=3) is not None:
        run.vm.ui("click", "button", show_label, window, check=True)
        time.sleep(0.5)    # the slide-down


def verdict(problems, good):
    return Outcome("fail" if problems else "pass", "; ".join(problems) or good)


@check("Preferences", "prefs_tabs", "Preferences opens with a Words tab and no Snippets tab")
def prefs_tabs(run):
    run.dbus("OpenPreferences")
    if run.vm.ui("find", "page tab", "General", PREFS, timeout=30) is None:
        raise Abort("Preferences didn't open")
    tabs = [w["name"] for w in widgets(run) if w["role"] == "page tab"]
    shots = []
    for tab in TABS:
        if tab in tabs:
            run.vm.ui("tab", tab, PREFS)
            time.sleep(0.8)
            shots.append(run.shot(f"prefs-{tab.split()[0].lower()}"))
    run.vm.ui("tab", "General", PREFS)
    run.prefs_shots = shots
    problems = []
    if tabs != TABS:
        problems.append(f"tabs are {tabs}")
    if "Snippets" in tabs:
        problems.append("there is still a Snippets tab")
    outcome = verdict(problems, "Tabs: " + ", ".join(tabs))
    outcome.screenshot = shots[0] if shots else None
    return outcome


@check("Preferences", "prefs_send",
       "“Send each dictation automatically”: off, choices up to 4 seconds")
def prefs_send(run):
    run.vm.ui("tab", "Advanced", PREFS)
    box = "Send each dictation automatically (press Enter)"
    problems = []
    before = states_of(run, "check box", box)
    combo_before = states_of(run, "combo box", "After 2 seconds")
    if before is None or combo_before is None:
        return Outcome("fail", "the option or its Send dropdown is missing")
    if "checked" in before:
        problems.append("it's ticked by default")
    if "sensitive" in combo_before:
        problems.append("“Send: After 2 seconds” isn't greyed while unticked")
    items = [i["name"] for i in run.vm.ui("items", "combo box", "After 2 seconds", PREFS) or []]
    wanted = ["Right away", "After 1 second"] + [f"After {n} seconds" for n in (2, 3, 4)]
    if items != wanted:
        problems.append(f"choices are {items}")
    run.vm.ui("click", "check box", box, PREFS)
    time.sleep(0.5)
    if "sensitive" not in (states_of(run, "combo box", "After 2 seconds") or set()):
        problems.append("ticking it doesn't enable the Send dropdown")
    run.vm.ui("click", "check box", box, PREFS)
    return verdict(problems, "Off, Send greyed until ticked, choices Right away to After 4 seconds")


@check("Preferences", "prefs_text_formatting",
       "Text defaults: numbers as written, no Swiss German, plain text in terminals on")
def prefs_text_formatting(run):
    problems = []
    if find(run, "combo box", "As the speech model writes them") is None:
        problems.append("Numbers doesn't read “As the speech model writes them”")
    # Offered only when German is in play; this is an English system.
    if states_of(run, "check box", "Swiss German spelling (ß → ss)", hidden=True) is not None:
        problems.append("Swiss German spelling is offered on an English system")
    plain = states_of(run, "check box", "Plain text in terminals and code editors", hidden=True)
    if plain is None or "checked" not in plain:
        problems.append("plain text in terminals is missing or off")
    return verdict(problems, "Numbers as the speech model writes them, no Swiss German spelling, "
                             "plain text in terminals ticked")


@check("Preferences", "prefs_folds", "Folds are buttons with a note beside them, no arrows")
def prefs_folds(run):
    problems = []
    texts = run.vm.ui("texts", PREFS) or []
    if find(run, "button", "Show fine-tuning") is None:
        problems.append("no “Show fine-tuning” button")
    elif "formatting, inserting text, privacy" not in texts:
        problems.append("no “formatting, inserting text, privacy” beside it")
    else:
        open_fold(run, "Show fine-tuning")
        if find(run, "button", "Hide fine-tuning") is None:
            problems.append("“Show fine-tuning” didn't turn into “Hide fine-tuning”")
        run.vm.ui("click", "button", "Hide fine-tuning", PREFS)
    # Closed with "✓ everything is set up", or open by itself on a problem.
    if find(run, "button", "Show system check") is not None:
        if "✓ everything is set up" not in texts:
            problems.append("System check is closed without “✓ everything is set up”")
    elif find(run, "button", "Hide system check") is not None:
        if "⚠ something needs your attention" not in texts:
            problems.append("System check opened by itself without saying why")
    else:
        problems.append("no System check button")
    run.vm.ui("tab", "General", PREFS)
    texts = run.vm.ui("texts", PREFS) or []
    if find(run, "button", "Show more shortcuts") is None:
        problems.append("no “Show more shortcuts” button")
    elif "cheat sheet Ctrl+Alt+V · paste last Ctrl+Alt+P" not in texts:
        problems.append("the shortcut keys aren't beside “Show more shortcuts”")
    if find(run, "button", "Change model, device or language") is None:
        problems.append("no “Change model, device or language” button")
    run.vm.ui("tab", "Audio", PREFS)
    run.vm.ui("click", "button", "Customize…", PREFS, check=True)
    indicator = "Recording Indicator"
    if run.vm.ui("find", "button", "Show fine position", indicator, timeout=10) is None:
        problems.append("no “Show fine position” in the Recording Indicator window")
    elif "not moved" not in (run.vm.ui("texts", indicator) or []):
        problems.append("“not moved” isn't beside “Show fine position”")
    run.vm.ui("click", "button", "Done", indicator)
    run.vm.ui("tab", "Advanced", PREFS)
    return verdict(problems, "Show fine-tuning, System check, Show more shortcuts, Change model, "
                             "device or language and Show fine position: buttons with their notes")


@check("Preferences", "prefs_labs", "TuxTalk Labs section, everything off")
def prefs_labs(run):
    names = {w["name"]: w for w in widgets(run)}
    wanted_buttons = ["How these work", "Send feedback", "Show tips again"]
    wanted_boxes = ["Follows the window you’re dictating into", "Type each phrase when you pause"]
    prefixes = ["Voice editing:", "Select and move:"]
    problems = [f"no “{b}” button" for b in wanted_buttons if b not in names]
    boxes = wanted_boxes + [n for n in names for p in prefixes if n.startswith(p)]
    if len(boxes) != 4:
        problems.append(f"Labs options found: {boxes}")
    problems += [f"“{b[:30]}” is on" for b in boxes
                 if "checked" in names.get(b, {}).get("states", [])]
    return verdict(problems, "Voice editing, Select and move, Type at pause, Indicator follows the "
                             "window: all off; How these work and Send feedback present")


@check("Preferences", "prefs_audio_defaults",
       "Audio: other sound “Pause music and videos”, mic on System Default")
def prefs_audio_defaults(run):
    run.vm.ui("tab", "Audio", PREFS)
    problems = []
    sound = run.vm.ui("items", "combo box", "Pause music and videos", PREFS, timeout=5)
    choices = [i["name"] for i in sound or []]
    if not choices:
        problems.append("“Other sound while you dictate” doesn't read “Pause music and videos”")
    elif choices != ["Pause music and videos", "Pause music and videos, turn the rest down",
                     "Turn everything down", "Leave it alone"]:
        problems.append(f"its choices are {choices}")
    if find(run, "combo box", "System Default") is None:
        problems.append("the microphone isn't on System Default")
    return verdict(problems, "Other sound: Pause music and videos (four choices), System Default "
                             "microphone")


@check("Preferences", "prefs_mic_test", "Preferences mic test records and replays")
def prefs_mic_test(run):
    run.vm.ui("click", "button", "Start Recording", PREFS, check=True)
    time.sleep(0.5)
    run.play("basic")
    stop = states_of(run, "button", "Stop Recording") or set()
    run.vm.ui("click", "button", "Stop Recording", PREFS)
    time.sleep(1)
    replay = states_of(run, "button", "Replay") or set()
    shot = run.shot("prefs-mic-test")
    problems = []
    if "sensitive" not in stop:
        problems.append("Stop Recording wasn't available while recording")
    if "sensitive" not in replay:
        problems.append("Replay isn't available after recording")
    outcome = verdict(problems, "Recorded from the pretend mic; Replay became available")
    outcome.screenshot = shot
    return outcome


@check("Preferences", "prefs_languages", "General: one Language dropdown, setup in one line")
def prefs_languages(run):
    run.vm.ui("tab", "General", PREFS)
    problems = []
    # Behind "Change model, device or language", closed; read it anyway.
    language = run.vm.ui("items", "combo box", "Detect automatically", PREFS, timeout=5,
                         hidden=True)
    items = [i["name"] for i in language or []]
    if not items:
        problems.append("the Language dropdown doesn't read “Detect automatically”")
    else:
        languages = [i for i in items if i != "Detect automatically"]
        if len(languages) != 35 or "Japanese (日本語)" not in languages:
            problems.append(f"Language lists {len(languages)} languages")
        if "Several languages…" in items:
            problems.append("“Several languages…” is offered on Parakeet")
        if any(FLAGS.search(i) for i in items):
            problems.append("flags in the language list")
    texts = run.vm.ui("texts", PREFS) or []
    if not any(t.startswith("Parakeet on") and "detects the language" in t for t in texts):
        problems.append("no “Parakeet on … · detects the language” line")
    if not any("recommended setup" in t for t in texts):
        problems.append("no “You're on the recommended setup” line")
    run.vm.ui("click", "button", "Cancel", PREFS)
    return verdict(problems, "Detect automatically and 35 native-name languages, no “Several "
                             "languages…” on Parakeet; the setup line and the recommended setup")


@check("Preferences", "prefs_model_switch",
       "Model: Parakeet to small and back, Preferences stays open; Cancel keeps Parakeet")
def prefs_model_switch(run):
    """0.17 test, 2026-10-20: Parakeet to small crashed Preferences, the
    Language dropdown's refill calling itself (5cec98a)."""
    # Preferences is its own process: an uncaught error in a GTK handler goes
    # to the tray's output, not always into the log.
    out_mark, mark = run.log_mark(OUT), run.log_mark()
    # prefs_languages' Cancel lets go of the pidfile a moment before that
    # Preferences exits, and opening one in between brings nothing up.
    run.vm.ssh("timeout 10 sh -c 'while pgrep -f [t]uxtalk.prefs >/dev/null; do sleep 0.3; done'",
               check=False)
    run.dbus("OpenPreferences")
    if run.vm.ui("find", "page tab", "General", PREFS, timeout=30) is None:
        return Outcome("fail", "Preferences didn't open")
    problems, closed, shot = [], False, None
    try:
        run.vm.ui("tab", "General", PREFS)
        open_fold(run, "Change model, device or language")
        # The Model dropdown is named by the id of its model (its first
        # column), its entries by what they show. Small is picked again at
        # the end, so Cancel has a change to throw away.
        small = ("small", "~small — good balance", True)
        back = ("small", "parakeet-v3", "~Parakeet — recommended", False)
        steps = [("parakeet-v3",) + small, back, ("parakeet-v3",) + small]
        for current, model, entry, whisper in steps:
            if run.vm.ui("pick", "combo box", current, entry, PREFS, timeout=5) is None:
                problems.append(f"couldn't pick {model} in the Model dropdown")
                break
            time.sleep(1.5)     # the loop happened at once, so 1.5 s is enough
            if run.vm.ui("find", "page tab", "General", PREFS, timeout=10) is None:
                problems.append(f"Preferences closed after picking {model}")
                closed = True
                break
            if find(run, "combo box", model) is None:
                problems.append(f"the Model dropdown doesn't read {model}")
                break
            languages = [i["name"] for i in run.vm.ui("items", "combo box", "Detect automatically",
                                                      PREFS, timeout=5) or []]
            if not languages:
                problems.append(f"no Language dropdown after picking {model}")
            elif ("Several languages…" in languages) != whisper:
                problems.append(f"the Language list wasn't refilled for {model}")
        shot = run.shot("prefs-model-switch")
    finally:
        cancelled = not closed and run.vm.ui("click", "button", "Cancel", PREFS, timeout=5)
    if not closed:
        time.sleep(1)
        if not cancelled:
            problems.append("couldn't press Cancel")
        elif run.vm.ui("find", "page tab", "General", PREFS, timeout=1) is not None:
            problems.append("Preferences stayed open after Cancel")
    model = run.config().get("model", "").strip('"')
    if model != "parakeet-v3":
        problems.append(f"Cancel left the model at {model}")
    lines = (run.log_since(mark) + run.log_since(out_mark, OUT)).splitlines()
    errors = ([line for line in lines if "RecursionError" in line]
              or [line for line in lines if "Traceback" in line])
    if errors:
        problems.append(f"in the log: “{errors[0].strip()[:120]}”")
    outcome = verdict(problems, "Parakeet to small and back: Preferences stayed open and the "
                                "Language list followed the model; small again, then Cancel kept "
                                "Parakeet")
    outcome.screenshot = shot
    return outcome
