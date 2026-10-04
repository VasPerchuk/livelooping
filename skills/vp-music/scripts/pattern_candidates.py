"""Role-aware exact/normalized/near-loop live-loop candidate detection.

Consumes validated written bars plus the `performed_order` expansion
(`tools\\performed_order.py`) so repeated frequency is counted across
*performed* passes (repeats/alternate endings/directions), while every
coordinate reported to a human stays in source-stable *written*-bar terms,
with occurrence/pass/ending context appended only when it matters.

Three candidate passes, in descending confidence order:

  * ``exact``      - identical (onset, pitch, duration) signature; the
                      highest-confidence pass, extended from the previous
                      exact-only implementation.
  * ``normalized`` - a declared, recorded transform (currently pitch
                      translation/transposition, plus rhythmic-grid
                      quantization where float precision requires it)
                      makes two otherwise-different windows equal.
  * ``near``       - bounded bar-by-bar comparison anchored on exact/
                      normalized per-bar fingerprints; identifies a cluster
                      such as "3 of 4 bars match, bar offset 3 diverges"
                      without an all-pairs whole-song fuzzy search.

Track role (percussion/bass/rhythm/lead-vocal/solo-lead/ostinato-lead/
unknown) is a *prior* that nudges scoring and the recommended disposition;
it never hard-excludes a track the way the previous vocal/solo name regex
did. Normalized and near matches are never automatically treated as an
approved loop and this module never mutates source note data; `UNRESOLVED`
candidates always carry a structured `decision` for a human to resolve.
"""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from music_assets import inspect  # noqa: E402

# Four- and eight-bar phrases are the useful default for live looping. One- and
# two-bar loops remain discoverable, but they must win on musical evidence rather
# than receiving the same preference as a complete phrase.
PREFERRED_SPANS = (4, 8)
SUPPORTED_SPANS = (1, 2, 4, 8)
DEFAULT_MAX_PER_TRACK = 3
DEFAULT_MAX_BARS_SOFT = 8
DEFAULT_MIN_OCCURRENCES = 2
OVERLAP_SUPPRESSION_RATIO = 0.75
RHYTHM_GRID_QUARTERS = 0.25  # sixteenth-note grid; tolerates float precision noise
NEAR_MIN_SPAN = 2
# Exact-tier scanning also covers spans beyond the preferred set, up to this
# multiple of the (possibly overridden) soft bar maximum, so a longer
# repeated passage is still found and routed to review instead of never
# being detected at all.
LONG_SPAN_SCAN_MULTIPLIER = 2

# Role priors: positive favors LOOP, negative favors LIVE, neutral abstains.
ROLE_PRIORS = {
    "percussion": 2.0,
    "bass": 1.5,
    "rhythm": 1.5,
    "ostinato_lead": 0.5,
    "unknown": 0.0,
    "solo_lead": -1.5,
    "lead_vocal": -1.5,
}

_PERCUSSION_RE = re.compile(r"\b(drums?|percussion|kit)\b", re.I)
_BASS_RE = re.compile(r"\bbass\b", re.I)
_RHYTHM_RE = re.compile(r"\b(rhythm|chords?|comp(?:ing)?|pad|accompaniment)\b", re.I)
_LEAD_RE = re.compile(r"\b(vocals?|voice|lead|solo)\b", re.I)
_VOCAL_RE = re.compile(r"\b(vocals?|voice)\b", re.I)


def _slug(text):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "track").lower()).strip("-")
    return slug or "track"


def _window_segment(notes, start, end):
    return [note for note in notes if start <= note["start_quarters"] < end]


def _sounding_segment(notes, start, end):
    return [
        note for note in notes
        if note["start_quarters"] < end and
        note["start_quarters"] + note["duration_quarters"] > start
    ]


