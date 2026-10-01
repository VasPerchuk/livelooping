---
name: vp-music-prepare
description: >-
  Coordinate a multi-phase livelooping song preparation from artist and title:
  research and validate chords and Guitar Pro/MIDI, analyze repeatable
  instrumental patterns and song structure, then finalize chord fingerings,
  the performance worksheet and a scored Guitar Pro reduction when the
  analyzed events support one. Use whenever a user asks to prepare, arrange
  or build a livelooping worksheet for a song. Pause for explicit approval
  after research and after analysis; route to the specialized vp-music agents.
---

# Prepare a livelooping song

Read `skills\vp-music\CONTRACT.md` and `skills\vp-music\roles\vp-music-prepare.md`.
The phases are research -> approval -> analysis -> approval -> finalization.
Finalization begins with conflict/gap resolution and performance-environment
confirmation before writing outputs; it is not a mechanical publish step.
At each phase, read its role and skill instructions:

| Phase | Role | Skill |
| --- | --- | --- |
| Research | `roles\vp-music-research.md` | `research\SKILL.md` |
| Analyze | `roles\vp-music-analyze.md` | `analyze\SKILL.md` |
| Finalize | `roles\vp-music-finalize.md` | `finalize\SKILL.md` |

Resolve paths relative to the `skills\vp-music` folder, not the terminal's current
directory. Before delegating research, check existing song-name variants and
inventory source assets. If none are present, ask once whether the user can
provide notes, GP, PDF, text or MIDI; wait for promised uploads, or continue
with stated gaps if none are available. Determine legacy versus tracking layout
as defined in the contract. On a new song, perform research and stop; on resume,
read progress in the active research record (tracking `research\research.md`
or legacy root `research.md`). Numbered next actions (`1`, `2`, `3`) only apply to the
last menu presented for this song, and free-text feedback takes precedence. Do
not let a background agent cross a user approval boundary. Keep research facts,
pattern tabs and worksheet cues in their separate files.
