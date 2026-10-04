"""Check a song worksheet's section timings and references to pattern files."""

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from v2_schema import Invalid, load_performance_plan  # noqa: E402
from render_views import (  # noqa: E402
    _output_track_index,
    _section_action_reasons,
    _seconds_for_bars,
    _timeline_runs,
)

def check_published_against_plan(song_dir, plan_path):
    """Validate a *published* `worksheet.md` (rendered by
    `render_views.py --plan ...`) against its compiled, authoritative
    `performance-arrangement.json` (plan_version 2). The plan is always
    authoritative: every mismatch is reported as the Markdown disagreeing
    with the plan, never the reverse.
    """
    errors = []
    try:
        raw = json.loads(Path(plan_path).read_text(encoding="utf-8"))
        if raw.get("version") != 2:
            return [f"--plan {plan_path}: only version 2 performance plans are supported"]
        plan = load_performance_plan(raw)
    except (OSError, ValueError, Invalid) as exc:
        return [f"--plan {plan_path}: {exc}"]

    worksheet_path = Path(song_dir) / "worksheet.md"
    try:
        sheet = worksheet_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{worksheet_path}: {exc}"]

    publication = plan["publication"]
    status = publication["status"]

    # --- status / publication -------------------------------------------------
    statuses = re.findall(r"(?mi)^\*\*Status:\s*(Final|Partial|Draft)\*\*\s*$", sheet)
    if len(statuses) != 1:
        errors.append("published worksheet must have exactly one Final/Partial/Draft status "
                     "line")
    elif statuses[0].lower() != status:
        errors.append(f"worksheet status {statuses[0]!r} disagrees with plan publication.status "
                     f"{status!r}")
    if status == "partial":
        limitations_line = re.search(r"(?mi)^\*\*Limitations:\*\*\s*(\S.*?)\s*$", sheet)
        expected_limitations = "; ".join(publication.get("limitations") or [])
        if not limitations_line:
            errors.append("Partial worksheet is missing a Limitations line (plan requires "
                         "limitations to be visible to the reader)")
        elif limitations_line[1] != expected_limitations:
            errors.append(f"worksheet Limitations {limitations_line[1]!r} disagrees with plan "
                         f"publication.limitations {expected_limitations!r}")
        consent = publication.get("consent")
        if consent:
            consent_line = re.search(r"(?mi)^\*\*Consent:\*\*\s*(\S.*?)\s*$", sheet)
            if not consent_line or consent_line[1] != consent:
                errors.append("worksheet is missing (or disagrees with) the plan's recorded "
                             "publication.consent")
    elif status == "final":
        if re.search(r"(?mi)^\*\*Limitations:\*\*", sheet):
            errors.append("Final worksheet must not carry a Limitations line (no consented "
                         "gaps remain)")

    # --- tempo / meter / total bars & seconds -----------------------------------
    tempo_bpm = plan.get("tempo_bpm")
    meter = plan.get("meter")
    tempo_match = re.search(r"\*\*Tempo:\*\*\s*quarter\s*=\s*(\d+(?:\.\d+)?)\s*BPM", sheet)
    if not tempo_match or float(tempo_match[1]) != float(tempo_bpm):
        errors.append(f"worksheet tempo disagrees with plan tempo_bpm ({tempo_bpm})")
    meter_match = re.search(r"\*\*Meter:\*\*\s*(\d+/\d+)", sheet)
    if not meter_match or meter_match[1] != meter:
        errors.append(f"worksheet meter disagrees with plan meter ({meter})")

    output_timeline = plan.get("output_timeline", []) or []
    total_bars = len(output_timeline)
    total_seconds = _seconds_for_bars(total_bars, tempo_bpm, meter) if tempo_bpm and meter else None
    total_match = re.search(r"\*\*Total:\*\*\s*(\d+)\s+output\s+bars?\s*\(~(\d+(?:\.\d+)?)\s*"
                           r"seconds\)", sheet)
    if not total_match:
        errors.append("worksheet is missing a Total output bars/seconds line")
    else:
        if int(total_match[1]) != total_bars:
            errors.append(f"worksheet total bars {total_match[1]} disagrees with plan "
                         f"output_timeline length ({total_bars})")
        if total_seconds is not None and float(total_match[2]) != total_seconds:
            errors.append(f"worksheet total seconds {total_match[2]} disagrees with plan-derived "
                         f"~{total_seconds}s")

    # --- structure: section order/ids/bar ranges/counts/times, active tracks ---
    tracks_by_id = {t["id"]: t for t in plan.get("tracks", []) or []}
    patterns_by_id = {p["id"]: p for p in plan.get("patterns", []) or []}
    instances_by_id = {si["id"]: si for si in plan.get("section_instances", []) or []}
    output_track_index = _output_track_index(plan)
    all_placements = plan.get("placements", []) or []

    runs = _timeline_runs(output_timeline)
    section_instances = plan.get("section_instances", []) or []
    orig_index = {si["id"]: i for i, si in enumerate(section_instances)}
    retained_ids = [si["id"] for si in section_instances if si["action"] == "retain"]
    reasons = _section_action_reasons(plan)
    section_transitions = {
        entry["section_id"]: entry
        for entry in plan.get("performance", {}).get("section_transitions", [])
    }

    structure_match = re.search(r"(?ms)^## Structure\s*\n(.*?)(?=^## |\Z)", sheet)
    found_sections = re.findall(r"(?ms)^###\s+\d+\.\s+.+?\n(?:.*?\n)*?(?=^### \d+\.|\Z)",
                                structure_match[1]) if structure_match else []
    if not structure_match:
        errors.append("worksheet has no ## Structure section")
    elif len(found_sections) != len(runs):
        errors.append(f"worksheet has {len(found_sections)} Structure section(s) but plan's "
                     f"compiled output_timeline declares {len(runs)}")

    cumulative = 0.0
    for position, (instance_id, start, end) in enumerate(runs, start=1):
        instance = instances_by_id[instance_id]
        section_text = found_sections[position - 1] if position <= len(found_sections) else ""
        bars = end - start + 1
        seconds = _seconds_for_bars(bars, tempo_bpm, meter)
        cumulative += seconds
        header_pattern = (rf"###\s+{position}\.\s+{re.escape(instance['name'])}\s+"
                         rf"\(occurrence {instance['occurrence_index']}\)\s*(?:-|—)\s*output "
                         rf"bars {start + 1}-{end + 1},\s*{bars}\s*bars?\s*\(~{re.escape(str(seconds))}s,"
                         rf"\s*cumulative ~{re.escape(str(round(cumulative, 1)))}s\)")
        if not re.search(header_pattern, section_text):
            errors.append(f"worksheet section #{position} ({instance['name']!r}, occurrence "
                         f"{instance['occurrence_index']}) heading disagrees with plan "
                         f"(expected output bars {start + 1}-{end + 1}, {bars} bars, "
                         f"~{seconds}s, cumulative ~{round(cumulative, 1)}s)")

        placements = [
            placement for placement in all_placements
            if start <= placement["output_bar_range"][0] and
            placement["output_bar_range"][1] <= end
        ]
        for placement in placements:
            track_id = placement["output_track_id"]
            if f"`{track_id}`" not in section_text:
                errors.append(f"worksheet is missing active output track id `{track_id}` for "
                             f"section {instance['name']!r} (occurrence "
                             f"{instance['occurrence_index']})")
            if placement.get("optional"):
                segment = re.search(rf"(?m)^- \w+ `{re.escape(track_id)}`.*$", section_text)
                if not segment or "[OPTIONAL]" not in segment[0]:
                    errors.append(f"worksheet is missing the [OPTIONAL] marker for retained "
                                 f"optional placement on track `{track_id}`")
            if placement["mode"] == "live":
                placement_start, placement_end = placement["output_bar_range"]
                expected_cue = f"manual cue: bars {placement_start + 1}-{placement_end + 1}"
                segment = re.search(
                    rf"(?m)^- LIVE `{re.escape(track_id)}`.*"
                    rf"{re.escape(expected_cue)}.*$", section_text)
                if not segment or expected_cue not in segment[0]:
                    errors.append(f"worksheet is missing LIVE disclosure ({expected_cue}) for "
                                  f"track `{track_id}`")

        # --- transitions: never invented; confirmed reason or REVIEW REQUIRED ---
        confirmed_transition = section_transitions.get(instance["section_id"])
        if confirmed_transition:
            prefix = "INFEASIBLE — " if not confirmed_transition["feasible"] else ""
            expected = f"Transition: {prefix}{confirmed_transition['action']}"
            if expected not in section_text:
                errors.append(f"worksheet section #{position} is missing confirmed transition "
                              f"action {confirmed_transition['action']!r}")
            if (not confirmed_transition["feasible"] and
                    f"**Transition warning:** INFEASIBLE — {confirmed_transition['warning']}"
                    not in section_text):
                errors.append(f"worksheet section #{position} is missing its confirmed "
                              "infeasibility warning")
        elif position == 1:
            if not re.search(r"Transition:\s*opening section\.", section_text):
                errors.append(f"worksheet section #{position} is missing the opening-section "
                             "Transition line")
        else:
            prev_id = retained_ids[retained_ids.index(instance_id) - 1]
            gap_instances = section_instances[orig_index[prev_id] + 1:orig_index[instance_id]]
            removed = [si for si in gap_instances if si["action"] == "delete"]
            if not removed:
                if not re.search(r"Transition:\s*contiguous with previous section", section_text):
                    errors.append(f"worksheet section #{position} ({instance['name']!r}) is "
                                 "missing its contiguous Transition line")
            else:
                matched = [reasons[si["section_id"]] for si in removed
                          if si["section_id"] in reasons]
                if matched:
                    for reason in matched:
                        if reason not in section_text:
                            errors.append(f"worksheet is missing the confirmed transition "
                                         f"resolution {reason!r} for the new adjacency before "
                                         f"{instance['name']!r}")
                elif status == "final":
                    errors.append(f"plan has an unresolved new-adjacency transition before "
                                 f"{instance['name']!r} but publication.status is 'final' -- "
                                 "this must never happen in an authoritative plan")
                else:
                    if "REVIEW REQUIRED" not in section_text:
                        errors.append(f"worksheet is missing a REVIEW REQUIRED flag for the new "
                                     f"adjacency the plan records before {instance['name']!r}")

    if status == "final" and "REVIEW REQUIRED" in sheet:
        errors.append("Final worksheet must not contain an unresolved REVIEW REQUIRED transition")

    # --- deleted sections / omitted patterns: a separate Omissions section -----
    removal_ledger = plan.get("removal_ledger", []) or []
    omissions_match = re.search(r"(?ms)^## Omissions and Edits\s*\n(.*?)(?=^## |\Z)", sheet)
    omissions_body = omissions_match[1] if omissions_match else ""
    if removal_ledger or plan["disclosure"].get("omitted_patterns"):
        if not omissions_match:
            errors.append("worksheet is missing the ## Omissions and Edits section")
    for entry in removal_ledger:
        if f"`{entry['section_instance_id']}`" not in omissions_body:
            errors.append(f"worksheet Omissions section is missing deleted section instance "
                         f"`{entry['section_instance_id']}` ({entry['name']!r})")
        if structure_match and f"`{entry['section_instance_id']}`" in structure_match[1]:
            errors.append(f"deleted section instance `{entry['section_instance_id']}` must not "
                         "appear in the Structure timeline")
    for pattern_id in plan["disclosure"].get("omitted_patterns", []) or []:
        if f"`{pattern_id}`" not in omissions_body:
            errors.append(f"worksheet Omissions section is missing omitted pattern "
                         f"`{pattern_id}` ({patterns_by_id[pattern_id]['name']!r})")

    # --- unresolved disclosure (Partial only) -----------------------------------
    unresolved_patterns = plan["disclosure"].get("unresolved_patterns", []) or []
    if unresolved_patterns:
        unresolved_match = re.search(r"(?ms)^## Unresolved \(Partial\)\s*\n(.*?)(?=^## |\Z)", sheet)
        if not unresolved_match:
            errors.append("worksheet is missing the ## Unresolved (Partial) section")
        else:
            for pattern_id in unresolved_patterns:
                if f"`{pattern_id}`" not in unresolved_match[1]:
                    errors.append(f"worksheet Unresolved section is missing pattern "
                                 f"`{pattern_id}`")
    elif re.search(r"(?m)^## Unresolved \(Partial\)\s*$", sheet):
        errors.append("worksheet declares an Unresolved section but the plan discloses no "
                     "unresolved patterns")

    return errors

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--song-dir", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=None,
                        help="compiled v2 performance-arrangement.json (plan_version 2); "
                             "validates the published worksheet.md against the plan")
    parser.add_argument("--verify-bundle", action="store_true",
                        help="verify the finalize_build.py-published bundle record under "
                             "--song-dir (checksums of worksheet.md/arrangement.gp/"
                             "performance-arrangement.json plus the plan/worksheet "
                             "cross-check); mutually exclusive with --plan")
    args = parser.parse_args(argv)
    if (args.plan is not None) == args.verify_bundle:
        print("ERROR: provide exactly one of --plan or --verify-bundle",
             file=sys.stderr)
        return 1
    try:
        if args.verify_bundle:
            # Deferred import: `finalize_build` imports `check_published_against_plan`
            # from this module, so this stays a function-local import to avoid a
            # circular import at module load time.
            from finalize_build import verify_bundle  # noqa: E402
            errors = verify_bundle(args.song_dir)
        else:
            errors = check_published_against_plan(args.song_dir, args.plan)
    except OSError as exc:
        errors = [str(exc)]
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Worksheet structure, timing and pattern references verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
