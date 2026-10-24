"""Dictation with nobody speaking: phrases played into the pretend microphone,
F8 and F9 pressed on the VM's own keyboard, and what TuxTalk typed read back
from the test window. CLAUDE.md Step 8, the dictation lines.
"""
import re
import time
import wave

import numpy as np
from harness import CONFIG, FINISHED, Abort, Outcome, check, heard_in
from vmlib import REMOTE

TERM_FILE = f"~/{REMOTE}/term.txt"


def contains_all(typed, wanted, absent=()):
    """Outcome for a dictation that must contain every piece of *wanted*."""
    typed = (typed or "").rstrip(" ")
    missing = [w for w in wanted if w not in typed]
    present = [a for a in absent if a in typed]
    problems = [f"missing “{m}”" for m in missing] + [f"has “{p}”" for p in present]
    return problems, typed


def pieces_outcome(run, phrase_id, wanted, absent=()):
    typed, heard, _log = run.say(phrase_id)
    problems, typed = contains_all(typed, wanted, absent)
    outcome = run.expect_typed(phrase_id, "", typed, heard) if problems else None
    status = "pass" if not problems else ("misheard" if outcome.status == "misheard" else "fail")
    return Outcome(status, "; ".join(problems) or "All there",
                   expected=" … ".join(wanted), typed=typed, heard=heard)


SPEAKERS_WAV = f"{REMOTE}/speakers.wav"
SPEAKERS_ERR = f"{REMOTE}/speakers.err"


def record_speakers(run, seconds):
    """Record what the speakers play (the default output's monitor) for the
    next *seconds*, in the background."""
    sink = run.vm.ssh("pactl get-default-sink").stdout.strip()
    run.vm.ssh(f"rm -f ~/{SPEAKERS_WAV}; setsid -f timeout {seconds} pw-record -P "
               f"'{{ stream.capture.sink=true }}' --target {sink} --rate 16000 --channels 1 "
               f"~/{SPEAKERS_WAV} > ~/{SPEAKERS_ERR} 2>&1 < /dev/null")
    time.sleep(1)       # pw-record takes a moment to connect; a sound before that is missed


def speakers_recording(run):
    """What record_speakers recorded, once it has finished: (mono samples,
    rate). The recording is deleted from the VM."""
    local = run.out_dir / f"{run.vm.spec.key}-speakers.wav"
    try:
        run.vm.get(SPEAKERS_WAV, local)
    except Exception as e:
        errors = run.vm.ssh(f"cat ~/{SPEAKERS_ERR}", check=False).stdout.strip()
        raise RuntimeError(f"pw-record made no recording ({errors or e})") from e
    finally:
        run.vm.ssh(f"rm -f ~/{SPEAKERS_WAV} ~/{SPEAKERS_ERR}", check=False)
    try:
        with wave.open(str(local)) as w:
            rate, channels = w.getframerate(), w.getnchannels()
            samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    finally:
        local.unlink()
    return samples.reshape(-1, channels).mean(axis=1), rate


def beep_bursts(samples, rate):
    """How many separate beeps a recording of the speakers holds."""
    window = rate // 50                                    # 20 ms
    usable = len(samples) // window * window
    rms = np.sqrt((samples[:usable].reshape(-1, window) ** 2).mean(axis=1))
    loud = rms > max(500, rms.max() * 0.2)
    bursts, quiet_run = 0, 99
    for is_loud in loud:
        if is_loud and quiet_run >= 8:                     # 160 ms of quiet between beeps
            bursts += 1
        quiet_run = 0 if is_loud else quiet_run + 1
    return bursts


TONE_STEP_S = 0.005
TONE_WINDOW_S = 0.02
# A tone at -50 dBFS: far under a beep (-14 dBFS at full volume), over hiss.
TONE_QUIETEST = 32768 * 10 ** (-50 / 20)


