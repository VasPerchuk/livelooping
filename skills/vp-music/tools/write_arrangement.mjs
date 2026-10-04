// GP7 source-copy arrangement writer. Requires Node.js, Python and local alphaTab 1.8.4.
// Usage: node write_arrangement.mjs --performance <performance-arrangement.json>
// --song-dir <song> --output <song>\arrangement.gp
// Resolves each selected LOOP pattern's single-span `source_ref` against a digest-matched
// modern ZIP .gp/GPIF asset (song.source.capabilities must include "source_bar_reuse") and
// copies a narrow, documented alphaTab field matrix (see `source_compiler.mjs`) instead of
// authoring numeric events. Only contiguous 1/2/4/8 written bars, one track/staff/voice per
// pattern, constant meter/tempo per pattern and no tie/grace/tuplet/hammer-pull boundary
// crossing or other unsupported effect are supported; anything else raises instead of silently falling
// back to a numeric reconstruction. GP3/4/5 and GPX sources are not supported for export here.
import * as alphaTab from '@coderline/alphatab';
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID, createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { SourceResolver, compileSourcePattern, compileSourceSpan, assertFingerprintMatches,
  ONE_OFF_SPAN_LIMITS } from './source_compiler.mjs';


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

// --- v2 analysis-contract foundation -------------------------------------
// `--manifest` (arrangement.json) remains validate-only for version 2: analysis-time
// facts are never compiled into a score directly. `--performance` (a compiled
// performance-arrangement.json with `source.capabilities` including
// "source_bar_reuse") *can* produce arrangement.gp, by copying resolved GPIF source
// bars through `source_compiler.mjs` (see `buildV2`/`mainV2` below) rather than
// authoring numeric events. See analyze\references\arrangement-handoff.md for the
// full schema rationale and this slice's exact capabilities/limitations.
const DISPOSITIONS_V2 = ['LOOP', 'LIVE', 'OPTIONAL', 'UNRESOLVED', 'REST'];
const MATCH_TIERS_V2 = ['exact', 'normalized', 'near', 'legacy_unknown'];
// Closed vocabulary describing what the validated source asset can supply. Most values
// record capability facts only; "source_bar_reuse" is the one explicit authorization
// token `SourceResolver` requires before it will touch a GPIF asset for copying.
const CAPABILITIES_V2 = ['performed_order', 'repeat_directions', 'alternate_endings',
  'tempo_map', 'written_notation', 'source_bar_reuse'];
const SHA256_RE = /^[0-9a-f]{64}$/;

const relativeSourcePath = (value, where) => {
  insist(typeof value === 'string' && value.length > 0 && value.length <= 400,
    `${where}: expected nonempty relative path`);
  insist(!value.includes('\\') && !/^[A-Za-z]:/.test(value) && !value.startsWith('/') &&
    !value.split('/').includes('..') && value.trim() === value,
  `${where}: expected a song-relative forward-slash path with no drive letter, ` +
    'leading slash or ".." segment');
  return value;
};

const sha256 = (value, where) => {
  insist(typeof value === 'string' && SHA256_RE.test(value),
    `${where}: expected 64-character lowercase hex SHA-256`);
  return value;
};

function parseSourceRefV2(ref, where) {
  if (ref === undefined) return undefined;
  keys(ref, ['asset_sha256', 'asset_relative_path', 'track_index', 'track_name',
    'staff_index', 'written_master_bars', 'voice_indices', 'performed_occurrences',
    'ending_context', 'content_fingerprint'], where);
  sha256(ref.asset_sha256, `${where}.asset_sha256`);
  relativeSourcePath(ref.asset_relative_path, `${where}.asset_relative_path`);
  if (ref.content_fingerprint !== undefined) sha256(ref.content_fingerprint, `${where}.content_fingerprint`);
  if (ref.track_index !== undefined) integer(ref.track_index, 0, LIMITS.tracks - 1, `${where}.track_index`);
  if (ref.staff_index !== undefined) integer(ref.staff_index, 0, 8, `${where}.staff_index`);
  if (ref.track_name !== undefined) text(ref.track_name, `${where}.track_name`);
  for (const field of ['written_master_bars', 'voice_indices', 'performed_occurrences']) {
    if (ref[field] === undefined) continue;
    insist(Array.isArray(ref[field]) && ref[field].length > 0 && ref[field].length <= LIMITS.bars,
      `${where}.${field}: expected nonempty array`);
    ref[field].forEach((n, i) => integer(n, 0, 100_000, `${where}.${field}[${i}]`));
  }
  if (ref.ending_context !== undefined && ref.ending_context !== null) {
    metadata(ref.ending_context, `${where}.ending_context`, 200);
  }
  return ref;
}

function parseNearMatchV2(nearMatch, where) {
  if (nearMatch === undefined || nearMatch === null) return undefined;
  keys(nearMatch, ['matching_bars', 'total_bars', 'divergent_bar_offsets'], where);
  integer(nearMatch.total_bars, 1, LIMITS.bars, `${where}.total_bars`);
  integer(nearMatch.matching_bars, 0, nearMatch.total_bars, `${where}.matching_bars`);
  insist(Array.isArray(nearMatch.divergent_bar_offsets) &&
    nearMatch.divergent_bar_offsets.length > 0 &&
    nearMatch.divergent_bar_offsets.length <= nearMatch.total_bars,
  `${where}.divergent_bar_offsets: expected a nonempty array within total_bars`);
  nearMatch.divergent_bar_offsets.forEach((n, i) =>
    integer(n, 0, nearMatch.total_bars - 1, `${where}.divergent_bar_offsets[${i}]`));
  return nearMatch;
}

function parseSpanV2(span, where) {
  keys(span, ['id', 'disposition', 'source_ref', 'match_tier', 'evidence', 'near_match'], where);
  text(span.id, `${where}.id`);
  insist(DISPOSITIONS_V2.includes(span.disposition),
    `${where}.disposition: expected one of ${DISPOSITIONS_V2.join('|')}`);
  parseSourceRefV2(span.source_ref, `${where}.source_ref`);
  if (span.match_tier !== undefined) {
    insist(MATCH_TIERS_V2.includes(span.match_tier),
      `${where}.match_tier: expected one of ${MATCH_TIERS_V2.join('|')}`);
  }
  if (span.evidence !== undefined) metadata(span.evidence, `${where}.evidence`, 500);
  parseNearMatchV2(span.near_match, `${where}.near_match`);
  return span;
}

function parseTrackV2(track, where) {
  keys(track, ['id', 'name', 'role', 'role_confidence', 'spans'], where);
  if (track.id !== undefined) text(track.id, `${where}.id`);
  text(track.name, `${where}.name`);
  if (track.role !== undefined) metadata(track.role, `${where}.role`, 100);
  if (track.role_confidence !== undefined) {
    insist(['confirmed', 'likely', 'estimated', 'unknown'].includes(track.role_confidence),
      `${where}.role_confidence: expected confirmed|likely|estimated|unknown`);
  }
  insist(Array.isArray(track.spans) && track.spans.length > 0 &&
    track.spans.length <= LIMITS.patterns, `${where}.spans: expected nonempty array`);
  const ids = new Set();
  const spans = track.spans.map((span, si) => {
    const parsed = parseSpanV2(span, `${where}.spans[${si}]`);
    insist(!ids.has(parsed.id), `${where}.spans[${si}]: duplicate span id`);
    ids.add(parsed.id);
    return parsed;
  });
  return { id: track.id, name: track.name, role: track.role,
    role_confidence: track.role_confidence, spans };
}

function parseDecisionV2(decision, where) {
  keys(decision, ['id', 'question', 'options', 'depends_on', 'applies_when', 'coordinates'], where);
  text(decision.id, `${where}.id`);
  metadata(decision.question, `${where}.question`, 500);
  insist(Array.isArray(decision.options) && decision.options.length >= 2 &&
    decision.options.length <= 8, `${where}.options: expected 2-8 choices`);
  decision.options.forEach((option, i) => metadata(option, `${where}.options[${i}]`, 200));
  insist((decision.depends_on === undefined) === (decision.applies_when === undefined),
    `${where}: depends_on and applies_when must both be present or both absent`);
  if (decision.depends_on !== undefined) {
    text(decision.depends_on, `${where}.depends_on`);
    metadata(decision.applies_when, `${where}.applies_when`, 200);
  }
  if (decision.coordinates !== undefined) {
    insist(Array.isArray(decision.coordinates) && decision.coordinates.length <= LIMITS.bars,
      `${where}.coordinates: expected array`);
    decision.coordinates.forEach((c, i) => metadata(c, `${where}.coordinates[${i}]`, 300));
  }
  return decision;
}

function parseVariantV2(variant, where, knownSpanIds) {
  keys(variant, ['id', 'span_ids', 'is_canonical', 'occurrence_index'], where);
  text(variant.id, `${where}.id`);
  insist(Array.isArray(variant.span_ids) && variant.span_ids.length > 0,
    `${where}.span_ids: expected nonempty array`);
  variant.span_ids.forEach((id, i) => {
    text(id, `${where}.span_ids[${i}]`);
    insist(knownSpanIds.has(id), `${where}.span_ids[${i}]: span id not found on this pattern's track`);
  });
  if (variant.is_canonical !== undefined) {
    insist(typeof variant.is_canonical === 'boolean', `${where}.is_canonical: expected boolean`);
  }
  if (variant.occurrence_index !== undefined) {
    integer(variant.occurrence_index, 1, LIMITS.patterns, `${where}.occurrence_index`);
  }
  return variant;
}

function parsePatternVariantsV2(pattern, where, knownSpanIds) {
  if (pattern.variants === undefined) return undefined;
  insist(Array.isArray(pattern.variants) && pattern.variants.length > 0,
    `${where}.variants: expected nonempty array when present`);
  const variants = pattern.variants.map((v, i) => parseVariantV2(v, `${where}.variants[${i}]`, knownSpanIds));
  const ids = new Set();
  let canonicalCount = 0;
  for (const variant of variants) {
    insist(!ids.has(variant.id), `${where}.variants: duplicate variant id ${variant.id}`);
    ids.add(variant.id);
    if (variant.is_canonical) canonicalCount += 1;
  }
  insist(canonicalCount <= 1, `${where}.variants: at most one variant may be is_canonical`);
  return variants;
}

function resolvePatternTrackId(pattern, where, trackSpanIds, trackIdsByName) {
  // Stable track identity (requirement 1): new authoring should declare
  // `track_id` explicitly; a legacy pattern that only gives `track` (a display
  // name) is accepted only when that name is unambiguous across the
  // manifest's tracks -- never silently guessed when two tracks share a name.
  if (pattern.track_id !== undefined) {
    text(pattern.track_id, `${where}.track_id`);
    insist(trackSpanIds.has(pattern.track_id), `${where}.track_id: unknown track id`);
    return pattern.track_id;
  }
  const candidates = trackIdsByName.get(pattern.track) || [];
  insist(candidates.length !== 0, `${where}.track: unknown track name`);
  insist(candidates.length === 1,
    `${where}.track: track name ${pattern.track} is shared by ${candidates.length} tracks; ` +
    `declare an explicit track_id to disambiguate`);
  return candidates[0];
}