def _window_signature(segment, start, normalize=False, grid=None):
    # A single note carries no pitch "shape": normalizing it to a fixed relative
    # pitch of 0 would make it trivially equal to every other single note,
    # which is not real transposition evidence. Only normalize windows with at
    # least two notes, where a genuine interval contour exists.
    if normalize and len(segment) < 2:
        normalize = False
    pitches = [note["pitch"] for note in segment if note.get("pitch") is not None]
    base = min(pitches) if normalize and pitches else 0

    def quantize(value):
        if grid:
            return round(round(value / grid) * grid, 4)
        return round(value, 4)

    return tuple(sorted(
        (quantize(note["start_quarters"] - start),
         (note["pitch"] - base) if note.get("pitch") is not None else -1,
         quantize(note["duration_quarters"]))
        for note in segment
    ))


def _bar_signatures(bars, notes):
    """Per-written-bar exact fingerprint, keyed by written bar number; None = silent."""
    sigs = {}
    for bar in bars:
        start = bar["start_quarters"]
        end = start + bar["length_quarters"]
        segment = _window_segment(notes, start, end)
        sigs[bar["number"]] = _window_signature(segment, start) if segment else None
    return sigs


def _chord_density(notes):
    if not notes:
        return 0.0
    by_onset = defaultdict(int)
    for note in notes:
        by_onset[round(note["start_quarters"], 4)] += 1
    chorded = sum(count for count in by_onset.values() if count > 1)
    return chorded / len(notes)


def _repetition_density(bar_sigs):
    present = [sig for sig in bar_sigs.values() if sig is not None]
    if len(present) < 2:
        return 0.0
    counts = Counter(present)
    repeated = sum(count for count in counts.values() if count > 1)
    return repeated / len(present)


def classify_role(track, bars):
    """Return {role, role_confidence, role_prior, evidence}. A prior, not a verdict."""
    name = track.get("name") or ""
    notes = track.get("notes", [])
    evidence = []

    is_percussion = any(staff.get("percussion") for staff in track.get("staves", []) or ())
    if is_percussion:
        evidence.append("staff metadata marks this track as percussion")
    if _PERCUSSION_RE.search(name):
        is_percussion = True
        evidence.append(f"track name '{name}' matches a percussion hint")

    is_bass = bool(_BASS_RE.search(name))
    if is_bass:
        evidence.append(f"track name '{name}' matches a bass hint")

    is_rhythm = bool(_RHYTHM_RE.search(name))
    if is_rhythm:
        evidence.append(f"track name '{name}' matches a rhythm/accompaniment hint")

    is_lead_name = bool(_LEAD_RE.search(name))
    if is_lead_name:
        evidence.append(f"track name '{name}' matches a vocal/solo/lead hint")

    pitches = [note["pitch"] for note in notes if note.get("pitch") is not None]
    if pitches:
        low, high, avg = min(pitches), max(pitches), sum(pitches) / len(pitches)
        evidence.append(f"pitch range {low}-{high} MIDI (avg {avg:.1f})")
        if avg < 48 and not is_bass:
            is_bass = True
            evidence.append("average pitch below MIDI 48 suggests a bass register")

    chord_density = _chord_density(notes)
    evidence.append(f"chord density {chord_density:.2f} (fraction of notes sharing an onset)")
    bar_sigs = _bar_signatures(bars, notes)
    repetition_density = _repetition_density(bar_sigs)
    evidence.append(f"bar repetition density {repetition_density:.2f}")
    onset_density = len(notes) / max(1, len(bars))
    evidence.append(f"onset density {onset_density:.2f} notes/bar")

    if is_percussion:
        role, confidence = "percussion", "high"
    elif is_bass:
        role, confidence = "bass", "medium" if pitches else "low"
    elif is_rhythm or (chord_density >= 0.3 and not is_lead_name):
        role, confidence = "rhythm", "medium"
        if not is_rhythm:
            evidence.append("polyphonic chord density without a lead/vocal hint suggests accompaniment")
    elif is_lead_name:
        if repetition_density >= 0.5:
            role, confidence = "ostinato_lead", "medium"
            evidence.append("high repetition density overrides the usual lead/vocal live prior "
                             "(repetitive simultaneous leads/ostinati are a neutral-to-positive "
                             "loop prior)")
        else:
            role = "lead_vocal" if _VOCAL_RE.search(name) else "solo_lead"
            confidence = "medium"
    else:
        role, confidence = "unknown", "low"

    return {"role": role, "role_confidence": confidence,
            "role_prior": ROLE_PRIORS[role], "evidence": evidence}


