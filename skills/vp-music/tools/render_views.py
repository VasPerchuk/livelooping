"""Render generated-view Markdown from a version 2 analysis manifest.

Produces `.vp-music-tracking\\pattern\\<instrument>.patterns.md` (one per
track that has at least one selected pattern) and
`.vp-music-tracking\\draft\\worksheet-draft.md` (only when the manifest
declares an ordered `sections` list) from the authoritative
`arrangement.json` v2 handoff. The manifest is the single source of truth;
these files are generated, read-only-by-convention views for humans, not a
second place to hand-edit facts. See `analyze\\references\\arrangement-handoff.md`.

Usage:
  python render_views.py --manifest <song>\\.vp-music-tracking\\arrangement\\arrangement.json --song-dir <song>
  python render_views.py --manifest <song>\\...\\arrangement.json --song-dir <song> --replace

Safety:
  * No path in the manifest or on the command line is trusted blindly: the
    manifest is schema-validated (`v2_schema.load_analysis_manifest`) before
    any rendering happens, and no raw source notation/XML is read or echoed.
  * By default, if any generated target file already exists, nothing is
    written and the tool reports every conflicting path; use `--replace`
    (an explicit migration/re-render mode) to intentionally regenerate.
  * Every write (whether creating a new file or using `--replace`) stages
    content in a temporary file in the same directory and uses an atomic
    rename into place, so a crash mid-run cannot leave a half-written or
    corrupted generated view, and partial failures never leave some files
    updated and others stale.
"""

import argparse
import json
from pathlib import Path
import re
import sys
import tempfile

from v2_schema import Invalid, load_analysis_manifest, load_performance_plan
from music_assets import inspect as inspect_music_asset

MAX_MANIFEST_BYTES = 1_000_000


def _slug(name):
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "track").lower()).strip("-")
    return slug or "track"


def _fmt_coordinates(source_ref):
    if not source_ref:
        return "unsourced (no source_ref recorded)"
    parts = []
    bars = source_ref.get("written_master_bars")
    if bars:
        parts.append(f"written master bars {bars[0]}-{bars[-1]}" if len(bars) > 1
                     else f"written master bar {bars[0]}")
    occurrences = source_ref.get("performed_occurrences")
    if occurrences:
        parts.append(f"performed occurrences {', '.join(str(o) for o in occurrences)}")
    ending = source_ref.get("ending_context")
    if ending:
        parts.append(f"ending: {ending}")
    track_name = source_ref.get("track_name")
    if track_name:
        parts.append(f"source track {track_name}")
    path = source_ref.get("asset_relative_path")
    if path:
        parts.append(f"asset {path}")
    return "; ".join(parts) if parts else "source_ref present but no coordinate fields set"


def _fmt_near_match(near_match):
    if not near_match:
        return None
    offsets = ", ".join(str(o) for o in near_match["divergent_bar_offsets"])
    return (f"{near_match['matching_bars']}/{near_match['total_bars']} bars match; "
            f"diverges at bar offset(s) {offsets}")


def _source_ref_for_pattern(pattern, spans_by_id):
    canonical = next(
        (variant for variant in pattern.get("variants", []) if variant.get("is_canonical")),
        None,
    )
    span_ids = canonical["span_ids"] if canonical else pattern["span_ids"]
    refs = [spans_by_id[span_id].get("source_ref") for span_id in span_ids]
    refs = [ref for ref in refs if ref]
    return refs[0] if refs else None


def _number(value):
    rounded = round(float(value), 3)
    return str(int(rounded)) if rounded.is_integer() else str(rounded)


