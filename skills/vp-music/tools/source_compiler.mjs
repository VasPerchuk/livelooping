// v2 source-bar reuse compiler: resolves a digest-matched modern ZIP .gp/GPIF asset and
// explicitly copies a narrow, documented field matrix for one selected track/staff/voice
// span into plain data the arrangement writer turns into brand-new alphaTab model objects.
//
// This module never calls a public `.clone()` on alphaTab objects and never persists or
// re-exports raw source bytes/XML; it only reads bounded fields off the parsed in-memory
// score to build independent output objects. See `analyze\references\arrangement-handoff.md`
// ("Source-bar reuse (vertical slice)") for the authoritative capability/limitation text
// this module must stay consistent with.
//
// Copied field matrix (documented; anything else on a beat/note is either rejected as an
// unsupported effect or silently irrelevant structural bookkeeping that the writer does not
// need, e.g. `Beat.index`):
//   Beat:  duration, dots, tupletNumerator, tupletDenominator, (notes empty => true rest)
//   Note (fretted):   string, fret
//   Note (pitched):   octave, tone
//   Note (percussion): percussionArticulation
//   Note effects: isPalmMute, isLetRing, isHammerPullOrigin plus the validated,
//     span-local destination coordinate required by hammer/pull
//   Staff:  stringTuning (ascending tuning_midi), capo, isPercussion, showTablature
//   Track:  name, playback program/bank/channels and source color
//
// Supported slice only: modern ZIP .gp/GPIF source, one track/staff/voice per span,
// constant meter/tempo across the span, no tie/grace/tuplet/hammer-pull dependency crossing
// the span's written-bar boundaries, and none of the unsupported-effect fields listed in
// `UNSUPPORTED_NOTE_EFFECTS`/`UNSUPPORTED_BEAT_EFFECTS`. `compileSourcePattern` (LOOP
// material) additionally restricts the span to a contiguous 1/2/4/8-bar range;
// `compileSourceSpan` (one-off LIVE/UNRESOLVED-reference material) lifts that cycle-length
// restriction but still bounds the span to `ONE_OFF_SPAN_LIMITS.maxBars`. Any violation
// raises instead of silently falling back to a numeric reconstruction or a plain rest.
import * as alphaTab from '@coderline/alphatab';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const { model } = alphaTab;

export const SOURCE_LIMITS = { bytes: 16 * 1024 * 1024, barsPerPattern: [1, 2, 4, 8] };
// One-off LIVE/UNRESOLVED reference spans are not loops: they are not restricted to the
// 1/2/4/8-bar cycle set, but remain bounded (a "contiguous one-off span", not an unbounded
// copy of an entire source file) by this generous bar count.
export const ONE_OFF_SPAN_LIMITS = { maxBars: 64 };

const insist = (ok, message) => { if (!ok) throw new Error(`source-copy: ${message}`); };

function runPython(args, options) {
  try {
    return execFileSync('python', args, options);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    return execFileSync('py', ['-3', ...args], options);
  }
}

function inspectWithPython(file) {
  const script = path.join(path.dirname(fileURLToPath(import.meta.url)), 'music_assets.py');
  const raw = runPython([script, 'inspect', '--path', file],
    { encoding: 'utf8', timeout: 60_000, maxBuffer: 16 * 1024 * 1024 });
  return JSON.parse(raw);
}

// Resolves a song-relative asset path strictly under `songDir`, rejecting traversal and
// symlink escape by comparing the real (symlink-resolved) path against the real song dir.
function resolveUnderSongDir(songDir, relativePath) {
  const songDirReal = fs.realpathSync(path.resolve(songDir));
  const candidate = path.resolve(songDirReal, relativePath);
  insist(fs.existsSync(candidate), `source asset not found: ${relativePath}`);
  const real = fs.realpathSync(candidate);
  const rel = path.relative(songDirReal, real);
  insist(rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel)),
    `source asset path escapes the song directory: ${relativePath}`);
  return real;
}

// One resolved-and-parsed source per manifest `source` block, cached by absolute path so
// multiple patterns referencing the same asset only validate/parse it once.
export class SourceResolver {
  constructor(songDir) {
    this.songDir = songDir;
    this.cache = new Map();
  }

