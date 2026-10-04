---
name: vp-music-analyze
description: Analyze validated Guitar Pro or MIDI into hardware-neutral reusable patterns, reduced-loop note events and a draft worksheet with explicit gaps. Use only for the explicitly authorized analysis phase.
---

# Analyze loopable material

Read `skills\vp-music\CONTRACT.md`, the tracking research report, `chords.md`, and
`analyze\references\arrangement-handoff.md`. Do not read performance defaults.

1. Inspect the validated source:

   ```text
   python skills\vp-music\tools\music_assets.py inspect --path <asset>
   ```

   Parsing confidence is not instrument identity or recording fidelity. Preserve
   unknown track/channel/pitch labels rather than inventing instruments, frets,
   percussion names, techniques, tempo, or section names.

2. Establish performed order from repeats, alternate endings and directions.
   Preserve pickups, meter changes and legitimate irregular sections.

3. Review candidate suggestions:

   ```text
   python skills\vp-music\scripts\pattern_candidates.py --path <asset>
   ```

   Prefer coherent 4/8-bar phrases and normally at most three core loops per stable
   source track. Short, irregular or additional patterns need concrete musical
   evidence. Role is a prior, never a blanket exclusion. Keep fills/solos LIVE;
   repeated silence is not a loop.

4. Author version 2
   `.vp-music-tracking\arrangement\arrangement.json`. Use stable track IDs, exact
   source refs, closed dispositions, explicit decision graphs, ordered sections and
   `performed_written_bars` when performed order is non-linear.
   `active_pattern_ids` means simultaneous for the whole block; split ordered
   body/endings and sequential changes.

5. Validate source capabilities:

   ```text
   node skills\vp-music\tools\analysis_preflight.mjs --manifest <arrangement.json> --song-dir <song>
   ```

   Hard source errors cannot become cues or be waived by selection prose. Musical
   review findings remain separate. A blocked report blocks the handoff.

6. Generate the pattern aids and Draft worksheet:

   ```text
   python skills\vp-music\tools\render_views.py --manifest <arrangement.json> --song-dir <song> --dry-run
   python skills\vp-music\tools\render_views.py --manifest <arrangement.json> --song-dir <song>
   ```

   Inspect the generated playable string/fret, pitch, or percussion grids. Visible
   grid gaps are preferable to fabricated notation. Confirm the Draft structure,
   durations, rests/live passages, and exact active patterns.

Summarize supported facts, review decisions and source/export gaps. Offer up to
three numbered next actions and stop. Finalization requires explicit approval.

