---
name: vp-music-finalize
description: Complete a livelooping song's sourced chords.md with guitar chord fingerings and permitted lyrics, and turn an analyzed draft worksheet into clear performance guidance. Use only after an approved analysis-to-finalization handoff or an explicit user request to finalize prepared song files.
---

# Finalize song preparation

Read `skills\vp-music\CONTRACT.md`, the active research record, root `chords.md`,
every active pattern file, and the internal `draft\worksheet-draft.md` (legacy:
root Draft `worksheet.md`), then the finalizer role and worksheet skill.
Confirm that the report identifies the song version, all worksheet names
resolve to patterns, and all bars/times match the music source. Do not silently
change a chord chart because a MIDI harmonization differs; document the conflict
and ask which arrangement the player intends.

In `chords.md`, list recommended playable guitar shapes before the section chart
(for example `Am (x02210)`) and state tuning, capo and any alternative voicings.
Keep the section chart with chord timing/bar grouping as far as sources support.
If user-provided or appropriately licensed lyrics are available, align chords
above the permitted lyric text. Otherwise link the full lyrics/chord reference,
and provide short section or lyric-entry cues only where permitted; do not
reproduce web lyrics or a complete tab merely because the worksheet is personal.
Distinguish fingering recommendations from verified original-performance shapes.

Publish `worksheet.md` in the song root, leaving the tracking draft as an
internal record. Keep only the essential identity/tempo/meter, minimal
instrument/effect notes if useful, pattern legend with count, and every
performed section's bars, approximate seconds, active patterns and one short
`Transition:` line specifying the human action to reach the next block.
Keep detailed assumptions, evidence and alternative arrangements in the draft.
No chord names, note names, full tabs, vocal patterns or one-off solo
transcription. Verify that each transition is physically executable for one
performer, including scene changes versus separate looper-channel changes;
if not, label the section `INFEASIBLE` and add a conspicuous
`**Transition warning:**` with the unresolved constraint. Final is permitted
with that warning, but do not describe it as fully executable. Suggest Maschine Group/Pattern/Scene
mapping and RC-300 track/overdub roles only if useful and physically feasible.
Do not assert RC-300 will follow Maschine's clock; ask first if sync matters.
Refer to the actual original Ditto as an optional manually timed auxiliary loop.
Run `python skills\vp-music\scripts\check_worksheet.py --song-dir <folder> --require-final`
after writing the final worksheet; fix discrepancies before
calling it complete. For changing tempos or meters, verify event-map timings
manually and explain the timing assumption (the checker handles constant
tempo/meter only).

Leave the working worksheet **Draft** if an essential section, chord source,
duration or pattern relationship cannot be justified. Otherwise publish
`**Status: Final**` (even with an explicit unresolved transition warning),
summarize remaining optional uncertainty and recommend up to three rehearsal
or correction actions. Do not overwrite changes the user made since analysis
without checking with them.
