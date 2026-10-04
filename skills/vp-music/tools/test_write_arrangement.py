"""Synthetic, rights-independent end-to-end tests for the GP7 arrangement writer."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from music_assets import inspect

HERE = Path(__file__).resolve().parent
WRITER = HERE / "write_arrangement.mjs"

def v2_manifest(disposition="LOOP", decisions=None):
    manifest = {
        "version": 2,
        "source": {
            "asset_relative_path": "source/synthetic.gp",
            "asset_sha256": "a" * 64,
            "format": "gpif",
        },
        "tracks": [
            {
                "name": "Rhythm Guitar",
                "role": "rhythm",
                "role_confidence": "likely",
                "spans": [{"id": "span-a", "disposition": disposition}],
            },
        ],
    }
    if decisions is not None:
        manifest["decisions"] = decisions
    return manifest


class ValidateOnlyContractTests(unittest.TestCase):
    """v2 JSON contract foundation: schema-only validation, no filesystem
    side effects, and analysis-time vs. performance/export distinctions."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vp-validate-only-")
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.manifest = self.dir / "arrangement.json"

    def run_validate(self, manifest, assert_final=False):
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        args = ["node", str(WRITER), "--manifest", str(self.manifest), "--validate-only"]
        if assert_final:
            args.append("--assert-final")
        return subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)

    def test_validate_only_does_not_require_worksheet_pattern_or_output(self):
        result = self.run_validate(v2_manifest())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"status": "valid", "version": 2})
        self.assertEqual(list(self.dir.iterdir()), [self.manifest])

    def test_analysis_may_contain_unresolved_but_export_gate_rejects_it(self):
        manifest = v2_manifest("UNRESOLVED", decisions=[
            {"id": "d1", "question": "Preserve variant or use canonical loop?",
             "options": ["preserve_variant", "use_canonical"]},
        ])
        analysis = self.run_validate(manifest)
        self.assertEqual(analysis.returncode, 0, analysis.stderr)
        export_gate = self.run_validate(manifest, assert_final=True)
        self.assertNotEqual(export_gate.returncode, 0)
        self.assertIn("UNRESOLVED", export_gate.stderr)

    def test_unresolved_without_decisions_is_rejected_even_at_analysis_time(self):
        result = self.run_validate(v2_manifest("UNRESOLVED"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("decisions", result.stderr)

    def test_all_five_dispositions_are_accepted_at_analysis_time(self):
        for disposition in ("LOOP", "LIVE", "OPTIONAL", "REST"):
            with self.subTest(disposition=disposition):
                result = self.run_validate(v2_manifest(disposition))
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_disposition_is_rejected(self):
        result = self.run_validate(v2_manifest("BLOCKED"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("disposition", result.stderr)

    def test_strict_unknown_key_rejected_at_every_level(self):
        cases = {
            "top-level": lambda m: m.update(extra=1),
            "source": lambda m: m["source"].update(extra=1),
            "track": lambda m: m["tracks"][0].update(extra=1),
            "span": lambda m: m["tracks"][0]["spans"][0].update(extra=1),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                manifest = v2_manifest()
                mutate(manifest)
                result = self.run_validate(manifest)
                self.assertNotEqual(result.returncode, 0, label)
                self.assertIn("unsupported field", result.stderr)

    def test_rejects_absolute_or_escaping_source_paths(self):
        for bad_path in ("/etc/passwd", "C:\\secret\\song.gp", "../../escape.gp"):
            with self.subTest(path=bad_path):
                manifest = v2_manifest()
                manifest["source"]["asset_relative_path"] = bad_path
                result = self.run_validate(manifest)
                self.assertNotEqual(result.returncode, 0, bad_path)

    def test_rejects_raw_source_content_smuggled_as_a_field(self):
        manifest = v2_manifest()
        manifest["tracks"][0]["spans"][0]["raw_gpif"] = "<GPIF>not allowed</GPIF>"
        result = self.run_validate(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported field", result.stderr)

    def test_source_capabilities_accepted_and_validated(self):
        manifest = v2_manifest()
        manifest["source"]["capabilities"] = ["performed_order", "tempo_map"]
        self.assertEqual(self.run_validate(manifest).returncode, 0)
        bad = v2_manifest()
        bad["source"]["capabilities"] = ["not_a_real_capability"]
        result = self.run_validate(bad)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("capabilities", result.stderr)

    def test_span_match_tier_evidence_and_near_match_are_validated(self):
        manifest = v2_manifest("UNRESOLVED", decisions=[
            {"id": "decision-span-a", "question": "Preserve or canonicalize?",
             "options": ["preserve_variants", "use_canonical"]},
        ])
        manifest["tracks"][0]["spans"][0].update({
            "match_tier": "near",
            "evidence": "3 of 4 bars match",
            "near_match": {"matching_bars": 3, "total_bars": 4, "divergent_bar_offsets": [3]},
        })
        self.assertEqual(self.run_validate(manifest).returncode, 0)
        bad = v2_manifest()
        bad["tracks"][0]["spans"][0]["match_tier"] = "fuzzy"
        result = self.run_validate(bad)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("match_tier", result.stderr)

    def test_grouped_decision_depends_on_and_applies_when_are_validated(self):
        manifest = v2_manifest("UNRESOLVED", decisions=[
            {"id": "decision-span-a", "question": "Preserve or canonicalize?",
             "options": ["preserve_variants", "use_canonical", "play_live"],
             "coordinates": ["Rhythm Guitar, written bars 1-2"]},
            {"id": "decision-span-a-followup", "depends_on": "decision-span-a",
             "applies_when": "use_canonical",
             "question": "Keep in rotation or omit?", "options": ["keep_loop", "omit"]},
        ])
        self.assertEqual(self.run_validate(manifest).returncode, 0)

        dangling = v2_manifest("UNRESOLVED", decisions=[
            {"id": "decision-span-a", "question": "Preserve or canonicalize?",
             "options": ["preserve_variants", "use_canonical"]},
            {"id": "decision-span-a-followup", "depends_on": "decision-missing",
             "applies_when": "use_canonical",
             "question": "Keep in rotation or omit?", "options": ["keep_loop", "omit"]},
        ])
        result = self.run_validate(dangling)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("depends_on", result.stderr)

        bad_option = v2_manifest("UNRESOLVED", decisions=[
            {"id": "decision-span-a", "question": "Preserve or canonicalize?",
             "options": ["preserve_variants", "use_canonical"]},
            {"id": "decision-span-a-followup", "depends_on": "decision-span-a",
             "applies_when": "not_an_option",
             "question": "Keep in rotation or omit?", "options": ["keep_loop", "omit"]},
        ])
        result = self.run_validate(bad_option)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("applies_when", result.stderr)

    def test_selected_patterns_and_sections_reference_integrity(self):
        manifest = v2_manifest()
        manifest["patterns"] = [{
            "id": "pattern-a", "name": "Rhythm A", "track": "Rhythm Guitar",
            "span_ids": ["span-a"], "bars": 2, "disposition": "LOOP", "confidence": "likely",
        }]
        manifest["sections"] = [
            {"id": "sec-1", "name": "Intro", "bars": 2, "active_pattern_ids": ["pattern-a"]},
            {"id": "sec-2", "name": "Break", "bars": 1, "active_pattern_ids": [],
             "empty_policy": "rest"},
        ]
        manifest["tempo_bpm"] = 120
        manifest["meter"] = "4/4"
        self.assertEqual(self.run_validate(manifest).returncode, 0)

        unknown_span = v2_manifest()
        unknown_span["patterns"] = [{
            "id": "pattern-a", "name": "Rhythm A", "track": "Rhythm Guitar",
            "span_ids": ["no-such-span"], "bars": 2, "disposition": "LOOP",
        }]
        result = self.run_validate(unknown_span)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("span id not found", result.stderr)

        unknown_pattern = v2_manifest()
        unknown_pattern["patterns"] = [{
            "id": "pattern-a", "name": "Rhythm A", "track": "Rhythm Guitar",
            "span_ids": ["span-a"], "bars": 2, "disposition": "LOOP",
        }]
        unknown_pattern["sections"] = [
            {"id": "sec-1", "name": "Intro", "bars": 2, "active_pattern_ids": ["no-such-pattern"]},
        ]
        result = self.run_validate(unknown_pattern)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown pattern id", result.stderr)

        empty_section_needs_policy = v2_manifest()
        empty_section_needs_policy["patterns"] = [{
            "id": "pattern-a", "name": "Rhythm A", "track": "Rhythm Guitar",
            "span_ids": ["span-a"], "bars": 2, "disposition": "LOOP",
        }]
        empty_section_needs_policy["sections"] = [
            {"id": "sec-1", "name": "Break", "bars": 1, "active_pattern_ids": []},
        ]
        result = self.run_validate(empty_section_needs_policy)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty_policy", result.stderr)


class PerformancePlanValidateOnlyTests(unittest.TestCase):
    """Cross-validator agreement: a performance-plan compiled by
    compile_performance.py (Python) must also validate via write_arrangement.mjs
    (JS) --performance --validate-only, and legacy pre-foundation documents
    must be flagged as needing recompilation by both."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vp-perf-plan-validate-")
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    @staticmethod
    def _manifest(disposition="LOOP", decisions=None):
        manifest = {
            "version": 2, "tempo_bpm": 120, "meter": "4/4",
            "source": {"asset_relative_path": "source/song.gp", "asset_sha256": "a" * 64,
                      "format": "gpif"},
            "tracks": [{
                "name": "Rhythm Guitar", "role": "rhythm", "role_confidence": "likely",
                "spans": [{"id": "span-a", "disposition": disposition}],
            }],
            "patterns": [{"id": "pattern-a", "name": "Rhythm A", "track": "Rhythm Guitar",
                         "span_ids": ["span-a"], "bars": 2, "disposition": disposition}],
            "sections": [
                {"id": "sec-1", "name": "Intro", "bars": 2, "active_pattern_ids": ["pattern-a"]},
            ],
        }
        if decisions is not None:
            manifest["decisions"] = decisions
        return manifest

    def compile_plan(self, manifest, resolutions, performance):
        manifest_path = self.dir / "arrangement.json"
        resolutions_path = self.dir / "resolutions.json"
        performance_path = self.dir / "performance.json"
        output_path = self.dir / "performance-arrangement.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        resolutions_path.write_text(json.dumps(resolutions), encoding="utf-8")
        performance_path.write_text(json.dumps(performance), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-B", str(HERE / "compile_performance.py"),
             "--manifest", str(manifest_path), "--resolutions", str(resolutions_path),
             "--performance", str(performance_path), "--output", str(output_path)],
            capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return output_path

    def run_node_validate(self, performance_path):
        return subprocess.run(
            ["node", str(WRITER), "--performance", str(performance_path), "--validate-only"],
            capture_output=True, text=True, timeout=30, check=False)

    def test_python_compiled_final_plan_validates_in_node(self):
        manifest = self._manifest("LOOP")
        resolutions = []
        performance = {"status": "final", "limitations": []}
        output_path = self.compile_plan(manifest, resolutions, performance)
        result = self.run_node_validate(output_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "valid")
        self.assertEqual(payload["plan_version"], 2)

    def test_python_compiled_draft_plan_with_unresolved_validates_in_node(self):
        manifest = self._manifest("UNRESOLVED", decisions=[
            {"id": "decision-span-a", "question": "Preserve variant or use canonical loop?",
             "options": ["preserve_variants", "use_canonical", "defer_unresolved"]},
        ])
        resolutions = [{"decision_id": "decision-span-a", "option": "defer_unresolved"}]
        performance = {"status": "draft", "limitations": []}
        output_path = self.compile_plan(manifest, resolutions, performance)
        result = self.run_node_validate(output_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "valid")

    def test_node_rejects_building_gp_from_a_draft_or_unresolved_plan(self):
        manifest = self._manifest("UNRESOLVED", decisions=[
            {"id": "decision-span-a", "question": "Preserve variant or use canonical loop?",
             "options": ["preserve_variants", "use_canonical", "defer_unresolved"]},
        ])
        resolutions = [{"decision_id": "decision-span-a", "option": "defer_unresolved"}]
        performance = {"status": "draft", "limitations": []}
        output_path = self.compile_plan(manifest, resolutions, performance)
        song_dir = self.dir / "song"
        song_dir.mkdir()
        gp_output = self.dir / "arrangement.gp"
        result = subprocess.run(
            ["node", str(WRITER), "--performance", str(output_path),
             "--song-dir", str(song_dir), "--output", str(gp_output)],
            capture_output=True, text=True, timeout=30, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("draft previews are not buildable", result.stderr)

    def test_node_rejects_all_cue_partial_plan_after_readable_layout_pruning(self):
        """Consent permits Partial planning, but readable GP export still requires
        notated material; cue-only requirements remain in the worksheet.
        """
        manifest = self._manifest("UNRESOLVED", decisions=[
            {"id": "decision-span-a", "question": "Preserve variant or use canonical loop?",
             "options": ["preserve_variants", "use_canonical", "defer_unresolved"]},
        ])
        resolutions = [{"decision_id": "decision-span-a", "option": "defer_unresolved"}]
        performance = {"status": "partial", "limitations": ["one pattern deferred"],
                       "consent": "reviewed and accepted as a partial preview"}
        output_path = self.compile_plan(manifest, resolutions, performance)
        song_dir = self.dir / "song2"
        song_dir.mkdir()
        gp_output = self.dir / "arrangement2.gp"
        result = subprocess.run(
            ["node", str(WRITER), "--performance", str(output_path),
             "--song-dir", str(song_dir), "--output", str(gp_output)],
            capture_output=True, text=True, timeout=30, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("arrangement has no notated material", result.stderr)
        self.assertFalse(gp_output.exists())

    def test_node_rejects_building_gp_from_a_final_plan_retaining_unresolved(self):
        """A Final publication can never retain an UNRESOLVED decision; only a
        consented Partial may (see the test above). `compile_performance.py` itself
        already rejects this combination before the writer is ever invoked."""
        manifest_path = self.dir / "arrangement-final-unresolved.json"
        resolutions_path = self.dir / "resolutions-final-unresolved.json"
        performance_path = self.dir / "performance-final-unresolved.json"
        output_path = self.dir / "performance-arrangement-final-unresolved.json"
        manifest = self._manifest("UNRESOLVED", decisions=[
            {"id": "decision-span-a", "question": "Preserve variant or use canonical loop?",
             "options": ["preserve_variants", "use_canonical", "defer_unresolved"]},
        ])
        resolutions = [{"decision_id": "decision-span-a", "option": "defer_unresolved"}]
        performance = {"status": "final", "limitations": []}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        resolutions_path.write_text(json.dumps(resolutions), encoding="utf-8")
        performance_path.write_text(json.dumps(performance), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-B", str(HERE / "compile_performance.py"),
             "--manifest", str(manifest_path), "--resolutions", str(resolutions_path),
             "--performance", str(performance_path), "--output", str(output_path)],
            capture_output=True, text=True, timeout=30, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("UNRESOLVED", result.stderr)

    def test_legacy_pre_foundation_performance_document_flagged_by_node(self):
        manifest = self._manifest("LOOP")
        resolutions = []
        performance = {"status": "final", "limitations": []}
        output_path = self.compile_plan(manifest, resolutions, performance)
        legacy = json.loads(output_path.read_text(encoding="utf-8"))
        for key in ("plan_version", "fingerprints", "instruments", "section_instances",
                   "output_timeline", "removal_ledger", "placements", "variant_selections",
                   "tail_policy"):
            legacy.pop(key, None)
        legacy_path = self.dir / "legacy-performance-arrangement.json"
        legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
        result = self.run_node_validate(legacy_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("predates the performance-plan foundation", result.stderr)

    def test_python_compiled_plan_with_approved_section_deletion_validates_in_node(self):
        # Regression: write_arrangement.mjs's removal_ledger parser must accept the
        # exact shape compile_performance.py/v2_schema.py produce (section_instance_id,
        # name, removed_bars, decision_ref), not a stale (occurrence_index, reason) shape.
        manifest = {
            "version": 2, "tempo_bpm": 120, "meter": "4/4",
            "source": {"asset_relative_path": "source/song.gp", "asset_sha256": "a" * 64,
                      "format": "gpif"},
            "tracks": [{
                "name": "Rhythm Guitar", "role": "rhythm", "role_confidence": "likely",
                "spans": [{"id": "span-a", "disposition": "LOOP"}],
            }],
            "patterns": [{"id": "pattern-a", "name": "Rhythm A", "track": "Rhythm Guitar",
                         "span_ids": ["span-a"], "bars": 2, "disposition": "LOOP"}],
            "sections": [
                {"id": "sec-keep", "name": "Intro", "bars": 2, "active_pattern_ids": ["pattern-a"]},
                {"id": "sec-cut", "name": "Bridge", "bars": 2, "active_pattern_ids": ["pattern-a"]},
            ],
        }
        resolutions = {"section_actions": [
            {"section_id": "sec-cut", "action": "delete", "approved": True,
             "reason": "trim bridge for live set"},
        ]}
        performance = {"status": "final", "limitations": []}
        output_path = self.compile_plan(manifest, resolutions, performance)
        compiled = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(compiled["removal_ledger"], [{
            "section_instance_id": "sec-cut", "name": "Bridge", "removed_bars": 2,
            "decision_ref": "section_actions[sec-cut]",
        }])
        result = self.run_node_validate(output_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "valid")

    def test_python_compiled_plan_with_preserve_variants_validates_in_node(self):
        # Regression: write_arrangement.mjs's variant_selections parser must accept the
        # exact shape compile_performance.py/v2_schema.py produce (pattern_id,
        # occurrence_index, variant_id, source_ref), not a stale
        # (pattern_id, section_instance_id, variant_id) shape, and must validate the
        # pattern_id against known patterns.
        manifest = {
            "version": 2, "tempo_bpm": 120, "meter": "4/4",
            "source": {"asset_relative_path": "source/song.gp", "asset_sha256": "a" * 64,
                      "format": "gpif"},
            "tracks": [{
                "name": "Rhythm Guitar", "role": "rhythm", "role_confidence": "likely",
                "spans": [{"id": "span-a", "disposition": "UNRESOLVED"}],
            }],
            "patterns": [{
                "id": "pattern-a", "name": "Rhythm A", "track": "Rhythm Guitar",
                "span_ids": ["span-a"], "bars": 2, "disposition": "UNRESOLVED",
                "variants": [
                    {"id": "variant-1", "span_ids": ["span-a"], "occurrence_index": 1,
                     "is_canonical": True},
                    {"id": "variant-2", "span_ids": ["span-a"], "occurrence_index": 2},
                ],
            }],
            "sections": [
                {"id": "sec-1", "name": "Intro", "bars": 2, "active_pattern_ids": ["pattern-a"]},
            ],
            "decisions": [
                {"id": "decision-span-a", "question": "Preserve variants or use canonical loop?",
                 "options": ["preserve_variants", "use_canonical"]},
            ],
        }
        resolutions = {
            "decisions": [{"decision_id": "decision-span-a", "option": "preserve_variants"}],
            "variant_selections": [
                {"pattern_id": "pattern-a", "occurrence_index": 1, "variant_id": "variant-1"},
                {"pattern_id": "pattern-a", "occurrence_index": 2, "variant_id": "variant-2"},
            ],
        }
        performance = {"status": "final", "limitations": []}
        output_path = self.compile_plan(manifest, resolutions, performance)
        compiled = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(len(compiled["variant_selections"]), 2)
        for selection in compiled["variant_selections"]:
            self.assertEqual(set(selection), {"pattern_id", "occurrence_index", "variant_id",
                                              "source_ref"})
        result = self.run_node_validate(output_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "valid")


class PerformancePlanWorksheetParityTests(unittest.TestCase):
    """Requirement 5/6: `verifyV2`'s optional `--worksheet` cross-check catches a
    worksheet.md that has drifted from the compiled plan + just-built GP file, and
    requirement 6's deterministic GP track-naming convention disambiguates duplicate
    analysis-track display names rather than silently colliding."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vp-worksheet-parity-")
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    @staticmethod
    def _duplicate_name_manifest():
        # Two distinct tracks share the display name "Guitar" -- stable ids
        # (track-guitar-1/2) are the real identity; GP track names must disambiguate.
        return {
            "version": 2, "tempo_bpm": 120, "meter": "4/4",
            "source": {"asset_relative_path": "source/song.gp", "asset_sha256": "a" * 64,
                      "format": "gpif"},
            "tracks": [
                {"id": "track-guitar-1", "name": "Guitar",
                 "spans": [{"id": "g1-solo", "disposition": "LIVE"}]},
                {"id": "track-guitar-2", "name": "Guitar",
                 "spans": [{"id": "g2-solo", "disposition": "LIVE"}]},
                {"id": "track-keys", "name": "Keys",
                 "spans": [{"id": "keys-fill", "disposition": "OPTIONAL"}]},
            ],
            "patterns": [
                {"id": "pattern-g1", "name": "Guitar One Solo", "track": "Guitar",
                 "track_id": "track-guitar-1",
                 "span_ids": ["g1-solo"], "bars": 2, "disposition": "LIVE"},
                {"id": "pattern-g2", "name": "Guitar Two Solo", "track": "Guitar",
                 "track_id": "track-guitar-2",
                 "span_ids": ["g2-solo"], "bars": 2, "disposition": "LIVE"},
                {"id": "pattern-keys-fill", "name": "Keys Fill", "track": "Keys",
                 "span_ids": ["keys-fill"], "bars": 2, "disposition": "OPTIONAL",
                 "retain_role": "live"},
            ],
            "sections": [
                {"id": "sec-1", "name": "Verse", "bars": 2,
                 "active_pattern_ids": ["pattern-g1", "pattern-g2", "pattern-keys-fill"]},
            ],
        }

    def _compile_and_render(self, manifest, resolutions=None, publication=None):
        from compile_performance import compile_performance_arrangement
        from render_views import render_published

        resolutions = resolutions if resolutions is not None else {}
        publication = publication or {"status": "final", "limitations": []}
        output, errors = compile_performance_arrangement(manifest, resolutions, publication)
        self.assertEqual(errors, [])
        # These worksheet-contract tests intentionally exercise the legacy
        # cue-track layout. Readable-layout pruning has dedicated exporter tests.
        output.pop("export_layout", None)
        plan_path = self.dir / "performance-arrangement.json"
        plan_path.write_text(json.dumps(output), encoding="utf-8")
        song_dir = self.dir / "song"
        song_dir.mkdir(exist_ok=True)
        render_result = render_published(plan_path, song_dir, replace=False, title="Song")
        self.assertEqual(render_result["status"], "written", render_result)
        return plan_path, song_dir, song_dir / "worksheet.md", output

    def run_node_build(self, plan_path, song_dir, output_path, worksheet_path=None):
        args = ["node", str(WRITER), "--performance", str(plan_path),
               "--song-dir", str(song_dir), "--output", str(output_path)]
        if worksheet_path is not None:
            args += ["--worksheet", str(worksheet_path)]
        return subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)

    def test_duplicate_display_names_get_disambiguated_gp_track_names(self):
        manifest = self._duplicate_name_manifest()
        plan_path, song_dir, worksheet_path, output = self._compile_and_render(manifest)
        gp_output = self.dir / "arrangement.gp"
        result = self.run_node_build(plan_path, song_dir, gp_output, worksheet_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(gp_output)
        self.assertEqual(actual["status"], "valid")
        names = [t["name"] for t in actual["tracks"]]
        # Guitar's two instruments are ambiguous (same display name) -> disambiguated by
        # instrument id; Keys is unambiguous -> unchanged `${name} (${role})` convention.
        guitar_names = [n for n in names if n.startswith("Guitar ")]
        self.assertEqual(len(guitar_names), len(set(guitar_names)),
                         f"ambiguous Guitar track names were not disambiguated: {guitar_names}")
        self.assertTrue(any("instrument-guitar-track-guitar-1" in n for n in names), names)
        self.assertTrue(any("instrument-guitar-track-guitar-2" in n for n in names), names)
        self.assertTrue(any(n.startswith("Keys (") and "instrument-" not in n for n in names),
                        names)

    def test_worksheet_parity_passes_for_an_unmodified_rendered_worksheet(self):
        manifest = self._duplicate_name_manifest()
        plan_path, song_dir, worksheet_path, output = self._compile_and_render(manifest)
        gp_output = self.dir / "arrangement.gp"
        result = self.run_node_build(plan_path, song_dir, gp_output, worksheet_path)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_optional_marker_is_present_in_both_worksheet_and_gp(self):
        manifest = self._duplicate_name_manifest()
        plan_path, song_dir, worksheet_path, output = self._compile_and_render(manifest)
        self.assertIn("[OPTIONAL]", worksheet_path.read_text(encoding="utf-8"))
        gp_output = self.dir / "arrangement.gp"
        result = self.run_node_build(plan_path, song_dir, gp_output, worksheet_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(gp_output) as zf:
            score_xml = zf.read("Content/score.gpif").decode("utf-8")
        self.assertIn("OPTIONAL", score_xml)

    def test_unselected_live_span_exports_visible_cue_and_round_trips(self):
        manifest = {
            "version": 2, "tempo_bpm": 120, "meter": "4/4",
            "source": {"asset_relative_path": "source/not-needed.gp",
                       "asset_sha256": "d" * 64, "format": "gpif"},
            "tracks": [{"id": "track-guitar", "name": "Guitar", "spans": [
                {"id": "guitar-live", "disposition": "LIVE",
                 "source_ref": {"asset_sha256": "d" * 64,
                                "asset_relative_path": "source/not-needed.gp",
                                "written_master_bars": [1, 2]}},
                {"id": "guitar-rest", "disposition": "REST"},
            ]}],
            "patterns": [{"id": "pattern-rest", "name": "Rest", "track": "Guitar",
                          "span_ids": ["guitar-rest"], "bars": 1,
                          "disposition": "REST"}],
            "sections": [{"id": "sec-live", "name": "Live", "bars": 2,
                          "active_pattern_ids": [], "empty_policy": "live"}],
        }
        plan_path, song_dir, worksheet_path, output = self._compile_and_render(manifest)
        placement = output["placements"][0]
        self.assertEqual(placement["material_id"], "guitar-live")
        self.assertEqual(placement["output_track_id"], "instrument-guitar-live")
        worksheet = worksheet_path.read_text(encoding="utf-8")
        self.assertIn("LIVE `instrument-guitar-live`", worksheet)
        self.assertIn("manual cue: bars 1-2", worksheet)

        gp_output = self.dir / "live-cue.gp"
        result = self.run_node_build(plan_path, song_dir, gp_output, worksheet_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(gp_output)
        self.assertEqual((actual["status"], actual["bar_count"]), ("valid", 2))
        with zipfile.ZipFile(gp_output) as zf:
            score_xml = zf.read("Content/score.gpif").decode("utf-8")
        self.assertIn("LIVE: bars 1-2", score_xml)

    def test_worksheet_missing_live_disclosure_fails_parity(self):
        manifest = self._duplicate_name_manifest()
        plan_path, song_dir, worksheet_path, output = self._compile_and_render(manifest)
        text = worksheet_path.read_text(encoding="utf-8")
        self.assertIn("manual cue: bars 1-2", text)
        worksheet_path.write_text(text.replace("manual cue: bars 1-2", "hidden"),
                                  encoding="utf-8")
        gp_output = self.dir / "missing-live-disclosure.gp"
        result = self.run_node_build(plan_path, song_dir, gp_output, worksheet_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing LIVE disclosure", result.stderr)
        self.assertFalse(gp_output.exists())

    def test_worksheet_missing_optional_marker_fails_parity(self):
        manifest = self._duplicate_name_manifest()
        plan_path, song_dir, worksheet_path, output = self._compile_and_render(manifest)
        text = worksheet_path.read_text(encoding="utf-8")
        self.assertIn("[OPTIONAL]", text)
        worksheet_path.write_text(text.replace("[OPTIONAL]", ""), encoding="utf-8")
        gp_output = self.dir / "arrangement.gp"
        result = self.run_node_build(plan_path, song_dir, gp_output, worksheet_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[OPTIONAL]", result.stderr)
        self.assertFalse(gp_output.exists(), "a failed verify must never publish the .gp file")

    def test_worksheet_status_mismatch_fails_parity(self):
        manifest = self._duplicate_name_manifest()
        plan_path, song_dir, worksheet_path, output = self._compile_and_render(manifest)
        text = worksheet_path.read_text(encoding="utf-8")
        worksheet_path.write_text(text.replace("**Status: Final**", "**Status: Partial**"),
                                  encoding="utf-8")
        gp_output = self.dir / "arrangement.gp"
        result = self.run_node_build(plan_path, song_dir, gp_output, worksheet_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Status: Final", result.stderr)
        self.assertFalse(gp_output.exists())

    def test_worksheet_missing_track_id_fails_parity(self):
        manifest = self._duplicate_name_manifest()
        plan_path, song_dir, worksheet_path, output = self._compile_and_render(manifest)
        text = worksheet_path.read_text(encoding="utf-8")
        corrupted = text.replace("instrument-keys-live", "deleted-track-id")
        self.assertNotEqual(corrupted, text)
        worksheet_path.write_text(corrupted, encoding="utf-8")
        gp_output = self.dir / "arrangement.gp"
        result = self.run_node_build(plan_path, song_dir, gp_output, worksheet_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing output track id", result.stderr)
        self.assertFalse(gp_output.exists())

    def test_missing_worksheet_file_fails_parity(self):
        manifest = self._duplicate_name_manifest()
        plan_path, song_dir, worksheet_path, output = self._compile_and_render(manifest)
        missing = self.dir / "does-not-exist.md"
        gp_output = self.dir / "arrangement.gp"
        result = self.run_node_build(plan_path, song_dir, gp_output, missing)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exist", result.stderr)
        self.assertFalse(gp_output.exists())

    def test_build_without_worksheet_flag_is_unaffected(self):
        """The `--worksheet` flag is opt-in; omitting it must build/verify exactly as
        it did before this feature existed (requirement 5's "preserve" expectation)."""
        manifest = self._duplicate_name_manifest()
        plan_path, song_dir, worksheet_path, output = self._compile_and_render(manifest)
        gp_output = self.dir / "arrangement.gp"
        result = self.run_node_build(plan_path, song_dir, gp_output, worksheet_path=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(gp_output)
        self.assertEqual(actual["status"], "valid")


if __name__ == "__main__":
    unittest.main()
