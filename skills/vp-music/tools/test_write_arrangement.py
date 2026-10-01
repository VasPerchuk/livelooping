"""Synthetic, rights-independent end-to-end tests for the GP7 arrangement writer."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from music_assets import inspect

HERE = Path(__file__).resolve().parent
WRITER = HERE / "write_arrangement.mjs"


def worksheet_text(manifest):
    names = [p["name"] for track in manifest["tracks"] for p in track["patterns"]]
    legend = "\n".join(f"- **{name}** (1 bar) - synthetic" for name in names)
    numerator, denominator = map(int, manifest["meter"].split("/"))
    structure = "\n".join(
        f"### {s['name']} — {s['bars']} bars "
        f"(~{round(s['bars'] * numerator * 4 / denominator * 60 / manifest['tempo_bpm'])} seconds)\n"
        f"Patterns: {', '.join(s['patterns']) if s['patterns'] else 'none'}\n"
        f"Transition: Continue."
        for s in manifest["sections"]
    )
    publication = manifest.get("publication", {"status": "final", "limitations": []})
    status = "Partial" if publication["status"] == "partial" else "Final"
    limitations = (
        f"**Limitations:** {'; '.join(publication['limitations'])}\n"
        if status == "Partial" else ""
    )
    return (f"# Synthetic\n\n**Status: {status}**\n{limitations}"
            f"**Tempo:** quarter = {manifest['tempo_bpm']} BPM  "
            f"**Meter:** {manifest['meter']}\n\n"
            f"## Patterns ({len(names)} total)\n{legend}\n\n## Structure\n{structure}\n")


def sample():
    return {
        "version": 1, "tempo_bpm": 108, "meter": "4/4",
        "tracks": [
            {"name": "Synthetic guitar", "kind": "fretted",
             "source": "Original synthetic events", "confidence": "verified",
             "tuning_midi": [40, 45, 50, 55, 59, 64], "capo_fret": 2,
             "patterns": [
                 {"name": "picked", "bars": 2, "source": "Synthetic score bars 1-2",
                  "confidence": "likely", "notes": [
                     {"bar": 1, "offset_quarters": 0, "duration_quarters": 1,
                      "string": 1, "fret": 3, "pitch": 45},
                     {"bar": 1, "offset_quarters": 0, "duration_quarters": 1,
                      "string": 3, "fret": 0},
                     {"bar": 2, "offset_quarters": 0.5, "duration_quarters": 0.5,
                      "string": 2, "fret": 2},
                 ]},
            ]},
            {"name": "Synthetic keys", "kind": "pitched", "patterns": [
                {"name": "pulse", "bars": 1, "source": "Original synthetic score",
                 "confidence": "confirmed", "notes": [
                    {"bar": 1, "offset_quarters": 1, "duration_quarters": 0.25,
                     "pitch": 60},
                    {"bar": 1, "offset_quarters": 2, "duration_quarters": 2,
                     "pitch": 64},
                ]},
            ]},
            {"name": "Synthetic drums", "kind": "percussion", "patterns": [
                {"name": "kick", "bars": 1, "source": "Original synthetic score",
                 "confidence": "confirmed", "notes": [
                    {"bar": 1, "offset_quarters": 0, "duration_quarters": 0.5,
                     "pitch": 36},
                ]},
            ]},
        ],
        "sections": [
            {"name": "A", "bars": 4, "patterns": ["picked", "pulse", "kick"]},
            {"name": "B", "bars": 3, "patterns": ["picked", "pulse"],
             "tail_policy": "truncate"},
            {"name": "Live only", "bars": 1, "patterns": [], "empty_policy": "live"},
        ],
    }


class ArrangementWriterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vp-arrangement-")
        self.addCleanup(temp.cleanup)
        self.song_dir = Path(temp.name)
        self.manifest = self.song_dir / ".vp-music-tracking" / "arrangement" / "arrangement.json"
        self.manifest.parent.mkdir(parents=True)
        self.output = self.song_dir / "arrangement.gp"
        self.worksheet = self.song_dir / "worksheet.md"
        self.patterns = self.song_dir / "synthetic.patterns.md"

    def run_writer(self, manifest, worksheet=None, pattern_names=None,
                   use_worksheet=False, tracking_patterns=False, create_pattern_files=True):
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        self.worksheet.write_text(
            worksheet if worksheet is not None else worksheet_text(manifest), encoding="utf-8")
        names = (pattern_names if pattern_names is not None else
                 [p["name"] for t in manifest["tracks"] for p in t["patterns"]])
        if create_pattern_files:
            pattern_file = self.patterns
            if tracking_patterns:
                pattern_dir = self.song_dir / ".vp-music-tracking" / "pattern"
                pattern_dir.mkdir(parents=True, exist_ok=True)
                pattern_file = pattern_dir / "synthetic.patterns.md"
            pattern_file.write_text("\n".join(f"## Pattern: {n}" for n in names),
                                    encoding="utf-8")
        return subprocess.run(
            ["node", str(WRITER), "--manifest", str(self.manifest),
             "--worksheet" if use_worksheet else "--song-dir",
             str(self.worksheet if use_worksheet else self.song_dir),
             "--output", str(self.output)],
            capture_output=True, text=True, timeout=90, check=False,
        )

    def test_multitrack_sections_repeats_tail_and_exact_events(self):
        result = self.run_writer(sample())
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(self.output)
        self.assertEqual((actual["status"], actual["format"]), ("valid", "gp-zip"))
        validated = subprocess.run(
            [sys.executable, str(HERE / "music_assets.py"), "validate",
             "--path", str(self.output)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        self.assertEqual((actual["track_count"], actual["bar_count"]), (3, 8))
        self.assertEqual(actual["tempos"],
                         [{"position_quarters": 0.0, "bpm": 108.0}])
        self.assertEqual([b["start_quarters"] for b in actual["bars"]],
                         [0, 4, 8, 12, 16, 20, 24, 28])
        self.assertTrue(all(b["meter"] == "4/4" for b in actual["bars"]))
        guitar, keys, drums = [t["notes"] for t in actual["tracks"]]
        self.assertEqual(
            [(n["bar"], n["start_quarters"], n["duration_quarters"],
              n["pitch"], n["string"], n["fret"]) for n in guitar],
            [(bar, (bar - 1) * 4, 1, pitch, string, fret)
             if bar % 2 else (bar, (bar - 1) * 4 + .5, .5, 49, 2, 2)
             for bar in range(1, 8)
             for pitch, string, fret in ([(45, 1, 3), (52, 3, 0)]
                                         if bar % 2 else [(49, 2, 2)])],
        )
        self.assertEqual(
            [(n["bar"], n["start_quarters"], n["duration_quarters"],
              n["pitch"], n["string"], n["fret"]) for n in keys],
            [(bar, (bar - 1) * 4 + offset, duration, pitch, None, None)
             for bar in range(1, 8)
             for offset, duration, pitch in [(1, .25, 60), (2, 2, 64)]],
        )
        self.assertEqual(
            [(n["bar"], n["start_quarters"], n["duration_quarters"],
              n["pitch"], n["string"], n["fret"]) for n in drums],
            [(bar, (bar - 1) * 4, .5, 36, None, None) for bar in range(1, 5)],
        )
        self.assertEqual(actual["tracks"][0]["staves"][0]["tuning_midi"],
                         [40, 45, 50, 55, 59, 64])
        self.assertEqual(actual["tracks"][0]["staves"][0]["capo_fret"], 2)

    def test_rejects_existing_output_without_modifying_it(self):
        self.output.write_bytes(b"leave untouched")
        result = self.run_writer(sample())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("output already exists", result.stderr)
        self.assertEqual(self.output.read_bytes(), b"leave untouched")

    def test_exports_explicit_partial_with_visible_limitations(self):
        manifest = sample()
        manifest["publication"] = {
            "status": "partial",
            "limitations": [
                "Lead part omitted",
                "Ending rhythm is an approved estimate",
            ],
            "consent": "User accepted these disclosed limitations for rehearsal.",
        }
        manifest["performance"] = {
            "players": 2,
            "roles": ["Player 1 performs the live lead", "Player 2 controls loops"],
            "transitions": ["A to B uses one prepared state change"],
        }
        result = self.run_writer(
            manifest,
            pattern_names=["picked", "pulse", "kick", "omitted source lead"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(self.output)
        self.assertEqual((actual["status"], actual["format"]), ("valid", "gp-zip"))

    def test_partial_requires_consent_limitations_and_matching_worksheet(self):
        base = sample()
        base["publication"] = {
            "status": "partial",
            "limitations": ["Bass omitted"],
            "consent": "Approved for rehearsal.",
        }
        cases = {
            "missing consent": lambda m: m["publication"].pop("consent"),
            "missing limitations": lambda m: m["publication"].update(limitations=[]),
        }
        for label, change in cases.items():
            with self.subTest(label=label):
                manifest = copy.deepcopy(base)
                change(manifest)
                result = self.run_writer(manifest)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())
        final_sheet = worksheet_text(sample())
        result = self.run_writer(base, worksheet=final_sheet)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("**Status: Partial**", result.stderr)
        self.assertFalse(self.output.exists())

    def test_final_manifest_rejects_partial_consent_or_limitations(self):
        manifest = sample()
        manifest["publication"] = {
            "status": "final",
            "limitations": ["Should not be present"],
        }
        result = self.run_writer(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_explicit_worksheet_and_tracking_pattern_layout(self):
        result = self.run_writer(sample(), use_worksheet=True, tracking_patterns=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(inspect(self.output)["status"], "valid")
        self.assertFalse(self.patterns.exists())

    def test_accepts_checker_compatible_worksheet_annotations(self):
        sheet = worksheet_text(sample()).replace(
            "## Patterns (3 total)", "## Patterns (3 recurring loops total)"
        ).replace(
            "### A — 4 bars (~9 seconds)",
            "### A — 4 bars (~9 seconds; score bars 1–4)."
        ).replace(
            "Patterns: picked, pulse, kick",
            "Patterns: picked (loop 1), pulse, kick."
        ).replace("Patterns: none\n", "Patterns: none.\n")
        result = self.run_writer(sample(), worksheet=sheet)
        self.assertEqual(result.returncode, 0, result.stderr)
        checker = subprocess.run(
            [sys.executable, str(HERE.parent / "scripts" / "check_worksheet.py"),
             "--song-dir", str(self.song_dir), "--require-final"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(checker.returncode, 0, checker.stderr)

    def test_requires_published_worksheet_or_song_dir(self):
        self.manifest.write_text(json.dumps(sample()), encoding="utf-8")
        result = subprocess.run(
            ["node", str(WRITER), "--manifest", str(self.manifest),
             "--output", str(self.output)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--song-dir", result.stderr)
        self.assertFalse(self.output.exists())

    def test_rejects_tempo_and_meter_event_maps_explicitly(self):
        cases = [
            (lambda m: m.update(tempo_map=[]), "tempo_map"),
            (lambda m: m.update(meter_events=[]), "meter_events"),
            (lambda m: m["sections"][0].update(time_signature_changes=[]),
             "time_signature_changes"),
            (lambda m: m["tracks"][0]["patterns"][0].update(tempo_events=[]),
             "tempo_events"),
        ]
        for change, key in cases:
            with self.subTest(key=key):
                manifest = sample()
                change(manifest)
                result = self.run_writer(manifest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"{key} is an event map/change list", result.stderr)
                self.assertFalse(self.output.exists())

    def test_published_worksheet_and_pattern_headings_are_authoritative(self):
        baseline = sample()
        published = worksheet_text(baseline)
        cases = {
            "section name": (lambda m: m["sections"][0].update(name="Renamed"), {}),
            "section order": (lambda m: m["sections"].reverse(), {}),
            "bar count": (lambda m: m["sections"][0].update(bars=2), {}),
            "active pattern": (lambda m: m["sections"][0].update(patterns=["pulse", "picked", "kick"]), {}),
            "legend mismatch": (lambda m: None,
                                {"worksheet": published.replace("**kick**", "**other**")}),
            "tempo mismatch": (lambda m: None,
                               {"worksheet": published.replace("108 BPM", "120 BPM")}),
            "meter mismatch": (lambda m: None,
                               {"worksheet": published.replace("**Meter:** 4/4", "**Meter:** 3/4")}),
            "pattern file mismatch": (lambda m: None,
                                      {"pattern_names": ["picked", "pulse", "not kick"]}),
        }
        for label, (mutate, overrides) in cases.items():
            with self.subTest(label=label):
                manifest = copy.deepcopy(baseline)
                mutate(manifest)
                result = self.run_writer(manifest, **{"worksheet": published, **overrides})
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse(self.output.exists())
                self.assertFalse(list(self.song_dir.glob(f".{self.output.name}.*.stage.gp")))

    def test_rejects_draft_or_missing_final_status(self):
        for worksheet in (
            worksheet_text(sample()).replace("**Status: Final**", "**Status: Draft**"),
            worksheet_text(sample()).replace("**Status: Final**\n", ""),
        ):
            with self.subTest(worksheet=worksheet.splitlines()[2:3]):
                result = self.run_writer(sample(), worksheet=worksheet)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("**Status: Final**", result.stderr)
                self.assertFalse(self.output.exists())

    def test_rejects_missing_or_headingless_pattern_files(self):
        for options in ({"create_pattern_files": False}, {"pattern_names": []}):
            with self.subTest(options=options):
                result = self.run_writer(sample(), **options)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(".patterns.md", result.stderr)
                self.assertFalse(self.output.exists())

    def test_short_meter_and_explicit_rest_section(self):
        manifest = sample()
        manifest["meter"] = "3/8"
        manifest["tracks"][0]["patterns"][0]["notes"] = [
            {"bar": 1, "offset_quarters": .5, "duration_quarters": .5,
             "string": 1, "fret": 0},
        ]
        manifest["tracks"][1]["patterns"][0]["notes"] = [
            {"bar": 1, "offset_quarters": 0, "duration_quarters": .0625,
             "pitch": 0},
        ]
        manifest["tracks"][2]["patterns"][0]["notes"] = []
        manifest["sections"] = [
            {"name": "Notes", "bars": 1, "patterns": ["picked", "pulse"],
             "tail_policy": "truncate"},
            {"name": "Silence", "bars": 1, "patterns": [], "empty_policy": "rest"},
        ]
        result = self.run_writer(manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = inspect(self.output)
        self.assertEqual(actual["status"], "valid")
        self.assertEqual([b["length_quarters"] for b in actual["bars"]], [1.5, 1.5])
        self.assertEqual([(n["pitch"], n["start_quarters"],
                           n["duration_quarters"], n["fret"])
                          for n in actual["tracks"][1]["notes"]],
                         [(0, 0, .0625, None)])
        self.assertEqual([len(t["notes"]) for t in actual["tracks"]], [1, 1, 0])

    def test_invalid_manifest_cases_never_publish(self):
        base = sample()
        cases = {
            "unknown semantics": lambda x: x.update(tempo_changes=[120]),
            "invalid meter": lambda x: x.update(meter="7/3"),
            "non-grid start": lambda x: x["tracks"][0]["patterns"][0]["notes"][0].update(offset_quarters=.1),
            "note over bar": lambda x: x["tracks"][0]["patterns"][0]["notes"][0].update(offset_quarters=3.5),
            "bad pitch/fret": lambda x: x["tracks"][0]["patterns"][0]["notes"][0].update(pitch=46),
            "fake pitched fret": lambda x: x["tracks"][1]["patterns"][0]["notes"][0].update(fret=3),
            "missing tail policy": lambda x: x["sections"][1].pop("tail_policy"),
            "unknown pattern": lambda x: x["sections"][0]["patterns"].append("missing"),
            "duplicate pattern": lambda x: x["sections"][0]["patterns"].append("picked"),
            "overlap": lambda x: x["tracks"][0]["patterns"][0]["notes"].append(
                {"bar": 1, "offset_quarters": .5, "duration_quarters": 1,
                 "string": 4, "fret": 1}),
            "missing empty policy": lambda x: x["sections"][2].pop("empty_policy"),
            "excess bars": lambda x: x["sections"][0].update(bars=513),
            "noninteger version": lambda x: x.update(version=1.1),
            "fractional tempo": lambda x: x.update(tempo_bpm=108.5),
            "no notes": lambda x: [
                p.update(notes=[]) for t in x["tracks"] for p in t["patterns"]],
            "bad source": lambda x: x["tracks"][0].update(source=""),
            "bad confidence": lambda x: x["tracks"][0]["patterns"][0].update(confidence=5),
            "unknown confidence": lambda x: x["tracks"][0]["patterns"][0].update(confidence="unknown"),
            "missing source": lambda x: x["tracks"][1]["patterns"][0].pop("source"),
        }
        for label, corrupt in cases.items():
            with self.subTest(label=label):
                manifest = copy.deepcopy(base)
                corrupt(manifest)
                result = self.run_writer(manifest)
                self.assertNotEqual(result.returncode, 0, label)
                self.assertFalse(self.output.exists(), label)
                self.assertFalse(list(HERE.glob(f".{self.output.name}.*.stage.gp")), label)


if __name__ == "__main__":
    unittest.main()
