# VP Music workflow contract

## Purpose

Prepare a song for personal live looping through three separately approved phases:

1. research;
2. analysis;
3. finalization.

Stop for approval after research and after analysis. Approval for one phase never
pre-authorizes subsequent phases. A report marked `blocked`, `conflict`,
`review_required`, or Draft is not completion.

## Active artifact layout

Use one current tracking layout:

```text
<song>\
  chords.md
  worksheet.md                              # published only
  arrangement.gp                            # published only
  .vp-music-tracking\
    research\research.md
    pattern\<stable-track>.patterns.md       # generated
    draft\worksheet-draft.md                 # generated
    arrangement\arrangement.json             # neutral analysis
    arrangement\performance-arrangement.json # compiled performance plan
    finalize-input\resolutions.json
    finalize-input\performance.json
    publish\bundle.json
```

Old files may remain as historical evidence, but the workflow does not author,
export, or automatically migrate v1/legacy output layouts. Never modify a song
merely to upgrade its layout.

Exact personal hardware/software belongs only in repository-root
`.vp-music-tracking\env-defaults.md`. Research and analysis do not read it.
Finalization may read it after musical decisions, confirm it with the user, and
persist only generic capabilities/actions in song artifacts.

## Source and rights boundaries

- Do not acquire the original recording. Streaming catalog access is only for
  necessary identity metadata when independent sources cannot resolve it.
- User permission to inspect a page or file does not establish a right to
  reproduce its complete lyrics, tab, or score.
- Preserve source assets. Do not overwrite, move, delete, or silently convert them.
- Keep citations and source coordinates. A parsed score proves what that file
  contains, not that it matches a particular recording.
- GP3/4/5, GPX, modern GP, and MIDI can be research/analysis inputs.
  Source-copy GP export is narrower: modern ZIP `.gp`/GPIF with the declared
  `source_bar_reuse` capability and supported notation only.
- ASCII grids are short performance aids generated from validated source events.
  They are never the source of truth for GP export.

## Analysis invariants

The authoritative analysis is version 2
`.vp-music-tracking\arrangement\arrangement.json`.

- Use stable track IDs; display names may duplicate.
- Dispositions are `LOOP`, `LIVE`, `OPTIONAL`, `UNRESOLVED`, or `REST`.
- Prefer coherent 4/8-bar phrases and normally no more than three core loops per
  source instrument. A short, irregular, or additional selection needs a
  specific musical reason, but that reason never waives source or timing errors.
- Repeated silence does not establish a pattern. Sustained sounding notes are not
  REST.
- `active_pattern_ids` are simultaneous for the entire section block. Split
  ordered sub-blocks when material changes; do not encode body/ending or 2+1 bars
  as concurrent loops.
- Record performed written-bar order when repeats or alternate endings make it
  differ from linear written order.
- Keep musical-review findings separate from hard source/export errors.
- Render pattern Markdown and the Draft worksheet from the validated manifest;
  do not hand-maintain parallel copies.

Analysis preflight reviews all offered choices. A blocked analysis report must be
corrected or returned to the user; do not narrate it as complete.

## Finalization invariants

Finalization has two intake gates:

1. Resolve every applicable musical decision using its explicit decision graph.
2. Confirm players, live/preloaded roles, reachable actions, count-in, clock and
   synchronization assumptions, and one generic action per retained section.

Use free-form user answers when necessary, but store structured decisions and
generic performance facts. A conditional decision applies only when its
`depends_on` answer matches `applies_when`; IDs do not encode behavior.

Compile decisions before source-export checks. Unsupported discarded alternatives
remain documented but do not block a valid retained choice. Retained material must
still pass exact source, fingerprint, length, meter, tempo, boundary, and notation
checks.

Publication status:

- `final`: no unresolved musical material and no reliability limitations.
- `partial`: explicit limitations plus affirmative consent; may disclose deferred
  or omitted material, but cannot bypass source integrity or exporter limits.
- `draft`: isolated preview only; never publishes root worksheet/GP artifacts.

An infeasible but well-supported transition may remain Final only when both the
section and worksheet header visibly say `INFEASIBLE` and explain the constraint.

## Output-track and timing semantics

- Preserve the full source-track inventory for provenance.
- Allocate output lanes only for actual placements. Omitted/resting instruments
  and unused LIVE/UNRESOLVED roles create no GP track.
- Keep one logical output track per retained loop. Do not merge distinct material
  merely to meet a track budget.
- Place LIVE spans from verified written-to-performed coordinates. Never equate
  cumulative section position with written bar number when repeats differ.
- Apply partial-cycle behavior only from an explicit reviewed tail policy.
- Materialize notation, prune note-empty physical tracks, then enforce the
  32-notated-track application guard. A cue-only plan cannot publish a blank GP.
- Cue-only requirements stay visible in the worksheet after their empty physical
  GP lane is pruned.

## Publication and recovery

`tools\finalize_build.py` is the normal publication entry point. It compiles the
plan, renders the worksheet, writes and round-trips the GP, checks worksheet/plan
parity, stages hashes, and publishes through a recoverable journal. The bundle is
written last and is the durable publication record.

Never hand-edit generated pattern files, Draft worksheet, compiled plan, published
worksheet, GP, or bundle. Change authoritative inputs and rerun with explicit
replacement. Never report success unless the requested files exist and verification
passes.

`tools\phase_status.py` reports managed artifact presence and validation. It does
not infer user approval or musical correctness from progress prose.

## Tool ownership

- `music_assets.py`: bounded source parsing and performed-order extraction.
- `pattern_candidates.py`: candidate suggestions, not automatic musical approval.
- `v2_schema.py`: Python analysis/plan validation.
- `analysis_preflight.mjs` and `source_compiler.mjs`: source-backed capability review.
- `compile_performance.py`: decision resolution, section edits, placements and sparse lanes.
- `render_views.py`: generated grids, Draft worksheet and published worksheet.
- `check_worksheet.py --plan`: section-local published worksheet parity.
- `write_arrangement.mjs --performance`: retained-plan GP materialization/verification.
- `finalize_build.py`: coordinated publication and recovery.

Do not create another scheduler, workflow database, schema generator, approval-token
system, or duplicate renderer to solve a local validation problem.
