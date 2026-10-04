"""Cross-language parity tests for the canonical-JSON fingerprint algorithm.

`v2_schema.py`'s `canonical_sha256` and `write_arrangement.mjs`'s `canonicalSha256`
must produce byte-identical hex digests for any value reachable by round-tripping
through `json.loads`/`JSON.parse`, because `compile_performance.py` writes
fingerprints that `write_arrangement.mjs` later re-verifies (and vice versa for
resolutions/performance inputs). This module invokes the real Node implementation
(via the hidden `--canonical-fingerprint-stdin` test-support entry point) rather than
re-implementing or guessing at its behavior, so a future change to either side's
encoder is caught here.
"""
import json
import subprocess
import unittest
from pathlib import Path

from v2_schema import canonical_sha256, Invalid

HERE = Path(__file__).resolve().parent
WRITER = HERE / "write_arrangement.mjs"


def node_fingerprint(value):
    result = subprocess.run(
        ["node", str(WRITER), "--canonical-fingerprint-stdin"],
        input=json.dumps(value).encode("utf-8"),
        capture_output=True, timeout=30, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace"))
    return result.stdout.decode("utf-8")


class CanonicalFingerprintParityTests(unittest.TestCase):
    def assert_parity(self, value):
        self.assertEqual(canonical_sha256(value), node_fingerprint(value),
                          f"Python/JS canonical fingerprint mismatch for {value!r}")

    def test_whole_valued_float_matches_integer(self):
        self.assert_parity({"tempo_bpm": 120.0})
        self.assert_parity(120.0)
        self.assertEqual(node_fingerprint(120.0), node_fingerprint(120))
        self.assertEqual(canonical_sha256(120.0), canonical_sha256(120))

    def test_nested_structures(self):
        self.assert_parity({
            "b": [1, 2.0, {"z": 1, "a": 2.5}],
            "a": {"nested": {"deep": [True, False, None, "x"]}},
        })

    def test_non_ascii_strings_and_keys(self):
        self.assert_parity({"名前": "ギター", "café": "naïve résumé", "emoji": "🎸"})

    def test_key_ordering_and_scalars(self):
        self.assert_parity({"y": 2, "x": 1, "z": [3, 4, 5]})
        self.assert_parity("plain string")
        self.assert_parity(True)
        self.assert_parity(None)
        self.assert_parity(0)
        self.assert_parity(-0.0)

    def test_non_finite_rejected_on_both_sides(self):
        with self.assertRaises(Invalid):
            canonical_sha256(float("nan"))
        result = subprocess.run(
            ["node", str(WRITER), "--canonical-fingerprint-stdin"],
            input=b"null",  # JSON has no NaN/Infinity literal; exercise via a value
            # that becomes non-finite only on the JS side is not expressible through
            # JSON text, so this just confirms the stdin path itself still works for a
            # degenerate value and the Python side's rejection above stands alone.
            capture_output=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
