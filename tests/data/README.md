# Test recordings

## jfk-ask.wav and jfk-not.wav

Two quick words, "ask" and "not", said by a person, used by
`tests/test_silence.py` as the words hands-free hears over a dryer and a fan.

- Source: `samples/jfk.wav` from whisper.cpp
  (https://github.com/ggml-org/whisper.cpp), unchanged since the commit that
  added it, b0a11594aec50892a02cd8d129eee2dfe93a8bb8. Its SHA256 is
  `59dfb9a4acb36fe2a2affc14bacbee2920ff435cb13cc314a08c13f66ba7860e`.
- Cut points, in samples of the 16 kHz original: `jfk-ask.wav` is samples
  51840 to 60160 (3.24 s to 3.76 s), `jfk-not.wav` samples 63200 to 69760
  (3.95 s to 4.36 s). Both are 16 kHz mono 16-bit PCM, the samples copied
  as they are.
- Rights: the recording is of John F. Kennedy's inaugural address of
  20 January 1961, a work of the United States government made by the
  President in his official duties, so it is in the public domain in the
  United States (17 U.S.C. 105).

## librispeech-7902-96591-0018.wav

A sentence read by a person, "Yes, if other ways failed he could escape up
the chimney", 3.11 s, used by `tests/test_voice_detector.py` and as the
speech of the Type at pause and hands-free tests (its first word, "Yes", is
their quick word). The JFK words are an old broadcast with a crowd and
reverberation in it, which the detector is unsure of even at full level and
loses at levels a microphone still gives clearly; this is a voice as a
microphone hears it.

- Source: LibriSpeech, test-other, utterance 7902-96591-0018 (speaker 7902,
  chapter 96591), from https://www.openslr.org/12 as converted to Parquet in
  openslr/librispeech_asr on Hugging Face. 16 kHz mono 16-bit PCM, the
  samples unchanged.
- Rights: LibriSpeech is licensed CC BY 4.0, by Vassil Panayotov, Guoguo
  Chen, Daniel Povey and Sanjeev Khudanpur ("LibriSpeech: an ASR corpus
  based on public domain audio books", ICASSP 2015); the audio is a
  LibriVox recording of a public-domain book.
