from pathlib import Path
import tempfile
import unittest

from check_worksheet import check


class WorksheetCheckTest(unittest.TestCase):
    def test_legacy_layout_ignores_arrangement_handoff_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            handoff = song / ".vp-music-tracking" / "arrangement"
            handoff.mkdir(parents=True)
            (handoff / "arrangement.json").write_text("{}", encoding="utf-8")
            (song / "drums.patterns.md").write_text(
                "## Pattern: Drums A\n", encoding="utf-8"
            )
            (song / "worksheet.md").write_text(
                "# Legacy\n**Status: Final**\n**Tempo:** 120 BPM\n"
                "**Meter:** 4/4\n## Patterns (1 total)\n- **Drums A**\n"
                "## Structure\n### Intro - 1 bar (~2 seconds)\n"
                "Patterns: Drums A\n", encoding="utf-8"
            )
            self.assertEqual(check(song, require_final=True), [])

    def test_tracking_draft_and_published_final_with_transitions(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            tracking = song / ".vp-music-tracking"
            (tracking / "pattern").mkdir(parents=True)
            (tracking / "draft").mkdir()
            (tracking / "pattern" / "drums.patterns.md").write_text(
                "## Pattern: Drums A\n", encoding="utf-8"
            )
            content = (
                "# Test\n**Status: Draft**\n**Tempo:** quarter = 120 BPM\n"
                "**Meter:** 4/4\n## Patterns (1 total)\n- **Drums A** (1 bar)\n"
                "## Structure\n### Intro - 4 bars (~8 seconds)\n"
                "Patterns: Drums A\nTransition: Stop the drums on beat 1.\n"
            )
            (tracking / "draft" / "worksheet-draft.md").write_text(
                content, encoding="utf-8"
            )
            self.assertEqual(check(song), [])
            self.assertFalse((song / "worksheet.md").exists())
            (song / "worksheet.md").write_text(
                content.replace("Status: Draft", "Status: Final"), encoding="utf-8"
            )
            self.assertEqual(check(song, require_final=True), [])
            (tracking / "draft" / "worksheet-draft.md").unlink()
            self.assertTrue(
                any("no tracking draft" in e for e in check(song, require_final=True))
            )

    def test_tracking_final_requires_transition_and_warning_if_infeasible(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            (song / ".vp-music-tracking" / "pattern").mkdir(parents=True)
            (song / ".vp-music-tracking" / "draft").mkdir()
            (song / ".vp-music-tracking" / "draft" / "worksheet-draft.md").write_text(
                "# Working draft\n**Status: Draft**\n", encoding="utf-8"
            )
            (song / ".vp-music-tracking" / "pattern" / "drums.patterns.md").write_text(
                "## Pattern: Drums A\n", encoding="utf-8"
            )
            sheet = (
                "# Test\n**Status: Final**\n**Tempo:** 120 BPM\n**Meter:** 4/4\n"
                "## Patterns (1 total)\n- **Drums A**\n## Structure\n"
                "### Intro - 4 bars (~8 seconds)\nPatterns: Drums A\n"
            )
            (song / "worksheet.md").write_text(sheet, encoding="utf-8")
            self.assertTrue(any("Transition" in e for e in check(song, True)))
            (song / "worksheet.md").write_text(
                sheet + "Transition: INFEASIBLE - two pedals need pressing at once.\n",
                encoding="utf-8",
            )
            self.assertTrue(any("final warning" in e for e in check(song, True)))
            (song / "worksheet.md").write_text(
                sheet.replace("## Patterns", "**Transition warning:** Two pedals cannot be pressed together.\n\n## Patterns")
                + "Transition: INFEASIBLE - two pedals need pressing at once.\n",
                encoding="utf-8",
            )
            self.assertEqual(check(song, True), [])

    def test_partial_publication_requires_limitations(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            (song / "drums.patterns.md").write_text(
                "## Pattern: Drums A\n", encoding="utf-8"
            )
            sheet = (
                "# Test\n**Status: Partial**\n**Tempo:** 120 BPM\n**Meter:** 4/4\n"
                "## Patterns (1 total)\n- **Drums A**\n## Structure\n"
                "### Intro - 1 bar (~2 seconds)\nPatterns: Drums A\n"
            )
            (song / "worksheet.md").write_text(sheet, encoding="utf-8")
            errors = check(song, require_final=True)
            self.assertTrue(any("Limitations" in error for error in errors))
            (song / "worksheet.md").write_text(
                sheet.replace(
                    "## Patterns",
                    "**Limitations:** Lead part omitted by explicit approval.\n\n## Patterns",
                ),
                encoding="utf-8",
            )
            self.assertEqual(check(song, require_final=True), [])

    def test_rejects_duplicate_status_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            (song / "worksheet.md").write_text(
                "# Test\n**Status: Final**\n**Status: Partial**\n"
                "**Limitations:** uncertain\n## Patterns (0 total)\n"
                "## Structure\n### Break - 1 bar (~2 seconds)\nPatterns: none\n",
                encoding="utf-8",
            )
            self.assertTrue(any("exactly one" in error for error in check(song, True)))

    def test_tracking_rejects_legacy_pattern_mix(self):
        with tempfile.TemporaryDirectory() as directory:
            song = Path(directory)
            (song / ".vp-music-tracking" / "pattern").mkdir(parents=True)
            (song / ".vp-music-tracking" / "draft").mkdir()
            (song / "drums.patterns.md").write_text("## Pattern: Drums A\n", encoding="utf-8")
            (song / ".vp-music-tracking" / "draft" / "worksheet-draft.md").write_text(
                "# Test\n**Status: Draft**\n## Patterns (0 total)\n"
                "## Structure\n### Break - 1 bar (~2 seconds)\n"
                "Patterns: none\nTransition: Stop.\n", encoding="utf-8"
            )
            self.assertTrue(any("both legacy and tracking" in e for e in check(song)))
            (song / "research.md").write_text("# Legacy report\n", encoding="utf-8")
            self.assertTrue(any("research locations" in e for e in check(song)))

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
