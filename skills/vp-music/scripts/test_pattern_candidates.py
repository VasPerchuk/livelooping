import copy
import unittest

from pattern_candidates import analyze, candidates


def _bars(count, meter="4/4", start_number=1):
    return [{"number": start_number + i, "start_quarters": i * 4,
             "length_quarters": 4, "meter": meter} for i in range(count)]


def score():
    """Original exact-only fixture: Guitar repeats in two 4-bar halves, Vocals hold one note."""
    bars = _bars(8)
    return {
        "status": "valid", "bars": bars,
        "tracks": [
            {"name": "Guitar", "notes": [
                {"pitch": 60 if i < 4 else 62, "start_quarters": 4 * i, "duration_quarters": 1}
                for i in range(8)
            ]},
            {"name": "Vocals", "notes": [
                {"pitch": 64, "start_quarters": i * 4, "duration_quarters": 1} for i in range(8)
            ]},
        ],
    }


class BackwardCompatibleExactTest(unittest.TestCase):
    """Requirement 1/CLI compatibility: old field names and exact behavior survive."""

    def test_repeated_instrumental_pattern_fields(self):
        found = [c for c in candidates(score()) if c["track"] == "Guitar"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["source_bar"], 1)
        self.assertEqual(found[0]["span_bars"], 4)
        for item in found:
            self.assertEqual(item["match_tier"], "normalized")
            self.assertEqual(item["disposition"], "UNRESOLVED")
            self.assertGreaterEqual(len(item["occurrence_bars"]), 2)
            self.assertIn("length_bars", item)
            self.assertIn("occurrences", item)
            self.assertIn("id", item)

    def test_cap_and_absent_notes(self):
        capped = [c for c in candidates(score(), max_per_track=1) if c["track"] == "Guitar"]
        self.assertEqual(len(capped), 1)
        blank = score()
        blank["tracks"][0]["notes"] = []
        self.assertEqual([c for c in candidates(blank) if c["track"] == "Guitar"], [])

    def test_reject_unvalidated_input(self):
        with self.assertRaisesRegex(ValueError, "invalid"):
            candidates({"status": "invalid", "error": "invalid"})


class PerformedOccurrenceFrequencyTest(unittest.TestCase):
    """Requirement 1: count repeated frequency in performed occurrences, cite written bars."""

    def _repeated_score(self):
        bars = _bars(4)
        notes = [
            {"pitch": 60, "start_quarters": 0, "duration_quarters": 1},
            {"pitch": 60, "start_quarters": 4, "duration_quarters": 1},
            {"pitch": 62, "start_quarters": 8, "duration_quarters": 1},
            {"pitch": 62, "start_quarters": 12, "duration_quarters": 1},
        ]
        return {
            "status": "valid", "bars": bars,
            "tracks": [{"name": "Rhythm Guitar", "notes": notes}],
            "performed_order": {"status": "ok",
                                "bars": [1, 2, 1, 2, 3, 4, 3, 4, 3, 4]},
        }

    def test_frequency_uses_performed_order_not_written_count(self):
        found = candidates(self._repeated_score())
        by_bar = {c["source_bar"]: c for c in found}
        self.assertIn(1, by_bar)
        self.assertIn(3, by_bar)
        # Frequency counts complete performed phrase occurrences, rather than
        # adding the pass count of every bar in a phrase.
        self.assertEqual(by_bar[1]["frequency"], 4)
        self.assertEqual(by_bar[3]["frequency"], 3)
        for occurrence in by_bar[1]["occurrences"]:
            self.assertIn("written bars", occurrence["coordinate"])
            self.assertIn("occurrence", occurrence["coordinate"])

    def test_fallback_without_performed_order_is_linear_single_pass(self):
        plain = self._repeated_score()
        del plain["performed_order"]
        found = candidates(plain)
        by_bar = {c["source_bar"]: c for c in found}
        self.assertEqual(by_bar[1]["frequency"], 2)


