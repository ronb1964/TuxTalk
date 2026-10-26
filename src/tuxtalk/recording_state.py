"""What the dictation service remembers between key presses: RecordingState,
and state, the one the service uses."""
from dataclasses import dataclass, field

from . import send_countdown


@dataclass
class RecordingState:
    """The dictation service's state between key presses: the recording
    under way, and what the last dictations typed and where, which undo,
    "edit that", select and move and the automatic Enter work from."""
    is_recording: bool = False
    was_cancelled: bool = False
    frames: list = None
    stream: object = None
    # The open stream's rate, which may not be the engines' 16 kHz (an ALSA hw:
    # device's own); engine.SAMPLE_RATE until a stream opens.
    recording_samplerate: int = 16000
    press_t0: float = None
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
    # What this recording has typed since it began or since its last
    # message ended, and where: where an automatic Enter or a key said after
    # it may go, and what its phrases wait on (send_countdown.Message).
    message: send_countdown.Message = field(default_factory=send_countdown.Message)
    # Mid-sentence continuation - if True, lowercase the first letter of next dictation
    continue_mid_sentence: bool = False
    # Hands-free auto-stop for this recording (a silence.SilenceDetector), or
    # None unless a double-tap of the toggle key armed it.
    silence: object = None
    # When the toggle key started this recording (None for any other start),
    # so a quick second tap can be told apart from a stop, and its start beep
    # (what beeps.beep returned, None with beeps off), which the second tap's
    # clock waits for.
    toggle_started_at: float = None
    start_beep: object = None
    # Paste Last Dictation: {"key", "key_up", "at"} from the combo press until
    # every key of it is let go, else None (see _handle_key_event).
    paste_last: object = None
    # Voice editing: this recording is an instruction for the selected text
    # (Shift + the dictation key), not dictation.
    edit_mode: bool = False
    # Chunked dictation: this recording's pause detector (chunking.PauseDetector)
    # or None, and how many chunks it has cut so far.
    chunker: object = None
    chunks_submitted: int = 0
    # This recording's press held a pending send, so a tap that's too short
    # to be a dictation says "Not sent" rather than "Cancelled".
    held_send: bool = False
    # The hold key was already up again when this recording had finished
    # starting, and that took under 2 * MIN_HOLD_MS: a tap, though the
    # start made it read as a longer hold (see the hold key).
    released_while_starting: bool = False
    # A Right Ctrl or Right Alt dictation key that's down and hidden from
    # the desktop (see hotkeys._hide_modifier_hotkey), or None.
    hidden_key: int | None = None
    # The last Enter TuxTalk pressed: {"at", "automatic"}, or None
    # (last_dictation.forget_after_key). "edit that" just after it explains itself.
    sent: object = None
    # How many deliveries delivery.inject_text has begun, on the dictation worker:
    # one begun after an automatic Enter's claim wasn't sent by it (pipeline.press_key).
    deliveries: int = 0
    # Speech the pause detector heard after its last cut, taken at the stop:
    # 0.0 means the rest of the recording is room noise only.
    last_chunk_speech: float = None
    # A recording typed in phrases (Type at pause) is still one dictation.
    # recording_serial counts recordings (send_countdown.recording_begins). said_text is all the
    # one numbered said_serial has typed or tried to: its Recent Dictations
    # entry (last_dictation.remember_said). shown_text is the part of it on screen in
    # shown_window, ending at the cursor, for "fix that word" to fix in
    # place; None once that isn't known (last_dictation.remember_shown).
    recording_serial: int = 0
    said_serial: int = -1
    said_text: str = ""
    shown_serial: int = -1
    shown_text: object = None
    shown_window: object = None

    def __post_init__(self):
        if self.frames is None:
            self.frames = []


# The one the dictation service keeps, read as recording_state.state.
state = RecordingState()
