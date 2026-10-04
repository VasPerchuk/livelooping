"""Compile a version 2 analysis manifest plus structured resolutions and
performance/publication data into `performance-arrangement.json`.

This is the slice's compilation path for requirement 3 in the v2 workflow
integration: it does not itself copy or regenerate source-bar note material
(that is `tools\\source_compiler.mjs`, invoked separately and only when the
resulting handoff authorizes `source_bar_reuse`); this script compiles the
neutral analysis facts plus resolved decisions and confirmed generic
performance capabilities into one performance handoff, validating every
decision is resolved and enforcing five-disposition semantics. See
`analyze\\references\\arrangement-handoff.md` ("Source-bar reuse (vertical
slice)" for the note-material compiler this handoff feeds).

Inputs (three separate JSON documents, matching the contract's separation of
analysis from performance/publication validation):
  * `--manifest`  the v2 analysis handoff (`arrangement.json`)
  * `--resolutions` a flat list: `[{"decision_id": ..., "option": ..., "note": ...}]`
  * `--performance` publication + generic performance capabilities:
    `{"status": "final"|"partial", "limitations": [...], "consent": "...",
      "performance": {"players": N, "roles": [...], "transitions": [...]}}`

Usage:
  python compile_performance.py --manifest <arrangement.json> \
      --resolutions <resolutions.json> --performance <performance.json> \
      --output <performance-arrangement.json>

Safety: never overwrites an existing performance handoff unless `--replace`
is given; writes are staged to a temp file in the same directory and renamed
in atomically, exactly like `render_views.py`.
"""

import argparse
import copy
import json
import re
from pathlib import Path
import sys
import tempfile

from v2_schema import (
    Invalid, DISPOSITIONS, PLAN_VERSION, PUBLICATION_STATUSES, TAIL_POLICIES,
    canonical_sha256, is_legacy_v2_performance, LEGACY_V2_RECOMPILE_MESSAGE,
    load_analysis_manifest, validate_resolutions, validate_performance_plan,
)

MAX_BYTES = 1_000_000
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(text):
    """Deterministic, stable id fragment for a human display name."""
    cleaned = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return cleaned or "x"