class RolePriorTest(unittest.TestCase):
    """Requirement 2: roles are priors, never a blanket exclusion."""

    def _lead_score(self, repeated):
        bars = _bars(8)
        if repeated:
            notes = [{"pitch": 67, "start_quarters": i * 4, "duration_quarters": 1} for i in range(8)]
        else:
            # Irregular, non-arithmetic contour: no constant interval, so no
            # window at any span/transform repeats under exact, normalized or
            # near matching.
            irregular_pitches = [60, 67, 59, 72, 63, 68, 55, 61]
            notes = [{"pitch": irregular_pitches[i], "start_quarters": i * 4, "duration_quarters": 1}
                     for i in range(8)]
        return {"status": "valid", "bars": bars,
                "tracks": [{"name": "Lead Vocals", "notes": notes}]}

    def test_non_repetitive_lead_not_over_patternized(self):
        found = candidates(self._lead_score(repeated=False))
        self.assertEqual(found, [])

    def test_repetitive_lead_eligible_for_loop(self):
        found = candidates(self._lead_score(repeated=True))
        self.assertTrue(found)
        self.assertEqual(found[0]["role"], "ostinato_lead")
        self.assertEqual(found[0]["disposition"], "LOOP")

    def test_percussion_and_bass_have_positive_prior(self):
        bars = _bars(4)
        data = {"status": "valid", "bars": bars,
                "tracks": [{"name": "Drums", "notes": [
                    {"pitch": 36, "start_quarters": i * 4, "duration_quarters": 1} for i in range(4)]}]}
        roles = analyze(data)["roles"]
        self.assertEqual(roles["track-0"]["role"], "percussion")
        self.assertGreater(roles["track-0"]["role_prior"], 0)

    def test_unknown_role_is_neutral(self):
        bars = _bars(4)
        data = {"status": "valid", "bars": bars,
                "tracks": [{"name": "Track 3", "notes": [
                    {"pitch": 70, "start_quarters": i * 4, "duration_quarters": 1} for i in range(4)]}]}
        roles = analyze(data)["roles"]
        self.assertEqual(roles["track-0"]["role"], "unknown")
        self.assertEqual(roles["track-0"]["role_prior"], 0.0)


class OddMeterTest(unittest.TestCase):
    def test_repeated_odd_meter_accepted(self):
        bars = _bars(4, meter="5/4")
        notes = [{"pitch": 48, "start_quarters": i * 4, "duration_quarters": 1} for i in range(4)]
        data = {"status": "valid", "bars": bars, "tracks": [{"name": "Bass", "notes": notes}]}
        found = candidates(data)
        self.assertTrue(found)
        self.assertEqual(found[0]["meter"], "5/4")
        self.assertEqual(found[0]["disposition"], "LOOP")


class SilenceTest(unittest.TestCase):
    def test_silence_is_rest_not_a_candidate(self):
        bars = _bars(4)
        # Bars 1-2 have distinct (non-repeating) notes, bars 3-4 are silent.
        notes = [{"pitch": 60, "start_quarters": 0, "duration_quarters": 1},
                 {"pitch": 67, "start_quarters": 4, "duration_quarters": 1}]
        data = {"status": "valid", "bars": bars, "tracks": [{"name": "Guitar", "notes": notes}]}
        result = analyze(data)
        self.assertEqual(result["candidates"], [])
        rest_spans = [span for span in result["remaining_spans"]["track-0"]["spans"]
                      if span["disposition"] == "REST"]
        self.assertTrue(rest_spans)
        self.assertEqual(rest_spans[0]["written_bars"], [3, 4])


class NearLoopTest(unittest.TestCase):
    def test_three_of_four_near_loop_with_divergent_bar(self):
        bars = _bars(8)
        # Two 4-bar windows: bars 1-4 and bars 5-8. Bars 1,2,4 match bars 5,6,8
        # exactly; bar 3 diverges from bar 7 (different pitch).
        def win(pitch3):
            return [
                {"pitch": 60, "start_quarters": 0, "duration_quarters": 1},
                {"pitch": 62, "start_quarters": 4, "duration_quarters": 1},
                {"pitch": pitch3, "start_quarters": 8, "duration_quarters": 1},
                {"pitch": 65, "start_quarters": 12, "duration_quarters": 1},
            ]
        notes = win(64) + [dict(n, start_quarters=n["start_quarters"] + 16) for n in win(67)]
        data = {"status": "valid", "bars": bars, "tracks": [{"name": "Guitar", "notes": notes}]}
        found = [c for c in candidates(data) if c["match_tier"] == "near"]
        self.assertEqual(len(found), 1)
        near = found[0]
        self.assertEqual(near["disposition"], "UNRESOLVED")
        self.assertIsNotNone(near["decision"])
        self.assertEqual(near["near_match"]["matching_bars"], 3)
        self.assertEqual(near["near_match"]["total_bars"], 4)
        self.assertEqual(near["near_match"]["divergent_bar_offsets"], [2])
        # First decision has <=3 options (no four-option single question), with a
        # conditional live/omit follow-up represented separately.
        first, followup = near["decision"]
        self.assertLessEqual(len(first["options"]), 3)
        self.assertEqual(followup.get("depends_on"), first["id"])
        self.assertIn("omit", followup["options"])


