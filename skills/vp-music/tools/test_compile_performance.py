"""Hermetic tests for the v2 performance-arrangement compiler."""

import json
from pathlib import Path
import tempfile
import unittest

from compile_performance import compile_performance_arrangement, main


def base_manifest():
    return {
        "version": 2,
        "tempo_bpm": 120,
        "meter": "4/4",
        "source": {
            "asset_relative_path": "source/song.gp",
            "asset_sha256": "a" * 64,
            "format": "gpif",
        },
        "tracks": [
            {
                "name": "Rhythm Guitar",
                "role": "rhythm",
                "role_confidence": "likely",
                "spans": [
                    {"id": "rhythm-verse-a", "disposition": "UNRESOLVED"},
                    {"id": "rhythm-outro-rest", "disposition": "REST"},
                ],
            },
        ],
        "decisions": [
            {
                "id": "decision-rhythm-verse-a",
                "question": "Preserve the fourth-bar variation or use the common loop?",
                "options": ["preserve_variants", "use_canonical", "play_live"],
            },
            {
                "id": "decision-rhythm-verse-a-followup",
                "depends_on": "decision-rhythm-verse-a",
                "applies_when": "use_canonical",
                "question": "Keep in rotation, or omit?",
                "options": ["keep_loop", "omit"],
            },
        ],
        "patterns": [
            {"id": "pattern-rhythm-a", "name": "Rhythm A", "track": "Rhythm Guitar",
             "span_ids": ["rhythm-verse-a"], "bars": 2, "disposition": "UNRESOLVED",
             "confidence": "likely"},
        ],
        "sections": [
            {"id": "sec-1", "name": "Intro", "bars": 2, "active_pattern_ids": ["pattern-rhythm-a"]},
        ],
    }


FINAL_PUBLICATION = {"status": "final", "limitations": [],
                     "performance": {"players": 1, "roles": ["Player 1 performs rhythm guitar"]}}


def multi_instrument_manifest():
    """Hermetic, original fixture covering: two source-backed near-loop
    variants on one instrument, a live-only instrument, an optional section
    instance, an unresolved span/pattern, duplicate section display names
    with distinct stable ids, and a partial-loop-cycle tail policy gap --
    the scenarios the previous (pre-contract) compiler could not represent
    distinctly (see module docstring / CONTRACT.md for the narrative).
    """
    return {
        "version": 2,
        "tempo_bpm": 100,
        "meter": "4/4",
        "source": {
            "asset_relative_path": "source/song.gp",
            "asset_sha256": "b" * 64,
            "format": "gpif",
            "capabilities": ["source_bar_reuse"],
        },
        "tracks": [
            {
                "id": "track-rhythm-guitar",
                "name": "Rhythm Guitar",
                "spans": [
                    {"id": "rg-verse-1", "disposition": "UNRESOLVED",
                     "source_ref": {"asset_sha256": "b" * 64,
                                    "asset_relative_path": "source/song.gp",
                                    "track_index": 0, "written_master_bars": [1, 2]}},
                    {"id": "rg-verse-2", "disposition": "UNRESOLVED",
                     "near_match": {"matching_bars": 1, "total_bars": 2,
                                   "divergent_bar_offsets": [1]},
                     "source_ref": {"asset_sha256": "b" * 64,
                                    "asset_relative_path": "source/song.gp",
                                    "track_index": 0, "written_master_bars": [9, 10]}},
                ],
            },
            {
                "id": "track-lead-vocal",
                "name": "Lead Vocal",
                "spans": [{"id": "lv-solo", "disposition": "LIVE"}],
            },
            {
                "id": "track-keys",
                "name": "Keys",
                "spans": [{"id": "keys-bridge", "disposition": "OPTIONAL"},
                         {"id": "keys-fill", "disposition": "UNRESOLVED"}],
            },
            {
                "id": "track-bass",
                "name": "Bass",
                "spans": [{"id": "bass-chorus", "disposition": "LOOP"}],
            },
        ],
        "decisions": [
            {"id": "decision-rg-verse-1",
             "question": "Preserve the distinct verse variants or use one canonical loop?",
             "options": ["preserve_variants", "use_canonical"]},
            {"id": "decision-keys-fill",
             "question": "Resolve the keys fill now, or defer it to a labeled preview?",
             "options": ["play_live", "defer_unresolved"]},
        ],
        "patterns": [
            {"id": "pattern-rhythm", "name": "Rhythm Verse", "track": "Rhythm Guitar",
             "span_ids": ["rg-verse-1", "rg-verse-2"], "bars": 2, "disposition": "UNRESOLVED",
             "variants": [
                 {"id": "variant-rhythm-occ1", "span_ids": ["rg-verse-1"], "occurrence_index": 1},
                 {"id": "variant-rhythm-occ2", "span_ids": ["rg-verse-2"], "occurrence_index": 2},
                 {"id": "variant-rhythm-canon", "span_ids": ["rg-verse-1"], "is_canonical": True},
             ]},
            {"id": "pattern-lead-live", "name": "Lead Solo", "track": "Lead Vocal",
             "span_ids": ["lv-solo"], "bars": 4, "disposition": "LIVE"},
            {"id": "pattern-keys-optional", "name": "Keys Bridge", "track": "Keys",
             "span_ids": ["keys-bridge"], "bars": 4, "disposition": "OPTIONAL"},
            {"id": "pattern-keys-fill", "name": "Keys Fill", "track": "Keys",
             "span_ids": ["keys-fill"], "bars": 1, "disposition": "UNRESOLVED"},
            {"id": "pattern-bass", "name": "Bass Chorus", "track": "Bass",
             "span_ids": ["bass-chorus"], "bars": 3, "disposition": "LOOP"},
        ],
        "sections": [
            {"id": "sec-verse-1", "name": "Verse", "bars": 2,
             "active_pattern_ids": ["pattern-rhythm"]},
            {"id": "sec-verse-2", "name": "Verse", "bars": 2,
             "active_pattern_ids": ["pattern-rhythm"]},
            {"id": "sec-solo", "name": "Solo", "bars": 4,
             "active_pattern_ids": ["pattern-lead-live"]},
            {"id": "sec-bridge", "name": "Bridge", "bars": 4,
             "active_pattern_ids": ["pattern-keys-optional", "pattern-keys-fill"]},
            {"id": "sec-chorus", "name": "Chorus", "bars": 8,
             "active_pattern_ids": ["pattern-bass"]},
        ],
    }


