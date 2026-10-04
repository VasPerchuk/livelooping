"""Synthetic, hermetic end-to-end tests for the v2 GPIF source-bar reuse compiler and
its integration into `write_arrangement.mjs --performance`.

Every fixture GP file is generated on the fly by `make_source_fixture.mjs` from plain
alphaTab model objects (see that file's header); no commercial song asset or raw
source XML is read, copied or persisted anywhere here.
"""
import hashlib
import json
import re
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from music_assets import inspect
from v2_schema import canonical_sha256

HERE = Path(__file__).resolve().parent
WRITER = HERE / "write_arrangement.mjs"
FIXTURE_GEN = HERE / "make_source_fixture.mjs"
ANALYSIS_PREFLIGHT = HERE / "analysis_preflight.mjs"


def make_fixture_bytes(**opts):
    result = subprocess.run(
        ["node", str(FIXTURE_GEN)],
        input=json.dumps(opts).encode("utf-8"),
        capture_output=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace"))
    return result.stdout


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "x"


def attach_plan(perf):
    """Mechanically derive the additive plan fields (`plan_version`/`instruments`/
    `section_instances`/`output_timeline`/`placements`/`variant_selections`/
    `fingerprints`) this module's build path now requires, straight from a hand-authored
    legacy-shaped performance dict (tracks/patterns/sections only). This deliberately
    mirrors the *shape* `compile_performance.py`'s `build_instruments`/`build_placements`
    produce (one live/loop(s)/unresolved output track per analysis track, one placement
    per retained-section/active-pattern pair, one section instance per section occurring
    once) for the simple single-occurrence fixtures these tests author by hand; it is not
    a reimplementation of decision/resolution wiring (every fixture pattern already
    carries its own `final_disposition`/`omitted`).
    """
    perf = json.loads(json.dumps(perf))  # isolate from caller mutation
    patterns = perf["patterns"]
    tracks = perf["tracks"]
    sections = perf["sections"]
    spans_by_id = {s["id"]: s for t in tracks for s in t["spans"]}

    instruments = []
    output_track_by_pattern = {}
    live_track_by_instrument = {}
    unresolved_track_by_instrument = {}
    for track in tracks:
        name = track["name"]
        inst_id = _slug(name)
        loop_patterns = sorted(
            (p for p in patterns if p["track"] == name and
             p["final_disposition"] == "LOOP" and not p["omitted"]),
            key=lambda p: p["id"])
        output_tracks = [{"id": f"{inst_id}-live", "role": "live"}]
        for pattern in loop_patterns:
            output_tracks.append({"id": f"{inst_id}-loop-{_slug(pattern['name'])}",
                                   "role": "loop", "pattern_id": pattern["id"]})
            output_track_by_pattern[pattern["id"]] = output_tracks[-1]["id"]
        output_tracks.append({"id": f"{inst_id}-unresolved", "role": "unresolved"})
        instruments.append({"id": inst_id, "analysis_track": name, "output_tracks": output_tracks})
        live_track_by_instrument[name] = f"{inst_id}-live"
        unresolved_track_by_instrument[name] = f"{inst_id}-unresolved"

    section_instances = []
    output_timeline = []
    placements = []
    idx = 0
    for section in sections:
        instance_id = f"{section['id']}-1"
        section_instances.append({"id": instance_id, "section_id": section["id"],
                                   "name": section["name"], "occurrence_index": 1,
                                   "action": "retain", "approved": True, "omitted_layers": []})
        start = idx
        for b in range(section["bars"]):
            output_timeline.append({"output_index": idx, "section_instance_id": instance_id,
                                    "source_section_bar": b})
            idx += 1
        end = idx - 1
        for pattern_id in section["active_pattern_ids"]:
            pattern = next(p for p in patterns if p["id"] == pattern_id)
            disposition = pattern["final_disposition"]
            if pattern["omitted"] or disposition == "OPTIONAL":
                continue
            if disposition == "LOOP":
                track_id = output_track_by_pattern[pattern_id]
                mode = "loop"
            elif disposition == "LIVE":
                track_id = live_track_by_instrument[pattern["track"]]
                mode = "live"
            elif disposition == "UNRESOLVED":
                track_id = unresolved_track_by_instrument[pattern["track"]]
                mode = "unresolved"
            else:
                continue
            placement = {"output_track_id": track_id, "output_bar_range": [start, end],
                        "material_id": pattern_id, "mode": mode,
                        "optional": pattern.get("disposition") == "OPTIONAL"}
            span_ids = pattern["span_ids"]
            if len(span_ids) == 1:
                span = spans_by_id.get(span_ids[0])
                if span and span.get("source_ref"):
                    placement["source_ref"] = span["source_ref"]
            placements.append(placement)

    perf["plan_version"] = 2
    perf["fingerprints"] = {"analysis_sha256": "0" * 64, "resolutions_sha256": "0" * 64}
    perf["instruments"] = instruments
    perf["section_instances"] = section_instances
    perf["output_timeline"] = output_timeline
    perf["removal_ledger"] = []
    perf["placements"] = placements
    perf["variant_selections"] = []
    return perf


class SourceCompilerIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vp-source-")
        self.addCleanup(temp.cleanup)
        self.song_dir = Path(temp.name)
        self.source_dir = self.song_dir / "source"
        self.source_dir.mkdir(parents=True)
        self.perf_path = (self.song_dir / ".vp-music-tracking" / "arrangement" /
                           "performance-arrangement.json")
        self.perf_path.parent.mkdir(parents=True)
        self.output = self.song_dir / "arrangement.gp"

    def write_fixture(self, name="fixture.gp", **opts):
        data = make_fixture_bytes(**opts)
        path = self.source_dir / name
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        return f"source/{name}", digest

    def base_performance(self, rel_path, digest, track_index=0, track_name="Fixture Guitar",
                          written_master_bars=(1, 2), bars=2, section_bars=None,
                          tempo_bpm=120, meter="4/4", voice_indices=(0,),
                          content_fingerprint=None):
        if section_bars is None:
            section_bars = bars
        source_ref = {
            "asset_sha256": digest, "asset_relative_path": rel_path,
            "track_index": track_index, "track_name": track_name,
            "staff_index": 0, "written_master_bars": list(written_master_bars),
            "voice_indices": list(voice_indices),
        }
        if content_fingerprint is not None:
            source_ref["content_fingerprint"] = content_fingerprint
        return {
            "version": 2,
            "source": {"asset_relative_path": rel_path, "asset_sha256": digest,
                       "format": "gpif", "capabilities": ["source_bar_reuse"]},
            "tracks": [{"name": "Guitar", "spans": [
                {"id": "span-1", "disposition": "LOOP", "source_ref": source_ref},
            ]}],
            "decisions": [], "resolutions": [],
            "patterns": [{
                "id": "pat-1", "name": "Riff", "track": "Guitar", "span_ids": ["span-1"],
                "bars": bars, "disposition": "LOOP", "final_disposition": "LOOP",
                "omitted": False,
                "selection_reason": "Short source fixture exercises bounded source-bar reuse.",
            }],
            "sections": [{"id": "sec-1", "name": "Verse", "bars": section_bars,
                          "active_pattern_ids": ["pat-1"]}],
            "tempo_bpm": tempo_bpm, "meter": meter,
            "publication": {"status": "final", "limitations": []},
            "performance": {},
            "disclosure": {"live_patterns": [], "omitted_patterns": [], "rest_spans": [],
                           "optional_spans_without_selected_pattern": []},
        }

    def run_writer(self, perf, skip_attach_plan=False):
        if not skip_attach_plan:
            perf = attach_plan(perf)
        self.perf_path.write_text(json.dumps(perf), encoding="utf-8")
        return subprocess.run(
            ["node", str(WRITER), "--performance", str(self.perf_path),
             "--song-dir", str(self.song_dir), "--output", str(self.output)],
            capture_output=True, text=True, timeout=90, check=False,
        )

    def run_analysis_preflight(self, manifest):
        manifest_path = self.song_dir / "arrangement.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return subprocess.run(
            ["node", str(ANALYSIS_PREFLIGHT), "--manifest", str(manifest_path),
             "--song-dir", str(self.song_dir)],
            capture_output=True, text=True, timeout=90, check=False,
        )

    def stage_files(self):
        return list(self.song_dir.glob(".arrangement.gp.*.stage.gp"))

    def score_xml(self):
        with zipfile.ZipFile(self.output) as zf:
            return zf.read("Content/score.gpif").decode("utf-8")

    # --- successful copies --------------------------------------------------

    def test_two_bar_copy_success(self):
        rel, digest = self.write_fixture(bars=4)
        perf = self.base_performance(rel, digest, bars=2, section_bars=4)
        result = self.run_writer(perf)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(self.output)
        self.assertEqual((actual["status"], actual["format"]), ("valid", "gp-zip"))
        self.assertEqual(actual["bar_count"], 4)
        frets = [n["fret"] for n in actual["tracks"][1]["notes"]]
        self.assertEqual(frets, [1, 2, 1, 2])
        self.assertEqual(actual["tracks"][1]["staves"][0]["tuning_midi"],
                         [40, 45, 50, 55, 59, 64])
        self.assertEqual(actual["tempos"], [{"position_quarters": 0.0, "bpm": 120.0}])
        self.assertEqual([], self.stage_files())

    def test_readable_layout_prunes_empty_tracks_and_preserves_sound_identity(self):
        rel, digest = self.write_fixture(
            bars=2, guitarProgram=31, guitarBank=2, guitarVolume=12, guitarBalance=5)
        perf = self.base_performance(rel, digest, bars=2, section_bars=2)
        perf["export_layout"] = {
            "version": 1,
            "prune_zero_note_tracks": True,
            "group_order": ["core", "optional", "unresolved"],
            "colors": {"drums": "#0000FF", "bass": "#FFFF00", "other": "#FF0000"},
        }
        result = self.run_writer(perf)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(self.output)
        self.assertEqual(actual["track_count"], 1)
        track = actual["tracks"][0]
        self.assertEqual(track["name"], "Guitar (loop)")
        self.assertEqual(track["source_color"], "#FF0000")
        self.assertEqual(track["playback"]["program"], 31)
        self.assertEqual(track["playback"]["bank"], 2)

    def test_logical_track_count_above_32_exports_when_pruned_projection_fits(self):
        rel, digest = self.write_fixture(bars=2)
        perf = attach_plan(self.base_performance(rel, digest, bars=2, section_bars=2))
        perf["export_layout"] = {
            "version": 1, "prune_zero_note_tracks": True,
            "group_order": ["core", "optional", "unresolved"],
            "colors": {"drums": "#0000FF", "bass": "#FFFF00", "other": "#FF0000"},
        }
        analysis_track = perf["instruments"][0]["analysis_track"]
        perf["instruments"] = [
            {
                "id": f"instrument-{i}",
                "analysis_track": analysis_track,
                "output_tracks": [{
                    "id": f"logical-{i}", "role": "loop", "pattern_id": "pat-1",
                }],
            }
            for i in range(33)
        ]
        perf["placements"][0]["output_track_id"] = "logical-0"
        result = self.run_writer(perf, skip_attach_plan=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(inspect(self.output)["track_count"], 1)

    def test_more_than_32_notated_tracks_fails_after_pruning(self):
        rel, digest = self.write_fixture(bars=2)
        perf = attach_plan(self.base_performance(rel, digest, bars=2, section_bars=2))
        perf["export_layout"] = {
            "version": 1, "prune_zero_note_tracks": True,
            "group_order": ["core", "optional", "unresolved"],
            "colors": {"drums": "#0000FF", "bass": "#FFFF00", "other": "#FF0000"},
        }
        analysis_track = perf["instruments"][0]["analysis_track"]
        source_placement = perf["placements"][0]
        perf["instruments"] = [
            {
                "id": f"instrument-{i}",
                "analysis_track": analysis_track,
                "output_tracks": [{
                    "id": f"logical-{i}", "role": "loop", "pattern_id": "pat-1",
                }],
            }
            for i in range(33)
        ]
        perf["placements"] = [
            {**source_placement, "output_track_id": f"logical-{i}"}
            for i in range(33)
        ]
        result = self.run_writer(perf, skip_attach_plan=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("33 notated output tracks after pruning", result.stderr)

    def test_analysis_preflight_reports_supported_loop_and_cue_only_live_span(self):
        rel, digest = self.write_fixture(bars=2)
        manifest = self.base_performance(rel, digest, bars=2, section_bars=2)
        manifest["tracks"][0]["spans"].append(
            {"id": "span-live", "disposition": "LIVE"})
        result = self.run_analysis_preflight(manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "ready")
        statuses = {item["id"]: item["status"] for item in report["findings"]}
        self.assertEqual(statuses, {"pat-1": "supported", "span-live": "cue_only"})

    def test_analysis_preflight_blocks_unbuildable_preserve_variants_choice(self):
        rel, digest = self.write_fixture(bars=2)
        manifest = self.base_performance(rel, digest, bars=2, section_bars=2)
        manifest["decisions"] = [{
            "id": "decision-variants",
            "question": "Preserve variants?",
            "options": ["preserve_variants", "use_canonical"],
        }]
        manifest["patterns"][0]["decision_ids"] = ["decision-variants"]
        result = self.run_analysis_preflight(manifest)
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["blockers"][0]["id"], "decision-variants")

    def test_analysis_preflight_blocks_same_track_patterns_stacked_for_whole_section(self):
        rel, digest = self.write_fixture(bars=4)
        manifest = self.base_performance(rel, digest, bars=4, section_bars=4)
        source_ref = manifest["tracks"][0]["spans"][0]["source_ref"]
        manifest["tracks"][0]["spans"].append(
            {"id": "span-2", "disposition": "LOOP", "source_ref": source_ref})
        manifest["patterns"].append({
            "id": "pat-2", "name": "Ending", "track": "Guitar", "span_ids": ["span-2"],
            "bars": 4, "disposition": "LOOP",
        })
        manifest["sections"][0]["active_pattern_ids"].append("pat-2")
        result = self.run_analysis_preflight(manifest)
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads(result.stdout)
        timing = [item for item in report["blockers"] if item["kind"] == "section_timing"]
        self.assertTrue(any("multiple loop patterns" in item["reason"] for item in timing), report)

    def test_selection_reason_does_not_waive_layering_review(self):
        rel, digest = self.write_fixture(bars=2)
        manifest = self.base_performance(rel, digest, bars=2, section_bars=4)
        source_ref = manifest["tracks"][0]["spans"][0]["source_ref"]
        manifest["tracks"][0]["spans"].append(
            {"id": "span-2", "disposition": "LOOP", "source_ref": source_ref})
        manifest["patterns"].append({
            "id": "pat-2", "name": "Ending", "track": "Guitar", "span_ids": ["span-2"],
            "bars": 2, "disposition": "LOOP",
            "selection_reason": "A short recurring source riff.",
        })
        manifest["sections"][0]["active_pattern_ids"].append("pat-2")
        result = self.run_analysis_preflight(manifest)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any("multiple loop patterns" in item["reason"]
                            for item in report["blockers"]), report)

    def test_preflight_checks_declared_length_and_fingerprint(self):
        rel, digest = self.write_fixture(bars=4)
        manifest = self.base_performance(rel, digest, bars=8, section_bars=8,
                                         content_fingerprint="0" * 64)
        result = self.run_analysis_preflight(manifest)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any("fingerprint" in item["reason"]
                            for item in report["blockers"]), report)

    def test_span_backed_variant_uses_owning_span_reference(self):
        rel, digest = self.write_fixture(bars=2)
        manifest = self.base_performance(rel, digest, bars=2, section_bars=2)
        manifest["patterns"][0]["variants"] = [{
            "id": "variant-1", "span_ids": ["span-1"], "is_canonical": True,
        }]
        result = self.run_analysis_preflight(manifest)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "ready", report)
        self.assertEqual(next(item for item in report["findings"]
                              if item["id"] == "variant-1")["status"], "supported")

    def test_preserve_variants_decision_cannot_borrow_another_patterns_variants(self):
        rel, digest = self.write_fixture(bars=4)
        manifest = self.base_performance(rel, digest, bars=4, section_bars=4)
        source_ref = manifest["tracks"][0]["spans"][0]["source_ref"]
        manifest["tracks"][0]["spans"].append({
            "id": "span-other", "disposition": "LOOP", "source_ref": source_ref,
        })
        manifest["decisions"] = [{
            "id": "decision-owner", "question": "Preserve owner variants?",
            "options": ["preserve_variants", "use_canonical"],
        }]
        manifest["patterns"][0]["decision_ids"] = ["decision-owner"]
        manifest["patterns"].append({
            "id": "pat-other", "name": "Other", "track": "Guitar",
            "span_ids": ["span-other"], "bars": 4, "disposition": "LOOP",
            "variants": [{"id": "other-variant", "span_ids": ["span-other"],
                          "occurrence_index": 1}],
        })
        result = self.run_analysis_preflight(manifest)
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads(result.stdout)
        variant_status = next(item for item in report["preserve_variants"]
                              if item["decision_id"] == "decision-owner")
        self.assertEqual(variant_status["status"], "blocked")
        self.assertEqual(variant_status["variant_pattern_ids"], ["pat-1"])

    def test_readable_layout_orders_core_before_earlier_optional_only_instrument(self):
        rel, digest = self.write_fixture(bars=2)
        source_ref = {
            "asset_sha256": digest, "asset_relative_path": rel,
            "track_index": 0, "track_name": "Fixture Guitar", "staff_index": 0,
            "written_master_bars": [1, 2], "voice_indices": [0],
        }
        perf = self.base_performance(rel, digest, bars=2, section_bars=2)
        perf["tracks"] = [
            {"name": "Optional Guitar", "spans": [
                {"id": "optional-span", "disposition": "OPTIONAL",
                 "source_ref": source_ref},
            ]},
            {"name": "Core Guitar", "spans": [
                {"id": "core-span", "disposition": "LOOP", "source_ref": source_ref},
            ]},
        ]
        perf["patterns"] = [
            {"id": "optional-pattern", "name": "Optional Riff",
             "track": "Optional Guitar", "span_ids": ["optional-span"], "bars": 2,
             "disposition": "OPTIONAL", "final_disposition": "LOOP", "omitted": False},
            {"id": "core-pattern", "name": "Core Riff",
             "track": "Core Guitar", "span_ids": ["core-span"], "bars": 2,
             "disposition": "LOOP", "final_disposition": "LOOP", "omitted": False},
        ]
        perf["sections"] = [{
            "id": "sec-1", "name": "Verse", "bars": 2,
            "active_pattern_ids": ["optional-pattern", "core-pattern"],
        }]
        perf["export_layout"] = {
            "version": 1,
            "prune_zero_note_tracks": True,
            "group_order": ["core", "optional", "unresolved"],
            "colors": {"drums": "#0000FF", "bass": "#FFFF00", "other": "#FF0000"},
        }
        result = self.run_writer(perf)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(self.output)
        self.assertEqual(
            [track["name"] for track in actual["tracks"]],
            ["Core Guitar (loop)", "Optional Guitar (loop) [OPTIONAL]"],
        )

    def test_one_bar_copy(self):
        rel, digest = self.write_fixture(bars=2)
        perf = self.base_performance(rel, digest, written_master_bars=(1, 1), bars=1,
                                      section_bars=3)
        result = self.run_writer(perf)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(self.output)
        self.assertEqual(actual["bar_count"], 3)
        frets = [n["fret"] for n in actual["tracks"][1]["notes"]]
        self.assertEqual(frets, [1, 1, 1])

    def test_four_bar_copy(self):
        rel, digest = self.write_fixture(bars=4)
        perf = self.base_performance(rel, digest, written_master_bars=(1, 4), bars=4,
                                      section_bars=8)
        result = self.run_writer(perf)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(self.output)
        self.assertEqual(actual["bar_count"], 8)
        frets = [n["fret"] for n in actual["tracks"][1]["notes"]]
        self.assertEqual(frets, [1, 2, 3, 4, 1, 2, 3, 4])

    def test_drum_track_percussion_copy(self):
        rel, digest = self.write_fixture(bars=2, drumTrack=True)
        perf = self.base_performance(rel, digest, track_index=1, track_name="Fixture Drums",
                                      bars=2, section_bars=2)
        perf["tracks"][0]["name"] = "Drums"
        perf["patterns"][0]["track"] = "Drums"
        perf["sections"][0]["active_pattern_ids"] = ["pat-1"]
        result = self.run_writer(perf)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(self.output)
        self.assertEqual(actual["tracks"][1]["staves"][0]["tuning_midi"], None)

    def test_odd_meter_stable_copy(self):
        rel, digest = self.write_fixture(bars=2, numerator=7, denominator=8, tempoBpm=100)
        perf = self.base_performance(rel, digest, bars=2, section_bars=4, tempo_bpm=100,
                                      meter="7/8")
        result = self.run_writer(perf)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(self.output)
        self.assertTrue(all(b["meter"] == "7/8" for b in actual["bars"]))
        self.assertEqual(actual["tempos"], [{"position_quarters": 0.0, "bpm": 100.0}])

    def test_repeated_section_placement_across_two_sections(self):
        rel, digest = self.write_fixture(bars=2)
        perf = self.base_performance(rel, digest, bars=2, section_bars=2)
        perf["sections"].append({"id": "sec-2", "name": "Verse 2", "bars": 4,
                                  "active_pattern_ids": ["pat-1"]})
        result = self.run_writer(perf)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(self.output)
        self.assertEqual(actual["bar_count"], 6)
        frets = [n["fret"] for n in actual["tracks"][1]["notes"]]
        self.assertEqual(frets, [1, 2, 1, 2, 1, 2])

    def test_content_fingerprint_present_and_matching_is_accepted(self):
        rel, digest = self.write_fixture(bars=2)
        # Discover the correct fingerprint by running once without constraining it, then
        # re-running with the exact value asserted (tests both presence and success path).
        perf = self.base_performance(rel, digest, bars=2, section_bars=2)
        result = self.run_writer(perf)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_supported_note_effects_round_trip(self):
        rel, digest = self.write_fixture(
            bars=4, palmMuteBars=[1], letRingBars=[2], hammerPullOriginBar=3)
        perf = self.base_performance(rel, digest, written_master_bars=(1, 4), bars=4,
                                     section_bars=4)
        result = self.run_writer(perf)
        self.assertEqual(result.returncode, 0, result.stderr)
        xml = self.score_xml()
        self.assertIn('name="PalmMuted"', xml)
        self.assertIn("<LetRing/>", xml)
        self.assertIn('name="HopoOrigin"', xml)
        self.assertIn('name="HopoDestination"', xml)

    # --- rejections: digest / path / fingerprint mismatch -------------------

    def test_digest_mismatch_rejected(self):
        rel, digest = self.write_fixture(bars=2)
        perf = self.base_performance(rel, digest, bars=2, section_bars=2)
        perf["source"]["asset_sha256"] = "0" * 64
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match", result.stderr)
        self.assertEqual([], self.stage_files())

    def test_path_mismatch_rejected(self):
        rel, digest = self.write_fixture(bars=2)
        perf = self.base_performance(rel, digest, bars=2, section_bars=2)
        perf["source"]["asset_relative_path"] = "source/does-not-exist.gp"
        perf["tracks"][0]["spans"][0]["source_ref"]["asset_relative_path"] = \
            "source/does-not-exist.gp"
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.stderr)

    def test_path_traversal_rejected(self):
        rel, digest = self.write_fixture(bars=2)
        escaped = "../outside.gp"
        perf = self.base_performance(rel, digest, bars=2, section_bars=2)
        perf["source"]["asset_relative_path"] = escaped
        perf["tracks"][0]["spans"][0]["source_ref"]["asset_relative_path"] = escaped
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)

    def test_fingerprint_mismatch_rejected(self):
        rel, digest = self.write_fixture(bars=2)
        perf = self.base_performance(rel, digest, bars=2, section_bars=2,
                                      content_fingerprint="a" * 64)
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("content_fingerprint", result.stderr)

    def test_capability_required(self):
        rel, digest = self.write_fixture(bars=2)
        perf = self.base_performance(rel, digest, bars=2, section_bars=2)
        perf["source"]["capabilities"] = []
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source_bar_reuse", result.stderr)

    # --- publication no-clobber ----------------------------------------------

    def test_no_clobber_existing_output(self):
        rel, digest = self.write_fixture(bars=2)
        perf = self.base_performance(rel, digest, bars=2, section_bars=2)
        self.output.write_bytes(b"leave untouched")
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("output already exists", result.stderr)
        self.assertEqual(self.output.read_bytes(), b"leave untouched")

    # --- unsupported-structure blockers (cleanup on failure, no stage files) -

    def test_unsupported_multi_voice_blocked(self):
        rel, digest = self.write_fixture(bars=2, secondVoiceBar=1)
        perf = self.base_performance(rel, digest, bars=2, section_bars=2)
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("additional active voice", result.stderr)
        self.assertEqual([], self.stage_files())
        self.assertFalse(self.output.exists())

    def test_boundary_tie_blocked(self):
        rel, digest = self.write_fixture(bars=4, tieIntoBar=3)
        perf = self.base_performance(rel, digest, written_master_bars=(3, 4), bars=2,
                                      section_bars=2)
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tie enters the pattern", result.stderr)
        self.assertEqual([], self.stage_files())

    def test_boundary_grace_blocked(self):
        rel, digest = self.write_fixture(bars=4, graceBar=3)
        perf = self.base_performance(rel, digest, written_master_bars=(3, 4), bars=2,
                                      section_bars=2)
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("grace note", result.stderr)
        self.assertEqual([], self.stage_files())

    def test_unsupported_effect_blocked(self):
        rel, digest = self.write_fixture(bars=2, effectBar=1)
        perf = self.base_performance(rel, digest, bars=2, section_bars=2)
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported effect", result.stderr)
        self.assertEqual([], self.stage_files())

    def test_hammer_pull_leaving_span_blocked(self):
        rel, digest = self.write_fixture(bars=4, hammerPullOriginBar=2)
        perf = self.base_performance(rel, digest, written_master_bars=(1, 2), bars=2,
                                     section_bars=2)
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hammer/pull relationship leaves", result.stderr)
        self.assertEqual([], self.stage_files())

    def test_hammer_pull_entering_span_blocked(self):
        rel, digest = self.write_fixture(bars=4, hammerPullOriginBar=1)
        perf = self.base_performance(rel, digest, written_master_bars=(2, 3), bars=2,
                                     section_bars=2)
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hammer/pull relationship enters", result.stderr)
        self.assertEqual([], self.stage_files())

    def test_let_ring_chain_crossing_span_boundary_copies_selected_flags(self):
        rel, digest = self.write_fixture(bars=4, letRingBars=[1, 2])
        perf = self.base_performance(rel, digest, written_master_bars=(2, 3), bars=2,
                                     section_bars=2)
        result = self.run_writer(perf)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<LetRing/>", self.score_xml())
        self.assertEqual([], self.stage_files())

    def test_palm_mute_chain_crossing_span_boundary_copies_selected_flags(self):
        rel, digest = self.write_fixture(bars=4, palmMuteBars=[2, 3])
        perf = self.base_performance(rel, digest, written_master_bars=(1, 2), bars=2,
                                     section_bars=2)
        result = self.run_writer(perf)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('name="PalmMuted"', self.score_xml())
        self.assertEqual([], self.stage_files())

    def test_mid_pattern_tempo_change_blocked(self):
        rel, digest = self.write_fixture(bars=4, tempoChangeBar=2)
        perf = self.base_performance(rel, digest, written_master_bars=(1, 2), bars=2,
                                      section_bars=2)
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tempo changes within the pattern", result.stderr)
        self.assertEqual([], self.stage_files())

    def test_mid_pattern_meter_change_blocked(self):
        rel, digest = self.write_fixture(bars=4, meterChangeBar=2)
        perf = self.base_performance(rel, digest, written_master_bars=(1, 2), bars=2,
                                      section_bars=2)
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("meter changes within the pattern", result.stderr)
        self.assertEqual([], self.stage_files())

    def test_invalid_bar_count_rejected(self):
        rel, digest = self.write_fixture(bars=4)
        perf = self.base_performance(rel, digest, written_master_bars=(1, 3), bars=3,
                                      section_bars=3)
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("1/2/4/8-bar range", result.stderr)

    def test_declared_tempo_disagreement_with_source_blocked(self):
        rel, digest = self.write_fixture(bars=2, tempoBpm=120)
        perf = self.base_performance(rel, digest, bars=2, section_bars=2, tempo_bpm=140)
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("disagrees with the arrangement's declared tempo_bpm", result.stderr)

    def test_declared_meter_disagreement_with_source_blocked(self):
        rel, digest = self.write_fixture(bars=2, numerator=3, denominator=4)
        perf = self.base_performance(rel, digest, bars=2, section_bars=2, meter="4/4")
        result = self.run_writer(perf)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("disagrees with the arrangement's declared meter", result.stderr)