def _pattern_grid(source_score, source_ref):
    if not source_score:
        return None, "validated source notes were not available while rendering"
    if not source_ref:
        return None, "pattern has no exact source_ref"
    track_index = source_ref.get("track_index")
    bars = source_ref.get("written_master_bars")
    if not isinstance(track_index, int) or not bars or len(bars) != 2:
        return None, "source_ref needs track_index and one contiguous written bar range"
    if not 0 <= track_index < len(source_score.get("tracks", [])):
        return None, f"source track index {track_index} does not exist"
    start_bar, end_bar = bars
    score_bars = {
        bar["number"]: bar for bar in source_score.get("bars", [])
        if start_bar <= bar["number"] <= end_bar
    }
    if len(score_bars) != end_bar - start_bar + 1:
        return None, "one or more source bars are unavailable"
    track = source_score["tracks"][track_index]
    selected = [
        note for note in track.get("notes", [])
        if start_bar <= note.get("bar", 0) <= end_bar
    ]
    bar_numbers = list(range(start_bar, end_bar + 1))

    def token(note):
        bar = score_bars[note["bar"]]
        beat = note["start_quarters"] - bar["start_quarters"] + 1
        return f"{_number(beat)}:{_number(note['duration_quarters'])}"

    staves = track.get("staves", [])
    fretted = bool(staves and staves[0].get("tuning_midi") and selected and all(
        note.get("string") is not None and note.get("fret") is not None for note in selected
    ))
    percussion = bool(staves and staves[0].get("percussion"))
    if fretted and not percussion:
        lanes = list(range(1, len(staves[0]["tuning_midi"]) + 1))
        heading = "String/fret grid (`beat:fret/duration`; `-` = rest):"
        labels = {lane: f"s{lane}" for lane in lanes}
        lane_for = lambda note: note["string"]
        value_for = lambda note: f"{token(note).split(':', 1)[0]}:{note['fret']}/" \
                                 f"{_number(note['duration_quarters'])}"
    else:
        lanes = sorted({note.get("pitch") for note in selected
                        if note.get("pitch") is not None}, reverse=True)
        heading = ("Percussion grid (`beat:duration`; MIDI pitch lanes):"
                   if percussion else
                   "Pitch grid (`beat:duration`; MIDI pitch lanes):")
        labels = {lane: f"p{lane}" for lane in lanes}
        lane_for = lambda note: note.get("pitch")
        value_for = token
    if not lanes:
        return None, "source range contains no renderable note events"

    lines = [heading, "```text",
             "lane | " + " | ".join(f"bar {number}" for number in bar_numbers)]
    for lane in lanes:
        cells = []
        for bar_number in bar_numbers:
            events = [
                value_for(note) for note in selected
                if note.get("bar") == bar_number and lane_for(note) == lane
            ]
            cells.append(",".join(events) if events else "-")
        lines.append(f"{labels[lane]:>4} | " + " | ".join(cells))
    lines.append("```")
    return lines, None


def render_pattern_markdown(manifest, source_score=None, source_error=None):
    """Return {stable_file_key: markdown_text} for every track with patterns."""
    track_records = []
    for index, track in enumerate(manifest["tracks"]):
        track_records.append((track.get("id", f"track-{index}"), track))
    tracks_by_id = dict(track_records)
    track_ids_by_name = {}
    for track_id, track in track_records:
        track_ids_by_name.setdefault(track["name"], []).append(track_id)
    name_counts = {}
    for track in manifest["tracks"]:
        name_counts[track["name"]] = name_counts.get(track["name"], 0) + 1
    patterns_by_track = {}
    for pattern in manifest.get("patterns", []) or []:
        track_id = pattern.get("_track_id") or pattern.get("track_id")
        if track_id is None:
            candidates = track_ids_by_name.get(pattern["track"], [])
            if len(candidates) != 1:
                raise Invalid(f"pattern {pattern['id']}: display name {pattern['track']!r} "
                              "does not resolve to one stable track id")
            track_id = candidates[0]
        patterns_by_track.setdefault(track_id, []).append(pattern)

    documents = {}
    for track_id, patterns in patterns_by_track.items():
        track = tracks_by_id[track_id]
        track_name = track["name"]
        spans_by_id = {span["id"]: span for span in track["spans"]}
        lines = [f"# {track_name} patterns", "",
                 f"**Role:** {track.get('role', 'unknown')} "
                 f"(confidence: {track.get('role_confidence', 'unknown')})",
                 "**Generated from:** `.vp-music-tracking\\arrangement\\arrangement.json` "
                 "(version 2) — do not hand-edit; re-render after updating the manifest.", ""]
        for pattern in patterns:
            lines.append(f"## Pattern: {pattern['name']}")
            lines.append("")
            lines.append(f"**ID:** `{pattern['id']}`   **Length:** {pattern['bars']} bar"
                         f"{'s' if pattern['bars'] != 1 else ''}   "
                         f"**Disposition:** {pattern['disposition']}   "
                         f"**Confidence:** {pattern.get('confidence', 'unknown')}")
            lines.append("")
            lines.append("**Source spans:**")
            for span_id in pattern["span_ids"]:
                span = spans_by_id[span_id]
                tier = span.get("match_tier", "unspecified")
                evidence = span.get("evidence", "no recorded evidence")
                coords = _fmt_coordinates(span.get("source_ref"))
                lines.append(f"- `{span_id}` — disposition {span['disposition']}, "
                             f"match tier {tier}")
                lines.append(f"  - Coordinates: {coords}")
                lines.append(f"  - Evidence: {evidence}")
                near = _fmt_near_match(span.get("near_match"))
                if near:
                    lines.append(f"  - Near-loop divergence: {near}")
            lines.append("")
            source_ref = _source_ref_for_pattern(pattern, spans_by_id)
            grid, grid_error = _pattern_grid(source_score, source_ref)
            if grid:
                lines.extend(["**Playable reduction:**", "", *grid])
            else:
                reason = source_error or grid_error
                lines.append(f"**Playable reduction gap:** {reason}.")
            # Decisions are matched to spans by id prefix convention
            # (`decision-<span-or-candidate-id>[...]`), since the schema does
            # not otherwise require a decision to name its spans.
            explicit_decisions = set(pattern.get("decision_ids", []))
            decisions_for_pattern = [
                d for d in manifest.get("decisions", []) or []
                if d["id"] in explicit_decisions or (
                    not explicit_decisions and
                    any(d["id"] == f"decision-{sid}" or
                        d["id"].startswith(f"decision-{sid}-")
                        for sid in pattern["span_ids"]))
            ]
            if decisions_for_pattern:
                lines.append("")
                lines.append("**Open decisions:**")
                for decision in decisions_for_pattern:
                    gate = ""
                    if "depends_on" in decision:
                        gate = f" (applies only if `{decision['depends_on']}` -> " \
                               f"`{decision['applies_when']}`)"
                    options = ", ".join(decision["options"])
                    lines.append(f"- `{decision['id']}`{gate}: {decision['question']} "
                                 f"[{options}]")
            lines.append("")
        file_key = track_name if name_counts[track_name] == 1 else f"{track_name}-{track_id}"
        documents[file_key] = "\n".join(lines).rstrip() + "\n"
    return documents


