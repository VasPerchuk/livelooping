# vp-music-analyze

Follow `skills\vp-music\CONTRACT.md` and the analysis skill. Read the layout's
research (`.vp-music-tracking\research\research.md` or legacy root `research.md`),
validated GP/MIDI and existing draft artifacts. Check instrument identity and
ordered sections against research. Derive concise recurring instrumental patterns
for live looping, normally no more than three per instrument; exclude vocal and
non-recurring solo transcription. For new preparations, write one
`.vp-music-tracking\pattern\<instrument>.patterns.md` per repeatable part and
`.vp-music-tracking\draft\worksheet-draft.md` with **Status: Draft**; do not
publish root `worksheet.md` during analysis. For legacy root-level workflows,
update root patterns and Draft `worksheet.md` in place. If both layouts coexist,
ask which handoff is current instead of creating a second active draft.

For generic MIDI track names, use `analyze\references\midi-pitches.md` and
inspect per-note channel (zero-based; 9 means GM channel 10), pitch range,
timing/rhythm and note overlap/polyphony. Corroborate with track names, research
and any independently available patch/kit data; `music_assets.py inspect` does
not expose program changes. Separate mixed-channel parts. State the proposed
role, evidence, alternative and confidence per uncertain part; the inspector's
`confidence` is parse completeness, not an instrument verdict. If attribution
or drum mapping remains uncertain, retain track/channel/pitch labels and seek
a verified score, kit map or audio instead of inventing an instrument, drum
sound or guitar fingering.

Do not assert a section length merely because a file contains that many MIDI ticks.
Check repeats, meter, tempo, pickups and source omissions; name every gap and
uncertainty explicitly. Pattern names in the worksheet must resolve to real
patterns. Offer up to three next actions including finalization only if the draft
has enough evidence; obtain approval before that handoff.
