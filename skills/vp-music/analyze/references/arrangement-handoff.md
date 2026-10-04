# Version 2 arrangement handoff

`arrangement.json` is the hardware-neutral analysis authority. Generated pattern
Markdown and the Draft worksheet are views; the compiled performance plan is a
separate finalization artifact.

## Analysis manifest

```json
{
  "version": 2,
  "source": {
    "asset_relative_path": "source.gp",
    "asset_sha256": "<64 hex>",
    "format": "gpif",
    "capabilities": ["performed_order", "source_bar_reuse"]
  },
  "tracks": [],
  "decisions": [],
  "patterns": [],
  "sections": [],
  "performed_written_bars": [1, 2, 3, 4],
  "tempo_bpm": 120,
  "meter": "4/4"
}
```

Paths are song-relative and cannot escape the song directory. Hashes bind the
manifest to the exact source bytes. `performed_written_bars` is optional only
when performed order equals linear written order; when present, its length equals
the sum of section bars.

### Tracks and spans

```json
{
  "id": "track-rhythm",
  "name": "Guitar",
  "role": "rhythm",
  "role_confidence": "likely",
  "spans": [{
    "id": "rhythm-a",
    "disposition": "LOOP",
    "match_tier": "exact",
    "evidence": "bars 9-12 recur at 25-28",
    "source_ref": {
      "asset_relative_path": "source.gp",
      "asset_sha256": "<same source hash>",
      "track_index": 0,
      "track_name": "Guitar",
      "staff_index": 0,
      "voice_indices": [0],
      "written_master_bars": [9, 12],
      "performed_occurrences": [1, 2],
      "content_fingerprint": "<optional 64 hex>"
    }
  }]
}
```

Track IDs are identity; names may duplicate. Dispositions are:

- `LOOP`: repeatable retained candidate;
- `LIVE`: performed/cued once rather than looped;
- `OPTIONAL`: omitted by default unless explicitly retained;
- `UNRESOLVED`: requires a decision before Final;
- `REST`: intentional silence.

`written_master_bars` is an inclusive contiguous range for source-copy material.
Do not combine unrelated ranges into one source-copy pattern. Near-match evidence
records matching/total bars and divergent offsets; it never silently normalizes
source notes.

### Decisions

```json
{
  "id": "choose-rhythm-a",
  "question": "Use the canonical loop or play this passage live?",
  "options": ["use_canonical", "play_live", "omit"]
}
```

A conditional decision adds both:

```json
{
  "id": "omit-rhythm-a",
  "depends_on": "choose-rhythm-a",
  "applies_when": "use_canonical",
  "question": "Keep or omit the canonical loop?",
  "options": ["keep_loop", "omit"]
}
```

Patterns bind decisions with `decision_ids`. The compiler follows the dependency
graph; ordering and ID suffixes have no semantics.

### Patterns and variants

```json
{
  "id": "pattern-rhythm-a",
  "name": "Rhythm A",
  "track_id": "track-rhythm",
  "track": "Guitar",
  "span_ids": ["rhythm-a"],
  "bars": 4,
  "disposition": "LOOP",
  "confidence": "confirmed",
  "decision_ids": ["choose-rhythm-a", "omit-rhythm-a"],
  "selection_reason": "Four-bar harmonic phrase with clean boundary",
  "variants": [{
    "id": "rhythm-a-ending",
    "span_ids": ["rhythm-a-ending-span"],
    "occurrence_index": 2
  }]
}
```

Variants reference owning spans; they do not carry a second incompatible source
reference shape. `selection_reason` documents musical selection only.

### Sections

```json
{
  "id": "verse-1-body",
  "name": "Verse 1 body",
  "bars": 12,
  "active_pattern_ids": ["pattern-rhythm-a"]
}
```

All active patterns sound simultaneously for the whole block. Split an ending,
fill, or pattern change into a following block. An empty block declares
`empty_policy: "rest"` or `"live"`.

## Finalization inputs

`resolutions.json` may be a decision list or an object containing `decisions`,
`section_actions`, `layer_omissions`, and `variant_selections`.

`performance.json` contains publication status/limitations/consent, optional
reviewed `tail_policy`, generic roles and `section_transitions`:

```json
{
  "status": "final",
  "limitations": [],
  "performance": {
    "players": 1,
    "roles": ["Player performs rhythm guitar"],
    "section_transitions": [{
      "section_id": "verse-1-body",
      "action": "Launch the prepared verse state on beat 1.",
      "feasible": true
    }]
  }
}
```

Use `warning` when `feasible` is false. Finalization requires actions for retained
sections only.

## Source-copy export slice

Modern ZIP `.gp`/GPIF may authorize `source_bar_reuse`. LOOP material supports one
track/staff/voice and contiguous 1/2/4/8 bars. Exact LIVE/UNRESOLVED one-offs may
span 1-64 bars. Source meter/tempo must match the constant plan, boundaries cannot
depend on outside ties/grace timing, and unsupported effects remain explicit.

The writer resolves the exact retained source refs, checks fingerprints and declared
lengths, copies supported notation, prunes note-empty output tracks, and round-trips
the result. It never reconstructs retained source material from ASCII grids or
silently converts an invalid reference to a cue.

## Commands

```text
node skills\vp-music\tools\analysis_preflight.mjs --manifest <arrangement.json> --song-dir <song>
python skills\vp-music\tools\render_views.py --manifest <arrangement.json> --song-dir <song>
python skills\vp-music\tools\compile_performance.py --manifest <arrangement.json> --resolutions <resolutions.json> --performance <performance.json> --output <performance-arrangement.json>
python skills\vp-music\tools\finalize_build.py --song-dir <song> --analysis <arrangement.json> --resolutions <resolutions.json> --performance <performance.json> --title "Artist - Song"
```
