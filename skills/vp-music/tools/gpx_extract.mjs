import * as alphaTab from '@coderline/alphatab';
import fs from 'node:fs';

const MAX_ITEMS = 100_000;
const check = (condition, message) => {
  if (!condition) throw new Error(message);
};

try {
  const input = fs.readFileSync(0);
  check(input.length <= 16 * 1024 * 1024, 'GPX exceeds input limit');
  const score = alphaTab.importer.ScoreLoader.loadScoreFromBytes(
    new Uint8Array(input), new alphaTab.Settings()
  );
  check(score?.masterBars?.length > 0 && score.masterBars.length <= 10_000,
    'GPX has no usable master bars');
  check(score?.tracks?.length > 0 && score.tracks.length <= 256,
    'GPX has no usable tracks');

  const bars = [];
  const tempos = [];
  for (const master of score.masterBars) {
    const numerator = master.timeSignatureNumerator;
    const denominator = master.timeSignatureDenominator;
    check(Number.isInteger(numerator) && numerator > 0 && numerator <= 64 &&
      [1, 2, 4, 8, 16, 32].includes(denominator), 'invalid GPX meter');
    const start = master.start / 960;
    const nominal = numerator * 4 / denominator;
    const next = master.nextMasterBar?.start / 960;
    const length = Number.isFinite(next) && next > start ? next - start : nominal;
    check(Number.isFinite(start) && start >= 0 && length > 0 && length <= 64,
      'invalid GPX bar length');
    bars.push({
      number: bars.length + 1, start_quarters: start,
      length_quarters: length, meter: `${numerator}/${denominator}`,
      repeat_open: !!master.isRepeatStart,
      repeat_close: master.repeatCount > 0 ? master.repeatCount : null
    });
    for (const automation of master.tempoAutomations) {
      const ratio = automation.ratioPosition;
      check(Number.isFinite(ratio) && ratio >= 0 && ratio <= 1 &&
        Number.isFinite(automation.value) && automation.value > 0 &&
        automation.value <= 1000, 'invalid GPX tempo automation');
      tempos.push({
        position_quarters: start + length * ratio,
        bpm: automation.value
      });
      check(tempos.length <= MAX_ITEMS, 'too many GPX tempo changes');
    }
  }

  const tracks = [];
  let visited = 0;
  let noteCount = 0;
  for (const track of score.tracks) {
    const notes = [];
    const staves = [];
    for (const staff of track.staves) {
      check(staff.bars.length <= bars.length, 'GPX track exceeds master bars');
      const pitches = staff.stringTuning?.tunings;
      const tuning = Array.isArray(pitches) && pitches.length > 0 &&
        pitches.length <= 16 && pitches.some(v => v !== 0) &&
        pitches.every(v => Number.isInteger(v) && v >= 0 && v <= 127)
        ? [...pitches].reverse() : null;
      staves.push({
        number: staff.index + 1,
        tuning_midi: tuning,
        capo_fret: Number.isInteger(staff.capo) &&
          staff.capo >= 0 && staff.capo <= 36 ? staff.capo : null,
        percussion: !!staff.isPercussion
      });
      for (const bar of staff.bars) {
        const master = bars[bar.index];
        check(master !== undefined, 'GPX references absent master bar');
        for (const voice of bar.voices) {
          for (const beat of voice.beats) {
            visited++;
            check(visited <= MAX_ITEMS, 'too many GPX beats');
            for (const note of beat.notes) {
              check(Number.isFinite(beat.displayStart) &&
                Number.isFinite(beat.displayDuration) && beat.displayDuration >= 0,
                'invalid GPX beat timing');
              notes.push({
                bar: master.number,
                staff: staff.index + 1,
                pitch: Number.isInteger(note.realValue) &&
                  note.realValue >= 0 && note.realValue <= 127 ? note.realValue : null,
                string: note.string > 0 ? note.string : null,
                fret: note.fret >= 0 ? note.fret : null,
                start_quarters: master.start_quarters + beat.displayStart / 960,
                duration_quarters: beat.displayDuration / 960
              });
              noteCount++;
              check(noteCount <= MAX_ITEMS, 'too many GPX notes');
            }
          }
        }
      }
    }
    tracks.push({name: String(track.name || `Track ${tracks.length + 1}`).slice(0, 200),
      staves, notes});
  }
  process.stdout.write(JSON.stringify({tracks, bars, tempos}));
} catch (error) {
  console.error(String(error?.message || error).slice(0, 300));
  process.exitCode = 1;
}
