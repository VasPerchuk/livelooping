---
name: vp-music-analyze
description: Analyze validated Guitar Pro or MIDI and a cited song research report to find repeatable instrumental live-loop patterns, write per-instrument ASCII tab pattern files and a draft section-by-section worksheet. Use for the analysis phase of vp-music-prepare or when explicitly asked to analyze a song for loops; not for vocals or non-repeating solos.
---

# Analyze loopable material

Read `skills\vp-music\CONTRACT.md`, the layout's research file
(`.vp-music-tracking\research\research.md` or legacy root `research.md`),
and the analyzer role. Require
a validated score or user-provided playable evidence for precise note-level tabs;
where absent, make a partial arrangement map and request sources instead of
fabricating parts. The technical helper's `inspect` output is a starting point,
not a proof that an uploaded transcription matches the recording.
Do not read `.vp-music-tracking\env-defaults.md`. Analyze musical evidence
independently of player count, loop capacity, controller layout, routing,
synchronization or performance software.

1. Inspect GP/MIDI tracks and explicit tempo, signature, pickup, repeats and
   marker events with `skills\vp-music\tools\music_assets.py inspect --path <asset>`.
   GP string/fret notation is suitable for ASCII guitar/bass tab once tuning/capo
   and the parser's per-track string numbering are accounted for (legacy GP
   numbers high string first; GPX/modern GP inspection numbers low string first).
   MIDI has pitches, not strings/frets or accurate guitar
   techniques; display pitch-labeled ASCII lanes for MIDI-only guitar, synth and
   piano instead of inventing fret choices. Drum lanes should be labeled kick,
   snare, hat etc. only when mapping is evidenced; otherwise show MIDI pitches.
   Use `analyze\references\midi-pitches.md` for numeric pitch/octave names
   and the conditional GM channel-10 percussion map.
   For unnamed or generic MIDI tracks, inspect each note's `channel` (zero-based:
   9 is channel 10), `pitch`, `start_quarters`, `duration_quarters` and `bar`,
   along with the track `name`. Group notes by channel if a track mixes channels.
   Compare pitch range, monophonic vs. simultaneous notes, register, rhythmic
   placement/repetition and researched arrangement; a low monophonic line may
   suggest bass, chords may suggest keys/guitar, and channel-10 GM keys may
   suggest drums, but none alone identifies the instrument. A specific program
   (patch) or kit is a useful clue **only when independently available** (e.g.
   from the source project or DAW): `inspect` does not report MIDI program
   changes, so do not infer a patch from its JSON. Treat generic `Track N`
   names as placeholders, not instrument evidence.
   Record proposed identity, supporting clues, alternatives and confidence
   (confirmed from corroborated sources, likely from converging clues, or
   uncertain). Keep uncertain lines labeled by track/channel/pitch rather than
   assigning an instrument or GM drum sound; ask for a verified score, kit map
   or recording when precise attribution matters. The inspector's top-level
   `confidence` describes parsing completeness, **not** instrument identity.
2. Establish **performed** section order separately from source measure order:
   unfold repeat signs/alternate endings and check against researched intro,
   verse, pre-chorus, chorus, bridge, solo and outro. A track's length does not
   establish its song section names. Preserve legitimate odd-meter/irregular
   sections; do not round bar counts to 4/8/16/32 without evidence.
3. Compare measures on the same instrumental part for recurring rhythm, pitch
   contour and harmonic compatibility. A loop repeats cleanly at its boundary
   and works across the stated sections; a fill, a single solo take and vocals
   do not qualify. Prefer one reusable core pattern to separate near-duplicates;
   keep normally <=3 patterns per instrument. Keep logical musical patterns
   separate from later physical loop/control allocation. Use
   `python skills\vp-music\scripts\pattern_candidates.py --path <asset>` for
   exact-repeat candidates; these are hints only, since repeated MIDI notes
   do not prove a musically good looping boundary or section name. Review
   candidate bars against harmony and available evidence.
4. For new preparations, write
   `.vp-music-tracking\pattern\<instrument>.patterns.md` for each repeatable
   instrument; for a legacy root-level workflow, update root
   `<instrument>.patterns.md` in place. Use
   `analyze\references\pattern-template.md` as a guide. For each
   named `## Pattern`, include source file/track/measures, length in bars, meter,
   tuning/capo or lane key, an **original, short** guitar-style monospaced ASCII
   tab/grid, entry/loop-end cue, source confidence and matching song sections.
   Show subdivisions/barlines and rests, not the source's entire tab. Example
   of a *format*, not a song transcription:

   ```text
   Count |1 & 2 & 3 & 4 & |1 & 2 & 3 & 4 & |
   Kick  |x---x---x---x---|x---x---x---x---|
   Snare |----x-------x---|----x-------x---|
   ```

   Use readable pitch-lane ASCII grids for non-fretted instruments, never
   plausible-looking guitar fingerings without string/fret evidence.
   In addition to the short grids, author the **complete original reduced
   loop events** needed for a future scored arrangement in the versioned
   handoff described by `analyze\references\arrangement-handoff.md`.
   Use `.vp-music-tracking\arrangement\arrangement.json` for both layouts;
   its creation does not migrate a legacy song's research/pattern locations.
   Events need explicit onsets/durations and supported string/fret, pitch or
   percussion evidence. Do not infer a full score from the ASCII summary or
   copy an entire third-party tab into the new file. Record gaps instead of
   creating placeholder notes; an incomplete arrangement handoff is allowed
   while the worksheet can still be useful.
5. For new preparations, write
   `.vp-music-tracking\draft\worksheet-draft.md` marked `**Status: Draft**`
   using the worksheet skill; reserve root `worksheet.md` for finalization.
   For a legacy root-level workflow, update root `worksheet.md` as Draft
   in place. If both layouts coexist, ask which handoff is current before
   writing either. Do not create a second active draft.
   Every song section has its actual/estimated bar count, derived duration and
   precise active names from the pattern files; `none` is valid when no loop
   sounds in a break or solo. Note performance-only sections without analyzing
   their note sequence. Draft transitions as generic musical actions when
   possible; defer concrete control feasibility and performer allocation to
   finalization. Match section names, bar counts and active pattern names
   in the scored handoff to this draft. For sections without loops, distinguish
   live/unscored performance from intentional rest; for incomplete cycles,
   record the intended tail explicitly. Describe missing instrument/section
   data and GP-export blockers without delaying the analysis approval gate.

Offer one to three explicitly numbered (`1.`, `2.`, `3.`) next actions and stop.
Actionable missing sources, ambiguous tails, attribution conflicts and export
limitations may be handed to finalization for its questionnaire; do not bind
them to a performance environment here. If there is no usable musical material
at all, recommend more evidence or an explicitly original design rather than
an unjustified worksheet. Finalization requires explicit user approval;
producing a draft is not approval to advance.
