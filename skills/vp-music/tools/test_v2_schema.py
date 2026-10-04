"""Hermetic tests for the shared v2 analysis-manifest schema validator."""

import unittest

from v2_schema import (
    Invalid, canonical_sha256, is_legacy_v2_performance, load_analysis_manifest,
    validate_performance_plan, validate_resolutions,
)


def minimal_manifest(disposition="LOOP", **overrides):
    manifest = {
        "version": 2,
        "source": {
            "asset_relative_path": "source/song.gp",
            "asset_sha256": "a" * 64,
            "format": "gpif",
        },
        "tracks": [{
            "name": "Rhythm Guitar",
            "spans": [{"id": "span-a", "disposition": disposition}],
        }],
    }
    manifest.update(overrides)
    return manifest


class SchemaValidationTests(unittest.TestCase):
    def test_accepts_minimal_valid_manifest(self):
        load_analysis_manifest(minimal_manifest())

    def test_all_five_dispositions_accepted(self):
        for disposition in ("LOOP", "LIVE", "OPTIONAL", "REST"):
            load_analysis_manifest(minimal_manifest(disposition))
        load_analysis_manifest(minimal_manifest("UNRESOLVED", decisions=[
            {"id": "d1", "question": "Preserve or canonicalize?",
             "options": ["preserve_variants", "use_canonical"]},
        ]))

    def test_unresolved_without_decisions_rejected(self):
        with self.assertRaises(Invalid):
            load_analysis_manifest(minimal_manifest("UNRESOLVED"))

    def test_unknown_top_level_field_rejected(self):
        manifest = minimal_manifest()
        manifest["bogus"] = 1
        with self.assertRaises(Invalid):
            load_analysis_manifest(manifest)

    def test_bad_sha256_rejected(self):
        manifest = minimal_manifest()
        manifest["source"]["asset_sha256"] = "not-a-hash"
        with self.assertRaises(Invalid):
            load_analysis_manifest(manifest)

    def test_bad_capability_rejected(self):
        manifest = minimal_manifest()
        manifest["source"]["capabilities"] = ["bogus"]
        with self.assertRaises(Invalid):
            load_analysis_manifest(manifest)

    def test_depends_on_requires_valid_parent_option(self):
        manifest = minimal_manifest("UNRESOLVED", decisions=[
            {"id": "d1", "question": "Q1?", "options": ["a", "b"]},
            {"id": "d2", "depends_on": "d1", "applies_when": "not-an-option",
             "question": "Q2?", "options": ["x", "y"]},
        ])
        with self.assertRaises(Invalid):
            load_analysis_manifest(manifest)

    def test_pattern_span_reference_integrity(self):
        manifest = minimal_manifest(patterns=[{
            "id": "p1", "name": "Pattern A", "track": "Rhythm Guitar",
            "span_ids": ["no-such-span"], "bars": 1, "disposition": "LOOP",
        }])
        with self.assertRaises(Invalid):
            load_analysis_manifest(manifest)

    def test_section_active_pattern_reference_integrity(self):
        manifest = minimal_manifest(
            patterns=[{"id": "p1", "name": "Pattern A", "track": "Rhythm Guitar",
                      "span_ids": ["span-a"], "bars": 1, "disposition": "LOOP"}],
            sections=[{"id": "s1", "name": "Intro", "bars": 1, "active_pattern_ids": ["missing"]}],
        )
        with self.assertRaises(Invalid):
            load_analysis_manifest(manifest)

    def test_pattern_variants_accepted_with_referential_integrity(self):
        manifest = minimal_manifest(
            disposition="UNRESOLVED",
            decisions=[{"id": "d1", "question": "Preserve or canonicalize?",
                       "options": ["preserve_variants", "use_canonical"]}],
            patterns=[{"id": "p1", "name": "Pattern A", "track": "Rhythm Guitar",
                      "span_ids": ["span-a"], "bars": 1, "disposition": "UNRESOLVED",
                      "variants": [{"id": "v1", "span_ids": ["span-a"], "occurrence_index": 1,
                                   "is_canonical": True}]}],
        )
        load_analysis_manifest(manifest)

    def test_pattern_variant_unknown_span_is_rejected(self):
        manifest = minimal_manifest(patterns=[{
            "id": "p1", "name": "Pattern A", "track": "Rhythm Guitar",
            "span_ids": ["span-a"], "bars": 1, "disposition": "LOOP",
            "variants": [{"id": "v1", "span_ids": ["no-such-span"]}],
        }])
        with self.assertRaises(Invalid):
            load_analysis_manifest(manifest)

    def test_pattern_variants_at_most_one_canonical(self):
        manifest = minimal_manifest(patterns=[{
            "id": "p1", "name": "Pattern A", "track": "Rhythm Guitar",
            "span_ids": ["span-a"], "bars": 1, "disposition": "LOOP",
            "variants": [{"id": "v1", "span_ids": ["span-a"], "is_canonical": True},
                        {"id": "v2", "span_ids": ["span-a"], "is_canonical": True}],
        }])
        with self.assertRaises(Invalid):
            load_analysis_manifest(manifest)