def tone_heard(samples, rate, freq):
    """The longest stretch of a recording in which a tone of *freq* Hz is
    the loudest sound: (where it starts, how long it is), in seconds, in
    steps of TONE_STEP_S. A whole tone reads as its length and about half a
    window more, as the windows over each of its edges still hold mostly
    the tone (tests/test_vm_beep_detector.py)."""
    hop, size = int(rate * TONE_STEP_S), int(rate * TONE_WINDOW_S)
    window = np.hanning(size)
    bins = np.fft.rfftfreq(size, 1 / rate)
    longest, start, current = 0, None, 0
    for step, i in enumerate(range(0, len(samples) - size, hop)):
        spectrum = np.abs(np.fft.rfft(samples[i:i + size] * window))
        # Through a Hann window a tone's peak is its amplitude times size / 4.
        heard = (spectrum.max() > TONE_QUIETEST * size / 4
                 and abs(bins[spectrum.argmax()] - freq) < 30)
        current = current + 1 if heard else 0
        if current > longest:
            longest, start = current, (step - current + 1) * TONE_STEP_S
    return start, longest * TONE_STEP_S


@check("Dictation", "basic", "Dictation works (processor)")
def basic(run):
    run.set_config(log_transcripts=True)    # so a miss shows what Parakeet heard
    run.sink_ready()
    typed, heard, _log = run.say("basic")
    if not typed:
        run.shot("basic-nothing-typed")
        raise Abort("nothing was typed: the remaining dictation checks would all fail")
    return run.expect_typed("basic", "This is a test of the dictation system.", typed, heard)


@check("Dictation", "beeps", "Start and stop beeps play")
def beeps(run):
    run.sink_ready()
    record_speakers(run, 9)
    run.say("basic")
    time.sleep(9)
    bursts = beep_bursts(*speakers_recording(run))
    if bursts >= 2:
        return Outcome("pass", f"{bursts} beeps heard on the speakers during one dictation")
    return Outcome("fail", f"{bursts} beeps heard on the speakers (expected a start and a stop "
                   "beep)")


def type_in_terminal(run, shot_name, key="f8"):
    """Dictate "git status" into a terminal, holding *key* (a QEMU key name);
    the Outcome: plain text, no stray character from the key."""
    run.vm.ssh(f"rm -f {TERM_FILE}")
    command = run.vm.spec.terminal + ["bash", "-c", f"stty -icanon -echo; exec cat > {TERM_FILE}"]
    run.vm.session_background("terminal", *command)
    time.sleep(4)
    run.vm.click_centre()
    time.sleep(0.5)
    _typed, _heard, mark = run.say("git_status", key=key, wait=False)
    typed = run.wait_dictation(mark, sink="term")   # reads ~/tt/term.txt
    shot = run.shot(shot_name)
    run.vm.ssh("pkill -x cat", check=False)
    time.sleep(1)
    run.vm.tap("ctrl", "shift", "w")   # close the terminal window
    time.sleep(1)
    # Whatever words Parakeet heard, a terminal gets them plain: no capital
    # at the start, no period at the end, nothing from F8.
    heard = heard_in(run.log_since(mark))
    plain = (heard[:1].lower() + heard[1:]).rstrip(".") if heard else "git status"
    typed_clean = (typed or "").rstrip(" ")
    if typed_clean == plain:
        outcome = Outcome("pass", "Plain: no capital, no period, nothing from F8",
                          expected=plain, typed=typed_clean, heard=heard)
    else:
        outcome = Outcome("fail", "", expected=plain, typed=typed_clean, heard=heard)
    if "\x1b" in typed_clean:
        outcome.detail = "F8 left an escape sequence in the terminal"
    outcome.screenshot = shot
    return outcome


@check("Dictation", "terminal_plain", "Plain text in a terminal, no stray character from F8")
def terminal_plain(run):
    blind = run.window_blind()
    if blind:
        return Outcome("skip", blind)
    return type_in_terminal(run, "terminal")


