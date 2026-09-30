"""Check a song worksheet's section timings and references to pattern files."""

import argparse
from pathlib import Path
import re
import sys


def check(song_dir, require_final=False):
    folder = Path(song_dir)
    tracking = folder / ".vp-music-tracking"
    new_layout = tracking.is_dir()
    draft = tracking / "draft" / "worksheet-draft.md"
    sheet_path = folder / "worksheet.md"
    if new_layout and not require_final:
        sheet_path = draft
    sheet = sheet_path.read_text(encoding="utf-8")
    errors = []
    if new_layout and require_final and not draft.is_file():
        errors.append("published worksheet has no tracking draft")
    if new_layout and (folder / "research.md").exists():
        errors.append("both legacy and tracking research locations exist; choose one handoff")
    if require_final and not re.search(r"\*\*Status:\s*Final\*\*", sheet, re.I):
        errors.append("worksheet is not marked Final")
    if not re.search(r"\*\*Status:\s*(Draft|Final)\*\*", sheet, re.I):
        errors.append("worksheet has no Draft/Final status")
    names = {}
    pattern_dir = tracking / "pattern" if new_layout else folder
    if new_layout and list(folder.glob("*.patterns.md")):
        errors.append("both legacy and tracking pattern files exist; choose one handoff")
    for path in pattern_dir.glob("*.patterns.md"):
        text = path.read_text(encoding="utf-8")
        for name in re.findall(r"(?m)^## Pattern:\s*(.+?)\s*$", text):
            if name in names:
                errors.append(f"duplicate pattern name: {name}")
            names[name] = path.name
    legend = re.search(r"(?ms)^## Patterns(?:\s*\((\d+)(?:\s+\w+)*\s+total\))?\s*\n(.*?)(?=^## |\Z)", sheet)
    if not legend:
        errors.append("worksheet has no Patterns section")
        listed = []
    else:
        listed = re.findall(r"(?m)^-\s+\*\*(.+?)\*\*", legend[2])
        if len(set(listed)) != len(listed):
            errors.append("duplicate names in worksheet pattern legend")
        if legend[1] is None or int(legend[1]) != len(listed):
            errors.append("pattern count does not match legend")
        for name in listed:
            if name not in names:
                errors.append(f"undefined pattern in legend: {name}")
    structure = re.search(r"(?ms)^## Structure\s*\n(.*?)(?=^## |\Z)", sheet)
    if not structure:
        errors.append("worksheet has no Structure section")
        return errors
    sections = re.findall(r"(?ms)^###\s+(.+?)\s*\n(.*?)(?=^### |\Z)", structure[1])
    if not sections:
        errors.append("worksheet has no song sections")
    tempo = re.search(r"\*\*Tempo:\*\*\s*(?:(dotted-quarter|quarter)(?:\s*[=:])?\s*)?(\d+(?:\.\d+)?)\s*BPM", sheet, re.I)
    meter = re.search(r"\*\*(?:Meter|Time signature):\*\*\s*(\d+)/(\d+)", sheet, re.I)
    if require_final and (not tempo or not meter):
        errors.append("final worksheet needs explicit BPM and time signature")
    for heading, body in sections:
        label = heading.strip()
        if new_layout:
            transition = re.search(r"(?mi)^Transition:\s*(.+?)\s*$", body)
            if not transition:
                errors.append(f"section lacks Transition line: {label}")
            elif re.search(r"\bINFEASIBLE\b", transition[1], re.I) and not re.search(
                r"(?mi)^\*\*Transition warning:\*\*\s*\S+", sheet
            ) and require_final:
                errors.append(f"infeasible transition lacks final warning: {label}")
        timing = re.search(r"(?:-|—)\s*(\d+)\s+bars?\s*\(\s*~?\s*(\d+(?:\.\d+)?)\s*(?:seconds|s)\b", label, re.I)
        if not timing:
            errors.append(f"section lacks bar count and approximate seconds: {label}")
        elif tempo and meter:
            bars = int(timing[1])
            claimed = float(timing[2])
            num, den = map(int, meter.groups())
            bpm = float(tempo[2])
            if den <= 0 or bpm <= 0:
                errors.append("invalid meter or tempo")
            else:
                quarters_per_bar = num * 4 / den
                if tempo[1] and tempo[1].lower() == "dotted-quarter":
                    quarters_per_minute = bpm * 1.5
                else:
                    quarters_per_minute = bpm
                expected = round(bars * quarters_per_bar * 60 / quarters_per_minute)
                if abs(expected - claimed) > 1:
                    errors.append(f"{label}: ~{claimed}s disagrees with ~{expected}s at stated tempo")
        active = re.search(r"(?m)^(?:\*\*)?Patterns:(?:\*\*)?\s*(.*?)\s*$", body)
        if not active:
            errors.append(f"section has no Patterns line: {label}")
            continue
        for entry in active[1].split(","):
            name = re.sub(r"\s*\(.*?\)", "", entry).strip().rstrip(".")
            if name.lower() != "none" and name not in listed:
                errors.append(f"{label}: pattern not in legend: {name}")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--song-dir", type=Path, required=True)
    parser.add_argument("--require-final", action="store_true")
    args = parser.parse_args(argv)
    try:
        errors = check(args.song_dir, args.require_final)
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
