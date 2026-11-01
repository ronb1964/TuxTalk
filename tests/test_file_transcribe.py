"""Transcribe a File (file_transcribe.py; the window is transcribe_window.py).

The file is cut into pieces at quiet moments, pieces nobody talks in are
skipped, and the rest come out as paragraphs or as SRT subtitles.
"""
import itertools
import os
import types
import wave

import numpy as np
import pytest

from tuxtalk import config
from tuxtalk import file_transcribe as ft

SR = ft.SAMPLE_RATE


def _speech(seconds, seed=0):
    """Loud noise standing in for talking."""
    return np.random.default_rng(seed).uniform(-0.5, 0.5, int(seconds * SR)).astype(np.float32)


def _quiet(seconds):
    return np.zeros(int(seconds * SR), dtype=np.float32)


# --- cutting --------------------------------------------------------------------------

def test_a_short_file_is_one_piece():
    audio = _speech(12)
    assert ft.cut_points(audio) == [(0, len(audio))]


def test_pieces_cover_the_file_and_are_never_longer_than_30_seconds():
    audio = np.concatenate([_speech(7, i) if i % 2 == 0 else _quiet(1.5) for i in range(40)])
    cuts = ft.cut_points(audio)
    assert cuts[0][0] == 0 and cuts[-1][1] == len(audio)
    assert all(a[1] == b[0] for a, b in itertools.pairwise(cuts))         # no gaps, no overlap
    assert all((end - start) / SR <= ft.MAX_PIECE_S for start, end in cuts)
    assert all((end - start) / SR >= ft.MIN_PIECE_S for start, end in cuts[:-1])


def test_a_cut_lands_in_the_long_pause_not_a_short_one():
    # 18 s talk, a comma's 0.15 s pause, 3 s talk, then a sentence's 1.2 s pause.
    audio = np.concatenate([_speech(18), _quiet(0.15), _speech(3, 1), _quiet(1.2), _speech(20, 2)])
    first_cut = ft.cut_points(audio)[0][1] / SR
    assert 21.15 < first_cut < 22.35


# --- transcribing ------------------------------------------------------------------------

class FakeEngine:
    def __init__(self):
        self.calls = 0
        self.loudest = []

    def text(self, audio):
        self.calls += 1
        self.loudest.append(float(np.abs(audio).max()))
        return f"Piece {self.calls}."


def test_quiet_pieces_are_skipped_and_progress_reaches_the_end():
    audio = np.concatenate([_speech(25), _quiet(1), _quiet(29), _quiet(1), _speech(20, 3)])
    seen, engine = [], FakeEngine()
    pieces = ft.transcribe(audio, engine, progress=lambda done, total: seen.append((done, total)))
    assert len(pieces) == engine.calls < len(ft.cut_points(audio))   # one piece never ran
    assert all(loudest > 0 for loudest in engine.loudest)             # and it was the silent one
    assert seen[-1][0] == pytest.approx(len(audio) / SR)


def test_stop_keeps_what_is_done():
    audio = np.concatenate([_speech(25, i) for i in range(4)])
    engine = FakeEngine()
    pieces = ft.transcribe(audio, engine, cancelled=lambda: engine.calls >= 2)
    assert len(pieces) == 2


# --- writing it out ------------------------------------------------------------------------

@pytest.mark.parametrize("seconds, short, srt", [
    (5, "0:05", "00:00:05,000"),
    (65.25, "1:05", "00:01:05,250"),
    (3725.5, "1:02:05", "01:02:05,500"),
])
def test_clock(seconds, short, srt):
    assert ft.clock(seconds) == short
    assert ft.clock(seconds, srt=True) == srt


def test_paragraphs_carry_on_across_a_cut_in_mid_sentence():
    pieces = [ft.Piece(0, 20, "The batteries are lithium"), ft.Piece(20, 40, "and last ten years."),
              ft.Piece(40, 60, "Next week, cabinets.")]
    assert ft.to_text(pieces) == "The batteries are lithium and last ten years.\n\nNext week, cabinets."
    assert ft.to_text(pieces, times=True).startswith("[0:00] The batteries")
    assert "\n\n[0:40] Next week" in ft.to_text(pieces, times=True)


