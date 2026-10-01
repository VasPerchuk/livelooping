// Literal GP7 arrangement writer. Requires Node.js, Python and local alphaTab 1.8.4.
// Usage: node skills\vp-music\tools\write_arrangement.mjs --manifest <song>\.vp-music-tracking\arrangement\arrangement.json --song-dir <song> --output <song>\arrangement.gp
// Manifest location is the same for new and legacy song folders; no song layout migration.
// --worksheet <path> may replace --song-dir for explicit worksheet/testing paths.
// Published Final worksheet structure/legend and actual .patterns.md headings must match before export.
// v1 supports fixed integer tempo/meter, literal pattern cycles per section, single-staff
// fretted/pitched/percussion tracks, chords of equal-duration notes and 64th-note grid.
// Notes cannot overlap in one track or cross a bar; ties, tuplets and tempo/meter
// changes are not representable. "live" sections contain unnotated (silent) score bars.
// source/confidence on tracks or patterns are validated descriptive strings, not GP fields.
import * as alphaTab from '@coderline/alphatab';
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const { model } = alphaTab;
const LIMITS = { bytes: 1_000_000, tracks: 32, patterns: 256, sections: 128,
  bars: 512, notes: 20_000, beats: 50_000 };
const UNITS = [64, 32, 16, 8, 4, 2, 1]; // 1/16 of a quarter note; whole through 64th
const DURATIONS = new Map(UNITS.map((units, index) =>
  [units, [model.Duration.Whole, model.Duration.Half, model.Duration.Quarter,
    model.Duration.Eighth, model.Duration.Sixteenth, model.Duration.ThirtySecond,
    model.Duration.SixtyFourth][index]]));
const insist = (yes, message) => { if (!yes) throw new Error(message); };
const keys = (obj, allowed, where) => {
  insist(obj && typeof obj === 'object' && !Array.isArray(obj), `${where}: expected object`);
  for (const key of Object.keys(obj)) {
    if (allowed.includes(key)) continue;
    const normalized = key.replace(/[^a-z]/gi, '').toLowerCase();
    insist(!(/^(tempo|meter|timesignature)(map|events?|changes?)$/.test(normalized) ||
      /^(events?|eventmaps?)$/.test(normalized)),
    `${where}: ${key} is an event map/change list; v1 requires constant tempo and meter`);
    insist(false, `${where}: unsupported field ${key}`);
  }
};
const integer = (n, min, max, where) => {
  insist(Number.isSafeInteger(n) && n >= min && n <= max, `${where}: expected integer ${min}..${max}`);
  return n;
};
const text = (s, where) => {
  insist(typeof s === 'string' && s.length > 0 && s.length <= 100 &&
    s.trim() === s && !/[\u0000-\u001f\u007f]/u.test(s), `${where}: invalid name`);
  return s;
};
const metadata = (s, where, max) => {
  insist(typeof s === 'string' && s.length > 0 && s.length <= max &&
    s.trim() === s && !/[\u0000-\u001f\u007f]/u.test(s),
  `${where}: expected nonempty descriptive text (at most ${max} characters)`);
};
const units = (n, where) => {
  insist(typeof n === 'number' && Number.isFinite(n) && n >= 0 &&
    n * 16 <= 65536 && Number.isInteger(n * 16), `${where}: expected nonnegative 1/16-quarter grid`);
  return n * 16;
};

