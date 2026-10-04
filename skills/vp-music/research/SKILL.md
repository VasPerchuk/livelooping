---
name: vp-music-research
description: Research chords and song facts from GP-first evidence without acquiring the original recording, producing an internal cited record and root chords.md. Use streaming services only for necessary identity metadata; ask for missing score assets and pause when GP is sufficient.
---

# Research a song

Read `skills\vp-music\CONTRACT.md`. Confirm artist, title and exact
recording/arrangement. Inventory spelling variants and existing files before
creating `songs\<artist>-<title>`.

Ask once for user-provided GP, GPX, MIDI, PDF, notes, or explicitly supplied
chord/tab text when precise evidence is missing. Do not acquire the original
recording. Use modern/legacy GP first for musical facts, then provided notation,
then MIDI. A transcription proves only its own contents. Pause when GP answers the
musical questions; ask whether to compare lower-tier evidence or proceed to the
analysis approval gate.

For web facts, record URL, claim and confidence. Use streaming catalogs only when
independent sources cannot settle necessary identity metadata. Access/download
permission does not establish a right to reproduce complete lyrics, tabs or scores.
Link to third-party material; copy only short necessary quotations or content the
user supplied/has rights to use.

Validate local/downloaded music with `tools\music_assets.py`. Use
`scripts\download_asset.py` only for an authorized direct HTTPS music-file URL;
never save a landing page as a score, execute downloads, delete a failed user file,
or claim an unsupported file is valid. Log origin, SHA-256, format, parser result,
notes and export eligibility separately. Older GP/GPX/MIDI may support analysis
without supporting modern source-copy export.

Write:

- `.vp-music-tracking\research\research.md` using the report template;
- root `chords.md` with key/tempo, section chart, sources and clearly marked
  uncertainty.

Separate verified fact, transcription-specific evidence, user statement and
hypothesis. Record section/order/bar, instruments/effects, tuning/capo, conflicts,
gaps and progress. Do not read performance defaults or design the user's hardware
setup.

Stop after research. State whether validated evidence supports analysis and offer
up to three numbered next actions. Analysis needs explicit approval.

