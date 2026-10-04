import fs from 'node:fs';
import path from 'node:path';
import {
  SourceResolver,
  assertFingerprintMatches,
  compileSourcePattern,
  compileSourceSpan,
} from './source_compiler.mjs';

const fail = (message) => {
  process.stderr.write(`analysis-preflight: ${message}\n`);
  process.exitCode = 1;
};

function argsOf(argv) {
  const result = {};
  for (let i = 0; i < argv.length; i += 2) {
    const flag = argv[i];
    const value = argv[i + 1];
    if (!['--manifest', '--song-dir'].includes(flag) || !value) {
      throw new Error('usage: node analysis_preflight.mjs --manifest <arrangement.json> ' +
        '--song-dir <song-folder>');
    }
    result[flag.slice(2)] = value;
  }
  if (!result.manifest || !result['song-dir']) {
    throw new Error('both --manifest and --song-dir are required');
  }
  return { manifest: path.resolve(result.manifest), songDir: path.resolve(result['song-dir']) };
}

function sameRef(a, b) {
  const normalize = (value) => {
    if (Array.isArray(value)) return value.map(normalize);
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.keys(value).sort().map((key) => [key, normalize(value[key])]));
    }
    return value;
  };
  return JSON.stringify(normalize(a)) === JSON.stringify(normalize(b));
}

function patternSourceRef(pattern, spansById) {
  const canonical = (pattern.variants || []).find((variant) => variant.is_canonical);
  if (canonical?.source_ref) return canonical.source_ref;
  if (canonical?.span_ids?.length) {
    return sourceRefFromSpanIds(canonical.span_ids, spansById);
  }
  return sourceRefFromSpanIds(pattern.span_ids, spansById);
}

function sourceRefFromSpanIds(spanIds, spansById) {
  const refs = spanIds.map((id) => spansById.get(id)?.source_ref).filter(Boolean);
  if (refs.length !== spanIds.length) {
    throw new Error('one or more selected spans have no source_ref');
  }
  const first = refs[0];
  if (!refs.every((ref) => sameRef(ref, first))) {
    throw new Error('selected spans do not share one exact source_ref');
  }
  return first;
}

function cueEligibleLimitation(message) {
  return /(unsupported effect|tie enters|tie leaves|grace note)/i.test(message);
}

function finding(id, kind, run, sourceRef, expectedBars, cueOnUnsupported = false) {
  try {
    const compiled = run();
    assertFingerprintMatches(compiled, sourceRef, id);
    if (expectedBars !== undefined && compiled.bars.length !== expectedBars) {
      throw new Error(`${id}: declared ${expectedBars} bars but source_ref resolves to ` +
        `${compiled.bars.length}`);
    }
    return {
      id, kind, status: 'supported',
      content_fingerprint: compiled.contentFingerprint,
      bars: compiled.bars.length,
      instrument: {
        kind: compiled.kind,
        tuning_midi: compiled.tuningMidi,
        capo_fret: compiled.capoFret,
        playback: compiled.playback,
      },
    };
  } catch (error) {
    const reason = String(error?.message || error);
    return {
      id, kind,
      status: cueOnUnsupported && cueEligibleLimitation(reason) ? 'cue_only' : 'unsupported',
      reason,
    };
  }
}

function decisionIdsForPattern(pattern, decisions) {
  if (Array.isArray(pattern.decision_ids) && pattern.decision_ids.length) {
    return pattern.decision_ids;
  }
  const expected = new Set((pattern.span_ids || []).map((id) => `decision-${id}`));
  return decisions.filter((decision) => expected.has(decision.id)).map((decision) => decision.id);
}

function canResolveToLoop(pattern, decisions) {
  if (pattern.disposition === 'LOOP' ||
      (pattern.disposition === 'OPTIONAL' && pattern.retain_role === 'loop')) return true;
  if (pattern.disposition !== 'UNRESOLVED') return false;
  const ids = new Set(decisionIdsForPattern(pattern, decisions));
  return decisions.some((decision) => ids.has(decision.id) &&
    (decision.options || []).some((option) =>
      ['use_canonical', 'normalize_canonical', 'accept_long_loop',
        'keep_loop', 'preserve_variants'].includes(option)));
}

