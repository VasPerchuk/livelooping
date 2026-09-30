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

    def test_eval_set_covers_all_three_phases(self):
        data = json.loads((MUSIC / "prepare" / "evals" / "evals.json").read_text(encoding="utf-8"))
        self.assertEqual(data["skill_name"], "vp-music-prepare")
        self.assertEqual(len(data["evals"]), 3)
        self.assertEqual(len({item["id"] for item in data["evals"]}), 3)
        for item in data["evals"]:
            self.assertTrue(item["prompt"] and item["expected_output"])
            self.assertTrue(item["expectations"])


if __name__ == "__main__":
    unittest.main()