@check("Dictation", "handsfree_silence", "Hands-free stops by itself when nothing is said")
def handsfree_silence(run):
    # Double-tap hands-free is off until "Double-tap F9 for hands-free" is ticked.
    run.set_config(auto_stop_silence=True)
    run.sink_ready()
    mark = run.log_mark()
    # A relaxed pace, about half a second apart (#21: 0.4 s missed it),
    # still inside the app's 0.7 s double-tap window.
    run.vm.tap("f9", hold=0.1)
    time.sleep(0.4)
    run.vm.tap("f9", hold=0.1)
    log = run.wait_log(mark, "Auto-stop: nothing said", timeout=25)
    typed = run.sink_text()
    if log and not typed:
        return Outcome("pass", "Log: “Auto-stop: nothing said”; nothing typed")
    if log is None:
        run.vm.tap("f9")           # stop it, so the next check starts clean
    return Outcome("fail", f"auto-stop logged: {log is not None}; typed {typed!r}")


# The start and hands-free beeps (tuxtalk/beeps.py): Hz, and seconds long.
START_BEEP = (1200, 0.12)
HANDS_FREE_BEEP = (1600, 0.07)
# A sound output as slow as the one in issue #21: every program that plays
# through PipeWire's ALSA plugin, as TuxTalk does, gets a 0.17 s buffer.
# PipeWire before 1.2 reads client-rt.conf.d for it, later ones client.conf.d.
SLOW_OUTPUT = [f"~/.config/pipewire/{folder}/tuxtalk-test-slow-output.conf"
               for folder in ("client-rt.conf.d", "client.conf.d")]
SLOW_OUTPUT_CONF = ("alsa.properties = {\n  alsa.buffer-bytes = 32768\n"
                    "  alsa.period-bytes = 2048\n}\n")
# Under this, TuxTalk's output wasn't slowed (no PipeWire ALSA plugin, say).
SLOW_ENOUGH_S = 0.15


@check("Dictation", "handsfree_beeps",
       "Hands-free double-tap: both beeps are heard, on a slow sound output too")
def handsfree_beeps(run):
    # A file of that name the VM already had is put aside under a name of
    # this run's own, and back afterwards; only files this check wrote are
    # removed.
    aside = f".before-test-{int(time.time())}"
    moved, written = [], []
    try:
        for path in SLOW_OUTPUT:
            if run.vm.ssh(f"test -e {path}", check=False).returncode == 0:
                run.vm.ssh(f"mv {path} {path}{aside}")
                moved.append(path)
            written.append(path)
            run.vm.ssh(f"mkdir -p $(dirname {path}) && cat > {path}", input=SLOW_OUTPUT_CONF)
        # set_config restarts the service, which then plays through the slow
        # output; the first beep logs its latency.
        run.set_config(auto_stop_silence=True)
        run.sink_ready()
        # Long enough for both beeps, and over before the auto-stop, whose
        # silent-microphone notice has a sound of its own.
        record_speakers(run, 3)
        mark = run.log_mark()
        run.vm.tap("f9", hold=0.05)
        run.vm.tap("f9", hold=0.05)
        time.sleep(2.5)
        samples, rate = speakers_recording(run)
        log = run.log_since(mark)
        if run.wait_log(mark, "Auto-stop", timeout=25) is None:
            run.vm.tap("f9")           # stop it, so the next check starts clean
    finally:
        if written:
            run.vm.ssh("rm -f " + " ".join(written), check=False)
        for path in moved:
            run.vm.ssh(f"mv {path}{aside} {path}", check=False)
        run.restart_service()
    return beeps_outcome(log, samples, rate)


def beeps_outcome(log, samples, rate):
    """Whether the double-tap's two beeps were heard in full, in order, on an
    output the log shows was really slow."""
    if "Double-tap" not in log:
        return Outcome("fail", "The double-tap didn't switch the recording to hands-free")
    measured = re.search(r"Beeps: the output's latency is ([\d.]+)s", log)
    if not measured:
        return Outcome("fail", "The log never said the output's latency (no beep played?)")
    latency = float(measured.group(1))
    if latency < SLOW_ENOUGH_S:
        return Outcome("skip", f"The slow-output setting didn't reach TuxTalk's output: "
                       f"latency {latency:.3f}s")
    beeps = {"start": START_BEEP, "hands-free": HANDS_FREE_BEEP}
    heard = {name: tone_heard(samples, rate, freq) for name, (freq, _length) in beeps.items()}
    detail = ", ".join(f"{name} beep {seconds * 1000:.0f} of {beeps[name][1] * 1000:.0f} ms"
                       for name, (_start, seconds) in heard.items())
    # A whole beep reads as its length plus half a window or more; one cut
    # short by 10 ms reads 5 ms over its length at most.
    full = all(seconds >= beeps[name][1] + TONE_WINDOW_S / 2 - 1e-9
               for name, (_start, seconds) in heard.items())
    in_order = full and heard["start"][0] < heard["hands-free"][0]
    if full and not in_order:
        detail += "; the hands-free beep came first"
    return Outcome("pass" if in_order else "fail",
                   f"Heard on a {latency:.3f} s output: {detail}")


