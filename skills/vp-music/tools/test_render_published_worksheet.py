"""Hermetic tests for the published (Final/Partial) worksheet renderer.

Covers requirement 7's finalize-views scenarios: a Final multi-track
worksheet, a consented Partial with an unresolved pattern, retained
OPTIONAL material, a deleted section that both changes bars/times and
creates a reviewable transition gap, duplicate section-instance display
names, and the Draft-mode regression (the pre-existing renderer must be
untouched by this work).
"""

import json
from pathlib import Path
import tempfile
import unittest

from compile_performance import compile_performance_arrangement
from render_views import (
    render,
    render_published,
    render_published_worksheet,
    render_worksheet_markdown,
)
from test_compile_performance import multi_instrument_manifest, multi_instrument_resolutions
from v2_schema import Invalid, load_performance_plan


def _compile(resolutions=None, publication=None, manifest=None):
    manifest = manifest or multi_instrument_manifest()
    resolutions = resolutions if resolutions is not None else multi_instrument_resolutions(
        rhythm_option="use_canonical", keys_fill_option="play_live")
    publication = publication or {"status": "final", "limitations": [],
                                  "tail_policy": "reviewed_truncate"}
    output, errors = compile_performance_arrangement(manifest, resolutions, publication)
    assert errors == [], errors
    # Round-trip through JSON, exactly as a CLI caller reading
    # performance-arrangement.json from disk would.
    return load_performance_plan(json.loads(json.dumps(output)))


class FinalWorksheetTests(unittest.TestCase):
    def setUp(self):
        self.plan = _compile()
        self.text = render_published_worksheet(self.plan, title="Final Song")

    def test_status_and_title(self):
        self.assertIn("# Final Song", self.text)
        self.assertIn("**Status: Final**", self.text)
        self.assertNotIn("Limitations", self.text)

    def test_tempo_meter_and_total_duration_from_bars(self):
        self.assertIn("quarter = 100 BPM", self.text)
        self.assertIn("Meter:** 4/4", self.text)
        total_bars = len(self.plan["output_timeline"])
        self.assertEqual(total_bars, 2 + 2 + 4 + 4 + 8)
        self.assertIn(f"**Total:** {total_bars} output bars", self.text)

    def test_every_output_track_listed_with_stable_ids(self):
        for instrument in self.plan["instruments"]:
            self.assertIn(f"instrument `{instrument['id']}`", self.text)
            for ot in instrument["output_tracks"]:
                self.assertIn(f"`{ot['id']}`", self.text)

    def test_structure_has_five_compiled_sections_in_order(self):
        headers = [line for line in self.text.splitlines() if line.startswith("### ")]
        self.assertEqual(len(headers), 5)
        self.assertTrue(headers[0].startswith("### 1. Verse (occurrence 1)"))
        self.assertTrue(headers[1].startswith("### 2. Verse (occurrence 2)"))
        self.assertTrue(headers[2].startswith("### 3. Solo (occurrence 1)"))
        self.assertTrue(headers[3].startswith("### 4. Bridge (occurrence 1)"))
        self.assertTrue(headers[4].startswith("### 5. Chorus (occurrence 1)"))

    def test_no_review_required_and_no_unresolved_section_in_final(self):
        self.assertNotIn("REVIEW REQUIRED", self.text)
        self.assertNotIn("## Unresolved (Partial)", self.text)

    def test_optional_bridge_pattern_is_omitted_not_in_structure(self):
        # pattern-keys-optional was never retained in this fixture -> omitted.
        self.assertIn("## Omissions and Edits", self.text)
        self.assertIn("OMITTED pattern `pattern-keys-optional`", self.text)
        structure = self.text.split("## Structure", 1)[1].split("## Omissions", 1)[0]
        self.assertNotIn("Keys Bridge", structure)

    def test_active_placement_lines_name_pattern_and_track(self):
        self.assertIn('LOOP `', self.text)
        self.assertIn('"Rhythm Verse"', self.text)
        self.assertIn('(Rhythm Guitar)', self.text)
        self.assertIn('LIVE `', self.text)
        self.assertIn('"Lead Solo"', self.text)
        self.assertIn("manual cue: bars 5-8", self.text)


class PartialUnresolvedTests(unittest.TestCase):
    def setUp(self):
        resolutions = multi_instrument_resolutions(rhythm_option="use_canonical",
                                                    keys_fill_option="defer_unresolved")
        publication = {"status": "partial",
                       "limitations": ["Keys fill is deferred/unresolved for this preview."],
                       "consent": "User accepted the labeled preview/partial gaps.",
                       "tail_policy": "reviewed_truncate"}
        self.plan = _compile(resolutions=resolutions, publication=publication)
        self.text = render_published_worksheet(self.plan, title="Partial Song")

    def test_status_limitations_and_consent_shown(self):
        self.assertIn("**Status: Partial**", self.text)
        self.assertIn("**Limitations:** Keys fill is deferred/unresolved", self.text)
        self.assertIn("**Consent:** User accepted", self.text)

    def test_unresolved_section_lists_pattern(self):
        self.assertIn("## Unresolved (Partial)", self.text)
        self.assertIn("`pattern-keys-fill` \"Keys Fill\"", self.text)

    def test_unresolved_track_marked_in_structure(self):
        self.assertIn("UNRESOLVED `", self.text)


