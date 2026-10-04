import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from phase_status import status


class PhaseStatusTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vp-phase-")
        self.addCleanup(temp.cleanup)
        self.song = Path(temp.name)

    def write_research(self):
        research = self.song / ".vp-music-tracking" / "research"
        research.mkdir(parents=True)
        (research / "research.md").write_text(
            "# Research\n\nSource identity and structure verified.\n", encoding="utf-8")
        (self.song / "chords.md").write_text(
            "# Chords\n\nVerse: Am | F\n", encoding="utf-8")

    def valid_analysis(self):
        source = self.song / "source.gp"
        source.write_bytes(b"fixture")
        return {
            "version": 2,
            "source": {
                "asset_relative_path": "source.gp",
                "asset_sha256": hashlib.sha256(b"fixture").hexdigest(),
                "format": "gpif",
            },
            "tracks": [{
                "id": "track-guitar", "name": "Guitar",
                "spans": [{"id": "rest", "disposition": "REST"}],
            }],
            "decisions": [],
            "patterns": [],
            "sections": [],
            "tempo_bpm": 120,
            "meter": "4/4",
        }

    def test_empty_song_has_no_verified_phase(self):
        report = status(self.song)
        self.assertEqual(report["status"], "ok")
        self.assertIsNone(report["latest_verified_phase"])

    def test_research_requires_both_record_and_chords(self):
        research = self.song / ".vp-music-tracking" / "research"
        research.mkdir(parents=True)
        (research / "research.md").write_text(
            "# Research\n\nSource identity verified.\n", encoding="utf-8")
        self.assertIsNone(status(self.song)["latest_verified_phase"])
        (self.song / "chords.md").write_text("# Chords\n\nVerse: Am\n", encoding="utf-8")
        self.assertEqual(status(self.song)["latest_verified_phase"], "research")

    def test_invalid_analysis_is_not_reported_as_verified(self):
        self.write_research()
        arrangement = self.song / ".vp-music-tracking" / "arrangement"
        arrangement.mkdir(parents=True)
        (arrangement / "arrangement.json").write_text("{bad", encoding="utf-8")
        report = status(self.song)
        self.assertEqual(report["status"], "conflict")
        self.assertEqual(report["latest_verified_phase"], "research")
        self.assertTrue(report["validation_errors"])

    def test_valid_analysis_advances_phase(self):
        self.write_research()
        arrangement = self.song / ".vp-music-tracking" / "arrangement"
        arrangement.mkdir(parents=True)
        (arrangement / "arrangement.json").write_text(
            json.dumps(self.valid_analysis()), encoding="utf-8")
        with patch("phase_status.inspect_music_asset", return_value={
                "status": "valid",
                "checksum_sha256": hashlib.sha256(b"fixture").hexdigest(),
                "bars": [], "tracks": [],
        }):
            report = status(self.song)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["latest_verified_phase"], "analysis")

    def test_missing_source_does_not_advance_analysis(self):
        self.write_research()
        arrangement = self.song / ".vp-music-tracking" / "arrangement"
        arrangement.mkdir(parents=True)
        raw = self.valid_analysis()
        (self.song / "source.gp").unlink()
        (arrangement / "arrangement.json").write_text(json.dumps(raw), encoding="utf-8")
        report = status(self.song)
        self.assertEqual(report["latest_verified_phase"], "research")
        self.assertEqual(report["status"], "conflict")

    def test_unrelated_large_file_is_not_in_managed_inventory(self):
        (self.song / "unrelated.bin").write_bytes(b"x" * (17 * 1024 * 1024))
        report = status(self.song)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["files"], [])


if __name__ == "__main__":
    unittest.main()
