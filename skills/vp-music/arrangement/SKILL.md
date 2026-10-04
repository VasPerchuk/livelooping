---
name: vp-music-arrangement
description: Create a continuous Final or explicitly approved Partial instrumental arrangement.gp from finalized performance events and a matching worksheet. Invoked by approved song finalization; preserve structural validation and report hard blockers.
---

# Export the compiled performance plan

Read `skills\vp-music\CONTRACT.md` and the compiled `plan_version: 2`
`performance-arrangement.json`. Normal publication is owned by
`tools\finalize_build.py`; use the writer directly only for diagnosis:

```text
node skills\vp-music\tools\write_arrangement.mjs \
  --performance <performance-arrangement.json> --song-dir <song> \
  --output <song>\arrangement.gp --worksheet <song>\worksheet.md
```

The plan, not ASCII aids or display names, owns material and track identity.
Preserve source tuning/capo/playback/percussion facts where supported. Do not
invent notes, frets, drums, effects, clocks, or personal hardware.

Materialize retained placements, prune note-empty physical tracks, order the
survivors according to the current export layout, enforce the 32-notated-track
guard, and keep cue-only requirements in the worksheet. Reject blank exports,
stale fingerprints, unsupported retained notation, placement conflicts, source
drift and round-trip mismatches. A Partial label cannot bypass these errors.

Source-copy export supports only the modern GPIF slice described in
`analyze\references\arrangement-handoff.md`. Older GP/GPX/MIDI remain valid
analysis inputs but require supported source material or disclosed cues for export.
Never overwrite an existing output without the coordinated build's explicit
replacement/recovery path.
