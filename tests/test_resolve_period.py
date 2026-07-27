import json
import subprocess
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "resolve-period.py"


def run_resolver(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


class ResolvePeriodTests(unittest.TestCase):
    def test_daily_uses_local_day_and_previous_day_comparison(self):
        result = run_resolver("--period", "daily", "--reference", "2026-07-26")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["start"], "2026-07-26T00:00:00+08:00")
        self.assertEqual(payload["end"], "2026-07-27T00:00:00+08:00")
        self.assertEqual(payload["comparison_start"], "2026-07-25T00:00:00+08:00")
        self.assertEqual(payload["comparison_end"], "2026-07-26T00:00:00+08:00")
        self.assertEqual(payload["routed_profile"], "daily")
        self.assertFalse(payload["is_partial"])

    def test_current_week_is_truncated_to_snapshot_with_equal_comparison(self):
        result = run_resolver(
            "--period",
            "weekly",
            "--reference",
            "2026-07-26",
            "--snapshot",
            "2026-07-26T18:00:00+08:00",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["start"], "2026-07-20T00:00:00+08:00")
        self.assertEqual(payload["end"], "2026-07-26T18:00:00+08:00")
        self.assertEqual(payload["natural_end"], "2026-07-27T00:00:00+08:00")
        self.assertEqual(payload["comparison_start"], "2026-07-13T00:00:00+08:00")
        self.assertEqual(payload["comparison_end"], "2026-07-19T18:00:00+08:00")
        self.assertTrue(payload["is_partial"])

    def test_previous_week_is_deterministic(self):
        result = run_resolver(
            "--period",
            "weekly",
            "--reference",
            "2026-07-27",
            "--relative",
            "previous",
            "--snapshot",
            "2026-07-27T10:00:00+08:00",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["start"], "2026-07-20T00:00:00+08:00")
        self.assertEqual(payload["end"], "2026-07-27T00:00:00+08:00")
        self.assertFalse(payload["is_partial"])

    def test_previous_month_crosses_year_boundary(self):
        result = run_resolver(
            "--period",
            "monthly",
            "--reference",
            "2027-01-10",
            "--relative",
            "previous",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["start"], "2026-12-01T00:00:00+08:00")
        self.assertEqual(payload["end"], "2027-01-01T00:00:00+08:00")
        self.assertEqual(payload["title_period"], "2026年12月")

    def test_month_comparison_never_overlaps_report_window(self):
        result = run_resolver(
            "--period",
            "monthly",
            "--reference",
            "2026-03-31",
            "--snapshot",
            "2026-03-31T18:00:00+08:00",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["comparison_start"], "2026-02-01T00:00:00+08:00")
        self.assertEqual(payload["comparison_end"], "2026-03-01T00:00:00+08:00")

    def test_custom_routes_by_duration(self):
        result = run_resolver(
            "--period",
            "custom",
            "--reference",
            "2026-07-26",
            "--start",
            "2026-07-01",
            "--end",
            "2026-07-10",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["routed_profile"], "weekly")
        self.assertEqual(payload["comparison_start"], "2026-06-21T00:00:00+08:00")
        self.assertEqual(payload["comparison_end"], "2026-07-01T00:00:00+08:00")

    def test_naive_snapshot_is_rejected(self):
        result = run_resolver(
            "--period",
            "daily",
            "--reference",
            "2026-07-26",
            "--snapshot",
            "2026-07-26T10:00:00",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timezone offset", json.loads(result.stderr)["error"])

    def test_custom_rejects_previous_relative_mode(self):
        result = run_resolver(
            "--period",
            "custom",
            "--reference",
            "2026-07-26",
            "--relative",
            "previous",
            "--start",
            "2026-07-01",
            "--end",
            "2026-07-10",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not support", json.loads(result.stderr)["error"])


if __name__ == "__main__":
    unittest.main()
