---
name: vp-music-worksheet
description: Draft or publish a concise instrumental live-loop worksheet from analyzed song sections and repeatable patterns, showing bars, seconds, active loops and actionable transitions without tabs or chords. Use when asked for a song's loop structure map.
---

# Performance worksheet

Read `skills\vp-music\CONTRACT.md`. Do not hand-author authoritative worksheets.

- Analysis Draft:

  ```text
  python skills\vp-music\tools\render_views.py --manifest <arrangement.json> --song-dir <song>
  ```

- Published Final/Partial:

  ```text
  python skills\vp-music\tools\render_views.py --plan <performance-arrangement.json> --song-dir <song> --title "Artist - Song"
  python skills\vp-music\scripts\check_worksheet.py --song-dir <song> --plan <performance-arrangement.json>
  ```

The compiled plan owns section order, retained/deleted instances, output bars,
times, placements, omissions, limitations, consent and transitions. Check every
section locally; an action or track listed elsewhere cannot satisfy it.

Keep the published worksheet glanceable:

- status, tempo, meter, total bars/time;
- generic instruments and stable output IDs;
- each retained section's output range, duration, active loops/live cues, rests;
- one confirmed action into each retained section;
- omissions/edits and Partial limitations.

Do not include chords, full tabs, vocal/solo transcription, exact personal
hardware, evidence logs or invented synchronization. Pattern grids live in
generated pattern files.

For an infeasible action, render `INFEASIBLE` in the section and a prominent
header warning explaining the constraint. A warned Final may remain valid when
the music is reliable; incomplete/estimated music needs consented Partial status.
Never publish Draft or unresolved review prose as Final.