class ResolutionValidationTests(unittest.TestCase):
    def test_all_applicable_decisions_resolved_is_valid(self):
        manifest = {
            "decisions": [
                {"id": "d1", "question": "Q1?", "options": ["use_canonical", "play_live"]},
                {"id": "d2", "depends_on": "d1", "applies_when": "use_canonical",
                 "question": "Q2?", "options": ["keep_loop", "omit"]},
            ],
        }
        resolutions = [{"decision_id": "d1", "option": "use_canonical"},
                      {"decision_id": "d2", "option": "keep_loop"}]
        _, errors = validate_resolutions(manifest, resolutions)
        self.assertEqual(errors, [])

    def test_followup_not_required_when_parent_chooses_other_branch(self):
        manifest = {
            "decisions": [
                {"id": "d1", "question": "Q1?", "options": ["use_canonical", "play_live"]},
                {"id": "d2", "depends_on": "d1", "applies_when": "use_canonical",
                 "question": "Q2?", "options": ["keep_loop", "omit"]},
            ],
        }
        resolutions = [{"decision_id": "d1", "option": "play_live"}]
        _, errors = validate_resolutions(manifest, resolutions)
        self.assertEqual(errors, [])

    def test_missing_required_resolution_reported(self):
        manifest = {"decisions": [{"id": "d1", "question": "Q1?", "options": ["a", "b"]}]}
        _, errors = validate_resolutions(manifest, [])
        self.assertTrue(errors)

    def test_resolution_with_invalid_option_reported(self):
        manifest = {"decisions": [{"id": "d1", "question": "Q1?", "options": ["a", "b"]}]}
        _, errors = validate_resolutions(manifest, [{"decision_id": "d1", "option": "c"}])
        self.assertTrue(errors)


class FingerprintTests(unittest.TestCase):
    def test_canonical_sha256_is_deterministic_and_key_order_independent(self):
        a = canonical_sha256({"x": 1, "y": 2})
        b = canonical_sha256({"y": 2, "x": 1})
        self.assertEqual(a, b)
        self.assertRegex(a, r"^[0-9a-f]{64}$")

    def test_canonical_sha256_changes_with_content(self):
        self.assertNotEqual(canonical_sha256({"x": 1}), canonical_sha256({"x": 2}))

    def test_whole_valued_float_normalized_to_integer_text(self):
        # 120.0 must fingerprint identically to 120: this is the exact cross-language
        # divergence (Python `json.dumps(120.0)` == "120.0" vs JS `JSON.stringify(120.0)`
        # == "120") that broke fingerprint parity; the canonical encoder must normalize
        # it away on the Python side.
        self.assertEqual(canonical_sha256({"tempo_bpm": 120.0}),
                          canonical_sha256({"tempo_bpm": 120}))
        self.assertNotEqual(canonical_sha256({"tempo_bpm": 120.0}),
                             canonical_sha256({"tempo_bpm": 120.5}))

    def test_non_finite_float_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(Invalid):
                canonical_sha256({"x": bad})

    def test_is_legacy_v2_performance_detects_missing_plan_version(self):
        self.assertTrue(is_legacy_v2_performance(
            {"version": 2, "publication": {}, "disclosure": {}}))
        self.assertFalse(is_legacy_v2_performance(
            {"version": 2, "plan_version": 2, "publication": {}, "disclosure": {}}))
        self.assertFalse(is_legacy_v2_performance({"version": 1}))


