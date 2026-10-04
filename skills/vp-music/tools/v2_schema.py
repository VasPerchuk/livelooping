"""Shared, deterministic validation for the version 2 analysis-contract JSON.

This module is the Python-side mirror of the version 2 subset implemented in
`write_arrangement.mjs` (`parseManifestV2` and friends) plus the schema
extensions this slice adds: selected `patterns`, ordered `sections`, grouped
`decisions` (with `depends_on`/`applies_when`), and `source.capabilities`.
Keep both validators in agreement; see `analyze\\references\\arrangement-handoff.md`
for the authoritative contract text.

Two independent validation entry points, deliberately kept separate:

  * `load_analysis_manifest` validates the analysis-time handoff
    (`arrangement.json`): closed five-value dispositions, stable ids,
    structured decisions and optional selected patterns/sections. An
    `UNRESOLVED` disposition is allowed here.
  * `validate_resolutions` / `compile_performance_arrangement` validate the
    separate performance/publication compilation inputs (resolutions and
    publication data) and reject any `UNRESOLVED` disposition that survives
    into the compiled performance handoff.

Neither function touches the filesystem; callers (the renderer and the
compiler CLI) own all file I/O.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re

DISPOSITIONS = ("LOOP", "LIVE", "OPTIONAL", "UNRESOLVED", "REST")
MATCH_TIERS = ("exact", "normalized", "near", "legacy_unknown")
CONFIDENCES = ("confirmed", "likely", "estimated", "unknown")
CAPABILITIES = ("performed_order", "repeat_directions", "alternate_endings",
                "tempo_map", "written_notation", "source_bar_reuse")
SOURCE_FORMATS = ("gpif", "gpx", "gp3", "gp4", "gp5", "midi")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
METER_RE = re.compile(r"^([1-9]\d?)/(1|2|4|8|16|32)$")

# --- performance-plan foundation (finalize-contract) ------------------------
# Additive vocabulary layered on top of the five-disposition analysis contract.
# A disposition (LOOP/LIVE/OPTIONAL/UNRESOLVED/REST) remains a *musical*
# decision; `PHYSICAL_ROLES` is the separate, closed vocabulary for the kind
# of output track a placement actually lands on. OPTIONAL is retention/deletion
# *state*, never a track role: retained optional material plays on whichever
# LIVE/LOOP/UNRESOLVED track its resolved disposition already selected.
PHYSICAL_ROLES = ("live", "loop", "unresolved")
SECTION_ACTIONS = ("retain", "delete")
TAIL_POLICIES = ("reviewed_truncate", "reviewed_wrap_full_cycles_only",
                  "reviewed_no_partial_cycle")
# `publication.status` gains an explicit, non-publishable preview value: a
# Draft preview may show labeled UNRESOLVED/reference material, but it is
# never a legitimate target for `worksheet.md`/`arrangement.gp` publication
# (see CONTRACT.md's preview-path isolation requirement).
PUBLICATION_STATUSES = ("final", "partial", "draft")
PLAN_VERSION = 2


def _canonical_encode(value) -> str:
    """Recursively encode `value` to the same canonical compact JSON text that
    `write_arrangement.mjs`'s `canonicalSha256` produces, so a fingerprint
    computed on one side of the language boundary always matches a recompute
    on the other for any value that round-trips through `json.loads`/
    `JSON.parse`. The one real cross-language divergence this closes: Python's
    `json` module and JavaScript's `JSON.stringify` agree on every JSON value
    *except* a whole-valued finite float, which Python renders with a trailing
    `.0` (`120.0`) while JavaScript's single numeric type renders the same
    double as `120`. Every whole-valued finite float is therefore normalized
    to its bare integer text here; any non-finite float (`NaN`/`Infinity`/
    `-Infinity` - which `json.dumps` would otherwise silently emit as invalid,
    non-interoperable JSON tokens) is a hard error instead.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise Invalid("canonical_sha256: non-finite number cannot be fingerprinted")
        if value.is_integer():
            return str(int(value))
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(_canonical_encode(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            f"{json.dumps(str(k), ensure_ascii=False)}:{_canonical_encode(value[k])}"
            for k in sorted(value.keys())
        ) + "}"
    raise Invalid(f"canonical_sha256: unsupported value type {type(value).__name__}")


def canonical_sha256(obj) -> str:
    """Deterministic SHA-256 over a canonical (recursively sorted-key,
    compact) JSON encoding of `obj`, byte-identical to the JS mirror
    `canonicalSha256` in `write_arrangement.mjs` for any value reachable by
    parsing JSON text (see `_canonical_encode`). Used to fingerprint the
    immutable analysis manifest and the resolutions/performance inputs so a
    source-copy writer can detect a stale compiled artifact without re-reading
    mutable file timestamps.
    """
    encoded = _canonical_encode(obj)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class Invalid(ValueError):
    """Raised for any schema violation; message names the offending path."""


def _require(condition, where, message):
    if not condition:
        raise Invalid(f"{where}: {message}")


def _text(value, where, max_len=200):
    _require(isinstance(value, str) and value and len(value) <= max_len and
              value.strip() == value, where, "expected nonempty descriptive text")
    return value


def _relative_path(value, where):
    _text(value, where, 400)
    _require("\\" not in value and not re.match(r"^[A-Za-z]:", value) and
              not value.startswith("/") and ".." not in value.split("/"),
              where, "expected a song-relative forward-slash path")
    return value


def _sha256(value, where):
    _require(isinstance(value, str) and bool(SHA256_RE.match(value)), where,
              "expected 64-character lowercase hex SHA-256")
    return value


def _closed_keys(obj, allowed, where):
    _require(isinstance(obj, dict), where, "expected object")
    extra = set(obj) - set(allowed)
    _require(not extra, where, f"unsupported field(s): {', '.join(sorted(extra))}")


def _source_ref(ref, where):
    if ref is None:
        return None
    _closed_keys(ref, ("asset_sha256", "asset_relative_path", "track_index", "track_name",
                        "staff_index", "written_master_bars", "voice_indices",
                        "performed_occurrences", "ending_context", "content_fingerprint"), where)
    _sha256(ref.get("asset_sha256"), f"{where}.asset_sha256")
    _relative_path(ref.get("asset_relative_path"), f"{where}.asset_relative_path")
    if "content_fingerprint" in ref:
        _sha256(ref["content_fingerprint"], f"{where}.content_fingerprint")
    for field in ("written_master_bars", "voice_indices", "performed_occurrences"):
        if field not in ref:
            continue
        value = ref[field]
        _require(isinstance(value, list) and value, f"{where}.{field}", "expected nonempty array")
        for i, n in enumerate(value):
            _require(isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 100_000,
                      f"{where}.{field}[{i}]", "expected integer 0..100000")
    return ref


def _near_match(near, where):
    if near is None:
        return None
    _closed_keys(near, ("matching_bars", "total_bars", "divergent_bar_offsets"), where)
    total = near.get("total_bars")
    _require(isinstance(total, int) and total >= 1, f"{where}.total_bars", "expected positive integer")
    matching = near.get("matching_bars")
    _require(isinstance(matching, int) and 0 <= matching <= total,
              f"{where}.matching_bars", "expected 0..total_bars")
    offsets = near.get("divergent_bar_offsets")
    _require(isinstance(offsets, list) and offsets, f"{where}.divergent_bar_offsets",
              "expected nonempty array")
    for i, n in enumerate(offsets):
        _require(isinstance(n, int) and 0 <= n < total,
                  f"{where}.divergent_bar_offsets[{i}]", "out of range")
    return near


def _span(span, where):
    _closed_keys(span, ("id", "disposition", "source_ref", "match_tier", "evidence",
                         "near_match"), where)
    _text(span.get("id"), f"{where}.id", 200)
    _require(span.get("disposition") in DISPOSITIONS, f"{where}.disposition",
              f"expected one of {'|'.join(DISPOSITIONS)}")
    _source_ref(span.get("source_ref"), f"{where}.source_ref")
    if "match_tier" in span:
        _require(span["match_tier"] in MATCH_TIERS, f"{where}.match_tier",
                  f"expected one of {'|'.join(MATCH_TIERS)}")
    if "evidence" in span:
        _text(span["evidence"], f"{where}.evidence", 500)
    _near_match(span.get("near_match"), f"{where}.near_match")
    return span


def _track(track, where):
    # `id` (requirement 1): a stable, author-assigned identity distinct from the
    # human-readable `name`, so two tracks may deliberately share a display name
    # (e.g. two "Guitar" parts) without ever being confused with each other.
    # `load_analysis_manifest` assigns a deterministic `track-<index>` fallback
    # id to any track that omits it, and always cross-checks/injects the final
    # effective id back onto this dict; `name` is never used as an identity key.
    _closed_keys(track, ("id", "name", "role", "role_confidence", "spans"), where)
    if "id" in track:
        _text(track["id"], f"{where}.id", 200)
    _text(track.get("name"), f"{where}.name", 100)
    if "role" in track:
        _text(track["role"], f"{where}.role", 100)
    if "role_confidence" in track:
        _require(track["role_confidence"] in CONFIDENCES, f"{where}.role_confidence",
                  f"expected one of {'|'.join(CONFIDENCES)}")
    spans = track.get("spans")
    _require(isinstance(spans, list) and spans, f"{where}.spans", "expected nonempty array")
    seen = set()
    for i, span in enumerate(spans):
        _span(span, f"{where}.spans[{i}]")
        _require(span["id"] not in seen, f"{where}.spans[{i}]", "duplicate span id")
        seen.add(span["id"])
    return track


def _decision(decision, where):
    _closed_keys(decision, ("id", "question", "options", "depends_on", "applies_when",
                             "coordinates"), where)
    _text(decision.get("id"), f"{where}.id", 200)
    _text(decision.get("question"), f"{where}.question", 500)
    options = decision.get("options")
    _require(isinstance(options, list) and 2 <= len(options) <= 8, f"{where}.options",
              "expected 2-8 choices")
    for i, option in enumerate(options):
        _text(option, f"{where}.options[{i}]", 200)
    has_depends = "depends_on" in decision
    has_applies = "applies_when" in decision
    _require(has_depends == has_applies, where,
              "depends_on and applies_when must both be present or both absent")
    if has_depends:
        _text(decision["depends_on"], f"{where}.depends_on", 200)
        _text(decision["applies_when"], f"{where}.applies_when", 200)
    if "coordinates" in decision:
        coords = decision["coordinates"]
        _require(isinstance(coords, list), f"{where}.coordinates", "expected array")
        for i, c in enumerate(coords):
            _text(c, f"{where}.coordinates[{i}]", 300)
    return decision


def _variant(variant, where, known_span_ids):
    _closed_keys(variant, ("id", "span_ids", "is_canonical", "occurrence_index"), where)
    _text(variant.get("id"), f"{where}.id", 200)
    span_ids = variant.get("span_ids")
    _require(isinstance(span_ids, list) and span_ids, f"{where}.span_ids", "expected nonempty array")
    _require(len(set(span_ids)) == len(span_ids), f"{where}.span_ids", "duplicate span id")
    for i, sid in enumerate(span_ids):
        _text(sid, f"{where}.span_ids[{i}]", 200)
        _require(sid in known_span_ids, f"{where}.span_ids[{i}]", "span id not found on pattern's track")
    if "is_canonical" in variant:
        _require(isinstance(variant["is_canonical"], bool), f"{where}.is_canonical", "expected boolean")
    if "occurrence_index" in variant:
        _require(isinstance(variant["occurrence_index"], int) and
                  not isinstance(variant["occurrence_index"], bool) and variant["occurrence_index"] >= 1,
                  f"{where}.occurrence_index", "expected positive integer")
    return variant


def _pattern(pattern, where, track_span_ids, track_ids_by_name):
    """`track_span_ids` maps each track's *stable id* to its known span-id set
    (never keyed by display name: requirement 1). `track_ids_by_name` maps
    each display name to the list of track ids sharing it, so a legacy
    `track` (name-only) pattern can still be resolved deterministically
    whenever that name is unambiguous. The pattern's resolved track id is
    injected onto `pattern["_track_id"]` (stripped from any published output,
    matching this module's existing leading-underscore scratch-field
    convention) for every downstream consumer (`compile_performance.py`) to
    use instead of ever re-deriving identity from `track`/`name`.
    """
    # Idempotent: a pattern already carrying a resolved `_track_id` (e.g. from
    # a prior `load_analysis_manifest` call on this same dict, as
    # `compile_performance_arrangement` does defensively so it never silently
    # trusts an unvalidated manifest) is re-derived from scratch rather than
    # rejected as an unsupported field.
    for key in [k for k in pattern if k.startswith("_")]:
        del pattern[key]
    _closed_keys(pattern, ("id", "name", "track", "track_id", "span_ids", "bars", "disposition",
                            "confidence", "variants", "retain_role", "selection_reason",
                            "decision_ids"), where)
    _text(pattern.get("id"), f"{where}.id", 200)
    _text(pattern.get("name"), f"{where}.name", 200)
    track_name = pattern.get("track")
    _text(track_name, f"{where}.track", 100)
    explicit_track_id = pattern.get("track_id")
    if explicit_track_id is not None:
        # New v2 authoring (requirement 1): always unambiguous.
        _text(explicit_track_id, f"{where}.track_id", 200)
        _require(explicit_track_id in track_span_ids, f"{where}.track_id", "unknown track id")
        resolved_track_id = explicit_track_id
    else:
        # Legacy v2 authoring without track_id: deterministic only when this
        # track name is not shared by more than one track; otherwise this is
        # an unresolvable ambiguity that must never be silently guessed.
        candidates = track_ids_by_name.get(track_name, [])
        if len(candidates) > 1:
            raise Invalid(f"{where}.track: track name {track_name!r} is shared by "
                          f"{len(candidates)} tracks; specify track_id explicitly "
                          "(this pattern's source track identity cannot be inferred "
                          "from name alone)")
        _require(len(candidates) == 1, f"{where}.track", "unknown track name")
        resolved_track_id = candidates[0]
    pattern["_track_id"] = resolved_track_id
    span_ids = pattern.get("span_ids")
    _require(isinstance(span_ids, list) and span_ids, f"{where}.span_ids", "expected nonempty array")
    _require(len(set(span_ids)) == len(span_ids), f"{where}.span_ids", "duplicate span id")
    known = track_span_ids[resolved_track_id]
    for i, sid in enumerate(span_ids):
        _text(sid, f"{where}.span_ids[{i}]", 200)
        _require(sid in known, f"{where}.span_ids[{i}]",
                  f"span id not found on track {track_name}")
    bars = pattern.get("bars")
    _require(isinstance(bars, int) and bars >= 1, f"{where}.bars", "expected positive integer")
    _require(pattern.get("disposition") in DISPOSITIONS, f"{where}.disposition",
              f"expected one of {'|'.join(DISPOSITIONS)}")
    if "retain_role" in pattern:
        _require(pattern.get("disposition") == "OPTIONAL", f"{where}.retain_role",
                  "retain_role is only meaningful on an OPTIONAL-disposition pattern")
        _require(pattern["retain_role"] in PHYSICAL_ROLES, f"{where}.retain_role",
                  f"expected one of {'|'.join(PHYSICAL_ROLES)}")
    if "confidence" in pattern:
        _require(pattern["confidence"] in CONFIDENCES, f"{where}.confidence",
                  f"expected one of {'|'.join(CONFIDENCES)}")
    if "selection_reason" in pattern:
        _text(pattern["selection_reason"], f"{where}.selection_reason", 500)
    if "decision_ids" in pattern:
        decision_ids = pattern["decision_ids"]
        _require(isinstance(decision_ids, list) and decision_ids,
                 f"{where}.decision_ids", "expected nonempty array")
        _require(len(set(decision_ids)) == len(decision_ids),
                 f"{where}.decision_ids", "duplicate decision id")
        for i, decision_id in enumerate(decision_ids):
            _text(decision_id, f"{where}.decision_ids[{i}]", 200)
    if "variants" in pattern:
        variants = pattern["variants"]
        _require(isinstance(variants, list) and variants, f"{where}.variants", "expected nonempty array")
        ids = set()
        canonical_count = 0
        for i, variant in enumerate(variants):
            _variant(variant, f"{where}.variants[{i}]", known)
            _require(variant["id"] not in ids, f"{where}.variants[{i}].id", "duplicate variant id")
            ids.add(variant["id"])
            if variant.get("is_canonical"):
                canonical_count += 1
        _require(canonical_count <= 1, f"{where}.variants", "at most one variant may be is_canonical")
    return pattern


def _section(section, where, pattern_ids):
    _closed_keys(section, ("id", "name", "bars", "active_pattern_ids", "empty_policy"), where)
    _text(section.get("id"), f"{where}.id", 200)
    _text(section.get("name"), f"{where}.name", 200)
    bars = section.get("bars")
    _require(isinstance(bars, int) and bars >= 1, f"{where}.bars", "expected positive integer")
    active = section.get("active_pattern_ids")
    _require(isinstance(active, list), f"{where}.active_pattern_ids", "expected array")
    _require(len(set(active)) == len(active), where, "duplicate active pattern id")
    for i, pid in enumerate(active):
        _require(pid in pattern_ids, f"{where}.active_pattern_ids[{i}]", "unknown pattern id")
    empty_policy = section.get("empty_policy")
    _require(empty_policy is None or empty_policy in ("rest", "live"),
              f"{where}.empty_policy", "expected rest|live")
    _require(bool(active) or empty_policy, where,
              "empty active_pattern_ids requires empty_policy=rest or live")
    _require(not active or empty_policy is None, where,
              "empty_policy applies only when no patterns are active")
    return section


def load_analysis_manifest(raw):
    """Validate a parsed (already `json.load`ed) v2 analysis manifest.

    Returns the same dict, with ids cross-checked for referential integrity,
    or raises `Invalid`. Mirrors `write_arrangement.mjs`'s `parseManifestV2`
    plus this slice's `patterns`/`sections`/`source.capabilities` additions.
    """
    _closed_keys(raw, ("version", "source", "tracks", "decisions", "patterns",
                        "sections", "tempo_bpm", "meter", "performed_written_bars"),
                 "manifest")
    _require(raw.get("version") == 2, "manifest.version", "expected 2")
    source = raw.get("source")
    _closed_keys(source, ("asset_relative_path", "asset_sha256", "format", "capabilities"), "source")
    _relative_path(source.get("asset_relative_path"), "source.asset_relative_path")
    _sha256(source.get("asset_sha256"), "source.asset_sha256")
    _require(source.get("format") in SOURCE_FORMATS, "source.format",
              f"expected one of {'|'.join(SOURCE_FORMATS)}")
    if "capabilities" in source:
        caps = source["capabilities"]
        _require(isinstance(caps, list), "source.capabilities", "expected array")
        for i, c in enumerate(caps):
            _require(c in CAPABILITIES, f"source.capabilities[{i}]",
                      f"expected one of {'|'.join(CAPABILITIES)}")
        _require(len(set(caps)) == len(caps), "source.capabilities", "duplicate entry")

    tracks = raw.get("tracks")
    _require(isinstance(tracks, list) and tracks, "tracks", "expected nonempty array")
    # Stable track identity (requirement 1): display `name` duplicates are always
    # supported and never rejected. Every track gets an effective `id` -- its own
    # declared `id` if present, otherwise a deterministic, index-stable
    # `track-<i>` fallback -- and that effective id (never `name`) is the only
    # thing identity/grouping is ever keyed by downstream. The resolved id is
    # cross-checked for uniqueness and written back onto each track dict so
    # every consumer (including the embedded copy in a compiled
    # performance-arrangement.json) always has one, whether or not the author
    # declared it explicitly.
    effective_ids = []
    explicit_ids_seen = set()
    for i, track in enumerate(tracks):
        _track(track, f"tracks[{i}]")
        if "id" in track:
            _require(track["id"] not in explicit_ids_seen, f"tracks[{i}].id", "duplicate track id")
            explicit_ids_seen.add(track["id"])
            effective_ids.append(track["id"])
        else:
            effective_ids.append(None)  # resolved to a track-<i> fallback below
    for i, track in enumerate(tracks):
        if effective_ids[i] is None:
            effective_ids[i] = f"track-{i}"
    _require(len(set(effective_ids)) == len(effective_ids), "tracks",
              "duplicate track id (an explicit id collides with another track's "
              "explicit or auto-assigned id)")
    for track, track_id in zip(tracks, effective_ids):
        track["id"] = track_id
    track_span_ids = {t["id"]: {s["id"] for s in t["spans"]} for t in tracks}
    track_ids_by_name = {}
    for t in tracks:
        track_ids_by_name.setdefault(t["name"], []).append(t["id"])

    decisions = raw.get("decisions", []) or []
    _require(isinstance(decisions, list), "decisions", "expected array")
    decision_ids = set()
    for i, decision in enumerate(decisions):
        _decision(decision, f"decisions[{i}]")
        _require(decision["id"] not in decision_ids, f"decisions[{i}].id", "duplicate decision id")
        decision_ids.add(decision["id"])
    by_id = {d["id"]: d for d in decisions}
    for decision in decisions:
        if "depends_on" not in decision:
            continue
        _require(decision["depends_on"] in by_id, f"decisions[{decision['id']}].depends_on",
                  "references unknown decision")
        parent = by_id[decision["depends_on"]]
        _require(decision["applies_when"] in parent["options"],
                  f"decisions[{decision['id']}].applies_when", "not an option of the parent decision")

    patterns = raw.get("patterns", []) or []
    _require(isinstance(patterns, list), "patterns", "expected array")
    pattern_ids, pattern_names = set(), set()
    for i, pattern in enumerate(patterns):
        _pattern(pattern, f"patterns[{i}]", track_span_ids, track_ids_by_name)
        for j, decision_id in enumerate(pattern.get("decision_ids", [])):
            _require(decision_id in decision_ids, f"patterns[{i}].decision_ids[{j}]",
                     "references unknown decision")
        _require(pattern["id"] not in pattern_ids, f"patterns[{i}].id", "duplicate pattern id")
        pattern_ids.add(pattern["id"])
        _require(pattern["name"] not in pattern_names, f"patterns[{i}].name", "duplicate pattern name")
        pattern_names.add(pattern["name"])

    sections = raw.get("sections", []) or []
    _require(isinstance(sections, list), "sections", "expected array")
    _require(patterns or not sections, "sections", "requires patterns to reference when nonempty")
    section_ids = set()
    for i, section in enumerate(sections):
        _section(section, f"sections[{i}]", pattern_ids)
        _require(section["id"] not in section_ids, f"sections[{i}].id", "duplicate section id")
        section_ids.add(section["id"])
    if "performed_written_bars" in raw:
        performed = raw["performed_written_bars"]
        _require(isinstance(performed, list) and performed,
                 "performed_written_bars", "expected nonempty array")
        for i, bar_number in enumerate(performed):
            _require(isinstance(bar_number, int) and not isinstance(bar_number, bool) and
                     1 <= bar_number <= 100_000,
                     f"performed_written_bars[{i}]", "expected integer 1..100000")
        _require(len(performed) == sum(section["bars"] for section in sections),
                 "performed_written_bars",
                 "length must equal the total performed section bars")

    if "tempo_bpm" in raw:
        bpm = raw["tempo_bpm"]
        _require(isinstance(bpm, int) and 20 <= bpm <= 400, "tempo_bpm", "expected integer 20..400")
    if "meter" in raw:
        _require(isinstance(raw["meter"], str) and METER_RE.match(raw["meter"]) and
                  int(METER_RE.match(raw["meter"])[1]) <= 16, "meter", "expected n/d")

    unresolved = any(s["disposition"] == "UNRESOLVED" for t in tracks for s in t["spans"])
    if unresolved:
        _require(decisions, "decisions", "an UNRESOLVED span requires at least one decision")
    return raw


def public_manifest(manifest):
    """Strip internal, leading-underscore scratch fields (e.g. `_track_id`,
    injected onto each pattern by `load_analysis_manifest`/`_pattern` to carry
    resolved track identity through the pipeline) before persisting a manifest
    to disk. `load_analysis_manifest` validates and mutates its input in
    place for callers that keep operating on the returned dict (e.g.
    `compile_performance.py`); any caller that re-serializes that same dict
    back to a file must call this first so the internal scratch fields never
    leak into the on-disk JSON contract (mirrors the existing
    `resolved_patterns_public` convention in `compile_performance.py`).
    """
    return {
        **manifest,
        "patterns": [{k: v for k, v in p.items() if not k.startswith("_")}
                     for p in manifest.get("patterns", []) or []],
    }


def applicable_decisions(decisions, resolutions_by_id):
    """Decisions that require a resolution given already-known resolutions.

    A decision with `depends_on` only applies when its parent decision was
    resolved with the matching `applies_when` option; unconditional decisions
    always apply.
    """
    by_id = {d["id"]: d for d in decisions}
    applicable = []
    for decision in decisions:
        depends_on = decision.get("depends_on")
        if depends_on is None:
            applicable.append(decision)
            continue
        parent_resolution = resolutions_by_id.get(depends_on)
        if parent_resolution and parent_resolution["option"] == decision["applies_when"]:
            applicable.append(decision)
    return applicable


def validate_resolutions(manifest, resolutions):
    """Validate a flat `resolutions` list against `manifest["decisions"]`.

    Returns `(resolutions_by_id, errors)`. `errors` is empty only when every
    *applicable* decision (honoring `depends_on`/`applies_when` gating) has
    exactly one resolution whose `option` is one of that decision's
    `options`, and no resolution references an unknown or inapplicable
    decision.
    """
    decisions = manifest.get("decisions", []) or []
    by_id = {d["id"]: d for d in decisions}
    errors = []
    resolutions_by_id = {}
    for i, resolution in enumerate(resolutions):
        where = f"resolutions[{i}]"
        if not isinstance(resolution, dict):
            errors.append(f"{where}: expected object")
            continue
        decision_id = resolution.get("decision_id")
        option = resolution.get("option")
        if decision_id not in by_id:
            errors.append(f"{where}: unknown decision_id {decision_id!r}")
            continue
        if decision_id in resolutions_by_id:
            errors.append(f"{where}: duplicate resolution for decision {decision_id}")
            continue
        if option not in by_id[decision_id]["options"]:
            errors.append(f"{where}: option {option!r} is not valid for decision {decision_id}")
            continue
        resolutions_by_id[decision_id] = resolution

    applicable = applicable_decisions(decisions, resolutions_by_id)
    for decision in applicable:
        if decision["id"] not in resolutions_by_id:
            errors.append(f"decision {decision['id']} has no resolution: {decision['question']}")
    applicable_ids = {d["id"] for d in applicable}
    for decision_id in resolutions_by_id:
        if decision_id not in applicable_ids:
            errors.append(f"resolution for {decision_id} does not apply "
                           "(its depends_on condition was not met)")
    return resolutions_by_id, errors


# --- performance-plan foundation (finalize-contract) ------------------------
# The structures below are additive to the compiled `performance-arrangement.json`
# produced by `compile_performance.py`. They are validated independently of (and
# in addition to) the legacy fields already checked above/by `parsePerformanceV2`
# in `write_arrangement.mjs`: a document missing them is still a valid *legacy*
# v2 performance handoff (see `is_legacy_v2_performance`); `compile_performance.py`
# always emits the full plan going forward.

def is_legacy_v2_performance(raw):
    """True for a previously-compiled v2 performance handoff that predates the
    performance-plan foundation (has `disclosure`/`publication` but no
    `plan_version`). Such a file must be explicitly recompiled, never silently
    reinterpreted as the new layout.
    """
    return (isinstance(raw, dict) and raw.get("version") == 2 and
            "disclosure" in raw and "publication" in raw and "plan_version" not in raw)


LEGACY_V2_RECOMPILE_MESSAGE = (
    "this performance-arrangement.json predates the performance-plan foundation "
    "(no plan_version): rerun compile_performance.py to recompile it; the old "
    "layout is never silently reinterpreted as the new one"
)


def _fingerprints(value, where):
    _closed_keys(value, ("analysis_sha256", "resolutions_sha256"), where)
    _sha256(value.get("analysis_sha256"), f"{where}.analysis_sha256")
    _sha256(value.get("resolutions_sha256"), f"{where}.resolutions_sha256")
    return value


def _output_track(track, where, pattern_ids):
    _closed_keys(track, ("id", "role", "pattern_id"), where)
    _text(track.get("id"), f"{where}.id", 200)
    _require(track.get("role") in PHYSICAL_ROLES, f"{where}.role",
              f"expected one of {'|'.join(PHYSICAL_ROLES)}")
    if "pattern_id" in track:
        _require(track["role"] == "loop", f"{where}.pattern_id", "only a loop track names a pattern_id")
        _text(track["pattern_id"], f"{where}.pattern_id", 200)
        _require(track["pattern_id"] in pattern_ids, f"{where}.pattern_id", "unknown pattern id")
    else:
        _require(track["role"] != "loop", f"{where}.pattern_id", "a loop track requires pattern_id")
    return track


def _instrument(instrument, where, track_ids, track_ids_by_name, pattern_ids):
    _closed_keys(instrument, ("id", "analysis_track", "output_tracks"), where)
    _text(instrument.get("id"), f"{where}.id", 200)
    # `analysis_track` holds the stable track id (requirement 1), never the
    # display name, for any plan compiled by compile_performance.py. A
    # hand-authored/legacy plan that instead gives a track *name* is accepted
    # only when that name is unambiguous (exactly one track has it) -- the
    # same deterministic adapter `_pattern()` uses -- and is rejected with an
    # explicit diagnostic when the name is shared by more than one track.
    analysis_track = instrument.get("analysis_track")
    if analysis_track not in track_ids:
        candidates = track_ids_by_name.get(analysis_track, [])
        _require(len(candidates) != 0, f"{where}.analysis_track", "unknown analysis track")
        _require(len(candidates) == 1, f"{where}.analysis_track",
                  f"track name {analysis_track!r} is shared by {len(candidates)} tracks; "
                  "this plan must be recompiled with compile_performance.py so analysis_track "
                  "references a stable track id instead of an ambiguous name")
    tracks = instrument.get("output_tracks")
    _require(isinstance(tracks, list) and tracks, f"{where}.output_tracks", "expected nonempty array")
    ids = set()
    roles_seen = []
    for i, track in enumerate(tracks):
        _output_track(track, f"{where}.output_tracks[{i}]", pattern_ids)
        _require(track["id"] not in ids, f"{where}.output_tracks[{i}].id", "duplicate output track id")
        ids.add(track["id"])
        roles_seen.append(track["role"])
    _require(roles_seen.count("live") <= 1, f"{where}.output_tracks",
              "expected at most one live output track")
    _require(roles_seen.count("unresolved") <= 1, f"{where}.output_tracks",
              "expected at most one unresolved output track")
    ranks = {"live": 0, "loop": 1, "unresolved": 2}
    _require([ranks[role] for role in roles_seen] ==
             sorted(ranks[role] for role in roles_seen), f"{where}.output_tracks",
             "expected order: live (if used), loop pattern(s), unresolved (if used)")
    return instrument


def _section_instance(section_instance, where, section_ids, pattern_ids):
    _closed_keys(section_instance, ("id", "section_id", "name", "occurrence_index", "action",
                                     "approved", "omitted_layers"), where)
    _text(section_instance.get("id"), f"{where}.id", 200)
    _require(section_instance.get("section_id") in section_ids, f"{where}.section_id",
              "unknown section id")
    _text(section_instance.get("name"), f"{where}.name", 200)
    _require(isinstance(section_instance.get("occurrence_index"), int) and
              not isinstance(section_instance["occurrence_index"], bool) and
              section_instance["occurrence_index"] >= 1,
              f"{where}.occurrence_index", "expected positive integer")
    _require(section_instance.get("action") in SECTION_ACTIONS, f"{where}.action",
              f"expected one of {'|'.join(SECTION_ACTIONS)}")
    _require(isinstance(section_instance.get("approved"), bool), f"{where}.approved",
              "expected boolean")
    if section_instance["action"] == "delete":
        _require(section_instance["approved"], where,
                  "a delete action requires explicit scoped approval")
    omitted = section_instance.get("omitted_layers", [])
    _require(isinstance(omitted, list), f"{where}.omitted_layers", "expected array")
    _require(len(set(omitted)) == len(omitted), f"{where}.omitted_layers", "duplicate pattern id")
    for i, pid in enumerate(omitted):
        _require(pid in pattern_ids, f"{where}.omitted_layers[{i}]", "unknown pattern id")
    return section_instance


def _output_timeline_entry(entry, where, section_instance_ids):
    _closed_keys(entry, ("output_index", "section_instance_id", "source_section_bar"), where)
    _require(isinstance(entry.get("output_index"), int) and
              not isinstance(entry["output_index"], bool) and entry["output_index"] >= 0,
              f"{where}.output_index", "expected nonnegative integer")
    _require(entry.get("section_instance_id") in section_instance_ids,
              f"{where}.section_instance_id", "unknown section instance id")
    _require(isinstance(entry.get("source_section_bar"), int) and
              not isinstance(entry["source_section_bar"], bool) and
              entry["source_section_bar"] >= 0,
              f"{where}.source_section_bar", "expected nonnegative integer")
    return entry


def _removal_ledger_entry(entry, where, section_instance_ids):
    _closed_keys(entry, ("section_instance_id", "name", "removed_bars", "decision_ref"), where)
    _require(entry.get("section_instance_id") in section_instance_ids,
              f"{where}.section_instance_id", "unknown section instance id")
    _text(entry.get("name"), f"{where}.name", 200)
    _require(isinstance(entry.get("removed_bars"), int) and entry["removed_bars"] >= 1,
              f"{where}.removed_bars", "expected positive integer")
    _text(entry.get("decision_ref"), f"{where}.decision_ref", 200)
    return entry


def _placement(placement, where, output_track_ids, max_output_index, pattern_ids, live_span_ids):
    _closed_keys(placement, ("output_track_id", "output_bar_range", "material_id", "variant_id",
                              "source_ref", "mode", "optional", "tail_policy"), where)
    _require(placement.get("output_track_id") in output_track_ids, f"{where}.output_track_id",
              "unknown output track id")
    bar_range = placement.get("output_bar_range")
    _require(isinstance(bar_range, list) and len(bar_range) == 2, f"{where}.output_bar_range",
              "expected [start, end]")
    start, end = bar_range
    for n, label in ((start, "start"), (end, "end")):
        _require(isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= max_output_index,
                  f"{where}.output_bar_range.{label}", "out of range")
    _require(start <= end, f"{where}.output_bar_range", "start must not exceed end")
    _text(placement.get("material_id"), f"{where}.material_id", 200)
    if "variant_id" in placement:
        _text(placement["variant_id"], f"{where}.variant_id", 200)
    if "source_ref" in placement and placement["source_ref"] is not None:
        _source_ref(placement["source_ref"], f"{where}.source_ref")
    _require(placement.get("mode") in PHYSICAL_ROLES, f"{where}.mode",
              f"expected one of {'|'.join(PHYSICAL_ROLES)}")
    material_id = placement["material_id"]
    _require(material_id in pattern_ids or
             (placement["mode"] == "live" and material_id in live_span_ids),
             f"{where}.material_id",
             "expected a pattern id, or a LIVE span id for a live placement")
    _require(isinstance(placement.get("optional"), bool), f"{where}.optional", "expected boolean")
    if "tail_policy" in placement and placement["tail_policy"] is not None:
        _require(placement["tail_policy"] in TAIL_POLICIES, f"{where}.tail_policy",
                  f"expected one of {'|'.join(TAIL_POLICIES)}")
    return placement


def _variant_selection(selection, where, pattern_ids):
    _closed_keys(selection, ("pattern_id", "occurrence_index", "variant_id", "source_ref"), where)
    _require(selection.get("pattern_id") in pattern_ids, f"{where}.pattern_id", "unknown pattern id")
    _require(isinstance(selection.get("occurrence_index"), int) and
              not isinstance(selection["occurrence_index"], bool) and
              selection["occurrence_index"] >= 1,
              f"{where}.occurrence_index", "expected positive integer")
    _text(selection.get("variant_id"), f"{where}.variant_id", 200)
    _source_ref(selection.get("source_ref"), f"{where}.source_ref")
    return selection


def validate_performance_plan(raw):
    """Validate the performance-plan-only additive fields of a compiled
    `performance-arrangement.json`: `plan_version`, `fingerprints`,
    `instruments`, `section_instances`, `output_timeline`, `removal_ledger`,
    `placements`, `variant_selections` and top-level `tail_policy`.

    Does not re-validate the legacy fields (`source`/`tracks`/`decisions`/
    `patterns`/`sections`/`publication`/`disclosure`); callers validate those
    separately (`load_analysis_manifest`-shaped checks already cover `tracks`/
    `patterns`/`sections`, and `compile_performance.py` owns `publication`).
    Raises `Invalid` naming the offending path; never touches the filesystem.
    """
    _require(raw.get("plan_version") == PLAN_VERSION, "plan_version",
              f"expected {PLAN_VERSION}")
    _fingerprints(raw.get("fingerprints"), "fingerprints")
    if "export_layout" in raw:
        _export_layout(raw["export_layout"], "export_layout")

    # Mirrors load_analysis_manifest's effective-id computation so a hand-authored
    # or legacy plan whose tracks omit "id" entirely still gets a deterministic,
    # index-stable id to resolve `instrument.analysis_track` name fallbacks against.
    raw_tracks = raw.get("tracks", [])
    effective_track_ids = [t.get("id") or f"track-{i}" for i, t in enumerate(raw_tracks)]
    track_ids = set(effective_track_ids)
    track_ids_by_name = {}
    for t, tid in zip(raw_tracks, effective_track_ids):
        track_ids_by_name.setdefault(t.get("name"), []).append(tid)
    pattern_ids = {p["id"] for p in raw.get("patterns", [])}
    live_span_ids = {
        span["id"] for track in raw_tracks for span in track.get("spans", [])
        if span.get("disposition") == "LIVE"
    }
    section_ids = {s["id"] for s in raw.get("sections", [])}

    instruments = raw.get("instruments")
    _require(isinstance(instruments, list), "instruments", "expected array")
    instrument_ids = set()
    output_track_ids = set()
    for i, instrument in enumerate(instruments):
        _instrument(instrument, f"instruments[{i}]", track_ids, track_ids_by_name, pattern_ids)
        _require(instrument["id"] not in instrument_ids, f"instruments[{i}].id", "duplicate instrument id")
        instrument_ids.add(instrument["id"])
        for track in instrument["output_tracks"]:
            _require(track["id"] not in output_track_ids, f"instruments[{i}]", "duplicate output track id")
            output_track_ids.add(track["id"])

    section_instances = raw.get("section_instances")
    _require(isinstance(section_instances, list), "section_instances", "expected array")
    section_instance_ids = set()
    for i, si in enumerate(section_instances):
        _section_instance(si, f"section_instances[{i}]", section_ids, pattern_ids)
        _require(si["id"] not in section_instance_ids, f"section_instances[{i}].id", "duplicate id")
        section_instance_ids.add(si["id"])

    output_timeline = raw.get("output_timeline")
    _require(isinstance(output_timeline, list), "output_timeline", "expected array")
    for i, entry in enumerate(output_timeline):
        _output_timeline_entry(entry, f"output_timeline[{i}]", section_instance_ids)
    indices = [e["output_index"] for e in output_timeline]
    _require(indices == list(range(len(indices))), "output_timeline",
              "output_index must be a contiguous 0-based sequence")

    removal_ledger = raw.get("removal_ledger", []) or []
    _require(isinstance(removal_ledger, list), "removal_ledger", "expected array")
    for i, entry in enumerate(removal_ledger):
        _removal_ledger_entry(entry, f"removal_ledger[{i}]", section_instance_ids)

    max_output_index = max(indices) if indices else -1
    placements = raw.get("placements", []) or []
    _require(isinstance(placements, list), "placements", "expected array")
    for i, placement in enumerate(placements):
        _placement(placement, f"placements[{i}]", output_track_ids, max_output_index,
                   pattern_ids, live_span_ids)

    variant_selections = raw.get("variant_selections", []) or []
    _require(isinstance(variant_selections, list), "variant_selections", "expected array")
    for i, selection in enumerate(variant_selections):
        _variant_selection(selection, f"variant_selections[{i}]", pattern_ids)

    if "tail_policy" in raw and raw["tail_policy"] is not None:
        _require(raw["tail_policy"] in TAIL_POLICIES, "tail_policy",
                  f"expected one of {'|'.join(TAIL_POLICIES)}")
    return raw


# --- whole-document loader for a compiled performance-arrangement.json -----------------
# `load_analysis_manifest` and `validate_performance_plan` each validate one slice of the
# compiled performance handoff (the legacy analysis-shaped fields, and the additive
# finalize-contract fields, respectively). Neither alone accepts the full, real on-disk
# document `compile_performance.py` writes (its top level also carries `resolutions`,
# `publication`, `performance`, `disclosure`, on top of `plan_version`/the plan fields).
# `load_performance_plan` is the single entry point a *consumer* of a finished
# performance-arrangement.json (the published-worksheet renderer, the published-worksheet
# checker, a GP build/parity step) should call: it closes the top-level key set, validates
# the legacy subset without mutating the caller's dict, and validates `publication`/
# `disclosure` plus every additive plan field, raising `Invalid` naming the offending path.
PERFORMANCE_PLAN_TOP_KEYS = (
    "version", "plan_version", "source", "tracks", "decisions", "resolutions", "patterns",
    "sections", "tempo_bpm", "meter", "publication", "performance", "disclosure",
    "fingerprints", "instruments", "section_instances", "output_timeline", "removal_ledger",
    "placements", "variant_selections", "tail_policy", "export_layout",
    "performed_written_bars",
)


def _export_layout(layout, where):
    _closed_keys(layout, ("version", "prune_zero_note_tracks", "group_order", "colors"), where)
    _require(layout.get("version") == 1, f"{where}.version", "expected 1")
    _require(layout.get("prune_zero_note_tracks") is True,
             f"{where}.prune_zero_note_tracks", "expected true")
    _require(layout.get("group_order") == ["core", "optional", "unresolved"],
             f"{where}.group_order", "expected core, optional, unresolved")
    colors = layout.get("colors")
    _closed_keys(colors, ("drums", "bass", "other"), f"{where}.colors")
    _require(colors == {"drums": "#0000FF", "bass": "#FFFF00", "other": "#FF0000"},
             f"{where}.colors", "expected the canonical drums/bass/other palette")
    return layout


def _plan_publication(pub, where, pattern_ids):
    _closed_keys(pub, ("status", "limitations", "consent"), where)
    _require(pub.get("status") in PUBLICATION_STATUSES, f"{where}.status",
              f"expected one of {'|'.join(PUBLICATION_STATUSES)}")
    limitations = pub.get("limitations", [])
    _require(isinstance(limitations, list) and all(isinstance(x, str) and x for x in limitations),
              f"{where}.limitations", "expected array of nonempty strings")
    if pub["status"] == "partial":
        _require(bool(limitations), f"{where}.limitations", "partial publication requires "
                  "nonempty limitations")
        _require(isinstance(pub.get("consent"), str) and pub["consent"].strip(),
                  f"{where}.consent", "partial publication requires a nonempty consent")
    elif pub["status"] == "final":
        _require(not limitations, f"{where}.limitations", "final publication cannot have "
                  "limitations")
        _require(pub.get("consent") is None, f"{where}.consent", "final publication does not "
                  "accept a consent field")
    else:
        _require(pub.get("consent") is None, f"{where}.consent", "draft preview does not "
                  "accept a consent field")
    return pub


def _plan_performance(performance, where, section_ids):
    _closed_keys(performance, ("players", "roles", "transitions", "section_transitions"), where)
    if "players" in performance:
        _require(isinstance(performance["players"], int) and
                 not isinstance(performance["players"], bool) and
                 1 <= performance["players"] <= 32,
                 f"{where}.players", "expected integer 1..32")
    for field in ("roles", "transitions"):
        values = performance.get(field, [])
        _require(isinstance(values, list) and
                 all(isinstance(item, str) and item for item in values),
                 f"{where}.{field}", "expected array of nonempty strings")
    entries = performance.get("section_transitions", [])
    _require(isinstance(entries, list), f"{where}.section_transitions", "expected array")
    seen = set()
    for i, entry in enumerate(entries):
        at = f"{where}.section_transitions[{i}]"
        _closed_keys(entry, ("section_id", "action", "feasible", "warning"), at)
        _require(entry.get("section_id") in section_ids, f"{at}.section_id",
                 "unknown section id")
        _require(entry["section_id"] not in seen, f"{at}.section_id", "duplicate section id")
        seen.add(entry["section_id"])
        _text(entry.get("action"), f"{at}.action", 500)
        _require(isinstance(entry.get("feasible"), bool), f"{at}.feasible", "expected boolean")
        if not entry["feasible"]:
            _text(entry.get("warning"), f"{at}.warning", 500)
        elif "warning" in entry:
            _text(entry["warning"], f"{at}.warning", 500)
    return performance


def _plan_disclosure(disclosure, where, pattern_ids, span_ids):
    _closed_keys(disclosure, ("live_patterns", "live_spans_without_selected_pattern",
                              "omitted_patterns", "rest_spans",
                              "optional_spans_without_selected_pattern", "unresolved_patterns"),
                 where)
    for field in ("live_patterns", "omitted_patterns", "unresolved_patterns"):
        value = disclosure.get(field, [])
        _require(isinstance(value, list), f"{where}.{field}", "expected array")
        for i, pid in enumerate(value):
            _require(pid in pattern_ids, f"{where}.{field}[{i}]", "unknown pattern id")
    for field in ("live_spans_without_selected_pattern", "rest_spans",
                  "optional_spans_without_selected_pattern"):
        value = disclosure.get(field, [])
        _require(isinstance(value, list), f"{where}.{field}", "expected array")
        for i, sid in enumerate(value):
            _require(sid in span_ids, f"{where}.{field}[{i}]", "unknown span id")
    if disclosure.get("status") == "final":
        _require(not disclosure.get("unresolved_patterns"), f"{where}.unresolved_patterns",
                  "a final publication cannot disclose an unresolved pattern")
    return disclosure


def load_performance_plan(raw):
    """Validate a parsed compiled `performance-arrangement.json` (plan_version 2) in
    full: the legacy analysis-shaped fields (via a private deep copy passed to
    `load_analysis_manifest`, so this never mutates the caller's `raw`), `publication`/
    `disclosure`, and every additive finalize-contract field (via
    `validate_performance_plan`). Raises `Invalid` naming the offending path;
    `is_legacy_v2_performance` should be checked by the caller first when a pre-plan
    document must be rejected with an explicit recompile message instead of the
    generic "missing plan_version" error this function raises.
    """
    _require(isinstance(raw, dict), "plan", "expected object")
    _closed_keys(raw, PERFORMANCE_PLAN_TOP_KEYS, "plan")
    _require(raw.get("version") == 2, "plan.version", "expected 2")
    _require(raw.get("plan_version") == PLAN_VERSION, "plan.plan_version",
              f"expected {PLAN_VERSION}")
    if "export_layout" in raw:
        _export_layout(raw["export_layout"], "plan.export_layout")

    legacy_subset = {"version": 2}
    for key in ("source", "tracks", "decisions", "sections", "tempo_bpm", "meter",
                "performed_written_bars"):
        if key in raw:
            legacy_subset[key] = raw[key]
    # `compile_performance.py` additively resolves each pattern with `final_disposition`/
    # `omitted` (see `resolved_patterns_public`); `load_analysis_manifest`'s pattern
    # validator only knows the analysis-time pattern shape, so those two resolved fields
    # are validated here instead and stripped before delegating.
    resolved_patterns = raw.get("patterns", []) or []
    legacy_subset["patterns"] = []
    for i, pattern in enumerate(resolved_patterns):
        where = f"patterns[{i}]"
        _require(pattern.get("final_disposition") in DISPOSITIONS, f"{where}.final_disposition",
                  f"expected one of {'|'.join(DISPOSITIONS)}")
        _require(isinstance(pattern.get("omitted"), bool), f"{where}.omitted", "expected boolean")
        legacy_subset["patterns"].append(
            {k: v for k, v in pattern.items() if k not in ("final_disposition", "omitted")})
    load_analysis_manifest(copy.deepcopy(legacy_subset))

    pattern_ids = {p["id"] for p in resolved_patterns}
    span_ids = {s["id"] for t in raw.get("tracks", []) or [] for s in t["spans"]}

    _require(isinstance(raw.get("publication"), dict), "plan.publication", "expected object")
    _plan_publication(raw["publication"], "plan.publication", pattern_ids)
    _require(isinstance(raw.get("performance"), dict), "plan.performance", "expected object")
    _plan_performance(raw["performance"], "plan.performance",
                      {section["id"] for section in raw.get("sections", [])})
    _require(isinstance(raw.get("disclosure"), dict), "plan.disclosure", "expected object")
    _plan_disclosure(raw["disclosure"], "plan.disclosure", pattern_ids, span_ids)

    validate_performance_plan(raw)
    return raw
