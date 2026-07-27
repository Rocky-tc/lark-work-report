import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
MANAGER = ROOT / "scripts" / "manage-run.py"
FINALIZER = ROOT / "scripts" / "finalize-run.py"


def run(command, *args):
    return subprocess.run(
        [sys.executable, str(command), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def report_model():
    return {
        "schema_version": 1,
        "profile": "weekly",
        "title": "个人工作周报｜2026-07-20 至 2026-07-26",
        "summary": [
            {
                "result": "完成报告优化",
                "source_ref": "https://example.com/work/1",
            }
        ],
        "workstreams": [],
        "risks": [],
        "next_actions": [],
        "uncertain": [],
        "coverage": {
            "start": "2026-07-20T00:00:00+08:00",
            "end": "2026-07-27T00:00:00+08:00",
            "snapshot": "2026-07-27T00:00:00+08:00",
            "domains": ["文档"],
            "access_gaps": [],
            "work_count": 1,
            "uncertain_count": 0,
        },
    }


class FinalizeRunTests(unittest.TestCase):
    def setUp(self):
        created = run(MANAGER, "create", "--profile", "weekly")
        self.assertEqual(created.returncode, 0, created.stderr)
        self.run_dir = Path(json.loads(created.stdout)["run_dir"])
        (self.run_dir / "run-plan.json").write_text(
            json.dumps({"period": {"routed_profile": "weekly"}}),
            encoding="utf-8",
        )
        (self.run_dir / "report-model.json").write_text(
            json.dumps(report_model(), ensure_ascii=False),
            encoding="utf-8",
        )
        (self.run_dir / "ledger.json").write_text(
            json.dumps(
                {
                    "work": [{"source_ref": "https://example.com/work/1"}],
                    "uncertain": [],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        if self.run_dir.exists():
            run(MANAGER, "cleanup", "--run-dir", str(self.run_dir))

    def test_render_and_validation_are_one_atomic_step(self):
        result = run(FINALIZER, "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        report = Path(payload["report_file"])
        self.assertTrue(report.is_file())
        self.assertIn("## 本周摘要", report.read_text(encoding="utf-8"))
        self.assertNotIn("# 个人工作周报", result.stdout)

    def test_invalid_evidence_does_not_write_report(self):
        (self.run_dir / "ledger.json").write_text(
            json.dumps({"work": [], "uncertain": []}),
            encoding="utf-8",
        )
        result = run(FINALIZER, "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse((self.run_dir / "report.md").exists())
        self.assertIn("不在 work 账本", result.stdout)

    def test_output_outside_run_is_rejected(self):
        result = run(
            FINALIZER,
            "--run-dir",
            str(self.run_dir),
            "--output",
            str(self.run_dir.parent / "escaped-report.md"),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("must remain inside", result.stderr)


if __name__ == "__main__":
    unittest.main()