function parsePatternV2(pattern, where, trackSpanIds, trackIdsByName) {
  keys(pattern, ['id', 'name', 'track', 'track_id', 'span_ids', 'bars', 'disposition', 'confidence',
    'retain_role', 'variants', 'selection_reason', 'decision_ids'], where);
  text(pattern.id, `${where}.id`);
  text(pattern.name, `${where}.name`);
  text(pattern.track, `${where}.track`);
  const trackId = resolvePatternTrackId(pattern, where, trackSpanIds, trackIdsByName);
  pattern._trackId = trackId;
  insist(Array.isArray(pattern.span_ids) && pattern.span_ids.length > 0 &&
    pattern.span_ids.length <= LIMITS.patterns, `${where}.span_ids: expected nonempty array`);
  const known = trackSpanIds.get(trackId);
  pattern.span_ids.forEach((id, i) => {
    text(id, `${where}.span_ids[${i}]`);
    insist(known.has(id), `${where}.span_ids[${i}]: span id not found on track ${pattern.track}`);
  });
  insist(new Set(pattern.span_ids).size === pattern.span_ids.length,
    `${where}.span_ids: duplicate span id`);
  integer(pattern.bars, 1, LIMITS.bars, `${where}.bars`);
  insist(DISPOSITIONS_V2.includes(pattern.disposition),
    `${where}.disposition: expected one of ${DISPOSITIONS_V2.join('|')}`);
  if (pattern.confidence !== undefined) {
    insist(['confirmed', 'likely', 'estimated', 'unknown'].includes(pattern.confidence),
      `${where}.confidence: expected confirmed|likely|estimated|unknown`);
  }
  if (pattern.selection_reason !== undefined) {
    text(pattern.selection_reason, `${where}.selection_reason`, 500);
  }
  if (pattern.decision_ids !== undefined) {
    insist(Array.isArray(pattern.decision_ids) && pattern.decision_ids.length > 0,
      `${where}.decision_ids: expected nonempty array`);
    pattern.decision_ids.forEach((id, i) => text(id, `${where}.decision_ids[${i}]`));
    insist(new Set(pattern.decision_ids).size === pattern.decision_ids.length,
      `${where}.decision_ids: duplicate decision id`);
  }
  if (pattern.retain_role !== undefined) {
    insist(pattern.disposition === 'OPTIONAL',
      `${where}.retain_role: only valid when disposition is OPTIONAL`);
    insist(PHYSICAL_ROLES_V2.includes(pattern.retain_role),
      `${where}.retain_role: expected one of ${PHYSICAL_ROLES_V2.join('|')}`);
  }
  parsePatternVariantsV2(pattern, where, known);
  return pattern;
}

function parseSectionV2(section, where, patternIds) {
  keys(section, ['id', 'name', 'bars', 'active_pattern_ids', 'empty_policy'], where);
  text(section.id, `${where}.id`);
  text(section.name, `${where}.name`);
  integer(section.bars, 1, LIMITS.bars, `${where}.bars`);
  insist(Array.isArray(section.active_pattern_ids), `${where}.active_pattern_ids: expected array`);
  insist(new Set(section.active_pattern_ids).size === section.active_pattern_ids.length,
    `${where}: duplicate active pattern id`);
  section.active_pattern_ids.forEach((id, i) => {
    text(id, `${where}.active_pattern_ids[${i}]`);
    insist(patternIds.has(id), `${where}.active_pattern_ids[${i}]: unknown pattern id`);
  });
  insist(section.empty_policy === undefined || ['rest', 'live'].includes(section.empty_policy),
    `${where}.empty_policy: expected rest|live`);
  insist(section.active_pattern_ids.length || section.empty_policy,
    `${where}: empty active_pattern_ids requires empty_policy=rest or live`);
  insist(section.active_pattern_ids.length === 0 || section.empty_policy === undefined,
    `${where}: empty_policy applies only when no patterns are active`);
  return section;
}

function parseMeterV2(raw) {
  let tempoBpm, meter;
  if (raw.tempo_bpm !== undefined) {
    tempoBpm = integer(raw.tempo_bpm, 20, 400, 'tempo_bpm');
  }
  if (raw.meter !== undefined) {
    const match = typeof raw.meter === 'string' && /^([1-9]\d?)\/(1|2|4|8|16|32)$/.exec(raw.meter);
    insist(match && Number(match[1]) <= 16, 'meter: expected n/d, n=1..16, d=1,2,4,8,16,32');
    meter = raw.meter;
  }
  return { tempoBpm, meter };
}

function parseSourceBlockV2(source, where) {
  keys(source, ['asset_relative_path', 'asset_sha256', 'format', 'capabilities'], where);
  relativeSourcePath(source.asset_relative_path, `${where}.asset_relative_path`);
  sha256(source.asset_sha256, `${where}.asset_sha256`);
  insist(['gpif', 'gpx', 'gp3', 'gp4', 'gp5', 'midi'].includes(source.format),
    `${where}.format: expected gpif|gpx|gp3|gp4|gp5|midi`);
  if (source.capabilities !== undefined) {
    insist(Array.isArray(source.capabilities) &&
      source.capabilities.length <= CAPABILITIES_V2.length,
    `${where}.capabilities: expected array`);
    source.capabilities.forEach((c, i) => insist(CAPABILITIES_V2.includes(c),
      `${where}.capabilities[${i}]: expected one of ${CAPABILITIES_V2.join('|')}`));
    insist(new Set(source.capabilities).size === source.capabilities.length,
      `${where}.capabilities: duplicate entry`);
  }
  return source;
}

function parseDecisionsV2(raw) {
  let decisions = [];
  if (raw.decisions !== undefined) {
    insist(Array.isArray(raw.decisions) && raw.decisions.length <= LIMITS.sections,
      'decisions: expected array');
    decisions = raw.decisions.map((decision, di) => parseDecisionV2(decision, `decisions[${di}]`));
    const decisionIds = new Set();
    for (const decision of decisions) {
      insist(!decisionIds.has(decision.id), `decisions: duplicate decision id ${decision.id}`);
      decisionIds.add(decision.id);
    }
    for (const decision of decisions) {
      if (decision.depends_on === undefined) continue;
      insist(decisionIds.has(decision.depends_on),
        `decisions: ${decision.id}.depends_on references unknown decision ${decision.depends_on}`);
      const parent = decisions.find((d) => d.id === decision.depends_on);
      insist(parent.options.includes(decision.applies_when),
        `decisions: ${decision.id}.applies_when is not an option of ${decision.depends_on}`);
    }
  }
  return decisions;
}

function assignEffectiveTrackIds(tracks, where) {
  // Stable track identity (requirement 1): display `name` duplicates are
  // always supported and never rejected. Every track gets an effective `id`
  // -- its own declared `id` if present, otherwise a deterministic,
  // index-stable `track-<i>` fallback -- written back onto the track so every
  // downstream consumer keys identity by id, never by name.
  const explicitIdsSeen = new Set();
  const effectiveIds = tracks.map((t, i) => {
    if (t.id !== undefined) {
      insist(!explicitIdsSeen.has(t.id), `${where}[${i}].id: duplicate track id`);
      explicitIdsSeen.add(t.id);
      return t.id;
    }
    return null;
  });
  const resolved = effectiveIds.map((id, i) => (id === null ? `track-${i}` : id));
  insist(new Set(resolved).size === resolved.length,
    `${where}: duplicate track id (an explicit id collides with another track's explicit or ` +
    'auto-assigned id)');
  tracks.forEach((t, i) => { t.id = resolved[i]; });
  const trackSpanIds = new Map(tracks.map((t) => [t.id, new Set(t.spans.map((s) => s.id))]));
  const trackIdsByName = new Map();
  for (const t of tracks) {
    if (!trackIdsByName.has(t.name)) trackIdsByName.set(t.name, []);
    trackIdsByName.get(t.name).push(t.id);
  }
  return { trackSpanIds, trackIdsByName };
}

function parseManifestV2(raw) {
  keys(raw, ['version', 'source', 'tracks', 'decisions', 'patterns', 'sections',
    'tempo_bpm', 'meter', 'performed_written_bars'], 'manifest');
  insist(raw.version === 2, 'only manifest version 2 is supported here');
  parseSourceBlockV2(raw.source, 'source');
  insist(Array.isArray(raw.tracks) && raw.tracks.length > 0 &&
    raw.tracks.length <= LIMITS.tracks, 'tracks: missing or too many');
  const tracks = raw.tracks.map((track, ti) => parseTrackV2(track, `tracks[${ti}]`));
  const decisions = parseDecisionsV2(raw);
  const { trackSpanIds, trackIdsByName } = assignEffectiveTrackIds(tracks, 'tracks');
  let patterns = [];
  if (raw.patterns !== undefined) {
    insist(Array.isArray(raw.patterns) && raw.patterns.length <= LIMITS.patterns,
      'patterns: expected array');
    patterns = raw.patterns.map((pattern, pi) =>
      parsePatternV2(pattern, `patterns[${pi}]`, trackSpanIds, trackIdsByName));
    const patternIds = new Set();
    const patternNames = new Set();
    for (const pattern of patterns) {
      insist(!patternIds.has(pattern.id), `patterns: duplicate pattern id ${pattern.id}`);
      patternIds.add(pattern.id);
      insist(!patternNames.has(pattern.name), `patterns: duplicate pattern name ${pattern.name}`);
      patternNames.add(pattern.name);
      for (const decisionId of pattern.decision_ids || []) {
        insist(decisions.some((decision) => decision.id === decisionId),
          `patterns[${pattern.id}].decision_ids references unknown decision ${decisionId}`);
      }
    }
  }
  let sections = [];
  if (raw.sections !== undefined) {
    insist(patterns.length > 0 || raw.sections.length === 0,
      'sections: requires patterns to reference when nonempty');
    const patternIds = new Set(patterns.map((p) => p.id));
    insist(Array.isArray(raw.sections) && raw.sections.length <= LIMITS.sections,
      'sections: expected array');
    sections = raw.sections.map((section, si) => parseSectionV2(section, `sections[${si}]`, patternIds));
    const sectionIds = new Set();
    for (const section of sections) {
      insist(!sectionIds.has(section.id), `sections: duplicate section id ${section.id}`);
      sectionIds.add(section.id);
    }
  }
  const { tempoBpm, meter } = parseMeterV2(raw);
  const unresolved = tracks.some((t) => t.spans.some((s) => s.disposition === 'UNRESOLVED'));
  insist(!unresolved || decisions.length > 0,
    'an UNRESOLVED span requires at least one entry in decisions');
  return { version: 2, source: raw.source, tracks, decisions, patterns, sections,
    tempoBpm, meter, unresolved };
}

function assertFinalReadyV2(spec) {
  insist(!spec.unresolved, 'Final performance export cannot contain an UNRESOLVED disposition');
}

// --- v2 performance/publication (performance-arrangement.json) parsing ----
// This is the compiled output of compile_performance.py: the same tracks/decisions/
// patterns/sections shape as arrangement.json, plus `resolutions`, `publication`,
// `performance` and `disclosure`, and every selected pattern additionally carries
// `final_disposition`/`omitted` (never `UNRESOLVED` for a `final` publication).
function parsePublicationV2(pub, where) {
  keys(pub, ['status', 'limitations', 'consent'], where);
  insist(['final', 'partial', 'draft'].includes(pub.status),
    `${where}.status: expected final, partial, or draft`);
  insist(Array.isArray(pub.limitations) && pub.limitations.length <= 32,
    `${where}.limitations: expected array of at most 32 items`);
  pub.limitations.forEach((item, i) => metadata(item, `${where}.limitations[${i}]`, 500));
  if (pub.status === 'partial') {
    insist(pub.limitations.length > 0, `${where}: partial publication requires limitations`);
    metadata(pub.consent, `${where}.consent`, 2000);
  } else if (pub.status === 'draft') {
    insist(pub.consent === undefined || pub.consent === null,
      `${where}: draft preview does not accept a consent field`);
  } else {
    insist(pub.limitations.length === 0, `${where}: final publication cannot have limitations`);
    insist(pub.consent === undefined || pub.consent === null,
      `${where}: final publication does not accept a consent field`);
  }
  return { status: pub.status, limitations: [...pub.limitations], consent: pub.consent ?? null };
}

