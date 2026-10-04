"""Reconcile vp-music phase state from files and hashes, never from progress prose."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from finalize_build import verify_bundle
from music_assets import inspect as inspect_music_asset
from render_views import render_pattern_markdown, render_worksheet_markdown, _slug
from v2_schema import Invalid, load_analysis_manifest

PHASES = ("research", "analysis", "finalization")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(song_dir: Path):
    tracking = song_dir / ".vp-music-tracking"
    candidates = [
        song_dir / "chords.md", song_dir / "worksheet.md", song_dir / "arrangement.gp",
        tracking / "research" / "research.md",
        tracking / "arrangement" / "arrangement.json",
        tracking / "arrangement" / "performance-arrangement.json",
        tracking / "draft" / "worksheet-draft.md",
        tracking / "publish" / "bundle.json",
        *(tracking / "pattern").glob("*.patterns.md"),
    ]
    rows = []
    names = {}
    for path in sorted((p for p in candidates if p.is_file()),
                       key=lambda p: str(p.relative_to(song_dir)).lower()):
        relative = path.relative_to(song_dir).as_posix()
        rows.append({"path": relative, "sha256": sha256(path), "bytes": path.stat().st_size})
        names.setdefault(relative.lower(), []).append(relative)
    collisions = [items for items in names.values() if len(items) > 1]
    return rows, collisions


def _substantive_markdown(path):
    if not path or not path.is_file():
        return False
    content = path.read_text(encoding="utf-8").strip()
    meaningful = [
        line.strip() for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return bool(meaningful)


def status(song_dir: Path):
    files, collisions = inventory(song_dir)
    paths = {row["path"]: row for row in files}
    analysis_path = ".vp-music-tracking/arrangement/arrangement.json"
    plan_path = ".vp-music-tracking/arrangement/performance-arrangement.json"
    bundle_path = ".vp-music-tracking/publish/bundle.json"
    bundle_errors = verify_bundle(song_dir) if bundle_path in paths else ["bundle absent"]
    validation_errors = []
    research_path = ".vp-music-tracking/research/research.md"
    completed = None
    if (_substantive_markdown(song_dir / research_path)
            and _substantive_markdown(song_dir / "chords.md")):
        completed = "research"
    if analysis_path in paths:
        try:
            raw_analysis = json.loads((song_dir / analysis_path).read_text(encoding="utf-8"))
            analysis = load_analysis_manifest(raw_analysis)
            source_path = (song_dir / analysis["source"]["asset_relative_path"]).resolve()
            if song_dir.resolve() not in source_path.parents:
                raise Invalid("source path escapes song directory")
            source_score = inspect_music_asset(source_path)
            if source_score["status"] != "valid":
                raise Invalid("source asset is not valid: " +
                              (source_score.get("error") or source_score["status"]))
            if source_score["checksum_sha256"] != analysis["source"]["asset_sha256"]:
                raise Invalid("source asset checksum differs from analysis manifest")
            expected_patterns = render_pattern_markdown(analysis, source_score=source_score)
            expected_paths = {
                f".vp-music-tracking/pattern/{_slug(key)}.patterns.md": value
                for key, value in expected_patterns.items()
            }
            expected_draft = render_worksheet_markdown(analysis)
            if expected_draft is not None:
                expected_paths[".vp-music-tracking/draft/worksheet-draft.md"] = expected_draft
            if bundle_errors:
                actual_view_paths = {
                    relative for relative in paths
                    if relative.startswith(".vp-music-tracking/pattern/") or
                    relative == ".vp-music-tracking/draft/worksheet-draft.md"
                }
                if actual_view_paths != set(expected_paths):
                    raise Invalid("generated pattern/draft view inventory is missing or stale")
                for relative, expected in expected_paths.items():
                    if (song_dir / relative).read_text(encoding="utf-8") != expected:
                        raise Invalid(f"generated view is stale: {relative}")
            if completed == "research":
                completed = "analysis"
            else:
                validation_errors.append(
                    "analysis manifest exists but verified research.md and chords.md are incomplete")
        except (OSError, UnicodeError, json.JSONDecodeError, Invalid) as exc:
            validation_errors.append(f"analysis manifest invalid: {exc}")
    if (completed == "analysis" and not bundle_errors and plan_path in paths and
            "worksheet.md" in paths and "arrangement.gp" in paths):
        completed = "finalization"
    return {
        "status": "conflict" if (collisions or validation_errors or
                                  (bundle_path in paths and bundle_errors)) else "ok",
        "latest_verified_phase": completed,
        "validation_errors": validation_errors,
        "case_insensitive_collisions": collisions,
        "bundle": {"present": bundle_path in paths, "valid": not bundle_errors,
                   "errors": bundle_errors},
        "files": files,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--song-dir", required=True, type=Path)
    parser.add_argument("--phase", choices=PHASES)
    args = parser.parse_args(argv)
    song_dir = args.song_dir.resolve()
    if not song_dir.is_dir():
        parser.error("--song-dir must be an existing directory")
    result = status(song_dir)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