  resolve(manifestSource) {
    insist(manifestSource.format === 'gpif',
      'only modern ZIP .gp/GPIF sources are supported in this slice ' +
      `(got source.format=${manifestSource.format})`);
    insist(Array.isArray(manifestSource.capabilities) &&
      manifestSource.capabilities.includes('source_bar_reuse'),
    'source.capabilities must explicitly include "source_bar_reuse" to authorize ' +
      'GPIF source-bar reuse');
    const cacheKey = `${manifestSource.asset_relative_path}:${manifestSource.asset_sha256}`;
    if (this.cache.has(cacheKey)) return this.cache.get(cacheKey);

    const absolute = resolveUnderSongDir(this.songDir, manifestSource.asset_relative_path);
    const size = fs.statSync(absolute).size;
    insist(size > 0 && size <= SOURCE_LIMITS.bytes, 'source asset exceeds the size bound');
    const bytes = fs.readFileSync(absolute);
    const checksum = crypto.createHash('sha256').update(bytes).digest('hex');
    insist(checksum === manifestSource.asset_sha256,
      'source asset SHA-256 does not match the manifest digest (source changed or wrong file)');
    insist(bytes.length >= 4 && bytes.readUInt32BE(0) === 0x504b0304,
      'source asset is not a modern ZIP-based .gp file');

    // Reuse the hardened, size/entry/XML-bounded Python inspector as the gate before
    // trusting alphaTab's own parser with the bytes; this preserves the existing archive
    // safety controls (path traversal, zip-bomb ratios, DOCTYPE/ENTITY, object counts).
    const inspected = inspectWithPython(absolute);
    insist(inspected.status === 'valid' && inspected.format === 'gp-zip',
      `source asset failed the hardened inspector: ${inspected.error || inspected.status}`);
    insist(inspected.checksum_sha256 === manifestSource.asset_sha256,
      'hardened inspector checksum disagrees with the manifest digest');

    const score = alphaTab.importer.ScoreLoader.loadScoreFromBytes(
      new Uint8Array(bytes), new alphaTab.Settings());
    insist(score && Array.isArray(score.tracks) && score.tracks.length > 0 &&
      Array.isArray(score.masterBars) && score.masterBars.length > 0,
    'source asset produced an unusable parsed score');

    const resolved = { score, absolute, checksum };
    this.cache.set(cacheKey, resolved);
    return resolved;
  }
}

const UNSUPPORTED_NOTE_EFFECTS = [
  ['bendType', (n) => n.bendType !== model.BendType.None],
  ['harmonicType', (n) => n.harmonicType !== model.HarmonicType.None],
  ['isGhost', (n) => n.isGhost],
  ['slideInType', (n) => n.slideInType !== model.SlideInType.None],
  ['slideOutType', (n) => n.slideOutType !== model.SlideOutType.None],
  ['vibrato', (n) => n.vibrato !== model.VibratoType.None],
  ['isTrill', (n) => n.isTrill],
  ['isSlurDestination', (n) => n.isSlurDestination],
];
const UNSUPPORTED_BEAT_EFFECTS = [
  ['whammyBarPoints', (b) => !!b.whammyBarPoints && b.whammyBarPoints.length > 0],
  ['vibrato', (b) => b.vibrato !== model.VibratoType.None],
  ['fade', (b) => b.fade !== model.FadeType.None],
  ['pop', (b) => b.pop],
  ['slap', (b) => b.slap],
  ['tap', (b) => b.tap],
  ['brushType', (b) => b.brushType !== model.BrushType.None],
  ['hasChord', (b) => b.hasChord],
];

function assertNoUnsupportedEffects(beat, where) {
  for (const [field, test] of UNSUPPORTED_BEAT_EFFECTS) {
    insist(!test(beat), `${where}: beat has unsupported effect '${field}' that would not be ` +
      'copied faithfully');
  }
  for (const note of beat.notes) {
    for (const [field, test] of UNSUPPORTED_NOTE_EFFECTS) {
      insist(!test(note), `${where}: note has unsupported effect '${field}' that would not ` +
        'be copied faithfully');
    }
  }
}

function barIndexOf(beat) {
  return beat.voice.bar.index;
}

function assertNoBoundaryTies(firstBarBeats, lastBarBeats, startIndex, endIndex, where) {
  for (const beat of firstBarBeats) {
    for (const note of beat.notes) {
      insist(!note.isTieDestination,
        `${where}: a tie enters the pattern from before written bar ${startIndex + 1}`);
    }
  }
  for (const beat of lastBarBeats) {
    for (const note of beat.notes) {
      if (note.tieDestination) {
        const destinationBar = barIndexOf(note.tieDestination.beat);
        insist(destinationBar <= endIndex,
          `${where}: a tie leaves the pattern after written bar ${endIndex + 1}`);
      }
    }
  }
}

