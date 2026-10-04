---
name: vp-music-finalize
description: Finalize approved live-looping song artifacts by resolving gaps, confirming performance defaults, and producing a setup-compatible worksheet and scored arrangement.gp. Supports explicitly accepted Partial results and reports hard blockers.
---

# Finalize an approved analysis

Read `skills\vp-music\CONTRACT.md`, the research/chords files, generated pattern
aids, Draft worksheet and version 2 analysis manifest. Run phase status before
trusting prior prose.

## 1. Resolve musical decisions

Present each applicable manifest decision with at most three useful options.
Honor `depends_on` and `applies_when`; do not infer behavior from an ID suffix.
Store answers in `.vp-music-tracking\finalize-input\resolutions.json`. Include
section deletion/layer omission/variant selection records only when explicitly
approved. Do not modify the neutral analysis to encode final choices.

## 2. Confirm performance facts

Only now read repository-root `.vp-music-tracking\env-defaults.md`. Confirm or
replace the relevant facts: player roles, live/preloaded parts, capacity,
reachable controls, count-in, free-running versus synchronized clocks, effects,
and one feasible generic action for each retained section.

Store generic data in `.vp-music-tracking\finalize-input\performance.json`,
including `performance.section_transitions`. A song count-in is not automatically
a count-in before every switch. Free-running devices are not synchronized merely
because they start together. Keep one-off exact product/preset details in the
conversation unless the user separately approves updating shared defaults.

Use `final` only with no reliability limitations. `partial` needs visible,
specific limitations and affirmative consent. An infeasible but evidenced action
must be marked `INFEASIBLE` with a warning.

## 3. Publish through the coordinated build

```text
python skills\vp-music\tools\finalize_build.py --song-dir <song> \
  --analysis <song>\.vp-music-tracking\arrangement\arrangement.json \
  --resolutions <resolutions.json> --performance <performance.json> \
  --title "Artist - Song"
```

This is the normal path. It resolves omissions first, validates only retained
source-backed choices, compiles sparse lanes, renders and checks section-local
worksheet actions, materializes/round-trips GP, and publishes a recoverable bundle.
Do not hand-patch generated outputs or use an old manifest/export path.

If a Draft preview is requested, use a Draft performance input plus `--draft`;
it never publishes root worksheet/GP files. Use `--replace` only after explicit
approval to replace a different or drifted existing bundle. Report exact blockers
and do not claim publication until bundle verification succeeds.

