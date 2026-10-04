"""Hermetic published-worksheet checks against a compiled performance plan."""

import json
import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from check_worksheet import check_published_against_plan
from compile_performance import compile_performance_arrangement
from render_views import render_published
from test_compile_performance import multi_instrument_manifest, multi_instrument_resolutions


def _compile_and_render(song, *, resolutions=None, publication=None):
    manifest = multi_instrument_manifest()
    resolutions = resolutions if resolutions is not None else multi_instrument_resolutions(
        rhythm_option="use_canonical", keys_fill_option="play_live")
    publication = publication or {"status": "final", "limitations": [],
                                  "tail_policy": "reviewed_truncate"}
    output, errors = compile_performance_arrangement(manifest, resolutions, publication)
    assert errors == [], errors
    plan_path = song / "performance-arrangement.json"
    plan_path.write_text(json.dumps(output), encoding="utf-8")
    result = render_published(plan_path, song, replace=False, title="Song")
    assert result["status"] == "written", result
    return plan_path


class FinalParityTests(unittest.TestCase):
    def test_clean_final_worksheet_has_no_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            plan_path = _compile_and_render(song)
            self.assertEqual(check_published_against_plan(song, plan_path), [])

    def test_status_mismatch_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            plan_path = _compile_and_render(song)
            worksheet = song / "worksheet.md"
            worksheet.write_text(
                worksheet.read_text(encoding="utf-8").replace("**Status: Final**",
                                                              "**Status: Partial**"),
                encoding="utf-8")
            errors = check_published_against_plan(song, plan_path)
            self.assertTrue(any("disagrees with plan publication.status" in e for e in errors),
                            errors)

    def test_bar_count_mismatch_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            plan_path = _compile_and_render(song)
            worksheet = song / "worksheet.md"
            worksheet.write_text(
                worksheet.read_text(encoding="utf-8").replace("output bars 1-2, 2 bars",
                                                              "output bars 1-3, 3 bars"),
                encoding="utf-8")
            errors = check_published_against_plan(song, plan_path)
            self.assertTrue(any("heading disagrees with plan" in e for e in errors), errors)

    def test_time_mismatch_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            plan_path = _compile_and_render(song)
            worksheet = song / "worksheet.md"
            worksheet.write_text(
                worksheet.read_text(encoding="utf-8").replace("~48.0 seconds", "~99.0 seconds"),
                encoding="utf-8")
            errors = check_published_against_plan(song, plan_path)
            self.assertTrue(any("total seconds" in e for e in errors), errors)

    def test_missing_active_track_id_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            plan_path = _compile_and_render(song)
            worksheet = song / "worksheet.md"
            text = worksheet.read_text(encoding="utf-8")
            text = text.replace("instrument-bass-loop-bass-chorus", "deleted-track-id")
            worksheet.write_text(text, encoding="utf-8")
            errors = check_published_against_plan(song, plan_path)
            self.assertTrue(any("missing active output track id" in e for e in errors), errors)

    def test_missing_live_cue_disclosure_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            plan_path = _compile_and_render(song)
            worksheet = song / "worksheet.md"
            text = worksheet.read_text(encoding="utf-8")
            self.assertIn("manual cue: bars 5-8", text)
            worksheet.write_text(text.replace("manual cue: bars 5-8", "hidden"),
                                 encoding="utf-8")
            errors = check_published_against_plan(song, plan_path)
            self.assertTrue(any("missing LIVE disclosure" in e for e in errors), errors)

    def test_deletion_removed_from_worksheet_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            resolutions = multi_instrument_resolutions(rhythm_option="use_canonical",
                                                        keys_fill_option="play_live")
            resolutions["section_actions"] = [{"section_id": "sec-solo", "action": "delete",
                                              "approved": True,
                                              "reason": "Cut the solo; segue to Bridge."}]
            publication = {"status": "partial", "limitations": ["Solo removed."],
                          "consent": "User accepted the labeled preview/partial gaps.",
                          "tail_policy": "reviewed_truncate"}
            plan_path = _compile_and_render(song, resolutions=resolutions, publication=publication)
            self.assertEqual(check_published_against_plan(song, plan_path), [])

            worksheet = song / "worksheet.md"
            text = worksheet.read_text(encoding="utf-8")
            corrupted = text.replace("- DELETED section `sec-solo` \"Solo\", 4 bars removed "
                                     "(section_actions[sec-solo]) — Cut the solo; segue to "
                                     "Bridge.\n", "")
            self.assertNotEqual(corrupted, text, "fixture's DELETED line text must match exactly")
            worksheet.write_text(corrupted, encoding="utf-8")
            errors = check_published_against_plan(song, plan_path)
            self.assertTrue(any("missing deleted section instance" in e for e in errors), errors)

    def test_review_required_omission_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            resolutions = multi_instrument_resolutions(rhythm_option="use_canonical",
                                                        keys_fill_option="play_live")
            resolutions["section_actions"] = [{"section_id": "sec-solo", "action": "delete",
                                              "approved": True}]
            publication = {"status": "partial", "limitations": ["Solo removed."],
                          "consent": "User accepted the labeled preview/partial gaps.",
                          "tail_policy": "reviewed_truncate"}
            plan_path = _compile_and_render(song, resolutions=resolutions, publication=publication)
            self.assertEqual(check_published_against_plan(song, plan_path), [])

            worksheet = song / "worksheet.md"
            text = worksheet.read_text(encoding="utf-8")
            corrupted = text.replace("REVIEW REQUIRED", "all good")
            worksheet.write_text(corrupted, encoding="utf-8")
            errors = check_published_against_plan(song, plan_path)
            self.assertTrue(any("missing a REVIEW REQUIRED flag" in e for e in errors), errors)

    def test_limitations_mismatch_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            resolutions = multi_instrument_resolutions(rhythm_option="use_canonical",
                                                        keys_fill_option="defer_unresolved")
            publication = {"status": "partial", "limitations": ["Keys fill deferred."],
                          "consent": "User accepted the labeled preview/partial gaps.",
                          "tail_policy": "reviewed_truncate"}
            plan_path = _compile_and_render(song, resolutions=resolutions, publication=publication)
            self.assertEqual(check_published_against_plan(song, plan_path), [])

            worksheet = song / "worksheet.md"
            worksheet.write_text(
                worksheet.read_text(encoding="utf-8").replace("Keys fill deferred.",
                                                              "Something else."),
                encoding="utf-8")
            errors = check_published_against_plan(song, plan_path)
            self.assertTrue(any("Limitations" in e and "disagrees" in e for e in errors), errors)

    def test_unresolved_disclosure_mismatch_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            resolutions = multi_instrument_resolutions(rhythm_option="use_canonical",
                                                        keys_fill_option="defer_unresolved")
            publication = {"status": "partial", "limitations": ["Keys fill deferred."],
                          "consent": "User accepted the labeled preview/partial gaps.",
                          "tail_policy": "reviewed_truncate"}
            plan_path = _compile_and_render(song, resolutions=resolutions, publication=publication)

            worksheet = song / "worksheet.md"
            text = worksheet.read_text(encoding="utf-8")
            corrupted = text.replace("## Unresolved (Partial)\n"
                                     "- `pattern-keys-fill` \"Keys Fill\" — unresolved; see "
                                     "Limitations.\n", "")
            self.assertNotEqual(corrupted, text, "fixture's Unresolved block text must match")
            worksheet.write_text(corrupted, encoding="utf-8")
            errors = check_published_against_plan(song, plan_path)
            self.assertTrue(any("missing the ## Unresolved" in e for e in errors), errors)

    def test_bad_plan_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            plan_path = song / "performance-arrangement.json"
            plan_path.write_text(json.dumps({"version": 1}), encoding="utf-8")
            (song / "worksheet.md").write_text("# Song\n", encoding="utf-8")
            errors = check_published_against_plan(song, plan_path)
            self.assertTrue(any("only version 2 performance plans" in e for e in errors), errors)

    def test_missing_worksheet_file_reports_error(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            resolutions = multi_instrument_resolutions(rhythm_option="use_canonical",
                                                        keys_fill_option="play_live")
            manifest = multi_instrument_manifest()
            output, errors = compile_performance_arrangement(
                manifest, resolutions, {"status": "final", "limitations": [],
                                        "tail_policy": "reviewed_truncate"})
            self.assertEqual(errors, [])
            plan_path = song / "performance-arrangement.json"
            plan_path.write_text(json.dumps(output), encoding="utf-8")
            errors = check_published_against_plan(song, plan_path)
            self.assertTrue(errors)

    def test_swapped_section_actions_are_detected_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            manifest = multi_instrument_manifest()
            resolutions = multi_instrument_resolutions(
                rhythm_option="use_canonical", keys_fill_option="play_live")
            performance = {
                "status": "final", "limitations": [], "tail_policy": "reviewed_truncate",
                "performance": {"section_transitions": [
                    {"section_id": section["id"], "action": f"Action {index}",
                     "feasible": True}
                    for index, section in enumerate(manifest["sections"])
                ]},
            }
            output, errors = compile_performance_arrangement(
                manifest, resolutions, performance)
            self.assertEqual(errors, [])
            plan_path = song / "performance-arrangement.json"
            plan_path.write_text(json.dumps(output), encoding="utf-8")
            render_published(plan_path, song, replace=False, title="Song")
            worksheet = song / "worksheet.md"
            text = worksheet.read_text(encoding="utf-8")
            text = text.replace("Transition: Action 0", "Transition: SWAP")
            text = text.replace("Transition: Action 1", "Transition: Action 0")
            text = text.replace("Transition: SWAP", "Transition: Action 1")
            worksheet.write_text(text, encoding="utf-8")
            errors = check_published_against_plan(song, plan_path)
            self.assertTrue(any("section #1" in error for error in errors), errors)
            self.assertTrue(any("section #2" in error for error in errors), errors)

if __name__ == "__main__":
    unittest.main()