def _performed_counts(bars, performed_order):
    """Return played order and written-bar -> 1-based performed positions."""
    played = None
    if performed_order and performed_order.get("status") == "ok" and performed_order.get("bars"):
        played = performed_order["bars"]
    if not played:
        played = [bar["number"] for bar in bars]  # fallback: assume one linear pass
    order_index = defaultdict(list)
    for position, number in enumerate(played):
        order_index[number].append(position + 1)
    return played, order_index


def _performed_window_positions(played, bar_numbers):
    length = len(bar_numbers)
    return [
        index + 1 for index in range(len(played) - length + 1)
        if played[index:index + length] == bar_numbers
    ]


def _track_windows(bars, notes, span):
    """Contiguous same-meter, non-silent, non-boundary-crossing candidate windows."""
    windows = []
    for first in range(0, len(bars) - span + 1):
        window = bars[first:first + span]
        if len({bar["meter"] for bar in window}) != 1:
            continue  # never cross a meter boundary
        start = window[0]["start_quarters"]
        end = window[-1]["start_quarters"] + window[-1]["length_quarters"]
        segment = _window_segment(notes, start, end)
        if not segment:
            continue  # silence/rest is never a loop candidate
        if any(note["start_quarters"] + note["duration_quarters"] > end + 1e-5 for note in segment):
            continue  # a note crosses the window boundary
        windows.append({
            "first_index": first, "start_bar_number": window[0]["number"],
            "end_bar_number": window[-1]["number"], "meter": window[0]["meter"],
            "segment": segment, "start": start, "bar_numbers": [bar["number"] for bar in window],
        })
    return windows


def _select_non_overlapping(members, span):
    ordered = sorted(members, key=lambda window: window["first_index"])
    selected, last_end = [], -1
    for window in ordered:
        if window["first_index"] >= last_end:
            selected.append(window)
            last_end = window["first_index"] + span
    return selected


def _build_occurrences(members, span, played, bars_by_number, track_name):
    selected = _select_non_overlapping(members, span)
    occurrences = []
    for index, window in enumerate(selected):
        start_num, end_num = window["start_bar_number"], window["end_bar_number"]
        passes = _performed_window_positions(played, window["bar_numbers"])
        ending = bars_by_number[start_num].get("alternate_endings") or None
        coordinate = f"{track_name}, written bars {start_num}-{end_num}, occurrence {index + 1}"
        if ending:
            coordinate += f", ending {'/'.join(str(n) for n in ending)}"
        occurrences.append({
            "written_bars": [start_num, end_num],
            "occurrence_index": index + 1,
            "performed_passes": passes,
            "ending": ending,
            "coordinate": coordinate,
        })
    return occurrences


def _candidate_id(track_id, track_name, tier, span, start_bar_numbers):
    key = f"{track_id}|{tier}|{span}|{','.join(str(n) for n in start_bar_numbers)}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return f"{_slug(track_name)}-{tier}-{span}b-{digest}"


_TIER_BONUS = {"exact": 3.0, "normalized": 1.0, "near": 0.5}


def _score(tier, coverage_bars, frequency, span, max_bars_soft, near_uncertainty):
    phrase_bonus = {1: 0.0, 2: 1.0, 4: 4.0, 8: 5.0}.get(span, 0.0)
    length_penalty = max(0, span - max_bars_soft) * 0.5
    # Raw occurrence count strongly over-rewards a one-bar cell contained inside
    # every longer phrase. Cap that signal and let coherent coverage/phrase length
    # decide between candidates that explain the same musical material.
    recurrence_bonus = min(frequency, 4) * 0.5
    return (coverage_bars * 0.5 + recurrence_bonus + _TIER_BONUS[tier] +
            phrase_bonus - length_penalty - near_uncertainty)


