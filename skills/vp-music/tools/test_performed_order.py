import unittest

from performed_order import expand_performed_order


def bar(number, repeat_open=False, repeat_close_total_passes=None,
        alternate_endings=None, directions=None):
    return {"number": number, "repeat_open": repeat_open,
            "repeat_close_total_passes": repeat_close_total_passes,
            "alternate_endings": alternate_endings or [], "directions": directions or []}


class PerformedOrderTests(unittest.TestCase):
    def test_linear_order(self):
        bars = [bar(1), bar(2), bar(3)]
        result = expand_performed_order(bars)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["bars"], [1, 2, 3])
        self.assertEqual(result["diagnostics"], [])

    def test_simple_repeat_normalized_total_passes(self):
        # bar 2 closes a 2-total-pass repeat opened at bar 1: 1,2,1,2,3
        bars = [bar(1, repeat_open=True), bar(2, repeat_close_total_passes=2), bar(3)]
        result = expand_performed_order(bars)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["bars"], [1, 2, 1, 2, 3])

    def test_nested_repeats(self):
        # outer 1..4 x2, inner 2..3 x2 each outer pass:
        # pass A: 1 (2 2 3 3) 4   pass B: 1 (2 2 3 3) 4
        bars = [
            bar(1, repeat_open=True),
            bar(2, repeat_open=True),
            bar(3, repeat_close_total_passes=2),
            bar(4, repeat_close_total_passes=2),
        ]
        result = expand_performed_order(bars)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["bars"], [1, 2, 3, 2, 3, 4, 1, 2, 3, 2, 3, 4])

    def test_adjacent_repeats(self):
        # first group 1..2 x2, second group (implicit open) 3..4 x2
        bars = [
            bar(1, repeat_open=True), bar(2, repeat_close_total_passes=2),
            bar(3, repeat_open=True), bar(4, repeat_close_total_passes=2),
        ]
        result = expand_performed_order(bars)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["bars"], [1, 2, 1, 2, 3, 4, 3, 4])

    def test_alternate_endings_with_multi_bar_propagation(self):
        # bar2 = 1st ending (2 bars: 2,3 via propagation), bar4 = 2nd ending.
        bars = [
            bar(1, repeat_open=True),
            bar(2, alternate_endings=[1]),
            bar(3, alternate_endings=None),  # inherits mask from bar 2 (pass-1 only)
            bar(4, alternate_endings=[2], repeat_close_total_passes=2),
            bar(5),
        ]
        result = expand_performed_order(bars)
        self.assertEqual(result["status"], "ok")
        # pass 1: 1,2,3 (both ending bars belong to pass 1 via propagation,
        # bar 4 does not since it is explicitly pass-2 only)
        # pass 2: 1,4 then bar 5
        self.assertEqual(result["bars"], [1, 2, 3, 1, 4, 5])

    def test_anacrusis_bar_passes_through_unaffected(self):
        bars = [bar(1), bar(2), bar(3)]
        result = expand_performed_order(bars)
        self.assertEqual(result["bars"], [1, 2, 3])

    def test_unclosed_repeat_is_explicit_diagnostic_not_a_guess(self):
        bars = [bar(1, repeat_open=True), bar(2)]
        result = expand_performed_order(bars)
        self.assertEqual(result["status"], "unsupported")
        self.assertIsNone(result["bars"])
        self.assertTrue(any("unclosed repeat_open" in d for d in result["diagnostics"]))

    def test_expansion_bound_is_enforced_not_infinite(self):
        bars = [bar(1, repeat_open=True), bar(2, repeat_close_total_passes=50_000)]
        result = expand_performed_order(bars, max_steps=100, max_output=100)
        self.assertEqual(result["status"], "unsupported")
        self.assertIsNone(result["bars"])
        self.assertTrue(result["bounded"])
        self.assertTrue(any("exceeded the bounded step/output limit" in d
                            for d in result["diagnostics"]))

    def test_bare_da_capo(self):
        bars = [bar(1), bar(2, directions=["JumpDaCapo"]), bar(3)]
        result = expand_performed_order(bars)
        # A bare D.C. (no Fine/Coda target) jumps back to the start exactly
        # once, then plays straight through to the written end without
        # re-triggering (repeats are not re-honored post-jump) -- this
        # matches the reference engine, not an infinite loop.
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["bars"], [1, 2, 1, 2, 3])

    def test_da_capo_al_fine(self):
        bars = [bar(1, directions=["TargetFine"]), bar(2),
                bar(3, directions=["JumpDaCapoAlFine"])]
        result = expand_performed_order(bars)
        self.assertEqual(result["status"], "ok")
        # 1,2,3 then jump to start in "post-DC al Fine" state, stop at Fine (bar 1).
        self.assertEqual(result["bars"], [1, 2, 3, 1])

    def test_dal_segno_al_fine(self):
        bars = [bar(1), bar(2, directions=["TargetSegno", "TargetFine"]), bar(3),
                bar(4, directions=["JumpDalSegnoAlFine"])]
        result = expand_performed_order(bars)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["bars"], [1, 2, 3, 4, 2])

    def test_da_capo_al_coda(self):
        bars = [
            bar(1), bar(2, directions=["JumpDaCapoAlCoda"]),
            bar(3, directions=["TargetCoda"]), bar(4),
        ]
        result = expand_performed_order(bars)
        self.assertEqual(result["status"], "ok")
        # First pass 1,2 jumps to start; second pass stops at bar 3 (Coda)
        # and jumps forward to the Coda target bar, then continues to bar 4.
        self.assertEqual(result["bars"], [1, 2, 1, 2, 3, 4])

    def test_missing_jump_target_is_nonfatal_diagnostic(self):
        bars = [bar(1), bar(2, directions=["JumpDalSegnoAlFine"])]
        result = expand_performed_order(bars)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["bars"], [1, 2])
        self.assertTrue(any("no reachable" in d for d in result["diagnostics"]))


if __name__ == "__main__":
    unittest.main()