def multi_instrument_resolutions(*, rhythm_option="preserve_variants", keys_fill_option="play_live"):
    return {
        "decisions": [
            {"decision_id": "decision-rg-verse-1", "option": rhythm_option},
            {"decision_id": "decision-keys-fill", "option": keys_fill_option},
        ],
        "variant_selections": [
            {"pattern_id": "pattern-rhythm", "occurrence_index": 1,
             "variant_id": "variant-rhythm-occ1"},
            {"pattern_id": "pattern-rhythm", "occurrence_index": 2,
             "variant_id": "variant-rhythm-occ2"},
        ] if rhythm_option == "preserve_variants" else [],
    }


class MultiInstrumentFixtureTests(unittest.TestCase):
    """Positive coverage for the performance-plan foundation across every
    scenario named in the finalize-contract slice."""

    def _compile(self, *, status="partial", rhythm_option="preserve_variants",
                keys_fill_option="play_live", tail_policy="reviewed_truncate", extra_limitations=()):
        manifest = multi_instrument_manifest()
        resolutions = multi_instrument_resolutions(rhythm_option=rhythm_option,
                                                   keys_fill_option=keys_fill_option)
        if status == "final":
            publication = {"status": "final", "limitations": [], "tail_policy": tail_policy}
        else:
            publication = {"status": status,
                           "limitations": ["Bass chorus loop ends with a partial cycle",
                                           *extra_limitations],
                           "consent": "User accepted the labeled preview/partial gaps.",
                           "tail_policy": tail_policy}
        return compile_performance_arrangement(manifest, resolutions, publication)

    def test_two_near_loop_variants_select_distinct_source_material(self):
        output, errors = self._compile()
        self.assertEqual(errors, [])
        selections = {(s["occurrence_index"]): s["variant_id"] for s in output["variant_selections"]}
        self.assertEqual(selections, {1: "variant-rhythm-occ1", 2: "variant-rhythm-occ2"})
        # The two occurrences selected genuinely distinct declared variants,
        # not merely the same canonical material relabeled twice.
        self.assertNotEqual(selections[1], selections[2])

    def test_use_canonical_does_not_require_variant_selections(self):
        output, errors = self._compile(rhythm_option="use_canonical")
        self.assertEqual(errors, [])
        self.assertEqual(output["variant_selections"], [])
        self.assertEqual(output["patterns"][0]["final_disposition"], "LOOP")

    def test_preserve_variants_without_declared_variants_is_an_actionable_gap(self):
        manifest = multi_instrument_manifest()
        del manifest["patterns"][0]["variants"]
        resolutions = multi_instrument_resolutions()
        publication = {"status": "partial", "limitations": ["x"], "consent": "ok",
                      "tail_policy": "reviewed_truncate"}
        output, errors = compile_performance_arrangement(manifest, resolutions, publication)
        self.assertIsNone(output)
        self.assertTrue(any("declares no variants" in e for e in errors), errors)

    def test_live_only_instrument_gets_only_used_live_track(self):
        output, errors = self._compile()
        self.assertEqual(errors, [])
        lead = next(i for i in output["instruments"] if i["analysis_track"] == "track-lead-vocal")
        roles = [t["role"] for t in lead["output_tracks"]]
        self.assertEqual(roles, ["live"])
        placement = next(p for p in output["placements"]
                         if p["output_track_id"] == f"{lead['id']}-live")
        self.assertEqual(placement["mode"], "live")

    def test_every_compiled_output_track_has_a_placement(self):
        output, errors = self._compile()
        self.assertEqual(errors, [])
        placed = {placement["output_track_id"] for placement in output["placements"]}
        declared = {
            track["id"]
            for instrument in output["instruments"]
            for track in instrument["output_tracks"]
        }
        self.assertEqual(declared, placed)

    def test_optional_section_pattern_produces_no_physical_placement_but_is_disclosed(self):
        output, errors = self._compile()
        self.assertEqual(errors, [])
        self.assertIn("pattern-keys-optional", output["disclosure"]["omitted_patterns"])
        self.assertFalse(any(p["material_id"] == "pattern-keys-optional" for p in output["placements"]))

    def test_unresolved_pattern_is_labeled_and_blocked_only_in_final(self):
        output, errors = self._compile(keys_fill_option="defer_unresolved")
        self.assertEqual(errors, [])
        self.assertIn("pattern-keys-fill", output["disclosure"]["unresolved_patterns"])
        keys = next(i for i in output["instruments"] if i["analysis_track"] == "track-keys")
        unresolved_track = next(t for t in keys["output_tracks"] if t["role"] == "unresolved")
        placement = next(p for p in output["placements"]
                         if p["output_track_id"] == unresolved_track["id"])
        self.assertEqual(placement["mode"], "unresolved")

        final_output, final_errors = self._compile(status="final", keys_fill_option="defer_unresolved")
        self.assertIsNone(final_output)
        self.assertTrue(any("UNRESOLVED" in e for e in final_errors), final_errors)

    def test_draft_status_also_permits_labeled_unresolved(self):
        manifest = multi_instrument_manifest()
        resolutions = multi_instrument_resolutions(keys_fill_option="defer_unresolved")
        publication = {"status": "draft", "limitations": [], "tail_policy": "reviewed_truncate"}
        output, errors = compile_performance_arrangement(manifest, resolutions, publication)
        self.assertEqual(errors, [])
        self.assertEqual(output["publication"]["status"], "draft")
        self.assertIn("pattern-keys-fill", output["disclosure"]["unresolved_patterns"])

    def test_duplicate_section_display_names_get_distinct_stable_instance_ids(self):
        output, errors = self._compile()
        self.assertEqual(errors, [])
        verses = [si for si in output["section_instances"] if si["name"] == "Verse"]
        self.assertEqual(len(verses), 2)
        self.assertEqual({v["id"] for v in verses}, {"sec-verse-1", "sec-verse-2"})
        self.assertEqual(sorted(v["occurrence_index"] for v in verses), [1, 2])

    def test_partial_loop_cycle_requires_explicit_tail_policy(self):
        manifest = multi_instrument_manifest()
        resolutions = multi_instrument_resolutions()
        publication = {"status": "partial",
                       "limitations": ["Bass chorus loop ends with a partial cycle"],
                       "consent": "ok"}
        output, errors = compile_performance_arrangement(manifest, resolutions, publication)
        self.assertIsNone(output)
        self.assertTrue(any("tail_policy" in e for e in errors), errors)

    def test_explicit_tail_policy_is_recorded_on_the_partial_cycle_placement(self):
        output, errors = self._compile()
        self.assertEqual(errors, [])
        bass_placement = next(p for p in output["placements"] if p["material_id"] == "pattern-bass")
        self.assertEqual(bass_placement["tail_policy"], "reviewed_truncate")
        self.assertEqual(output["tail_policy"], "reviewed_truncate")


