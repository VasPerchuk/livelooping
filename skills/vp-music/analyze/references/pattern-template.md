# <Instrument> patterns

**Source:** <validated file, track, checksum or research link>
**Tuning / capo / lanes:** <required for meaningful tab>
**Coverage:** <which instrumental line this file represents>

## Pattern: <stable name, e.g. Verse Bass>

**Source bars:** <file measures>   **Loop length:** <N> bars
**Meter:** <n/d>   **Confidence:** <confirmed/likely/estimated>
**Used in:** <section names from draft worksheet>
**Match tier / disposition:** <exact|normalized|near> / <LOOP|LIVE|OPTIONAL|UNRESOLVED|REST>
**Role evidence:** <name/MIDI/channel hint, pitch range, repetition density — role is a prior, not an exclusion>

```text
Count |1 & 2 & 3 & 4 &|
Lane  |--x-------x-----|
```

**Entry/end cue:** <start on downbeat; stop/retrigger at next section>
**Limitations:** <unscored parts, MIDI pitch-only, uncertain rhythm, etc.>

The ASCII grid is a concise performance aid, not a complete GP-ready score.
Keep it independent of personal performance hardware/software and controller
allocation.
If a scored instrumental reduction is supported, record its complete original
note onsets, durations and evidenced pitches/string/frets with the same pattern
name in `.vp-music-tracking\arrangement\arrangement.json` following
`analyze\references\arrangement-handoff.md`. Leave the arrangement blocked
when the missing events cannot be justified; do not fill them by guessing
from this grid.
