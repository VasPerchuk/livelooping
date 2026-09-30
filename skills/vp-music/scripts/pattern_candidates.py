"""Propose exact recurring instrumental phrases from validated MIDI/Guitar Pro notes."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from music_assets import inspect  # noqa: E402


def candidates(score, max_per_track=3):
    if score["status"] != "valid":
        raise ValueError(score.get("error") or "source is not validated")
    bars = score["bars"]
    if not bars:
        return []
    result = []
    for track in score["tracks"]:
        if re.search(r"\b(vocals?|voice|solo)\b", track["name"], re.I):
            continue
        notes = track.get("notes", [])
        grouped = defaultdict(list)
        for span in (1, 2, 4, 8):
            for first in range(0, len(bars) - span + 1):
                window = bars[first:first + span]
                if len({bar["meter"] for bar in window}) != 1:
                    continue
                start = window[0]["start_quarters"]
                end = window[-1]["start_quarters"] + window[-1]["length_quarters"]
                segment = [note for note in notes
                           if start <= note["start_quarters"] < end]
                if not segment or any(note["start_quarters"] + note["duration_quarters"] > end + 1e-5
                                      for note in segment):
                    continue
                signature = tuple(sorted(
                    (round(note["start_quarters"] - start, 4),
                     note["pitch"] if note.get("pitch") is not None else -1,
                     round(note["duration_quarters"], 4))
                    for note in segment
                ))
                grouped[(span, window[0]["meter"], signature)].append(first + 1)
        ranked = []
        for (span, meter, signature), positions in grouped.items():
            selected = []
            for position in positions:
                if not selected or position >= selected[-1] + span:
                    selected.append(position)
            if len(selected) < 2:
                continue
            ranked.append({
                "track": track["name"], "source_bar": selected[0],
                "length_bars": span, "meter": meter,
                "occurrence_bars": selected, "note_count": len(signature),
                "covered_bars": len(selected) * span,
            })
        ranked.sort(key=lambda item: (
            -item["covered_bars"], -len(item["occurrence_bars"]),
            item["length_bars"], item["source_bar"]
        ))
        chosen = []
        covered = set()
        for item in ranked:
            spans = {bar for start in item["occurrence_bars"]
                     for bar in range(start, start + item["length_bars"])}
            if len(spans & covered) / len(spans) >= 0.75:
                continue
            chosen.append(item)
            covered.update(spans)
            if len(chosen) >= max_per_track:
                break
        result.extend(chosen)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--max-per-track", type=int, default=3)
    args = parser.parse_args(argv)
    if not 1 <= args.max_per_track <= 8:
        parser.error("--max-per-track must be between 1 and 8")
    try:
        score = inspect(args.path)
        output = {"source": str(args.path), "status": score["status"],
                  "candidates": candidates(score, args.max_per_track),
                  "warning": "Exact repeats are hints, not confirmed musical sections or playable loops."}
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
