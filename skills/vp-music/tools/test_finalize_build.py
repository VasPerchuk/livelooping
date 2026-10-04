"""Hermetic, end-to-end tests for `finalize_build.py`'s coordinated build +
transactional publication pipeline. Builds a real hermetic GP source fixture via
`make_source_fixture.mjs` (no commercial song asset), compiles/renders/writes through
the real `compile_performance.py`/`render_views.py`/`write_arrangement.mjs` building
blocks, and exercises no-clobber, `--replace`, idempotent rebuild, Partial/Draft,
failure injection + rollback, interrupted-journal recovery, and path containment.
"""

import hashlib
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from finalize_build import (
    FinalizeError,
    InjectedFailure,
    Layout,
    finalize_build,
    recover,
    verify_bundle,
)
from phase_status import status as phase_status
from v2_schema import Invalid

HERE = Path(__file__).resolve().parent
FIXTURE_GEN = HERE / "make_source_fixture.mjs"


def make_fixture_bytes(**opts):
    result = subprocess.run(
        ["node", str(FIXTURE_GEN)], input=json.dumps(opts).encode("utf-8"),
        capture_output=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace"))
    return result.stdout


def _ref(digest, rel_path, bars):
    return {"asset_sha256": digest, "asset_relative_path": rel_path, "track_index": 0,
            "track_name": "Fixture Guitar", "staff_index": 0,
            "written_master_bars": bars, "voice_indices": [0]}


class FinalizeBuildTestCase(unittest.TestCase):
    """Common fixture scaffolding shared by every test below."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vp-finalize-")
        self.addCleanup(temp.cleanup)
        self.song_dir = Path(temp.name)
        (self.song_dir / "source").mkdir(parents=True)
        self.tracking = self.song_dir / ".vp-music-tracking" / "arrangement"
        self.tracking.mkdir(parents=True)
        research_dir = self.song_dir / ".vp-music-tracking" / "research"
        research_dir.mkdir(parents=True)
        (research_dir / "research.md").write_text(
            "# Fixture research\n\nValidated original test fixture.\n", encoding="utf-8")
        (self.song_dir / "chords.md").write_text(
            "# Fixture chords\n\nNo harmony assertions required.\n", encoding="utf-8")
        self.analysis_path = self.tracking / "arrangement.json"
        self.resolutions_path = self.tracking / "resolutions.json"
        self.performance_path = self.tracking / "performance.json"

        data = make_fixture_bytes(bars=4)
        asset_path = self.song_dir / "source" / "fixture.gp"
        asset_path.write_bytes(data)
        self.digest = hashlib.sha256(data).hexdigest()
        self.rel_path = "source/fixture.gp"

    def _manifest(self):
        ref_a = _ref(self.digest, self.rel_path, [1, 2])
        return {
            "version": 2, "tempo_bpm": 120, "meter": "4/4",
            "source": {"asset_relative_path": self.rel_path, "asset_sha256": self.digest,
                      "format": "gpif", "capabilities": ["source_bar_reuse"]},
            "tracks": [{
                "id": "track-guitar", "name": "Guitar",
                "spans": [
                    {"id": "gtr-a", "disposition": "LOOP", "source_ref": ref_a},
                    {"id": "gtr-live", "disposition": "LIVE"},
                    {"id": "gtr-fill", "disposition": "UNRESOLVED"},
                ],
            }],
            "decisions": [
                {"id": "decision-gtr-fill",
                 "question": "Resolve the outro fill now, or defer it?",
                 "options": ["play_live", "defer_unresolved"]},
            ],
            "patterns": [
                {"id": "pattern-a", "name": "Riff A", "track": "Guitar",
                 "span_ids": ["gtr-a"], "bars": 2, "disposition": "LOOP",
                 "selection_reason": "Two-bar source fixture intentionally tests short loop reuse."},
                {"id": "pattern-live", "name": "Live Fills", "track": "Guitar",
                 "span_ids": ["gtr-live"], "bars": 2, "disposition": "LIVE"},
                {"id": "pattern-fill", "name": "Outro Fill", "track": "Guitar",
                 "span_ids": ["gtr-fill"], "bars": 1, "disposition": "UNRESOLVED"},
            ],
            "sections": [
                {"id": "sec-verse", "name": "Verse", "bars": 2,
                 "active_pattern_ids": ["pattern-a"]},
                {"id": "sec-bridge", "name": "Bridge", "bars": 2,
                 "active_pattern_ids": ["pattern-live"]},
                {"id": "sec-outro", "name": "Outro", "bars": 1,
                 "active_pattern_ids": ["pattern-fill"]},
            ],
        }

    def _write_inputs(self, resolutions, performance):
        self.analysis_path.write_text(json.dumps(self._manifest()), encoding="utf-8")
        self.resolutions_path.write_text(json.dumps(resolutions), encoding="utf-8")
        self.performance_path.write_text(json.dumps(performance), encoding="utf-8")

    def _build(self, **kwargs):
        return finalize_build(self.song_dir, self.analysis_path, self.resolutions_path,
                              self.performance_path, **kwargs)

    PARTIAL_RESOLUTIONS = [{"decision_id": "decision-gtr-fill", "option": "defer_unresolved"}]
    SECTION_TRANSITIONS = [
        {"section_id": "sec-verse", "action": "Start the prepared riff after the count-in.",
         "feasible": True},
        {"section_id": "sec-bridge", "action": "Stop the riff and play the bridge live.",
         "feasible": True},
        {"section_id": "sec-outro", "action": "Continue live into the outro cue.",
         "feasible": True},
    ]
    PARTIAL_PERFORMANCE = {
        "status": "partial", "limitations": ["outro fill deferred"],
        "consent": "reviewed and accepted",
        "performance": {"players": 1, "roles": ["Player 1 performs guitar"],
                        "section_transitions": SECTION_TRANSITIONS},
    }
    FINAL_RESOLUTIONS = [{"decision_id": "decision-gtr-fill", "option": "play_live"}]
    FINAL_PERFORMANCE = {
        "status": "final", "limitations": [],
        "performance": {"players": 1, "roles": ["Player 1 performs guitar"],
                        "section_transitions": SECTION_TRANSITIONS},
    }
    DRAFT_PERFORMANCE = {"status": "draft", "limitations": [],
                         "performance": {"players": 1, "roles": ["Player 1 performs guitar"]}}


class SuccessfulPublishTests(FinalizeBuildTestCase):
    def test_publication_requires_confirmed_action_for_every_section(self):
        performance = json.loads(json.dumps(self.FINAL_PERFORMANCE))
        performance["performance"]["section_transitions"].pop()
        self._write_inputs(self.FINAL_RESOLUTIONS, performance)
        with self.assertRaisesRegex(Invalid, "confirmed performance actions.*sec-outro"):
            self._build()

    def test_final_publish_writes_bundle_and_public_files(self):
        self._write_inputs(self.FINAL_RESOLUTIONS, self.FINAL_PERFORMANCE)
        result = self._build(title="Fixture Song")
        self.assertEqual(result["status"], "written")

        layout = Layout(self.song_dir)
        self.assertTrue(layout.worksheet_target.exists())
        self.assertTrue(layout.gp_target.exists())
        self.assertTrue(layout.plan_target.exists())
        self.assertTrue(layout.bundle_path.exists())
        bundle = json.loads(layout.bundle_path.read_text(encoding="utf-8"))
        self.assertEqual(bundle["status"], "final")
        self.assertEqual(set(bundle["files"]),
                         {"performance_arrangement", "worksheet", "arrangement_gp"})
        self.assertEqual(
            bundle["exported_tracks"],
            [{
                "output_track_id": "instrument-guitar-loop-riff-a",
                "gp_track_name": "Guitar (loop)",
                "group": "core",
            }],
        )

        # no leaked temp/staging/backup state after a clean success
        self.assertFalse(layout.journal_path.exists())
        self.assertFalse(layout.staging_root.exists() and any(layout.staging_root.iterdir()))
        self.assertFalse(layout.backups_root.exists() and any(layout.backups_root.iterdir()))

        self.assertEqual(verify_bundle(self.song_dir), [])
        reconciled = phase_status(self.song_dir)
        self.assertEqual(reconciled["status"], "ok")
        self.assertEqual(reconciled["latest_verified_phase"], "finalization")

    def test_partial_publish_has_limitations_and_consent(self):
        self._write_inputs(self.PARTIAL_RESOLUTIONS, self.PARTIAL_PERFORMANCE)
        result = self._build()
        self.assertEqual(result["status"], "written")
        text = (self.song_dir / "worksheet.md").read_text(encoding="utf-8")
        self.assertIn("**Status: Partial**", text)
        self.assertIn("outro fill deferred", text)
        self.assertEqual(verify_bundle(self.song_dir), [])

    def test_unselected_live_span_survives_in_worksheet_without_empty_gp_track(self):
        manifest = self._manifest()
        manifest["tracks"][0]["spans"][1]["source_ref"] = {
            "asset_sha256": self.digest, "asset_relative_path": self.rel_path,
            "written_master_bars": [3, 4],
        }
        manifest["patterns"] = [
            pattern for pattern in manifest["patterns"]
            if pattern["id"] != "pattern-live"
        ]
        manifest["sections"][1] = {
            "id": "sec-bridge", "name": "Bridge", "bars": 2,
            "active_pattern_ids": [], "empty_policy": "live",
        }
        self.analysis_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.resolutions_path.write_text(json.dumps(self.FINAL_RESOLUTIONS), encoding="utf-8")
        self.performance_path.write_text(json.dumps(self.FINAL_PERFORMANCE), encoding="utf-8")

        result = self._build(title="Fixture Song")
        self.assertEqual(result["status"], "written")
        worksheet = (self.song_dir / "worksheet.md").read_text(encoding="utf-8")
        self.assertIn("LIVE `instrument-guitar-live`", worksheet)
        self.assertIn("manual cue: bars 3-4", worksheet)
        with zipfile.ZipFile(self.song_dir / "arrangement.gp") as zf:
            score_xml = zf.read("Content/score.gpif").decode("utf-8")
        self.assertNotIn("LIVE: bars 3-4", score_xml)
        self.assertEqual(verify_bundle(self.song_dir), [])

    def test_draft_build_never_touches_public_root(self):
        self._write_inputs(self.PARTIAL_RESOLUTIONS, self.DRAFT_PERFORMANCE)
        result = self._build(draft=True)
        self.assertEqual(result["status"], "written")
        self.assertFalse((self.song_dir / "worksheet.md").exists())
        self.assertFalse((self.song_dir / "arrangement.gp").exists())
        layout = Layout(self.song_dir)
        self.assertFalse(layout.plan_target.exists())
        preview_dirs = list(layout.preview_root.iterdir())
        self.assertEqual(len(preview_dirs), 1)
        self.assertTrue((preview_dirs[0] / "performance-arrangement.json").exists())
        self.assertTrue((preview_dirs[0] / "bundle.json").exists())

    def test_draft_status_requires_draft_flag(self):
        self._write_inputs(self.PARTIAL_RESOLUTIONS, self.DRAFT_PERFORMANCE)
        with self.assertRaises(Invalid):
            self._build(draft=False)

    def test_draft_flag_requires_draft_status(self):
        self._write_inputs(self.FINAL_RESOLUTIONS, self.FINAL_PERFORMANCE)
        with self.assertRaises(Invalid):
            self._build(draft=True)


class IdempotenceAndConflictTests(FinalizeBuildTestCase):
    def test_identical_rebuild_is_idempotent(self):
        self._write_inputs(self.FINAL_RESOLUTIONS, self.FINAL_PERFORMANCE)
        first = self._build()
        self.assertEqual(first["status"], "written")
        second = self._build()
        self.assertEqual(second["status"], "idempotent")
        self.assertEqual(second["generation"], first["generation"])

    def test_changed_resolutions_without_replace_is_blocked(self):
        self._write_inputs(self.FINAL_RESOLUTIONS, self.FINAL_PERFORMANCE)
        self._build()
        # Change resolutions/performance to Partial (different generation) and rebuild
        # without --replace: must be blocked, and public files must stay untouched.
        before = (self.song_dir / "worksheet.md").read_bytes()
        self._write_inputs(self.PARTIAL_RESOLUTIONS, self.PARTIAL_PERFORMANCE)
        result = self._build()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual((self.song_dir / "worksheet.md").read_bytes(), before)

    def test_changed_title_is_not_treated_as_idempotent(self):
        self._write_inputs(self.FINAL_RESOLUTIONS, self.FINAL_PERFORMANCE)
        first = self._build(title="First Title")
        self.assertEqual(first["status"], "written")
        second = self._build(title="Second Title")
        self.assertEqual(second["status"], "blocked")
        self.assertNotEqual(second.get("generation"), first["generation"])

    def test_replace_overwrites_with_new_generation(self):
        self._write_inputs(self.FINAL_RESOLUTIONS, self.FINAL_PERFORMANCE)
        first = self._build()
        self._write_inputs(self.PARTIAL_RESOLUTIONS, self.PARTIAL_PERFORMANCE)
        second = self._build(replace=True)
        self.assertEqual(second["status"], "written")
        self.assertNotEqual(second["generation"], first["generation"])
        text = (self.song_dir / "worksheet.md").read_text(encoding="utf-8")
        self.assertIn("Partial", text)

    def test_conflicting_pre_existing_files_without_bundle_blocked(self):
        self._write_inputs(self.FINAL_RESOLUTIONS, self.FINAL_PERFORMANCE)
        (self.song_dir / "worksheet.md").write_text("hand authored", encoding="utf-8")
        result = self._build()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual((self.song_dir / "worksheet.md").read_text(encoding="utf-8"),
                         "hand authored")

    def test_drifted_published_file_blocks_without_replace(self):
        self._write_inputs(self.FINAL_RESOLUTIONS, self.FINAL_PERFORMANCE)
        self._build()
        (self.song_dir / "worksheet.md").write_text("tampered", encoding="utf-8")
        result = self._build()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("drifted", result["reason"])
        # --replace forces a clean republish over the drift.
        fixed = self._build(replace=True)
        self.assertEqual(fixed["status"], "written")
        self.assertEqual(verify_bundle(self.song_dir), [])


class FailureInjectionTests(FinalizeBuildTestCase):
    def test_injected_failure_after_journal_rolls_back_on_recovery(self):
        self._write_inputs(self.FINAL_RESOLUTIONS, self.FINAL_PERFORMANCE)
        first = self._build()
        self.assertEqual(first["status"], "written")
        original_worksheet = (self.song_dir / "worksheet.md").read_bytes()
        original_gp = (self.song_dir / "arrangement.gp").read_bytes()
        original_plan = Layout(self.song_dir).plan_target.read_bytes()

        self._write_inputs(self.PARTIAL_RESOLUTIONS, self.PARTIAL_PERFORMANCE)
        with self.assertRaises(InjectedFailure):
            self._build(replace=True, inject_failure_after="replaced-performance_arrangement")

        layout = Layout(self.song_dir)
        self.assertTrue(layout.journal_path.exists())  # crash left the journal in place

        recovered = recover(layout)
        # The journal belonged to the interrupted *second* (Partial) attempt, not the
        # original successful (Final) generation it was trying to replace.
        self.assertIsNotNone(recovered["rolled_back_generation"])
        self.assertNotEqual(recovered["rolled_back_generation"], first["generation"])
        self.assertFalse(layout.journal_path.exists())
        self.assertEqual((self.song_dir / "worksheet.md").read_bytes(), original_worksheet)
        self.assertEqual((self.song_dir / "arrangement.gp").read_bytes(), original_gp)
        self.assertEqual(layout.plan_target.read_bytes(), original_plan)
        self.assertEqual(verify_bundle(self.song_dir), [])

    def test_next_build_call_recovers_automatically(self):
        self._write_inputs(self.FINAL_RESOLUTIONS, self.FINAL_PERFORMANCE)
        first = self._build()
        original_worksheet = (self.song_dir / "worksheet.md").read_bytes()

        self._write_inputs(self.PARTIAL_RESOLUTIONS, self.PARTIAL_PERFORMANCE)
        with self.assertRaises(InjectedFailure):
            self._build(replace=True, inject_failure_after="replaced-worksheet")

        # Re-supply the original (final) inputs and rebuild: the interrupted journal
        # from the aborted partial attempt must be rolled back first automatically,
        # then this call succeeds as an idempotent rebuild of the original bundle.
        self._write_inputs(self.FINAL_RESOLUTIONS, self.FINAL_PERFORMANCE)
        result = self._build()
        self.assertEqual(result["status"], "idempotent")
        self.assertEqual((self.song_dir / "worksheet.md").read_bytes(), original_worksheet)

    def test_staged_failure_before_journal_leaves_public_files_untouched(self):
        self._write_inputs(self.FINAL_RESOLUTIONS, self.FINAL_PERFORMANCE)
        with self.assertRaises(InjectedFailure):
            self._build(inject_failure_after="staged-verified")
        layout = Layout(self.song_dir)
        self.assertFalse(layout.worksheet_target.exists())
        self.assertFalse(layout.gp_target.exists())
        self.assertFalse(layout.bundle_path.exists())
        self.assertFalse(layout.journal_path.exists())


class PathContainmentTests(FinalizeBuildTestCase):
    def test_analysis_path_outside_song_dir_rejected(self):
        self._write_inputs(self.FINAL_RESOLUTIONS, self.FINAL_PERFORMANCE)
        outside = self.song_dir.parent / "outside-analysis.json"
        outside.write_text(self.analysis_path.read_text(encoding="utf-8"), encoding="utf-8")
        self.addCleanup(outside.unlink)
        with self.assertRaises(Invalid):
            finalize_build(self.song_dir, outside, self.resolutions_path,
                           self.performance_path, draft=False)

    def test_traversal_style_analysis_path_rejected(self):
        self._write_inputs(self.FINAL_RESOLUTIONS, self.FINAL_PERFORMANCE)
        traversal = self.song_dir / ".." / self.song_dir.name / "outside-analysis.json"
        with self.assertRaises(Invalid):
            finalize_build(self.song_dir, self.song_dir / ".." / "outside-analysis.json",
                           self.resolutions_path, self.performance_path)


class SourceDriftTests(FinalizeBuildTestCase):
    def test_source_asset_drift_detected(self):
        self._write_inputs(self.FINAL_RESOLUTIONS, self.FINAL_PERFORMANCE)
        asset_path = self.song_dir / self.rel_path
        asset_path.write_bytes(asset_path.read_bytes() + b"\x00")
        with self.assertRaises(Invalid):
            self._build()


class RecoverOnlyTests(FinalizeBuildTestCase):
    def test_recover_only_is_safe_with_no_journal(self):
        layout = Layout(self.song_dir)
        result = recover(layout)
        self.assertIsNone(result["rolled_back_generation"])


if __name__ == "__main__":
    unittest.main()
