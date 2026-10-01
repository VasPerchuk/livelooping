# Scored reduction handoff (v1)

Analysis writes `.vp-music-tracking\arrangement\arrangement.json` for new
**and** legacy songs when it can author complete instrumental loop events.
This file is hardware-neutral: do not include product names, player/controller
allocation, routing, synchronization or performance-software settings.
This is an original reduction, not a copy of every note in a supplied score.
The short ASCII pattern files remain separate and must use the same names.
If the evidence does not support a playable part, record the gap in the draft
and do not manufacture events to make an exporter pass.

The v1 handoff is a single JSON object:

```json
{
  "version": 1,
  "tempo_bpm": 120,
  "meter": "4/4",
  "tracks": [
    {
      "name": "Guitar",
      "kind": "fretted",
      "tuning_midi": [40, 45, 50, 55, 59, 64],
      "capo_fret": 0,
      "patterns": [
        {
          "name": "Guitar A",
          "bars": 1,
          "source": "original reduction; evidence: validated score, track and bars",
          "confidence": "likely",
          "notes": [
            {
              "bar": 1,
              "offset_quarters": 0,
              "duration_quarters": 1,
              "string": 1,
              "fret": 0,
              "pitch": 40
            }
          ]
        }
      ]
    }
  ],
  "sections": [
    {"name": "Intro", "bars": 2, "patterns": ["Guitar A"]},
    {"name": "Break", "bars": 1, "patterns": [], "empty_policy": "live"}
  ]
}
```

The example is invented, not drawn from a song. The tuning array is MIDI
pitches **low string to high string**, and a fretted note's `string` uses
**1 = lowest-pitched string**, matching the modern GP exporter. The analyzer
must convert legacy GP5's 1 = highest convention before recording events;
the writer must round-trip-verify pitch and string/fret. A fretted note
requires a nonnegative fret and evidenced string;
`pitch` is an optional cross-check, not permission to invent a fret. For
`kind: "pitched"` use a pitch (0-127) with no string/fret; for
`kind: "percussion"` use the evidenced MIDI percussion pitch, not an
unverified kit-piece name. Record a nonempty source and `confirmed`, `likely`
or `estimated` confidence for each pattern; `unknown` is a GP blocker,
including uncertainties about how its sounds map to the recording.

`bar` is 1-based within its pattern; `offset_quarters` starts at zero within
that bar. Version 1 accepts 1/16-quarter onset positions (64th-note grid)
and single **undotted** whole through 64th durations; notes must end within
their own bar. Simultaneous notes can form a chord only if they have the
same duration. Overlapping different onsets, ties, tuplets, independent
voices and notes crossing bars are unsupported; record these as blockers
rather than quantizing them silently. The
published worksheet's `## Structure` supplies the authoritative performed
section order/bar counts and active names. An event handoff's `sections`
must match it; a non-multiple cycle length requires an explicit
`tail_policy: "truncate"` after human review. A section with no active
patterns needs `empty_policy: "rest"` (intentional silence) or `"live"`
(unscored human performance); neither creates invented notes.

Version 1 supports **constant** quarter-note BPM and meter only. For
dotted-quarter tempo, changing meter/tempo, pickups, sustained notes crossing
boundaries, unsupported string techniques or uncertain rhythm, leave the
arrangement blocked with a specific reason while the supported worksheet
can still be published. Do not silently coerce or average those cases.
This sidecar is an internal file: never move an existing legacy worksheet or
pattern file merely to create it. Never overwrite a hand-edited sidecar.

Finalization creates a separate
`.vp-music-tracking\arrangement\performance-arrangement.json` from this
analysis handoff after resolving conflicts and confirming generic performance
capabilities. Do not rewrite this neutral file to apply finalization choices.
The performance handoff retains the version 1 musical fields and adds:

```json
{
  "publication": {
    "status": "partial",
    "limitations": ["Lead part omitted"],
    "consent": "User accepted the disclosed omission for rehearsal."
  },
  "performance": {
    "players": 2,
    "roles": ["Player 1 performs the live lead"],
    "transitions": ["Use one prepared state change at the chorus"]
  }
}
```

The fragment is illustrative; merge these objects into the complete handoff.
`status` is `final` with an empty limitations array and no consent field, or
`partial` with nonempty limitations and consent. Performance descriptions use
generic capabilities/actions only. The exporter accepts a missing `publication`
object as a backward-compatible complete Final handoff, but new finalizations
must write explicit publication metadata to the performance handoff.