class RetainOptionalTests(unittest.TestCase):
    """Requirement 3: retained OPTIONAL material is a distinct state from
    omit/delete. A retained-OPTIONAL pattern must physically sound (with a
    visible `optional=true` marker on its placement) on whichever declared or
    resolved physical role (LOOP/LIVE/UNRESOLVED) it belongs to; it is never
    silently guessed, and the pre-existing default-omitted behavior (no
    decision, no declared role) must keep working unchanged.
    """

    def _compile(self, manifest, resolutions=None):
        resolutions = resolutions if resolutions is not None else multi_instrument_resolutions()
        publication = {"status": "partial",
                       "limitations": ["Bass chorus loop ends with a partial cycle"],
                       "consent": "User accepted the labeled preview/partial gaps.",
                       "tail_policy": "reviewed_truncate"}
        return compile_performance_arrangement(manifest, resolutions, publication)

    def test_default_optional_with_no_decision_or_role_stays_omitted(self):
        # Unchanged pre-existing behavior (requirement 3 must not regress this).
        output, errors = self._compile(multi_instrument_manifest())
        self.assertEqual(errors, [])
        self.assertIn("pattern-keys-optional", output["disclosure"]["omitted_patterns"])
        self.assertFalse(any(p["material_id"] == "pattern-keys-optional"
                             for p in output["placements"]))

    def test_declared_retain_role_retains_on_that_physical_role_with_optional_marker(self):
        manifest = multi_instrument_manifest()
        for pattern in manifest["patterns"]:
            if pattern["id"] == "pattern-keys-optional":
                pattern["retain_role"] = "live"
        output, errors = self._compile(manifest)
        self.assertEqual(errors, [])
        resolved = next(p for p in output["patterns"] if p["id"] == "pattern-keys-optional")
        self.assertEqual(resolved["final_disposition"], "LIVE")
        self.assertFalse(resolved["omitted"])
        self.assertNotIn("pattern-keys-optional", output["disclosure"]["omitted_patterns"])
        placement = next(p for p in output["placements"] if p["material_id"] == "pattern-keys-optional")
        self.assertTrue(placement["optional"])
        self.assertEqual(placement["mode"], "live")
        keys = next(i for i in output["instruments"] if i["analysis_track"] == "track-keys")
        live_track = next(t for t in keys["output_tracks"] if t["role"] == "live")
        self.assertEqual(placement["output_track_id"], live_track["id"])

    def test_decision_can_retain_optional_pattern_as_loop_with_optional_marker(self):
        manifest = multi_instrument_manifest()
        manifest["decisions"].append({
            "id": "decision-keys-bridge",
            "question": "Retain the optional keys bridge as a loop, or omit it?",
            "options": ["keep_loop", "omit"],
        })
        resolutions = multi_instrument_resolutions()
        resolutions["decisions"].append({"decision_id": "decision-keys-bridge", "option": "keep_loop"})
        output, errors = self._compile(manifest, resolutions)
        self.assertEqual(errors, [])
        resolved = next(p for p in output["patterns"] if p["id"] == "pattern-keys-optional")
        self.assertEqual(resolved["final_disposition"], "LOOP")
        self.assertFalse(resolved["omitted"])
        placement = next(p for p in output["placements"] if p["material_id"] == "pattern-keys-optional")
        self.assertTrue(placement["optional"])
        self.assertEqual(placement["mode"], "loop")

    def test_decision_can_explicitly_omit_optional_pattern(self):
        manifest = multi_instrument_manifest()
        manifest["decisions"].append({
            "id": "decision-keys-bridge",
            "question": "Retain the optional keys bridge, or omit it?",
            "options": ["keep_live", "omit"],
        })
        resolutions = multi_instrument_resolutions()
        resolutions["decisions"].append({"decision_id": "decision-keys-bridge", "option": "omit"})
        output, errors = self._compile(manifest, resolutions)
        self.assertEqual(errors, [])
        resolved = next(p for p in output["patterns"] if p["id"] == "pattern-keys-optional")
        self.assertEqual(resolved["final_disposition"], "OPTIONAL")
        self.assertTrue(resolved["omitted"])
        self.assertFalse(any(p["material_id"] == "pattern-keys-optional" for p in output["placements"]))

    def test_decision_takes_precedence_over_declared_retain_role(self):
        manifest = multi_instrument_manifest()
        for pattern in manifest["patterns"]:
            if pattern["id"] == "pattern-keys-optional":
                pattern["retain_role"] = "live"
        manifest["decisions"].append({
            "id": "decision-keys-bridge",
            "question": "Retain the optional keys bridge as a loop, or omit it?",
            "options": ["keep_loop", "omit"],
        })
        resolutions = multi_instrument_resolutions()
        resolutions["decisions"].append({"decision_id": "decision-keys-bridge", "option": "keep_loop"})
        output, errors = self._compile(manifest, resolutions)
        self.assertEqual(errors, [])
        resolved = next(p for p in output["patterns"] if p["id"] == "pattern-keys-optional")
        self.assertEqual(resolved["final_disposition"], "LOOP")


