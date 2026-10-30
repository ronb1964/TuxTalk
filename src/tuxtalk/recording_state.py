"""What the dictation service remembers between key presses: the Recording
under way (one per dictation, new each time), and RecordingState, what the
service keeps across recordings, read as recording_state.state."""
from dataclasses import dataclass, field

from . import send_countdown


@dataclass
class Recording:
    """One recording, from its key going down until its audio is handed to
    the dictation worker: what the hotkey loop and the audio callback keep
    of it. Made new for each (recording.start_recording), so nothing of the
    last one carries over, and a block of audio or a phrase cut for one
    can't land in another.

    Its message is the half the dictation worker carries on with: where its
    words went, what its next phrase picks up, and whether it was cancelled
    (send_countdown.Message). Its number is the message's serial."""
    message: send_countdown.Message = field(default_factory=send_countdown.Message)
    # Voice editing: an instruction for the selected text (Shift + the
    # dictation key), not dictation.
    edit_mode: bool = False
    # Its press held a pending send, so a tap too short to be a dictation
    # says "Not sent" rather than "Cancelled".
    held_send: bool = False
    # When its key went down (time.time()), for the shortest hold.
    press_t0: float = None
    # Raw int16 blocks since it began, or since Type at pause's last cut.
    frames: list = field(default_factory=list)
    stream: object = None
    # The open stream's rate, which may not be the engines' 16 kHz (an ALSA
    # hw: device's own); engine.SAMPLE_RATE until a stream opens.
    samplerate: int = 16000
    # Type at pause: its pause detector (chunking.PauseDetector) or None,
    # and how many phrases it has cut so far.
    chunker: object = None
    chunks_submitted: int = 0
    # Speech the pause detector heard after its last cut, taken at the stop:
    # 0.0 means the rest of the recording is room noise only.
    last_chunk_speech: float = None
    # Hands-free auto-stop (a silence.SilenceDetector), or None unless a
    # double-tap of the toggle key armed it.
    silence: object = None
    # When the toggle key started it (None for any other start), so a quick
    # second tap can be told apart from a stop, and its start beep (what
    # beeps.beep returned, None with beeps off), which the second tap's
    # clock waits for.
    toggle_started_at: float = None
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
    typed, except is_recording and current (the hotkey loop) and the
    hotkey loop's own keys."""
    is_recording: bool = False
    # The recording under way, else the last one.
    current: Recording = field(default_factory=Recording)
    # Undo history - tracks last inserted text for voice-activated undo
    last_inserted_text: str = ""
    # Select and move by voice: the known text (left, right) of the cursor
    # inside the last dictation after a word or letter move, so the next move
    # is counted exactly too; None when the cursor is somewhere unknown.
    nav_context: tuple = None
    # The window the last dictation was typed into (its class), or None.
    last_inserted_window: str = None
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
    # The last Enter TuxTalk pressed: {"at", "automatic"}, or None
    # (last_dictation.forget_after_key). "edit that" just after it explains itself.
    sent: object = None
    # The Message of the recording whose words went in last (set by
    # delivery.inject_text): its screen copy is the one "fix that word" and
    # forgetting it work on, and a correction across a pause reaches back
    # only over a phrase of its own recording.
    last_message: send_countdown.Message = field(default_factory=send_countdown.Message)


# The one the dictation service keeps, read as recording_state.state.
state = RecordingState()
