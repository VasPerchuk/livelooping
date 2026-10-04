"""One coordinated finalize-build entry point: analysis + resolution + performance
compilation, resolved worksheet rendering/checking, GP export/round-trip parity and
recoverable, transactional publication -- so public song artifacts can never silently
mix generations from unrelated builds.

This module does not re-derive any musical fact. It only orchestrates the existing,
already-tested building blocks, each imported and called directly (never
re-implemented):

  * `compile_performance.compile_performance_arrangement` -- analysis + resolutions +
    performance/publication -> compiled performance plan (pure function, no I/O).
  * `render_views.render_published_worksheet` -- compiled plan -> published
    `worksheet.md` text (pure function, no I/O; raises for a Draft plan by design).
  * `write_arrangement.mjs --performance ...` -- compiled plan -> `arrangement.gp`,
    including its own internal staged export + round-trip verification.
  * `check_worksheet.check_published_against_plan` -- published worksheet vs. the
    compiled plan, cross-checked after staging.
  * `music_assets.inspect` -- an independent second look at the staged `.gp` file's
    bar/track counts against the compiled plan, for GP/plan parity.

What this module adds on top of those pieces:

  1. Preflight: strict path containment under `--song-dir`, size limits, schema
     validation, source-asset fingerprint drift detection, and a compile dry run
     so bad inputs are reported before any file is touched.
  2. A disposable staging directory (`.vp-music-tracking\\publish\\staging\\<generation>`)
     where every artifact is built and independently re-verified before publication.
  3. A journaled, ordered, four-file publish transaction (the compiled
     `performance-arrangement.json`, root `worksheet.md`, root `arrangement.gp`, and a
     deterministic bundle record `bundle.json`, written last as the commit marker) with
     per-file backups, so a failure at any point can be rolled back to the previous
     valid bundle. A filesystem rename is not inherently multi-file atomic; this
     journal-plus-backups scheme is the best available substitute, not a claim of true
     atomicity (see CONTRACT.md).
  4. Default no-clobber, `--replace` for an explicit, intentional overwrite, and
     idempotent success when an identical, undrifted bundle already exists.
  5. A `draft` publication status never writes to the public root or the tracking
     `arrangement/` folder at all: it is isolated under
     `.vp-music-tracking\\preview\\<generation>` with its own record, and a single
     directory rename is used as its commit step (no shared files are touched, so true
     atomicity *is* available there).
  6. Startup recovery: any leftover journal from an interrupted previous run is rolled
     back before a new build proceeds; stale staging/backup/preview-temp directories
     (all owned, specifically-named paths -- never a broad directory) are swept.

Usage:
  python finalize_build.py --song-dir <song> \\
      --analysis <song>\\.vp-music-tracking\\arrangement\\arrangement.json \\
      --resolutions <resolutions.json> --performance <performance.json> \\
      --title "Artist - Song"

  python finalize_build.py --song-dir <song> --recover-only

Add `--replace` for an explicit intentional republish/migration, and `--draft` when
`--performance`'s `status` is `"draft"` (required together; a Draft build is a
non-publishable preview and never touches public `worksheet.md`/`arrangement.gp`).
"""

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent / "scripts"
for _p in (HERE, SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from v2_schema import Invalid, canonical_sha256, load_analysis_manifest  # noqa: E402
from compile_performance import compile_performance_arrangement  # noqa: E402
from render_views import render_published_worksheet  # noqa: E402
from music_assets import inspect as inspect_gp  # noqa: E402
from check_worksheet import check_published_against_plan  # noqa: E402

WRITER = HERE / "write_arrangement.mjs"

MAX_BYTES = 1_000_000
BUNDLE_VERSION = 1
JOURNAL_VERSION = 1
TRACKING_DIRNAME = ".vp-music-tracking"

# Fixed, documented four-file commit order; the bundle record is always last so it
# only ever describes a complete, already-written set of the other three files.
_TARGET_NAMES = ("performance_arrangement", "worksheet", "arrangement_gp", "bundle")


class FinalizeError(Invalid):
    """Any preflight, build, verification, or publication failure. Never raised after
    the bundle record (the commit marker) has been successfully written."""


class InjectedFailure(RuntimeError):
    """Raised only when `--inject-failure-after` (test-only) names the step just
    completed; simulates a crash at that exact point for recovery testing."""


# --------------------------------------------------------------------------- utils --

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_atomic(path: Path, data):
    """Stage-then-rename write, matching the convention already used by
    `compile_performance.py`/`render_views.py`. Accepts `str` or `bytes`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        if mode == "wb":
            with open(fd, "wb") as handle:
                handle.write(data)
        else:
            with open(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(data)
        Path(tmp_name).replace(path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _replace_file_atomic(src: Path, dst: Path):
    """Copy `src`'s bytes into `dst` via the same stage-then-rename pattern (never a
    direct in-place overwrite), so a crash mid-copy can never leave `dst` truncated.
    """
    _write_atomic(dst, src.read_bytes())


def _require_contained(song_dir: Path, candidate: Path, label: str) -> Path:
    """Strict containment: `candidate` must resolve to a real path inside `song_dir`.
    Rejects `..`-style traversal and a symlink that resolves outside the song tree.
    """
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise FinalizeError(f"{label}: {candidate}: {exc}") from exc
    try:
        resolved.relative_to(song_dir)
    except ValueError:
        raise FinalizeError(f"{label}: {candidate} resolves outside song directory {song_dir} "
                             "(path containment violation)") from None
    return resolved


def _load_json_limited(path: Path, label: str):
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise FinalizeError(f"{label}: {path} exceeds {MAX_BYTES} bytes")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FinalizeError(f"{label}: {path}: invalid JSON: {exc}") from exc


def _generation_id(analysis_sha, resolutions_sha, performance_sha, title) -> str:
    combined = f"{analysis_sha}:{resolutions_sha}:{performance_sha}:{title}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


def _rel(song_dir: Path, path: Path) -> str:
    return path.relative_to(song_dir).as_posix()


# ------------------------------------------------------------------------ layout ----

class Layout:
    def __init__(self, song_dir: Path):
        self.song_dir = song_dir
        self.tracking = song_dir / TRACKING_DIRNAME
        self.plan_target = self.tracking / "arrangement" / "performance-arrangement.json"
        self.worksheet_target = song_dir / "worksheet.md"
        self.gp_target = song_dir / "arrangement.gp"
        self.publish_dir = self.tracking / "publish"
        self.staging_root = self.publish_dir / "staging"
        self.backups_root = self.publish_dir / "backups"
        self.journal_path = self.publish_dir / "journal.json"
        self.bundle_path = self.publish_dir / "bundle.json"
        self.preview_root = self.tracking / "preview"

    def targets(self):
        """`(name, target_path)` in fixed commit order."""
        return (
            ("performance_arrangement", self.plan_target),
            ("worksheet", self.worksheet_target),
            ("arrangement_gp", self.gp_target),
            ("bundle", self.bundle_path),
        )


# --------------------------------------------------------------------- recovery -----

def recover(layout: Layout) -> dict:
    """Idempotent startup recovery: roll back an interrupted journal (if any), then
    sweep every other transient path this module ever creates. Every path removed is
    one of this module's own, specifically named staging/backup/preview-temp
    directories -- never a broad or user-owned directory.
    """
    result = {"rolled_back_generation": None, "swept": []}
    if layout.journal_path.exists():
        journal = json.loads(layout.journal_path.read_text(encoding="utf-8"))
        _rollback_journal(layout, journal)
        result["rolled_back_generation"] = journal["generation"]

    for root in (layout.staging_root, layout.backups_root):
        if root.exists():
            for child in root.iterdir():
                shutil.rmtree(child, ignore_errors=True)
                result["swept"].append(str(child))

    if layout.preview_root.exists():
        for child in layout.preview_root.iterdir():
            if child.name.startswith(".") and child.name.endswith(".tmp"):
                shutil.rmtree(child, ignore_errors=True)
                result["swept"].append(str(child))
    return result


def _rollback_journal(layout: Layout, journal: dict):
    generation = journal["generation"]
    backups_dir = layout.backups_root / generation
    for entry in reversed(journal["targets"]):
        target = layout.song_dir / entry["target"]
        _require_contained(layout.song_dir, target.parent, "journal target parent")
        if entry["had_backup"]:
            backup_path = backups_dir / f"{entry['name']}.bak"
            if backup_path.exists():
                os.replace(backup_path, target)
        else:
            if target.exists():
                target.unlink()
    layout.journal_path.unlink(missing_ok=True)
    shutil.rmtree(backups_dir, ignore_errors=True)
    shutil.rmtree(layout.staging_root / generation, ignore_errors=True)


# ---------------------------------------------------------------------- bundle ------

def _compute_bundle(layout: Layout, generation, compiled, analysis_sha, resolutions_sha,
                    performance_sha, source_asset_sha, staged_files, exported_tracks):
    files = {}
    for name, _target in layout.targets():
        if name == "bundle":
            continue
        staged = staged_files.get(name)
        if staged is None:
            continue
        files[name] = {"path": _rel(layout.song_dir, _target_for(layout, name)),
                       "sha256": _sha256_file(staged)}
    return {
        "bundle_version": BUNDLE_VERSION,
        "generation": generation,
        "status": compiled["publication"]["status"],
        "publication": {
            "status": compiled["publication"]["status"],
            "limitations": compiled["publication"].get("limitations", []),
            "consent": compiled["publication"].get("consent"),
        },
        "fingerprints": {
            "analysis_sha256": analysis_sha,
            "resolutions_sha256": resolutions_sha,
            "performance_input_sha256": performance_sha,
            "source_asset_sha256": source_asset_sha,
        },
        "exported_tracks": exported_tracks,
        "files": files,
    }


def _target_for(layout: Layout, name: str) -> Path:
    return dict(layout.targets())[name]


def _existing_bundle_state(layout: Layout):
    """Returns `(bundle_dict_or_None, drifted: bool)`. `drifted` is True when a bundle
    record exists but at least one of its recorded files is missing or its checksum no
    longer matches the actual on-disk file -- i.e. public files may already mix
    generations or have been hand-edited.
    """
    if not layout.bundle_path.exists():
        return None, False
    try:
        bundle = json.loads(layout.bundle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, True
    for name, info in bundle.get("files", {}).items():
        target = layout.song_dir / info["path"]
        if not target.exists() or _sha256_file(target) != info["sha256"]:
            return bundle, True
    return bundle, False


def _any_targets_exist(layout: Layout) -> list:
    return [str(t) for _n, t in layout.targets() if t.exists()]


# --------------------------------------------------------------------- verify -------

def verify_bundle(song_dir: Path) -> list:
    """Standalone bundle verifier (requirement 5): confirms the current bundle record
    is complete and every recorded checksum matches the actual published file, then
    additionally cross-checks the published worksheet against the compiled plan via
    the existing `check_worksheet.check_published_against_plan`. Returns a list of
    error strings (empty means the bundle verifies cleanly). Never used to verify a
    Draft preview, which is not a publication.
    """
    layout = Layout(Path(song_dir).resolve(strict=True))
    if not layout.bundle_path.exists():
        return [f"no bundle record at {layout.bundle_path}"]
    bundle, drifted = _existing_bundle_state(layout)
    errors = []
    if drifted:
        errors.append("bundle record is drifted: one or more published files are missing or do "
                      "not match the bundle's recorded SHA-256 (public files may mix generations)")
    if layout.plan_target.exists():
        errors.extend(check_published_against_plan(layout.song_dir, layout.plan_target))
    else:
        errors.append(f"missing {layout.plan_target}")
    exported_tracks = bundle.get("exported_tracks") if isinstance(bundle, dict) else None
    if exported_tracks is not None and layout.gp_target.exists():
        try:
            actual_names = [track["name"] for track in inspect_gp(layout.gp_target)["tracks"]]
            expected_names = [track["gp_track_name"] for track in exported_tracks]
            if actual_names != expected_names:
                errors.append("arrangement.gp track names/order disagree with bundle "
                              "exported_tracks projection")
            plan = json.loads(layout.plan_target.read_text(encoding="utf-8"))
            logical_ids = {
                track["id"]
                for instrument in plan.get("instruments", [])
                for track in instrument.get("output_tracks", [])
            }
            projected_ids = [track.get("output_track_id") for track in exported_tracks]
            if (len(projected_ids) != len(set(projected_ids)) or
                    any(track_id not in logical_ids for track_id in projected_ids)):
                errors.append("bundle exported_tracks contains duplicate or unknown logical "
                              "output track ids")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"could not verify bundle exported_tracks projection: {exc}")
    return errors


# ----------------------------------------------------------------- staged build -----

def _maybe_inject(point: str, inject_after):
    if inject_after is not None and point == inject_after:
        raise InjectedFailure(f"injected failure after step {point!r}")


def _run_writer(performance_path, song_dir, output_path, analysis_path, resolutions_path,
                worksheet_path):
    args = ["node", str(WRITER), "--performance", str(performance_path),
            "--song-dir", str(song_dir), "--output", str(output_path),
            "--analysis", str(analysis_path), "--resolutions", str(resolutions_path),
            "--worksheet", str(worksheet_path)]
    result = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0:
        raise FinalizeError(f"write_arrangement.mjs failed: {result.stderr.strip()}")
    try:
        receipt = json.loads(result.stdout.splitlines()[-1])
    except json.JSONDecodeError as exc:
        raise FinalizeError("write_arrangement.mjs did not return a valid export receipt") from exc
    exported_tracks = receipt.get("exported_tracks")
    if receipt.get("status") != "written" or not isinstance(exported_tracks, list):
        raise FinalizeError("write_arrangement.mjs returned an invalid export receipt")
    return exported_tracks


def _require_confirmed_section_transitions(compiled, raw_performance):
    if raw_performance.get("status") == "draft":
        return
    performance = raw_performance.get("performance") or {}
    entries = performance.get("section_transitions") or []
    by_section = {entry.get("section_id") for entry in entries if isinstance(entry, dict)}
    required = {
        instance["section_id"]
        for instance in compiled.get("section_instances", [])
        if instance.get("action") == "retain"
    }
    missing = sorted(required - by_section)
    if missing:
        raise FinalizeError(
            "confirmed performance actions are missing for section(s): " + ", ".join(missing))


def _stage_publication(layout: Layout, staging_dir: Path, raw_analysis, raw_resolutions,
                       raw_performance, analysis_path, resolutions_path, title,
                       inject_after=None):
    compiled, errors = compile_performance_arrangement(raw_analysis, raw_resolutions,
                                                        raw_performance)
    if errors:
        raise FinalizeError("compile errors: " + "; ".join(errors))

    plan_staged = staging_dir / "performance-arrangement.json"
    _write_atomic(plan_staged, json.dumps(compiled, indent=2, ensure_ascii=False) + "\n")
    _maybe_inject("staged-performance-arrangement", inject_after)

    staged_files = {"performance_arrangement": plan_staged}

    worksheet_staged = None
    gp_staged = None
    if compiled["publication"]["status"] != "draft":
        worksheet_text = render_published_worksheet(compiled, title=title)
        worksheet_staged = staging_dir / "worksheet.md"
        _write_atomic(worksheet_staged, worksheet_text)
        _maybe_inject("staged-worksheet", inject_after)
        staged_files["worksheet"] = worksheet_staged

        gp_staged = staging_dir / "arrangement.gp"
        # `--song-dir` is only used to resolve source-asset-relative paths (read-only);
        # the real song directory (not the staging directory) is where those source
        # assets actually live.
        exported_tracks = _run_writer(
            plan_staged, layout.song_dir, gp_staged, analysis_path, resolutions_path,
            worksheet_staged)
        _maybe_inject("staged-arrangement-gp", inject_after)
        staged_files["arrangement_gp"] = gp_staged

        # --- staged verification: GP/worksheet/plan parity (requirement 2/3) -------
        worksheet_errors = check_published_against_plan(staging_dir, plan_staged)
        if worksheet_errors:
            raise FinalizeError("staged worksheet disagrees with compiled plan: " +
                                "; ".join(worksheet_errors))
        actual = inspect_gp(gp_staged)
        expected_bars = len(compiled["output_timeline"])
        logical_tracks = sum(len(i["output_tracks"]) for i in compiled["instruments"])
        if actual["bar_count"] != expected_bars:
            raise FinalizeError(f"staged arrangement.gp bar_count {actual['bar_count']} "
                                f"disagrees with plan output_timeline length {expected_bars}")
        if actual["track_count"] != len(exported_tracks):
            raise FinalizeError(f"staged arrangement.gp track_count {actual['track_count']} "
                                f"disagrees with export receipt {len(exported_tracks)}")
        projected_ids = [track.get("output_track_id") for track in exported_tracks]
        logical_ids = {
            track["id"]
            for instrument in compiled["instruments"]
            for track in instrument["output_tracks"]
        }
        if (len(projected_ids) != len(set(projected_ids)) or
                any(track_id not in logical_ids for track_id in projected_ids)):
            raise FinalizeError("export receipt contains duplicate or unknown logical output "
                                "track ids")
        if not 1 <= actual["track_count"] <= logical_tracks:
            raise FinalizeError(f"staged arrangement.gp track_count {actual['track_count']} "
                                f"is outside the pruned export range 1..{logical_tracks}")
        _maybe_inject("staged-verified", inject_after)

    return compiled, staged_files, exported_tracks if gp_staged is not None else []


# --------------------------------------------------------------------- publish ------

def _publish_transaction(layout: Layout, generation, staged_files, bundle, inject_after=None):
    layout.publish_dir.mkdir(parents=True, exist_ok=True)
    backups_dir = layout.backups_root / generation
    backups_dir.mkdir(parents=True, exist_ok=True)

    staging_dir = layout.staging_root / generation
    bundle_staged = staging_dir / "bundle.json"
    _write_atomic(bundle_staged, json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")
    staged_files = dict(staged_files, bundle=bundle_staged)

    journal_targets = []
    for name, target in layout.targets():
        had_backup = target.exists()
        if had_backup:
            shutil.copy2(target, backups_dir / f"{name}.bak")
        journal_targets.append({"name": name, "target": _rel(layout.song_dir, target),
                                "had_backup": had_backup})
    journal = {"journal_version": JOURNAL_VERSION, "generation": generation,
              "targets": journal_targets}
    _write_atomic(layout.journal_path, json.dumps(journal, indent=2))
    _maybe_inject("journal-written", inject_after)

    for name, target in layout.targets():
        _replace_file_atomic(staged_files[name], target)
        _maybe_inject(f"replaced-{name}", inject_after)

    layout.journal_path.unlink()
    shutil.rmtree(backups_dir, ignore_errors=True)
    shutil.rmtree(staging_dir, ignore_errors=True)


def _publish_draft(layout: Layout, generation, compiled, bundle, replace, inject_after=None):
    preview_dir = layout.preview_root / generation
    if preview_dir.exists():
        existing_marker = preview_dir / "bundle.json"
        if existing_marker.exists():
            existing = json.loads(existing_marker.read_text(encoding="utf-8"))
            plan_path = preview_dir / "performance-arrangement.json"
            if (existing.get("generation") == generation and plan_path.exists() and
                    _sha256_file(plan_path) == existing["files"]["performance_arrangement"]["sha256"]):
                return {"status": "idempotent", "generation": generation,
                       "bundle": str(existing_marker)}
        if not replace:
            return {"status": "blocked", "conflicts": [str(preview_dir)],
                   "reason": "a preview already exists for this generation id; rerun with "
                             "--replace for an explicit rebuild"}
        shutil.rmtree(preview_dir)

    tmp_dir = layout.preview_root / f".{generation}.tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)
    _write_atomic(tmp_dir / "performance-arrangement.json",
                 json.dumps(compiled, indent=2, ensure_ascii=False) + "\n")
    _write_atomic(tmp_dir / "bundle.json", json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")
    _maybe_inject("draft-staged", inject_after)
    # Single directory rename: the only shared/public state here is the preview_dir's
    # own name, so this commit step genuinely is atomic (no journal needed).
    os.replace(tmp_dir, preview_dir)
    return {"status": "written", "generation": generation,
           "files": [str(preview_dir / "performance-arrangement.json"),
                     str(preview_dir / "bundle.json")]}


# ----------------------------------------------------------------------- main -------

def finalize_build(song_dir, analysis_path, resolutions_path, performance_path, title="Untitled",
                   replace=False, draft=False, inject_failure_after=None):
    song_dir = Path(song_dir).resolve(strict=True)
    if not song_dir.is_dir():
        raise FinalizeError(f"--song-dir {song_dir} is not a directory")
    layout = Layout(song_dir)

    recover(layout)

    analysis_path = _require_contained(song_dir, Path(analysis_path), "--analysis")
    resolutions_path = _require_contained(song_dir, Path(resolutions_path), "--resolutions")
    performance_path = _require_contained(song_dir, Path(performance_path), "--performance")

    raw_analysis = _load_json_limited(analysis_path, "--analysis")
    raw_resolutions = _load_json_limited(resolutions_path, "--resolutions")
    raw_performance = _load_json_limited(performance_path, "--performance")

    if raw_analysis.get("version") != 2:
        raise FinalizeError("finalize_build only supports version 2 analysis manifests")
    load_analysis_manifest(copy.deepcopy(raw_analysis))  # schema preflight; raises Invalid

    if not isinstance(raw_resolutions, (list, dict)):
        raise FinalizeError("--resolutions: expected a JSON array or object")
    if not isinstance(raw_performance, dict):
        raise FinalizeError("--performance: expected a JSON object")

    status = raw_performance.get("status")
    is_draft = status == "draft"
    if is_draft and not draft:
        raise FinalizeError("--performance status is \"draft\": pass --draft to build an "
                            "isolated, non-publishable preview (never published to the public "
                            "root)")
    if draft and not is_draft:
        raise FinalizeError("--draft was given but --performance status is not \"draft\"")

    source = raw_analysis.get("source") or {}
    source_asset_sha = source.get("asset_sha256")
    if source.get("asset_relative_path"):
        asset_path = _require_contained(song_dir, song_dir / source["asset_relative_path"],
                                        "source.asset_relative_path")
        if _sha256_file(asset_path) != source_asset_sha:
            raise FinalizeError(f"source asset drift: {asset_path} no longer matches "
                                "source.asset_sha256 recorded in the analysis manifest")

    analysis_sha = canonical_sha256(raw_analysis)
    resolutions_sha = canonical_sha256(raw_resolutions)
    performance_sha = canonical_sha256(raw_performance)
    generation = _generation_id(analysis_sha, resolutions_sha, performance_sha, title)

    # Resolve omissions and retained material before publication checks. Analysis
    # preflight reports every offered choice for review, but discarded alternatives
    # must not block a valid retained arrangement. The writer later materializes and
    # validates only the retained source references in the isolated staging area.
    _preflight_compiled, preflight_errors = compile_performance_arrangement(
        copy.deepcopy(raw_analysis), copy.deepcopy(raw_resolutions), copy.deepcopy(raw_performance))
    if preflight_errors:
        return {"status": "error", "errors": preflight_errors}
    _require_confirmed_section_transitions(_preflight_compiled, raw_performance)

    if is_draft:
        # Draft bundles only ever carry the compiled plan (never worksheet/gp, which
        # both reject a Draft publication by design); the checksum is computed from a
        # throwaway staged copy under this generation's own owned temp subdirectory.
        compiled = _preflight_compiled
        tmp_dir = layout.staging_root / f".draft-check-{generation}"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)
        tmp_plan = tmp_dir / "performance-arrangement.json"
        _write_atomic(tmp_plan, json.dumps(compiled, indent=2, ensure_ascii=False) + "\n")
        bundle = {
            "bundle_version": BUNDLE_VERSION, "generation": generation, "status": "draft",
            "publication": {"status": "draft", "limitations": compiled["publication"].get(
                "limitations", []), "consent": compiled["publication"].get("consent")},
            "fingerprints": {"analysis_sha256": analysis_sha,
                            "resolutions_sha256": resolutions_sha,
                            "performance_input_sha256": performance_sha,
                            "source_asset_sha256": source_asset_sha},
            "files": {"performance_arrangement": {"path": "performance-arrangement.json",
                                                  "sha256": _sha256_file(tmp_plan)}},
        }
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return _publish_draft(layout, generation, compiled, bundle, replace, inject_failure_after)

    # --- publish (final/partial) path ---------------------------------------------
    existing_bundle, drifted = _existing_bundle_state(layout)
    if existing_bundle is not None:
        if drifted and not replace:
            return {"status": "blocked", "reason": "existing bundle is drifted (public files "
                                                    "changed since last publish or do not match "
                                                    "the recorded bundle); rerun with --replace "
                                                    "to force a clean republish",
                   "conflicts": _any_targets_exist(layout)}
        if not drifted and existing_bundle["generation"] == generation:
            return {"status": "idempotent", "generation": generation,
                   "bundle": str(layout.bundle_path)}
        if not drifted and not replace:
            return {"status": "blocked",
                   "reason": "a different, up-to-date bundle already exists for this song "
                             "(generation changed); rerun with --replace for an explicit "
                             "republish", "conflicts": _any_targets_exist(layout)}
    elif _any_targets_exist(layout) and not replace:
        return {"status": "blocked",
               "reason": "existing worksheet.md/arrangement.gp/performance-arrangement.json "
                         "with no bundle record; rerun with --replace for an explicit "
                         "migration/republish", "conflicts": _any_targets_exist(layout)}

    staging_dir = layout.staging_root / generation
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    compiled, staged_files, exported_tracks = _stage_publication(
        layout, staging_dir, raw_analysis, raw_resolutions, raw_performance, analysis_path,
        resolutions_path, title, inject_failure_after)

    # Revalidate input hashes immediately before publish (requirement 2): guards
    # against the source files changing between preflight and this point.
    if (canonical_sha256(_load_json_limited(analysis_path, "--analysis")) != analysis_sha or
            canonical_sha256(_load_json_limited(resolutions_path, "--resolutions")) != resolutions_sha or
            canonical_sha256(_load_json_limited(performance_path, "--performance")) != performance_sha):
        raise FinalizeError("inputs changed on disk during the build; rerun finalize_build "
                            "from the unmodified files")
    _maybe_inject("revalidated", inject_failure_after)

    bundle = _compute_bundle(layout, generation, compiled, analysis_sha, resolutions_sha,
                             performance_sha, source_asset_sha, staged_files, exported_tracks)

    _publish_transaction(layout, generation, staged_files, bundle, inject_failure_after)

    return {"status": "written", "generation": generation,
           "files": [str(t) for _n, t in layout.targets()]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--song-dir", required=True, type=Path)
    parser.add_argument("--analysis", type=Path)
    parser.add_argument("--resolutions", type=Path)
    parser.add_argument("--performance", type=Path)
    parser.add_argument("--title", default="Untitled")
    parser.add_argument("--replace", action="store_true",
                        help="explicit, intentional overwrite of an existing bundle/preview")
    parser.add_argument("--draft", action="store_true",
                        help="required together with a --performance status of \"draft\"; "
                             "builds an isolated, non-publishable preview bundle")
    parser.add_argument("--recover-only", action="store_true",
                        help="run only the startup journal-rollback/sweep pass and exit")
    parser.add_argument("--verify", action="store_true",
                        help="verify the current published bundle instead of building one")
    parser.add_argument("--inject-failure-after", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        if args.recover_only:
            layout = Layout(Path(args.song_dir).resolve(strict=True))
            result = {"status": "recovered", **recover(layout)}
        elif args.verify:
            errors = verify_bundle(args.song_dir)
            result = {"status": "valid" if not errors else "invalid", "errors": errors}
        else:
            for name in ("analysis", "resolutions", "performance"):
                if getattr(args, name) is None:
                    raise FinalizeError(f"--{name} is required unless --recover-only/--verify")
            result = finalize_build(args.song_dir, args.analysis, args.resolutions,
                                    args.performance, title=args.title, replace=args.replace,
                                    draft=args.draft, inject_failure_after=args.inject_failure_after)
    except InjectedFailure as exc:
        print(json.dumps({"status": "injected-failure", "error": str(exc)}), file=sys.stderr)
        return 1
    except (Invalid, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    ok_statuses = ("written", "idempotent", "recovered", "valid")
    return 0 if result.get("status") in ok_statuses else 1


if __name__ == "__main__":
    sys.exit(main())
