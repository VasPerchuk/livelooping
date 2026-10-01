---
name: vp-music-finalize
description: Complete an approved live-looping preparation by resolving musical gaps, confirming the performance environment, then producing setup-compatible chord guidance, worksheet.md and arrangement.gp. Use only after an approved analysis-to-finalization handoff or an explicit request to finalize prepared song files.
---

# Finalize song preparation

Read `skills\vp-music\CONTRACT.md`, the active research record, root `chords.md`,
every active pattern file, the internal `draft\worksheet-draft.md` (legacy:
root Draft `worksheet.md`), the finalizer role, worksheet skill and arrangement
skill. Inspect `.vp-music-tracking\arrangement\arrangement.json` when present.
Do not write final artifacts before completing the intake below.

## 1. Inventory conflicts and gaps

Confirm song/version identity, section order, bars/times, pattern references,
chord conflicts, event coverage, ambiguous live/rest sections, partial cycles,
rights/provenance and exporter limitations. Treat the neutral analysis handoff
as evidence, not as an already finalized performance arrangement.

Open a conflict-resolution questionnaire even when the inventory appears
complete: state that no material conflicts were found, or ask one question per
gap. Give up to three visibly numbered (`1.`, `2.`, `3.`) options for each
question and accept free-form details. Options may include an evidence-backed
interpretation, an explicitly original simplification/estimate, and supplying
or deferring for more evidence. Never create unsupported alternatives merely
to fill three slots. A bare numeric answer applies only to the displayed
question. Record each resolution as source-supported, user-provided or an
original arrangement choice.

## 2. Confirm the performance environment

Only now read repository-root `.vp-music-tracking\env-defaults.md`. If it is
missing or malformed, say so and gather the active environment instead of
silently inventing defaults. Present a performance-defaults questionnaire that
asks the user to confirm the defaults or choose/provide another setup. For every
relevant unknown, provide up to three numbered alternatives and free-form input.
Gather only what affects this song:

- hardware/software roles and capabilities, without copying product names into
  song artifacts;
- number of players and which parts remain live or are preloaded;
- loop/pattern capacity and available prepared states;
- reachable controls and whether hands can leave an instrument;
- preferred transition style and count-in;
- clock, routing and synchronization when relevant;
- effects roles and changes that affect performance actions.

If the user selects a different setup, treat it as a one-off override and
separately ask whether to replace the shared defaults. If yes, gather complete
details and update only `.vp-music-tracking\env-defaults.md`. If no, keep exact
one-off details in the conversation only. Persist generic capabilities and
derived performance decisions everywhere else.

## 3. Compile the performance artifacts

Create or update
`.vp-music-tracking\arrangement\performance-arrangement.json` without
overwriting the neutral analysis handoff. It uses the documented arrangement
schema plus:

- `publication.status`: `final` or `partial`;
- `publication.limitations`: an array, empty only for `final`;
- `publication.consent`: a nonempty description for `partial`;
- optional generic `performance` roles/actions with no exact personal product
  names, versions, preset identifiers or routing device names.

Use source events and explicitly approved original simplifications only. Keep
omitted/unscored coverage explicit. Do not invent source-derived notes, guitar
frets, percussion identities or rights.

In `chords.md`, list recommended playable guitar shapes before the section chart
and state tuning, capo and alternative voicings. Keep source timing as far as it
is supported. If user-provided or appropriately licensed lyrics are available,
align chords above permitted text. Otherwise link the lyric/chord reference and
use short entry cues; do not reproduce full web lyrics or a complete tab.
Distinguish recommended shapes from verified original-performance shapes.

Publish root `worksheet.md` using the worksheet skill. Keep essential
identity/tempo/meter, the pattern legend, every performed section's bars,
approximate seconds, active patterns and one short generic `Transition:` action.
Keep evidence, detailed assumptions and alternatives in the internal draft.
No exact personal product names, chord names, note names, full tabs, vocal
patterns or one-off solo transcription. Check each action against the confirmed
players, controls and time. If it cannot be performed, label the section
`INFEASIBLE` and add a conspicuous `**Transition warning:**`; a fully evidenced
worksheet may still be Final with that warning.

For supported complete results use `**Status: Final**`. For explicitly accepted
unreliable/incomplete results use `**Status: Partial**` and a nonempty
`**Limitations:**` warning naming estimated or omitted coverage and uncertain
transitions. Run:

```text
python skills\vp-music\scripts\check_worksheet.py --song-dir <folder> --require-final
```

The command accepts a published Final or a properly labeled Partial. Fix all
structural discrepancies before export. For changing tempos/meters, verify
event-map timing manually; the current checker/exporter supports only the
documented model.

Invoke `arrangement\SKILL.md` with the finalized performance handoff and publish
root `arrangement.gp` when representable. Preserve output no-clobber behavior,
source-rights checks and round-trip validation. Partial consent permits a
structurally valid, visibly Partial score of the approved available reduction;
it does not permit malformed timing, hidden omissions, fabricated notes or
bypassing exporter limits. Record the worksheet/GP status, checksum and exact
blocker or limitations in `## Preparation progress`.

## 4. Resolve any remaining insufficiency

If the questionnaires leave essential information unresolved, explain the
effect and ask exactly:

1. Accept clearly labeled partial/unreliable results.
2. Provide more details and rerun analysis, then finalization.
3. Pause.

Do not infer partial consent from approval to finalize. If option 2 is selected,
collect the details, run only the authorized analysis, stop at its approval
gate, and require fresh authorization before finalization. If option 1 is
selected, record the disclosed gaps, assumptions and consent scope in the
performance handoff and publish both Partial artifacts only when structurally
possible. If no supported notes remain, the timeline is unrepresentable, an
existing output cannot be overwritten, or another hard constraint remains,
report the precise blocker rather than producing success-shaped output.

Do not overwrite files edited since analysis without checking with the user.
Report both requested artifacts and their quality, or name which artifact is
blocked and why.