class SectionActionFixtureTests(unittest.TestCase):
    def _compile(self, section_actions=(), layer_omissions=()):
        manifest = multi_instrument_manifest()
        resolutions = multi_instrument_resolutions()
        resolutions["section_actions"] = list(section_actions)
        resolutions["layer_omissions"] = list(layer_omissions)
        publication = {"status": "partial",
                       "limitations": ["Bass chorus loop ends with a partial cycle"],
                       "consent": "ok", "tail_policy": "reviewed_truncate"}
        return compile_performance_arrangement(manifest, resolutions, publication)

    def test_section_deletion_requires_explicit_approval(self):
        output, errors = self._compile(
            section_actions=[{"section_id": "sec-solo", "action": "delete"}])
        self.assertIsNone(output)
        self.assertTrue(any("approval" in e for e in errors), errors)

    def test_approved_section_deletion_removes_only_that_instance(self):
        output, errors = self._compile(
            section_actions=[{"section_id": "sec-verse-1", "action": "delete", "approved": True}])
        self.assertEqual(errors, [])
        deleted = next(si for si in output["section_instances"] if si["id"] == "sec-verse-1")
        kept = next(si for si in output["section_instances"] if si["id"] == "sec-verse-2")
        self.assertEqual(deleted["action"], "delete")
        self.assertEqual(kept["action"], "retain")
        self.assertEqual(len(output["removal_ledger"]), 1)
        self.assertEqual(output["removal_ledger"][0]["section_instance_id"], "sec-verse-1")
        self.assertEqual(output["removal_ledger"][0]["removed_bars"], 2)
        # The deleted section's bars are gone from the rebuilt timeline...
        self.assertFalse(any(e["section_instance_id"] == "sec-verse-1"
                             for e in output["output_timeline"]))
        # ...but the surviving occurrence of the same display name is untouched.
        self.assertTrue(any(e["section_instance_id"] == "sec-verse-2"
                            for e in output["output_timeline"]))
        self.assertTrue(any(p["output_track_id"].endswith("-loop-rhythm-verse") and
                            p["material_id"] == "pattern-rhythm" for p in output["placements"]))

    def test_output_timeline_is_contiguous_after_deletion(self):
        output, errors = self._compile(
            section_actions=[{"section_id": "sec-verse-1", "action": "delete", "approved": True}])
        self.assertEqual(errors, [])
        indices = [e["output_index"] for e in output["output_timeline"]]
        self.assertEqual(indices, list(range(len(indices))))

    def test_layer_omission_is_distinct_from_section_deletion(self):
        output, errors = self._compile(
            layer_omissions=[{"section_id": "sec-bridge", "pattern_id": "pattern-keys-optional"}])
        self.assertEqual(errors, [])
        bridge = next(si for si in output["section_instances"] if si["id"] == "sec-bridge")
        self.assertEqual(bridge["action"], "retain")
        self.assertEqual(bridge["omitted_layers"], ["pattern-keys-optional"])
        # The section's bars remain in the timeline (omission is not deletion)...
        self.assertTrue(any(e["section_instance_id"] == "sec-bridge" for e in output["output_timeline"]))
        # ...keys-fill (the other active pattern in the same section) still gets a placement.
        self.assertTrue(any(p["material_id"] == "pattern-keys-fill" for p in output["placements"]))

    def test_unknown_section_id_in_section_actions_is_rejected(self):
        output, errors = self._compile(
            section_actions=[{"section_id": "no-such-section", "action": "delete", "approved": True}])
        self.assertIsNone(output)
        self.assertTrue(any("unknown section_id" in e for e in errors), errors)

    def test_layer_omission_for_inactive_pattern_is_rejected(self):
        output, errors = self._compile(
            layer_omissions=[{"section_id": "sec-solo", "pattern_id": "pattern-keys-optional"}])
        self.assertIsNone(output)
        self.assertTrue(any("not active in section" in e for e in errors), errors)


