"""Load-test arithmetic: percentiles and the per-level summary."""

import unittest

from scripts.load_test import percentile, summarize


class PercentileTest(unittest.TestCase):
    def test_nearest_rank_on_one_to_one_hundred(self) -> None:
        values = [float(value) for value in range(1, 101)]

        self.assertEqual([percentile(values, percent) for percent in (50, 90, 95, 99, 100)], [50.0, 90.0, 95.0, 99.0, 100.0])

    def test_small_samples_never_interpolate_or_exceed_the_maximum(self) -> None:
        self.assertEqual([percentile([10.0, 20.0, 30.0], percent) for percent in (1, 50, 99)], [10.0, 20.0, 30.0])
        self.assertEqual(percentile([7.0], 99), 7.0)

    def test_no_samples_is_an_error_not_a_zero(self) -> None:
        with self.assertRaises(ValueError):
            percentile([], 50)


class SummaryTest(unittest.TestCase):
    def test_counts_failures_and_reports_throughput_of_successes_only(self) -> None:
        summary = summarize([0.010, 0.020, 0.030, 0.040], [200, 200, 429, 0], elapsed_s=2.0, concurrency=2)

        self.assertEqual((summary["requests"], summary["successful"], summary["failed"]), (4, 2, 2))
        self.assertEqual(summary["status_counts"], {"0": 1, "200": 2, "429": 1})
        self.assertEqual(summary["throughput_rps"], 1.0)

    def test_latency_is_reported_in_milliseconds(self) -> None:
        latency = summarize([0.010, 0.020, 0.030, 0.040], [200] * 4, elapsed_s=1.0, concurrency=1)["latency_ms"]

        self.assertEqual((latency["min"], latency["p50"], latency["p99"], latency["max"], latency["mean"]), (10.0, 20.0, 40.0, 40.0, 25.0))


if __name__ == "__main__":
    unittest.main()
