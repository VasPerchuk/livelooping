"""Hermetic tests for the v2 pattern/worksheet Markdown renderer."""

import json
from pathlib import Path
import tempfile
import unittest

from render_views import render, render_pattern_markdown, render_worksheet_markdown


def sample_manifest(disposition="LOOP"):
    return {
        "version": 2,
        "tempo_bpm": 120,
        "meter": "4/4",
        "source": {
            "asset_relative_path": "source/song.gp",
            "asset_sha256": "a" * 64,
            "format": "gpif",
            "capabilities": ["performed_order"],
        },
        "tracks": [{
            "name": "Rhythm Guitar",
            "role": "rhythm",
            "role_confidence": "likely",
            "spans": [{
                "id": "rhythm-verse-a",
                "disposition": disposition,
                "match_tier": "near",
                "evidence": "near-identical riff, bar 4 diverges",
                "near_match": {"matching_bars": 3, "total_bars": 4, "divergent_bar_offsets": [3]},
                "source_ref": {
                    "asset_sha256": "a" * 64,
                    "asset_relative_path": "source/song.gp",
                    "track_index": 0,
                    "written_master_bars": [9, 12],
                    "performed_occurrences": [1, 2],
                },
            }],
        }],
        "decisions": [{
            "id": "decision-rhythm-verse-a",
            "question": "Preserve the fourth-bar variation or use the common loop?",
            "options": ["preserve_variants", "use_canonical", "play_live"],
        }] if disposition == "UNRESOLVED" else [],
        "patterns": [{
            "id": "pattern-rhythm-a", "name": "Rhythm A", "track": "Rhythm Guitar",
            "span_ids": ["rhythm-verse-a"], "bars": 2, "disposition": disposition,
            "confidence": "likely",
        }],
        "sections": [
            {"id": "sec-intro", "name": "Intro", "bars": 2, "active_pattern_ids": ["pattern-rhythm-a"]},
            {"id": "sec-break", "name": "Break", "bars": 1, "active_pattern_ids": [],
             "empty_policy": "rest"},
        ],
    }


class RenderContentTests(unittest.TestCase):
    def test_pattern_markdown_includes_ids_coordinates_tier_evidence_and_divergence(self):
        docs = render_pattern_markdown(sample_manifest("UNRESOLVED"))
        text = docs["Rhythm Guitar"]
        self.assertIn("pattern-rhythm-a", text)
        self.assertIn("rhythm-verse-a", text)
        self.assertIn("written master bars 9-12", text)
        self.assertIn("performed occurrences 1, 2", text)
        self.assertIn("match tier near", text)
        self.assertIn("near-identical riff", text)
        self.assertIn("diverges at bar offset(s) 3", text)
        self.assertIn("decision-rhythm-verse-a", text)
        self.assertIn("UNRESOLVED", text)

    def test_worksheet_markdown_reflects_sections_timings_and_dispositions(self):
        text = render_worksheet_markdown(sample_manifest("LOOP"), title="My Song")
        self.assertIn("# My Song", text)
        self.assertIn("**Status: Draft**", text)
        self.assertIn("quarter = 120 BPM", text)
        self.assertIn("### Intro - 2 bars (~4.0 seconds)", text)
        self.assertIn("Patterns: Rhythm A", text)
        self.assertIn("### Break - 1 bar (~2.0 seconds)", text)
        self.assertIn("Patterns: none (rest)", text)

    def test_fretted_source_generates_playable_string_grid(self):
        source = {
            "bars": [
                {"number": number, "start_quarters": (number - 1) * 4,
                 "length_quarters": 4}
                for number in range(9, 13)
            ],
            "tracks": [{
                "name": "Rhythm Guitar",
                "staves": [{"tuning_midi": [64, 59, 55, 50, 45, 40],
                            "capo_fret": 0}],
                "notes": [
                    {"bar": 9, "pitch": 64, "string": 1, "fret": 0,
                     "start_quarters": 32, "duration_quarters": 1},
                    {"bar": 10, "pitch": 57, "string": 2, "fret": 2,
                     "start_quarters": 37.5, "duration_quarters": 0.5},
                ],
            }],
        }
        text = render_pattern_markdown(
            sample_manifest("LOOP"), source_score=source)["Rhythm Guitar"]
        self.assertIn("String/fret grid", text)
        self.assertIn("lane | bar 9 | bar 10 | bar 11 | bar 12", text)
        self.assertIn("s1 | 1:0/1 | - | - | -", text)
        self.assertIn("s2 | - | 2.5:2/0.5 | - | -", text)

    def test_non_fretted_source_generates_pitch_grid(self):
        manifest = sample_manifest("LOOP")
        source = {
            "bars": [
                {"number": number, "start_quarters": (number - 1) * 4,
                 "length_quarters": 4}
                for number in range(9, 13)
            ],
            "tracks": [{
                "name": "Synth", "staves": [],
                "notes": [{"bar": 9, "pitch": 72, "start_quarters": 32,
                           "duration_quarters": 2}],
            }],
        }
        text = render_pattern_markdown(manifest, source_score=source)["Rhythm Guitar"]
        self.assertIn("Pitch grid", text)
        self.assertIn("p72 | 1:2 | - | - | -", text)

    def test_worksheet_markdown_is_none_without_sections(self):
        manifest = sample_manifest("LOOP")
        manifest["sections"] = []
        self.assertIsNone(render_worksheet_markdown(manifest))


class RenderFileSystemTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vp-render-")
        self.addCleanup(temp.cleanup)
        self.song_dir = Path(temp.name)
        self.manifest_path = self.song_dir / "arrangement.json"
        self.manifest_path.write_text(json.dumps(sample_manifest("LOOP")), encoding="utf-8")

    def test_first_render_writes_pattern_and_worksheet_files(self):
        result = render(self.manifest_path, self.song_dir, replace=False, title="Song")
        self.assertEqual(result["status"], "written")
        pattern_file = self.song_dir / ".vp-music-tracking" / "pattern" / "rhythm-guitar.patterns.md"
        worksheet_file = self.song_dir / ".vp-music-tracking" / "draft" / "worksheet-draft.md"
        self.assertTrue(pattern_file.is_file())
        self.assertTrue(worksheet_file.is_file())

    def test_rerender_without_replace_blocks_and_does_not_modify_existing_files(self):
        render(self.manifest_path, self.song_dir, replace=False, title="Song")
        pattern_file = self.song_dir / ".vp-music-tracking" / "pattern" / "rhythm-guitar.patterns.md"
        original = pattern_file.read_text(encoding="utf-8")
        pattern_file.write_text(original + "\nHUMAN EDIT\n", encoding="utf-8")
        edited = pattern_file.read_text(encoding="utf-8")

        result = render(self.manifest_path, self.song_dir, replace=False, title="Song")
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("rhythm-guitar.patterns.md" in c for c in result["conflicts"]))
        self.assertEqual(pattern_file.read_text(encoding="utf-8"), edited,
                         "no-clobber must leave the human-edited file untouched")

    def test_replace_mode_overwrites_atomically(self):
        render(self.manifest_path, self.song_dir, replace=False, title="Song")
        pattern_file = self.song_dir / ".vp-music-tracking" / "pattern" / "rhythm-guitar.patterns.md"
        pattern_file.write_text("stale content", encoding="utf-8")

        result = render(self.manifest_path, self.song_dir, replace=True, title="Song")
        self.assertEqual(result["status"], "written")
        self.assertIn("Rhythm A", pattern_file.read_text(encoding="utf-8"))
        self.assertNotIn("stale content", pattern_file.read_text(encoding="utf-8"))

    def test_atomic_write_leaves_no_temp_file_on_success(self):
        render(self.manifest_path, self.song_dir, replace=False, title="Song")
        pattern_dir = self.song_dir / ".vp-music-tracking" / "pattern"
        leftovers = [p for p in pattern_dir.iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [])

    def test_dry_run_reports_without_writing(self):
        result = render(self.manifest_path, self.song_dir, replace=False, title="Song", dry_run=True)
        self.assertEqual(result["status"], "dry-run")
        self.assertFalse((self.song_dir / ".vp-music-tracking").exists())

    def test_invalid_manifest_is_rejected_before_any_write(self):
        bad = sample_manifest("LOOP")
        bad["tracks"][0]["spans"][0]["disposition"] = "NOT_A_REAL_DISPOSITION"
        self.manifest_path.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(Exception):
            render(self.manifest_path, self.song_dir, replace=False, title="Song")
        self.assertFalse((self.song_dir / ".vp-music-tracking").exists())


if __name__ == "__main__":
    unittest.main()