@check("Dictation", "silent_mic", "Silent microphone warning (and the switch to it is followed)")
def silent_mic(run):
    # Switch the desktop's default input to the dead microphone, then speak
    # into the live one: only a TuxTalk that follows the switch hears silence.
    run.sink_ready()
    run.vm.ssh("pactl set-default-source tt_quiet_mic")
    time.sleep(2)
    typed, _heard, log = run.say("basic")
    time.sleep(0.5)
    shot = run.shot("silent-mic")
    run.mic_change_log = log
    if typed:
        return Outcome("fail", "it recorded the old microphone: the switch to the silent one "
                       "wasn't followed", typed=typed, screenshot=shot)
    if "The microphone sent only silence" in log:
        return Outcome("pass", "Log: “The microphone sent only silence”; the notice is in the "
                       "screenshot", screenshot=shot)
    return Outcome("fail", "nothing typed, but the log never said “The microphone sent only "
                   "silence”", screenshot=shot)


@check("Dictation", "mic_follows_default", "Mic follows the system default")
def mic_follows_default(run):
    run.vm.ssh("pactl set-default-source tt_mic")
    time.sleep(2)
    run.sink_ready()
    typed, heard, log = run.say("basic")
    switch = "The system's default microphone changed to"
    changed = switch in run.mic_change_log and switch in log
    outcome = run.expect_typed("basic", "This is a test of the dictation system.", typed, heard)
    if not changed:
        outcome.status = "fail"
        outcome.detail = "the log didn't say “default microphone changed” for both switches"
    elif outcome.status == "pass":
        outcome.detail = "Log: “The system's default microphone changed to …”, both ways"
    return outcome


@check("Dictation", "press_enter", "“press enter” at the end types the sentence, then Enter")
def press_enter(run):
    run.sink_ready()
    typed, heard, log = run.say("noon_press_enter")
    outcome = run.expect_typed("noon_press_enter", "See you at noon.\n", typed, heard)
    if outcome.status == "pass" and 'Pressed Enter (said "press enter")' not in log:
        outcome.status, outcome.detail = "fail", "typed right, but the log has no “Pressed Enter”"
    return outcome


@check("Dictation", "press_enter_alone", "Saying only “press enter” just presses Enter")
def press_enter_alone(run):
    run.sink_ready()
    typed, heard, _log = run.say("press_enter_alone")
    return run.expect_typed("press_enter_alone", "\n", typed.rstrip(" ") if typed else typed, heard)


@check("Dictation", "press_enter_words", "“press enter to continue” mid-sentence is typed as words")
def press_enter_words(run):
    run.sink_ready()
    typed, heard, _log = run.say("press_enter_words")
    # Parakeet may write "Enter" with a capital; what matters is words, not a key press.
    expected = "Press enter to continue when the screen turns green."
    if typed and "\n" not in typed and typed.rstrip(" ").lower() == expected.lower():
        return Outcome("pass", "Typed as words, no Enter", expected=expected,
                       typed=typed.rstrip(" "), heard=heard)
    return run.expect_typed("press_enter_words", expected, typed, heard)