class RetainedOptionalTests(unittest.TestCase):
    def test_retained_optional_pattern_shown_distinctly_from_omit_delete(self):
        manifest = multi_instrument_manifest()
        # Analysis-declared `retain_role` is the explicit, non-guessed opt-in path
        # for retaining OPTIONAL material on a specific physical role (see
        # resolve_dispositions's docstring in compile_performance.py).
        for pattern in manifest["patterns"]:
            if pattern["id"] == "pattern-keys-optional":
                pattern["retain_role"] = "loop"
        resolutions = multi_instrument_resolutions(rhythm_option="use_canonical",
                                                    keys_fill_option="play_live")
        output, errors = compile_performance_arrangement(manifest, resolutions,
                                                          {"status": "final", "limitations": [],
                                                           "tail_policy": "reviewed_truncate"})
        self.assertEqual(errors, [])
        plan = load_performance_plan(json.loads(json.dumps(output)))
        retained = [p for p in plan["patterns"]
                   if p["id"] == "pattern-keys-optional" and p.get("disposition") == "OPTIONAL"
                   and not p.get("omitted")]
        self.assertTrue(retained, "pattern-keys-optional should be retained via retain_role")
        text = render_published_worksheet(plan, title="Song")
        self.assertIn("## Retained Optional Material", text)
        self.assertIn("`pattern-keys-optional` \"Keys Bridge\" retained", text)
        self.assertIn("[OPTIONAL]", text)
        self.assertNotIn("OMITTED pattern `pattern-keys-optional`", text)


class DeletedSectionTests(unittest.TestCase):
    def _compile_with_deletion(self, reason=None):
        manifest = multi_instrument_manifest()
        resolutions = multi_instrument_resolutions(rhythm_option="use_canonical",
                                                    keys_fill_option="play_live")
        action = {"section_id": "sec-solo", "action": "delete", "approved": True}
        if reason:
            action["reason"] = reason
        resolutions["section_actions"] = [action]
        publication = {"status": "partial", "limitations": ["Solo section removed by request."],
                       "consent": "User accepted the labeled preview/partial gaps.",
                       "tail_policy": "reviewed_truncate"}
        output, errors = compile_performance_arrangement(manifest, resolutions, publication)
        self.assertEqual(errors, [])
        return load_performance_plan(json.loads(json.dumps(output)))

    def test_deletion_shrinks_total_bars_and_seconds(self):
        baseline = _compile()
        deleted = self._compile_with_deletion(reason="Cut the solo; segue straight into Bridge.")
        baseline_bars = len(baseline["output_timeline"])
        deleted_bars = len(deleted["output_timeline"])
        self.assertEqual(deleted_bars, baseline_bars - 4)
        text = render_published_worksheet(deleted, title="Song")
        self.assertIn(f"**Total:** {deleted_bars} output bars", text)

    def test_deletion_listed_in_omissions_not_structure(self):
        deleted = self._compile_with_deletion(reason="Cut the solo; segue straight into Bridge.")
        text = render_published_worksheet(deleted, title="Song")
        structure = text.split("## Structure", 1)[1].split("## Omissions", 1)[0]
        headers = [line for line in structure.splitlines() if line.startswith("### ")]
        self.assertFalse(any("Solo" in h for h in headers),
                         "a deleted section must not appear as its own Structure entry")
        self.assertIn("## Omissions and Edits", text)
        self.assertIn('DELETED section `sec-solo`', text)
        self.assertIn("Solo", text.split("## Omissions and Edits", 1)[1])

    def test_confirmed_reason_documents_transition_instead_of_review_required(self):
        deleted = self._compile_with_deletion(reason="Cut the solo; segue straight into Bridge.")
        text = render_published_worksheet(deleted, title="Song")
        self.assertIn("new adjacency after removing Solo (occurrence 1)", text)
        self.assertIn("Cut the solo; segue straight into Bridge.", text)
        self.assertNotIn("REVIEW REQUIRED", text)

    def test_missing_reason_flags_review_required_on_partial(self):
        deleted = self._compile_with_deletion(reason=None)
        text = render_published_worksheet(deleted, title="Song")
        self.assertIn("REVIEW REQUIRED", text)

    def test_missing_reason_on_final_is_a_validation_error_not_invented_music(self):
        manifest = multi_instrument_manifest()
        resolutions = multi_instrument_resolutions(rhythm_option="use_canonical",
                                                    keys_fill_option="play_live")
        resolutions["section_actions"] = [{"section_id": "sec-solo", "action": "delete",
                                           "approved": True}]
        publication = {"status": "final", "limitations": [], "tail_policy": "reviewed_truncate"}
        output, errors = compile_performance_arrangement(manifest, resolutions, publication)
        self.assertEqual(errors, [])
        plan = load_performance_plan(json.loads(json.dumps(output)))
        with self.assertRaises(Invalid):
            render_published_worksheet(plan, title="Song")