class NormalizedCandidateTest(unittest.TestCase):
    def test_transposed_normalized_candidate(self):
        bars = _bars(8)

        def win(offset):
            return [
                {"pitch": 60 + offset, "start_quarters": 0, "duration_quarters": 1},
                {"pitch": 62 + offset, "start_quarters": 4, "duration_quarters": 1},
                {"pitch": 64 + offset, "start_quarters": 8, "duration_quarters": 1},
                {"pitch": 65 + offset, "start_quarters": 12, "duration_quarters": 1},
            ]
        notes = win(0) + [dict(n, start_quarters=n["start_quarters"] + 16) for n in win(5)]
        data = {"status": "valid", "bars": bars, "tracks": [{"name": "Guitar", "notes": notes}]}
        found = [c for c in candidates(data) if c["match_tier"] == "normalized"]
        self.assertEqual(len(found), 1)
        normalized = found[0]
        self.assertEqual(normalized["disposition"], "UNRESOLVED")
        self.assertIsNotNone(normalized["decision"])
        self.assertEqual(normalized["normalization"]["type"], "pitch_translation")
        self.assertEqual(
            normalized["normalization"]["semitone_offsets_relative_to_first_occurrence"], [0, 5])

    def test_no_source_mutation(self):
        bars = _bars(8)

        def win(offset):
            return [
                {"pitch": 60 + offset, "start_quarters": 0, "duration_quarters": 1},
                {"pitch": 62 + offset, "start_quarters": 4, "duration_quarters": 1},
            ]
        notes = win(0) + [dict(n, start_quarters=n["start_quarters"] + 16) for n in win(5)]
        data = {"status": "valid", "bars": bars, "tracks": [{"name": "Guitar", "notes": notes}]}
        before = copy.deepcopy(data)
        candidates(data)
        self.assertEqual(data, before)


class SoftMaxAndOverrideTest(unittest.TestCase):
    def _long_repeat_score(self, span):
        total_bars = span * 2
        bars = _bars(total_bars)
        # Strictly ascending, all-distinct pitches: no shorter internal
        # periodicity exists, so only the full-span repeat can match.
        pattern = [{"pitch": 48 + i, "start_quarters": i * 4, "duration_quarters": 1}
                  for i in range(span)]
        notes = pattern + [dict(n, start_quarters=n["start_quarters"] + span * 4) for n in pattern]
        return {"status": "valid", "bars": bars, "tracks": [{"name": "Guitar", "notes": notes}]}

    def test_default_cap_three_per_instrument(self):
        bars = _bars(16)
        notes = []
        for group in range(4):
            notes.extend({"pitch": 60 + group, "start_quarters": (group * 4 + i) * 4,
                         "duration_quarters": 1} for i in range(4))
        data = {"status": "valid", "bars": bars, "tracks": [{"name": "Guitar", "notes": notes}]}
        found = candidates(data, min_occurrences=2)
        self.assertLessEqual(len(found), 3)

    def test_coherent_phrase_outranks_repeated_one_bar_cell(self):
        bars = _bars(16)
        notes = [{"pitch": 60, "start_quarters": i * 4, "duration_quarters": 1}
                 for i in range(16)]
        data = {"status": "valid", "bars": bars,
                "tracks": [{"name": "Rhythm", "notes": notes}]}
        found = candidates(data, max_per_track=1)
        self.assertEqual(found[0]["span_bars"], 4)

    def test_shared_silence_does_not_create_near_loop(self):
        bars = _bars(16)
        notes = [
            {"pitch": 60, "start_quarters": 7 * 4, "duration_quarters": 1},
            {"pitch": 67, "start_quarters": 15 * 4, "duration_quarters": 1},
        ]
        data = {"status": "valid", "bars": bars,
                "tracks": [{"name": "Background Guitar", "notes": notes}]}
        near = [item for item in candidates(data) if item["match_tier"] == "near"]
        self.assertEqual(near, [])

    def test_sparse_but_identical_phrase_remains_exact_candidate(self):
        bars = _bars(16)
        notes = [
            {"pitch": 60, "start_quarters": 7 * 4, "duration_quarters": 1},
            {"pitch": 60, "start_quarters": 15 * 4, "duration_quarters": 1},
        ]
        data = {"status": "valid", "bars": bars,
                "tracks": [{"name": "Sparse Ostinato", "notes": notes}]}
        exact = [item for item in candidates(data)
                 if item["match_tier"] == "exact" and item["span_bars"] == 8]
        self.assertEqual(len(exact), 1)

    def test_max_per_track_override(self):
        data = score()
        self.assertEqual(len([c for c in candidates(data, max_per_track=1) if c["track"] == "Guitar"]), 1)

    def test_redundant_long_span_does_not_consume_pattern_budget(self):
        data = self._long_repeat_score(span=12)
        found = [c for c in candidates(data) if c["span_bars"] == 12]
        self.assertEqual(found, [])

    def test_max_bars_override_does_not_disable_redundancy_suppression(self):
        data = self._long_repeat_score(span=12)
        found = [c for c in candidates(data, max_bars_soft=16) if c["span_bars"] == 12]
        self.assertEqual(found, [])

    def test_steady_passage_selects_one_phrase_not_long_overlaps(self):
        bars = _bars(32)
        notes = [{"pitch": 60, "start_quarters": i * 4, "duration_quarters": 1}
                 for i in range(32)]
        data = {"status": "valid", "bars": bars,
                "tracks": [{"name": "Rhythm", "notes": notes}]}
        found = candidates(data)
        self.assertEqual([(item["span_bars"], item["match_tier"]) for item in found],
                         [(8, "exact")])