function parsePerformanceInfoV2(perf, where) {
  if (perf === undefined || perf === null) return {};
  keys(perf, ['players', 'roles', 'transitions', 'section_transitions'], where);
  if (perf.players !== undefined) integer(perf.players, 1, 32, `${where}.players`);
  for (const field of ['roles', 'transitions']) {
    if (perf[field] === undefined) continue;
    insist(Array.isArray(perf[field]) && perf[field].length <= 128,
      `${where}.${field}: expected array of at most 128 descriptions`);
    perf[field].forEach((item, i) => metadata(item, `${where}.${field}[${i}]`, 500));
  }
  if (perf.section_transitions !== undefined) {
    insist(Array.isArray(perf.section_transitions) && perf.section_transitions.length <= LIMITS.sections,
      `${where}.section_transitions: expected bounded array`);
    const seen = new Set();
    perf.section_transitions.forEach((entry, i) => {
      const at = `${where}.section_transitions[${i}]`;
      keys(entry, ['section_id', 'action', 'feasible', 'warning'], at);
      text(entry.section_id, `${at}.section_id`);
      metadata(entry.action, `${at}.action`, 500);
      insist(typeof entry.feasible === 'boolean', `${at}.feasible: expected boolean`);
      if (!entry.feasible) metadata(entry.warning, `${at}.warning`, 500);
      else if (entry.warning !== undefined) metadata(entry.warning, `${at}.warning`, 500);
      insist(!seen.has(entry.section_id), `${at}.section_id: duplicate section id`);
      seen.add(entry.section_id);
    });
  }
  return perf;
}

function parseDisclosureV2(disclosure, where) {
  keys(disclosure, ['live_patterns', 'live_spans_without_selected_pattern',
    'omitted_patterns', 'rest_spans',
    'optional_spans_without_selected_pattern', 'unresolved_patterns'], where);
  for (const field of Object.keys(disclosure)) {
    insist(Array.isArray(disclosure[field]), `${where}.${field}: expected array`);
    disclosure[field].forEach((id, i) => text(id, `${where}.${field}[${i}]`));
  }
  return disclosure;
}

function parseResolvedPatternV2(pattern, where, trackSpanIds, trackIdsByName) {
  keys(pattern, ['id', 'name', 'track', 'track_id', 'span_ids', 'bars', 'disposition', 'confidence',
    'retain_role', 'final_disposition', 'omitted', 'variants', 'selection_reason',
    'decision_ids'], where);
  text(pattern.id, `${where}.id`);
  text(pattern.name, `${where}.name`);
  text(pattern.track, `${where}.track`);
  const trackId = resolvePatternTrackId(pattern, where, trackSpanIds, trackIdsByName);
  pattern._trackId = trackId;
  insist(Array.isArray(pattern.span_ids) && pattern.span_ids.length > 0,
    `${where}.span_ids: expected nonempty array`);
  const known = trackSpanIds.get(trackId);
  pattern.span_ids.forEach((id, i) => {
    text(id, `${where}.span_ids[${i}]`);
    insist(known.has(id), `${where}.span_ids[${i}]: span id not found on track ${pattern.track}`);
  });
  insist(new Set(pattern.span_ids).size === pattern.span_ids.length,
    `${where}.span_ids: duplicate span id`);
  integer(pattern.bars, 1, LIMITS.bars, `${where}.bars`);
  insist(DISPOSITIONS_V2.includes(pattern.disposition),
    `${where}.disposition: expected one of ${DISPOSITIONS_V2.join('|')}`);
  insist(DISPOSITIONS_V2.includes(pattern.final_disposition),
    `${where}.final_disposition: expected one of ${DISPOSITIONS_V2.join('|')}`);
  insist(typeof pattern.omitted === 'boolean', `${where}.omitted: expected boolean`);
  if (pattern.confidence !== undefined) {
    insist(['confirmed', 'likely', 'estimated', 'unknown'].includes(pattern.confidence),
      `${where}.confidence: expected confirmed|likely|estimated|unknown`);
  }
  if (pattern.selection_reason !== undefined) {
    text(pattern.selection_reason, `${where}.selection_reason`, 500);
  }
  if (pattern.decision_ids !== undefined) {
    insist(Array.isArray(pattern.decision_ids) && pattern.decision_ids.length > 0,
      `${where}.decision_ids: expected nonempty array`);
    pattern.decision_ids.forEach((id, i) => text(id, `${where}.decision_ids[${i}]`));
  }
  if (pattern.retain_role !== undefined) {
    insist(pattern.disposition === 'OPTIONAL',
      `${where}.retain_role: only valid when disposition is OPTIONAL`);
    insist(PHYSICAL_ROLES_V2.includes(pattern.retain_role),
      `${where}.retain_role: expected one of ${PHYSICAL_ROLES_V2.join('|')}`);
  }
  parsePatternVariantsV2(pattern, where, known);
  return pattern;
}

function parsePerformanceV2(raw) {
  keys(raw, ['version', 'source', 'tracks', 'decisions', 'resolutions', 'patterns', 'sections',
    'tempo_bpm', 'meter', 'performed_written_bars', 'publication', 'performance', 'disclosure',
    'plan_version', 'fingerprints', 'instruments', 'section_instances', 'output_timeline',
    'removal_ledger', 'placements', 'variant_selections', 'tail_policy', 'export_layout'],
  'performance');
  insist(raw.version === 2, 'only version 2 performance handoffs are supported here');
  parseSourceBlockV2(raw.source, 'source');
  insist(Array.isArray(raw.tracks) && raw.tracks.length > 0 &&
    raw.tracks.length <= LIMITS.tracks, 'tracks: missing or too many');
  const tracks = raw.tracks.map((track, ti) => parseTrackV2(track, `tracks[${ti}]`));
  const decisions = parseDecisionsV2(raw);
  insist(Array.isArray(raw.resolutions) ||
    (raw.resolutions && typeof raw.resolutions === 'object'),
  'resolutions: expected array or object');

  const { trackSpanIds, trackIdsByName } = assignEffectiveTrackIds(tracks, 'tracks');
  insist(Array.isArray(raw.patterns) && raw.patterns.length > 0 &&
    raw.patterns.length <= LIMITS.patterns, 'patterns: expected nonempty array');
  const patterns = raw.patterns.map((pattern, pi) =>
    parseResolvedPatternV2(pattern, `patterns[${pi}]`, trackSpanIds, trackIdsByName));
  const patternIds = new Set();
  const patternNames = new Set();
  for (const pattern of patterns) {
    insist(!patternIds.has(pattern.id), `patterns: duplicate pattern id ${pattern.id}`);
    patternIds.add(pattern.id);
    insist(!patternNames.has(pattern.name), `patterns: duplicate pattern name ${pattern.name}`);
    patternNames.add(pattern.name);
  }

  insist(Array.isArray(raw.sections) && raw.sections.length > 0 &&
    raw.sections.length <= LIMITS.sections, 'sections: missing or too many');
  const sections = raw.sections.map((section, si) =>
    parseSectionV2(section, `sections[${si}]`, patternIds));
  const sectionIds = new Set();
  for (const section of sections) {
    insist(!sectionIds.has(section.id), `sections: duplicate section id ${section.id}`);
    sectionIds.add(section.id);
  }

  const { tempoBpm, meter } = parseMeterV2(raw);
  insist(raw.publication, 'publication: required for a performance handoff');
  const publication = parsePublicationV2(raw.publication, 'publication');
  const performance = parsePerformanceInfoV2(raw.performance, 'performance');
  for (const transition of performance.section_transitions || []) {
    insist(sectionIds.has(transition.section_id),
      `performance.section_transitions: unknown section ${transition.section_id}`);
  }
  insist(raw.disclosure, 'disclosure: required for a performance handoff');
  const disclosure = parseDisclosureV2(raw.disclosure, 'disclosure');
  if (publication.status === 'final') {
    insist(!patterns.some((p) => p.final_disposition === 'UNRESOLVED'),
      'Final performance export cannot contain an UNRESOLVED disposition');
  }
  if (raw.export_layout !== undefined) {
    const layout = raw.export_layout;
    insist(layout && typeof layout === 'object', 'export_layout: expected object');
    keys(layout, ['version', 'prune_zero_note_tracks', 'group_order', 'colors'],
      'export_layout');
    insist(layout.version === 1 && layout.prune_zero_note_tracks === true,
      'export_layout: expected version 1 with prune_zero_note_tracks=true');
    insist(JSON.stringify(layout.group_order) ===
      JSON.stringify(['core', 'optional', 'unresolved']),
    'export_layout.group_order: expected core, optional, unresolved');
    insist(JSON.stringify(layout.colors) === JSON.stringify(
      { drums: '#0000FF', bass: '#FFFF00', other: '#FF0000' }),
    'export_layout.colors: expected canonical drums/bass/other palette');
  }
  return { version: 2, source: raw.source, tracks, decisions, patterns, sections,
    tempoBpm, meter, publication, performance, disclosure, raw };
}

// --- Performance-plan foundation (additive, mirrors v2_schema.py validate_performance_plan) ---

const PHYSICAL_ROLES_V2 = ['live', 'loop', 'unresolved'];
const SECTION_ACTIONS_V2 = ['retain', 'delete'];
const TAIL_POLICIES_V2 = ['reviewed_truncate', 'reviewed_wrap_full_cycles_only',
  'reviewed_no_partial_cycle'];
// Mirrors v2_schema.py's PLAN_VERSION.
const PLAN_VERSION_V2 = 2;

function parseFingerprintsV2(fp, where) {
  insist(fp && typeof fp === 'object', `${where}: expected object`);
  keys(fp, ['analysis_sha256', 'resolutions_sha256'], where);
  for (const field of ['analysis_sha256', 'resolutions_sha256']) {
    insist(typeof fp[field] === 'string' && /^[0-9a-f]{64}$/.test(fp[field]),
      `${where}.${field}: expected 64-char hex sha256`);
  }
  return fp;
}

function parseOutputTrackV2(track, where, patternIds) {
  insist(track && typeof track === 'object', `${where}: expected object`);
  keys(track, ['id', 'role', 'pattern_id'], where);
  text(track.id, `${where}.id`);
  insist(PHYSICAL_ROLES_V2.includes(track.role),
    `${where}.role: expected one of ${PHYSICAL_ROLES_V2.join('|')}`);
  if (track.role === 'loop') {
    insist(typeof track.pattern_id === 'string' && patternIds.has(track.pattern_id),
      `${where}.pattern_id: required for role=loop and must reference a known pattern`);
  } else {
    insist(track.pattern_id === undefined,
      `${where}.pattern_id: only valid for role=loop`);
  }
  return track;
}

function parseInstrumentV2(instrument, where, patternIds) {
  insist(instrument && typeof instrument === 'object', `${where}: expected object`);
  keys(instrument, ['id', 'analysis_track', 'output_tracks'], where);
  text(instrument.id, `${where}.id`);
  text(instrument.analysis_track, `${where}.analysis_track`);
  insist(Array.isArray(instrument.output_tracks) && instrument.output_tracks.length > 0,
    `${where}.output_tracks: expected nonempty array`);
  const tracks = instrument.output_tracks.map((t, i) =>
    parseOutputTrackV2(t, `${where}.output_tracks[${i}]`, patternIds));
  const liveCount = tracks.filter((t) => t.role === 'live').length;
  const unresolvedCount = tracks.filter((t) => t.role === 'unresolved').length;
  insist(liveCount <= 1, `${where}.output_tracks: expected at most one live track`);
  insist(unresolvedCount <= 1, `${where}.output_tracks: expected at most one unresolved track`);
  const roleRank = { live: 0, loop: 1, unresolved: 2 };
  for (let i = 1; i < tracks.length; i++) {
    insist(roleRank[tracks[i - 1].role] <= roleRank[tracks[i].role],
      `${where}.output_tracks: expected live (if used), loop pattern(s), ` +
      'unresolved (if used)');
  }
  const ids = new Set();
  for (const t of tracks) {
    insist(!ids.has(t.id), `${where}.output_tracks: duplicate output track id ${t.id}`);
    ids.add(t.id);
  }
  return instrument;
}

