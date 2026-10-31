"""What the dictation service remembers between key presses: one Recording
and one Message per dictation, new each time, and RecordingState, what the
service keeps across recordings, read as recording_state.state."""
from dataclasses import dataclass, field

from . import focused_window


@dataclass
class Message:
    """A recording's record on the dictation worker, which carries on with
    it once the recording has stopped: where its words went (for an
    automatic Enter or a key said after them), what its next phrase picks
    up, its Recent Dictations entry and its screen copy. Each recording has
    its own, so a recording started before the last one's phrases are typed
    can't take them over.

    Enter or Tab mid-recording (type_at_pause.start_afresh) ends the message
    its words make: to, has_words, complete and ends_on start over
    (start_over), and so do the held mark, cue and open question, and the
    screen copy (shown, shown_window). The rest lasts the whole recording:
    serial, cancelled, the Recent Dictations entry (said) and the text
    "edit that" waits to change.

    An automatic Enter and a key said after its words go only where they
    went, and only while all of them went in: a phrase that stopped partway
    leaves a hole a send would let out, and the phrases after it don't fill
    it."""
    # Where its words went (send_countdown.landed), None once the focus
    # moved while any of them went in: they may be in two places.
    to: focused_window.Destination | None = None
    # Words of it were delivered, or tried: a key said now isn't said alone.
    has_words: bool = False
    # Every delivery of it went in whole.
    complete: bool = True
    # The word its last words were cut off on ("and"), or "": at the end of
    # a Type at pause recording, half a sentence isn't sent.
    ends_on: str = ""
    # Which recording it is (send_countdown.recording_begins numbered it): a
    # send for it waits while a newer one records.
    serial: int = 0
    # What Type at pause's next phrase of this recording picks up: the last
    # phrase's final mark, held back for it to decide
    # (chunk_text.hold_back_boundary: {"mark", "audio", "raw", "typed",
    # "where"}); a "No wait." said alone, whose corrected value comes next; a
    # "?" heard as mid-sentence, so the question ends with the next phrase
    # (chunk_text.carry_question); and the text "edit that" said alone is to
    # change. Kept out of the repr: they hold what the user said.
    held_mark: dict | None = field(default=None, repr=False)
    held_cue: str = field(default="", repr=False)
    question_open: bool = False
    edit_waiting: str | None = field(default=None, repr=False)
    # Its recording was cancelled (Esc): a phrase of it still waiting to be
    # typed is dropped.
    cancelled: bool = False
    # Its recording's entry in Recent Dictations, all its phrases, typed or
    # tried (last_dictation.remember_said).
    said: str = field(default="", repr=False)
    # Its screen copy: the text it believes it put on screen in
    # shown_window, ending at the cursor while it's the last typed, which
    # "fix that word" and "edit that" work on; None once that can't be
    # trusted, and shown_window None until its first piece is typed
    # (last_dictation.remember_shown).
    shown: str | None = field(default="", repr=False)
    shown_window: tuple | None = None

    def add(self, outcome: str, landed) -> None:
        """Words of it were delivered: delivery.inject_text's *outcome*, and
        where they went (*landed*, None when not all of them did). Words
        known to be in another window move it there (the user switched
        windows between two phrases); a look that can't tell (a held-back
        mark typed where only the app is known) keeps where it was."""
        if not self.has_words or (self.to is not None and (
                landed is None or focused_window.changed(self.to, landed))):
            self.to = landed
        self.has_words = True
        self.complete = self.complete and outcome == "ok"

    def start_over(self) -> None:
        """Enter or Tab ended it: what's typed next is a new message."""
        self.to, self.has_words, self.complete, self.ends_on = None, False, True, ""