def _minimal_plan(**overrides):
    plan = {
        "plan_version": 2,
        "fingerprints": {"analysis_sha256": "a" * 64, "resolutions_sha256": "b" * 64},
        "tracks": [{"name": "Rhythm Guitar"}],
        "patterns": [{"id": "pattern-a"}],
        "sections": [{"id": "sec-1"}],
        "instruments": [{
            "id": "instrument-rhythm-guitar", "analysis_track": "Rhythm Guitar",
            "output_tracks": [
                {"id": "instrument-rhythm-guitar-live", "role": "live"},
                {"id": "instrument-rhythm-guitar-loop-a", "role": "loop", "pattern_id": "pattern-a"},
                {"id": "instrument-rhythm-guitar-unresolved", "role": "unresolved"},
            ],
        }],
        "section_instances": [{
            "id": "sec-1", "section_id": "sec-1", "name": "Intro", "occurrence_index": 1,
            "action": "retain", "approved": False, "omitted_layers": [],
        }],
        "output_timeline": [{"output_index": 0, "section_instance_id": "sec-1",
                             "source_section_bar": 0}],
        "removal_ledger": [],
        "placements": [{"output_track_id": "instrument-rhythm-guitar-loop-a",
                        "output_bar_range": [0, 0], "material_id": "pattern-a",
                        "mode": "loop", "optional": False}],
        "variant_selections": [],
        "tail_policy": None,
    }
    plan.update(overrides)
    return plan


class PerformancePlanValidationTests(unittest.TestCase):
    def test_minimal_plan_is_accepted(self):
        validate_performance_plan(_minimal_plan())

    def test_versioned_readable_export_layout_is_accepted(self):
        plan = _minimal_plan(export_layout={
            "version": 1,
            "prune_zero_note_tracks": True,
            "group_order": ["core", "optional", "unresolved"],
            "colors": {"drums": "#0000FF", "bass": "#FFFF00", "other": "#FF0000"},
        })
        validate_performance_plan(plan)

    def test_readable_export_layout_rejects_noncanonical_policy(self):
        plan = _minimal_plan(export_layout={
            "version": 1,
            "prune_zero_note_tracks": False,
            "group_order": ["core", "unresolved", "optional"],
            "colors": {"drums": "#0000FF", "bass": "#FFFF00", "other": "#FF0000"},
        })
        with self.assertRaises(Invalid):
            validate_performance_plan(plan)

    def test_wrong_plan_version_rejected(self):
        with self.assertRaises(Invalid):
            validate_performance_plan(_minimal_plan(plan_version=1))

    def test_instrument_output_tracks_require_live_first_and_unresolved_last(self):
        plan = _minimal_plan()
        plan["instruments"][0]["output_tracks"] = list(reversed(plan["instruments"][0]["output_tracks"]))
        with self.assertRaises(Invalid):
            validate_performance_plan(plan)

    def test_instrument_requires_exactly_one_live_and_one_unresolved_track(self):
        plan = _minimal_plan()
        plan["instruments"][0]["output_tracks"] = [
            {"id": "x-live", "role": "live"},
            {"id": "x-live2", "role": "live"},
            {"id": "x-unresolved", "role": "unresolved"},
        ]
        with self.assertRaises(Invalid):
            validate_performance_plan(plan)

    def test_section_instance_delete_requires_approved_true(self):
        plan = _minimal_plan()
        plan["section_instances"][0]["action"] = "delete"
        plan["section_instances"][0]["approved"] = False
        with self.assertRaises(Invalid):
            validate_performance_plan(plan)

    def test_output_timeline_must_be_contiguous(self):
        plan = _minimal_plan()
        plan["output_timeline"] = [{"output_index": 1, "section_instance_id": "sec-1",
                                    "source_section_bar": 0}]
        with self.assertRaises(Invalid):
            validate_performance_plan(plan)

    def test_placement_bar_range_must_be_within_timeline_bounds(self):
        plan = _minimal_plan()
        plan["placements"][0]["output_bar_range"] = [0, 5]
        with self.assertRaises(Invalid):
            validate_performance_plan(plan)

    def test_loop_output_track_requires_pattern_id_and_non_loop_forbids_it(self):
        plan = _minimal_plan()
        plan["instruments"][0]["output_tracks"][1].pop("pattern_id")
        with self.assertRaises(Invalid):
            validate_performance_plan(plan)
        plan2 = _minimal_plan()
        plan2["instruments"][0]["output_tracks"][0]["pattern_id"] = "pattern-a"
        with self.assertRaises(Invalid):
            validate_performance_plan(plan2)


if __name__ == "__main__":
    unittest.main()