def _disposition(tier, span, max_bars_soft, role_prior, frequency):
    if tier == "near":
        return "UNRESOLVED", ["a bounded near-loop match needs a human preserve/canonicalize decision"]
    if tier == "normalized":
        return "UNRESOLVED", ["a normalized (transposed/grid-quantized) match needs a human "
                              "confirmation before it becomes an approved loop"]
    reasons = []
    if span > max_bars_soft:
        reasons.append(f"span {span} bars exceeds the soft maximum of {max_bars_soft}; "
                       "flagged for review rather than automatically approved")
        return "UNRESOLVED", reasons
    if role_prior < 0 and frequency < 3:
        reasons.append("role prior favors live performance and performed frequency does not "
                       "yet override it")
        return "LIVE", reasons
    reasons.append(f"exact repeat within the soft span/occurrence thresholds (frequency {frequency})")
    return "LOOP", reasons


def _decision_for(candidate):
    tier = candidate["match_tier"]
    cid = candidate["id"]
    coords = [occurrence["coordinate"] for occurrence in candidate["occurrences"]]
    if tier == "near":
        return [
            {
                "id": f"decision-{cid}",
                "question": "Preserve the per-occurrence variation or use one canonical loop "
                            "for this near-match?",
                "coordinates": coords,
                "options": ["preserve_variants", "use_canonical", "play_live"],
            },
            {
                "id": f"decision-{cid}-followup",
                "depends_on": f"decision-{cid}",
                "applies_when": "use_canonical",
                "question": "Keep the canonical loop in rotation, or omit this material instead?",
                "coordinates": coords,
                "options": ["keep_loop", "omit"],
            },
        ]
    if tier == "normalized":
        return [{
            "id": f"decision-{cid}",
            "question": "Treat the detected normalization (e.g. transposition) as one canonical "
                        "loop, or keep each occurrence distinct?",
            "coordinates": coords,
            "options": ["normalize_canonical", "preserve_variants", "play_live"],
        }]
    # exact tier, flagged only for exceeding the soft length maximum
    return [{
        "id": f"decision-{cid}",
        "question": f"This exact repeat spans {candidate['span_bars']} bars, beyond the default "
                    "soft maximum - accept it as one loop or perform it live?",
        "coordinates": coords,
        "options": ["accept_long_loop", "play_live", "omit"],
    }]


def _build_candidate(track_id, track_name, role_info, tier, span, meter, occurrences, note_count,
                      max_bars_soft, normalization=None, divergent_bar_offsets=None):
    frequency = sum(len(occurrence["performed_passes"]) for occurrence in occurrences)
    coverage_bars = len(occurrences) * span
    near_uncertainty = 0.15 * len(divergent_bar_offsets) if divergent_bar_offsets else 0.0
    score = _score(tier, coverage_bars, frequency, span,
                   max_bars_soft, near_uncertainty)
    disposition, reasons = _disposition(tier, span, max_bars_soft, role_info["role_prior"], frequency)
    candidate = {
        "id": _candidate_id(track_id, track_name, tier, span,
                            [occurrence["written_bars"][0] for occurrence in occurrences]),
        "track_id": track_id,
        "track": track_name,
        "role": role_info["role"], "role_confidence": role_info["role_confidence"],
        "role_prior": role_info["role_prior"],
        "match_tier": tier,
        "source_bar": occurrences[0]["written_bars"][0],
        "length_bars": span, "span_bars": span, "meter": meter,
        "occurrence_bars": [occurrence["written_bars"][0] for occurrence in occurrences],
        "occurrences": occurrences,
        "note_count": note_count,
        "covered_bars": coverage_bars,
        "frequency": frequency,
        "score": round(score, 4),
        "score_components": {
            "coverage_bars": coverage_bars, "frequency": frequency,
            "role_prior": role_info["role_prior"], "tier_bonus": _TIER_BONUS[tier],
            "phrase_span_bonus": {1: 0.0, 2: 1.0, 4: 4.0, 8: 5.0}.get(span, 0.0),
            "recurrence_bonus": min(frequency, 4) * 0.5,
            "length_penalty": max(0, span - max_bars_soft) * 0.5,
            "near_uncertainty": near_uncertainty,
        },
        "disposition": disposition,
        "reasons": reasons,
        "normalization": normalization,
        "near_match": ({"matching_bars": span - len(divergent_bar_offsets), "total_bars": span,
                        "divergent_bar_offsets": divergent_bar_offsets}
                       if divergent_bar_offsets else None),
        "decision": None,
    }
    if disposition == "UNRESOLVED":
        candidate["decision"] = _decision_for(candidate)
    candidate["_score"] = score
    return candidate


