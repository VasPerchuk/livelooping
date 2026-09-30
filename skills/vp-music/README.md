# Livelooping song-preparation workflow

The canonical, host-independent role and skill instructions live in this folder.
`CONTRACT.md` defines artifacts and human approval gates. `roles\` contains the
four agents; `research\`, `analyze\`, `finalize\`, `performance-worksheet\` and
`prepare\` contain task skills. Project Copilot adapters live in
`.github\agents\` and `.github\skills\`; they point back to these canonical files.
The `performance-worksheet\` skill replaces the former single-pass worksheet.

Start in the `livelooping` directory with `vp-music-prepare` and give an artist
and song title. A new preparation stops after research; explicitly approve
analysis and later finalization. You can invoke a single specialist directly
for just its phase. The song folder holds `research.md`, `chords.md`, the draft
then final `worksheet.md`, individual `<instrument>.patterns.md`, and any
validated GP/MIDI. The agent reports gaps and pauses rather than fabricating
instrument parts or full internet lyrics.

## Copilot CLI activation

Project discovery is documented for repository `.github` directories. Although
this folder is not currently a Git repository, `copilot skill list` run from
here already shows all five project skills. If an agent/skill does not show
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
foreach ($name in @('vp-music-prepare','vp-music-research','vp-music-analyze','vp-music-finalize','vp-music-worksheet')) {
    if (Test-Path "$HOME\.copilot\skills\$name") { throw "Already exists: $name" }
}
Copy-Item ".github\agents\vp-music-*.agent.md" "$HOME\.copilot\agents\"
foreach ($name in @('vp-music-prepare','vp-music-research','vp-music-analyze','vp-music-finalize','vp-music-worksheet')) {
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

## Equipment assumptions

Maschine 3 + Mikro MK3 handles sequenced patterns/scenes. The RC-300 can
handle up to three independent stereo phrase tracks; the original Ditto is
an optional manually timed audio loop. HX Stomp is for effects, Cubase for
preparation. MIDI clock routing is not yet chosen. The workflow does not
claim Maschine can force the RC-300 to follow its clock.

## Technical helpers

`scripts\download_asset.py` stages a direct HTTPS download, bounds its size,
validates it and never replaces an existing song file. For existing/local
files use `tools\music_assets.py validate --path <file>` or `inspect` with the
same `--path` option; `duration` emits bar seconds.
`scripts\pattern_candidates.py --path <file>` proposes exact repeated phrases
for the analyzer to review, rather than asserting sections automatically.
`scripts\check_worksheet.py --song-dir <folder> --require-final` checks that
the final worksheet's patterns exist and its steady-tempo section durations
match the header. Read their `--help` for details. Existing examples include
GP3/4/5, GPX, ZIP-based modern GP and MIDI; the validator fails explicitly on
unsupported formats. It does not determine whether a transcription matches the
recording.

Install parser dependencies only if missing: `python -m pip install -r
skills\vp-music\tools\requirements.txt` for legacy GP3/4/5, and run `npm ci`
inside `skills\vp-music\tools` for GPX (Node.js required). MIDI and modern
ZIP-based GP inspection use Python's standard library. Guitar Pro string
numbering differs by format; use the reported `tablature.string_numbering`
and staff tuning before writing a tab.
