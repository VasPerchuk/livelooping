// Hermetic, original modern-GP (GP7 zip/GPIF) fixture generator for source-reuse tests.
// Builds a small synthetic score directly from alphaTab model objects (no commercial
// song assets, no copied notation) and exports it with the bundled Gp7Exporter.
// Usage: options JSON on stdin -> .gp bytes on stdout. See `--help` for the option shape.
//
// Options (all optional unless noted):
//   bars: integer total master bars (default 8)
//   numerator/denominator: meter (default 4/4)
//   tempoBpm: constant tempo (default 120)
//   tempoChangeBar: 1-based bar index that gets an additional, different tempo
//     automation (to test a rejected mid-pattern tempo change)
//   meterChangeBar: 1-based bar index that gets a different numerator (numerator + 1)
//     than the rest of the fixture (to test a rejected mid-pattern meter change)
//   graceBar: 1-based bar index whose first beat becomes an on-beat grace note
//   tieIntoBar: 1-based bar index whose first note is flagged `isTieDestination`
//     (simulates a tie entering from the previous bar)
//   secondVoiceBar: 1-based bar index that gets a populated second voice
//     (simulates an unsupported extra active voice)
//   effectBar: 1-based bar index whose first note gets an unsupported ghost-note effect
//   palmMuteBars/letRingBars: arrays of 1-based bars whose first note gets that effect
//   hammerPullOriginBar: 1-based bar whose first note starts a hammer-on/pull-off;
//     alphaTab links it to the next note on the same string
//   drumTrack: include a second, percussion track (default true)
//   guitarProgram/guitarBank/guitarVolume/guitarBalance: playback metadata overrides
import * as alphaTab from '@coderline/alphatab';
import fs from 'node:fs';

const { model } = alphaTab;
const DURATION_BY_DENOMINATOR = { 1: model.Duration.Whole, 2: model.Duration.Half,
  4: model.Duration.Quarter, 8: model.Duration.Eighth, 16: model.Duration.Sixteenth,
  32: model.Duration.ThirtySecond };

function readStdin() {
  const chunks = [];
  const fd = 0;
  const buf = Buffer.alloc(65536);
  // Synchronous stdin read, mirroring gpx_extract.mjs's fs.readFileSync(0) convention.
  return fs.readFileSync(fd);
}

