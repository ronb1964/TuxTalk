"""Your Words for Parakeet (parakeet_bias.py): a word is written whole where
the audio fits it, and ordinary speech is left alone.

Measured on Parakeet v3 (int8, processor) with synthetic speech, Oct 17 2026:
"TuckStock" -> TuxTalk, "Mambo Studio" -> BambuStudio, "Kig Steel" ->
Higgsfield, while 14 everyday sentences full of near misses ("tax talks",
"the cat clawed the couch", "bamboo poles", "free coffee") came out exactly
as without it. These tests pin the rules with a scripted decoder.
"""
import numpy as np
import pytest

from tuxtalk import parakeet_bias as pb
from tuxtalk import live_settings as live

# A tiny vocabulary in onnx-asr's form (word start = a plain space).
VOCAB = {0: " T", 1: "uck", 2: "ux", 3: " talk", 4: "Talk", 5: " on", 6: " Linux",
         7: "<unk>", 8: "<blk>"}
BLANK = 8


class FakeAsr:
    """Stands in for onnx-asr's NemoConformerTdt: _decode returns scores
    from *script(t, tokens)* (piece -> score, everything else -20, blank 0
    unless given), and a TDT step of 1 after a piece."""
    use_low_precision = False
    _blank_idx = BLANK
    _max_tokens_per_step = 10
    _vocab = VOCAB

    def __init__(self, script):
        self.script = script

    def _create_state(self):
        return ()

    def _decode(self, tokens, state, enc):
        t = int(enc[0])
        logits = np.full(len(VOCAB), -20.0, dtype=np.float32)
        logits[BLANK] = 0.0
        for pid, score in self.script(t, tokens).items():
            logits[pid] = score
        best = int(logits.argmax())
        return logits, (1 if best != BLANK else 0), (tuple(tokens),)


def _run(script, words, frames=6, **kw):
    asr = FakeAsr(script)
    tried = []
    booster = pb.WordBooster(VOCAB, words, BLANK, **kw)
    decode = pb.boosted_tdt_decoding(asr, booster, on_word=lambda w, c, k: tried.append((w, k)))
    enc = np.arange(frames, dtype=np.float32).reshape(1, frames, 1)
    tokens, _ts, _lp = next(decode(enc, np.array([frames])))
    return "".join(VOCAB[i] for i in tokens).strip(), tried


def tuck_talk(gap_ux, gap_talk):
    """Speech Parakeet hears as "Tuck talk on Linux"; the TuxTalk pieces are
    *gap_ux* and *gap_talk* below its favourites."""
    def script(t, tokens):
        last = VOCAB[tokens[-1]] if tokens else None
        if t == 0:
            return {0: 5}                                   # " T"
        if t == 1 and last == " T":
            return {1: 5, 2: 5 - gap_ux}                    # "uck" or "ux"
        if t == 2 and last in ("uck", "ux"):
            return {3: 5, 4: 5 - gap_talk}                  # " talk" or "Talk"
        if t == 3:
            return {5: 5}                                   # " on"
        if t == 4:
            return {6: 5}                                   # " Linux"
        return {}
    return script


def test_a_close_call_becomes_the_word():
    text, tried = _run(tuck_talk(gap_ux=2, gap_talk=3), ["TuxTalk"])
    assert text == "TuxTalk on Linux"
    assert ("TuxTalk", True) in tried


def test_a_far_fetched_match_leaves_the_speech_alone():
    """Each piece too far below Parakeet's favourite: nothing changes, and
    no half-word ("Tux talk") is left behind."""
    text, _tried = _run(tuck_talk(gap_ux=9, gap_talk=9), ["TuxTalk"])
    assert text == "Tuck talk on Linux"


def test_the_whole_word_must_fit_the_budget():
    """Each step is allowed, but the total is over the word budget."""
    text, tried = _run(tuck_talk(gap_ux=5, gap_talk=5), ["TuxTalk"], word_budget=8)
    assert text == "Tuck talk on Linux"
    assert ("TuxTalk", False) in tried


def test_without_words_it_is_parakeet_as_usual():
    text, tried = _run(tuck_talk(gap_ux=2, gap_talk=3), [])
    assert text == "Tuck talk on Linux"
    assert tried == []


def test_never_writes_where_parakeet_heard_nothing():
    """Silence (blank) at a point where a word could start stays silence."""
    def script(t, tokens):
        return {0: -1, BLANK: 0}     # " T" close to, but below, "nothing here"
    text, tried = _run(script, ["TuxTalk"])
    assert text == ""
    assert tried == []