try {
  const { manifest, songDir } = argsOf(process.argv.slice(2));
  const raw = JSON.parse(fs.readFileSync(manifest, 'utf8'));
  if (raw.version !== 2) throw new Error('manifest.version must be 2');
  const resolver = new SourceResolver(songDir);
  const resolved = resolver.resolve(raw.source);
  const spansById = new Map();
  for (const track of raw.tracks || []) {
    for (const span of track.spans || []) spansById.set(span.id, span);
  }

  const findings = [];
  const decisions = raw.decisions || [];
  const sourcePatterns = (raw.patterns || []).filter((pattern) =>
    canResolveToLoop(pattern, decisions));
  for (const pattern of sourcePatterns) {
    const sourceRef = patternSourceRef(pattern, spansById);
    findings.push(finding(pattern.id, 'pattern', () =>
      compileSourcePattern(resolved, sourceRef, `patterns[${pattern.id}]`),
    sourceRef, pattern.bars));
    for (const variant of pattern.variants || []) {
      const variantSourceRef = sourceRefFromSpanIds(variant.span_ids, spansById);
      findings.push(finding(variant.id, 'variant', () =>
        compileSourcePattern(resolved, variantSourceRef, `variants[${variant.id}]`),
      variantSourceRef, pattern.bars));
    }
  }
  for (const track of raw.tracks || []) {
    for (const span of track.spans || []) {
      if (span.disposition !== 'LIVE') continue;
      if (!span.source_ref) {
        findings.push({ id: span.id, kind: 'live', status: 'cue_only',
          reason: 'LIVE span has no exact source_ref' });
        continue;
      }
      findings.push(finding(span.id, 'live', () =>
        compileSourceSpan(resolved, span.source_ref, `LIVE span ${span.id}`),
      span.source_ref, undefined, true));
    }
  }

  const preserveVariantsReady = decisions
    .filter((decision) => (decision.options || []).includes('preserve_variants'))
    .map((decision) => {
      const patterns = sourcePatterns.filter((pattern) =>
        decisionIdsForPattern(pattern, decisions).includes(decision.id));
      return {
        decision_id: decision.id,
        status: patterns.length > 0 && patterns.every((pattern) => pattern.variants?.length) &&
          patterns.every((pattern) => pattern.variants.every((variant) =>
            findings.some((item) => item.id === variant.id && item.status === 'supported')))
          ? 'supported' : 'blocked',
        variant_pattern_ids: patterns.map((pattern) => pattern.id),
      };
    });

  const qualityFindings = [];
  const trackKey = (pattern) => pattern.track_id || pattern.track;
  const loopPatternsByTrack = new Map();
  for (const pattern of sourcePatterns) {
    const key = trackKey(pattern);
    if (!loopPatternsByTrack.has(key)) loopPatternsByTrack.set(key, []);
    loopPatternsByTrack.get(key).push(pattern);
    if (![4, 8].includes(pattern.bars) && !pattern.selection_reason) {
      qualityFindings.push({
        id: pattern.id, kind: 'selection_quality', status: 'review_required',
        reason: `${pattern.bars}-bar selected loop needs a musical selection_reason; ` +
          '4/8 bars are the default phrase lengths',
      });
    }
  }
  for (const patterns of loopPatternsByTrack.values()) {
    patterns.slice(3).forEach((pattern) => {
      if (!pattern.selection_reason) {
        qualityFindings.push({
          id: pattern.id, kind: 'selection_quality', status: 'review_required',
          reason: 'more than three selected loop patterns on one source track requires ' +
            'a musical selection_reason',
        });
      }
    });
  }
  const patternsById = new Map((raw.patterns || []).map((pattern) => [pattern.id, pattern]));
  for (const section of raw.sections || []) {
    const activeLoops = (section.active_pattern_ids || [])
      .map((id) => patternsById.get(id)).filter((pattern) => pattern &&
        canResolveToLoop(pattern, decisions));
    const byTrack = new Map();
    for (const pattern of activeLoops) {
      const key = trackKey(pattern);
      if (!byTrack.has(key)) byTrack.set(key, []);
      byTrack.get(key).push(pattern);
      if (section.bars % pattern.bars !== 0) {
        qualityFindings.push({
          id: `${section.id}:${pattern.id}`, kind: 'section_timing',
          status: 'review_required',
          reason: `section ${section.id} is ${section.bars} bars but pattern ${pattern.id} is ` +
            `${pattern.bars} bars; split ordered sub-blocks or record an explicit musical reason`,
        });
      }
    }
    for (const patterns of byTrack.values()) {
      if (patterns.length > 1) {
        qualityFindings.push({
          id: section.id, kind: 'section_timing', status: 'review_required',
          reason: `multiple loop patterns from one source track are active for the entire ` +
            `section (${patterns.map((pattern) => pattern.id).join(', ')}); split sequential ` +
            'sub-blocks or justify intentional layering',
        });
      }
    }
  }
  findings.push(...qualityFindings);

  const blockers = findings.filter((item) =>
    item.status === 'unsupported' || item.status === 'review_required')
    .map((item) => ({ id: item.id, kind: item.kind, status: item.status,
      reason: item.reason }));
  for (const decision of preserveVariantsReady.filter((item) => item.status === 'blocked')) {
    blockers.push({
      id: decision.decision_id,
      reason: decision.variant_pattern_ids.length
        ? 'one or more declared occurrence variants cannot be exported exactly'
        : 'preserve_variants is offered but no source-backed occurrence variants are declared',
    });
  }
  const output = {
    status: blockers.length ? 'blocked' : 'ready',
    source_sha256: raw.source?.asset_sha256 ?? null,
    findings,
    preserve_variants: preserveVariantsReady,
    blockers,
  };
  process.stdout.write(`${JSON.stringify(output, null, 2)}\n`);
  if (blockers.length) process.exitCode = 2;
} catch (error) {
  fail(String(error?.message || error));
}