def test_subtitles_split_by_sentence_inside_their_piece():
    srt = ft.to_srt([ft.Piece(10, 20, "First one. Second one here.")])
    blocks = srt.strip().split("\n\n")
    assert blocks[0].splitlines() == ["1", blocks[0].splitlines()[1], "First one."]
    assert blocks[0].splitlines()[1].startswith("00:00:10,000 --> ")
    assert blocks[1].splitlines()[1].endswith("--> 00:00:20,000")
    assert blocks[1].splitlines()[2] == "Second one here."


def test_a_long_sentence_splits_evenly_with_no_lonely_word():
    sentence = ("This week we finished the electrical system and installed the solar panels "
                "on the roof of the van.")
    parts = ft._cue_texts(sentence)
    assert " ".join(parts) == sentence
    assert all(len(p) <= ft.MAX_CUE_CHARS for p in parts)
    assert min(len(p) for p in parts) > 30


def test_a_comma_near_the_middle_is_preferred():
    parts = ft._cue_texts("We finished the whole electrical system last week, and the solar "
                          "panels went on the roof on Thursday afternoon.")
    assert parts[0].endswith("last week,")


# --- reading files ---------------------------------------------------------------------------

def test_a_wav_file_is_read_as_16khz_mono(tmp_path):
    path = tmp_path / "talk.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes((np.ones((44100, 2)) * 8000).astype("<i2").tobytes())
    audio = ft.decode(str(path))
    assert audio.dtype == np.float32
    assert len(audio) == pytest.approx(SR, abs=SR // 50)


def test_a_damaged_spot_skips_only_that_bit(monkeypatch):
    """One frame of a recording that won't decode ended the decoding there,
    and the window said Done with the rest of the file missing."""
    import av

    def second():
        frame = av.AudioFrame.from_ndarray((np.ones((1, SR)) * 1000).astype("int16"),
                                           format="s16", layout="mono")
        frame.sample_rate = SR
        return frame

    def damaged():
        raise av.error.InvalidDataError(1094995529, "Invalid data found when processing input")

    class Packet:
        def __init__(self, decode):
            self.decode = decode

    class Container:
        streams = types.SimpleNamespace(audio=[object()])

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def demux(self, stream):
            return iter([Packet(lambda: [second()]), Packet(damaged), Packet(lambda: [second()])])

        def decode(self, audio):         # how PyAV decodes the whole stream in one go
            yield second()
            damaged()
            yield second()
    monkeypatch.setattr(av, "open", lambda path, mode: Container())
    assert len(ft.decode("talk.mp3")) == pytest.approx(2 * SR, abs=SR // 50)


def test_a_file_without_sound_says_so(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("not a recording")
    with pytest.raises(ft.NoSound):
        ft.decode(str(path))


# --- the engines --------------------------------------------------------------------------------

def test_parakeet_text_gets_your_words_spelling():
    class Parakeet:
        def recognize(self, audio):
            return "I like tux talk"
    engine = ft.FileEngine(Parakeet(), "parakeet", "Parakeet", words=["TuxTalk"])
    assert engine.text(_speech(1)) == "I like TuxTalk"


def test_parakeet_is_steered_toward_your_words_before_it_hears_the_file():
    """Dictation hands Parakeet Your Words before each recording, so its
    decoder leans toward them; a file only got the spelling fixed after."""
    steered = []

    class Parakeet:
        def set_words(self, words):
            steered.append(list(words))

        def recognize(self, audio):
            return "I like TuxTalk" if steered else "I like tax talks"
    engine = ft.FileEngine(Parakeet(), "parakeet", "Parakeet", words=["TuxTalk"])
    assert engine.text(_speech(1)) == "I like TuxTalk"
    assert steered == [["TuxTalk"]]


def test_whisper_text_honours_the_language_list():
    class Whisper:
        def __init__(self):
            self.languages = []

        def transcribe(self, audio, language=None, **options):
            self.languages.append(language)
            info = type("Info", (), {"language": language or "fr",
                                     "all_language_probs": [("fr", 0.7), ("es", 0.2)]})()
            seg = type("Seg", (), {"text": " hola "})()
            return iter([seg]), info
    model = Whisper()
    engine = ft.FileEngine(model, "whisper", "Whisper", limit_to=("en", "es"))
    assert engine.text(_speech(1)) == "hola"
    assert model.languages == [None, "es"]


def test_whisper_on_a_file_has_dictations_guards_against_repeating_itself():
    """Whisper gets stuck repeating one phrase most in a file's 30 s pieces,
    and Transcribe a File decoded them without dictation's guards."""
    class Whisper:
        def transcribe(self, audio, language=None, **options):
            self.options = options
            info = type("Info", (), {"language": "en", "all_language_probs": None})()
            return iter([type("Seg", (), {"text": " hello "})()]), info
    model = Whisper()
    ft.FileEngine(model, "whisper", "Whisper").text(_speech(1))
    assert model.options["repetition_penalty"] > 1
    assert model.options["no_repeat_ngram_size"] > 0


def test_a_model_that_is_not_downloaded_says_so(monkeypatch):
    from tuxtalk import model_helper, whisper_vulkan
    monkeypatch.setattr(whisper_vulkan, "is_installed", lambda name: False)
    monkeypatch.setattr(model_helper, "is_model_cached_fast", lambda name: False)
    with pytest.raises(ft.NotDownloaded, match="Whisper Small"):
        ft.load_engine(config.Settings(model="small", device="cpu"))


# --- the menu and the window ------------------------------------------------------------------

def test_the_d_bus_method_opens_the_window():
    from tuxtalk.dbus_service import TuxTalkDBusService
    calls = []
    service = TuxTalkDBusService.__new__(TuxTalkDBusService)
    service.app = object()                  # a tray that isn't quitting
    service._dispatch = lambda name, *args: calls.append(name)
    TuxTalkDBusService.TranscribeFile(service)
    assert calls == ["show_transcribe_file"]


def test_the_window_runs_as_its_own_module(monkeypatch):
    from tuxtalk import service_launcher
    started = []
    monkeypatch.setattr(service_launcher.subprocess, "Popen",
                        lambda cmd, env=None: started.append(cmd))
    service_launcher.launch_transcribe_window()
    assert started[0][1:] == ["-m", "tuxtalk.transcribe_window"]


def test_an_open_window_is_found_by_its_pidfile(tmp_path, monkeypatch):
    from tuxtalk import transcribe_window as tw
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert tw._running_pid() is None
    (tmp_path / "tuxtalk-transcribe.pid").write_text("1")     # pid 1 is not the window
    assert tw._running_pid() is None


@pytest.mark.parametrize("argv,found", [
    (["/usr/bin/python3", "-m", "tuxtalk.transcribe_window"], True),
    # A program that only mentions it (an editor with the file open, say):
    # the signal to come to the front would end it.
    (["/usr/bin/vim", "tuxtalk.transcribe_window"], False),
    (["/usr/bin/python3", "-c", "pass", "tuxtalk.transcribe_window"], False)])
def test_only_the_window_itself_is_taken_for_the_open_window(tmp_path, monkeypatch, argv, found):
    from tuxtalk import service_launcher
    from tuxtalk import transcribe_window as tw
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "tuxtalk-transcribe.pid").write_text("4242")
    monkeypatch.setattr(service_launcher, "read_argv", lambda pid: argv)
    assert tw._running_pid() == (4242 if found else None)


@pytest.fixture
def window_lock(tmp_path, monkeypatch):
    """The window lock in a private folder; returns the module and the
    signals it sent."""
    from tuxtalk import transcribe_window as tw
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    sent = []
    monkeypatch.setattr(tw.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    return tw, sent


def test_a_second_quick_launch_brings_the_first_window_forward(window_lock, monkeypatch):
    """A fast double-click: the first window holds the lock and writes its
    pid a moment later; the second waits for it rather than opening too."""
    from tuxtalk import single_instance
    tw, sent = window_lock
    first = single_instance.open_lock(tw.WINDOW_LOCK)
    assert single_instance.claim(first)
    pids = iter([None, None, 4242])
    monkeypatch.setattr(tw, "_running_pid", lambda: next(pids))

    assert single_instance.claim_window(tw.WINDOW_LOCK, tw._brought_forward, tw.TITLE) == (
        False, None)
    assert sent == [(4242, tw.FRONT)]
    first.close()


def test_the_pid_is_kept_beside_the_lock(window_lock, tmp_path, monkeypatch):
    """Without logind there's no /run/user/N: the lock goes to the private
    fallback folder, and the pid must too, or a second launch never finds
    the open window."""
    from tuxtalk import single_instance
    tw, _sent = window_lock
    fallback = tmp_path / "fallback"
    monkeypatch.setattr(single_instance, "runtime_dir", lambda: str(fallback))
    assert os.path.dirname(tw.pidfile_path()) == str(fallback)


# --- from the code review, 2026-10-15 ---------------------------------------------------------

def test_a_soft_pause_in_steady_noise_still_wins_over_the_window_edges():
    # A music bed or room noise: no true silence, the pause only 0.8x as loud.
    rng = np.random.default_rng(5)
    audio = rng.uniform(-0.3, 0.3, 45 * SR).astype(np.float32)
    audio[int(22.6 * SR):int(23.4 * SR)] *= 0.8
    first_cut = ft.cut_points(audio)[0][1] / SR
    assert 22.5 < first_cut < 23.5


def test_japanese_subtitles_split_by_sentence_and_length():
    text = ("今日は天気がいいので、公園に行きました。そこで友達に会って、一緒にお昼ご飯を食べました。"
            "午後は図書館で本を読んで、夕方に家に帰りました。")
    parts = ft._cue_texts(text)
    assert "".join(parts) == text
    assert len(parts) >= 3 and all(len(p) <= ft.MAX_CUE_CHARS_UNSPACED for p in parts)


def test_a_long_unpunctuated_unspaced_line_is_still_cut():
    parts = ft._cue_texts("あ" * 100)
    assert "".join(parts) == "あ" * 100
    assert all(len(p) <= ft.MAX_CUE_CHARS_UNSPACED for p in parts)


def test_a_quiet_talk_over_room_hum_is_not_skipped():
    rng = np.random.default_rng(6)
    hum = rng.normal(0, 0.01, 30 * SR).astype(np.float32)           # about -40 dBFS
    talk = hum.copy()
    talk[5 * SR:25 * SR] += rng.normal(0, 0.02, 20 * SR).astype(np.float32)  # ~6 dB over it
    assert not ft.is_silent(talk)
    assert ft.is_silent(np.zeros(30 * SR, dtype=np.float32))
    assert ft.is_silent(rng.normal(0, 0.0005, 30 * SR).astype(np.float32))   # recorder hiss


def test_changing_the_model_or_languages_reloads_the_engine():
    base = ft.engine_settings(config.Settings(model="small", device="cpu"))
    assert ft.engine_settings(config.Settings(model="small", device="cpu")) == base
    assert ft.engine_settings(config.Settings(model="base", device="cpu")) != base
    assert ft.engine_settings(config.Settings(model="small", device="cpu", auto_languages="en,es")) != base


# --- Remove ums and repeats ------------------------------------------------------

def test_cleaned_takes_out_ums_and_repeats_and_keeps_the_rest():
    from tuxtalk.file_transcribe import Piece, cleaned
    pieces = [Piece(0, 18, "Okay, this is the cleaver which needs its own maintenance a lot "
                           "more often than your uh single fiber cleaver."),
              Piece(18, 40, "12 times faster than uh than a single. This is this is a blade.")]
    out = cleaned(pieces)
    assert out[0].text == ("Okay, this is the cleaver which needs its own maintenance a lot "
                           "more often than your single fiber cleaver.")
    assert out[1].text == "12 times faster than a single. This is a blade."
    assert [(p.start, p.end) for p in out] == [(0, 18), (18, 40)]      # times kept
    assert pieces[1].text.startswith("12 times faster than uh")          # originals untouched


def test_a_piece_that_was_only_an_um_is_dropped():
    from tuxtalk.file_transcribe import Piece, cleaned
    assert [p.text for p in cleaned([Piece(0, 2, "Um."), Piece(2, 9, "The blade.")])] == ["The blade."]


def test_a_piece_carrying_on_a_sentence_keeps_its_lowercase():
    """Cut mid-sentence: "uh, the blade" carries on, so no capital T."""
    from tuxtalk.file_transcribe import Piece, cleaned
    out = cleaned([Piece(0, 30, "So when you rotate"), Piece(30, 50, "uh, the blade it moves.")])
    assert out[1].text.startswith("the blade")


def test_a_piece_starting_a_sentence_keeps_its_capital():
    from tuxtalk.file_transcribe import Piece, cleaned
    out = cleaned([Piece(0, 30, "That's the pin."), Piece(30, 50, "Uh, the blade moves.")])
    assert out[1].text.startswith("The blade")


@pytest.mark.parametrize("text", ["a " + "b" * 260, "a" * 260 + " b", "我们用 TuxTalk" + "很好用" * 70],
                         ids=["space first", "space last", "chinese"])
def test_subtitles_cut_a_run_on_sentence_with_one_space_instead_of_crashing(text):
    """Save as Subtitles failed with "min() iterable argument is empty" once
    a long sentence's only space was used up."""
    parts = ft._cue_texts(text)
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")
    assert len(parts) > 1