def test_word_variants_and_spelling():
    assert pb.word_variants("ydotool") == ["ydotool", "Ydotool"]
    assert pb.word_variants("TuxTalk") == ["TuxTalk"]
    assert pb.word_variants("RBrand  Customs") == ["RBrand Customs"]
    pieces = {p.replace(" ", pb.WORD_START): i for i, p in VOCAB.items()}
    assert pb._Target("TuxTalk", pieces, 6).reachable()
    assert not pb._Target("Zebra", pieces, 6).reachable()
    # Words the pieces can't spell are skipped, not an error.
    assert not pb.WordBooster(VOCAB, ["Zebra"], BLANK)
    assert pb.WordBooster(VOCAB, ["TuxTalk", "Zebra"], BLANK)


def test_parakeet_model_switches_the_decoder_with_the_word_list(monkeypatch):
    from tuxtalk import parakeet_engine

    asr = FakeAsr(tuck_talk(2, 3))
    plain = object()
    asr._decoding = plain
    model = parakeet_engine.ParakeetModel.__new__(parakeet_engine.ParakeetModel)
    model._model = type("Adapter", (), {"asr": asr})()

    model.set_words(["TuxTalk"])
    assert asr._decoding is not plain
    boosted = asr._decoding
    model.set_words(["TuxTalk"])            # same list: left as is
    assert asr._decoding is boosted
    model.set_words([])                     # empty list: plain decoding again
    assert asr._decoding is plain


def test_a_broken_onnx_asr_keeps_the_plain_decoder(monkeypatch):
    """A future onnx-asr without these internals must not break dictation."""
    from tuxtalk import parakeet_engine

    class Odd:
        _decoding = "plain"
    model = parakeet_engine.ParakeetModel.__new__(parakeet_engine.ParakeetModel)
    model._model = type("Adapter", (), {"asr": Odd()})()
    model.set_words(["TuxTalk"])
    assert Odd._decoding == "plain"


def test_gpu_model_passes_words_to_its_processor_fallback():
    from tuxtalk import parakeet_gpu

    got = []
    gpu = parakeet_gpu.ParakeetGpuModel.__new__(parakeet_gpu.ParakeetGpuModel)
    gpu._words = ()
    gpu._cpu = None
    gpu.set_words(["TuxTalk"])
    gpu._cpu = type("Cpu", (), {"set_words": lambda self, w: got.append(w)})()
    gpu.set_words(["TuxTalk", "ClaudeClaw"])
    assert got == [("TuxTalk", "ClaudeClaw")]


@pytest.mark.parametrize("raw", ["git status", "Hello there."])
def test_dictation_hands_parakeet_the_word_list(monkeypatch, raw):
    from tuxtalk import app, parakeet_engine

    seen = []

    class Model(parakeet_engine.ParakeetModel):
        def __init__(self):
            pass

        def set_words(self, words):
            seen.append(tuple(words))

        def recognize(self, audio):
            return raw
    monkeypatch.setattr(app, "model", Model(), raising=False)  # set by main() normally
    monkeypatch.setattr(live, "words", ["TuxTalk"])
    audio = np.zeros(16000, dtype=np.float32)
    app._transcribe_audio(audio, None)
    assert seen == [("TuxTalk",)]


def test_engine_tables_flatten_every_spelling():
    """The lists TuxTalk's patched whisper.cpp takes (parakeet_set_word_boost):
    each target's positions, each position's (piece, next position)."""
    booster = pb.WordBooster(VOCAB, ["TuxTalk"], BLANK)
    targets, positions, pids, nexts = pb.engine_tables(booster)
    assert targets[0] == 0 and targets[-1] == len(positions) - 1
    assert positions[0] == 0 and positions[-1] == len(pids) == len(nexts)
    # Walk the first target from position 0 along the listed pieces.
    text = booster.targets[0].text
    pos, spelled = 0, ""
    while pos < len(text):
        p = targets[0] + pos
        options = list(zip(pids[positions[p]:positions[p + 1]], nexts[positions[p]:positions[p + 1]]))
        pid, nxt = max(options, key=lambda o: o[1])        # the longest piece
        spelled += VOCAB[pid]
        pos = nxt
    assert spelled.replace(" ", pb.WORD_START) == text
    assert pb.engine_settings(booster) == [6.0, 6.0, 3.0, 8.0, 30.0, 3.0]


def test_no_words_make_empty_tables():
    targets, positions, pids, nexts = pb.engine_tables(pb.WordBooster(VOCAB, [], BLANK))
    assert targets == [0] and positions == [0] and pids == [] and nexts == []