function parseSectionInstanceV2(instance, where) {
  insist(instance && typeof instance === 'object', `${where}: expected object`);
  keys(instance, ['id', 'section_id', 'name', 'occurrence_index', 'action', 'approved',
    'omitted_layers'], where);
  text(instance.id, `${where}.id`);
  text(instance.section_id, `${where}.section_id`);
  text(instance.name, `${where}.name`);
  integer(instance.occurrence_index, 1, LIMITS.sections, `${where}.occurrence_index`);
  insist(SECTION_ACTIONS_V2.includes(instance.action),
    `${where}.action: expected one of ${SECTION_ACTIONS_V2.join('|')}`);
  insist(typeof instance.approved === 'boolean', `${where}.approved: expected boolean`);
  if (instance.action === 'delete') {
    insist(instance.approved === true, `${where}: delete action requires approved=true`);
  }
  insist(Array.isArray(instance.omitted_layers), `${where}.omitted_layers: expected array`);
  instance.omitted_layers.forEach((id, i) => text(id, `${where}.omitted_layers[${i}]`));
  return instance;
}

function parseOutputTimelineEntryV2(entry, where, instanceIds) {
  insist(entry && typeof entry === 'object', `${where}: expected object`);
  keys(entry, ['output_index', 'section_instance_id', 'source_section_bar'], where);
  integer(entry.output_index, 0, LIMITS.bars, `${where}.output_index`);
  insist(instanceIds.has(entry.section_instance_id),
    `${where}.section_instance_id: unknown section instance`);
  integer(entry.source_section_bar, 0, LIMITS.bars, `${where}.source_section_bar`);
  return entry;
}

function parseRemovalLedgerEntryV2(entry, where, instanceIds) {
  insist(entry && typeof entry === 'object', `${where}: expected object`);
  keys(entry, ['section_instance_id', 'name', 'removed_bars', 'decision_ref'], where);
  insist(instanceIds.has(entry.section_instance_id),
    `${where}.section_instance_id: unknown section instance`);
  text(entry.name, `${where}.name`);
  integer(entry.removed_bars, 1, LIMITS.bars, `${where}.removed_bars`);
  text(entry.decision_ref, `${where}.decision_ref`);
  return entry;
}

function parsePlacementV2(placement, where, outputTrackIds, timelineBounds) {
  insist(placement && typeof placement === 'object', `${where}: expected object`);
  keys(placement, ['output_track_id', 'output_bar_range', 'material_id', 'mode', 'optional',
    'tail_policy', 'source_ref'], where);
  insist(outputTrackIds.has(placement.output_track_id),
    `${where}.output_track_id: unknown output track`);
  insist(Array.isArray(placement.output_bar_range) && placement.output_bar_range.length === 2,
    `${where}.output_bar_range: expected [start, end]`);
  const [start, end] = placement.output_bar_range;
  integer(start, 0, LIMITS.bars, `${where}.output_bar_range[0]`);
  integer(end, 0, LIMITS.bars, `${where}.output_bar_range[1]`);
  insist(start <= end, `${where}.output_bar_range: start must be <= end`);
  if (timelineBounds) {
    insist(start >= timelineBounds.min && end <= timelineBounds.max,
      `${where}.output_bar_range: out of timeline bounds`);
  }
  text(placement.material_id, `${where}.material_id`);
  insist(['live', 'loop', 'unresolved'].includes(placement.mode),
    `${where}.mode: expected one of live|loop|unresolved`);
  insist(typeof placement.optional === 'boolean', `${where}.optional: expected boolean`);
  if (placement.tail_policy !== undefined && placement.tail_policy !== null) {
    insist(TAIL_POLICIES_V2.includes(placement.tail_policy),
      `${where}.tail_policy: expected one of ${TAIL_POLICIES_V2.join('|')}`);
  }
  if (placement.source_ref !== undefined && placement.source_ref !== null) {
    parseSourceRefV2(placement.source_ref, `${where}.source_ref`);
  }
  return placement;
}

function parseVariantSelectionV2(selection, where, patternIds) {
  insist(selection && typeof selection === 'object', `${where}: expected object`);
  keys(selection, ['pattern_id', 'occurrence_index', 'variant_id', 'source_ref'], where);
  insist(typeof selection.pattern_id === 'string' && patternIds.has(selection.pattern_id),
    `${where}.pattern_id: unknown pattern id`);
  integer(selection.occurrence_index, 1, LIMITS.sections, `${where}.occurrence_index`);
  text(selection.variant_id, `${where}.variant_id`);
  if (selection.source_ref !== undefined && selection.source_ref !== null) {
    parseSourceRefV2(selection.source_ref, `${where}.source_ref`);
  }
  return selection;
}

// Standalone validator for the additive performance-plan foundation, mirroring
// v2_schema.py's validate_performance_plan. Deliberately does NOT re-implement
// GP track-output building; it only confirms the new executable-plan shape is
// self-consistent before the source-copy writer consumes it.
function parsePerformancePlanV2(raw) {
  insist(raw.plan_version === PLAN_VERSION_V2,
    `plan_version: expected ${PLAN_VERSION_V2} (recompile with compile_performance.py)`);
  parseFingerprintsV2(raw.fingerprints, 'fingerprints');
  const patternIds = new Set((raw.patterns || []).map((p) => p.id));
  insist(Array.isArray(raw.instruments), 'instruments: expected array');
  raw.instruments.forEach((inst, i) => parseInstrumentV2(inst, `instruments[${i}]`, patternIds));
  insist(Array.isArray(raw.section_instances), 'section_instances: expected array');
  raw.section_instances.forEach((inst, i) => parseSectionInstanceV2(inst, `section_instances[${i}]`));
  const instanceIds = new Set(raw.section_instances.map((i) => i.id));
  insist(Array.isArray(raw.output_timeline), 'output_timeline: expected array');
  raw.output_timeline.forEach((e, i) =>
    parseOutputTimelineEntryV2(e, `output_timeline[${i}]`, instanceIds));
  const sortedIndexes = raw.output_timeline.map((e) => e.output_index).slice().sort((a, b) => a - b);
  sortedIndexes.forEach((idx, i) => insist(idx === i,
    `output_timeline: expected a contiguous 0-based sequence of output_index`));
  const timelineBounds = raw.output_timeline.length
    ? { min: 0, max: raw.output_timeline.length - 1 } : null;
  insist(Array.isArray(raw.removal_ledger), 'removal_ledger: expected array');
  raw.removal_ledger.forEach((e, i) =>
    parseRemovalLedgerEntryV2(e, `removal_ledger[${i}]`, instanceIds));
  const outputTrackIds = new Set();
  for (const inst of raw.instruments) {
    for (const t of inst.output_tracks) outputTrackIds.add(t.id);
  }
  insist(Array.isArray(raw.placements), 'placements: expected array');
  raw.placements.forEach((p, i) =>
    parsePlacementV2(p, `placements[${i}]`, outputTrackIds, timelineBounds));
  insist(Array.isArray(raw.variant_selections), 'variant_selections: expected array');
  raw.variant_selections.forEach((s, i) =>
    parseVariantSelectionV2(s, `variant_selections[${i}]`, patternIds));
  if (raw.tail_policy !== undefined && raw.tail_policy !== null) {
    insist(TAIL_POLICIES_V2.includes(raw.tail_policy),
      `tail_policy: expected one of ${TAIL_POLICIES_V2.join('|')}`);
  }
  return raw;
}

