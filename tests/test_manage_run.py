import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "manage-run.py"


def run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


class ManageRunTests(unittest.TestCase):
    def test_create_record_status_and_cleanup(self):
        created = run("create", "--profile", "daily")
        self.assertEqual(created.returncode, 0, created.stderr)
        run_dir = json.loads(created.stdout)["run_dir"]
        try:
            recorded = run(
                "record-call",
                "--run-dir",
                run_dir,
                "--domain",
                "docs",
                "--operation",
                "candidate.list",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            self.assertEqual(json.loads(recorded.stdout)["call_count"], 1)
            status = run("status", "--run-dir", run_dir)
            self.assertEqual(json.loads(status.stdout)["remaining"], 17)
        finally:
            cleaned = run("cleanup", "--run-dir", run_dir)
        self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
        self.assertFalse(Path(run_dir).exists())

    def test_hard_limit_refuses_next_external_call(self):
        created = run("create", "--profile", "daily")
        run_dir = json.loads(created.stdout)["run_dir"]
        try:
            for index in range(18):
                result = run(
                    "record-call",
                    "--run-dir",
                    run_dir,
                    "--domain",
                    "test",
                    "--operation",
                    f"call-{index}",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            rejected = run(
                "record-call",
                "--run-dir",
                run_dir,
                "--domain",
                "test",
                "--operation",
                "call-19",
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("hard limit would be exceeded", json.loads(rejected.stderr)["error"])
        finally:
            run("cleanup", "--run-dir", run_dir)

    def test_template_fetch_adds_one_call_without_reducing_collection_budget(self):
        created = run(
            "create",
            "--profile",
            "daily",
            "--template-call-count",
            "1",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        payload = json.loads(created.stdout)
        run_dir = payload["run_dir"]
        try:
            self.assertEqual(payload["base_hard_limit"], 18)
            self.assertEqual(payload["template_call_count"], 1)
            self.assertEqual(payload["hard_limit"], 19)
        finally:
            run("cleanup", "--run-dir", run_dir)

    def test_record_batch_reserves_parallel_wave_atomically(self):
        created = run("create", "--profile", "daily")
        run_dir = json.loads(created.stdout)["run_dir"]
        try:
            recorded = run(
                "record-batch",
                "--run-dir",
                run_dir,
                "--domain",
                "metadata",
                "--operation",
                "candidate.list",
                "--count",
                "4",
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            payload = json.loads(recorded.stdout)
            self.assertEqual(payload["recorded"], 4)
            self.assertEqual(payload["call_count"], 4)
            status = json.loads(
                run("status", "--run-dir", run_dir).stdout
            )
            self.assertEqual(len(status["calls"]), 4)
        finally:
            run("cleanup", "--run-dir", run_dir)

    def test_cleanup_refuses_arbitrary_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run("cleanup", "--run-dir", tmp)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing path", json.loads(result.stderr)["error"])
            self.assertTrue(Path(tmp).exists())

    def test_call_log_rejects_free_text(self):
        created = run("create", "--profile", "daily")
        run_dir = json.loads(created.stdout)["run_dir"]
        try:
            result = run(
                "record-call",
                "--run-dir",
                run_dir,
                "--domain",
                "私人群名称",
                "--operation",
                "candidate.list",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("machine label", json.loads(result.stderr)["error"])
        finally:
            run("cleanup", "--run-dir", run_dir)

    def test_corrupt_state_returns_a_controlled_error(self):
        created = run("create", "--profile", "daily")
        run_dir = Path(json.loads(created.stdout)["run_dir"])
        state_path = run_dir / ".run-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["call_count"] = 2
        state_path.write_text(json.dumps(state), encoding="utf-8")
        try:
            result = run("status", "--run-dir", str(run_dir))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid call ledger", json.loads(result.stderr)["error"])
            self.assertNotIn("Traceback", result.stderr)
        finally:
            state["call_count"] = 0
            state_path.write_text(json.dumps(state), encoding="utf-8")
            run("cleanup", "--run-dir", str(run_dir))


if __name__ == "__main__":
    unittest.main()