function assertNoBoundaryGrace(firstBarBeats, lastBarBeats, startIndex, endIndex, where) {
  for (const beat of firstBarBeats) {
    insist(beat.graceType === model.GraceType.None,
      `${where}: a grace note at written bar ${startIndex + 1} may borrow time across ` +
      'the pattern boundary');
  }
  for (const beat of lastBarBeats) {
    insist(beat.graceType === model.GraceType.None,
      `${where}: a grace note at written bar ${endIndex + 1} may borrow time across ` +
      'the pattern boundary');
  }
}

function assertNoBoundaryTuplets(firstBarBeats, lastBarBeats, where) {
  const first = firstBarBeats[0];
  if (first && first.hasTuplet && first.previousBeat &&
    barIndexOf(first.previousBeat) < barIndexOf(first)) {
    insist(!(first.previousBeat.hasTuplet &&
      first.previousBeat.tupletNumerator === first.tupletNumerator &&
      first.previousBeat.tupletDenominator === first.tupletDenominator),
    `${where}: a tuplet group crosses the pattern's start boundary`);
  }

  const last = lastBarBeats[lastBarBeats.length - 1];
  if (last && last.hasTuplet && last.nextBeat && barIndexOf(last.nextBeat) > barIndexOf(last)) {
    insist(!(last.nextBeat.hasTuplet &&
      last.nextBeat.tupletNumerator === last.tupletNumerator &&
      last.nextBeat.tupletDenominator === last.tupletDenominator),
    `${where}: a tuplet group crosses the pattern's end boundary`);
  }
}

function noteLocations(barBeats) {
  const locations = new Map();
  for (const [barOffset, beats] of barBeats.entries()) {
    for (const [beatIndex, beat] of beats.entries()) {
      for (const [noteIndex, note] of beat.notes.entries()) {
        locations.set(note, { barOffset, beatIndex, noteIndex });
      }
    }
  }
  return locations;
}

function destinationCoordinate(note, destination, locations) {
  if (!destination) return null;
  const from = locations.get(note);
  const to = locations.get(destination);
  if (!from || !to) return null;
  return [to.barOffset - from.barOffset, to.beatIndex, to.noteIndex];
}

function assertNoBoundaryNoteEffects(staff, voiceIndex, barBeats, startIndex, endIndex, where) {
  const selected = noteLocations(barBeats);
  const allNotes = [];
  for (const bar of staff.bars) {
    const voice = bar.voices[voiceIndex];
    if (!voice) continue;
    for (const beat of voice.beats) allNotes.push(...beat.notes);
  }

  for (const note of selected.keys()) {
    if (!note.isHammerPullOrigin) continue;
    const destination = note.hammerPullDestination;
    insist(destination && destination !== note,
      `${where}: hammer/pull origin has no representable destination`);
    insist(selected.has(destination),
      `${where}: hammer/pull relationship leaves the selected source span after written ` +
      `bar ${endIndex + 1}`);
  }
  for (const note of allNotes) {
    if (selected.has(note) || !note.isHammerPullOrigin) continue;
    const destination = note.hammerPullDestination;
    if (destination && selected.has(destination)) {
      insist(false, `${where}: hammer/pull relationship enters the selected source span ` +
        `before written bar ${startIndex + 1}`);
    }
  }
}

function findActiveTempo(score, beforeOrAtIndex) {
  for (let i = beforeOrAtIndex; i >= 0; i--) {
    const automations = score.masterBars[i].tempoAutomations;
    if (automations && automations.length > 0) return automations[automations.length - 1].value;
  }
  return undefined;
}

function fingerprintOf(bars) {
  const payload = bars.map((bar) => bar.beats.map((beat) => [
    beat.duration, beat.dots, beat.tupletNumerator, beat.tupletDenominator,
    beat.notes.map((n) => [n.string ?? null, n.fret ?? null, n.octave ?? null, n.tone ?? null,
      n.percussionArticulation ?? null, n.isPalmMute, n.isLetRing, n.isHammerPullOrigin,
      n.hammerPullDestination]),
  ]));
  return crypto.createHash('sha256').update(JSON.stringify(payload)).digest('hex');
}

