"""Structural checks for the agent/skill discovery and phase contracts."""

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MUSIC = ROOT / "skills" / "vp-music"
AGENTS = ROOT / ".github" / "agents"
SKILLS = ROOT / ".github" / "skills"
NAMES = ("prepare", "research", "analyze", "finalize")


class DefinitionsTest(unittest.TestCase):
    def test_project_agents_point_to_existing_canonical_files(self):
        for name in NAMES:
            with self.subTest(agent=name):
                agent = AGENTS / f"vp-music-{name}.agent.md"
                content = agent.read_text(encoding="utf-8")
                self.assertIn(f"name: vp-music-{name}\n", content)
                self.assertIn("skills\\vp-music\\CONTRACT.md", content)
                self.assertIn(f"roles\\vp-music-{name}.md", content)
                self.assertTrue((MUSIC / "roles" / f"vp-music-{name}.md").is_file())
                self.assertTrue((MUSIC / name / "SKILL.md").is_file())

    def test_project_skills_have_distinct_names_and_canonical_files(self):
        canonical = {
            "prepare": "prepare",
            "research": "research",
            "analyze": "analyze",
            "finalize": "finalize",
            "worksheet": "performance-worksheet",
            "arrangement": "arrangement",
        }
        for name, folder in canonical.items():
            with self.subTest(skill=name):
                adapter = (SKILLS / f"vp-music-{name}" / "SKILL.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn(f"name: vp-music-{name}\n", adapter)
                self.assertTrue((MUSIC / folder / "SKILL.md").is_file())

    def test_research_and_analyze_stop_at_approval_gate(self):
        contract = (MUSIC / "CONTRACT.md").read_text(encoding="utf-8")
        self.assertIn("stop for approval", contract)
        self.assertIn("never pre-authorize subsequent phases", contract)
        for name in ("research", "analyze"):
            content = (MUSIC / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertRegex(content, re.compile(r"\bapproval\b", re.IGNORECASE))
        self.assertIn("**Status: Draft**", (MUSIC / "analyze" / "SKILL.md").read_text(encoding="utf-8"))

    def test_layout_source_gate_and_transitions_are_defined(self):
        contract = (MUSIC / "CONTRACT.md").read_text(encoding="utf-8")
        research = (MUSIC / "research" / "SKILL.md").read_text(encoding="utf-8")
        analyze = (MUSIC / "analyze" / "SKILL.md").read_text(encoding="utf-8")
        final = (MUSIC / "finalize" / "SKILL.md").read_text(encoding="utf-8")
        worksheet = (MUSIC / "performance-worksheet" / "SKILL.md").read_text(encoding="utf-8")
        for path in (
            r".vp-music-tracking\research\research.md",
            r".vp-music-tracking\pattern\<instrument>.patterns.md",
            r".vp-music-tracking\draft\worksheet-draft.md",
        ):
            self.assertIn(path, contract)
        self.assertIn("ask once", research)
        self.assertIn("PDF", research)
        self.assertIn("MIDI", research)
        self.assertIn("pause", research.lower())
        self.assertIn(r"draft\worksheet-draft.md", analyze)
        self.assertIn(r"draft\worksheet-draft.md", final)
        self.assertIn("Transition:", worksheet)
        self.assertIn("INFEASIBLE", worksheet)
        self.assertTrue((MUSIC / "analyze" / "references" / "midi-pitches.md").is_file())
        self.assertTrue((MUSIC / "analyze" / "references" / "arrangement-handoff.md").is_file())
        self.assertIn(r".vp-music-tracking\arrangement\arrangement.json", contract)
        self.assertIn(r"performance-arrangement.json", contract)
        self.assertIn(r"env-defaults.md", contract)
        self.assertIn("arrangement.gp", contract)
        self.assertIn(r"arrangement\SKILL.md", final)
        self.assertIn("conflict-resolution questionnaire", final)
        self.assertIn("performance-defaults questionnaire", final)
        self.assertIn("Status: Partial", final)

    def test_research_excludes_recording_acquisition_and_streaming_audio(self):
        research = (MUSIC / "research" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Do not download, rip, capture", research)
        self.assertIn("identity metadata", research)
        self.assertIn("audio-feature", research)
        self.assertIn(r"env-defaults.md", research)

    def test_environment_defaults_are_finalization_only(self):
        contract = (MUSIC / "CONTRACT.md").read_text(encoding="utf-8")
        analyze = (MUSIC / "analyze" / "SKILL.md").read_text(encoding="utf-8")
        final = (MUSIC / "finalize" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("read\nonly during finalization", contract)
        self.assertIn("Do not read", analyze)
        self.assertIn("Only now read", final)
        self.assertIn("separately ask whether to replace", final)

    def test_private_performance_identifiers_are_isolated(self):
        defaults = ROOT / ".vp-music-tracking" / "env-defaults.md"
        text = defaults.read_text(encoding="utf-8")
        match = re.search(r"private-identifiers:\s*(.*?)\s*-->", text)
        self.assertIsNotNone(match)
        identifiers = match.group(1).split("|")
        files = [
            *MUSIC.rglob("*.md"),
            *MUSIC.rglob("*.json"),
            *AGENTS.rglob("*.md"),
            *SKILLS.rglob("*.md"),
            *(ROOT / "songs").rglob("*.md"),
        ]
        for identifier in identifiers:
            with self.subTest(identifier=identifier):
                matches = [
                    str(path.relative_to(ROOT))
                    for path in files
                    if identifier.casefold() in path.read_text(
                        encoding="utf-8", errors="replace"
                    ).casefold()
                ]
                self.assertEqual(matches, [])

    def test_eval_set_covers_all_three_phases(self):
        data = json.loads((MUSIC / "prepare" / "evals" / "evals.json").read_text(encoding="utf-8"))
        self.assertEqual(data["skill_name"], "vp-music-prepare")
        self.assertGreaterEqual(len(data["evals"]), 10)
        self.assertEqual(len({item["id"] for item in data["evals"]}), len(data["evals"]))
        for item in data["evals"]:
            self.assertTrue(item["prompt"] and item["expected_output"])
            self.assertTrue(item["expectations"])


if __name__ == "__main__":
    unittest.main()
