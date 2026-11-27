"""Type at pause: the mark or space between two phrases goes in as fast as
it can, since the phrase after it waits for it, through the real pipeline
with a fake engine and fake typing (type_at_pause_rig.py)."""
import pytest

from tuxtalk import delivery
from type_at_pause_rig import fake_pipeline, frames_of, speech, type_chunk


@pytest.fixture
def modes(monkeypatch):
    """The fake engine and typing; returns (texts, [(text, mode) delivered])."""
    texts, _typed, _beeps = fake_pipeline(monkeypatch)
    sent = []
    fake_inject = delivery.inject_text

    def recording_inject(text, mode, *args, **kwargs):
        sent.append((text, mode))
        return fake_inject(text, mode, *args, **kwargs)
    monkeypatch.setattr(delivery, "inject_text", recording_inject)
    return texts, sent


@pytest.mark.parametrize(("mode", "input_method", "boundary"), [
    pytest.param("auto", False, "type", id="auto-types-it"),
    pytest.param("auto", True, "auto", id="auto-pastes-it-past-an-input-method"),
    pytest.param("paste", False, "paste", id="paste-chosen"),
    pytest.param("type", False, "type", id="type-chosen"),
])
def test_the_space_between_phrases_is_typed_unless_paste_was_chosen(
        modes, monkeypatch, mode, input_method, boundary):
    """Each paste took 0.3 s on GNOME: pasted in auto mode, the space held the
    last phrase back long enough for the user to press Enter before it arrived.
    Through an input method a typed mark can come out changed, so not there."""
    monkeypatch.setattr("tuxtalk.keyboard_layout.input_method_in_use", lambda: input_method)
    texts, sent = modes
    texts.extend(["see you at noon", "bring snacks", "see you at noon bring snacks"])
    type_chunk(frames_of(speech(1.0)), "middle", injection_mode=mode)
    type_chunk(frames_of(speech(1.0)), "middle", injection_mode=mode)
    assert sent == [("See you at noon", mode), (" ", boundary), ("bring snacks", mode)]
