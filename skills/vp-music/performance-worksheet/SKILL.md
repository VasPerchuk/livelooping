---
name: vp-music-worksheet
description: Draft or finalize an easy-to-scan live-looping performance worksheet from verified song research and repeatable instrument patterns. Use when vp-music-analyze or vp-music-finalize needs worksheet.md, or a user asks specifically for a loop structure map with section bars, approximate durations, active patterns and executable human transitions. Keep chords, note-level tabs and vocal lines in separate files.
---

# Performance worksheet

Read `skills\vp-music\CONTRACT.md` before writing. Use the active research record
and pattern files as evidence, not guesses. Analysis writes
`.vp-music-tracking\draft\worksheet-draft.md`, never the published root
`worksheet.md`. Finalization distills that draft into root `worksheet.md`.
For a legacy preparation, keep the existing root Draft/Final location instead.
Keep source justification, unresolved assumptions, and detailed alternative
transition plans in the draft; the published Final or approved Partial is a
glanceable action map.
Exact personal hardware/software names never appear in either worksheet.
Finalization translates confirmed capabilities into generic performer actions.

```markdown
# Artist - Song

**Status: Final**
**Version:** [recording / arrangement]
**Tempo:** quarter = 112 BPM  **Meter:** 4/4
**Key:** [if useful; disputed or unknown if applicable]

## Patterns (2 total)
- **Drums A** (2 bars) - [source pattern filename]
- **Bass A** (4 bars) - [source pattern filename]

## Structure
### Intro - 4 bars (~9 seconds)
Patterns: Drums A
Transition: At the next downbeat, launch the prepared state containing Bass A.

### Verse 1 - 16 bars (~34 seconds)
Patterns: Drums A, Bass A
Transition: At the last bar, mute both with one prepared state change.

### Break - 2 bars (~4 seconds)
Patterns: none
Transition: Count two bars and launch the chorus Scene on beat 1.
```

Build the structure in **performed playback order** including repeated verses,
instrumental breaks, solo (as a section without a solo *pattern*), bridge and
ending where present. Use exactly the instrument pattern names defined in
pattern files; no vocal pattern or one-off solo tab. The total is the number
of unique loop patterns, not section repetitions. Keep pattern length distinct
from section length. Label estimates next to each uncertain bar count and time;
if a vital section is unknown, leave Draft in the active draft location.
Each section, including the ending, needs a short `Transition:` line saying
exactly what the performer does next (or how to stop the song). During finalization, check that the confirmed players can operate the required
controls within the available beats. If simultaneous independent changes cannot
be operated, write
`Transition: INFEASIBLE — ...` at the section and a conspicuous
`**Transition warning:** ...` near the final header. Explain the physical
constraint and feasible alternative(s) in the draft. A warned Final may
still be published when the music is well supported, but must not be sold as
fully executable. Prefer one prepared state change over several simultaneous
manually triggered actions where appropriate.

Calculate seconds from meter and quarter-note tempo, or integrate event-map
tempo changes. If BPM is in dotted-quarter beats (e.g. 6/8), convert appropriately
instead of treating the meter numerator as quarter beats. Rounding is for
glanceability, never a reason to adjust actual bar counts. Cite research for
section boundaries in the draft; include only essential uncertainty in a brief
final warning if needed. Never copy the draft's full evidence log into Final.

Use generic terms such as pattern, prepared state, loop track, effect change and
performer. Do not prescribe synchronization until its master and routing are
confirmed.

For an explicitly approved unreliable publication, use `**Status: Partial**`
and add a nonempty `**Limitations:**` warning near the header. Name estimated or
omitted sections/parts and uncertain transitions. Do not use Partial merely for
an infeasible but fully evidenced transition: a warned Final remains appropriate.