function validateOnly(args) {
  const booleans = new Set(['--validate-only', '--assert-final']);
  const trailing = args.slice(2);
  insist(args.length >= 2 && args.length <= 4 &&
    args[0] === '--manifest' && !!args[1] && !args[1].startsWith('--') &&
    trailing.every((a) => booleans.has(a)) && new Set(trailing).size === trailing.length,
  'usage: node write_arrangement.mjs --manifest <json> --validate-only [--assert-final]');
  const manifestPath = path.resolve(args[1]);
  insist(fs.statSync(manifestPath).size <= LIMITS.bytes, 'manifest exceeds 1 MB');
  const raw = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  insist(Number.isSafeInteger(raw.version) && raw.version === 2,
    'manifest.version: expected 2; legacy output manifests are retired');
  const assertFinal = args.includes('--assert-final');
  assertFinal ? assertFinalReadyV2(parseManifestV2(raw)) : parseManifestV2(raw);
  console.log(JSON.stringify({ status: 'valid', version: raw.version }));
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

// Mirrors v2_schema.py's `canonical_sha256`: SHA-256 over a canonical (recursively
// sorted-key, compact) JSON encoding, so a parsed JSON value hashes identically whether it
// was produced by Python's `json.dumps(obj, sort_keys=True, separators=(",", ":"),
// ensure_ascii=False)` or this encoder. Used only to recompute/verify the analysis/
// resolutions fingerprints a compiled performance plan embeds (requirement 1's preflight);
// never used to decide trust on its own without those original input files.
function canonicalSha256(value) {
  const encode = (v) => {
    if (v === null || v === undefined) return 'null';
    if (typeof v === 'number') {
      insist(Number.isFinite(v), 'canonicalSha256: non-finite number cannot be fingerprinted');
      return JSON.stringify(v);
    }
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (typeof v === 'string') return JSON.stringify(v);
    if (Array.isArray(v)) return `[${v.map(encode).join(',')}]`;
    if (typeof v === 'object') {
      const sortedKeys = Object.keys(v).sort();
      return `{${sortedKeys.map((k) => `${JSON.stringify(k)}:${encode(v[k])}`).join(',')}}`;
    }
    throw new Error('canonicalSha256: unsupported value type');
  };
  return createHash('sha256').update(encode(value), 'utf8').digest('hex');
}

// Staging, verification, and atomic no-clobber publication for source-copy output.
// Python's os.rename on Windows refuses an existing destination, unlike Node's
// replace-on-rename behavior. On other platforms hard-link publication is no-clobber.
function publishAtomically(output, bytes, verifyFn) {
  insist(bytes.length > 0 && bytes.length <= 16 * 1024 * 1024, 'export exceeds 16 MiB');
  const stage = path.join(path.dirname(output), `.${path.basename(output)}.${randomUUID()}.stage.gp`);
  try {
    fs.writeFileSync(stage, bytes, { flag: 'wx' });
    verifyFn(stage);
    insist(!fs.existsSync(output), 'output already exists');
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

// --- v2 source-copy build (performance-arrangement.json -> arrangement.gp) -----
// Resolves each selected LOOP pattern's `source_ref` against the digest-matched GPIF
// source via `source_compiler.mjs` and copies its field matrix directly into brand-new
// alphaTab model objects; never reuses/clones the resolved source objects and never
// falls back to a numeric reconstruction. A single constant tempo_bpm/meter applies to
// the whole arrangement (parity with v1); every copied pattern must agree with it.
function sourceRefForPattern(spansByTrack, pattern) {
  const spanMap = spansByTrack.get(pattern._trackId);
  insist(spanMap, `patterns[${pattern.id}]: references unknown track ${pattern._trackId}`);
  let ref;
  for (const spanId of pattern.span_ids) {
    const span = spanMap.get(spanId);
    insist(span, `patterns[${pattern.id}]: references unknown span ${spanId}`);
    if (!span.source_ref) continue;
    if (!ref) { ref = span.source_ref; continue; }
    const same = ref.asset_sha256 === span.source_ref.asset_sha256 &&
      ref.asset_relative_path === span.source_ref.asset_relative_path &&
      ref.track_index === span.source_ref.track_index &&
      (ref.staff_index ?? 0) === (span.source_ref.staff_index ?? 0) &&
      JSON.stringify(ref.voice_indices) === JSON.stringify(span.source_ref.voice_indices) &&
      JSON.stringify(ref.written_master_bars) === JSON.stringify(span.source_ref.written_master_bars);
    insist(same, `patterns[${pattern.id}]: its span_ids disagree on source_ref identity ` +
      '(performed occurrences are context, not source lookup identity, but the written ' +
      'content location they point at must agree)');
  }
  return ref;
}

// Requirement 2/3: `buildV2` now consumes the compiled plan's `instruments`/
// `output_tracks`/`output_timeline`/`placements`/`variant_selections` directly - it never
// rebuilds a single instrument-name-keyed track map from `spec.patterns`/`spec.sections`
// the way the pre-plan build path did. Track identity is `output_track.id` (display names,
// e.g. duplicated analysis track names, are never used as a lookup key). Every instrument's
// LIVE and UNRESOLVED output tracks are always authored, even when they end up fully silent.
function sameSourceRefIdentity(a, b) {
  return a.asset_sha256 === b.asset_sha256 && a.asset_relative_path === b.asset_relative_path &&
    a.track_index === b.track_index && (a.staff_index ?? 0) === (b.staff_index ?? 0) &&
    JSON.stringify(a.voice_indices) === JSON.stringify(b.voice_indices) &&
    JSON.stringify(a.written_master_bars) === JSON.stringify(b.written_master_bars);
}

function buildV2(spec, songDir) {
  const raw = spec.raw;
  insist(Number.isSafeInteger(spec.tempoBpm) && typeof spec.meter === 'string',
    'v2 source-copy export requires top-level tempo_bpm and meter: a single constant ' +
    'value for the whole arrangement, matching v1\'s capabilities');
  const match = /^([1-9]\d?)\/(1|2|4|8|16|32)$/.exec(spec.meter);
  const numerator = Number(match[1]);
  const denominator = Number(match[2]);
  const barUnits = numerator * 64 / denominator;
  insist(Number.isInteger(barUnits), 'meter: bar must fit 1/16-quarter grid');

  const totalBars = raw.output_timeline.length;
  insist(totalBars >= 1 && totalBars <= LIMITS.bars,
    `output_timeline: expected 1..${LIMITS.bars} bars`);

  const spansByTrack = new Map(spec.tracks.map((t) => [t.id, new Map(t.spans.map((s) => [s.id, s]))]));
  const tracksById = new Map(spec.tracks.map((t) => [t.id, t]));
  const tracksByName = new Map();
  for (const t of spec.tracks) {
    if (!tracksByName.has(t.name)) tracksByName.set(t.name, []);
    tracksByName.get(t.name).push(t);
  }
  const patternById = new Map(spec.patterns.map((p) => [p.id, p]));
  const spanById = new Map();
  for (const track of spec.tracks) {
    for (const span of track.spans) spanById.set(span.id, span);
  }

  // --- requirement 2: deterministic LIVE/loop-pattern(s)/UNRESOLVED order, trusted
  // straight from the compiled plan (already validated by compile_performance.py /
  // v2_schema.py); the writer never re-derives its own track ordering. ---------------
  const outputTrackMeta = new Map(); // output_track.id -> { id, instrumentId, analysisTrack, role, patternId, gpTrackName }
  const trackOrder = [];
  const instrumentDisplayNameById = new Map(); // instrument.id -> display name (for ambiguity below)
  for (const [instrumentIndex, instrument] of raw.instruments.entries()) {
    // `instrument.analysis_track` holds the stable track id for any plan compiled by
    // compile_performance.py. A hand-authored/legacy plan that instead gives a track
    // *name* is accepted only when that name is unambiguous (requirement 1's
    // deterministic adapter); ambiguous names are rejected with an explicit
    // diagnostic rather than guessed.
    let trackRecord = tracksById.get(instrument.analysis_track);
    if (!trackRecord) {
      const candidates = tracksByName.get(instrument.analysis_track) || [];
      insist(candidates.length !== 0, `instruments[${instrument.id}]: references unknown ` +
        `analysis track ${instrument.analysis_track}`);
      insist(candidates.length === 1, `instruments[${instrument.id}]: analysis track name ` +
        `${instrument.analysis_track} is shared by ${candidates.length} tracks; this plan ` +
        'must be recompiled with compile_performance.py so analysis_track references a ' +
        'stable track id instead of an ambiguous name');
      [trackRecord] = candidates;
    }
    instrumentDisplayNameById.set(instrument.id, trackRecord.name);
    for (const ot of instrument.output_tracks) {
      // the exported GP track name uses the track's display `name` (never the id, and
      // never assumed unique -- duplicate display names are always supported).
      const meta = { id: ot.id, instrumentId: instrument.id, analysisTrack: trackRecord.name,
        analysisRole: trackRecord.role || null, role: ot.role, patternId: ot.pattern_id,
        instrumentIndex, logicalIndex: trackOrder.length };
      insist(!outputTrackMeta.has(ot.id), `instruments: duplicate output_track.id ${ot.id}`);
      outputTrackMeta.set(ot.id, meta);
      trackOrder.push(meta);
    }
  }
  insist(trackOrder.length <= LIMITS.patterns,
    'instruments: expected at most ' + LIMITS.patterns + ' bounded logical output tracks');

  // --- requirement 6: duplicate analysis-track display names are always legal (stable
  // ids are the real identity -- see v2_schema.py), but an exported GP track name built
  // purely from `${displayName} (${role})` is ambiguous whenever two *different*
  // instruments share a display name (their `(role)` suffixes collide too). Disambiguate
  // deterministically by appending the instrument id only when genuinely ambiguous, so an
  // unambiguous plan's GP names are unchanged and this stays a readable, round-trippable
  // convention for `verifyV2`/the worksheet-vs-GP parity checker to assert against. --------
  const instrumentNameCounts = new Map();
  for (const name of instrumentDisplayNameById.values()) {
    instrumentNameCounts.set(name, (instrumentNameCounts.get(name) || 0) + 1);
  }
  for (const meta of trackOrder) {
    const ambiguous = (instrumentNameCounts.get(meta.analysisTrack) || 0) > 1;
    meta.gpTrackName = ambiguous
      ? `${meta.analysisTrack} [${meta.instrumentId}] (${meta.role})`
      : `${meta.analysisTrack} (${meta.role})`;
  }

  // --- requirement 7: conflicting overlapping placements are rejected per output track;
  // the *same* source material reused across different output tracks is a legitimate,
  // independent placement and is never flagged here. ----------------------------------
  const placementsByTrack = new Map(trackOrder.map((t) => [t.id, []]));
  for (const placement of raw.placements) {
    insist(placementsByTrack.has(placement.output_track_id),
      `placements: unknown output_track_id ${placement.output_track_id}`);
    placementsByTrack.get(placement.output_track_id).push(placement);
  }
  for (const [trackId, list] of placementsByTrack) {
    const sorted = [...list].sort((a, b) => a.output_bar_range[0] - b.output_bar_range[0]);
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1];
      const cur = sorted[i];
      insist(cur.output_bar_range[0] > prev.output_bar_range[1],
        `output track ${trackId}: conflicting overlapping placements (bars ` +
        `${prev.output_bar_range[0]}-${prev.output_bar_range[1]} and ` +
        `${cur.output_bar_range[0]}-${cur.output_bar_range[1]})`);
    }
    placementsByTrack.set(trackId, sorted);
  }

  // --- requirement 6: lazily-resolved source (an all-rest/cue-only/no-loop plan never
  // needs `source.capabilities` to include `source_bar_reuse`, and never touches the
  // asset file at all) and requirement 7's caching of plain-data compiled descriptors -
  // never the mutable alphaTab `Score`/`Bar` objects `SourceResolver` parses internally. ---
  const resolver = new SourceResolver(songDir);
  let resolvedSource = null;
  const getResolvedSource = () => {
    if (!resolvedSource) resolvedSource = resolver.resolve(spec.source);
    return resolvedSource;
  };
  const sourceRefKey = (ref) => JSON.stringify([ref.asset_sha256, ref.asset_relative_path,
    ref.track_index, ref.staff_index ?? 0, ref.voice_indices, ref.written_master_bars]);
  const assertSourceRefMatchesDeclaredSource = (ref, where) => insist(
    ref.asset_sha256 === spec.source.asset_sha256 &&
    ref.asset_relative_path === spec.source.asset_relative_path,
  `${where}: source_ref asset does not match the performance's declared source`);

  // --- requirement 3: concrete material selection. `preserve_variants` LOOP patterns
  // carry no single pattern-level source_ref (span occurrences legitimately disagree),
  // so their placements are bound to a concrete per-occurrence `variant_selections` entry
  // instead; `use_canonical`/plain patterns already carry a single coherent source_ref on
  // the placement itself (attached by compile_performance.py's `pattern_source_ref`,
  // mirrored here by `sourceRefForPattern` as a redundant coherence check). An unavailable
  // variant_id or any source_ref the pattern's own spans disagree with is a hard error,
  // never silently substituted or invented. ------------------------------------------
  const occurrenceCounters = new Map();
  const nextOccurrenceIndex = (patternId) => {
    const next = (occurrenceCounters.get(patternId) || 0) + 1;
    occurrenceCounters.set(patternId, next);
    return next;
  };
  const variantSelectionByKey = new Map(
    raw.variant_selections.map((s) => [`${s.pattern_id}:${s.occurrence_index}`, s]));

  const loopMaterialCache = new Map();
  function compileLoopMaterial(pattern, placement, occurrenceIndex, where) {
    let sourceRef = placement.source_ref;
    let cacheKey;
    if (sourceRef) {
      const crossCheck = sourceRefForPattern(spansByTrack, pattern);
      insist(crossCheck && sameSourceRefIdentity(crossCheck, sourceRef),
        `${where}: placement.source_ref disagrees with this pattern's own span source_ref`);
      cacheKey = `ref:${sourceRefKey(sourceRef)}`;
    } else {
      const selection = variantSelectionByKey.get(`${pattern.id}:${occurrenceIndex}`);
      insist(selection, `${where}: preserve_variants pattern has no variant_selections ` +
        `entry for occurrence ${occurrenceIndex}`);
      const declared = (pattern.variants || []).find((v) => v.id === selection.variant_id);
      insist(declared, `${where}: variant_selections names variant_id ` +
        `${JSON.stringify(selection.variant_id)}, which is not a declared variant of this ` +
        'pattern (an unavailable variant is never substituted or invented)');
      sourceRef = selection.source_ref;
      insist(sourceRef, `${where}: selected variant ${selection.variant_id} has no ` +
        'source_ref to copy from');
      cacheKey = `occ:${pattern.id}:${occurrenceIndex}`;
    }
    if (loopMaterialCache.has(cacheKey)) return loopMaterialCache.get(cacheKey);
    assertSourceRefMatchesDeclaredSource(sourceRef, where);
    const compiled = compileSourcePattern(getResolvedSource(), sourceRef, where);
    assertFingerprintMatches(compiled, sourceRef, where);
    const expectedBars = sourceRef.written_master_bars[1] - sourceRef.written_master_bars[0] + 1;
    insist(pattern.bars === expectedBars,
      `${where}: declared bars (${pattern.bars}) disagrees with source_ref span (${expectedBars})`);
    insist(compiled.numerator === numerator && compiled.denominator === denominator,
      `${where}: source meter ${compiled.numerator}/${compiled.denominator} disagrees with ` +
      `the arrangement's declared meter ${spec.meter}`);
    insist(compiled.tempoBpm === spec.tempoBpm,
      `${where}: source tempo ${compiled.tempoBpm} disagrees with the arrangement's ` +
      `declared tempo_bpm ${spec.tempoBpm}`);
    loopMaterialCache.set(cacheKey, compiled);
    return compiled;
  }

  // --- requirement 4: source-backed LIVE/UNRESOLVED one-off spans, bounded and digest/
  // fingerprint checked; a "hard" integrity failure (wrong/missing/tampered asset, or a
  // fingerprint mismatch) always propagates and fails the build, while a "soft" failure
  // (unsupported notation: extra voice, boundary tie/grace/tuplet, unsupported effect,
  // meter/tempo drift, or span too long for `ONE_OFF_SPAN_LIMITS.maxBars`) falls back to
  // an explicit labeled cue instead - never an indistinguishable plain rest. -----------
  const HARD_FAILURE_RE = /does not match|not found|escapes the song directory|exceeds|not a modern ZIP|hardened inspector|content_fingerprint/;
  const oneOffCache = new Map();
  function tryCompileOneOff(pattern, placement, where) {
    const sourceRef = placement.source_ref;
    if (!sourceRef) return null;
    if (!Array.isArray(spec.source.capabilities) ||
      !spec.source.capabilities.includes('source_bar_reuse')) return null;
    if (sourceRef.asset_sha256 !== spec.source.asset_sha256 ||
      sourceRef.asset_relative_path !== spec.source.asset_relative_path) return null;
    const [s, e] = sourceRef.written_master_bars || [];
    if (!Number.isInteger(s) || !Number.isInteger(e) || e - s + 1 > ONE_OFF_SPAN_LIMITS.maxBars) {
      return null;
    }
    if (pattern) {
      const crossCheck = sourceRefForPattern(spansByTrack, pattern);
      if (crossCheck && !sameSourceRefIdentity(crossCheck, sourceRef)) return null;
    }
    const cacheKey = sourceRefKey(sourceRef);
    if (oneOffCache.has(cacheKey)) return oneOffCache.get(cacheKey);
    let compiled;
    try {
      compiled = compileSourceSpan(getResolvedSource(), sourceRef, where);
      assertFingerprintMatches(compiled, sourceRef, where);
    } catch (error) {
      const message = String((error && error.message) || error);
      if (HARD_FAILURE_RE.test(message)) throw error;
      compiled = null; // unsupported notation: an explicit labeled cue takes over instead
    }
    if (compiled && (compiled.numerator !== numerator || compiled.denominator !== denominator ||
      compiled.tempoBpm !== spec.tempoBpm)) {
      compiled = null; // meter/tempo drift from the arrangement's declared constants: soft-fail to a cue
    }
    oneOffCache.set(cacheKey, compiled);
    return compiled;
  }

  // barContent[outputTrackId][globalBarIndex] = compiled source bar, or null for a rest.
  // cueText[outputTrackId][globalBarIndex] = an optional explicit labeled-cue string
  // (requirement 4/5): LIVE/UNRESOLVED material that is not source-backed, and any
  // retained-OPTIONAL material (on whichever physical role track it already landed on),
  // always gets a visible marker here rather than an indistinguishable plain rest.
  const barContent = new Map(trackOrder.map((t) => [t.id, Array(totalBars).fill(null)]));
  const cueText = new Map(trackOrder.map((t) => [t.id, Array(totalBars).fill(null)]));
  const instrumentInfo = new Map();
  const ensureInstrumentInfo = (instrumentId, compiled) => {
    const existing = instrumentInfo.get(instrumentId);
    if (!existing) {
      instrumentInfo.set(instrumentId, { kind: compiled.kind, tuningMidi: compiled.tuningMidi,
        capoFret: compiled.capoFret, playback: compiled.playback,
        sourceColor: compiled.sourceColor });
      return;
    }
    insist(existing.kind === compiled.kind &&
      JSON.stringify(existing.tuningMidi) === JSON.stringify(compiled.tuningMidi) &&
      existing.capoFret === compiled.capoFret &&
      JSON.stringify(existing.playback) === JSON.stringify(compiled.playback),
    `instrument ${instrumentId}: its placements disagree on instrument kind/tuning/capo/playback`);
  };

  for (const meta of trackOrder) {
    for (const placement of placementsByTrack.get(meta.id)) {
      const [start, end] = placement.output_bar_range;
      const rangeLen = end - start + 1;
      const where = `placements[output_track=${meta.id},bars=${start}-${end}]`;
      const pattern = patternById.get(placement.material_id);
      const span = spanById.get(placement.material_id);
      insist(pattern || (span && placement.mode === 'live' && span.disposition === 'LIVE'),
        `${where}: material_id must name a pattern, or an analyzed LIVE span for a live placement`);
      if (placement.mode === 'loop') {
        insist(meta.role === 'loop' && meta.patternId === pattern.id,
          `${where}: a loop placement must target its own pattern's dedicated output track`);
        const occurrenceIndex = nextOccurrenceIndex(pattern.id);
        const compiled = compileLoopMaterial(pattern, placement, occurrenceIndex, where);
        ensureInstrumentInfo(meta.instrumentId, compiled);
        const cycles = Math.floor(rangeLen / compiled.bars.length);
        const fullCycleBars = cycles * compiled.bars.length;
        // requirement 6: explicit reviewed tail policy, never silent truncation/wrap.
        insist(placement.tail_policy || fullCycleBars === rangeLen,
          `${where}: a partial loop cycle requires an explicit reviewed tail_policy`);
        for (let i = 0; i < rangeLen; i++) {
          if (i >= fullCycleBars) {
            if (placement.tail_policy === 'reviewed_wrap_full_cycles_only') continue; // leftover stays a rest
            insist(placement.tail_policy === 'reviewed_truncate' || fullCycleBars === rangeLen,
              `${where}: unsupported tail_policy for a partial loop cycle`);
          }
          barContent.get(meta.id)[start + i] = compiled.bars[i % compiled.bars.length];
        }
      } else {
        insist(meta.role === placement.mode,
          `${where}: a ${placement.mode} placement must target the instrument's ${placement.mode} track`);
        const label = placement.mode === 'live' ? 'LIVE' : 'UNRESOLVED';
        const compiled = tryCompileOneOff(pattern || null, placement, where);
        if (compiled) {
          insist(compiled.bars.length === rangeLen,
            `${where}: source-backed span is ${compiled.bars.length} bars but the ` +
            `placement spans ${rangeLen} bars`);
          ensureInstrumentInfo(meta.instrumentId, compiled);
          for (let i = 0; i < rangeLen; i++) barContent.get(meta.id)[start + i] = compiled.bars[i];
        } else {
          for (let i = 0; i < rangeLen; i++) {
            cueText.get(meta.id)[start + i] = i === 0 ?
              `${label}: bars ${start + 1}-${end + 1}` : `${label} (cont.)`;
          }
        }
      }
      if (placement.optional) {
        for (let i = 0; i < rangeLen; i++) {
          const bi = start + i;
          const existing = cueText.get(meta.id)[bi];
          cueText.get(meta.id)[bi] = existing ? `${existing} [OPTIONAL]` : '[OPTIONAL]';
        }
      }
    }
  }

  const barHasNotes = (bar) => bar !== null &&
    bar.beats.some((beat) => beat.notes.length > 0);
  const optionalOnly = (meta) => {
    const placements = placementsByTrack.get(meta.id);
    return placements.length > 0 && placements.every((placement) => placement.optional);
  };
  const exportGroup = (meta) => meta.role === 'unresolved' ? 2 : optionalOnly(meta) ? 1 : 0;
  const usesReadableLayout = raw.export_layout?.version === 1;
  const exportedTrackOrder = usesReadableLayout
    ? trackOrder.filter((meta) => barContent.get(meta.id).some(barHasNotes))
      .sort((left, right) => exportGroup(left) - exportGroup(right) ||
        left.instrumentIndex - right.instrumentIndex || left.logicalIndex - right.logicalIndex)
    : [...trackOrder];
  insist(exportedTrackOrder.length <= LIMITS.tracks,
    `arrangement has ${exportedTrackOrder.length} notated output tracks after pruning; ` +
    `the exporter supports at most ${LIMITS.tracks}`);
  insist(exportedTrackOrder.length > 0, usesReadableLayout
    ? 'arrangement has no notated material after removing zero-note tracks; LIVE/UNRESOLVED ' +
      'cue requirements remain in the worksheet, but a cue-only Guitar Pro score is not useful'
    : 'arrangement has no output tracks');
  for (const meta of exportedTrackOrder) {
    meta.optionalOnly = optionalOnly(meta);
    if (usesReadableLayout && meta.optionalOnly) meta.gpTrackName += ' [OPTIONAL]';
  }

  const settings = new alphaTab.Settings();
  const score = new model.Score();
  const partial = spec.publication.status === 'partial';
  score.title = partial ? 'Arrangement (Partial)' : 'Arrangement';
  score.subTitle = partial ? 'PARTIAL - unreliable live-loop reduction' :
    'FINAL live-loop reduction';
  score.notices = partial ? spec.publication.limitations.join('; ') : '';

  const sectionsById = new Map((raw.sections || []).map((s) => [s.id, s]));
  const instancesById = new Map((raw.section_instances || []).map((s) => [s.id, s]));
  const boundaries = new Map();
  for (const entry of raw.output_timeline) {
    if (entry.source_section_bar !== 0) continue;
    const instance = instancesById.get(entry.section_instance_id);
    if (instance) boundaries.set(entry.output_index, instance);
  }
  for (let bi = 0; bi < totalBars; bi++) {
    const master = new model.MasterBar();
    master.timeSignatureNumerator = numerator;
    master.timeSignatureDenominator = denominator;
    if (bi === 0) master.tempoAutomations.push(
      model.Automation.buildTempoAutomation(false, 0, spec.tempoBpm, 2));
    if (boundaries.has(bi)) {
      const instance = boundaries.get(bi);
      const sectionDef = sectionsById.get(instance.section_id);
      const marker = new model.Section();
      marker.marker = (sectionDef && sectionDef.empty_policy) ?? instance.name;
      marker.text = instance.name;
      master.section = marker;
    }
    score.addMasterBar(master);
  }

  let beatCount = 0;
  for (const [exportIndex, meta] of exportedTrackOrder.entries()) {
    const info = instrumentInfo.get(meta.instrumentId) ||
      { kind: 'pitched', tuningMidi: null, capoFret: null, playback: null };
    const track = new model.Track();
    track.name = meta.gpTrackName;
    const isBass = typeof meta.analysisRole === 'string' &&
      meta.analysisRole.toLowerCase().includes('bass');
    if (usesReadableLayout) {
      track.color = info.kind === 'percussion'
        ? new model.Color(0, 0, 255)
        : isBass ? new model.Color(255, 255, 0) : new model.Color(255, 0, 0);
    }
    if (info.playback) {
      track.playbackInfo.program = info.playback.program;
      track.playbackInfo.bank = info.playback.bank;
      track.playbackInfo.volume = info.playback.volume;
      track.playbackInfo.balance = info.playback.balance;
    }
    score.addTrack(track);
    const staff = new model.Staff();
    if (info.kind === 'fretted') {
      staff.stringTuning = new model.Tuning('', [...info.tuningMidi].reverse());
      staff.capo = info.capoFret;
      staff.showTablature = true;
    } else {
      staff.stringTuning = new model.Tuning('', []);
      staff.showTablature = false;
      staff.isPercussion = info.kind === 'percussion';
    }
    if (staff.isPercussion) {
      track.playbackInfo.primaryChannel = 9;
      track.playbackInfo.secondaryChannel = 9;
    } else {
      const channel = exportIndex % 15;
      track.playbackInfo.primaryChannel = channel >= 9 ? channel + 1 : channel;
      track.playbackInfo.secondaryChannel = track.playbackInfo.primaryChannel;
    }
    staff.showStandardNotation = true;
    track.addStaff(staff);
    const bars = barContent.get(meta.id);
    const texts = cueText.get(meta.id);
    for (let bi = 0; bi < totalBars; bi++) {
      const bar = new model.Bar();
      staff.addBar(bar);
      const voice = new model.Voice();
      bar.addVoice(voice);
      const compiledBar = bars[bi];
      if (compiledBar === null) {
        let amount = barUnits;
        let first = true;
        while (amount) {
          const step = UNITS.find((u) => u <= amount);
          insist(step !== undefined, 'unrepresentable rest');
          insist(++beatCount <= LIMITS.beats, `arrangement exceeds ${LIMITS.beats} beats`);
          const beat = new model.Beat();
          beat.duration = DURATIONS.get(step);
          if (first && texts[bi]) beat.text = texts[bi];
          first = false;
          voice.addBeat(beat);
          amount -= step;
        }
        continue;
      }
      let first = true;
      for (const compiledBeat of compiledBar.beats) {
        insist(++beatCount <= LIMITS.beats, `arrangement exceeds ${LIMITS.beats} beats`);
        const beat = new model.Beat();
        beat.duration = compiledBeat.duration;
        beat.dots = compiledBeat.dots;
        if (compiledBeat.tupletNumerator !== 1 || compiledBeat.tupletDenominator !== 1) {
          beat.tupletNumerator = compiledBeat.tupletNumerator;
          beat.tupletDenominator = compiledBeat.tupletDenominator;
        }
        if (first && texts[bi]) beat.text = texts[bi];
        first = false;
        for (const compiledNote of compiledBeat.notes) {
          const note = new model.Note();
          if (info.kind === 'fretted') {
            note.string = compiledNote.string;
            note.fret = compiledNote.fret;
          } else if (info.kind === 'percussion') {
            note.percussionArticulation = compiledNote.percussionArticulation;
          } else {
            note.octave = compiledNote.octave;
            note.tone = compiledNote.tone;
          }
          note.isPalmMute = compiledNote.isPalmMute;
          note.isLetRing = compiledNote.isLetRing;
          note.isHammerPullOrigin = compiledNote.isHammerPullOrigin;
          beat.addNote(note);
        }
        voice.addBeat(beat);
      }
    }
  }
  score.finish(settings);
  return { score, settings, numerator, denominator, barUnits, totalBars,
    logicalTrackOrder: trackOrder, trackOrder: exportedTrackOrder,
    instrumentInfo, barContent, cueText };
}

function verifyV2(spec, built, staged, worksheetPath) {
  const raw = spec.raw;
  const actual = inspect(staged);
  insist(actual.status === 'valid' && actual.format === 'gp-zip',
    `project inspector rejected GP7: ${actual.error || actual.status}`);
  insist(actual.track_count === built.trackOrder.length && actual.bar_count === built.totalBars,
    'round-trip track/bar count differs');
  const meterLabel = `${built.numerator}/${built.denominator}`;
  insist(actual.bars.every((b, i) => b.number === i + 1 && b.meter === meterLabel &&
    Math.abs(b.start_quarters - i * built.barUnits / 16) < 1e-9 &&
    b.length_quarters === built.barUnits / 16 && !b.repeat_open && b.repeat_close === null),
  'round-trip bar/meter/repeats differ');
  insist(actual.tempos.length === 1 && actual.tempos[0].position_quarters === 0 &&
    Math.abs(actual.tempos[0].bpm - spec.tempoBpm) < 1e-9, 'round-trip tempo differs');
  for (const [ti, meta] of built.trackOrder.entries()) {
    const info = built.instrumentInfo.get(meta.instrumentId) ||
      { kind: 'pitched', tuningMidi: null, capoFret: null, playback: null };
    const got = actual.tracks[ti];
    insist(got.name === meta.gpTrackName && got.staves.length === 1,
      `track ${ti}: identity differs`);
    const staff = got.staves[0];
    if (info.kind === 'fretted') insist(
      JSON.stringify(staff.tuning_midi) === JSON.stringify(info.tuningMidi) &&
      staff.capo_fret === info.capoFret, `track ${ti}: tuning/capo differs`);
    else insist(staff.tuning_midi === null, `track ${ti}: non-fretted staff acquired tuning`);
    if (raw.export_layout?.version === 1) {
      const expectedColor = info.kind === 'percussion'
        ? '#0000FF'
        : (typeof meta.analysisRole === 'string' &&
            meta.analysisRole.toLowerCase().includes('bass')) ? '#FFFF00' : '#FF0000';
      insist(typeof got.source_color === 'string' &&
        got.source_color.toUpperCase() === expectedColor,
      `track ${ti}: color differs (expected ${expectedColor}, got ${got.source_color})`);
    }
    if (info.playback) {
      insist(got.playback.program === info.playback.program &&
        got.playback.bank === info.playback.bank,
      `track ${ti}: playback instrument differs`);
    }
  }
  // The GPIF inspector does not expose section markers, bar-local text/cues, the
  // percussion flag or raw duration/dots/tuplet fields; reload through alphaTab for
  // those and for a full per-beat/per-note comparison against the compiled field matrix.
  const parsed = alphaTab.importer.ScoreLoader.loadScoreFromBytes(
    new Uint8Array(fs.readFileSync(staged)), new alphaTab.Settings());
  const partial = spec.publication.status === 'partial';
  insist(parsed.title === (partial ? 'Arrangement (Partial)' : 'Arrangement') &&
    parsed.subTitle === (partial ? 'PARTIAL - unreliable live-loop reduction' :
      'FINAL live-loop reduction') &&
    parsed.notices === (partial ? spec.publication.limitations.join('; ') : ''),
  'round-trip publication status/limitations differ');
  insist(parsed.tracks.every((t, i) =>
    t.staves.length === 1 && t.staves[0].isPercussion ===
      ((built.instrumentInfo.get(built.trackOrder[i].instrumentId) || {}).kind === 'percussion')),
  'round-trip percussion status differs');
  for (const [ti, meta] of built.trackOrder.entries()) {
    const info = built.instrumentInfo.get(meta.instrumentId);
    if (!info?.playback) continue;
    const playback = parsed.tracks[ti].playbackInfo;
    insist(playback.program === info.playback.program &&
      playback.bank === info.playback.bank &&
      playback.volume === info.playback.volume &&
      playback.balance === info.playback.balance,
    `track ${ti}: alphaTab playback metadata differs`);
  }

  const sectionsById = new Map((raw.sections || []).map((s) => [s.id, s]));
  const instancesById = new Map((raw.section_instances || []).map((s) => [s.id, s]));
  for (const entry of raw.output_timeline) {
    if (entry.source_section_bar !== 0) continue;
    const instance = instancesById.get(entry.section_instance_id);
    if (!instance) continue;
    const sectionDef = sectionsById.get(instance.section_id);
    insist(parsed.masterBars[entry.output_index].section?.text === instance.name &&
      parsed.masterBars[entry.output_index].section.marker ===
        ((sectionDef && sectionDef.empty_policy) ?? instance.name),
      `round-trip section ${instance.name} differs`);
  }

  for (const [ti, meta] of built.trackOrder.entries()) {
    const info = built.instrumentInfo.get(meta.instrumentId) ||
      { kind: 'pitched', tuningMidi: null, capoFret: null };
    const parsedStaff = parsed.tracks[ti].staves[0];
    const expectedBars = built.barContent.get(meta.id);
    const expectedText = built.cueText.get(meta.id);
    insist(parsedStaff.bars.length === expectedBars.length, `track ${ti}: round-trip bar count differs`);
    for (const [bi, compiledBar] of expectedBars.entries()) {
      const parsedBeats = parsedStaff.bars[bi].voices[0].beats;
      insist((parsedBeats[0]?.text ?? null) === expectedText[bi],
        `track ${ti} bar ${bi + 1}: round-trip cue/OPTIONAL marker text differs`);
      if (compiledBar === null) {
        insist(parsedBeats.every((b) => b.notes.length === 0),
          `track ${ti} bar ${bi + 1}: expected a silent rest bar`);
        continue;
      }
      insist(parsedBeats.length === compiledBar.beats.length,
        `track ${ti} bar ${bi + 1}: round-trip beat count differs`);
      for (const [beatIdx, compiledBeat] of compiledBar.beats.entries()) {
        const parsedBeat = parsedBeats[beatIdx];
        const tupletOk = compiledBeat.tupletNumerator === 1 && compiledBeat.tupletDenominator === 1
          ? !parsedBeat.hasTuplet
          : parsedBeat.hasTuplet && parsedBeat.tupletNumerator === compiledBeat.tupletNumerator &&
            parsedBeat.tupletDenominator === compiledBeat.tupletDenominator;
        insist(parsedBeat.duration === compiledBeat.duration &&
          parsedBeat.dots === compiledBeat.dots && tupletOk,
        `track ${ti} bar ${bi + 1} beat ${beatIdx}: round-trip duration/dots/tuplet differs`);
        insist(parsedBeat.notes.length === compiledBeat.notes.length,
          `track ${ti} bar ${bi + 1} beat ${beatIdx}: round-trip note count differs`);
        for (const [ni, compiledNote] of compiledBeat.notes.entries()) {
          const parsedNote = parsedBeat.notes[ni];
          if (info.kind === 'fretted') insist(
            parsedNote.string === compiledNote.string && parsedNote.fret === compiledNote.fret,
            `track ${ti} bar ${bi + 1} beat ${beatIdx} note ${ni}: string/fret differs`);
          else if (info.kind === 'percussion') insist(
            parsedNote.percussionArticulation === compiledNote.percussionArticulation,
            `track ${ti} bar ${bi + 1} beat ${beatIdx} note ${ni}: percussion articulation differs`);
          else insist(
            parsedNote.octave === compiledNote.octave && parsedNote.tone === compiledNote.tone,
            `track ${ti} bar ${bi + 1} beat ${beatIdx} note ${ni}: octave/tone differs`);
          insist(parsedNote.isPalmMute === compiledNote.isPalmMute &&
            parsedNote.isLetRing === compiledNote.isLetRing &&
            parsedNote.isHammerPullOrigin === compiledNote.isHammerPullOrigin,
          `track ${ti} bar ${bi + 1} beat ${beatIdx} note ${ni}: copied note effects differ`);
          const relationCoordinate = (destination) => {
            if (!destination) return null;
            const destinationBar = destination.beat.voice.bar.index;
            const destinationBeat = destination.beat.index;
            return [destinationBar - bi, destinationBeat, destination.index];
          };
          insist(JSON.stringify(relationCoordinate(parsedNote.hammerPullDestination)) ===
            JSON.stringify(compiledNote.hammerPullDestination),
          `track ${ti} bar ${bi + 1} beat ${beatIdx} note ${ni}: copied effect linkage differs`);
        }
      }
    }
  }

  if (worksheetPath) verifyWorksheetAgainstPlanV2(spec, built, worksheetPath);
}

// Requirement 5's deterministic worksheet-vs-plan-vs-GP parity verifier: reloads the
// already-rendered (by render_views.py --plan) `worksheet.md` Markdown and cross-checks
// it against the same compiled plan + just-built/just-verified GP score that `verifyV2`
// already trusts, so a hand-edited or stale worksheet is caught the same way a stale GP
// would be. This never re-derives render_views.py's Markdown independently (that would
// duplicate its own source of truth); it instead asserts the few facts that are unique
// to this three-way comparison: total bars, the deterministic GP track-name convention
// (requirement 6) actually present for every output track id, section markers, and
// optional/cue markers/placements, plus publication status/limitations visibility. The
// bar/time/transition-level cross-check already lives in check_worksheet.py's
// `check_published_against_plan` (Python is the single source of truth there); this
// function intentionally does not duplicate that regex-level Markdown parsing.
function verifyWorksheetAgainstPlanV2(spec, built, worksheetPath) {
  const raw = spec.raw;
  insist(fs.existsSync(worksheetPath), `--worksheet ${worksheetPath}: file does not exist`);
  const sheet = fs.readFileSync(worksheetPath, 'utf8');

  const statusLabel = spec.publication.status === 'final' ? 'Final' : 'Partial';
  insist(sheet.includes(`**Status: ${statusLabel}**`),
    `worksheet does not declare the expected **Status: ${statusLabel}** matching the plan's ` +
    'publication.status');
  insist(sheet.includes(`**Total:** ${built.totalBars} output bar`),
    `worksheet total bars does not match the GP-built total (${built.totalBars})`);

  // --- track ids / deterministic GP name convention (requirement 6) -----------------
  for (const meta of built.trackOrder) {
    insist(sheet.includes(`\`${meta.id}\``),
      `worksheet is missing output track id \`${meta.id}\` that the GP file actually builds`);
  }
  // Every GP track's exported name must follow the documented
  // `${displayName} [${instrumentId}]? (${role})` convention, deterministically
  // disambiguated only when ambiguous -- this is the "GP track names round-trip safely"
  // contract requirement 5/6 asks this verifier to hold, independent of verifyV2's own
  // internal round-trip assertion (which already re-derives the same `meta.gpTrackName`;
  // this instead re-derives the *worksheet's declared* instrument/track identity and
  // checks it is not silently diverging from what actually got built into the GP file).
  for (const instrument of raw.instruments) {
    insist(sheet.includes(`instrument \`${instrument.id}\``),
      `worksheet is missing instrument id \`${instrument.id}\` referenced by the compiled plan`);
  }

  // --- section markers: every retained section instance that actually produced a GP
  // section marker (boundaries) must be named in the worksheet's Structure headings. ----
  const sectionsById = new Map((raw.sections || []).map((s) => [s.id, s]));
  const instancesById = new Map((raw.section_instances || []).map((s) => [s.id, s]));
  const seenInstanceIds = new Set();
  for (const entry of raw.output_timeline) {
    if (entry.source_section_bar !== 0 || seenInstanceIds.has(entry.section_instance_id)) continue;
    seenInstanceIds.add(entry.section_instance_id);
    const instance = instancesById.get(entry.section_instance_id);
    if (!instance) continue;
    insist(sheet.includes(`section instance \`${instance.id}\``),
      `worksheet Structure is missing retained section instance \`${instance.id}\` ` +
      `("${instance.name}") that the GP file actually renders a marker for`);
  }
  // A deleted section instance must never surface in the Structure timeline (it never
  // produced a GP marker/bar at all) -- only in the separate Omissions/Edits section.
  const deletedIds = (raw.section_instances || [])
    .filter((si) => si.action === 'delete').map((si) => si.id);
  for (const deletedId of deletedIds) {
    const structureMatch = /## Structure\s*\n([\s\S]*?)(?=\n## |$)/.exec(sheet);
    insist(!structureMatch || !structureMatch[1].includes(`section instance \`${deletedId}\``),
      `worksheet Structure must not reference deleted section instance \`${deletedId}\``);
  }

  // --- optional markers/cues: every placement the GP file marks [OPTIONAL] (verifyV2
  // already asserted the GP cue text itself) must also be disclosed [OPTIONAL] in the
  // worksheet, keyed by the same output track id -- never silently dropped. -----------
  for (const placement of raw.placements || []) {
    if (!placement.optional) continue;
    const trackId = placement.output_track_id;
    const lineMatch = new RegExp(`^- \\w+ \`${trackId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\`.*$`,
      'm').exec(sheet);
    insist(lineMatch && lineMatch[0].includes('[OPTIONAL]'),
      `worksheet is missing the [OPTIONAL] marker for output track \`${trackId}\`, which the ` +
      'GP file marks optional');
  }

  // LIVE placements must remain visibly disclosed in the worksheet even when
  // their source notation copied successfully and therefore needs no GP cue.
  for (const placement of raw.placements || []) {
    if (placement.mode !== 'live') continue;
    const trackId = placement.output_track_id;
    const [start, end] = placement.output_bar_range;
    const cue = `manual cue: bars ${start + 1}-${end + 1}`;
    const lineMatch = new RegExp(
      `^- LIVE \`${trackId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\`.*` +
      `${cue.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}.*$`, 'm').exec(sheet);
    insist(lineMatch,
      `worksheet is missing LIVE disclosure for output track \`${trackId}\`, bars ` +
      `${start + 1}-${end + 1}`);
  }

  // --- publication metadata: Partial limitations must be visible verbatim. -----------
  if (spec.publication.status === 'partial') {
    for (const limitation of spec.publication.limitations) {
      insist(sheet.includes(limitation),
        `worksheet does not disclose publication limitation ${JSON.stringify(limitation)} ` +
        'that the GP file\'s score.notices carries');
    }
  }
}

// Requirement 1: flexible CLI accepting the always-required --performance/--song-dir/
// --output trio plus two optional preflight flags; `raw.plan_version` is mandatory (no
// hidden stale-plan acceptance - a legacy, pre-plan performance handoff is rejected
// outright here rather than silently falling back to the old pattern/section rebuild).
function mainV2(args) {
  const flagNames = ['--performance', '--song-dir', '--output', '--analysis', '--resolutions',
    '--worksheet'];
  const pairs = [];
  for (let i = 0; i < args.length; i += 2) pairs.push([args[i], args[i + 1]]);
  insist(args.length % 2 === 0 && args.length >= 6 &&
    pairs.every(([k, v]) => !!k && k.startsWith('--') && !!v && !v.startsWith('--')) &&
    new Set(pairs.map(([k]) => k)).size === pairs.length &&
    pairs.every(([k]) => flagNames.includes(k)) &&
    ['--performance', '--song-dir', '--output'].every((k) => pairs.some(([pk]) => pk === k)),
  'usage: node write_arrangement.mjs --performance <json> --song-dir <folder> --output <gp> ' +
    '[--analysis <arrangement.json>] [--resolutions <resolutions.json>] ' +
    '[--worksheet <worksheet.md>]');
  const options = Object.fromEntries(pairs);
  const performancePath = path.resolve(options['--performance']);
  const output = path.resolve(options['--output']);
  const songDir = path.resolve(options['--song-dir']);
  const worksheetPath = options['--worksheet'] ? path.resolve(options['--worksheet']) : null;
  insist(path.extname(output).toLowerCase() === '.gp', 'output must end in .gp');
  insist(performancePath !== output && !fs.existsSync(output), 'output already exists');
  insist(fs.statSync(performancePath).size <= LIMITS.bytes, 'performance handoff exceeds 1 MB');
  const raw = JSON.parse(fs.readFileSync(performancePath, 'utf8'));
  insist(Number.isSafeInteger(raw.version) && raw.version === 2,
    'performance handoff: only version 2 is supported for source-copy export');
  insist(Number.isSafeInteger(raw.plan_version) && raw.plan_version === PLAN_VERSION_V2,
    'performance handoff: missing/outdated plan_version - this build path requires a ' +
    'compiled performance plan (instruments/output_tracks/output_timeline/placements/' +
    'variant_selections); recompile performance-arrangement.json with ' +
    'compile_performance.py (no hidden stale-plan acceptance in this build path)');
  const spec = parsePerformanceV2(raw);
  parsePerformancePlanV2(raw);
  insist(spec.publication.status !== 'draft',
    'performance handoff: draft previews are not buildable into a .gp file; resolve to partial/final first');
  // Requirement 5: UNRESOLVED material/cues are only ever buildable inside a consented
  // Partial publication (draft previews are already rejected above; a Final publication
  // can never contain an unresolved decision and is blocked outright here).
  insist(spec.publication.status === 'partial' ||
    !spec.patterns.some((p) => p.final_disposition === 'UNRESOLVED'),
  'performance handoff: a labeled UNRESOLVED pattern cannot be built into a .gp file for a ' +
    'final publication; resolve it, or publish as a consented partial first');

  // Requirement 1's analysis/resolutions preflight: when the *original* analysis
  // manifest and/or resolutions document are supplied, recompute their canonical
  // fingerprints and reject a stale compiled plan outright rather than trusting the
  // embedded `fingerprints` block on faith. (Internal referential/structural
  // consistency of the compiled plan itself is always checked above via
  // `parsePerformancePlanV2`, with or without these optional flags.)
  if (options['--analysis']) {
    const analysisPath = path.resolve(options['--analysis']);
    insist(fs.statSync(analysisPath).size <= LIMITS.bytes, 'analysis manifest exceeds 1 MB');
    const analysisRaw = JSON.parse(fs.readFileSync(analysisPath, 'utf8'));
    insist(canonicalSha256(analysisRaw) === raw.fingerprints.analysis_sha256,
      'stale compiled plan: the supplied --analysis manifest does not match ' +
      'fingerprints.analysis_sha256; recompile performance-arrangement.json with ' +
      'compile_performance.py before building');
  }
  if (options['--resolutions']) {
    const resolutionsPath = path.resolve(options['--resolutions']);
    insist(fs.statSync(resolutionsPath).size <= LIMITS.bytes, 'resolutions exceeds 1 MB');
    const resolutionsRaw = JSON.parse(fs.readFileSync(resolutionsPath, 'utf8'));
    insist(canonicalSha256(resolutionsRaw) === raw.fingerprints.resolutions_sha256,
      'stale compiled plan: the supplied --resolutions document does not match ' +
      'fingerprints.resolutions_sha256; recompile performance-arrangement.json with ' +
      'compile_performance.py before building');
  }

  const built = buildV2(spec, songDir);
  const bytes = new alphaTab.exporter.Gp7Exporter().export(built.score, built.settings);
  publishAtomically(output, bytes, (stage) => verifyV2(spec, built, stage, worksheetPath));
  process.stdout.write(JSON.stringify({
    status: 'written',
    exported_tracks: built.trackOrder.map((track) => ({
      output_track_id: track.id,
      gp_track_name: track.gpTrackName,
      group: track.role === 'unresolved' ? 'unresolved' :
        track.optionalOnly ? 'optional' : 'core'
    }))
  }));
}

function validatePerformancePlanOnly(args) {
  const booleans = new Set(['--validate-only']);
  const trailing = args.slice(2);
  insist(args.length >= 2 && args.length <= 3 &&
    args[0] === '--performance' && !!args[1] && !args[1].startsWith('--') &&
    trailing.every((a) => booleans.has(a)) && new Set(trailing).size === trailing.length,
  'usage: node write_arrangement.mjs --performance <json> --validate-only');
  const performancePath = path.resolve(args[1]);
  insist(fs.statSync(performancePath).size <= LIMITS.bytes, 'performance handoff exceeds 1 MB');
  const raw = JSON.parse(fs.readFileSync(performancePath, 'utf8'));
  insist(Number.isSafeInteger(raw.version) && raw.version === 2,
    'performance handoff: only version 2 is supported');
  if (!Number.isSafeInteger(raw.plan_version)) {
    throw new Error('plan_version: missing - this performance-arrangement.json predates the ' +
      'performance-plan foundation; recompile it with compile_performance.py');
  }
  parsePerformanceV2(raw);
  parsePerformancePlanV2(raw);
  console.log(JSON.stringify({ status: 'valid', version: raw.version, plan_version: raw.plan_version }));
}

// Test-support entry point only (not part of the documented CLI contract): reads one
// JSON value from stdin and prints its canonical-fingerprint SHA-256, so cross-language
// parity tests can verify this file's `canonicalSha256` agrees with v2_schema.py's
// `canonical_sha256` on the exact same parsed value without duplicating either
// implementation in a test harness.
function mainCanonicalFingerprint() {
  const chunks = [];
  process.stdin.on('data', (c) => chunks.push(c));
  process.stdin.on('end', () => {
    const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    process.stdout.write(canonicalSha256(value));
  });
}

function main(args) {
  if (args[0] === '--canonical-fingerprint-stdin') {
    mainCanonicalFingerprint();
    return;
  }
  if (args.includes('--validate-only') && args[0] === '--performance') {
    validatePerformancePlanOnly(args);
    return;
  }
  if (args.includes('--validate-only')) {
    validateOnly(args);
    return;
  }
  if (args[0] === '--performance') {
    mainV2(args);
    return;
  }
  throw new Error(
    'legacy output manifests are retired; compile a version 2 performance plan and use ' +
    '--performance <performance-arrangement.json>');
}

try {
  main(process.argv.slice(2));
} catch (error) {
  console.error(`arrangement: ${String(error.message || error)}`);
  process.exitCode = 1;
}