class StableIdentityAndSoundingCoverageTest(unittest.TestCase):
    def test_duplicate_display_names_remain_distinct(self):
        bars = _bars(8)
        notes = [{"pitch": 60, "start_quarters": i * 4, "duration_quarters": 1}
                 for i in range(8)]
        data = {"status": "valid", "bars": bars,
                "tracks": [{"id": "left", "name": "Guitar", "notes": notes},
                           {"id": "right", "name": "Guitar", "notes": notes}]}
        result = analyze(data)
        self.assertEqual(set(result["roles"]), {"left", "right"})
        self.assertEqual({item["track_id"] for item in result["candidates"]},
                         {"left", "right"})
        self.assertEqual(len({item["id"] for item in result["candidates"]}),
                         len(result["candidates"]))

    def test_written_phrase_repeated_by_score_order_is_a_candidate(self):
        bars = _bars(4)
        notes = [{"pitch": 60 + i, "start_quarters": i * 4, "duration_quarters": 1}
                 for i in range(4)]
        data = {"status": "valid", "bars": bars,
                "tracks": [{"name": "Guitar", "notes": notes}],
                "performed_order": {"status": "ok", "bars": [1, 2, 3, 4, 1, 2, 3, 4]}}
        found = candidates(data)
        exact_four = [item for item in found
                      if item["match_tier"] == "exact" and item["span_bars"] == 4]
        self.assertEqual(len(exact_four), 1)
        self.assertEqual(exact_four[0]["frequency"], 2)

    def test_note_sustaining_into_bar_is_not_rest(self):
        bars = _bars(2)
        data = {"status": "valid", "bars": bars,
                "tracks": [{"name": "Pad", "notes": [
                    {"pitch": 60, "start_quarters": 0, "duration_quarters": 8},
                ]}]}
        result = analyze(data)
        spans = result["remaining_spans"]["track-0"]["spans"]
        self.assertTrue(all(item["disposition"] != "REST" for item in spans))


class DeterministicIdTest(unittest.TestCase):
    def test_ids_are_stable_across_runs(self):
        data = score()
        first = [c["id"] for c in candidates(data)]
        second = [c["id"] for c in candidates(data)]
        self.assertEqual(first, second)
        self.assertEqual(len(first), len(set(first)))


class DecisionStructureTest(unittest.TestCase):
    def test_unresolved_candidates_always_have_a_decision(self):
        bars = _bars(8)

        def win(offset):
            return [{"pitch": 60 + offset, "start_quarters": 0, "duration_quarters": 1},
                   {"pitch": 62 + offset, "start_quarters": 4, "duration_quarters": 1}]
        notes = win(0) + [dict(n, start_quarters=n["start_quarters"] + 16) for n in win(3)]
        data = {"status": "valid", "bars": bars, "tracks": [{"name": "Guitar", "notes": notes}]}
        result = analyze(data)
        for candidate in result["candidates"]:
            if candidate["disposition"] == "UNRESOLVED":
                self.assertIsNotNone(candidate["decision"])
                self.assertIn(candidate["decision"][0], result["decisions"])


if __name__ == "__main__":
    unittest.main()