class LegacyRecompileFixtureTests(unittest.TestCase):
    def test_legacy_v2_performance_without_plan_version_is_flagged_for_recompile(self):
        from v2_schema import is_legacy_v2_performance
        legacy = {"version": 2, "publication": {"status": "final"}, "disclosure": {}}
        self.assertTrue(is_legacy_v2_performance(legacy))
        current = {"version": 2, "plan_version": 2, "publication": {"status": "final"},
                  "disclosure": {}}
        self.assertFalse(is_legacy_v2_performance(current))

    def test_recompiling_over_a_legacy_file_without_replace_is_blocked_with_recompile_reason(self):
        temp = tempfile.TemporaryDirectory(prefix="vp-compile-legacy-")
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        manifest_path = directory / "arrangement.json"
        resolutions_path = directory / "resolutions.json"
        performance_path = directory / "performance.json"
        output_path = directory / "performance-arrangement.json"
        manifest_path.write_text(json.dumps(base_manifest()), encoding="utf-8")
        resolutions_path.write_text(json.dumps([
            {"decision_id": "decision-rhythm-verse-a", "option": "use_canonical"},
            {"decision_id": "decision-rhythm-verse-a-followup", "option": "keep_loop"},
        ]), encoding="utf-8")
        performance_path.write_text(json.dumps(FINAL_PUBLICATION), encoding="utf-8")
        output_path.write_text(json.dumps(
            {"version": 2, "publication": {"status": "final"}, "disclosure": {}}), encoding="utf-8")
        code = main(["--manifest", str(manifest_path), "--resolutions", str(resolutions_path),
                    "--performance", str(performance_path), "--output", str(output_path)])
        self.assertNotEqual(code, 0)


