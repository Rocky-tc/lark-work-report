import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "prepare-run.py"
MANAGER = ROOT / "scripts" / "manage-run.py"


def adapter_manifest(*, parallel=False, batch=False):
    listed = {
        "available": True,
        "metadata_only": True,
        "domains": ["docs", "im", "tasks"],
    }
    if parallel:
        listed.update({"parallel_safe": True, "max_parallelism": 2})
    return {
        "adapter_id": "test.adapter",
        "capabilities": {
            "identity.current": {"available": True},
            "candidate.list": listed,
            "candidate.list_many": {
                "available": batch,
                **({"max_domains": 2} if batch else {}),
            },
            "candidate.fetch": {"available": True},
            "candidate.fetch_many": {
                "available": True,
                "max_batch_size": 10,
            },
            "report.create": {"available": False},
            "report.fetch": {"available": False},
        },
    }


def offline_manifest():
    return {
        "adapter_id": "offline.export",
        "capabilities": {
            "identity.current": {"available": False},
            "candidate.list": {"available": False},
            "candidate.fetch": {"available": False},
            "report.create": {"available": False},
            "report.fetch": {"available": False},
        },
    }


def run_prepare(manifest, *extra):
    tmp = tempfile.TemporaryDirectory()
    adapters = Path(tmp.name) / "adapters.json"
    adapters.write_text(
        json.dumps({"adapters": [manifest]}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--adapters-file",
            str(adapters),
            "--period",
            "weekly",
            "--reference",
            "2026-07-27",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return tmp, result


def cleanup(run_dir):
    subprocess.run(
        [sys.executable, str(MANAGER), "cleanup", "--run-dir", run_dir],
        capture_output=True,
        text=True,
        check=False,
    )


class PrepareRunTests(unittest.TestCase):
    def test_batch_listing_builds_domain_batches(self):
        tmp, result = run_prepare(adapter_manifest(batch=True))
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            run_dir = summary["run_dir"]
            plan = json.loads(Path(summary["plan_file"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["identity_call_count"], 1)
            self.assertEqual(
                plan["identity_call"],
                {
                    "adapter_id": "test.adapter",
                    "operation": "identity.current",
                },
            )
            self.assertEqual(summary["metadata_call_count"], 2)
            self.assertEqual(summary["metadata_wave_count"], 2)
            self.assertEqual(
                [call["domains"] for wave in plan["metadata_waves"] for call in wave["calls"]],
                [["docs", "im"], ["tasks"]],
            )
            self.assertTrue(
                all(
                    call["operation"] == "candidate.list_many"
                    for wave in plan["metadata_waves"]
                    for call in wave["calls"]
                )
            )
        finally:
            if "run_dir" in locals():
                cleanup(run_dir)
            tmp.cleanup()

    def test_parallel_listing_builds_bounded_parallel_waves(self):
        tmp, result = run_prepare(adapter_manifest(parallel=True))
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            run_dir = summary["run_dir"]
            plan = json.loads(Path(summary["plan_file"]).read_text(encoding="utf-8"))
            self.assertEqual([len(wave["calls"]) for wave in plan["metadata_waves"]], [2, 1])
            self.assertTrue(plan["metadata_waves"][0]["parallel"])
            self.assertFalse(plan["metadata_waves"][1]["parallel"])
        finally:
            if "run_dir" in locals():
                cleanup(run_dir)
            tmp.cleanup()

    def test_unassigned_domains_are_reported_without_extra_details(self):
        tmp, result = run_prepare(
            adapter_manifest(),
            "--domain",
            "docs,calendar",
        )
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            run_dir = summary["run_dir"]
            self.assertEqual(summary["unassigned_domains"], ["calendar"])
            self.assertNotIn("metadata_waves", summary)
        finally:
            if "run_dir" in locals():
                cleanup(run_dir)
            tmp.cleanup()

    def test_offline_run_uses_user_provided_identity(self):
        tmp, result = run_prepare(offline_manifest())
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            run_dir = summary["run_dir"]
            plan = json.loads(Path(summary["plan_file"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["identity_call_count"], 0)
            self.assertIsNone(plan["identity_call"])
            self.assertEqual(plan["metadata_waves"], [])
        finally:
            if "run_dir" in locals():
                cleanup(run_dir)
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
