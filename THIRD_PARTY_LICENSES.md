# Third-party software in TuxTalk

TuxTalk itself is MIT-licensed (see [LICENSE](LICENSE)). The AppImage, .deb and
.rpm also contain other people's open-source software, and each keeps its own
license. All of their license texts are installed with TuxTalk:

- AppImage: `usr/share/licenses/` inside it
- .deb and .rpm: `/opt/talktype/usr/share/licenses/` (the folder kept its old name)
- AUR: inside the AppImage at `/opt/tuxtalk/TuxTalk.AppImage`

## Programs built from source

| Program | Version | License | Source code |
|---|---|---|---|
| wl-clipboard | `v2.3.0` | GPL-3.0-or-later | https://github.com/bugaevc/wl-clipboard |

It is built unmodified from the source at the version above, and TuxTalk runs
it as a separate program to handle the clipboard for paste mode. The complete
source for that exact version is at the address above, and a backup copy is
kept at https://github.com/ronb1964/wl-clipboard. If it ever goes missing from
both, open an issue at https://github.com/ronb1964/TuxTalk/issues and I'll
provide a copy, for at least three years after the last TuxTalk release that
includes it.

TuxTalk 0.17.0 and earlier also included **ydotool** (AGPL-3.0-or-later),
built unmodified from commit `708e96ff27e381a8c549418a9d34cdde12305317` of
https://github.com/ReimuNotMoe/ydotool, to type the dictated text. TuxTalk now
types with its own virtual keyboard. The same promise holds for that ydotool:
its source is at that address and at https://github.com/ronb1964/ydotool, and
I'll provide a copy on request until three years after 0.17.0.

## Python and Python packages

- **Python 3.10** (PSF License), from Ubuntu 22.04.
- **Python packages** (faster-whisper, CTranslate2, onnxruntime, onnx-asr,
  text2num, NumPy, sounddevice, Hugging Face Hub, tokenizers, PyAV): each
  package's own license is in its `*.dist-info` folder under
  `usr/lib/python3.10/site-packages/`. CTranslate2 (MIT) and tokenizers
  (Apache-2.0) don't include theirs, so they're added under
  `usr/share/licenses/`.
- **PyGObject, pycairo and dbus-python**, from Ubuntu 22.04.

## FFmpeg and the libraries PyAV brings

Transcribe a File reads audio and video with PyAV 16.0.1, whose package
includes 39 libraries of its own: FFmpeg 8.0, x264 and x265, the AV1, VP8/9,
WebP, Opus, Vorbis, Speex, LAME, TwoLAME, AMR and H.264 codecs, GnuTLS,
Nettle, GMP, libunistring, alsa-lib, libdrm, libxcb, libXau and glibc's
libmvec. They come unmodified from PyAV's own build (pyav-ffmpeg release 8.0-2).
FFmpeg is built under the LGPL-3.0-or-later, and with x264 and x265
(GPL-2.0-or-later) the combination is under the GPL-3.0-or-later; the others
are under the LGPL, BSD, MIT and Apache licenses. CTranslate2 also brings
GCC's libgomp (GPL-3.0-or-later with the GCC Runtime Library Exception).

FFmpeg includes three files from libjpeg, which FFmpeg has changed; their
notices are in `ffmpeg-libjpeg-NOTICES`. This software is based in part on
the work of the Independent JPEG Group.

Every one of their license files is installed under `usr/share/licenses/pyav/`,
and `SOURCES.md` there names each library's exact version and where its
complete source is. If a source ever goes missing from those addresses, open
an issue at https://github.com/ronb1964/TuxTalk/issues and I'll provide a
copy, for at least three years after the last TuxTalk release that includes
it.

## Fonts

- **Noto Color Emoji** (SIL Open Font License 1.1), from
  https://github.com/googlefonts/noto-emoji at commit
  `1ffdd21391dd1f25c081fa93a9dea0c7c029442b`. TuxTalk carries only the emoji
  its own windows show, cut out of it unchanged otherwise
  (`packaging/build-emoji-font.py`), so they show on computers without an
  emoji font. The license is next to it, `tuxtalk/fonts/LICENSE`.

## Models

- **Silero VAD** v6.2.3 (MIT), from https://github.com/snakers4/silero-vad
  at commit `5cd7945676eb32225748052e2e6a0580e4686a08`: its
  `silero_vad_16k_op15.onnx`, unchanged, as `tuxtalk/models/silero_vad.onnx`.
  It tells speech from other sound for hands-free, Type at pause and the
  checks of whether a word was said. The license is next to it,
  `tuxtalk/models/LICENSE`.

## Libraries from Ubuntu 22.04

These are copied unmodified from Ubuntu 22.04: the tray icon libraries
(libayatana-appindicator3, libayatana-indicator3, libayatana-ido3,
libdbusmenu), GObject Introspection and its typelibs, libffi, PortAudio, the
JACK client library, Berkeley DB 5.3 and libpython. Mostly LGPL, MIT or
similar; each package's own copyright file is included.

The exact package versions are listed in
`usr/share/licenses/tuxtalk/bundled-ubuntu-packages.txt`. Their source is in
Ubuntu's archive: `apt-get source <package>` on Ubuntu 22.04, or
https://launchpad.net/ubuntu/jammy.
