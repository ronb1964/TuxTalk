"""Auto-detect held to the languages the user ticks (language_limit.py).

Whisper scores every language before it writes a word. While its pick is on
the user's list nothing changes; when it isn't, the dictation is run again in
the likeliest language that is.
"""
import numpy as np
import pytest

from tuxtalk import config, language_limit
from tuxtalk import engine
from tuxtalk import live_settings as live
from tuxtalk import whisper_vulkan as wv


# --- the setting -------------------------------------------------------------------

@pytest.mark.parametrize("text, codes", [
    ("", ()),
    ("en,es", ("en", "es")),
    (" EN , es ,", ("en", "es")),     # hand-edited spacing and capitals
    ("en,en,es", ("en", "es")),       # no repeats
    ("en,xx,es", ("en", "es")),       # a language the pickers don't offer is dropped
    ("xx", ()),
])
def test_parse(text, codes):
    assert language_limit.parse(text) == codes


def test_the_setting_is_written_in_the_pickers_order():
    assert language_limit.to_setting(["es", "en"]) == "en,es"
    assert language_limit.to_setting([]) == ""


@pytest.mark.parametrize("codes, shown", [
    ((), "Any language"),
    (("en",), "English"),
    (("en", "es"), "English, Spanish"),
    (("en", "es", "fr", "de", "it"), "English, Spanish, French and 2 more"),
])
def test_describe(codes, shown):
    assert language_limit.describe(codes) == shown


def test_one_language_is_simply_used():
    assert language_limit.forced_language(("es",)) == "es"
    assert language_limit.forced_language(("en", "es")) is None
    assert language_limit.forced_language(()) is None


def test_whisper_keeps_its_pick_when_it_is_on_the_list():
    assert language_limit.redirect("es", {"es": 0.6, "fr": 0.3}, ("en", "es")) is None


def test_no_list_never_redirects():
    assert language_limit.redirect("fr", {"fr": 0.9}, ()) is None


def test_off_the_list_goes_to_the_likeliest_on_it():
    # The tiny model heard a Spanish clip as French (measured 2026-10-14).
    scores = {"fr": 0.66, "de": 0.07, "en": 0.01, "es": 0.04}
    assert language_limit.redirect("fr", scores, ("en", "es")) == "es"


def test_a_language_without_a_score_counts_as_unlikely():
    assert language_limit.redirect("fr", {"fr": 0.9, "es": 0.01}, ("en", "es")) == "es"
    # Neither scored: the first on the list.
    assert language_limit.redirect("fr", {"fr": 0.9}, ("en", "es")) == "en"


@pytest.mark.parametrize("language, codes, expected", [
    ("ja", ("en", "es"), ("ja", ())),          # chosen by hand: the list waits
    (None, ("de",), ("de", ())),               # a list of one is that language
    ("", ("en", "es"), (None, ("en", "es"))),  # detected, held to the list
    (None, (), (None, ())),                    # no list: auto-detect as it is
])
def test_dictation_and_files_take_the_language_the_same_way(language, codes, expected):
    """Dictation and Transcribe a File each worked this out on their own,
    and had already drifted apart once (E-04)."""
    assert language_limit.language_and_limit(language, codes) == expected


def test_the_setting_defaults_to_any_language_and_applies_without_a_restart():
    assert config.Settings().auto_languages == ""
    assert "auto_languages" in config.LIVE_APPLIED_KEYS


def test_the_service_reads_it_on_each_reload(monkeypatch):
    monkeypatch.setattr(live, "ai_engine", None)
    live.apply(config.Settings(auto_languages="en,es"))
    assert live.auto_languages == ("en", "es")
    live.apply(config.Settings())
    assert live.auto_languages == ()


# --- the processor and CUDA engine (faster-whisper) ---------------------------------

class FakeWhisper:
    """faster-whisper's WhisperModel: detects first, decodes lazily."""
    def __init__(self, heard, scores):
        self.heard, self.scores, self.calls = heard, scores, []

    def transcribe(self, audio, language=None, **kwargs):
        self.calls.append(language)
        info = type("Info", (), {"language": language or self.heard,
                                 "all_language_probs": sorted(self.scores.items(),
                                                              key=lambda x: -x[1])})()
        seg = type("Seg", (), {"text": f"in {language or self.heard}", "no_speech_prob": 0.0,
                               "avg_logprob": -0.1, "compression_ratio": 1.0})()
        return iter([seg]), info


@pytest.fixture
def words_off(monkeypatch):
    monkeypatch.setattr(live, "words", [])


def _transcribe(monkeypatch, fake, languages, language=None):
    monkeypatch.setattr(engine, "model", fake, raising=False)
    monkeypatch.setattr(live, "auto_languages", languages)
    return engine.transcribe(np.zeros(16000, dtype=np.float32), language)


