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
Reuse the song's existing folder and assets without overwriting unrelated files.

Search independently for chord progression, BPM/beat unit, key, meter, section order
and bars, track lineup, tuning, capo, characteristic effects and production facts
that change a live arrangement. Rank transcription evidence by identifiable artist/
recording, specific track/measure detail, community corrections and corroboration;
a high rating alone does not establish correctness. Cross-check likely differences
against existing GP/MIDI metadata and another source where available. Distinguish
reported fact, arrangement-specific transcription and hypothesis. Record URLs beside
claims, not a disconnected list of links.

Search for both usable Guitar Pro and MIDI from authorized sources whose access
and download terms permit the intended use. Keep already present files. For each
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

Create `research.md` following `research\references\report-template.md` with
`## Song identity`, `## Verified musical facts`,
`## Arrangement and track evidence`, `## Effects and useful trivia`,
`## Assets and validation`, `## Conflicts and gaps`, and `## Preparation progress`.
List evidence/URL/confidence for each meaningful claim and indicate which tracks
and sections still need investigation. Create initial `chords.md` with key/tempo,
section-wise chord progression and source URLs. Do not copy an entire website's
lyrics or tab into it; only use full lyrics if supplied by the user or appropriately
licensed, otherwise link the source. Do not assume a repeating guitar riff is a
bass guitar merely because the MIDI program says "bass."

Stop at research completion, state whether analysis is supported by validated
score material, and show up to three numbered next actions, including analysis
only with explicit approval. If a site cannot be accessed, state this rather than
inventing chords or an asset.