class FingerprintFixtureTests(unittest.TestCase):
    def test_fingerprints_are_deterministic_sha256_of_inputs(self):
        manifest = multi_instrument_manifest()
        resolutions = multi_instrument_resolutions()
        publication = {"status": "partial",
                       "limitations": ["Bass chorus loop ends with a partial cycle"],
                       "consent": "ok", "tail_policy": "reviewed_truncate"}
        output1, errors1 = compile_performance_arrangement(manifest, resolutions, publication)
        output2, errors2 = compile_performance_arrangement(manifest, resolutions, publication)
        self.assertEqual(errors1, [])
        self.assertEqual(output1["fingerprints"], output2["fingerprints"])
        import re as _re
        self.assertRegex(output1["fingerprints"]["analysis_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(output1["fingerprints"]["resolutions_sha256"], r"^[0-9a-f]{64}$")
        mutated = multi_instrument_manifest()
        mutated["tempo_bpm"] = 101
        output3, errors3 = compile_performance_arrangement(mutated, resolutions, publication)
        self.assertEqual(errors3, [])
        self.assertNotEqual(output1["fingerprints"]["analysis_sha256"],
                            output3["fingerprints"]["analysis_sha256"])


class CompileDispositionTests(unittest.TestCase):
    def test_use_canonical_resolution_yields_loop_final_export(self):
        resolutions = [{"decision_id": "decision-rhythm-verse-a", "option": "use_canonical"},
                       {"decision_id": "decision-rhythm-verse-a-followup", "option": "keep_loop"}]
        output, errors = compile_performance_arrangement(base_manifest(), resolutions, FINAL_PUBLICATION)
        self.assertEqual(errors, [])
        self.assertEqual(output["patterns"][0]["final_disposition"], "LOOP")
        self.assertFalse(output["patterns"][0]["omitted"])
        self.assertEqual(output["publication"]["status"], "final")
        self.assertEqual(output["export_layout"], {
            "version": 1,
            "prune_zero_note_tracks": True,
            "group_order": ["core", "optional", "unresolved"],
            "colors": {"drums": "#0000FF", "bass": "#FFFF00", "other": "#FF0000"},
        })

    def test_play_live_resolution_yields_live_disposition(self):
        resolutions = [{"decision_id": "decision-rhythm-verse-a", "option": "play_live"}]
        publication = {"status": "partial", "limitations": ["Rhythm verse played live, not looped"],
                       "consent": "User accepted live performance for rehearsal."}
        output, errors = compile_performance_arrangement(base_manifest(), resolutions, publication)
        self.assertEqual(errors, [])
        self.assertEqual(output["patterns"][0]["final_disposition"], "LIVE")
        self.assertIn("pattern-rhythm-a", output["disclosure"]["live_patterns"])

    def test_use_canonical_then_omit_followup_yields_optional_and_omitted(self):
        resolutions = [{"decision_id": "decision-rhythm-verse-a", "option": "use_canonical"},
                       {"decision_id": "decision-rhythm-verse-a-followup", "option": "omit"}]
        publication = {"status": "partial", "limitations": ["Rhythm verse omitted by request"],
                       "consent": "User accepted omission."}
        output, errors = compile_performance_arrangement(base_manifest(), resolutions, publication)
        self.assertEqual(errors, [])
        self.assertTrue(output["patterns"][0]["omitted"])
        self.assertIn("pattern-rhythm-a", output["disclosure"]["omitted_patterns"])

    def test_followup_uses_dependency_graph_not_id_suffix(self):
        manifest = base_manifest()
        manifest["decisions"][1]["id"] = "drop-layer"
        manifest["patterns"][0]["decision_ids"] = [
            "decision-rhythm-verse-a", "drop-layer",
        ]
        resolutions = [
            {"decision_id": "decision-rhythm-verse-a", "option": "use_canonical"},
            {"decision_id": "drop-layer", "option": "omit"},
        ]
        publication = {"status": "partial", "limitations": ["Rhythm verse omitted by request"],
                       "consent": "User accepted omission."}
        output, errors = compile_performance_arrangement(manifest, resolutions, publication)
        self.assertEqual(errors, [])
        self.assertTrue(output["patterns"][0]["omitted"])
        self.assertEqual(output["placements"], [])

    def test_rest_span_is_recorded_in_disclosure(self):
        resolutions = [{"decision_id": "decision-rhythm-verse-a", "option": "use_canonical"},
                       {"decision_id": "decision-rhythm-verse-a-followup", "option": "keep_loop"}]
        output, errors = compile_performance_arrangement(base_manifest(), resolutions, FINAL_PUBLICATION)
        self.assertEqual(errors, [])
        self.assertIn("rhythm-outro-rest", output["disclosure"]["rest_spans"])

    def test_source_facts_are_preserved_verbatim(self):
        manifest = base_manifest()
        manifest_before = json.loads(json.dumps(manifest))
        resolutions = [{"decision_id": "decision-rhythm-verse-a", "option": "use_canonical"},
                       {"decision_id": "decision-rhythm-verse-a-followup", "option": "keep_loop"}]
        output, errors = compile_performance_arrangement(manifest, resolutions, FINAL_PUBLICATION)
        self.assertEqual(errors, [])
        self.assertEqual(output["source"], manifest["source"])
        # requirement 2/4: compiling never mutates the caller's manifest object --
        # `output["tracks"]` is only the same content plus the resolved stable track
        # `id` compile_performance.py always publishes downstream.
        self.assertEqual(manifest, manifest_before)
        expected_tracks = json.loads(json.dumps(manifest["tracks"]))
        for i, track in enumerate(expected_tracks):
            track.setdefault("id", f"track-{i}")
        self.assertEqual(output["tracks"], expected_tracks)


class UnselectedLiveSpanPlacementTests(unittest.TestCase):
    @staticmethod
    def manifest():
        return {
            "version": 2, "tempo_bpm": 120, "meter": "4/4",
            "source": {"asset_relative_path": "source/song.gp",
                       "asset_sha256": "c" * 64, "format": "gpif"},
            "tracks": [
                {"id": "track-guitar", "name": "Guitar", "spans": [
                    {"id": "guitar-live", "disposition": "LIVE",
                     "source_ref": {"asset_sha256": "c" * 64,
                                    "asset_relative_path": "source/song.gp",
                                    "written_master_bars": [1, 2, 5, 6]}},
                ]},
                {"id": "track-keys", "name": "Keys", "spans": [
                    {"id": "keys-rest", "disposition": "REST"},
                ]},
            ],
            "patterns": [
                {"id": "pattern-rest", "name": "Keys Rest", "track": "Keys",
                 "span_ids": ["keys-rest"], "bars": 1, "disposition": "REST"},
            ],
            "sections": [
                {"id": "sec-a", "name": "A", "bars": 2,
                 "active_pattern_ids": [], "empty_policy": "live"},
                {"id": "sec-rest", "name": "Rest", "bars": 2,
                 "active_pattern_ids": [], "empty_policy": "rest"},
                {"id": "sec-b", "name": "B", "bars": 2,
                 "active_pattern_ids": [], "empty_policy": "live"},
            ],
        }

    def test_unselected_live_span_becomes_explicit_correct_track_placements(self):
        output, errors = compile_performance_arrangement(
            self.manifest(), {}, {"status": "final", "limitations": []})
        self.assertEqual(errors, [])
        live = [p for p in output["placements"] if p["mode"] == "live"]
        self.assertEqual(
            [(p["output_track_id"], p["output_bar_range"], p["material_id"]) for p in live],
            [("instrument-guitar-live", [0, 1], "guitar-live"),
             ("instrument-guitar-live", [4, 5], "guitar-live")],
        )
        self.assertEqual(output["disclosure"]["live_spans_without_selected_pattern"],
                         ["guitar-live"])
        self.assertFalse(any(p["output_track_id"] == "instrument-keys-live"
                             for p in output["placements"]),
                         "empty_policy=live must not fabricate an instrument assignment")

    def test_deleted_live_section_does_not_survive_output_timeline(self):
        resolutions = {"section_actions": [
            {"section_id": "sec-b", "action": "delete", "approved": True,
             "reason": "approved edit"},
        ]}
        output, errors = compile_performance_arrangement(
            self.manifest(), resolutions, {"status": "final", "limitations": []})
        self.assertEqual(errors, [])
        self.assertEqual([p["output_bar_range"] for p in output["placements"]], [[0, 1]])

    def test_live_span_uses_performed_written_bar_mapping(self):
        manifest = self.manifest()
        manifest["tracks"][0]["spans"][0]["source_ref"]["written_master_bars"] = [1, 2]
        manifest["sections"] = [
            {"id": "first", "name": "First", "bars": 2,
             "active_pattern_ids": [], "empty_policy": "live"},
            {"id": "repeat", "name": "Repeat", "bars": 2,
             "active_pattern_ids": [], "empty_policy": "live"},
        ]
        manifest["performed_written_bars"] = [1, 2, 1, 2]
        output, errors = compile_performance_arrangement(
            manifest, {}, {"status": "final", "limitations": []})
        self.assertEqual(errors, [])
        self.assertEqual(
            [placement["output_bar_range"] for placement in output["placements"]],
            [[0, 1], [2, 3]],
        )

    def test_unselected_live_span_without_coordinates_is_rejected(self):
        manifest = self.manifest()
        manifest["tracks"][0]["spans"][0].pop("source_ref")
        output, errors = compile_performance_arrangement(
            manifest, {}, {"status": "final", "limitations": []})
        self.assertIsNone(output)
        self.assertTrue(any("no written_master_bars coordinates" in e for e in errors), errors)

    def test_overlapping_unselected_live_spans_are_rejected(self):
        manifest = self.manifest()
        manifest["tracks"][0]["spans"].append({
            "id": "guitar-live-overlap", "disposition": "LIVE",
            "source_ref": {"asset_sha256": "c" * 64,
                           "asset_relative_path": "source/song.gp",
                           "written_master_bars": [2, 2]},
        })
        output, errors = compile_performance_arrangement(
            manifest, {}, {"status": "final", "limitations": []})
        self.assertIsNone(output)
        self.assertTrue(any("conflicts" in e for e in errors), errors)


class CompileValidationTests(unittest.TestCase):
    def test_missing_resolution_is_rejected(self):
        output, errors = compile_performance_arrangement(base_manifest(), [], FINAL_PUBLICATION)
        self.assertIsNone(output)
        self.assertTrue(any("no resolution" in e for e in errors))

    def test_final_rejects_remaining_unresolved_pattern(self):
        manifest = base_manifest()
        # No decision maps to this pattern's span, so it stays effectively
        # unresolved; a Final export must refuse to publish it silently.
        manifest["decisions"] = []
        manifest["patterns"][0]["span_ids"] = ["rhythm-verse-a"]
        output, errors = compile_performance_arrangement(manifest, [], FINAL_PUBLICATION)
        self.assertIsNone(output)
        self.assertTrue(errors)

    def test_partial_without_consent_is_rejected(self):
        resolutions = [{"decision_id": "decision-rhythm-verse-a", "option": "play_live"}]
        bad_publication = {"status": "partial", "limitations": ["x"]}
        output, errors = compile_performance_arrangement(base_manifest(), resolutions, bad_publication)
        self.assertIsNone(output)
        self.assertTrue(any("consent" in e for e in errors))

    def test_partial_without_limitations_is_rejected(self):
        resolutions = [{"decision_id": "decision-rhythm-verse-a", "option": "play_live"}]
        bad_publication = {"status": "partial", "limitations": [], "consent": "ok"}
        output, errors = compile_performance_arrangement(base_manifest(), resolutions, bad_publication)
        self.assertIsNone(output)
        self.assertTrue(any("limitations" in e for e in errors))

    def test_final_with_limitations_is_rejected(self):
        resolutions = [{"decision_id": "decision-rhythm-verse-a", "option": "use_canonical"},
                       {"decision_id": "decision-rhythm-verse-a-followup", "option": "keep_loop"}]
        bad_publication = {"status": "final", "limitations": ["should not be here"]}
        output, errors = compile_performance_arrangement(base_manifest(), resolutions, bad_publication)
        self.assertIsNone(output)
        self.assertTrue(any("cannot have limitations" in e for e in errors))

    def test_unknown_decision_id_in_resolution_is_rejected(self):
        resolutions = [{"decision_id": "decision-does-not-exist", "option": "use_canonical"}]
        output, errors = compile_performance_arrangement(base_manifest(), resolutions, FINAL_PUBLICATION)
        self.assertIsNone(output)
        self.assertTrue(any("unknown decision_id" in e for e in errors))

    def test_invalid_option_for_decision_is_rejected(self):
        resolutions = [{"decision_id": "decision-rhythm-verse-a", "option": "not_a_real_option"}]
        output, errors = compile_performance_arrangement(base_manifest(), resolutions, FINAL_PUBLICATION)
        self.assertIsNone(output)
        self.assertTrue(any("not valid" in e for e in errors))

    def test_resolution_for_inapplicable_followup_is_rejected(self):
        # The followup only applies when the parent resolves to use_canonical.
        resolutions = [{"decision_id": "decision-rhythm-verse-a", "option": "play_live"},
                       {"decision_id": "decision-rhythm-verse-a-followup", "option": "keep_loop"}]
        output, errors = compile_performance_arrangement(base_manifest(), resolutions, FINAL_PUBLICATION)
        self.assertIsNone(output)
        self.assertTrue(any("does not apply" in e for e in errors))


class CompileCliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vp-compile-")
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.manifest_path = self.dir / "arrangement.json"
        self.resolutions_path = self.dir / "resolutions.json"
        self.performance_path = self.dir / "performance.json"
        self.output_path = self.dir / "performance-arrangement.json"
        self.manifest_path.write_text(json.dumps(base_manifest()), encoding="utf-8")
        self.resolutions_path.write_text(json.dumps([
            {"decision_id": "decision-rhythm-verse-a", "option": "use_canonical"},
            {"decision_id": "decision-rhythm-verse-a-followup", "option": "keep_loop"},
        ]), encoding="utf-8")
        self.performance_path.write_text(json.dumps(FINAL_PUBLICATION), encoding="utf-8")

    def run_cli(self, **extra_args):
        args = ["--manifest", str(self.manifest_path), "--resolutions", str(self.resolutions_path),
                "--performance", str(self.performance_path), "--output", str(self.output_path)]
        for flag, value in extra_args.items():
            if value is True:
                args.append(f"--{flag}")
        return main(args)

    def test_writes_output_file(self):
        code = self.run_cli()
        self.assertEqual(code, 0)
        self.assertTrue(self.output_path.is_file())
        data = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(data["publication"]["status"], "final")

    def test_no_clobber_without_replace(self):
        self.run_cli()
        original = self.output_path.read_text(encoding="utf-8")
        code = self.run_cli()
        self.assertNotEqual(code, 0)
        self.assertEqual(self.output_path.read_text(encoding="utf-8"), original)

    def test_replace_mode_overwrites(self):
        self.run_cli()
        self.output_path.write_text("stale", encoding="utf-8")
        code = self.run_cli(replace=True)
        self.assertEqual(code, 0)
        self.assertIn("publication", self.output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