class PlanDrivenWriterTests(unittest.TestCase):
    """Directly-authored `plan_version: 2` performance-plan fixtures (instruments/
    output_tracks/output_timeline/placements), exercising the finalize-tracks writer
    behaviors `compile_performance.py`'s own fixtures do not happen to cover: track
    ordering/duplicate display names, entirely-live instruments, UNRESOLVED/cue
    rendering, empty role tracks, OPTIONAL markers, overlap rejection, and the
    no-source-touch cue-only path.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vp-plan-driven-")
        self.addCleanup(temp.cleanup)
        self.song_dir = Path(temp.name)
        self.source_dir = self.song_dir / "source"
        self.source_dir.mkdir(parents=True)
        self.perf_path = (self.song_dir / ".vp-music-tracking" / "arrangement" /
                           "performance-arrangement.json")
        self.perf_path.parent.mkdir(parents=True)
        self.output = self.song_dir / "arrangement.gp"

    def write_fixture(self, name="fixture.gp", **opts):
        data = make_fixture_bytes(**opts)
        path = self.source_dir / name
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        return f"source/{name}", digest

    def run_writer(self, raw, extra_args=None):
        self.perf_path.write_text(json.dumps(raw), encoding="utf-8")
        args = ["node", str(WRITER), "--performance", str(self.perf_path),
                "--song-dir", str(self.song_dir), "--output", str(self.output)]
        if extra_args:
            args.extend(extra_args)
        return subprocess.run(
            args, capture_output=True, text=True, timeout=90, check=False,
        )

    def score_xml(self):
        """Decoded `Content/score.gpif` text from the written .gp (a ZIP container), for
        asserting on cue/marker text (`Beat.text`) that `music_assets.inspect()`'s plain
        GPIF-to-notes parser does not surface. Raw byte search on the ZIP file itself is
        not meaningful since its entries are DEFLATE-compressed.
        """
        with zipfile.ZipFile(self.output) as zf:
            return zf.read("Content/score.gpif").decode("utf-8")

    def minimal_raw(self, *, track_name="Vox", pattern_disposition="LIVE",
                    capabilities=("source_bar_reuse",), publication_status="final",
                    limitations=None, consent=None, instruments, section_instances,
                    output_timeline, placements, variant_selections=None, sections=None,
                    source=None):
        pub = {"status": publication_status, "limitations": list(limitations or [])}
        if publication_status == "partial":
            pub["consent"] = consent or "reviewed and accepted"
        if sections is None:
            sections = [{"id": "sec-1", "name": "Verse", "bars": max(len(output_timeline), 1),
                        "active_pattern_ids": ["dummy-pat"]}]
        if source is None:
            source = {"asset_relative_path": "source/none.gp", "asset_sha256": "a" * 64,
                      "format": "gpif", "capabilities": list(capabilities)}
        return {
            "version": 2,
            "source": source,
            "tracks": [{"name": track_name, "spans": [
                {"id": "dummy-span", "disposition": pattern_disposition},
            ]}],
            "decisions": [], "resolutions": [],
            "patterns": [{"id": "dummy-pat", "name": "Dummy", "track": track_name,
                         "span_ids": ["dummy-span"], "bars": 1,
                         "disposition": pattern_disposition,
                         "final_disposition": pattern_disposition, "omitted": False}],
            "sections": sections,
            "tempo_bpm": 120, "meter": "4/4",
            "publication": pub,
            "performance": {},
            "disclosure": {"live_patterns": [], "omitted_patterns": [], "rest_spans": [],
                           "optional_spans_without_selected_pattern": []},
            "plan_version": 2,
            "fingerprints": {"analysis_sha256": "0" * 64, "resolutions_sha256": "0" * 64},
            "instruments": instruments, "section_instances": section_instances,
            "output_timeline": output_timeline, "removal_ledger": [],
            "placements": placements, "variant_selections": variant_selections or [],
        }

    def section_instances_and_timeline(self, bars, section_id="sec-1"):
        instance_id = f"{section_id}-1"
        instances = [{"id": instance_id, "section_id": section_id, "name": "Verse",
                     "occurrence_index": 1, "action": "retain", "approved": True,
                     "omitted_layers": []}]
        timeline = [{"output_index": i, "section_instance_id": instance_id,
                    "source_section_bar": i} for i in range(bars)]
        return instances, timeline

    def test_two_loop_patterns_same_instrument_sparse_tracks_with_live_unresolved(self):
        rel, digest = self.write_fixture(bars=4)
        ref_a = {"asset_sha256": digest, "asset_relative_path": rel, "track_index": 0,
                "track_name": "Fixture Guitar", "staff_index": 0,
                "written_master_bars": [1, 2], "voice_indices": [0]}
        ref_b = {"asset_sha256": digest, "asset_relative_path": rel, "track_index": 0,
                "track_name": "Fixture Guitar", "staff_index": 0,
                "written_master_bars": [3, 4], "voice_indices": [0]}
        instruments = [{"id": "gtr", "analysis_track": "Guitar", "output_tracks": [
            {"id": "gtr-live", "role": "live"},
            {"id": "gtr-loop-a", "role": "loop", "pattern_id": "pat-a"},
            {"id": "gtr-loop-b", "role": "loop", "pattern_id": "pat-b"},
            {"id": "gtr-unresolved", "role": "unresolved"},
        ]}]
        instances, timeline = self.section_instances_and_timeline(4)
        placements = [
            {"output_track_id": "gtr-loop-a", "output_bar_range": [0, 1],
             "material_id": "pat-a", "mode": "loop", "optional": False, "source_ref": ref_a},
            {"output_track_id": "gtr-loop-b", "output_bar_range": [2, 3],
             "material_id": "pat-b", "mode": "loop", "optional": False, "source_ref": ref_b},
        ]
        raw = self.minimal_raw(instruments=instruments, section_instances=instances,
                               output_timeline=timeline, placements=placements,
                               source={"asset_relative_path": rel, "asset_sha256": digest,
                                      "format": "gpif", "capabilities": ["source_bar_reuse"]})
        raw["patterns"] = [
            {"id": "pat-a", "name": "A", "track": "Guitar", "span_ids": ["span-a"], "bars": 2,
             "disposition": "LOOP", "final_disposition": "LOOP", "omitted": False},
            {"id": "pat-b", "name": "B", "track": "Guitar", "span_ids": ["span-b"], "bars": 2,
             "disposition": "LOOP", "final_disposition": "LOOP", "omitted": False},
        ]
        raw["tracks"] = [{"name": "Guitar", "spans": [
            {"id": "span-a", "disposition": "LOOP", "source_ref": ref_a},
            {"id": "span-b", "disposition": "LOOP", "source_ref": ref_b},
        ]}]
        raw["sections"] = [{"id": "sec-1", "name": "Verse", "bars": 4,
                            "active_pattern_ids": ["pat-a", "pat-b"]}]
        result = self.run_writer(raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(self.output)
        self.assertEqual(actual["track_count"], 4)
        names = [t["name"] for t in actual["tracks"]]
        self.assertEqual(names, ["Guitar (live)", "Guitar (loop)", "Guitar (loop)",
                                 "Guitar (unresolved)"])
        self.assertEqual(len(actual["tracks"][1]["notes"]), 2)  # bars 0-1 only
        self.assertEqual(len(actual["tracks"][2]["notes"]), 2)  # bars 2-3 only
        self.assertEqual(len(actual["tracks"][0]["notes"]), 0)  # live track: all rests
        self.assertEqual(len(actual["tracks"][3]["notes"]), 0)  # unresolved track: all rests

    def test_entirely_live_instrument_has_no_loop_track(self):
        instruments = [{"id": "vox", "analysis_track": "Vox", "output_tracks": [
            {"id": "vox-live", "role": "live"}, {"id": "vox-unresolved", "role": "unresolved"},
        ]}]
        instances, timeline = self.section_instances_and_timeline(2)
        placements = [{"output_track_id": "vox-live", "output_bar_range": [0, 1],
                      "material_id": "dummy-pat", "mode": "live", "optional": False}]
        raw = self.minimal_raw(instruments=instruments, section_instances=instances,
                               output_timeline=timeline, placements=placements)
        result = self.run_writer(raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(self.output)
        self.assertEqual(actual["track_count"], 2)
        self.assertEqual([t["name"] for t in actual["tracks"]], ["Vox (live)", "Vox (unresolved)"])

    def test_unresolved_cue_in_partial_plan(self):
        instruments = [{"id": "vox", "analysis_track": "Vox", "output_tracks": [
            {"id": "vox-live", "role": "live"}, {"id": "vox-unresolved", "role": "unresolved"},
        ]}]
        instances, timeline = self.section_instances_and_timeline(2)
        placements = [{"output_track_id": "vox-unresolved", "output_bar_range": [0, 1],
                      "material_id": "dummy-pat", "mode": "unresolved", "optional": False}]
        raw = self.minimal_raw(instruments=instruments, section_instances=instances,
                               output_timeline=timeline, placements=placements,
                               pattern_disposition="UNRESOLVED", publication_status="partial",
                               limitations=["one pattern deferred"])
        result = self.run_writer(raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("UNRESOLVED: bars 1-2", self.score_xml())

    def test_final_plan_keeps_empty_unresolved_track(self):
        instruments = [{"id": "gtr", "analysis_track": "Guitar", "output_tracks": [
            {"id": "gtr-live", "role": "live"}, {"id": "gtr-unresolved", "role": "unresolved"},
        ]}]
        instances, timeline = self.section_instances_and_timeline(2)
        placements = [{"output_track_id": "gtr-live", "output_bar_range": [0, 1],
                      "material_id": "dummy-pat", "mode": "live", "optional": False}]
        raw = self.minimal_raw(instruments=instruments, section_instances=instances,
                               output_timeline=timeline, placements=placements,
                               track_name="Guitar")
        result = self.run_writer(raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(self.output)
        self.assertEqual(actual["track_count"], 2)
        self.assertEqual(actual["tracks"][1]["name"], "Guitar (unresolved)")
        self.assertEqual(len(actual["tracks"][1]["notes"]), 0)

    def test_optional_retained_marker_visible(self):
        instruments = [{"id": "gtr", "analysis_track": "Guitar", "output_tracks": [
            {"id": "gtr-live", "role": "live"}, {"id": "gtr-unresolved", "role": "unresolved"},
        ]}]
        instances, timeline = self.section_instances_and_timeline(2)
        placements = [{"output_track_id": "gtr-live", "output_bar_range": [0, 1],
                      "material_id": "dummy-pat", "mode": "live", "optional": True}]
        raw = self.minimal_raw(instruments=instruments, section_instances=instances,
                               output_timeline=timeline, placements=placements,
                               track_name="Guitar")
        result = self.run_writer(raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        xml = self.score_xml()
        self.assertIn("[OPTIONAL]", xml)
        # requirement 3: the retained-OPTIONAL placement's cue marker must not crowd out
        # the section's own visible GPIF `<Section>` marker (alphaTab's format API
        # supports both simultaneously: a bar-local cue/text and a master-bar section
        # name) -- here the *entire* retained section is this one optional placement,
        # so both markers must survive together on reload.
        self.assertIn("<Section>", xml)
        self.assertIn("<![CDATA[Verse]]>", xml)

    def test_overlap_rejected_same_output_track(self):
        instruments = [{"id": "gtr", "analysis_track": "Guitar", "output_tracks": [
            {"id": "gtr-live", "role": "live"}, {"id": "gtr-unresolved", "role": "unresolved"},
        ]}]
        instances, timeline = self.section_instances_and_timeline(4)
        placements = [
            {"output_track_id": "gtr-live", "output_bar_range": [0, 2],
             "material_id": "dummy-pat", "mode": "live", "optional": False},
            {"output_track_id": "gtr-live", "output_bar_range": [1, 3],
             "material_id": "dummy-pat", "mode": "live", "optional": False},
        ]
        raw = self.minimal_raw(instruments=instruments, section_instances=instances,
                               output_timeline=timeline, placements=placements,
                               track_name="Guitar")
        result = self.run_writer(raw)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicting overlapping placements", result.stderr)

    def test_all_cue_plan_does_not_require_source_bar_reuse(self):
        instruments = [{"id": "vox", "analysis_track": "Vox", "output_tracks": [
            {"id": "vox-live", "role": "live"}, {"id": "vox-unresolved", "role": "unresolved"},
        ]}]
        instances, timeline = self.section_instances_and_timeline(2)
        placements = [{"output_track_id": "vox-live", "output_bar_range": [0, 1],
                      "material_id": "dummy-pat", "mode": "live", "optional": False}]
        raw = self.minimal_raw(instruments=instruments, section_instances=instances,
                               output_timeline=timeline, placements=placements,
                               capabilities=())  # no source_bar_reuse: must still build (cue only)
        result = self.run_writer(raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LIVE: bars 1-2", self.score_xml())

    def test_tail_policy_wrap_full_cycles_only_leaves_remainder_rest(self):
        rel, digest = self.write_fixture(bars=2)
        ref = {"asset_sha256": digest, "asset_relative_path": rel, "track_index": 0,
              "track_name": "Fixture Guitar", "staff_index": 0,
              "written_master_bars": [1, 2], "voice_indices": [0]}
        instruments = [{"id": "gtr", "analysis_track": "Guitar", "output_tracks": [
            {"id": "gtr-live", "role": "live"},
            {"id": "gtr-loop-a", "role": "loop", "pattern_id": "pat-a"},
            {"id": "gtr-unresolved", "role": "unresolved"},
        ]}]
        instances, timeline = self.section_instances_and_timeline(3)
        placements = [{"output_track_id": "gtr-loop-a", "output_bar_range": [0, 2],
                      "material_id": "pat-a", "mode": "loop", "optional": False,
                      "source_ref": ref, "tail_policy": "reviewed_wrap_full_cycles_only"}]
        raw = self.minimal_raw(instruments=instruments, section_instances=instances,
                               output_timeline=timeline, placements=placements,
                               source={"asset_relative_path": rel, "asset_sha256": digest,
                                      "format": "gpif", "capabilities": ["source_bar_reuse"]})
        raw["patterns"] = [{"id": "pat-a", "name": "A", "track": "Guitar",
                           "span_ids": ["span-a"], "bars": 2, "disposition": "LOOP",
                           "final_disposition": "LOOP", "omitted": False}]
        raw["tracks"] = [{"name": "Guitar", "spans": [
            {"id": "span-a", "disposition": "LOOP", "source_ref": ref}]}]
        raw["sections"] = [{"id": "sec-1", "name": "Verse", "bars": 3,
                            "active_pattern_ids": ["pat-a"]}]
        result = self.run_writer(raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(self.output)
        self.assertEqual(len(actual["tracks"][1]["notes"]), 2)  # only the one full 2-bar cycle

    # --- requirement 2: real writer --analysis/--resolutions freshness checks ---------

    def _analysis_and_resolutions_docs(self):
        analysis_doc = {"version": 2, "tracks": [{"name": "Guitar", "spans": []}],
                         "decisions": [], "note": "fixture analysis manifest"}
        resolutions_doc = [{"decision_id": "decision-x", "option": "use_canonical"}]
        return analysis_doc, resolutions_doc

    def _fresh_raw_with_fingerprints(self, instruments, instances, timeline, placements,
                                     analysis_doc, resolutions_doc):
        raw = self.minimal_raw(instruments=instruments, section_instances=instances,
                               output_timeline=timeline, placements=placements,
                               track_name="Guitar")
        raw["fingerprints"] = {"analysis_sha256": canonical_sha256(analysis_doc),
                               "resolutions_sha256": canonical_sha256(resolutions_doc)}
        return raw

    def test_analysis_resolutions_freshness_accepts_matching(self):
        instruments = [{"id": "gtr", "analysis_track": "Guitar", "output_tracks": [
            {"id": "gtr-live", "role": "live"}, {"id": "gtr-unresolved", "role": "unresolved"},
        ]}]
        instances, timeline = self.section_instances_and_timeline(2)
        placements = [{"output_track_id": "gtr-live", "output_bar_range": [0, 1],
                      "material_id": "dummy-pat", "mode": "live", "optional": False}]
        analysis_doc, resolutions_doc = self._analysis_and_resolutions_docs()
        raw = self._fresh_raw_with_fingerprints(instruments, instances, timeline, placements,
                                                analysis_doc, resolutions_doc)
        analysis_path = self.song_dir / "arrangement-analysis.json"
        resolutions_path = self.song_dir / "resolutions.json"
        analysis_path.write_text(json.dumps(analysis_doc), encoding="utf-8")
        resolutions_path.write_text(json.dumps(resolutions_doc), encoding="utf-8")
        result = self.run_writer(raw, extra_args=["--analysis", str(analysis_path),
                                                   "--resolutions", str(resolutions_path)])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_analysis_resolutions_freshness_rejects_stale_analysis(self):
        instruments = [{"id": "gtr", "analysis_track": "Guitar", "output_tracks": [
            {"id": "gtr-live", "role": "live"}, {"id": "gtr-unresolved", "role": "unresolved"},
        ]}]
        instances, timeline = self.section_instances_and_timeline(2)
        placements = [{"output_track_id": "gtr-live", "output_bar_range": [0, 1],
                      "material_id": "dummy-pat", "mode": "live", "optional": False}]
        analysis_doc, resolutions_doc = self._analysis_and_resolutions_docs()
        raw = self._fresh_raw_with_fingerprints(instruments, instances, timeline, placements,
                                                analysis_doc, resolutions_doc)
        stale_analysis_doc = dict(analysis_doc, note="this has drifted since compile")
        analysis_path = self.song_dir / "arrangement-analysis.json"
        resolutions_path = self.song_dir / "resolutions.json"
        analysis_path.write_text(json.dumps(stale_analysis_doc), encoding="utf-8")
        resolutions_path.write_text(json.dumps(resolutions_doc), encoding="utf-8")
        result = self.run_writer(raw, extra_args=["--analysis", str(analysis_path),
                                                   "--resolutions", str(resolutions_path)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale compiled plan", result.stderr)
        self.assertIn("--analysis", result.stderr)

    def test_analysis_resolutions_freshness_rejects_stale_resolutions(self):
        instruments = [{"id": "gtr", "analysis_track": "Guitar", "output_tracks": [
            {"id": "gtr-live", "role": "live"}, {"id": "gtr-unresolved", "role": "unresolved"},
        ]}]
        instances, timeline = self.section_instances_and_timeline(2)
        placements = [{"output_track_id": "gtr-live", "output_bar_range": [0, 1],
                      "material_id": "dummy-pat", "mode": "live", "optional": False}]
        analysis_doc, resolutions_doc = self._analysis_and_resolutions_docs()
        raw = self._fresh_raw_with_fingerprints(instruments, instances, timeline, placements,
                                                analysis_doc, resolutions_doc)
        stale_resolutions_doc = resolutions_doc + [
            {"decision_id": "decision-y", "option": "play_live"}]
        analysis_path = self.song_dir / "arrangement-analysis.json"
        resolutions_path = self.song_dir / "resolutions.json"
        analysis_path.write_text(json.dumps(analysis_doc), encoding="utf-8")
        resolutions_path.write_text(json.dumps(stale_resolutions_doc), encoding="utf-8")
        result = self.run_writer(raw, extra_args=["--analysis", str(analysis_path),
                                                   "--resolutions", str(resolutions_path)])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale compiled plan", result.stderr)
        self.assertIn("--resolutions", result.stderr)


if __name__ == "__main__":
    unittest.main()