function buildFixtureScore(opts) {
  const numerator = opts.numerator ?? 4;
  const denominator = opts.denominator ?? 4;
  const totalBars = opts.bars ?? 8;
  const tempoBpm = opts.tempoBpm ?? 120;

  const settings = new alphaTab.Settings();
  const score = new model.Score();
  score.title = 'Fixture';

  for (let bi = 0; bi < totalBars; bi++) {
    const master = new model.MasterBar();
    const barNumerator = (opts.meterChangeBar && bi === opts.meterChangeBar - 1)
      ? numerator + 1 : numerator;
    master.timeSignatureNumerator = barNumerator;
    master.timeSignatureDenominator = denominator;
    if (bi === 0) {
      master.tempoAutomations.push(model.Automation.buildTempoAutomation(false, 0, tempoBpm, 2));
    }
    if (opts.tempoChangeBar && bi === opts.tempoChangeBar - 1) {
      master.tempoAutomations.push(
        model.Automation.buildTempoAutomation(false, 0, tempoBpm + 10, 2));
    }
    score.addMasterBar(master);
  }

  const guitar = new model.Track();
  guitar.name = 'Fixture Guitar';
  guitar.playbackInfo.program = opts.guitarProgram ?? 29;
  guitar.playbackInfo.bank = opts.guitarBank ?? 0;
  guitar.playbackInfo.volume = opts.guitarVolume ?? 13;
  guitar.playbackInfo.balance = opts.guitarBalance ?? 7;
  score.addTrack(guitar);
  const guitarStaff = new model.Staff();
  guitarStaff.stringTuning = new model.Tuning('', [64, 59, 55, 50, 45, 40]);
  guitarStaff.capo = 0;
  guitarStaff.showTablature = true;
  guitarStaff.showStandardNotation = true;
  guitar.addStaff(guitarStaff);

  const unitDuration = DURATION_BY_DENOMINATOR[denominator];
  if (unitDuration === undefined) throw new Error(`unsupported fixture denominator ${denominator}`);
  for (let bi = 0; bi < totalBars; bi++) {
    const bar = new model.Bar();
    guitarStaff.addBar(bar);
    const voice = new model.Voice();
    bar.addVoice(voice);
    const barNumber = bi + 1;
    const barNumerator = (opts.meterChangeBar && barNumber === opts.meterChangeBar)
      ? numerator + 1 : numerator;
    const beat = new model.Beat();
    beat.duration = unitDuration;
    if (opts.graceBar && barNumber === opts.graceBar) beat.graceType = model.GraceType.OnBeat;
    const note = new model.Note();
    note.string = 1;
    note.fret = barNumber % 12; // distinguishes every bar deterministically
    if (opts.tieIntoBar && barNumber === opts.tieIntoBar) note.isTieDestination = true;
    if (opts.effectBar && barNumber === opts.effectBar) note.isGhost = true;
    if (opts.palmMuteBars?.includes(barNumber)) note.isPalmMute = true;
    if (opts.letRingBars?.includes(barNumber)) note.isLetRing = true;
    if (opts.hammerPullOriginBar === barNumber) note.isHammerPullOrigin = true;
    beat.addNote(note);
    voice.addBeat(beat);
    const unitsLeft = barNumerator - 1; // remaining same-duration beats filling the bar
    for (let i = 0; i < unitsLeft; i++) {
      const rest = new model.Beat();
      rest.duration = unitDuration;
      voice.addBeat(rest);
    }
    // alphaTab expects a uniform voice count across every bar of a staff (it chains
    // beats per voice index across the whole staff); once any bar needs a second voice,
    // every bar gets one, with an all-rest voice on the bars that do not need real notes.
    if (opts.secondVoiceBar) {
      const voice2 = new model.Voice();
      bar.addVoice(voice2);
      if (barNumber === opts.secondVoiceBar) {
        const b2 = new model.Beat();
        b2.duration = unitDuration;
        const n2 = new model.Note();
        n2.string = 2;
        n2.fret = 1;
        b2.addNote(n2);
        voice2.addBeat(b2);
        for (let i = 0; i < unitsLeft; i++) {
          const rest2 = new model.Beat();
          rest2.duration = unitDuration;
          voice2.addBeat(rest2);
        }
      } else {
        for (let i = 0; i < barNumerator; i++) {
          const rest2 = new model.Beat();
          rest2.duration = unitDuration;
          voice2.addBeat(rest2);
        }
      }
    }
  }

  if (opts.drumTrack !== false) {
    const drums = new model.Track();
    drums.name = 'Fixture Drums';
    score.addTrack(drums);
    const drumStaff = new model.Staff();
    drumStaff.stringTuning = new model.Tuning('', []);
    drumStaff.showTablature = false;
    drumStaff.showStandardNotation = true;
    drumStaff.isPercussion = true;
    drums.addStaff(drumStaff);
    drums.playbackInfo.primaryChannel = 9;
    for (let bi = 0; bi < totalBars; bi++) {
      const bar = new model.Bar();
      drumStaff.addBar(bar);
      const voice = new model.Voice();
      bar.addVoice(voice);
      const beat = new model.Beat();
      beat.duration = unitDuration;
      const note = new model.Note();
      note.percussionArticulation = 35 + ((bi + 1) % 4);
      beat.addNote(note);
      voice.addBeat(beat);
      const barNumerator = (opts.meterChangeBar && bi + 1 === opts.meterChangeBar)
        ? numerator + 1 : numerator;
      const unitsLeft = barNumerator - 1;
      for (let i = 0; i < unitsLeft; i++) {
        const rest = new model.Beat();
        rest.duration = unitDuration;
        voice.addBeat(rest);
      }
    }
  }

  score.finish(settings);
  return { score, settings };
}

function main() {
  const raw = readStdin();
  const opts = raw.length ? JSON.parse(raw.toString('utf8')) : {};
  const { score, settings } = buildFixtureScore(opts);
  const bytes = new alphaTab.exporter.Gp7Exporter().export(score, settings);
  process.stdout.write(Buffer.from(bytes));
}

try {
  main();
} catch (error) {
  console.error(String(error?.message || error).slice(0, 500));
  process.exitCode = 1;
}
