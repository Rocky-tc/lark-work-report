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
        self.assertEqual(payload["title_period"], "2026-07-26")

    def test_weekly_uses_monday_to_sunday_across_month_boundary(self):
        result = run_resolver("--period", "weekly", "--reference", "2026-08-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["start"], "2026-07-27T00:00:00+08:00")
        self.assertEqual(payload["end"], "2026-08-03T00:00:00+08:00")
        self.assertEqual(payload["comparison_start"], "2026-07-20T00:00:00+08:00")
        self.assertEqual(payload["comparison_end"], "2026-07-27T00:00:00+08:00")
        self.assertEqual(payload["title_period"], "2026-07-27 至 2026-08-02")

    def test_monthly_comparison_crosses_year_boundary(self):
        result = run_resolver("--period", "monthly", "--reference", "2027-01-10")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["start"], "2027-01-01T00:00:00+08:00")
        self.assertEqual(payload["end"], "2027-02-01T00:00:00+08:00")
        self.assertEqual(payload["comparison_start"], "2026-12-01T00:00:00+08:00")
        self.assertEqual(payload["comparison_end"], "2027-01-01T00:00:00+08:00")
        self.assertEqual(payload["title_period"], "2027年1月")

    def test_custom_end_date_is_inclusive_and_comparison_has_equal_length(self):
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
        self.assertEqual(payload["start"], "2026-07-01T00:00:00+08:00")
        self.assertEqual(payload["end"], "2026-07-11T00:00:00+08:00")
        self.assertEqual(payload["comparison_start"], "2026-06-21T00:00:00+08:00")
        self.assertEqual(payload["comparison_end"], "2026-07-01T00:00:00+08:00")

    def test_custom_rejects_reversed_dates(self):
        result = run_resolver(
            "--period",
            "custom",
            "--reference",
            "2026-07-26",
            "--start",
            "2026-07-10",
            "--end",
            "2026-07-01",
        )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertIn("start must be on or before end", payload["error"])


if __name__ == "__main__":
    unittest.main()