def _write_atomic(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with open(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        Path(tmp_name).replace(path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _validate_performance(performance):
    if not isinstance(performance, dict):
        raise Invalid("performance: expected object")
    extra = set(performance) - {"status", "limitations", "consent", "performance", "tail_policy"}
    if extra:
        raise Invalid(f"performance: unsupported field(s): {', '.join(sorted(extra))}")
    status = performance.get("status")
    if status not in PUBLICATION_STATUSES:
        raise Invalid(f"performance.status: expected one of {'|'.join(PUBLICATION_STATUSES)}")
    limitations = performance.get("limitations", [])
    if not isinstance(limitations, list) or not all(isinstance(x, str) and x for x in limitations):
        raise Invalid("performance.limitations: expected array of nonempty strings")
    consent = performance.get("consent")
    if status == "partial":
        if not limitations:
            raise Invalid("partial publication requires nonempty limitations")
        if not isinstance(consent, str) or not consent.strip():
            raise Invalid("partial publication requires a nonempty consent description")
    elif status == "final":
        if limitations:
            raise Invalid("final publication cannot have limitations")
        if consent not in (None,):
            raise Invalid("final publication does not accept a consent field")
    else:  # draft: an internal, non-publishable preview; labeled UNRESOLVED material is allowed
        if consent not in (None,):
            raise Invalid("draft preview does not accept a consent field")
    tail_policy = performance.get("tail_policy")
    if tail_policy is not None and tail_policy not in TAIL_POLICIES:
        raise Invalid(f"performance.tail_policy: expected one of {'|'.join(TAIL_POLICIES)}")
    perf = performance.get("performance")
    if perf is not None:
        if not isinstance(perf, dict):
            raise Invalid("performance.performance: expected object")
        extra2 = set(perf) - {"players", "roles", "transitions", "section_transitions"}
        if extra2:
            raise Invalid(f"performance.performance: unsupported field(s): {', '.join(sorted(extra2))}")
        if "players" in perf and not (isinstance(perf["players"], int) and 1 <= perf["players"] <= 32):
            raise Invalid("performance.performance.players: expected integer 1..32")
        for field in ("roles", "transitions"):
            if field in perf and not (isinstance(perf[field], list) and
                                      all(isinstance(x, str) and x for x in perf[field])):
                raise Invalid(f"performance.performance.{field}: expected array of nonempty strings")
        section_transitions = perf.get("section_transitions", [])
        if not isinstance(section_transitions, list):
            raise Invalid("performance.performance.section_transitions: expected array")
        seen_sections = set()
        for index, transition in enumerate(section_transitions):
            where = f"performance.performance.section_transitions[{index}]"
            if not isinstance(transition, dict):
                raise Invalid(f"{where}: expected object")
            extra3 = set(transition) - {"section_id", "action", "feasible", "warning"}
            if extra3:
                raise Invalid(f"{where}: unsupported field(s): {', '.join(sorted(extra3))}")
            section_id = transition.get("section_id")
            action = transition.get("action")
            feasible = transition.get("feasible")
            if not isinstance(section_id, str) or not section_id.strip():
                raise Invalid(f"{where}.section_id: expected nonempty string")
            if section_id in seen_sections:
                raise Invalid(f"{where}.section_id: duplicate section id")
            seen_sections.add(section_id)
            if not isinstance(action, str) or not action.strip():
                raise Invalid(f"{where}.action: expected nonempty string")
            if not isinstance(feasible, bool):
                raise Invalid(f"{where}.feasible: expected boolean")
            warning = transition.get("warning")
            if not feasible and (not isinstance(warning, str) or not warning.strip()):
                raise Invalid(f"{where}.warning: infeasible action requires a warning")
            if feasible and warning is not None and (
                    not isinstance(warning, str) or not warning.strip()):
                raise Invalid(f"{where}.warning: expected nonempty string when present")
    return {
        "status": status,
        "limitations": list(limitations),
        "consent": consent,
        "performance": perf or {},
        "tail_policy": tail_policy,
    }


def resolve_dispositions(manifest, resolutions_by_id):
    """Determine the final disposition for every selected pattern.

    A pattern whose raw disposition is `UNRESOLVED` must be resolved by its
    matching decision(s) (matched by the `decision-<span-id>` id convention
    used by `pattern_candidates.py` and the renderer); the first decision's
    `option` is mapped onto a final disposition using a small, explicit
    vocabulary so the compiler never silently guesses:

      * `use_canonical` / `normalize_canonical` / `accept_long_loop` / `keep_loop` -> LOOP
      * `preserve_variants` -> LOOP (a concrete source-backed variant is selected
        per occurrence by `resolve_variant_selections`, not just a label change)
      * `play_live` / `keep_live`                                   -> LIVE
      * `omit`                                                      -> OPTIONAL (explicitly omitted)
      * `defer_unresolved`                                          -> UNRESOLVED (only ever
        legal to *survive* into a non-final publication; Final still blocks it)

    A pattern whose raw disposition is `OPTIONAL` requires the *same* explicit
    retain-vs-omit decision (requirement 3: `retain_optional` is a distinct
    state from omit/delete, never guessed): `OPTIONAL` is a retention/deletion
    *state*, not a physical-role verdict, so retaining it still requires
    deciding which physical role (`LOOP`/`LIVE`/`UNRESOLVED`) the retained
    material plays on. That role comes from exactly one of, in order:

      1. a matching `decision-<span-id>` resolution, using the identical
         `option_to_disposition` vocabulary above (so `omit` keeps it
         `OPTIONAL`+omitted, while e.g. `play_live`/`keep_loop` retains it on
         that physical role); or
      2. the pattern's own analysis-declared `retain_role` (`loop`/`live`/
         `unresolved`), when the analysis already knows which role fits and no
         decision is needed.

    If neither is present the pattern is reported as an error rather than
    silently defaulted to omitted (the prior, now-fixed, behavior): retain vs.
    omit for `OPTIONAL` material must always be an explicit fact, never a
    guess. A retained `OPTIONAL` pattern's `final_disposition` becomes the
    resolved physical disposition (never literally stays `OPTIONAL`, which is
    reserved for the omitted case); `build_placements` separately marks its
    placement `optional=true` from the pattern's original raw disposition so
    the distinction is never lost even after the physical role is assigned.

    Returns `(resolved_patterns, errors)`. `resolved_patterns` is a list of
    dicts shaped like the manifest's `patterns[]` plus `"final_disposition"`
    and `"omitted"` (bool). Any pattern whose disposition cannot be resolved
    this way is reported as an error rather than silently left `UNRESOLVED`;
    a pattern may only remain `UNRESOLVED` by an *explicit* `defer_unresolved`
    resolution, never by omission of a resolution altogether.
    """
    option_to_disposition = {
        "use_canonical": "LOOP", "normalize_canonical": "LOOP",
        "accept_long_loop": "LOOP", "keep_loop": "LOOP",
        "preserve_variants": "LOOP",
        "play_live": "LIVE", "keep_live": "LIVE",
        "omit": "OPTIONAL",
        "defer_unresolved": "UNRESOLVED",
    }
    role_to_disposition = {"loop": "LOOP", "live": "LIVE", "unresolved": "UNRESOLVED"}
    decisions = manifest.get("decisions", []) or []
    decisions_by_id = {decision["id"]: decision for decision in decisions}
    children_by_parent = {}
    for decision in decisions:
        if "depends_on" in decision:
            children_by_parent.setdefault(decision["depends_on"], []).append(decision["id"])

    def bound_decision_ids(pattern):
        bound = list(pattern.get("decision_ids", []))
        if not bound:
            bound = [
                f"decision-{span_id}" for span_id in pattern["span_ids"]
                if f"decision-{span_id}" in decisions_by_id
            ]
        seen = set(bound)
        queue = list(bound)
        while queue:
            for child_id in children_by_parent.get(queue.pop(0), []):
                if child_id not in seen:
                    seen.add(child_id)
                    queue.append(child_id)
        return seen

    def active_resolutions(pattern):
        bound = bound_decision_ids(pattern)
        active = []
        selected = {}
        for decision in decisions:
            decision_id = decision["id"]
            if decision_id not in bound:
                continue
            parent_id = decision.get("depends_on")
            if parent_id is not None and selected.get(parent_id) != decision["applies_when"]:
                continue
            resolution = resolutions_by_id.get(decision_id)
            if resolution is not None:
                selected[decision_id] = resolution["option"]
                active.append(resolution)
        return active

    errors = []
    resolved = []
    for pattern in manifest.get("patterns", []) or []:
        disposition = pattern["disposition"]
        omitted = False
        option = None
        matching_resolutions = active_resolutions(pattern)
        if disposition == "UNRESOLVED":
            if not matching_resolutions:
                errors.append(f"pattern {pattern['id']}: UNRESOLVED with no matching resolution")
                continue
            option = matching_resolutions[0]["option"]
            if option not in option_to_disposition:
                errors.append(f"pattern {pattern['id']}: resolution option {option!r} does not "
                              "map to a final disposition")
                continue
            disposition = option_to_disposition[option]
        elif disposition == "OPTIONAL":
            # Unlike UNRESOLVED, OPTIONAL's safe default (no decision, no declared
            # retain_role) is to stay omitted -- "optional" always meant "may be
            # omitted without resolving anything" and existing analysis output
            # exercises that default. Retaining it on a specific physical role is
            # an *opt-in* that must come from an explicit decision or an explicit
            # analysis-declared `retain_role`; it is never guessed.
            if matching_resolutions:
                option = matching_resolutions[0]["option"]
                if option not in option_to_disposition:
                    errors.append(f"pattern {pattern['id']}: resolution option {option!r} does "
                                  "not map to a final disposition")
                    continue
                resolved_disposition = option_to_disposition[option]
                if resolved_disposition == "OPTIONAL":
                    omitted = True  # explicit `omit` resolution: genuinely omitted
                else:
                    disposition = resolved_disposition  # retained on this physical role
            elif pattern.get("retain_role"):
                # No decision needed: analysis already declared which physical role
                # this retained-optional material plays on.
                disposition = role_to_disposition[pattern["retain_role"]]
            else:
                omitted = True  # default: stays OPTIONAL and omitted, no error
        for followup in matching_resolutions[1:]:
            followup_option = followup["option"]
            if followup_option == "omit":
                omitted = True
            elif followup_option in option_to_disposition:
                followup_disposition = option_to_disposition[followup_option]
                if followup_disposition != "OPTIONAL":
                    disposition = followup_disposition
                    omitted = False
        resolved.append({**pattern, "final_disposition": disposition, "omitted": omitted,
                         "_resolution_option": option})
    return resolved, errors


def _normalize_resolutions_input(raw_resolutions):
    """Accept either the legacy flat decisions-only array, or the new
    structured object shape:
    `{"decisions": [...], "section_actions": [...], "layer_omissions": [...],
      "variant_selections": [...]}`. Returns the four lists; any list may be
    empty. Unknown keys in the object form are a hard error.
    """
    if isinstance(raw_resolutions, list):
        return raw_resolutions, [], [], []
    if isinstance(raw_resolutions, dict):
        extra = set(raw_resolutions) - {"decisions", "section_actions", "layer_omissions",
                                        "variant_selections"}
        if extra:
            raise Invalid(f"resolutions: unsupported field(s): {', '.join(sorted(extra))}")
        decisions = raw_resolutions.get("decisions", [])
        section_actions = raw_resolutions.get("section_actions", [])
        layer_omissions = raw_resolutions.get("layer_omissions", [])
        variant_selections = raw_resolutions.get("variant_selections", [])
        for name, value in (("decisions", decisions), ("section_actions", section_actions),
                            ("layer_omissions", layer_omissions),
                            ("variant_selections", variant_selections)):
            if not isinstance(value, list):
                raise Invalid(f"resolutions.{name}: expected array")
        return decisions, section_actions, layer_omissions, variant_selections
    raise Invalid("resolutions: expected a JSON array or object")


def build_section_instances(manifest, section_actions, layer_omissions):
    """One section instance per analysis section (each analysis section is
    already a distinct occurrence, never a reusable template), carrying its
    retain/delete action and any per-layer omissions. Repeated sections that
    share a display `name` (e.g. two "Chorus" entries) get distinct
    `occurrence_index` values so deleting one never affects the other.
    """
    errors = []
    sections = manifest.get("sections", []) or []
    section_ids = {s["id"] for s in sections}
    pattern_ids = {p["id"] for p in manifest.get("patterns", []) or []}

    actions_by_section = {}
    for i, entry in enumerate(section_actions):
        where = f"section_actions[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{where}: expected object")
            continue
        extra = set(entry) - {"section_id", "action", "approved", "reason"}
        if extra:
            errors.append(f"{where}: unsupported field(s): {', '.join(sorted(extra))}")
            continue
        section_id = entry.get("section_id")
        if section_id not in section_ids:
            errors.append(f"{where}: unknown section_id {section_id!r}")
            continue
        if section_id in actions_by_section:
            errors.append(f"{where}: duplicate section_action for {section_id}")
            continue
        action = entry.get("action")
        if action not in ("retain", "delete"):
            errors.append(f"{where}: action must be retain or delete")
            continue
        if action == "delete" and not entry.get("approved"):
            errors.append(f"{where}: deleting section {section_id} requires explicit "
                          "scoped approval (approved: true)")
            continue
        actions_by_section[section_id] = entry

    omissions_by_section = {}
    for i, entry in enumerate(layer_omissions):
        where = f"layer_omissions[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{where}: expected object")
            continue
        extra = set(entry) - {"section_id", "pattern_id"}
        if extra:
            errors.append(f"{where}: unsupported field(s): {', '.join(sorted(extra))}")
            continue
        section_id = entry.get("section_id")
        pattern_id = entry.get("pattern_id")
        if section_id not in section_ids:
            errors.append(f"{where}: unknown section_id {section_id!r}")
            continue
        section = next(s for s in sections if s["id"] == section_id)
        if pattern_id not in pattern_ids:
            errors.append(f"{where}: unknown pattern_id {pattern_id!r}")
            continue
        if pattern_id not in section["active_pattern_ids"]:
            errors.append(f"{where}: pattern {pattern_id} is not active in section {section_id} "
                          "(nothing to omit)")
            continue
        omissions_by_section.setdefault(section_id, set()).add(pattern_id)

    name_counts = {}
    section_instances = []
    for section in sections:
        name_counts[section["name"]] = name_counts.get(section["name"], 0) + 1
        action_entry = actions_by_section.get(section["id"])
        action = action_entry["action"] if action_entry else "retain"
        section_instances.append({
            "id": section["id"],
            "section_id": section["id"],
            "name": section["name"],
            "occurrence_index": name_counts[section["name"]],
            "action": action,
            "approved": bool(action_entry and action_entry.get("approved")) if action_entry else False,
            "omitted_layers": sorted(omissions_by_section.get(section["id"], set())),
        })
    return section_instances, errors


def build_output_timeline_and_ledger(manifest, section_instances):
    """Rebuild the continuous output-bar timeline from only the *retained*
    section instances, in original analysis order, and record every deleted
    section instance (with its removed bar count) in a separate ledger so a
    deletion is auditable without the removed bars silently disappearing from
    the record.
    """
    sections_by_id = {s["id"]: s for s in manifest.get("sections", []) or []}
    timeline = []
    ledger = []
    output_index = 0
    for instance in section_instances:
        section = sections_by_id[instance["section_id"]]
        if instance["action"] == "delete":
            ledger.append({
                "section_instance_id": instance["id"],
                "name": instance["name"],
                "removed_bars": section["bars"],
                "decision_ref": f"section_actions[{instance['section_id']}]",
            })
            continue
        for local_bar in range(section["bars"]):
            timeline.append({
                "output_index": output_index,
                "section_instance_id": instance["id"],
                "source_section_bar": local_bar,
            })
            output_index += 1
    return timeline, ledger


def slug_instrument_id(track_name, track_id, unique_name):
    """Stable, human-readable instrument id. When a track's display `name` is
    unique across the manifest (the overwhelmingly common case, and every
    existing fixture/test), the id stays the plain `instrument-<slug(name)>`
    it always was -- no behavior change. Only when two or more tracks share a
    display name (requirement 1: duplicate names are always supported, never
    rejected) is the track's stable `id` appended to disambiguate, since name
    alone is no longer a unique key.
    """
    if unique_name:
        return f"instrument-{slug(track_name)}"
    return f"instrument-{slug(track_name)}-{slug(track_id)}"


def build_instruments(manifest, resolved_patterns):
    """Build only output roles that can carry material.

    Source tracks remain intact in ``manifest["tracks"]`` for provenance; an
    instrument/output track is an executable lane, not a placeholder. Ordering
    is deterministic: LIVE when needed, loop patterns in id order, then
    UNRESOLVED when needed.

    Instruments and patterns are associated purely by each track's stable
    `id` (requirement 1), never by its display `name`: two tracks are always
    allowed to share a display name, and the compiled `analysis_track` field
    holds the track id so downstream consumers never have to disambiguate by
    name.
    """
    patterns_by_track = {}
    for pattern in resolved_patterns:
        patterns_by_track.setdefault(pattern["_track_id"], []).append(pattern)

    name_counts = {}
    for track in manifest["tracks"]:
        name_counts[track["name"]] = name_counts.get(track["name"], 0) + 1

    instruments = []
    selected_span_ids = {
        span_id for pattern in resolved_patterns for span_id in pattern["span_ids"]
    }
    for track in manifest["tracks"]:
        unique_name = name_counts[track["name"]] == 1
        instrument_id = slug_instrument_id(track["name"], track["id"], unique_name)
        track_patterns = patterns_by_track.get(track["id"], [])
        needs_live = any(
            p["final_disposition"] == "LIVE" and not p["omitted"] for p in track_patterns
        ) or any(
            span["disposition"] == "LIVE" and span["id"] not in selected_span_ids
            for span in track["spans"]
        )
        needs_unresolved = any(
            p["final_disposition"] == "UNRESOLVED" and not p["omitted"]
            for p in track_patterns
        )
        output_tracks = []
        if needs_live:
            output_tracks.append({"id": f"{instrument_id}-live", "role": "live"})
        loop_patterns = sorted(
            (p for p in track_patterns
             if p["final_disposition"] == "LOOP" and not p["omitted"]),
            key=lambda p: p["id"])
        for pattern in loop_patterns:
            output_tracks.append({
                "id": f"{instrument_id}-loop-{slug(pattern['name'])}",
                "role": "loop",
                "pattern_id": pattern["id"],
            })
        if needs_unresolved:
            output_tracks.append({"id": f"{instrument_id}-unresolved", "role": "unresolved"})
        if output_tracks:
            instruments.append({"id": instrument_id, "analysis_track": track["id"],
                                "output_tracks": output_tracks})
    return instruments


def prune_unused_instruments(instruments, placements):
    """Remove executable lanes that received no placement.

    This is intentionally after section actions/layer omissions are applied:
    a selected pattern can be absent from the final timeline without forcing an
    empty logical track into the performance plan.
    """
    used = {placement["output_track_id"] for placement in placements}
    result = []
    for instrument in instruments:
        output_tracks = [track for track in instrument["output_tracks"] if track["id"] in used]
        if output_tracks:
            result.append({**instrument, "output_tracks": output_tracks})
    return result


def resolve_variant_selections(manifest, resolved_patterns, variant_selection_requests):
    """For every resolved `LOOP` pattern whose decision option was
    `preserve_variants`, bind each declared occurrence to a concrete,
    analysis-predeclared source variant (never a newly invented one).

    Returns `(variant_selections, errors)`. A pattern that chose
    `preserve_variants` but declares no `variants` in the analysis manifest
    produces an actionable error asking for analysis to declare them; a
    declared variant occurrence with no matching selection request is also
    an error (nothing is silently defaulted).
    """
    errors = []
    selections = []
    requests_by_pattern = {}
    for i, request in enumerate(variant_selection_requests):
        where = f"variant_selections[{i}]"
        if not isinstance(request, dict):
            errors.append(f"{where}: expected object")
            continue
        extra = set(request) - {"pattern_id", "occurrence_index", "variant_id"}
        if extra:
            errors.append(f"{where}: unsupported field(s): {', '.join(sorted(extra))}")
            continue
        key = (request.get("pattern_id"), request.get("occurrence_index"))
        if key in requests_by_pattern:
            errors.append(f"{where}: duplicate selection for pattern {key[0]} occurrence {key[1]}")
            continue
        requests_by_pattern[key] = request

    patterns_by_id = {p["id"]: p for p in manifest.get("patterns", []) or []}
    for pattern in resolved_patterns:
        if pattern.get("_resolution_option") != "preserve_variants":
            continue
        declared = patterns_by_id[pattern["id"]].get("variants")
        if not declared:
            errors.append(f"pattern {pattern['id']}: preserve_variants was chosen but analysis "
                          "declares no variants for this pattern; rerun analysis with "
                          "per-occurrence source variant data before finalizing this decision")
            continue
        variants_by_id = {v["id"]: v for v in declared}
        occurrences = sorted({v.get("occurrence_index") for v in declared if "occurrence_index" in v})
        if not occurrences:
            errors.append(f"pattern {pattern['id']}: declared variants have no occurrence_index "
                          "to bind a per-occurrence selection to")
            continue
        for occurrence_index in occurrences:
            key = (pattern["id"], occurrence_index)
            request = requests_by_pattern.get(key)
            if not request:
                errors.append(f"pattern {pattern['id']}: missing variant_selections entry for "
                              f"occurrence {occurrence_index}")
                continue
            variant_id = request.get("variant_id")
            if variant_id not in variants_by_id:
                errors.append(f"pattern {pattern['id']} occurrence {occurrence_index}: "
                              f"variant_id {variant_id!r} is not a declared variant")
                continue
            variant = variants_by_id[variant_id]
            span_id = variant["span_ids"][0]
            span = next((s for t in manifest["tracks"] for s in t["spans"] if s["id"] == span_id), None)
            selections.append({
                "pattern_id": pattern["id"],
                "occurrence_index": occurrence_index,
                "variant_id": variant_id,
                "source_ref": (span or {}).get("source_ref"),
            })
    return selections, errors


def _source_ref_identity(ref):
    return (ref.get("asset_sha256"), ref.get("asset_relative_path"), ref.get("track_index"),
            ref.get("staff_index", 0), tuple(ref.get("voice_indices") or ()),
            tuple(ref.get("written_master_bars") or ()))


def pattern_source_ref(manifest, pattern):
    """Return `(source_ref_or_None, error_or_None)`: the single `source_ref` shared by
    every span this pattern selects, mirroring the identity rule `write_arrangement.mjs`'s
    `sourceRefForPattern` enforces for LOOP patterns: performed-occurrence context may
    differ across spans, but the written content location they point at must agree. Used
    for every disposition (LOOP/LIVE/UNRESOLVED) so the writer can offer source-backed
    notation for LIVE/UNRESOLVED material, not just LOOP.

    A pattern with more than one `span_id` is ambiguous here on purpose: different spans
    legitimately point at different written bars for different performed occurrences
    (the `preserve_variants`/`variant_selections` path owns that case instead), so a
    multi-span pattern returns `(None, None)` rather than guessing or raising -- the
    writer falls back to an explicit labeled cue for that placement.
    """
    if len(pattern["span_ids"]) != 1:
        return None, None
    spans_by_id = {s["id"]: s for t in manifest["tracks"] for s in t["spans"]}
    ref = None
    for span_id in pattern["span_ids"]:
        span = spans_by_id.get(span_id)
        if span is None:
            return None, f"pattern {pattern['id']}: references unknown span {span_id}"
        span_ref = span.get("source_ref")
        if span_ref is None:
            continue
        if ref is None:
            ref = span_ref
            continue
        if _source_ref_identity(ref) != _source_ref_identity(span_ref):
            return None, (f"pattern {pattern['id']}: its span_ids disagree on source_ref "
                          "identity")
    return ref, None


def _expanded_written_bars(source_ref):
    """Return the evidenced 1-based written bars as a set.

    The established source_ref shape uses a two-item list as an inclusive
    range, while analysis may retain more than two explicit noncontiguous
    bars for one LIVE span.
    """
    bars = (source_ref or {}).get("written_master_bars")
    if not bars:
        return set()
    if len(bars) == 2:
        return set(range(bars[0], bars[1] + 1))
    return set(bars)


def _written_bar_sequences(source_ref):
    bars = (source_ref or {}).get("written_master_bars")
    if not bars:
        return []
    if len(bars) == 2:
        return [list(range(bars[0], bars[1] + 1))]
    runs = []
    for bar in bars:
        if runs and bar == runs[-1][-1] + 1:
            runs[-1].append(bar)
        else:
            runs.append([bar])
    return runs


def build_unselected_live_span_placements(manifest, instruments, section_instances, timeline,
                                          selected_span_ids):
    """Compile analyzed LIVE spans that are not selected patterns into explicit
    live-track placements.

    `performed_written_bars`, when present, maps every performed section bar
    back to its written source bar. Linear fallback is allowed only for old
    manifests whose performed and written timelines are identical.
    """
    errors = []
    live_track_by_analysis_track = {}
    for instrument in instruments:
        live_track = next(
            (t for t in instrument["output_tracks"] if t["role"] == "live"), None
        )
        if live_track is not None:
            live_track_by_analysis_track[instrument["analysis_track"]] = live_track["id"]

    instances_by_section = {si["section_id"]: si for si in section_instances}
    output_by_instance_bar = {
        (entry["section_instance_id"], entry["source_section_bar"]): entry["output_index"]
        for entry in timeline
    }
    output_by_performed_position = {}
    performed_position = 0
    for section in manifest.get("sections", []) or []:
        instance = instances_by_section[section["id"]]
        for local_offset in range(section["bars"]):
            if instance["action"] == "retain":
                output_by_performed_position[performed_position] = output_by_instance_bar[
                    (instance["id"], local_offset)
                ]
            performed_position += 1
    performed_written = manifest.get("performed_written_bars")
    if performed_written is None:
        performed_written = list(range(1, performed_position + 1))

    placements = []
    occupied_by_track = {}
    for track in manifest["tracks"]:
        track_id = track["id"]
        for span in track["spans"]:
            if span["disposition"] != "LIVE" or span["id"] in selected_span_ids:
                continue
            written_sequences = _written_bar_sequences(span.get("source_ref"))
            if not written_sequences:
                errors.append(
                    f"LIVE span {span['id']} on track {track['name']} is not covered by a "
                    "selected pattern and has no written_master_bars coordinates for an "
                    "explicit live placement")
                continue
            occurrence_starts = [
                (index, sequence)
                for sequence in written_sequences
                for index in range(len(performed_written) - len(sequence) + 1)
                if performed_written[index:index + len(sequence)] == sequence
            ]
            if not occurrence_starts:
                errors.append(
                    f"LIVE span {span['id']} on track {track['name']} has no complete occurrence "
                    "in performed_written_bars")
                continue

            placed_any = False
            for occurrence_start, written_sequence in occurrence_starts:
                output_indices = [
                    output_by_performed_position.get(position)
                    for position in range(
                        occurrence_start, occurrence_start + len(written_sequence))
                ]
                if all(index is None for index in output_indices):
                    continue
                runs = []
                for output_index in (index for index in output_indices if index is not None):
                    if runs and output_index == runs[-1][1] + 1:
                        runs[-1][1] = output_index
                    else:
                        runs.append([output_index, output_index])
                for output_start, output_end in runs:
                    track_id_out = live_track_by_analysis_track[track_id]
                    for previous_start, previous_end, previous_material in \
                            occupied_by_track.get(track_id_out, []):
                        if output_start <= previous_end and previous_start <= output_end:
                            errors.append(
                                f"LIVE span {span['id']} conflicts with {previous_material} on "
                                f"output track {track_id_out} at bars "
                                f"{max(output_start, previous_start) + 1}-"
                                f"{min(output_end, previous_end) + 1}")
                            break
                    else:
                        placement = {
                            "output_track_id": track_id_out,
                            "output_bar_range": [output_start, output_end],
                            "material_id": span["id"],
                            "mode": "live",
                            "optional": False,
                            "tail_policy": None,
                        }
                        # Only preserve a source_ref when this placement represents
                        # the complete span. Splitting or narrowing invalidates any
                        # whole-span fingerprint and is deliberately cue-only.
                        original_bars = span["source_ref"].get("written_master_bars") or []
                        if (len(runs) == 1 and all(index is not None for index in output_indices) and
                                len(original_bars) <= 2 and
                                isinstance(span["source_ref"].get("track_index"), int)):
                            placement["source_ref"] = span["source_ref"]
                        placements.append(placement)
                        occupied_by_track.setdefault(track_id_out, []).append(
                            (output_start, output_end, span["id"]))
                        placed_any = True
            if not placed_any and occurrence_starts:
                errors.append(f"LIVE span {span['id']} produced no output placement")
    return placements, errors


def build_placements(manifest, resolved_patterns, instruments, section_instances, timeline, tail_policy):
    """One placement per (retained section instance, active-and-not-omitted
    pattern) pair, targeting the instrument's matching output track. Patterns
    stay distinguishable from section bars; a layer omitted in just one
    section instance produces no placement there even though the section's
    bars/timeline are untouched (distinct from deleting the section).
    """
    errors = []
    patterns_by_id = {p["id"]: p for p in resolved_patterns}
    output_track_by_pattern = {}
    live_track_by_instrument = {}
    unresolved_track_by_instrument = {}
    for instrument in instruments:
        for ot in instrument["output_tracks"]:
            if ot["role"] == "loop":
                output_track_by_pattern[ot["pattern_id"]] = ot["id"]
            elif ot["role"] == "live":
                live_track_by_instrument[instrument["analysis_track"]] = ot["id"]
            elif ot["role"] == "unresolved":
                unresolved_track_by_instrument[instrument["analysis_track"]] = ot["id"]

    sections_by_id = {s["id"]: s for s in manifest.get("sections", []) or []}
    bar_range_by_instance = {}
    for entry in timeline:
        rng = bar_range_by_instance.setdefault(entry["section_instance_id"],
                                               [entry["output_index"], entry["output_index"]])
        rng[1] = max(rng[1], entry["output_index"])
        rng[0] = min(rng[0], entry["output_index"])

    placements = []
    for instance in section_instances:
        if instance["action"] == "delete":
            continue
        bar_range = bar_range_by_instance.get(instance["id"])
        section = sections_by_id[instance["section_id"]]
        if bar_range is None:
            continue  # a retained, zero-bar edge case would have no timeline entries
        for pattern_id in section["active_pattern_ids"]:
            if pattern_id in instance["omitted_layers"]:
                continue
            pattern = patterns_by_id.get(pattern_id)
            if pattern is None:
                errors.append(f"section {instance['id']}: unknown active pattern {pattern_id}")
                continue
            disposition = pattern["final_disposition"]
            if pattern["omitted"]:
                continue  # explicitly omitted (including a default-omitted OPTIONAL
                          # pattern with no retain decision/retain_role): no physical
                          # placement. A *retained* OPTIONAL pattern's final_disposition
                          # is never literally "OPTIONAL" (see resolve_dispositions), so
                          # it falls through to the matching LOOP/LIVE/UNRESOLVED branch
                          # below like any other pattern, just flagged optional=true.
            if disposition == "LOOP":
                track_id = output_track_by_pattern.get(pattern_id)
                mode = "loop"
            elif disposition == "LIVE":
                track_id = live_track_by_instrument.get(pattern["_track_id"])
                mode = "live"
            elif disposition == "UNRESOLVED":
                track_id = unresolved_track_by_instrument.get(pattern["_track_id"])
                mode = "unresolved"
            else:
                continue
            if track_id is None:
                errors.append(f"pattern {pattern_id}: no output track resolved for "
                              f"disposition {disposition}")
                continue
            placement_tail_policy = None
            if mode == "loop" and section["bars"] % pattern["bars"] != 0:
                if tail_policy is None:
                    errors.append(f"section {instance['id']} pattern {pattern_id}: a partial "
                                  f"loop cycle ({section['bars']} bars / {pattern['bars']}-bar "
                                  "pattern) requires an explicit reviewed tail_policy")
                    continue
                if tail_policy == "reviewed_no_partial_cycle":
                    errors.append(f"section {instance['id']} pattern {pattern_id}: "
                                  "reviewed_no_partial_cycle forbids a partial loop cycle "
                                  f"({section['bars']} bars / {pattern['bars']}-bar pattern); "
                                  "choose reviewed_truncate/reviewed_wrap_full_cycles_only or "
                                  "adjust section bars")
                    continue
                placement_tail_policy = tail_policy
            source_ref, ref_error = pattern_source_ref(manifest, pattern)
            if ref_error:
                errors.append(ref_error)
                continue
            placement = {
                "output_track_id": track_id,
                "output_bar_range": list(bar_range),
                "material_id": pattern_id,
                "mode": mode,
                "optional": pattern["disposition"] == "OPTIONAL",
                "tail_policy": placement_tail_policy,
            }
            if source_ref is not None:
                placement["source_ref"] = source_ref
            placements.append(placement)
    return placements, errors


def compile_performance_arrangement(manifest, resolutions, performance):
    """Pure function: returns `(output_dict, errors)`.

    `output_dict` is `None` when `errors` is nonempty. Every decision must be
    resolved (honoring `depends_on`/`applies_when` gating); a `final`
    publication rejects any pattern/span that remains `UNRESOLVED` or any
    `LIVE`/`REST`/omitted-`OPTIONAL` disposition without an explicit mention
    in `limitations` (nothing is implicitly silent), while `partial`/`draft`
    allow those same facts to survive, provided they are named in
    `limitations` (partial) and disclosed structurally (always).

    Never mutates the caller's `manifest` object (requirement 2/4: this
    function immediately works on its own private deep copy). This both keeps
    repeated calls on the same caller-held object deterministic and ensures
    `fingerprints.analysis_sha256` below is always the hash of exactly the
    manifest the caller passed in -- byte-identical to the on-disk analysis
    file in the real CLI path -- and not of a post-validation copy carrying
    scratch fields (e.g. each pattern's `_track_id`, a fallback track `id`)
    that `load_analysis_manifest` injects in place and that the real,
    untouched `--analysis` file a later `write_arrangement.mjs` build reads
    never has.
    """
    errors = []
    manifest = copy.deepcopy(manifest)
    analysis_fingerprint_source = copy.deepcopy(manifest)
    try:
        # Defensive/idempotent (requirement 1): ensures every track/pattern carries
        # a resolved stable id/_track_id even when a caller passes an unvalidated
        # manifest dict directly (main() already validates via load_analysis_manifest
        # before calling here; re-running it on an already-validated manifest is a
        # no-op since `_pattern`/`_track` re-derive rather than reject their own
        # previously-injected fields).
        load_analysis_manifest(manifest)
    except Invalid as exc:
        return None, [str(exc)]
    try:
        pub = _validate_performance(performance)
    except Invalid as exc:
        return None, [str(exc)]

    try:
        decision_resolutions, section_actions, layer_omissions, variant_requests = \
            _normalize_resolutions_input(resolutions)
    except Invalid as exc:
        return None, [str(exc)]

    resolutions_by_id, resolution_errors = validate_resolutions(manifest, decision_resolutions)
    errors.extend(resolution_errors)

    resolved_patterns, resolution_mapping_errors = resolve_dispositions(manifest, resolutions_by_id)
    errors.extend(resolution_mapping_errors)

    # Any track span disposition that is UNRESOLVED and not covered by a
    # selected pattern is also a hard gap: there is no way to publish it.
    pattern_span_ids = {sid for p in manifest.get("patterns", []) or [] for sid in p["span_ids"]}
    for track in manifest["tracks"]:
        for span in track["spans"]:
            if span["disposition"] == "UNRESOLVED" and span["id"] not in pattern_span_ids:
                errors.append(f"span {span['id']} on track {track['name']} is UNRESOLVED and not "
                              "covered by any selected pattern")

    section_instances, section_errors = build_section_instances(manifest, section_actions,
                                                                  layer_omissions)
    errors.extend(section_errors)

    if errors:
        return None, errors

    unresolved_patterns = [p for p in resolved_patterns if p["final_disposition"] == "UNRESOLVED"]
    if pub["status"] == "final" and unresolved_patterns:
        errors.append("Final performance export cannot contain an UNRESOLVED disposition")
        return None, errors

    variant_selections, variant_errors = resolve_variant_selections(
        manifest, resolved_patterns, variant_requests)
    errors.extend(variant_errors)
    if errors:
        return None, errors

    timeline, removal_ledger = build_output_timeline_and_ledger(manifest, section_instances)
    instruments = build_instruments(manifest, resolved_patterns)
    placements, placement_errors = build_placements(
        manifest, resolved_patterns, instruments, section_instances, timeline, pub["tail_policy"])
    errors.extend(placement_errors)
    implicit_live_placements, implicit_live_errors = build_unselected_live_span_placements(
        manifest, instruments, section_instances, timeline, pattern_span_ids)
    placements.extend(implicit_live_placements)
    errors.extend(implicit_live_errors)
    instruments = prune_unused_instruments(instruments, placements)
    placements_by_track = {}
    implicit_live_material_ids = {
        s["id"] for t in manifest["tracks"] for s in t["spans"]
        if s["disposition"] == "LIVE" and s["id"] not in pattern_span_ids
    }
    for placement in placements:
        placements_by_track.setdefault(placement["output_track_id"], []).append(placement)
    for track_id, track_placements in placements_by_track.items():
        ordered = sorted(track_placements, key=lambda p: p["output_bar_range"])
        for previous, current in zip(ordered, ordered[1:]):
            if (current["output_bar_range"][0] <= previous["output_bar_range"][1] and
                    (previous["material_id"] in implicit_live_material_ids or
                     current["material_id"] in implicit_live_material_ids)):
                errors.append(
                    f"output track {track_id}: conflicting overlapping placements "
                    f"{previous['material_id']} bars {previous['output_bar_range'][0] + 1}-"
                    f"{previous['output_bar_range'][1] + 1} and {current['material_id']} bars "
                    f"{current['output_bar_range'][0] + 1}-"
                    f"{current['output_bar_range'][1] + 1}")
    if errors:
        return None, errors

    # Make every non-LOOP/REST-with-evidence behavior explicit rather than
    # silently omitted: collect what a reader must be told about.
    live_patterns = [p for p in resolved_patterns if p["final_disposition"] == "LIVE"]
    live_spans_without_pattern = [
        s["id"] for t in manifest["tracks"] for s in t["spans"]
        if s["disposition"] == "LIVE" and s["id"] not in pattern_span_ids
    ]
    omitted_patterns = [p for p in resolved_patterns if p["omitted"] or
                        p["final_disposition"] == "OPTIONAL"]
    rest_spans = [s["id"] for t in manifest["tracks"] for s in t["spans"] if s["disposition"] == "REST"]
    optional_spans_without_pattern = [
        s["id"] for t in manifest["tracks"] for s in t["spans"]
        if s["disposition"] == "OPTIONAL" and s["id"] not in pattern_span_ids
    ]

    # `LIVE` is not itself a blocker for Final (per the five-disposition
    # contract) but every LIVE/omitted-OPTIONAL/REST fact must still be
    # explicit somewhere in the handoff; rather than requiring free-text
    # limitations to echo each one verbatim (brittle string matching), they
    # are always recorded structurally in `disclosure` below so nothing is
    # silently implied to be a scored loop.

    resolved_patterns_public = [{k: v for k, v in p.items() if not k.startswith("_")}
                                for p in resolved_patterns]

    output = {
        "version": 2,
        "plan_version": PLAN_VERSION,
        "source": manifest["source"],
        "tracks": manifest["tracks"],
        "decisions": manifest.get("decisions", []),
        "resolutions": resolutions,
        "patterns": resolved_patterns_public,
        "sections": manifest.get("sections", []),
        "tempo_bpm": manifest.get("tempo_bpm"),
        "meter": manifest.get("meter"),
        **({"performed_written_bars": manifest["performed_written_bars"]}
           if "performed_written_bars" in manifest else {}),
        "publication": {
            "status": pub["status"],
            "limitations": pub["limitations"],
            "consent": pub["consent"],
        },
        "performance": pub["performance"],
        "disclosure": {
            "live_patterns": [p["id"] for p in live_patterns],
            "live_spans_without_selected_pattern": live_spans_without_pattern,
            "omitted_patterns": [p["id"] for p in omitted_patterns],
            "rest_spans": rest_spans,
            "optional_spans_without_selected_pattern": optional_spans_without_pattern,
            "unresolved_patterns": [p["id"] for p in unresolved_patterns],
        },
        "fingerprints": {
            "analysis_sha256": canonical_sha256(analysis_fingerprint_source),
            "resolutions_sha256": canonical_sha256(resolutions),
        },
        "instruments": instruments,
        "section_instances": section_instances,
        "output_timeline": timeline,
        "removal_ledger": removal_ledger,
        "placements": placements,
        "variant_selections": variant_selections,
        "tail_policy": pub["tail_policy"],
        "export_layout": {
            "version": 1,
            "prune_zero_note_tracks": True,
            "group_order": ["core", "optional", "unresolved"],
            "colors": {"drums": "#0000FF", "bass": "#FFFF00", "other": "#FF0000"},
        },
    }
    try:
        validate_performance_plan(output)
    except Invalid as exc:
        return None, [f"internal error: compiled performance plan failed self-validation: {exc}"]
    return output, []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--resolutions", required=True, type=Path)
    parser.add_argument("--performance", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--replace", action="store_true",
                        help="explicit mode allowing an existing performance handoff to be "
                             "atomically replaced; without it, an existing output blocks")
    args = parser.parse_args(argv)
    try:
        for path in (args.manifest, args.resolutions, args.performance):
            if path.stat().st_size > MAX_BYTES:
                raise Invalid(f"{path}: exceeds {MAX_BYTES} bytes")
        raw_manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if raw_manifest.get("version") != 2:
            raise Invalid("compiler only supports version 2 analysis manifests")
        # Requirement 2/4: validate on a throwaway deep copy only, for a clean early
        # error message; `compile_performance_arrangement` never mutates its `manifest`
        # argument (it deep-copies internally), so `manifest` here stays exactly the
        # parsed, untouched on-disk file -- matching what a later
        # `write_arrangement.mjs --analysis` build reads and fingerprints.
        load_analysis_manifest(copy.deepcopy(raw_manifest))
        manifest = raw_manifest
        resolutions = json.loads(args.resolutions.read_text(encoding="utf-8"))
        if not isinstance(resolutions, (list, dict)):
            raise Invalid("resolutions: expected a JSON array or object")
        performance = json.loads(args.performance.read_text(encoding="utf-8"))

        if args.output.exists() and not args.replace:
            reason = "output already exists; rerun with --replace for an explicit migration/recompile"
            try:
                existing = json.loads(args.output.read_text(encoding="utf-8"))
                if is_legacy_v2_performance(existing):
                    reason = LEGACY_V2_RECOMPILE_MESSAGE
            except (OSError, json.JSONDecodeError):
                pass
            print(json.dumps({"status": "blocked", "conflicts": [str(args.output)],
                              "reason": reason}))
            return 1

        output, errors = compile_performance_arrangement(manifest, resolutions, performance)
        if errors:
            print(json.dumps({"status": "error", "errors": errors}), file=sys.stderr)
            return 1
        _write_atomic(args.output, json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    except (Invalid, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"status": "written", "file": str(args.output)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