// Validates a single pattern's `source_ref` against this slice's guaranteed capability and
// returns plain copied data: `{ trackName, kind, tuningMidi, capoFret, playback, sourceColor, numerator,
// denominator, tempoBpm, bars: [{ beats: [{ duration, dots, tupletNumerator,
// tupletDenominator, notes: [...] }] }], contentFingerprint }`.
//
// `compileSourcePattern` is the LOOP-only entry point: it keeps this slice's original
// contiguous 1/2/4/8-bar restriction (a loop must be a tidy, cycle-friendly length).
// `compileSourceSpan` is the one-off LIVE/UNRESOLVED-reference entry point added for
// requirement 4: the same digest/path/fingerprint/meter/tempo/effect-boundary checks
// apply, but the span is not restricted to the loop cycle set -- only bounded by
// `ONE_OFF_SPAN_LIMITS.maxBars` (it is still a single contiguous span, never an unbounded
// copy of an entire source file, and it never applies any transform: the copied field
// matrix is identical in shape to a loop pattern's).
function compileSpan(resolved, sourceRef, where, validateBarCount) {
  insist(sourceRef, `${where}: requested source-copy but has no source_ref`);
  insist(Number.isInteger(sourceRef.track_index), `${where}.source_ref.track_index: required`);
  const staffIndex = sourceRef.staff_index ?? 0;
  insist(Array.isArray(sourceRef.voice_indices) && sourceRef.voice_indices.length === 1,
    `${where}.source_ref.voice_indices: this slice supports exactly one voice per pattern`);
  insist(Array.isArray(sourceRef.written_master_bars) && sourceRef.written_master_bars.length === 2,
    `${where}.source_ref.written_master_bars: expected [start, end] (1-based, inclusive)`);
  const [startBar, endBar] = sourceRef.written_master_bars;
  insist(Number.isInteger(startBar) && Number.isInteger(endBar) && startBar >= 1 &&
    endBar >= startBar, `${where}.source_ref.written_master_bars: invalid range`);
  const barCount = endBar - startBar + 1;
  validateBarCount(barCount);

  const { score } = resolved;
  const track = score.tracks[sourceRef.track_index];
  insist(track, `${where}.source_ref.track_index: no such track`);
  if (sourceRef.track_name !== undefined) {
    insist(track.name === sourceRef.track_name,
      `${where}.source_ref.track_name: expected "${sourceRef.track_name}" but source track ` +
      `is named "${track.name}"`);
  }
  const staff = track.staves[staffIndex];
  insist(staff, `${where}.source_ref.staff_index: no such staff`);
  insist(staff.bars.length >= endBar,
    `${where}.source_ref.written_master_bars: source has only ${staff.bars.length} bars`);

  const startIndex = startBar - 1;
  const endIndex = endBar - 1;
  const voiceIndex = sourceRef.voice_indices[0];
  const selectedBars = staff.bars.slice(startIndex, endIndex + 1);

  const firstMaster = score.masterBars[startIndex];
  const numerator = firstMaster.timeSignatureNumerator;
  const denominator = firstMaster.timeSignatureDenominator;
  for (let i = startIndex; i <= endIndex; i++) {
    const master = score.masterBars[i];
    insist(master.timeSignatureNumerator === numerator &&
      master.timeSignatureDenominator === denominator,
    `${where}: meter changes within the pattern (written bars ${startBar}-${endBar})`);
    if (i > startIndex) {
      insist(!master.tempoAutomations || master.tempoAutomations.length === 0,
        `${where}: tempo changes within the pattern (written bars ${startBar}-${endBar})`);
    }
  }
  const tempoBpm = findActiveTempo(score, startIndex);
  insist(tempoBpm !== undefined, `${where}: no resolvable tempo for this pattern`);

  const barBeats = [];
  for (const [offset, bar] of selectedBars.entries()) {
    insist(bar.voices.length > voiceIndex, `${where}: selected voice does not exist on bar ${startBar + offset}`);
    for (const [vi, voice] of bar.voices.entries()) {
      if (vi === voiceIndex) continue;
      insist(voice.beats.every((b) => b.notes.length === 0),
        `${where}: bar ${startBar + offset} has an additional active voice ` +
        `(index ${vi}) that this slice cannot represent`);
    }
    const voice = bar.voices[voiceIndex];
    barBeats.push(voice.beats);
  }

  assertNoBoundaryTies(barBeats[0], barBeats[barBeats.length - 1], startIndex, endIndex, where);
  assertNoBoundaryGrace(barBeats[0], barBeats[barBeats.length - 1], startIndex, endIndex, where);
  assertNoBoundaryTuplets(barBeats[0], barBeats[barBeats.length - 1], where);
  assertNoBoundaryNoteEffects(staff, voiceIndex, barBeats, startIndex, endIndex, where);
  for (const [offset, beats] of barBeats.entries()) {
    for (const beat of beats) assertNoUnsupportedEffects(beat, `${where} bar ${startBar + offset}`);
  }

  const kind = staff.isPercussion ? 'percussion' : (staff.showTablature ? 'fretted' : 'pitched');
  const tuningMidi = kind === 'fretted' ? [...staff.stringTuning.tunings].reverse() : null;
  const capoFret = kind === 'fretted' ? staff.capo : null;
  const playback = {
    program: track.playbackInfo.program,
    bank: track.playbackInfo.bank,
    volume: track.playbackInfo.volume,
    balance: track.playbackInfo.balance,
    primaryChannel: track.playbackInfo.primaryChannel,
    secondaryChannel: track.playbackInfo.secondaryChannel,
  };
  for (const [field, value] of Object.entries(playback)) {
    insist(Number.isInteger(value), `${where}: source playback ${field} is not an integer`);
  }
  insist(playback.program >= 0 && playback.program <= 127,
    `${where}: source playback program is outside 0..127`);
  insist(playback.bank >= 0 && playback.bank <= 16383,
    `${where}: source playback bank is outside 0..16383`);
  insist(playback.volume >= 0 && playback.volume <= 16 &&
    playback.balance >= 0 && playback.balance <= 16,
  `${where}: source playback volume/balance is outside 0..16`);
  insist(playback.primaryChannel >= 0 && playback.primaryChannel <= 15 &&
    playback.secondaryChannel >= 0 && playback.secondaryChannel <= 15,
  `${where}: source playback channel is outside 0..15`);
  const sourceColor = typeof track.color?.rgba === 'string' ? track.color.rgba : null;

  const locations = noteLocations(barBeats);
  const bars = barBeats.map((beats) => ({
    beats: beats.map((beat) => ({
      duration: beat.duration,
      dots: beat.dots,
      tupletNumerator: beat.hasTuplet ? beat.tupletNumerator : 1,
      tupletDenominator: beat.hasTuplet ? beat.tupletDenominator : 1,
      notes: beat.notes.map((note) => ({
        ...(kind === 'fretted' ? { string: note.string, fret: note.fret } :
          kind === 'percussion' ? { percussionArticulation: note.percussionArticulation } :
            { octave: note.octave, tone: note.tone }),
        isPalmMute: note.isPalmMute,
        isLetRing: note.isLetRing,
        isHammerPullOrigin: note.isHammerPullOrigin,
        hammerPullDestination:
          destinationCoordinate(note, note.hammerPullDestination, locations),
      })),
    })),
  }));

  return {
    trackName: track.name, kind, tuningMidi, capoFret, playback, sourceColor,
    numerator, denominator, tempoBpm,
    bars, contentFingerprint: fingerprintOf(bars),
  };
}

export function compileSourcePattern(resolved, sourceRef, where) {
  return compileSpan(resolved, sourceRef, where, (barCount) => insist(
    SOURCE_LIMITS.barsPerPattern.includes(barCount),
    `${where}.source_ref.written_master_bars: pattern must span a contiguous ` +
    `${SOURCE_LIMITS.barsPerPattern.join('/')}-bar range (got ${barCount})`));
}

// One-off LIVE/UNRESOLVED-reference span: see the `compileSpan` doc comment above.
export function compileSourceSpan(resolved, sourceRef, where) {
  return compileSpan(resolved, sourceRef, where, (barCount) => insist(
    barCount >= 1 && barCount <= ONE_OFF_SPAN_LIMITS.maxBars,
    `${where}.source_ref.written_master_bars: a one-off source-backed span must be ` +
    `1-${ONE_OFF_SPAN_LIMITS.maxBars} contiguous bars (got ${barCount})`));
}

export function assertFingerprintMatches(compiled, sourceRef, where) {
  if (sourceRef.content_fingerprint === undefined) return;
  insist(compiled.contentFingerprint === sourceRef.content_fingerprint,
    `${where}: resolved source content no longer matches the manifest's content_fingerprint ` +
    '(source bars changed since analysis)');
}