def _describe_normalization(members):
    bases = []
    for window in members:
        pitches = [note["pitch"] for note in window["segment"] if note.get("pitch") is not None]
        bases.append(min(pitches) if pitches else None)
    anchor = bases[0]
    offsets = [None if anchor is None or base is None else base - anchor for base in bases]
    return {"type": "pitch_translation", "grid_quarters": RHYTHM_GRID_QUARTERS,
            "semitone_offsets_relative_to_first_occurrence": offsets}


def _bar_fingerprints(bars, bars_by_number, notes, bar_numbers):
    """(exact, normalized) per-bar signature pairs for the given written bar numbers."""
    fingerprints = []
    for number in bar_numbers:
        bar = bars_by_number[number]
        start = bar["start_quarters"]
        end = start + bar["length_quarters"]
        segment = _window_segment(notes, start, end)
        if not segment:
            fingerprints.append((None, None))
            continue
        exact = _window_signature(segment, start)
        normalized = _window_signature(segment, start, normalize=True, grid=RHYTHM_GRID_QUARTERS)
        fingerprints.append((exact, normalized))
    return fingerprints


def _near_matches(track_id, track_name, role_info, span, windows, notes, bars_by_number,
                  played, max_bars_soft, min_occurrences):
    if len(windows) < 2 or span < NEAR_MIN_SPAN:
        return []
    ordered = sorted(windows, key=lambda window: window["first_index"])
    anchor = ordered[0]
    anchor_bar_fp = _bar_fingerprints(None, bars_by_number, notes, anchor["bar_numbers"])

    groups = defaultdict(list)
    for window in ordered[1:]:
        window_bar_fp = _bar_fingerprints(None, bars_by_number, notes, window["bar_numbers"])
        divergent = []
        matching_sounding = 0
        for offset in range(span):
            a_exact, a_norm = anchor_bar_fp[offset]
            b_exact, b_norm = window_bar_fp[offset]
            if a_exact == b_exact or (a_exact is not None and b_exact is not None and
                                      a_norm == b_norm):
                if a_exact is not None and b_exact is not None:
                    matching_sounding += 1
                continue
            divergent.append(offset)
        # Shared rests describe form but do not establish a recurring musical
        # phrase. Require at least half of a near-match (and at least two bars)
        # to contain matching sounding material.
        minimum_sounding = max(2, (span + 1) // 2)
        if len(divergent) == 1 and matching_sounding >= minimum_sounding:
            groups[tuple(divergent)].append(window)

    candidates_found = []
    for divergent_offsets, members in groups.items():
        occurrence_windows = [anchor] + members
        if len(occurrence_windows) < min_occurrences:
            continue
        occurrences = _build_occurrences(occurrence_windows, span, played,
                                         bars_by_number, track_name)
        if sum(len(item["performed_passes"]) for item in occurrences) < min_occurrences:
            continue
        candidates_found.append(_build_candidate(
            track_id, track_name, role_info, "near", span, anchor["meter"], occurrences,
            note_count=len(anchor["segment"]), max_bars_soft=max_bars_soft,
            divergent_bar_offsets=list(divergent_offsets)))
    return candidates_found


def _select(raw_candidates, max_per_track, overlap_ratio):
    ordered = sorted(raw_candidates,
                     key=lambda c: (-c["_score"], c["span_bars"], c["source_bar"]))
    chosen, covered = [], set()
    for candidate in ordered:
        bars_covered = set()
        for occurrence in candidate["occurrences"]:
            start, end = occurrence["written_bars"]
            bars_covered.update(range(start, end + 1))
        mostly_overlaps = (bars_covered and
                           len(bars_covered & covered) / len(bars_covered) >= overlap_ratio)
        if mostly_overlaps:
            continue  # overlap suppression: mostly-redundant coverage is skipped
        chosen.append(candidate)
        covered |= bars_covered
        if len(chosen) >= max_per_track:
            break
    for candidate in chosen:
        candidate.pop("_score", None)
    return chosen


def _remaining_spans(bars, notes, covered_numbers):
    groups, current, current_silent = [], [], None
    for bar in bars:
        if bar["number"] in covered_numbers:
            if current:
                groups.append((current, current_silent))
                current, current_silent = [], None
            continue
        start = bar["start_quarters"]
        end = start + bar["length_quarters"]
        silent = not _sounding_segment(notes, start, end)
        if current and silent != current_silent:
            groups.append((current, current_silent))
            current = []
        current.append(bar)
        current_silent = silent
    if current:
        groups.append((current, current_silent))
    result = []
    for group, silent in groups:
        if silent:
            disposition, reason = "REST", "no notes in this span for this track"
        else:
            disposition, reason = (
                "LIVE",
                "no selected loop candidate covers this span; live performance is the "
                "conservative default")
        result.append({"written_bars": [group[0]["number"], group[-1]["number"]],
                       "disposition": disposition, "reason": reason})
    return result


def _span_scan_list(bars, max_bars_soft):
    scan_max = max(max(PREFERRED_SPANS), max_bars_soft) * LONG_SPAN_SCAN_MULTIPLIER
    spans = sorted(set(SUPPORTED_SPANS) | set(range(1, scan_max + 1)))
    return [span for span in spans if span <= len(bars)]


def candidates(score, max_per_track=DEFAULT_MAX_PER_TRACK, max_bars_soft=DEFAULT_MAX_BARS_SOFT,
              min_occurrences=DEFAULT_MIN_OCCURRENCES, overlap_ratio=OVERLAP_SUPPRESSION_RATIO):
    """Selected, role-aware exact/normalized/near loop candidates (list, backward compatible)."""
    if score["status"] != "valid":
        raise ValueError(score.get("error") or "source is not validated")
    bars = score["bars"]
    if not bars:
        return []
    bars_by_number = {bar["number"]: bar for bar in bars}
    played, _order_index = _performed_counts(bars, score.get("performed_order"))
    span_scan_list = _span_scan_list(bars, max_bars_soft)
    result = []
    for track_index, track in enumerate(score["tracks"]):
        notes = track.get("notes", [])
        track_name = track["name"]
        track_id = track.get("id") or f"track-{track_index}"
        role_info = classify_role(track, bars)
        raw = []
        for span in span_scan_list:
            windows = _track_windows(bars, notes, span)
            if not windows:
                continue
            exact_groups = defaultdict(list)
            for window in windows:
                exact_groups[_window_signature(window["segment"], window["start"])].append(window)
            handled = set()
            for signature, members in exact_groups.items():
                occurrences = _build_occurrences(members, span, played,
                                                 bars_by_number, track_name)
                if sum(len(item["performed_passes"]) for item in occurrences) < min_occurrences:
                    continue
                raw.append(_build_candidate(track_id, track_name, role_info, "exact", span,
                                            members[0]["meter"], occurrences,
                                            len(signature), max_bars_soft))
                for member in members:
                    handled.add(member["first_index"])
            remaining = [w for w in windows if w["first_index"] not in handled]
            if remaining and span in SUPPORTED_SPANS:
                norm_groups = defaultdict(list)
                for window in remaining:
                    sig = _window_signature(window["segment"], window["start"],
                                            normalize=True, grid=RHYTHM_GRID_QUARTERS)
                    norm_groups[sig].append(window)
                norm_handled = set()
                for signature, members in norm_groups.items():
                    exact_sigs = {_window_signature(m["segment"], m["start"]) for m in members}
                    if len(exact_sigs) < 2:
                        continue  # not an actual transform; would already be exact
                    occurrences = _build_occurrences(members, span, played,
                                                     bars_by_number, track_name)
                    if sum(len(item["performed_passes"]) for item in occurrences) < min_occurrences:
                        continue
                    raw.append(_build_candidate(
                        track_id, track_name, role_info, "normalized", span, members[0]["meter"],
                        occurrences, len(signature), max_bars_soft,
                        normalization=_describe_normalization(members)))
                    for member in members:
                        norm_handled.add(member["first_index"])
                remaining = [w for w in remaining if w["first_index"] not in norm_handled]
                if remaining:
                    raw.extend(_near_matches(track_id, track_name, role_info, span, remaining, notes,
                                             bars_by_number, played,
                                             max_bars_soft, min_occurrences))
        selected = _select(raw, max_per_track, overlap_ratio)
        result.extend(selected)
    return result


def analyze(score, max_per_track=DEFAULT_MAX_PER_TRACK, max_bars_soft=DEFAULT_MAX_BARS_SOFT,
           min_occurrences=DEFAULT_MIN_OCCURRENCES, overlap_ratio=OVERLAP_SUPPRESSION_RATIO):
    """Full candidate/role/decision/remaining-span report for one validated score."""
    cands = candidates(score, max_per_track=max_per_track, max_bars_soft=max_bars_soft,
                       min_occurrences=min_occurrences, overlap_ratio=overlap_ratio)
    bars = score["bars"]
    by_track = defaultdict(list)
    decisions = []
    for candidate in cands:
        by_track[candidate["track_id"]].append(candidate)
        if candidate["decision"]:
            decisions.extend(candidate["decision"])
    roles, remaining = {}, {}
    for track_index, track in enumerate(score["tracks"]):
        track_id = track.get("id") or f"track-{track_index}"
        role_info = classify_role(track, bars)
        roles[track_id] = {"name": track["name"], **{key: role_info[key] for key in
                           ("role", "role_confidence", "role_prior", "evidence")}}
        covered = set()
        for candidate in by_track.get(track_id, []):
            for occurrence in candidate["occurrences"]:
                start, end = occurrence["written_bars"]
                covered.update(range(start, end + 1))
        remaining[track_id] = {
            "name": track["name"],
            "spans": _remaining_spans(bars, track.get("notes", []), covered),
        }
    return {"candidates": cands, "roles": roles, "decisions": decisions, "remaining_spans": remaining}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--max-per-track", type=int, default=DEFAULT_MAX_PER_TRACK)
    parser.add_argument("--max-bars", type=int, default=DEFAULT_MAX_BARS_SOFT,
                        help="soft maximum loop span in bars before a long exact repeat "
                             "is flagged for review instead of auto-approved")
    parser.add_argument("--min-occurrences", type=int, default=DEFAULT_MIN_OCCURRENCES)
    parser.add_argument("--overlap-ratio", type=float, default=OVERLAP_SUPPRESSION_RATIO)
    args = parser.parse_args(argv)
    if not 1 <= args.max_per_track <= 8:
        parser.error("--max-per-track must be between 1 and 8")
    if not 1 <= args.max_bars <= 64:
        parser.error("--max-bars must be between 1 and 64")
    if not 2 <= args.min_occurrences <= 64:
        parser.error("--min-occurrences must be between 2 and 64")
    if not 0 < args.overlap_ratio <= 1:
        parser.error("--overlap-ratio must be between 0 (exclusive) and 1")
    try:
        score = inspect(args.path)
        analysis = analyze(score, max_per_track=args.max_per_track, max_bars_soft=args.max_bars,
                          min_occurrences=args.min_occurrences, overlap_ratio=args.overlap_ratio)
        output = {
            "source": str(args.path), "status": score["status"],
            "candidates": analysis["candidates"], "roles": analysis["roles"],
            "decisions": analysis["decisions"], "remaining_spans": analysis["remaining_spans"],
            "config": {"max_per_track": args.max_per_track, "max_bars_soft": args.max_bars,
                      "min_occurrences": args.min_occurrences, "overlap_ratio": args.overlap_ratio},
            "warning": "Exact/normalized/near matches are hints, not confirmed musical sections "
                      "or automatically approved loops; UNRESOLVED candidates require a human "
                      "decision before a Final export.",
        }
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
