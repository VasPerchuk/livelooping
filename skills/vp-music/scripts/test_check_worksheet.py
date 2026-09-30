from pathlib import Path
import tempfile
import unittest

from check_worksheet import check


class WorksheetCheckTest(unittest.TestCase):
    def test_compound_meter_timing_and_pattern_references(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            (song / "drums.patterns.md").write_text(
                "# Drums\n\n## Pattern: Drums A\n\nCount |1 . . 2 . .|\n", encoding="utf-8"
            )
            worksheet = (
                "# Original tune\n\n**Status: Final**\n"
                "**Tempo:** dotted-quarter 80 BPM   **Meter:** 6/8\n\n"
                "## Patterns (1 total)\n- **Drums A** (1-bar loop)\n\n"
                "## Structure\n### Intro - 4 bars (~6 seconds)\nPatterns: Drums A\n"
            )
            (song / "worksheet.md").write_text(worksheet, encoding="utf-8")
            self.assertEqual(check(song, require_final=True), [])
            (song / "worksheet.md").write_text(
                worksheet.replace("~6 seconds", "~12 seconds"), encoding="utf-8"
            )
            self.assertTrue(any("disagrees" in error for error in check(song, True)))

    def test_reject_unresolved_or_undefined_patterns(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            (song / "worksheet.md").write_text(
                "# Test\n**Status: Draft**\n**Tempo:** 100 BPM **Meter:** 4/4\n\n"
                "## Patterns (1 total)\n- **Missing Drum**\n\n"
                "## Structure\n### Intro - 4 bars (~10 seconds)\nPatterns: Missing Drum\n",
                encoding="utf-8"
            )
            errors = check(song, True)
            self.assertTrue(any("not marked Final" in error for error in errors))
            self.assertTrue(any("undefined pattern" in error for error in errors))

    def test_accept_draft_with_decimal_seconds_and_scored_count(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            (song / "drums.patterns.md").write_text(
                "## Pattern: Drums A\n\nCount |1 2 3 4|\n", encoding="utf-8"
            )
            (song / "worksheet.md").write_text(
                "# Practice\n**Status: Draft**\n**Tempo:** quarter = 100 BPM\n"
                "**Meter:** 4/4\n## Patterns (1 verified total)\n"
                "- **Drums A** - one bar\n## Structure\n"
                "### Intro — 4 bars (~9.6 seconds; MIDI bars 1–4)\n"
                "**Patterns:** Drums A.\n",
                encoding="utf-8"
            )
            self.assertEqual(check(song), [])


if __name__ == "__main__":
    unittest.main()
