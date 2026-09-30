---
name: vp-music-prepare
description: >-
  Coordinate a multi-phase livelooping song preparation from artist and title:
  research and validate chords and Guitar Pro/MIDI, analyze repeatable
  instrumental patterns and song structure, then finalize chord fingerings
  and the performance worksheet. Use whenever a user asks to prepare, arrange
  or build a livelooping worksheet for a song. Pause for explicit approval
  after research and after analysis; route to the specialized vp-music agents.
---

# Prepare a livelooping song

Read `skills\vp-music\CONTRACT.md` and `skills\vp-music\roles\vp-music-prepare.md`.
The phases are research -> approval -> analysis -> approval -> finalization.
At each phase, read its role and skill instructions:

| Phase | Role | Skill |
| --- | --- | --- |
| Research | `roles\vp-music-research.md` | `research\SKILL.md` |
| Analyze | `roles\vp-music-analyze.md` | `analyze\SKILL.md` |
| Finalize | `roles\vp-music-finalize.md` | `finalize\SKILL.md` |

Resolve paths relative to the `skills\vp-music` folder, not the terminal's current
directory. On a new song, perform research and stop; on resume, read progress in
the song's `research.md`. Numbered next actions (`1`, `2`, `3`) only apply to the
last menu presented for this song, and free-text feedback takes precedence. Do
not let a background agent cross a user approval boundary. Keep research facts,
pattern tabs and worksheet cues in their separate files.
