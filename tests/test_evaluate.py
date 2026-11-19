"""Pure evaluation-metric tests with no user or provider data."""
import unittest

from scripts.evaluate import first_matching_rank, overlaps, summarize


def clip(start, end, source="s1"):
    return {"source_id": source, "segment_start_id": start, "segment_end_id": end}


class EvaluationTests(unittest.TestCase):
    def test_overlap_and_rank(self):
        expected = [clip(4, 7)]
        self.assertFalse(overlaps(clip(1, 3), expected[0]))
        self.assertTrue(overlaps(clip(6, 9), expected[0]))
        self.assertEqual(first_matching_rank([clip(1, 3), clip(6, 9)], expected), 2)
        self.assertIsNone(first_matching_rank([clip(1, 3)], expected))

    def test_metrics(self):
        rows = [
            {
                "ground_truth": [clip(4, 7)],
                "clips": [clip(2, 8)],
                "roundtrip_ok": [True],
                "latency_ms": 15.0,
            },
            {
                "ground_truth": [],
                "clips": [],
                "roundtrip_ok": [],
                "latency_ms": 35.0,
            },
        ]
        report = summarize(rows)
        self.assertEqual(report["top1"], 1)
        self.assertEqual(report["top3"], 1)
        self.assertEqual(report["negative_rejected"], 1)
        self.assertEqual(report["roundtrip_checked"], 1)
        self.assertEqual(report["roundtrip_passed"], 1)
        self.assertEqual(report["median_ms"], 25.0)


if __name__ == "__main__":
    unittest.main()
