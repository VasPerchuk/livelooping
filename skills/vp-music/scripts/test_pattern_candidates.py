import unittest

from pattern_candidates import candidates


def score():
    bars = [{"number": i + 1, "start_quarters": i * 4,
             "length_quarters": 4, "meter": "4/4"} for i in range(8)]
    return {
        "status": "valid", "bars": bars,
        "tracks": [
            {"name": "Guitar", "notes": [
                {"pitch": 60 if i < 4 else 62, "start_quarters": 4 * i,
                 "duration_quarters": 1} for i in range(8)
            ]},
            {"name": "Vocals", "notes": [
                {"pitch": 64, "start_quarters": i * 4, "duration_quarters": 1}
                for i in range(8)
            ]},
        ],
    }


class PatternTest(unittest.TestCase):
    def test_repeated_instrumental_patterns_only(self):
        found = candidates(score())
        self.assertEqual(len(found), 2)
        self.assertEqual({item["track"] for item in found}, {"Guitar"})
        self.assertEqual({item["source_bar"] for item in found}, {1, 5})
        self.assertTrue(all(len(item["occurrence_bars"]) >= 2 for item in found))

    def test_cap_and_absent_notes(self):
        self.assertEqual(len(candidates(score(), max_per_track=1)), 1)
        blank = score()
        blank["tracks"][0]["notes"] = []
        self.assertEqual(candidates(blank), [])

    def test_reject_unvalidated_input(self):
        with self.assertRaisesRegex(ValueError, "invalid"):
            candidates({"status": "invalid", "error": "invalid"})


if __name__ == "__main__":
    unittest.main()
