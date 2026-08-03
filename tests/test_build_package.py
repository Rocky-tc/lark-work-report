import hashlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "tools" / "build_package.py"


class BuildPackageTests(unittest.TestCase):
    def test_package_is_runtime_only_and_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.zip"
            second = Path(tmp) / "second.zip"
            for output in (first, second):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), "--output", str(output)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                hashlib.sha256(first.read_bytes()).hexdigest(),
                hashlib.sha256(second.read_bytes()).hexdigest(),
            )
            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
            self.assertIn("lark-work-report/SKILL.md", names)
            self.assertIn(
                "lark-work-report/references/adapter-contract.schema.json",
                names,
            )
            self.assertIn(
                "lark-work-report/references/template-profile.schema.json",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/template_profiles.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/reconcile-evidence.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/compile-evidence.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/prepare-synthesis-view.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/normalize-fetch-body.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/prepare-repair-queue.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/repair_queue.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/report_hydration.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/stage-io.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/stage_io.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/request_context.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/execution_graph.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/empty_synthesis.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/usage_metrics.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/record-node-usage.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/record-run-usage.py",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/compare-runs.py",
                names,
            )
            self.assertIn(
                "lark-work-report/references/fetch-contract.md",
                names,
            )
            self.assertIn(
                "lark-work-report/references/stage-io.md",
                names,
            )
            self.assertIn(
                "lark-work-report/scripts/value_contracts.py",
                names,
            )
            self.assertFalse(any("/tests/" in name for name in names))
            self.assertFalse(any(name.endswith("README.md") for name in names))


if __name__ == "__main__":
    unittest.main()