class DuplicateSectionNameTests(unittest.TestCase):
    def test_duplicate_verse_instances_are_distinguished_by_occurrence_and_instance_id(self):
        plan = _compile()
        verse_lines = [line for line in plan["section_instances"] if line["name"] == "Verse"]
        self.assertEqual(len(verse_lines), 2)
        self.assertEqual({v["id"] for v in verse_lines}, {"sec-verse-1", "sec-verse-2"})
        text = render_published_worksheet(plan, title="Song")
        self.assertIn("Verse (occurrence 1)", text)
        self.assertIn("Verse (occurrence 2)", text)
        self.assertIn("section instance `sec-verse-1`", text)
        self.assertIn("section instance `sec-verse-2`", text)


class ErrorHandlingTests(unittest.TestCase):
    def test_draft_status_plan_is_rejected(self):
        plan = _compile()
        plan = dict(plan)
        plan["publication"] = dict(plan["publication"])
        plan["publication"]["status"] = "draft"
        with self.assertRaises(Invalid):
            render_published_worksheet(plan, title="Song")

    def test_missing_tempo_is_a_validation_error_not_an_estimate(self):
        plan = _compile()
        plan = dict(plan)
        plan["tempo_bpm"] = None
        with self.assertRaises(Invalid):
            render_published_worksheet(plan, title="Song")

    def test_missing_meter_is_a_validation_error_not_an_estimate(self):
        plan = _compile()
        plan = dict(plan)
        plan["meter"] = None
        with self.assertRaises(Invalid):
            render_published_worksheet(plan, title="Song")


class DraftRendererRegressionTests(unittest.TestCase):
    """The pre-existing Draft renderer must behave identically after this work."""

    def test_draft_worksheet_markdown_unaffected_by_published_renderer_additions(self):
        manifest = {
            "version": 2, "tempo_bpm": 120, "meter": "4/4",
            "source": {"asset_relative_path": "source/song.gp", "asset_sha256": "a" * 64,
                      "format": "gpif"},
            "tracks": [{"name": "Rhythm Guitar", "spans": [
                {"id": "s1", "disposition": "LOOP"}]}],
            "patterns": [{"id": "pattern-rhythm-a", "name": "Rhythm A", "track": "Rhythm Guitar",
                         "span_ids": ["s1"], "bars": 2, "disposition": "LOOP",
                         "confidence": "likely"}],
            "sections": [{"id": "sec-intro", "name": "Intro", "bars": 2,
                         "active_pattern_ids": ["pattern-rhythm-a"]}],
        }
        text = render_worksheet_markdown(manifest, title="Draft Song")
        self.assertIn("**Status: Draft**", text)
        self.assertIn("### Intro - 2 bars (~4.0 seconds)", text)


class CLIIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vp-render-published-")
        self.addCleanup(temp.cleanup)
        self.song_dir = Path(temp.name)
        self.plan_path = self.song_dir / "performance-arrangement.json"
        manifest = multi_instrument_manifest()
        resolutions = multi_instrument_resolutions(rhythm_option="use_canonical",
                                                    keys_fill_option="play_live")
        publication = {"status": "final", "limitations": [], "tail_policy": "reviewed_truncate"}
        output, errors = compile_performance_arrangement(manifest, resolutions, publication)
        assert errors == []
        self.plan_path.write_text(json.dumps(output), encoding="utf-8")

    def test_render_published_writes_worksheet_md(self):
        result = render_published(self.plan_path, self.song_dir, replace=False, title="Song")
        self.assertEqual(result["status"], "written")
        worksheet = self.song_dir / "worksheet.md"
        self.assertTrue(worksheet.is_file())
        self.assertIn("**Status: Final**", worksheet.read_text(encoding="utf-8"))

    def test_rerender_without_replace_blocks(self):
        render_published(self.plan_path, self.song_dir, replace=False, title="Song")
        result = render_published(self.plan_path, self.song_dir, replace=False, title="Song")
        self.assertEqual(result["status"], "blocked")

    def test_dry_run_reports_without_writing(self):
        result = render_published(self.plan_path, self.song_dir, replace=False, title="Song",
                                  dry_run=True)
        self.assertEqual(result["status"], "dry-run")
        self.assertFalse((self.song_dir / "worksheet.md").exists())

    def test_version_check_rejects_non_v2_plans(self):
        self.plan_path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        with self.assertRaises(Invalid):
            render_published(self.plan_path, self.song_dir, replace=False, title="Song")


if __name__ == "__main__":
    unittest.main()