function parseManifest(raw) {
  keys(raw, ['version', 'tempo_bpm', 'meter', 'tracks', 'sections',
    'publication', 'performance'], 'manifest');
  insist(raw.version === 1, 'only manifest version 1 is supported');
  let publication = { status: 'final', limitations: [], consent: null };
  if (raw.publication !== undefined) {
    keys(raw.publication, ['status', 'limitations', 'consent'], 'publication');
    insist(['final', 'partial'].includes(raw.publication.status),
      'publication.status: expected final or partial');
    insist(Array.isArray(raw.publication.limitations) &&
      raw.publication.limitations.length <= 32,
    'publication.limitations: expected array of at most 32 items');
    raw.publication.limitations.forEach((item, i) =>
      metadata(item, `publication.limitations[${i}]`, 500));
    if (raw.publication.status === 'partial') {
      insist(raw.publication.limitations.length > 0,
        'partial publication requires limitations');
      metadata(raw.publication.consent, 'publication.consent', 2000);
    } else {
      insist(raw.publication.limitations.length === 0,
        'final publication cannot have limitations');
      insist(raw.publication.consent === undefined,
        'final publication does not accept partial consent');
    }
    publication = {
      status: raw.publication.status,
      limitations: [...raw.publication.limitations],
      consent: raw.publication.consent ?? null
    };
  }
  if (raw.performance !== undefined) {
    keys(raw.performance, ['players', 'roles', 'transitions'], 'performance');
    if (raw.performance.players !== undefined) {
      integer(raw.performance.players, 1, 32, 'performance.players');
    }
    for (const field of ['roles', 'transitions']) {
      if (raw.performance[field] === undefined) continue;
      insist(Array.isArray(raw.performance[field]) && raw.performance[field].length <= 128,
        `performance.${field}: expected array of at most 128 descriptions`);
      raw.performance[field].forEach((item, i) =>
        metadata(item, `performance.${field}[${i}]`, 500));
    }
  }
  insist(Number.isSafeInteger(raw.tempo_bpm) &&
    raw.tempo_bpm >= 20 && raw.tempo_bpm <= 400, 'tempo_bpm: expected integer 20..400');
  const match = typeof raw.meter === 'string' && /^([1-9]\d?)\/(1|2|4|8|16|32)$/.exec(raw.meter);
  insist(match && Number(match[1]) <= 16, 'meter: expected n/d, n=1..16, d=1,2,4,8,16,32');
  const barUnits = Number(match[1]) * 64 / Number(match[2]);
  insist(Number.isInteger(barUnits), 'meter: bar must fit 1/16-quarter grid');
  insist(Array.isArray(raw.tracks) && raw.tracks.length > 0 &&
    raw.tracks.length <= LIMITS.tracks, 'tracks: missing or too many');
  insist(Array.isArray(raw.sections) && raw.sections.length > 0 &&
    raw.sections.length <= LIMITS.sections, 'sections: missing or too many');
  const names = new Set();
  const trackNames = new Set();
  const tracks = raw.tracks.map((track, ti) => {
    const where = `tracks[${ti}]`;
    keys(track, ['name', 'kind', 'tuning_midi', 'capo_fret', 'patterns',
      'source', 'confidence'], where);
    text(track.name, `${where}.name`);
    if (track.source !== undefined) metadata(track.source, `${where}.source`, 2000);
    if (track.confidence !== undefined) metadata(track.confidence, `${where}.confidence`, 100);
    insist(!trackNames.has(track.name), `${where}: duplicate track name`);
    trackNames.add(track.name);
    insist(['fretted', 'percussion', 'pitched'].includes(track.kind), `${where}: invalid kind`);
    if (track.kind === 'fretted') {
      insist(Array.isArray(track.tuning_midi) && track.tuning_midi.length >= 1 &&
        track.tuning_midi.length <= 12, `${where}: fretted tuning_midi required (1..12 strings)`);
      track.tuning_midi.forEach((p, i) => integer(p, 1, 127, `${where}.tuning_midi[${i}]`));
      insist(track.tuning_midi.every((p, i) => !i || p > track.tuning_midi[i - 1]),
        `${where}: tuning_midi must be strictly ascending (low to high)`);
      integer(track.capo_fret, 0, 36, `${where}.capo_fret`);
    } else {
      insist(track.tuning_midi === undefined && track.capo_fret === undefined,
        `${where}: tuning/capo only apply to fretted tracks`);
    }
    insist(Array.isArray(track.patterns) && track.patterns.length <= LIMITS.patterns,
      `${where}.patterns: expected array of at most ${LIMITS.patterns}`);
    const patterns = track.patterns.map((pattern, pi) => {
      const label = `${where}.patterns[${pi}]`;
      keys(pattern, ['name', 'bars', 'notes', 'source', 'confidence'], label);
      text(pattern.name, `${label}.name`);
      metadata(pattern.source, `${label}.source`, 2000);
      insist(['confirmed', 'likely', 'estimated'].includes(pattern.confidence),
        `${label}.confidence: scored events require confirmed, likely or estimated evidence`);
      insist(!names.has(pattern.name), `${label}: pattern names must be globally unique`);
      names.add(pattern.name);
      integer(pattern.bars, 1, LIMITS.bars, `${label}.bars`);
      insist(Array.isArray(pattern.notes) && pattern.notes.length <= LIMITS.notes,
        `${label}.notes: expected bounded array`);
      const notes = pattern.notes.map((note, ni) => {
        const at = `${label}.notes[${ni}]`;
        keys(note, ['bar', 'offset_quarters', 'duration_quarters', 'pitch', 'string', 'fret'], at);
        integer(note.bar, 1, pattern.bars, `${at}.bar`);
        const start = units(note.offset_quarters, `${at}.offset_quarters`);
        const duration = units(note.duration_quarters, `${at}.duration_quarters`);
        insist(duration > 0 && DURATIONS.has(duration), `${at}: duration must be a single undotted whole..64th note`);
        insist(start < barUnits && start + duration <= barUnits,
          `${at}: note must fit inside its local bar (ties and cross-bar notes unsupported)`);
        if (track.kind === 'fretted') {
          integer(note.string, 1, track.tuning_midi.length, `${at}.string`);
          integer(note.fret, 0, 36, `${at}.fret`);
          const pitch = track.tuning_midi[note.string - 1] + note.fret + track.capo_fret;
          insist(pitch <= 127, `${at}: resulting MIDI pitch exceeds 127`);
          if (note.pitch !== undefined) integer(note.pitch, 0, 127, `${at}.pitch`);
          insist(note.pitch === undefined || note.pitch === pitch,
            `${at}: pitch disagrees with tuning + capo + fret`);
          return { bar: note.bar, start, duration, pitch, string: note.string, fret: note.fret };
        }
        insist(note.string === undefined && note.fret === undefined,
          `${at}: ${track.kind} notes cannot have strings or frets`);
        integer(note.pitch, 0, 127, `${at}.pitch`);
        return { bar: note.bar, start, duration, pitch: note.pitch };
      });
      return { name: pattern.name, bars: pattern.bars, notes };
    });
    return { ...track, patterns };
  });
  let totalBars = 0;
  const sections = raw.sections.map((section, si) => {
    const where = `sections[${si}]`;
    keys(section, ['name', 'bars', 'patterns', 'tail_policy', 'empty_policy'], where);
    text(section.name, `${where}.name`);
    integer(section.bars, 1, LIMITS.bars, `${where}.bars`);
    totalBars += section.bars;
    insist(totalBars <= LIMITS.bars, `arrangement exceeds ${LIMITS.bars} bars`);
    insist(Array.isArray(section.patterns), `${where}.patterns: expected array`);
    insist(new Set(section.patterns).size === section.patterns.length,
      `${where}: duplicate active pattern`);
    insist(section.patterns.every(p => typeof p === 'string' && names.has(p)),
      `${where}: unknown pattern name`);
    insist(section.tail_policy === undefined || section.tail_policy === 'truncate',
      `${where}: only tail_policy=truncate supported`);
    insist(section.empty_policy === undefined ||
      ['rest', 'live'].includes(section.empty_policy), `${where}: invalid empty_policy`);
    insist(section.patterns.length || section.empty_policy,
      `${where}: empty patterns require empty_policy=rest or live`);
    insist(section.patterns.length === 0 || section.empty_policy === undefined,
      `${where}: empty_policy applies only when no patterns are active`);
    insist(section.patterns.length > 0 || section.tail_policy === undefined,
      `${where}: tail_policy without patterns`);
    return section;
  });
  const patternLookup = new Map(tracks.flatMap((t, ti) =>
    t.patterns.map(p => [p.name, { ...p, track: ti }])));
  let noteCount = 0;
  const events = tracks.map(() => Array.from({ length: totalBars }, () => []));
  let barIndex = 0;
  for (const section of sections) {
    for (const patternName of section.patterns) {
      const pattern = patternLookup.get(patternName);
      insist(pattern.notes.length > 0,
        `${section.name}: active pattern ${patternName} has no scored notes`);
      insist(section.bars % pattern.bars === 0 || section.tail_policy === 'truncate',
        `${section.name}: ${patternName} needs tail_policy=truncate for partial cycle`);
      for (let i = 0; i < section.bars; i++) {
        const localBar = i % pattern.bars + 1;
        for (const note of pattern.notes) if (note.bar === localBar) {
          events[pattern.track][barIndex + i].push(note);
          insist(++noteCount <= LIMITS.notes, `arrangement exceeds ${LIMITS.notes} notes`);
        }
      }
    }
    barIndex += section.bars;
  }
  insist(noteCount > 0, 'arrangement must contain notes for music_assets.py validate');
  for (const [ti, trackEvents] of events.entries()) for (const [bi, notes] of trackEvents.entries()) {
    notes.sort((a, b) => a.start - b.start || a.pitch - b.pitch);
    let end = 0;
    for (let j = 0; j < notes.length;) {
      const { start, duration } = notes[j];
      insist(start >= end, `tracks[${ti}] bar ${bi + 1}: overlapping notes require unsupported voices`);
      const identities = new Set();
      while (j < notes.length && notes[j].start === start) {
        const note = notes[j++];
        insist(note.duration === duration, `tracks[${ti}] bar ${bi + 1}: chord durations differ`);
        const identity = note.string ?? note.pitch;
        insist(!identities.has(identity), `tracks[${ti}] bar ${bi + 1}: duplicate chord string/pitch`);
        identities.add(identity);
      }
      end = start + duration;
    }
  }
  insist(totalBars * tracks.length * barUnits <= LIMITS.beats * 64,
    'arrangement exceeds beat budget');
  return { tracks, sections, events, totalBars, barUnits, tempo: raw.tempo_bpm,
    publication,
    numerator: Number(match[1]), denominator: Number(match[2]), meter: raw.meter };
}

