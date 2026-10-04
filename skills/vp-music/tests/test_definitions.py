"""Portable structural checks for the canonical vp-music skills and adapters."""

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
MUSIC = ROOT / "skills" / "vp-music"
AGENTS = ROOT / ".github" / "agents"
SKILLS = ROOT / ".github" / "skills"

PHASES = ("prepare", "research", "analyze", "finalize")


class DiscoveryAdapterTests(unittest.TestCase):
    def test_agents_link_directly_to_contract_and_canonical_skill(self):
        for name in PHASES:
            with self.subTest(name=name):
                text = (AGENTS / f"vp-music-{name}.agent.md").read_text(encoding="utf-8")
                self.assertIn(f"name: vp-music-{name}\n", text)
                self.assertIn(r"skills\vp-music\CONTRACT.md", text)
                self.assertIn(fr"skills\vp-music\{name}\SKILL.md", text)
                self.assertNotIn(r"roles\vp-music", text)

    def test_skill_adapters_link_to_existing_canonical_files(self):
        for adapter in SKILLS.glob("vp-music-*"):
            text = (adapter / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("skills\\vp-music\\", text)


class CanonicalContractTests(unittest.TestCase):
    def test_phase_skills_have_matching_frontmatter_and_are_concise(self):
        for name in PHASES:
            with self.subTest(name=name):
                path = MUSIC / name / "SKILL.md"
                text = path.read_text(encoding="utf-8")
                self.assertIn(f"name: vp-music-{name}\n", text)
                self.assertLess(len(text.splitlines()), 500)

    def test_contract_has_approval_and_generated_artifact_boundaries(self):
        text = (MUSIC / "CONTRACT.md").read_text(encoding="utf-8")
        for phrase in (
            "Stop for approval after research and after analysis",
            "never\npre-authorizes subsequent phases",
            r".vp-music-tracking\arrangement\arrangement.json",
            "finalize_build.py",
            "generated pattern files",
        ):
            self.assertIn(phrase, text)

    def test_retired_output_paths_are_not_documented_as_active(self):
        files = [
            MUSIC / "CONTRACT.md",
            MUSIC / "prepare" / "SKILL.md",
            MUSIC / "analyze" / "SKILL.md",
            MUSIC / "finalize" / "SKILL.md",
            MUSIC / "arrangement" / "SKILL.md",
            MUSIC / "performance-worksheet" / "SKILL.md",
            MUSIC / "analyze" / "references" / "arrangement-handoff.md",
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in files)
        self.assertNotIn("migrate_v2.py", text)
        self.assertNotIn("v1-only", text)
        self.assertNotIn("--write-receipt", text)

    def test_pattern_output_contract_requires_generated_playable_grids(self):
        analyze = (MUSIC / "analyze" / "SKILL.md").read_text(encoding="utf-8")
        contract = (MUSIC / "CONTRACT.md").read_text(encoding="utf-8")
        self.assertIn("generated playable string/fret, pitch, or percussion grids", analyze)
        self.assertIn("ASCII grids are short performance aids", contract)

    def test_research_rights_wording_does_not_equate_access_with_license(self):
        text = (MUSIC / "research" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("does not establish a right to reproduce", text)


class EvalDefinitionTests(unittest.TestCase):
    def test_phase_eval_files_are_well_formed(self):
        for name in PHASES:
            path = MUSIC / name / "evals" / "evals.json"
            if not path.exists():
                continue
            with self.subTest(name=name):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["skill_name"], f"vp-music-{name}")
                self.assertTrue(data["evals"])
                for case in data["evals"]:
                    self.assertTrue(case["prompt"].strip())
                    self.assertTrue(case["expected_output"].strip())


if __name__ == "__main__":
    unittest.main()