def _seconds_for_bars(bars, tempo_bpm, meter):
    if not tempo_bpm or not meter:
        return None
    num, den = (int(x) for x in meter.split("/"))
    quarters_per_bar = num * 4 / den
    return round(bars * quarters_per_bar * 60 / tempo_bpm, 1)


def render_worksheet_markdown(manifest, title="Untitled"):
    """Return worksheet-draft.md text, or None if no `sections` are declared."""
    sections = manifest.get("sections") or []
    if not sections:
        return None
    patterns = {p["id"]: p for p in manifest.get("patterns", []) or []}
    tempo_bpm = manifest.get("tempo_bpm")
    meter = manifest.get("meter")

    lines = [f"# {title}", "", "**Status: Draft**"]
    if tempo_bpm:
        lines.append(f"**Tempo:** quarter = {tempo_bpm} BPM" + (f"  **Meter:** {meter}" if meter else ""))
    lines.append("")
    pattern_list = list(patterns.values())
    lines.append(f"## Patterns ({len(pattern_list)} total)")
    for pattern in pattern_list:
        lines.append(f"- **{pattern['name']}** ({pattern['bars']} bar"
                     f"{'s' if pattern['bars'] != 1 else ''}) [`{pattern['id']}`, "
                     f"{pattern['disposition']}]")
    lines.append("")
    lines.append("## Structure")
    for section in sections:
        active_names = [patterns[pid]["name"] for pid in section["active_pattern_ids"]]
        seconds = _seconds_for_bars(section["bars"], tempo_bpm, meter)
        timing = f" (~{seconds} seconds)" if seconds is not None else " (~? seconds; tempo/meter not yet set)"
        unit = "bar" if section["bars"] == 1 else "bars"
        lines.append(f"### {section['name']} - {section['bars']} {unit}{timing}")
        if active_names:
            lines.append(f"Patterns: {', '.join(active_names)}")
        else:
            policy = section.get("empty_policy")
            lines.append(f"Patterns: none ({policy or 'unspecified'})")
        lines.append("Transition: TODO — generated view carries no transition evidence; "
                     "author the actual transition during analysis.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- published (resolved) worksheet, rendered from a compiled performance plan ---------
# `render_worksheet_markdown` above renders the analysis-time **Draft** preview from
# `arrangement.json`; this renders the **published** worksheet from a *compiled*
# `performance-arrangement.json` (plan_version 2) -- the finalize-contract's
# `instruments`/`output_tracks`/`section_instances`/`output_timeline`/`placements`/
# `removal_ledger`/`disclosure`/`publication`, never the Draft's raw `sections`/
# `patterns` alone. The caller (CLI `render_published` below) validates the plan with
# `v2_schema.load_performance_plan` before calling this; this function assumes a
# validated plan and never invents data: a Final/Partial publication requires explicit
# `tempo_bpm`/`meter` (a constant, writer-supported tempo/meter, matching
# `write_arrangement.mjs`'s `buildV2` requirement) or this raises `Invalid` rather than
# estimating, and a Draft (unpublished preview) publication status is rejected outright
# since a Draft is never a legitimate target for `worksheet.md`.
def _section_action_reasons(plan):
    """`{section_id: reason}` for every `section_actions[]` resolution entry that
    recorded a nonempty `reason` -- the only mechanism this renderer trusts as a
    *confirmed transition resolution* for a deletion-created adjacency (requirement 2:
    never invent transition music; only report what was explicitly decided).
    """
    resolutions = plan.get("resolutions")
    section_actions = resolutions.get("section_actions", []) if isinstance(resolutions, dict) else []
    reasons = {}
    for entry in section_actions or []:
        if isinstance(entry, dict) and entry.get("section_id") and entry.get("reason"):
            reasons[entry["section_id"]] = entry["reason"]
    return reasons


def _output_track_index(plan):
    """`output_track_id -> {instrument_id, analysis_track, role, pattern_id}`."""
    index = {}
    for instrument in plan.get("instruments", []) or []:
        for ot in instrument["output_tracks"]:
            index[ot["id"]] = {"instrument_id": instrument["id"],
                               "analysis_track": instrument["analysis_track"],
                               "role": ot["role"], "pattern_id": ot.get("pattern_id")}
    return index


def _timeline_runs(output_timeline):
    """Collapse the compiled plan's per-bar `output_timeline` into one
    `(section_instance_id, start_output_index, end_output_index)` run per contiguous
    stretch, preserving compiled order.
    """
    runs = []
    for entry in output_timeline:
        if runs and runs[-1][0] == entry["section_instance_id"]:
            runs[-1][2] = entry["output_index"]
        else:
            runs.append([entry["section_instance_id"], entry["output_index"], entry["output_index"]])
    return [tuple(r) for r in runs]


def render_published_worksheet(plan, title="Untitled"):
    """Return the published `worksheet.md` text for a compiled, validated
    `performance-arrangement.json` (plan_version 2). Raises `Invalid` (never estimates)
    when the plan is a Draft preview, lacks the constant `tempo_bpm`/`meter` the writer
    requires for timing, or (defensively) a Final publication still names an unresolved
    pattern.
    """
    publication = plan["publication"]
    status = publication["status"]
    if status == "draft":
        raise Invalid("render_published_worksheet: a Draft preview is never a legitimate "
                      "publication target for worksheet.md (compile to partial/final first)")
    tempo_bpm = plan.get("tempo_bpm")
    meter = plan.get("meter")
    if not tempo_bpm or not meter:
        raise Invalid("render_published_worksheet: a published worksheet requires explicit "
                      "constant tempo_bpm/meter on the performance plan; refusing to estimate")
    disclosure = plan["disclosure"]
    if status == "final" and disclosure.get("unresolved_patterns"):
        raise Invalid("render_published_worksheet: a Final publication cannot disclose an "
                      "UNRESOLVED pattern")

    tracks_by_id = {t["id"]: t for t in plan.get("tracks", []) or []}
    patterns_by_id = {p["id"]: p for p in plan.get("patterns", []) or []}
    sections_by_id = {s["id"]: s for s in plan.get("sections", []) or []}
    instances_by_id = {si["id"]: si for si in plan.get("section_instances", []) or []}
    output_track_index = _output_track_index(plan)
    all_placements = plan.get("placements", []) or []

    total_bars = len(plan.get("output_timeline", []) or [])
    total_seconds = _seconds_for_bars(total_bars, tempo_bpm, meter)

    status_label = status.capitalize()
    lines = [f"# {title}", "", f"**Status: {status_label}**"]
    lines.append(f"**Tempo:** quarter = {tempo_bpm} BPM  **Meter:** {meter}")
    lines.append(f"**Total:** {total_bars} output bar{'s' if total_bars != 1 else ''} "
                 f"(~{total_seconds} seconds)")
    if status == "partial":
        lines.append(f"**Limitations:** {'; '.join(publication['limitations'])}")
        if publication.get("consent"):
            lines.append(f"**Consent:** {publication['consent']}")
    infeasible = [
        entry for entry in plan.get("performance", {}).get("section_transitions", [])
        if not entry["feasible"]
    ]
    if infeasible:
        summary = "; ".join(
            f"{entry['section_id']}: {entry['warning']}" for entry in infeasible
        )
        lines.append(f"**Transition warning:** INFEASIBLE — {summary}")
    lines.append("")

    # --- Instruments: friendly label + stable ids for every output track -------------
    lines.append("## Instruments")
    for instrument in plan.get("instruments", []) or []:
        track = tracks_by_id[instrument["analysis_track"]]
        lines.append(f"- **{track['name']}** (`{track['id']}`) — instrument `{instrument['id']}`")
        for ot in instrument["output_tracks"]:
            if ot["role"] == "loop":
                pattern = patterns_by_id[ot["pattern_id"]]
                lines.append(f"  - loop \"{pattern['name']}\" (`{pattern['id']}`): `{ot['id']}`")
            else:
                lines.append(f"  - {ot['role']}: `{ot['id']}`")
    lines.append("")

    # --- Structure: compiled output order, bars/times, source occurrence, active -----
    # tracks/cues, and an explicit (never invented) transition into each section. ------
    lines.append("## Structure")
    runs = _timeline_runs(plan.get("output_timeline", []) or [])
    reasons = _section_action_reasons(plan)
    section_transitions = {
        entry["section_id"]: entry
        for entry in plan.get("performance", {}).get("section_transitions", [])
    }
    section_instances = plan.get("section_instances", []) or []
    orig_index = {si["id"]: i for i, si in enumerate(section_instances)}
    retained_ids = [si["id"] for si in section_instances if si["action"] == "retain"]
    cumulative = 0.0
    for position, (instance_id, start, end) in enumerate(runs, start=1):
        instance = instances_by_id[instance_id]
        bars = end - start + 1
        seconds = _seconds_for_bars(bars, tempo_bpm, meter)
        cumulative += seconds
        unit = "bar" if bars == 1 else "bars"
        lines.append(f"### {position}. {instance['name']} (occurrence {instance['occurrence_index']})"
                     f" — output bars {start + 1}-{end + 1}, {bars} {unit}"
                     f" (~{seconds}s, cumulative ~{round(cumulative, 1)}s)")
        lines.append(f"Source: analysis section `{instance['section_id']}`, "
                     f"section instance `{instance['id']}`")
        placements = [
            placement for placement in all_placements
            if start <= placement["output_bar_range"][0] and
            placement["output_bar_range"][1] <= end
        ]
        active_instrument_ids = set()
        if placements:
            lines.append("Active:")
            for placement in sorted(placements, key=lambda p: p["output_track_id"]):
                meta = output_track_index[placement["output_track_id"]]
                active_instrument_ids.add(meta["instrument_id"])
                track = tracks_by_id[meta["analysis_track"]]
                mode = placement["mode"].upper()
                detail = ""
                material = patterns_by_id.get(placement["material_id"])
                material_label = f" \"{material['name']}\"" if material else ""
                if meta["role"] == "loop":
                    detail = material_label
                else:
                    placement_start, placement_end = placement["output_bar_range"]
                    detail = (f"{material_label} — manual cue: bars "
                              f"{placement_start + 1}-{placement_end + 1}")
                optional = " [OPTIONAL]" if placement["optional"] else ""
                lines.append(f"- {mode} `{placement['output_track_id']}`{detail} "
                             f"({track['name']}){optional}")
        else:
            section_def = sections_by_id.get(instance["section_id"])
            if section_def and section_def.get("empty_policy") == "live":
                lines.append("Active: none (LIVE section has no instrument-specific placement)")
            else:
                lines.append("Active: none (REST for every instrument)")
        resting = [tracks_by_id[instrument["analysis_track"]]["name"]
                  for instrument in plan.get("instruments", []) or []
                  if instrument["id"] not in active_instrument_ids]
        if resting:
            lines.append(f"Resting: {', '.join(resting)}")
        confirmed_transition = section_transitions.get(instance["section_id"])
        if confirmed_transition:
            prefix = "INFEASIBLE — " if not confirmed_transition["feasible"] else ""
            lines.append(f"Transition: {prefix}{confirmed_transition['action']}")
            if not confirmed_transition["feasible"]:
                lines.append(
                    f"**Transition warning:** INFEASIBLE — {confirmed_transition['warning']}"
                )
        elif position == 1:
            lines.append("Transition: opening section.")
        else:
            prev_id = retained_ids[retained_ids.index(instance_id) - 1]
            gap_instances = section_instances[orig_index[prev_id] + 1:orig_index[instance_id]]
            removed = [si for si in gap_instances if si["action"] == "delete"]
            if not removed:
                lines.append("Transition: contiguous with previous section (no edits between).")
            else:
                removed_desc = ", ".join(f"{si['name']} (occurrence {si['occurrence_index']})"
                                         for si in removed)
                matched = [reasons[si["section_id"]] for si in removed if si["section_id"] in reasons]
                if matched:
                    lines.append(f"Transition: new adjacency after removing {removed_desc} — "
                                 f"{'; '.join(matched)}")
                elif status == "final":
                    raise Invalid(f"render_published_worksheet: section instance {instance_id!r} "
                                  f"gained a new adjacency from deleting {removed_desc} with no "
                                  "confirmed transition resolution (record a `reason` on the "
                                  "section_actions deletion); a Final publication cannot ship "
                                  "this unresolved")
                else:
                    lines.append(f"Transition: REVIEW REQUIRED — deleting {removed_desc} created "
                                 "a new adjacency here with no confirmed transition resolution; "
                                 "no transition music is invented.")
        lines.append("")

    # --- Omissions and edits: deleted sections and omitted (non-retained) patterns, ---
    # kept out of the Structure timeline per requirement 2. ----------------------------
    lines.append("## Omissions and Edits")
    removal_ledger = plan.get("removal_ledger", []) or []
    if not removal_ledger and not disclosure.get("omitted_patterns"):
        lines.append("None.")
    for entry in removal_ledger:
        reason = reasons.get(instances_by_id[entry["section_instance_id"]]["section_id"])
        reason_text = f" — {reason}" if reason else ""
        lines.append(f"- DELETED section `{entry['section_instance_id']}` \"{entry['name']}\", "
                     f"{entry['removed_bars']} bar{'s' if entry['removed_bars'] != 1 else ''} "
                     f"removed ({entry['decision_ref']}){reason_text}")
    for pattern_id in disclosure.get("omitted_patterns", []) or []:
        pattern = patterns_by_id[pattern_id]
        lines.append(f"- OMITTED pattern `{pattern_id}` \"{pattern['name']}\" (not retained)")
    lines.append("")

    # --- Retained OPTIONAL material: a distinct, explicit state from omit/delete. ----
    retained_optional = [p for p in plan.get("patterns", []) or []
                         if p.get("disposition") == "OPTIONAL" and not p.get("omitted")]
    if retained_optional:
        lines.append("## Retained Optional Material")
        for pattern in retained_optional:
            placement = next((p for p in plan.get("placements", []) or []
                              if p["material_id"] == pattern["id"]), None)
            track_label = ""
            if placement:
                meta = output_track_index[placement["output_track_id"]]
                track_label = (f" on {meta['role']} track `{placement['output_track_id']}` "
                               f"({tracks_by_id[meta['analysis_track']]['name']})")
            lines.append(f"- `{pattern['id']}` \"{pattern['name']}\" retained{track_label} "
                         "[OPTIONAL]")
        lines.append("")

    # --- Unresolved tracks/limitations: only ever present under consented Partial. ---
    unresolved_patterns = disclosure.get("unresolved_patterns", []) or []
    if unresolved_patterns:
        lines.append("## Unresolved (Partial)")
        for pattern_id in unresolved_patterns:
            pattern = patterns_by_id[pattern_id]
            lines.append(f"- `{pattern_id}` \"{pattern['name']}\" — unresolved; see Limitations.")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


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


def render(manifest_path: Path, song_dir: Path, replace: bool, title: str, dry_run: bool = False):
    size = manifest_path.stat().st_size
    if size > MAX_MANIFEST_BYTES:
        raise Invalid(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if raw.get("version") != 2:
        raise Invalid("renderer only supports version 2 analysis manifests")
    manifest = load_analysis_manifest(raw)

    tracking = song_dir / ".vp-music-tracking"
    source_score = None
    source_error = None
    source_path = (song_dir / manifest["source"]["asset_relative_path"]).resolve()
    song_root = song_dir.resolve()
    if song_root not in source_path.parents:
        source_error = "source path escapes the song directory"
    else:
        source_score = inspect_music_asset(source_path)
        if source_score["status"] != "valid":
            source_error = source_score.get("error") or "source validation failed"
            source_score = None
        elif source_score["checksum_sha256"] != manifest["source"]["asset_sha256"]:
            source_error = "source checksum no longer matches the analysis manifest"
            source_score = None
    pattern_docs = render_pattern_markdown(
        manifest, source_score=source_score, source_error=source_error)
    worksheet_doc = render_worksheet_markdown(manifest, title=title)

    targets = {}
    for track_name, text in pattern_docs.items():
        targets[tracking / "pattern" / f"{_slug(track_name)}.patterns.md"] = text
    if worksheet_doc is not None:
        targets[tracking / "draft" / "worksheet-draft.md"] = worksheet_doc

    if not targets:
        return {"status": "nothing-to-render",
               "reason": "manifest declares no patterns (and/or no sections for a worksheet)"}

    existing = [str(path) for path in targets if path.exists()]
    if existing and not replace:
        return {"status": "blocked", "conflicts": existing,
               "reason": "target(s) already exist; rerun with --replace for an explicit "
                         "migration/re-render"}

    if dry_run:
        return {"status": "dry-run", "would_write": [str(path) for path in targets]}

    written = []
    for path, text in targets.items():
        _write_atomic(path, text)
        written.append(str(path))
    return {"status": "written", "files": written}


def render_published(plan_path: Path, song_dir: Path, replace: bool, title: str,
                     dry_run: bool = False):
    """Render the published `worksheet.md` (status Final/Partial) from a compiled,
    schema-validated `performance-arrangement.json` (plan_version 2) -- never from the
    Draft analysis manifest. Mirrors `render()`'s no-clobber/atomic-write/dry-run
    conventions so the two CLI modes behave identically to callers/tests.
    """
    size = plan_path.stat().st_size
    if size > MAX_MANIFEST_BYTES:
        raise Invalid(f"plan exceeds {MAX_MANIFEST_BYTES} bytes")
    raw = json.loads(plan_path.read_text(encoding="utf-8"))
    if raw.get("version") != 2:
        raise Invalid("published-worksheet renderer only supports version 2 performance plans")
    plan = load_performance_plan(raw)

    target = song_dir / "worksheet.md"
    text = render_published_worksheet(plan, title=title)

    if target.exists() and not replace:
        return {"status": "blocked", "conflicts": [str(target)],
               "reason": "target already exists; rerun with --replace for an explicit "
                         "migration/re-render"}
    if dry_run:
        return {"status": "dry-run", "would_write": [str(target)]}
    _write_atomic(target, text)
    return {"status": "written", "files": [str(target)]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path,
                        help="v2 analysis manifest (arrangement.json); renders the Draft "
                             "pattern/worksheet views")
    parser.add_argument("--plan", type=Path,
                        help="compiled v2 performance plan (performance-arrangement.json); "
                             "renders the published worksheet.md instead of the Draft views")
    parser.add_argument("--song-dir", required=True, type=Path)
    parser.add_argument("--title", default="Untitled")
    parser.add_argument("--replace", action="store_true",
                        help="explicit staging/replace mode for migration; without it, "
                             "existing generated views are never overwritten")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be written without touching the filesystem")
    args = parser.parse_args(argv)
    if bool(args.manifest) == bool(args.plan):
        print(json.dumps({"status": "error",
                          "error": "exactly one of --manifest or --plan is required"}),
             file=sys.stderr)
        return 1
    try:
        if args.plan is not None:
            result = render_published(args.plan, args.song_dir, args.replace, args.title,
                                      args.dry_run)
        else:
            result = render(args.manifest, args.song_dir, args.replace, args.title, args.dry_run)
    except (Invalid, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in ("written", "dry-run", "nothing-to-render") else 1


if __name__ == "__main__":
    sys.exit(main())