function readMarkdown(file) {
  insist(fs.statSync(file).size <= 256_000, `${file}: worksheet/pattern file exceeds 256 KB`);
  return fs.readFileSync(file, 'utf8').replace(/\r\n?/g, '\n');
}

function syncWorksheet(spec, worksheet) {
  const songDir = path.dirname(worksheet);
  const sheet = readMarkdown(worksheet);
  const statuses = [...sheet.matchAll(/^\*\*Status:\s*(.*?)\*\*\s*$/gm)];
  const expectedStatus = spec.publication.status === 'partial' ? 'Partial' : 'Final';
  insist(statuses.length === 1 && statuses[0][1].trim() === expectedStatus,
    `published worksheet must have exactly one **Status: ${expectedStatus}**`);
  const limitationLines = [...sheet.matchAll(/^\*\*Limitations:\*\*\s*(\S.*?)\s*$/gm)];
  if (spec.publication.status === 'partial') {
    insist(limitationLines.length === 1,
      'Partial worksheet must have exactly one nonempty **Limitations:** warning');
    for (const limitation of spec.publication.limitations) {
      insist(limitationLines[0][1].includes(limitation),
        `worksheet limitations omit manifest limitation: ${limitation}`);
    }
  }
  const tempo = /\*\*Tempo:\*\*\s*(?:(dotted-quarter|quarter)\s*(?:[=:]\s*)?)?(\d+(?:\.\d+)?)\s*BPM/i.exec(sheet);
  const meter = /\*\*(?:Meter|Time signature):\*\*\s*(\d+)\/(\d+)/i.exec(sheet);
  insist(tempo && tempo[1]?.toLowerCase() !== 'dotted-quarter' &&
    Number(tempo[2]) === spec.tempo && meter &&
    `${Number(meter[1])}/${Number(meter[2])}` === spec.meter,
  'worksheet tempo/meter differs from constant quarter-note manifest or is missing');
  const heading = (label) => {
    const matches = [...sheet.matchAll(new RegExp(`^## ${label}(?:\\s*\\([^\\n]*\\))?\\s*$`, 'gm'))];
    insist(matches.length === 1, `worksheet must contain exactly one ## ${label} heading`);
    const start = matches[0].index + matches[0][0].length;
    const next = /^## /gm;
    next.lastIndex = start;
    return sheet.slice(start, next.exec(sheet)?.index ?? sheet.length);
  };
  const legend = heading('Patterns');
  const structure = heading('Structure');
  const legendNames = [...legend.matchAll(/^-\s+\*\*([^*\n]+)\*\*/gm)].map(m => m[1].trim());
  insist(legendNames.length > 0 && new Set(legendNames).size === legendNames.length,
    'worksheet pattern legend is missing or has duplicate names');
  const legendHeading = sheet.match(/^## Patterns(?:\s*\((\d+)(?:\s+\w+)*\s+total\))?\s*$/m);
  insist(legendHeading && (!legendHeading[1] ||
    Number(legendHeading[1]) === legendNames.length),
    'worksheet legend count differs from listed patterns');
  const defined = spec.tracks.flatMap(t => t.patterns.map(p => p.name));
  insist(legendNames.length === defined.length &&
    legendNames.every(n => defined.includes(n)),
  'manifest pattern names differ from published worksheet legend');
  const sectionHeadings = [...structure.matchAll(/^###\s+(.+?)\s*$/gm)];
  insist(sectionHeadings.length === spec.sections.length,
    'manifest section count differs from published worksheet');
  for (const [i, found] of sectionHeadings.entries()) {
    const parsed = /^(.+?)\s+[-—–]\s+(\d+)\s+bars?\s*\(\s*~?\s*\d+(?:\.\d+)?\s*(?:seconds|s)\b/i.exec(found[1]);
    insist(parsed, `worksheet section ${i + 1}: expected "Name — N bars (~Ns)" heading`);
    const section = spec.sections[i];
    insist(parsed[1].trim() === section.name && Number(parsed[2]) === section.bars,
      `worksheet section ${i + 1}: name/bar count differs from manifest`);
    const bodyStart = found.index + found[0].length;
    const bodyEnd = sectionHeadings[i + 1]?.index ?? structure.length;
    const active = [...structure.slice(bodyStart, bodyEnd)
      .matchAll(/^(?:\*\*)?Patterns:(?:\*\*)?\s*(.*?)\s*$/gm)];
    insist(active.length === 1, `worksheet section ${i + 1}: expected exactly one Patterns line`);
    const actual = /^none\.?$/i.test(active[0][1].trim())
      ? [] : active[0][1].split(',').map(n =>
        n.replace(/\s*\([^)]*\)/g, '').trim().replace(/\.$/, ''));
    insist(actual.every(n => n && legendNames.includes(n)) &&
      JSON.stringify(actual) === JSON.stringify(section.patterns),
    `worksheet section ${i + 1}: active patterns differ from manifest`);
  }

  const tracking = path.join(songDir, '.vp-music-tracking', 'pattern');
  const patternFiles = dir => fs.existsSync(dir)
    ? fs.readdirSync(dir).filter(name => name.endsWith('.patterns.md'))
      .map(name => path.join(dir, name)) : [];
  const rootFiles = patternFiles(songDir);
  const trackedFiles = patternFiles(tracking);
  insist(!(rootFiles.length && trackedFiles.length),
    'song has both root and tracking pattern files; layout is ambiguous');
  const files = trackedFiles.length ? trackedFiles : rootFiles;
  insist(files.length > 0, 'published worksheet requires active .patterns.md files');
  const names = files.flatMap(file =>
    [...readMarkdown(file).matchAll(/^## Pattern:\s*(.+?)\s*$/gm)].map(m => m[1].trim()));
  insist(new Set(names).size === names.length,
    'active .patterns.md headings contain duplicates');
  if (spec.publication.status === 'partial') {
    insist(legendNames.every(n => names.includes(n)),
      'Partial worksheet/manifest pattern is missing from active .patterns.md headings');
  } else {
    insist(names.length === legendNames.length && names.every(n => legendNames.includes(n)),
      'active .patterns.md headings differ from published worksheet legend/manifest');
  }
}

function build(spec) {
  const settings = new alphaTab.Settings();
  const score = new model.Score();
  const partial = spec.publication.status === 'partial';
  score.title = partial ? 'Arrangement (Partial)' : 'Arrangement';
  score.subTitle = partial ? 'PARTIAL - unreliable live-loop reduction' :
    'FINAL live-loop reduction';
  score.notices = partial ? spec.publication.limitations.join('; ') : '';
  const boundaries = new Map();
  let next = 0;
  for (const section of spec.sections) {
    boundaries.set(next, section);
    next += section.bars;
  }
  for (let bi = 0; bi < spec.totalBars; bi++) {
    const master = new model.MasterBar();
    master.timeSignatureNumerator = spec.numerator;
    master.timeSignatureDenominator = spec.denominator;
    if (bi === 0) master.tempoAutomations.push(
      model.Automation.buildTempoAutomation(false, 0, spec.tempo, 2));
    if (boundaries.has(bi)) {
      const section = boundaries.get(bi);
      const marker = new model.Section();
      marker.marker = section.empty_policy ?? section.name;
      marker.text = section.name;
      master.section = marker;
    }
    score.addMasterBar(master);
  }
  let beatCount = 0;
  for (const [ti, trackSpec] of spec.tracks.entries()) {
    const track = new model.Track();
    track.name = trackSpec.name;
    score.addTrack(track);
    const staff = new model.Staff();
    if (trackSpec.kind === 'fretted') {
      staff.stringTuning = new model.Tuning('', [...trackSpec.tuning_midi].reverse());
      staff.capo = trackSpec.capo_fret;
      staff.showTablature = true;
    } else {
      staff.stringTuning = new model.Tuning('', []);
      staff.showTablature = false;
      staff.isPercussion = trackSpec.kind === 'percussion';
      if (staff.isPercussion) track.playbackInfo.primaryChannel = 9;
    }
    staff.showStandardNotation = true;
    track.addStaff(staff);
    for (const notes of spec.events[ti]) {
      const bar = new model.Bar();
      staff.addBar(bar);
      const voice = new model.Voice();
      bar.addVoice(voice);
      const addBeat = (duration, chord = []) => {
        insist(++beatCount <= LIMITS.beats, `arrangement exceeds ${LIMITS.beats} beats`);
        const beat = new model.Beat();
        beat.duration = DURATIONS.get(duration);
        for (const source of chord) {
          const note = new model.Note();
          if (trackSpec.kind === 'fretted') {
            note.string = source.string;
            note.fret = source.fret;
          } else {
            note.octave = Math.floor(source.pitch / 12);
            note.tone = source.pitch % 12;
            if (trackSpec.kind === 'percussion') note.percussionArticulation = source.pitch;
          }
          beat.addNote(note);
        }
        voice.addBeat(beat);
      };
      const rest = amount => {
        while (amount) {
          const step = UNITS.find(u => u <= amount);
          insist(step !== undefined, 'unrepresentable rest');
          addBeat(step);
          amount -= step;
        }
      };
      let cursor = 0;
      for (let i = 0; i < notes.length;) {
        const onset = notes[i].start;
        rest(onset - cursor);
        const duration = notes[i].duration;
        const chord = [];
        while (i < notes.length && notes[i].start === onset) chord.push(notes[i++]);
        addBeat(duration, chord);
        cursor = onset + duration;
      }
      rest(spec.barUnits - cursor);
    }
  }
  score.finish(settings);
  return { score, settings };
}

function inspect(file) {
  const script = path.join(path.dirname(fileURLToPath(import.meta.url)), 'music_assets.py');
  const args = [script, 'inspect', '--path', file];
  return JSON.parse(runPython(args,
    { encoding: 'utf8', timeout: 60_000, maxBuffer: 16 * 1024 * 1024 }));
}
function runPython(args, options) {
  try {
    return execFileSync('python', args, options);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    return execFileSync('py', ['-3', ...args], options);
  }
}

function verify(spec, staged) {
  const actual = inspect(staged);
  insist(actual.status === 'valid' && actual.format === 'gp-zip',
    `project inspector rejected GP7: ${actual.error || actual.status}`);
  insist(actual.metadata.note_count > 0, 'project validator requires note content');
  insist(actual.track_count === spec.tracks.length && actual.bar_count === spec.totalBars,
    'round-trip track/bar count differs');
  insist(actual.bars.every((b, i) => b.number === i + 1 &&
    b.meter === spec.meter && Math.abs(b.start_quarters - i * spec.barUnits / 16) < 1e-9 &&
    b.length_quarters === spec.barUnits / 16 && !b.repeat_open && b.repeat_close === null),
  'round-trip bar/meter/repeats differ');
  insist(actual.tempos.length === 1 && actual.tempos[0].position_quarters === 0 &&
    Math.abs(actual.tempos[0].bpm - spec.tempo) < 1e-9, 'round-trip tempo differs');
  for (const [ti, track] of spec.tracks.entries()) {
    const got = actual.tracks[ti];
    insist(got.name === track.name && got.staves.length === 1, `track ${ti}: identity differs`);
    const staff = got.staves[0];
    if (track.kind === 'fretted') insist(
      JSON.stringify(staff.tuning_midi) === JSON.stringify(track.tuning_midi) &&
      staff.capo_fret === track.capo_fret, `track ${ti}: tuning/capo differs`);
    else insist(staff.tuning_midi === null, `track ${ti}: non-fretted staff acquired tuning`);
    const expected = spec.events[ti].flatMap((bar, bi) => bar.map(n => ({
      bar: bi + 1, pitch: n.pitch, string: n.string ?? null, fret: n.fret ?? null,
      start: bi * spec.barUnits / 16 + n.start / 16, duration: n.duration / 16
    }))).sort(eventSort);
    const received = got.notes.map(n => ({
      bar: n.bar, pitch: n.pitch, string: n.string, fret: n.fret,
      start: n.start_quarters, duration: n.duration_quarters
    })).sort(eventSort);
    insist(received.length === expected.length, `track ${ti}: round-trip note count differs`);
    for (let i = 0; i < expected.length; i++) {
      const a = received[i], b = expected[i];
      insist(a.bar === b.bar && a.pitch === b.pitch && a.string === b.string &&
        a.fret === b.fret && Math.abs(a.start - b.start) < 1e-9 &&
        Math.abs(a.duration - b.duration) < 1e-9, `track ${ti} event ${i}: round-trip note differs`);
    }
  }
  // The GPIF inspector does not expose section markers or the percussion flag.
  const parsed = alphaTab.importer.ScoreLoader.loadScoreFromBytes(
    new Uint8Array(fs.readFileSync(staged)), new alphaTab.Settings());
  const partial = spec.publication.status === 'partial';
  insist(parsed.title === (partial ? 'Arrangement (Partial)' : 'Arrangement') &&
    parsed.subTitle === (partial ? 'PARTIAL - unreliable live-loop reduction' :
      'FINAL live-loop reduction') &&
    parsed.notices === (partial ? spec.publication.limitations.join('; ') : ''),
  'round-trip publication status/limitations differ');
  insist(parsed.tracks.every((t, i) =>
    t.staves.length === 1 && t.staves[0].isPercussion === (spec.tracks[i].kind === 'percussion')),
  'round-trip percussion status differs');
  let start = 0;
  for (const section of spec.sections) {
    insist(parsed.masterBars[start].section?.text === section.name &&
      parsed.masterBars[start].section.marker === (section.empty_policy ?? section.name),
      `round-trip section ${section.name} differs`);
    start += section.bars;
  }
}
const eventSort = (a, b) => a.start - b.start || a.pitch - b.pitch ||
  (a.string ?? 0) - (b.string ?? 0);

function main(args) {
  insist(args.length === 6 &&
    args.every((a, i) => i % 2 === 0 ? a.startsWith('--') : !!a && !a.startsWith('--')) &&
    new Set([args[0], args[2], args[4]]).size === 3 &&
    args.filter((_, i) => i % 2 === 0).includes('--manifest') &&
    args.filter((_, i) => i % 2 === 0).includes('--output') &&
    (args.filter((_, i) => i % 2 === 0).includes('--song-dir') ||
      args.filter((_, i) => i % 2 === 0).includes('--worksheet')),
  'usage: node write_arrangement.mjs --manifest <json> --song-dir <folder> --output <gp> (or --worksheet <path>)');
  const options = Object.fromEntries([[args[0], args[1]], [args[2], args[3]], [args[4], args[5]]]);
  const manifest = path.resolve(options['--manifest']);
  const output = path.resolve(options['--output']);
  const worksheet = options['--song-dir']
    ? path.join(path.resolve(options['--song-dir']), 'worksheet.md')
    : path.resolve(options['--worksheet']);
  insist(path.extname(output).toLowerCase() === '.gp', 'output must end in .gp');
  insist(manifest !== output && !fs.existsSync(output), 'output already exists');
  insist(fs.statSync(manifest).size <= LIMITS.bytes, 'manifest exceeds 1 MB');
  const input = fs.readFileSync(manifest);
  const spec = parseManifest(JSON.parse(input.toString('utf8')));
  syncWorksheet(spec, worksheet);
  const { score, settings } = build(spec);
  const bytes = new alphaTab.exporter.Gp7Exporter().export(score, settings);
  insist(bytes.length > 0 && bytes.length <= 16 * 1024 * 1024, 'export exceeds 16 MiB');
  const stage = path.join(path.dirname(output), `.${path.basename(output)}.${randomUUID()}.stage.gp`);
  try {
    fs.writeFileSync(stage, bytes, { flag: 'wx' });
    verify(spec, stage);
    insist(!fs.existsSync(output), 'output already exists');
    // Python's os.rename on Windows refuses an existing destination, unlike Node's
    // replace-on-rename behavior. On other platforms hard-link publication is no-clobber.
    if (process.platform === 'win32') {
      runPython(['-c', 'import os,sys; os.rename(sys.argv[1],sys.argv[2])',
        stage, output], { timeout: 10_000 });
    } else {
      fs.linkSync(stage, output);
    }
    console.log(output);
  } finally {
    if (fs.existsSync(stage)) fs.unlinkSync(stage);
  }
}

try {
  main(process.argv.slice(2));
} catch (error) {
  console.error(`arrangement: ${String(error.message || error)}`);
  process.exitCode = 1;
}
