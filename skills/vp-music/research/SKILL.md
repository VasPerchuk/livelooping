---
name: vp-music-research
description: >-
  Research a named song for personal live-looping preparation: verify song
  details, chord sources, instruments, effects and useful performance trivia;
  find and validate Guitar Pro/MIDI assets; write a cited research report and
  initial chords.md. Use for the research phase of vp-music-prepare or when the
  user explicitly requests music preparation research, not for final worksheet
  production.
---

# Research a song for looping

Read `skills\vp-music\CONTRACT.md` and the `vp-music-research` role. First confirm
artist, title and recording/arrangement (live versions may have different bars).
Find existing song folders including spelling variants, inventory their source
assets, and reuse their files without overwriting unrelated material. If no
authoritative assets are present, ask once for user uploads (notes, GP, PDF,
explicit chord/tab text, MIDI). Wait for offered uploads; if none are possible,
create/reuse `songs\<artist_slug>-<song_slug>` and proceed with explicit gaps.
Select the tracking or legacy artifact paths according to the contract.

Verify identity and performance-relevant context against independent web sources.
For musical details (chords, BPM/beat unit, key, meter, sections/bars, tracks,
tuning, capo), inspect validated GP first; next use provided PDFs, explicit
chord/tab text, then MIDI, only for gaps or an approved comparison. A high
rating alone does not establish correctness. Distinguish reported fact, score-
specific transcription and hypothesis; record URLs beside web claims and local
file/measure evidence beside score claims. If GP already answers all required
musical questions, **pause** after web context verification and ask whether to
inspect the next tier for corroboration or proceed to the normal analysis
approval gate. This source decision never authorizes analysis by itself.
When consulting PDFs use existing PDF capability only; if notation cannot be
read reliably, mark it unverified rather than inventing notes.

Search for a missing usable score from authorized sources whose access
and download terms permit the intended use; do not fetch lower-tier assets merely
to populate every format. Keep already present files. For each
direct download, use `python skills\vp-music\scripts\download_asset.py --url
<https-url> --song-dir <song-folder> --name <new-filename>`: it bounds streamed
bytes, checks redirects, stages in the song folder, invokes
`tools\music_assets.py validate` and accepts only analyzed music with notes.
If the site supplies a landing page rather than a direct URL, inspect the
page for a real download URL instead of saving it as `.mid`. Reject HTML,
malformed scores, unsafe ZIP members and unsupported formats; never execute
downloaded content. Validate local files for analysis too, but do not delete
an existing user file on failure.
Log the asset origin, SHA-256, validator result and whether it has playable note
content. If the helper is unavailable, explain the blocker and leave downloaded
assets unaccepted rather than claim they are validated.

Create `.vp-music-tracking\research\research.md` (or legacy root `research.md`)
following `research\references\report-template.md` with
`## Song identity`, `## Verified musical facts`,
`## Arrangement and track evidence`, `## Effects and useful trivia`,
`## Assets and validation`, `## Conflicts and gaps`, and `## Preparation progress`.
List evidence/URL/confidence for each meaningful claim and indicate which tracks
and sections still need investigation. Create initial `chords.md` with key/tempo,
section-wise chord progression and source URLs. Ask user before copying an entire website's
lyrics or tab into it; otherwise use full lyrics if supplied by the user, otherwise link the source. Do not assume a repeating guitar riff is a
bass guitar merely because the MIDI program says "bass", research might show that it is played on a different instrument.

Stop at research completion, state whether analysis is supported by validated
score material, and show up to three numbered next actions, including analysis
only with explicit approval. If GP was sufficient, let the user choose lower-
tier comparison or analysis approval explicitly; the research progress record
must reflect the choice. If a site cannot be accessed, state this rather than
inventing chords or an asset.