@check("Dictation", "password_field", "Nothing is typed into a password field, or kept")
def password_field(run):
    run.sink_ready("password", "TT Password", fresh=True, password=True)
    _typed, _heard, mark = run.say("cat_couch", sink="password", wait=False)
    refused = run.wait_log(mark, "in a password field", *FINISHED, timeout=25)
    shot = run.shot("password-field")      # the label is up for a few seconds only
    time.sleep(2)
    in_box = run.sink_text("password")
    run.close_sink("password")
    problems = []
    if in_box:
        problems.append(f"typed {in_box!r} into the password box")
    if not refused or "Not typed: the cursor is in a password field" not in refused:
        problems.append("the log never said “Not typed: the cursor is in a password field”")
    # Not in Recent Dictations: Paste Last Dictation in the ordinary window
    # mustn't bring it back (it types the dictation before, or nothing).
    run.sink_ready(fresh=True)
    paste_mark = run.log_mark()
    run.vm.tap("ctrl", "alt", "p")
    run.wait_log(paste_mark, "Paste last dictation", timeout=10)
    time.sleep(3)
    if "couch" in run.sink_text():
        problems.append("Paste Last Dictation typed it: it was kept in Recent Dictations")
    detail = "; ".join(problems) or ("Nothing typed or kept; log: “Not typed: the cursor is in a "
                                     "password field”; the label is in the screenshot")
    return Outcome("fail" if problems else "pass", detail, typed=in_box, screenshot=shot)



@check("Dictation", "desktop_password_prompt",
       "Nothing is typed into the desktop's own password prompt")
def desktop_password_prompt(run):
    """The prompt the desktop shows for pkexec: part of the shell on GNOME,
    Cinnamon and COSMIC, an app of its own on KDE and Xfce. /usr/bin/pkexec,
    not the harness's stand-in, and from a shell that stays: pkexec won't
    run once its parent is gone."""
    run.vm.session_background("pk", "bash", "-c", "/usr/bin/pkexec /bin/true; sleep 1")
    try:
        deadline = time.monotonic() + 20
        while run.vm.ssh("pgrep -c -f '[p]olkit-agent-helper-1'",
                         check=False).stdout.strip() in ("", "0"):
            if time.monotonic() > deadline:
                return Outcome("error", "The desktop's password prompt didn't open")
            time.sleep(0.5)
        time.sleep(1.5)
        _typed, _heard, mark = run.say("cat_couch", wait=False)
        refused = run.wait_log(mark, "in a password field", *FINISHED, timeout=25)
        shot = run.shot("desktop-password-prompt")
    finally:
        # A prompt left open would take the keyboard from every later check.
        run.vm.tap("esc")
        time.sleep(2)
        run.vm.ssh("sudo -n pkill -x pkexec", check=False)
    if refused and "Not typed: the cursor is in a password field" in refused:
        return Outcome("pass", "Log: “Not typed: the cursor is in a password field”",
                       screenshot=shot)
    return Outcome("fail", "TuxTalk typed into the prompt (no “Not typed: the cursor is in a "
                   "password field” in the log)", screenshot=shot)

@check("Dictation", "password_field_others", "Other text boxes still get the dictation")
def password_field_others(run):
    run.sink_ready()
    typed, heard, _log = run.say("cat_couch")
    return run.expect_typed("cat_couch", "The cat clawed the couch.", typed, heard)


@check("Words and numbers", "numbers_digits", "Numbers: As digits")
def numbers_digits(run):
    run.set_config(numbers_as_digits=True, numbers_small_as_words=False)
    run.sink_ready()
    return pieces_outcome(run, "numbers", ["3 cats", "25 chickens", "7:30", "15th"])


@check("Words and numbers", "numbers_small_words", "Numbers: Digits from 10 up")
def numbers_small_words(run):
    run.set_config(numbers_small_as_words=True)
    run.sink_ready()
    return pieces_outcome(run, "numbers", ["three cats", "25 chickens", "7:30", "15th"])


@check("Words and numbers", "letters", "Letters with numbers")
def letters(run):
    run.sink_ready()
    return pieces_outcome(run, "letters", ["F8", "B5 A7", "a five minute walk"])


@check("Words and numbers", "vitamin", "More letter and number pairs")
def vitamin(run):
    run.sink_ready()
    return pieces_outcome(run, "vitamin", ["B12", "E9"])