def test_faster_whisper_runs_again_in_a_listed_language(words_off, monkeypatch):
    fake = FakeWhisper("fr", {"fr": 0.66, "es": 0.2, "en": 0.01})
    assert _transcribe(monkeypatch, fake, ("en", "es")) == "in es"
    assert fake.calls == [None, "es"]


def test_faster_whisper_keeps_a_listed_pick_with_one_pass(words_off, monkeypatch):
    fake = FakeWhisper("es", {"es": 0.8, "en": 0.1})
    assert _transcribe(monkeypatch, fake, ("en", "es")) == "in es"
    assert fake.calls == [None]


def test_faster_whisper_with_one_language_just_uses_it(words_off, monkeypatch):
    fake = FakeWhisper("fr", {"fr": 0.9})
    _transcribe(monkeypatch, fake, ("de",))
    assert fake.calls == ["de"]


def test_a_language_chosen_by_hand_wins_over_the_list(words_off, monkeypatch):
    fake = FakeWhisper("fr", {"fr": 0.9})
    _transcribe(monkeypatch, fake, ("en", "es"), language="ja")
    assert fake.calls == ["ja"]


def test_without_a_list_auto_detect_is_untouched(words_off, monkeypatch):
    fake = FakeWhisper("fr", {"fr": 0.9})
    assert _transcribe(monkeypatch, fake, ()) == "in fr"
    assert fake.calls == [None]


# --- the graphics engine (whisper-server) --------------------------------------------

def test_language_scores_are_only_asked_for_when_needed():
    # Working them out is one more pass of the model: about a fifth of a short
    # dictation on built-in graphics (0.63 s -> 0.49 s measured, 2026-10-14).
    assert wv.request_fields("en")["no_language_probabilities"] == "true"
    assert wv.request_fields(None)["no_language_probabilities"] == "true"
    assert wv.request_fields(None, scores=True)["no_language_probabilities"] == "false"


def test_language_scores_from_a_reply():
    scores = wv.language_scores({"language_probabilities": {"es": "0.5", "fr": 0.25}})
    assert scores == {"es": 0.5, "fr": 0.25}
    assert wv.language_scores({"text": "hi"}) == {}


class FakeServer(wv.VulkanWhisperModel):
    """The engine with its HTTP request replaced: records each request's fields."""
    def __init__(self, heard, scores):
        self.heard, self.scores, self.requests = heard, scores, []

    def _inference(self, fields, wav, timeout):
        self.requests.append(fields)
        language = fields["language"] if fields["language"] != "auto" else self.heard
        reply = {"segments": [{"text": f"in {language}", "no_speech_prob": 0.0}]}
        if fields["no_language_probabilities"] == "false":
            reply["language_probabilities"] = self.scores
        return reply


def test_graphics_engine_runs_again_in_a_listed_language():
    server = FakeServer("fr", {"fr": 0.66, "es": 0.2, "en": 0.01})
    text, _ = server.transcribe(np.zeros(16000, dtype=np.float32), None, limit_to=("en", "es"))
    assert text == "in es"
    assert [r["language"] for r in server.requests] == ["auto", "es"]
    assert [r["no_language_probabilities"] for r in server.requests] == ["false", "true"]


def test_graphics_engine_keeps_a_listed_pick_with_one_request():
    server = FakeServer("es", {"es": 0.8, "en": 0.1})
    text, _ = server.transcribe(np.zeros(16000, dtype=np.float32), None, limit_to=("en", "es"))
    assert text == "in es" and len(server.requests) == 1


def test_graphics_engine_without_a_list_skips_the_scores():
    server = FakeServer("fr", {"fr": 0.9})
    server.transcribe(np.zeros(16000, dtype=np.float32), None)
    assert [r["no_language_probabilities"] for r in server.requests] == ["true"]


def test_the_service_passes_the_list_to_the_graphics_engine(words_off, monkeypatch):
    server = FakeServer("fr", {"fr": 0.66, "es": 0.2})
    assert _transcribe(monkeypatch, server, ("en", "es")) == "in es"
    assert [r["language"] for r in server.requests] == ["auto", "es"]


# --- Preferences ----------------------------------------------------------------------

def test_parakeet_says_it_does_not_use_the_list():
    from tuxtalk.prefs_speech import model_setting_warning
    note = model_setting_warning("parakeet-v3", "vulkan", "auto", "", auto_languages=("en", "es"))
    assert "does nothing with Parakeet" in note
    assert model_setting_warning("parakeet-v3", "vulkan", "auto", "") == ""
    assert model_setting_warning("small", "vulkan", "auto", "", auto_languages=("en", "es")) == ""
    # Manual mode names one language, so the list doesn't apply at all.
    assert "Whisper models" not in model_setting_warning(
        "parakeet-v3", "vulkan", "manual", "en", auto_languages=("en", "es"))