@dataclass
class Recording:
    """One recording, from its key going down until its audio is handed to
    the dictation worker: what the hotkey loop and the audio callback keep
    of it. Made new for each (recording.start_recording), so nothing of the
    last one carries over, and a block of audio or a phrase cut for one
    can't land in another. Its message is the half the dictation worker
    carries on with; its number is the message's serial."""
    message: Message = field(default_factory=Message)
    # Voice editing: an instruction for the selected text (Shift + the
    # dictation key), not dictation.
    edit_mode: bool = False
    # Its press held a pending send, so a tap too short to be a dictation
    # says "Not sent" rather than "Cancelled".
    held_send: bool = False
    # When its key went down (time.time()), for the shortest hold.
    press_t0: float | None = None
    # Raw int16 blocks since it began, or since Type at pause's last cut.
    frames: list = field(default_factory=list, repr=False)
    # finish_capture took its audio (under type_at_pause.chunk_lock): a
    # block still arriving is dropped.
    captured: bool = False
    stream: object = None
    # The open stream's rate, which may not be the engines' 16 kHz (an ALSA
    # hw: device's own); engine.SAMPLE_RATE until a stream opens.
    samplerate: int = 16000
    # Type at pause: its pause detector (chunking.PauseDetector) or None,
    # and how many phrases it has cut so far.
    chunker: object = None
    chunks_submitted: int = 0
    # Whether the audio after the last cut holds speech, judged at the stop
    # (recording._rest_holds_speech); without it the rest isn't transcribed.
    rest_has_speech: bool = True
    # Hands-free auto-stop (a silence.SilenceDetector), or None unless a
    # double-tap of the toggle key armed it.
    silence: object = None
    # That detector heard the speaker finish: the service tick stops this
    # recording as a second tap would. Its own, so an audio callback still
    # running for it can't stop the next one.
    went_quiet: bool = False
    # When the toggle key started it (None for any other start), so a quick
    # second tap can be told apart from a stop, and its start beep (what
    # beeps.beep returned, None with beeps off), which the second tap's
    # clock waits for.
    toggle_started_at: float | None = None
    start_beep: object = None
    # The hold key was already up again when it had finished starting, and
    # that took under 2 * MIN_HOLD_MS: a tap, though the start made it read
    # as a longer hold.
    released_while_starting: bool = False


@dataclass
class RecordingState:
    """What the dictation service keeps across recordings: whether one is
    under way and which, and what the last dictations typed and where,
    which undo, "edit that", select and move and the automatic Enter work
    from. Written on the dictation worker, in the order the dictations are
    typed, except is_recording and current (the thread that starts and
    stops recordings: the hotkey loop, or the Flatpak's service tick) and
    the hotkey loop's own keys."""
    is_recording: bool = False
    # The recording under way, else the last one.
    current: Recording = field(default_factory=Recording)
    # Undo history - tracks last inserted text for voice-activated undo
    last_inserted_text: str = field(default="", repr=False)
    # Select and move by voice: the known text (left, right) of the cursor
    # inside the last dictation after a word or letter move, so the next move
    # is counted exactly too; None when the cursor is somewhere unknown.
    nav_context: tuple | None = field(default=None, repr=False)
    # The window the last dictation was typed into (its class), or None.
    last_inserted_window: str | None = None
    # And which window has it: a focused_window.look() right after it
    # landed, for Type at pause's mark held back after it.
    last_inserted_where: tuple = (None, None)
    # Mid-sentence continuation - if True, lowercase the first letter of next dictation
    continue_mid_sentence: bool = False
    # Paste Last Dictation: {"key", "key_up", "at"} from the combo press until
    # every key of it is let go, else None (see _handle_key_event).
    paste_last: object = None
    # A Right Ctrl or Right Alt dictation key that's down and hidden from
    # the desktop (see hotkeys._hide_modifier_hotkey), or None.
    hidden_key: int | None = None
    # The recording the hold key's last press may stop when it comes up:
    # the one it started, or the one already running. None after a press
    # refused as someone else's shortcut (Alt+F8 resizes the window), so
    # its release leaves a toggle recording running.
    hold_stops: object = None
    # The last Enter TuxTalk pressed: {"at", "automatic"}, or None
    # (last_dictation.forget_after_key). "edit that" just after it explains itself.
    sent: object = None
    # The Message of the recording whose words went in last, or may have
    # (delivery.inject_text): its screen copy is the one "fix that word"
    # and "edit that" work on, and the one forgotten when the text changes.
    last_message: Message = field(default_factory=Message)


# The one the dictation service keeps, read as recording_state.state.
state = RecordingState()
