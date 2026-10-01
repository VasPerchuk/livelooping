---
name: vp-music-arrangement
description: Generate a continuous Final or explicitly approved Partial Guitar Pro instrumental reduction from finalized performance events and a matching published live-loop worksheet. Use during every approved finalization; report representability, rights or no-clobber blockers explicitly.
---

# Export the finalized live-loop reduction

Read `skills\vp-music\CONTRACT.md`,
`analyze\references\arrangement-handoff.md`, the active research record,
active instrument pattern files,
`.vp-music-tracking\arrangement\performance-arrangement.json` and the published
root `worksheet.md`. This skill is invoked by `vp-music-finalize` after both
questionnaires and worksheet validation; it does not authorize another phase.

Confirm each structured pattern name belongs to an active pattern file and the
worksheet legend; ordered section names, bar counts and active patterns must
match. Confirm tempo, meter, source provenance and string/tuning assumptions.
Do not use abbreviated ASCII grids as complete music, invent guitar frets from
MIDI, name unknown drums, or copy a complete third-party score without rights.
Do not include exact personal hardware/software in the manifest or score.

The current writer requires constant quarter-note BPM and meter, integer
bar-aligned cycles unless truncation was approved, and the documented simple
event model. Distinguish rest-only breaks from live but unscored performance.
Vocals, free solos, controller changes and routing are not GP note events. A
Partial can omit disclosed parts or use live placeholders, but cannot bypass
timing/event representability, fabricate notes or hide coverage gaps.

For a Final export, `publication.status` is `final`, limitations are empty and
the matching worksheet has `**Status: Final**`. For an explicitly approved
Partial export, status is `partial`, limitations and consent are nonempty, and
the matching worksheet has `**Status: Partial**` with the same limitations.
The writer embeds the quality and limitations into supported score metadata and
checks that they survive export.

Run:

```text
node skills\vp-music\tools\write_arrangement.mjs --manifest <song-folder>\.vp-music-tracking\arrangement\performance-arrangement.json --song-dir <song-folder> --output <song-folder>\arrangement.gp
```

Never overwrite an existing `arrangement.gp`, supplied score or hand-edited
manifest. The writer must fail on malformed/unrepresentable events, export a
genuine modern `.gp`, validate it, and compare status metadata, bars, tracks,
tuning, tempo and note events against the input before publishing. Structural
validation does not certify fidelity to the target recording. Report the
created path/status/limitations or the exact error and that no new GP was
published.