@check("Words and numbers", "money", "A dollar sign, typed key by key")
def money(run):
    """The keymaps also put $ on KEY_DOLLAR, which X11 can't carry: "$27.50"
    arrived as "27.50" (0.18 release testing)."""
    run.set_config(injection_mode="type")
    try:
        run.sink_ready()
        return pieces_outcome(run, "money", ["$27.50"])
    finally:
        run.set_config(injection_mode="auto")


@check("Words and numbers", "kwrite_new_line", "“New line” in KWrite copies nothing from the line above")
def kwrite_new_line(run):
    """KWrite takes Shift+Enter as Insert Smart Newline: after "$45.", "new
    line, $25" came out as "$$25." (0.18 release testing)."""
    if "kde" not in run.vm.spec.tags:
        return Outcome("skip", "Only KDE has KWrite")
    if run.vm.ssh("command -v kwrite", check=False).returncode:
        return Outcome("skip", "KWrite isn't installed")
    path = f"{run.vm.home}/{REMOTE}/kwrite.txt"
    run.vm.ssh(f"printf '%s' '$45.' > {path}")
    run.vm.session_background("kwrite", "kwrite", path)
    time.sleep(6)       # Qt apps aren't in the accessibility tree to wait on
    if run.vm.ssh("pgrep -x kwrite", check=False).returncode:
        return Outcome("fail", "KWrite didn't start")
    run.vm.click_centre()
    run.vm.tap("ctrl", "end")
    time.sleep(0.5)
    _typed, _heard, mark = run.say("money_new_line", wait=False)
    log = run.wait_log(mark, "Smart hybrid paste completed", *FINISHED)
    time.sleep(1)
    run.vm.tap("ctrl", "s")
    time.sleep(1.5)
    shot = run.shot("kwrite-new-line")
    run.vm.ssh("pkill -x kwrite", check=False)
    typed = run.vm.read(path)
    heard = heard_in(log or "")
    if typed.startswith("$45.\n$25.") and "$$" not in typed:
        return Outcome("pass", expected="$45.\n$25.", typed=typed, heard=heard, screenshot=shot)
    status = "misheard" if heard and "$25" not in heard else "fail"
    return Outcome(status, expected="$45.\n$25.", typed=typed, heard=heard, screenshot=shot)


@check("Words and numbers", "qmark_words", "Talking about a question mark keeps the words")
def qmark_words(run):
    run.sink_ready()
    typed, heard, _log = run.say("qmark_words")
    return run.expect_typed("qmark_words", "That sentence should end with a question mark.",
                            typed, heard)


@check("Words and numbers", "qmark_said", "Saying “question mark” still types one")
def qmark_said(run):
    run.sink_ready()
    typed, heard, _log = run.say("qmark_said")
    return run.expect_typed("qmark_said", "Is that right?", typed, heard)


@check("Words and numbers", "swiss_german", "Swiss German spelling (ß → ss)")
def swiss_german(run):
    run.set_config(numbers_as_digits=False, numbers_small_as_words=False, swiss_spelling=True)
    run.sink_ready()
    return pieces_outcome(run, "german", ["Strasse", "gross"], absent=["ß"])


@check("Words and numbers", "your_words", "Your Words for Parakeet")
def your_words(run):
    words_file = CONFIG.replace("config.toml", "custom_words.txt")
    run.vm.ssh(f"printf 'TuxTalk\\n' > {words_file}")
    mark = run.log_mark()
    run.set_config(swiss_spelling=False)    # the restart loads the word
    run.sink_ready()
    outcome = pieces_outcome(run, "your_words", ["TuxTalk"])
    log = run.log_since(mark)
    loaded = "Your Words for Parakeet" in log
    if outcome.status == "pass" and not loaded:
        outcome.status = "fail"
        outcome.detail = "spelled right, but the log never loaded Your Words for Parakeet"
    elif loaded:
        outcome.detail = "Log: “Your Words for Parakeet …”; " + outcome.detail
    return outcome


@check("Words and numbers", "your_words_ordinary", "Your Words leave ordinary sentences alone")
def your_words_ordinary(run):
    run.sink_ready()
    typed, heard, _log = run.say("cat_couch")
    return run.expect_typed("cat_couch", "The cat clawed the couch.", typed, heard)
