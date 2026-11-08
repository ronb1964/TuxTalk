# Publishing TuxTalk

Moved out of CLAUDE.md: read this before uploading Parakeet or Vulkan engine files,
telling people about a release, or posting anywhere public.

## Step 12: Tell People (every release, the same day)

Ron, 2026-11-01: marketing stays in this project, with steps so nothing is
missed (Mastodon went quiet from 0.18 to 0.19 because no step named it).
Draft every text in Ron's voice (ron-voice skill), saying how TuxTalk is
made where "SAYING HOW TUXTALK IS MADE" (below) calls for it, show Ron all of them
together, and post each only after his OK. Every place we've ever posted is
in Obsidian "TalkType/Where TuxTalk is posted": start from its "Owed right
now" list, and update it with what went where (and the per-release log,
"TalkType/<version> release outreach").

1. **GitHub:** the release notes with the AUR line, and the Discussions →
   Announcements post (Step 11).
2. **AUR:** bump `tuxtalk-appimage` and `tuxtalk` (`aur/`, memory "Publishing to
   the AUR"). tuxtalk-appimage publishes from aur-repo-tuxtalk/ and tuxtalk
   from aur-publish/tuxtalk/ (both gitignored AUR clones). aur-repo/ is the
   old talktype-appimage clone and is not used. `python-onnx-asr` is bumped
   in the same change as any poetry.lock onnx-asr change (its test fails
   until then).
   After the tag, before pushing `tuxtalk`: its real `sha256sums` from the
   tagged archive (`makepkg -g` in the CachyOS VM from snapshot
   tuxtalk-aur-deps, its line over the SKIP: makepkg with SKIP computes none;
   then one `makepkg` and `makepkg --printsrcinfo > .SRCINFO`), committed.
3. **Mastodon** (@ronb1964 on mastodon.social, in the debug Chrome): one
   post, under 500 characters: what changed for the user, a link,
   #Linux #FOSS, and #Accessibility when it helps.
4. **The people waiting:** a short reply on every issue, discussion and
   forum thread whose fix or feature shipped (close the issue), naming what
   to do: the Mint forum thread (t=475225), the Zorin forum guide
   (forum.zorin.com/t/69971), Ron's KDE Discuss topic (t/50484) and the
   Fedora Discussion thread he answered in (t/203062). Never a reply only
   to bump a thread.
5. **The website and README:** the README's "New in X.Y" (Step 11), and a
   new feature added to tuxtalk.app where it belongs (the keys section, a
   feature section or "And a bunch of smaller stuff").
6. **Listings:** AlternativeTo (is the description still true?); AppImageHub
   updates itself from the release.
7. **Only for a release with a headline feature, if Ron wants:** one new
   place from the outreach list in Obsidian (EndeavourOS or openSUSE forums,
   accessibility communities, a Phoronix tip, a YouTuber who replied).

Not Reddit or Lemmy (posts removed, account closed), nor anywhere AI-written
code is banned (see below). Between releases: the weekly poll tally while a
poll runs, and answers to new issues, discussions and forum replies.

---

## PARAKEET ULTRA — HOSTED AS ITS OWN PRE-RELEASE

Parakeet's files (`src/tuxtalk/parakeet_ultra.py`) come from the GitHub
pre-release `parakeet-ultra-1`: the processor's int8 ONNX files (Olicorne's
export, unchanged) and the graphics card's q8_0 file (converted with
whisper.cpp's own script). Released versions check every file's SHA256, so a
published file is never replaced: a new conversion gets a new tag and new
hashes in `parakeet_ultra.py`. Upload with `gh release create ... --prerelease`
(a normal release would become "latest" for the updater), and only with Ron's OK:
it is public. How the files were made and checked: the release's README and
Obsidian "TalkType/2026-10-19 - Parakeet-ultra bake-off".

---

## VULKAN GRAPHICS ENGINE — BUILT AND HOSTED SEPARATELY

The "Vulkan (any GPU)" device (config value `vulkan`; AMD, Intel and NVIDIA)
runs whisper.cpp's whisper-server with its Vulkan backend (`src/tuxtalk/whisper_vulkan.py`). whisper.cpp publishes no
Linux Vulkan build, so TuxTalk builds one. It is NOT part of the AppImage
and is NOT rebuilt for each release. It only changes when whisper.cpp does,
or when its contents change (a new build number for the same version).

To update it:
1. `./build-vulkan-engine.sh <whisper.cpp version> <build number>`, e.g.
   `./build-vulkan-engine.sh 1.9.4 3`. Both are required. The build number
   must be higher than `ENGINE_BUILD` for the same version: a published
   build is never replaced (released versions check its SHA256), and the
   script refuses one that is. It builds in Ubuntu 22.04 and writes
   `talktype-whisper-vulkan-<ver>-b<build>-x64.tar.gz` + `.sha256`.
2. Upload it to a GitHub release tagged `whisper-vulkan-<ver>-b<build>`, marked
   **pre-release** (`gh release create ... --prerelease`). A normal release
   would become "latest", and the in-app updater and the AppImage's update
   information would treat it as a TuxTalk version.
3. Put the version, build number and SHA256 into `ENGINE_VERSION`,
   `ENGINE_BUILD` and `ENGINE_SHA256` in `whisper_vulkan.py` (and
   `ENGINE_SIZE_TEXT` if the download size changed).
4. Ask Ron before uploading. It is public.

The setup dialogs (download, speed check, Light/Full choice) live in
`vulkan_setup_dialogs.py`, shared by Preferences and the tray's presets.

---

## SAYING HOW TUXTALK IS MADE — WHERE WE INTRODUCE IT

Ron can't code; Claude writes TuxTalk and Ron designs, tests and uses it
daily. A post that introduces TuxTalk somewhere (a new forum thread, a pitch,
a listing, a post to people who may not know it yet) says so up front, in
plain words, never buried and never left for someone to discover in the
commit history (the Lemmy thread on 2026-10-16 went bad for exactly that); so
does any post where it really matters to the reader. Each release's texts
(the GitHub release notes, the Discussions announcement, the Mastodon post)
carry one short line near the end, since new people find TuxTalk there.
Replies in a conversation don't repeat it: in every reply it gets repetitive
and reads as AI (Ron, 2026-11-04). When it is said, the wording changes from
post to post.
Pair it with the proof that a person is responsible: thousands of automated
tests and CI, testing on several distros in VMs, the hotkey helper's review by
an outside developer (guelz), guelz's PR merged, Ron dictating nearly everything he writes with it.
**Always** say, too, that the code is held to the standard a human developer
would respect: readable, tested, no leftovers, not an AI mess (Ron, 2026-10-20:
"I'd like you to always remind or say that"). Link the About page's "Keeping
the code clean" section (tuxtalk.app/about.html) where a link fits.
Never apologise for it and never call it "vibe coded". Draft in Ron's voice
(ron-voice skill). Don't post where AI-written code is banned (Flathub,
extensions.gnome.org, GNOME Circle, Gentoo).
