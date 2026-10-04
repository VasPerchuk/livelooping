# Livelooping song-preparation workflow

New scored preparations distinguish readable sources, usable notation evidence and
modern-GP export readiness. Use `tools\analysis_preflight.mjs` before analysis
approval and `tools\phase_status.py` when resuming or reporting completion. New
performance plans produce decluttered GP scores with source instrument identity,
used-only logical lanes plus note-empty GP-track pruning,
core/optional/unresolved ordering and a
drums-blue/bass-yellow/other-red palette.

The canonical, host-independent role and skill instructions live in this folder.
`CONTRACT.md` defines artifacts and human approval gates. `roles\` contains the
four agents; `research\`, `analyze\`, `finalize\`, `performance-worksheet\`,
`arrangement\` and `prepare\` contain task skills. Project Copilot adapters live in
`.github\agents\` and `.github\skills\`; they point back to these canonical files.
The `performance-worksheet\` skill replaces the former single-pass worksheet.

Start in the `livelooping` directory with `vp-music-prepare` and give an artist
and song title. A new preparation stops after research; explicitly approve
analysis and later finalization. You can invoke a single specialist directly
for just its phase. The agent reports gaps and pauses rather than fabricating
instrument parts or full internet lyrics.

For **new** preparations, existing song variants and supplied assets are
checked first. If no authoritative files are available, the researcher asks
once for notes, GP, PDFs, text tabs/chords or MIDI, then proceeds with stated
gaps if you have none. Research verifies identity/context online and ranks
musical evidence GP > user-provided PDF > explicit chord/tab text > MIDI.
It does not acquire the original recording and uses large streaming services
only when necessary for song identity metadata, never as routine audio or
audio-feature sources.
When GP is sufficient, choose whether to compare lower-tier sources or move
to the separate analysis approval gate. Existing PDF-reading capability may
be used for an approved comparison; there is no dedicated PDF score parser.

The user-facing `chords.md` exists from research; a concise `worksheet.md` is
published only at finalization. Workflow files for new preparations live under
`.vp-music-tracking\`: `research\research.md`,
`pattern\<instrument>.patterns.md`, `draft\worksheet-draft.md`; analysis also
authors `arrangement\arrangement.json` with complete original reduced-loop
note events when musical evidence supports them. The draft
retains assumptions; the final shows section bars/times, active patterns and
a short human `Transition:` action per block. Any impossible simultaneous
actions receive an explicit `INFEASIBLE` section cue and final warning.
Existing prepared song folders retain their root-level workflow files without
automatic migration; their arrangement handoff also lives under tracking.
Source assets stay in the song root.

Approved finalization starts with a conflict/gap questionnaire and then confirms
the performance environment. Exact personal hardware/software defaults live
only in local, ignored `.vp-music-tracking\env-defaults.md`; research, analysis
and song artifacts remain product-neutral. A changed environment is a one-off
override unless the user separately approves replacing the defaults.

Finalization compiles
`.vp-music-tracking\arrangement\performance-arrangement.json`, a concise
`worksheet.md` and continuous instrumental `arrangement.gp`, using the analyzed
event handoff and
`skills\vp-music\arrangement\SKILL.md`. The pinned alphaTab GP7+ exporter
produces modern ZIP-based `.gp` files (not legacy `.gp5`). Short ASCII
patterns alone cannot be exported as a complete score: missing events,
unsupported tempo/meter changes, ambiguous rests/live passages or an
existing output are explicit GP blockers. If questions cannot close essential
gaps, the user may provide more details and rerun analysis/finalization, pause,
or explicitly accept visibly labeled Partial worksheet and GP results. Partial
consent never bypasses structural validation, source rights or exporter limits.
Do not overwrite source scores or assume uploaded third-party tablature can be
copied wholesale.

## Copilot CLI activation

Project discovery uses the repository's `.github` directories. If an agent/skill
does not show
up in your session, explicitly trust the folder from the `livelooping` root
with `copilot --add-dir .` (the local CLI's `--help` says this loads its
`.github\agents` and `.github\skills`). Confirm discovery with `/agent` and
`/skills list`. You can then select
`vp-music-prepare` via `/agent` or start with
`copilot --add-dir . --agent vp-music-prepare`. This does not initialize Git.
If your host does not load these project adapters, use the optional personal
installation below. Do not initialize a repository just for discovery.

To install personally on Windows (opt-in, from PowerShell started in this
folder), after checking for conflicting names:

```powershell
New-Item -ItemType Directory -Force "$HOME\.copilot\agents", "$HOME\.copilot\skills" | Out-Null
foreach ($name in @('vp-music-prepare','vp-music-research','vp-music-analyze','vp-music-finalize')) {
    if (Test-Path "$HOME\.copilot\agents\$name.agent.md") { throw "Already exists: $name.agent.md" }
}
foreach ($name in @('vp-music-prepare','vp-music-research','vp-music-analyze','vp-music-finalize','vp-music-worksheet','vp-music-arrangement')) {
    if (Test-Path "$HOME\.copilot\skills\$name") { throw "Already exists: $name" }
}
Copy-Item ".github\agents\vp-music-*.agent.md" "$HOME\.copilot\agents\"
foreach ($name in @('vp-music-prepare','vp-music-research','vp-music-analyze','vp-music-finalize','vp-music-worksheet','vp-music-arrangement')) {
    Copy-Item ".github\skills\$name" "$HOME\.copilot\skills\$name" -Recurse
}
```

These small personal adapters still refer to canonical files relative to the
`livelooping` working directory; run them from this folder. Copying them does
not copy the canonical skills or song content elsewhere. Avoid overwriting a
personal agent with the same name; first check `$HOME\.copilot\agents`.
Restart the CLI or reload skills and check `/agent` and `/skills info
vp-music-prepare`. If a host has different agent/skill discovery rules, use
the canonical Markdown directly and preserve the same handoff contract.

## Performance defaults

Keep exact personal hardware/software only in repository-root
`.vp-music-tracking\env-defaults.md`. This file is local and ignored. Only
finalization reads it, asks for confirmation and maps its capabilities to
generic performer actions. Do not copy product names, versions, preset
identifiers or device routing into research, analysis, worksheets, manifests
or generated score metadata.

## Technical helpers

`scripts\download_asset.py` stages a direct HTTPS download, bounds its size,
validates it and never replaces an existing song file. For existing/local
files use `tools\music_assets.py validate --path <file>` or `inspect` with the
same `--path` option; `duration` emits bar seconds.
`scripts\pattern_candidates.py --path <file>` proposes exact repeated phrases
for the analyzer to review, rather than asserting sections automatically.
`scripts\check_worksheet.py --song-dir <folder> --plan
<performance-arrangement.json>` checks a generated published worksheet against
the compiled `plan_version: 2` performance plan itself
(status/publication, section order/ids/bar ranges/counts/times, active track
ids, optional/unresolved/deleted disclosures, limitations/consent
visibility, transitions). Use `--verify-bundle` instead to verify a published
bundle. `analyze\references\midi-pitches.md` documents provisional MIDI
instrument/drum identification. `tools\write_arrangement.mjs` exports a matching Final or explicitly approved
Partial reduction only after checking its event/section handoff, publication
metadata and worksheet and refusing overwrite; a version 2 build additionally
accepts an optional `--worksheet <worksheet.md>` flag to cross-check the
built GP file against both the compiled plan and that published worksheet in
the same step. Existing examples include
GP3/4/5, GPX, ZIP-based modern GP and MIDI; the validator fails explicitly on
unsupported formats. It does not determine whether a transcription matches the
recording.

`tools\finalize_build.py --song-dir <folder> --analysis <arrangement.json>
--resolutions <resolutions.json> --performance <performance.json>`
coordinates compiling, rendering, exporting and publishing a version 2 plan
as one command instead of invoking `compile_performance.py`/
`render_views.py`/`write_arrangement.mjs`/`check_worksheet.py` by hand. It
publishes root `worksheet.md`, root `arrangement.gp`, the tracking
`performance-arrangement.json` and a `.vp-music-tracking\publish\bundle.json`
record together as one journaled, no-clobber transaction (ordered
replacement with per-file backups, bundle record written last, rollback on
failure — honestly recoverable, not true multi-file filesystem atomicity);
`--replace` is required to republish over an existing bundle, and `--draft`
builds an isolated preview under `.vp-music-tracking\preview\<generation>\`
that never touches public outputs. Verify a published bundle with
`--verify` or `scripts\check_worksheet.py --song-dir <folder>
--verify-bundle`.

Install parser dependencies only if missing: `python -m pip install -r
skills\vp-music\tools\requirements.txt` for legacy GP3/4/5, and run `npm ci`
inside `skills\vp-music\tools` for GPX (Node.js required). MIDI and modern
ZIP-based GP inspection use Python's standard library. Guitar Pro string
numbering differs by format; use the reported `tablature.string_numbering`
and staff tuning when reviewing generated grids. Historical output layouts are
left untouched and are not automatically migrated.
