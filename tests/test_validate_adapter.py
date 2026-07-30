import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "validate-adapter.py"


def run_validator(payload):
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "adapter.json"
        source.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--file", str(source)],
            capture_output=True,
            text=True,
            check=False,
        )


def manifest(metadata_only=True, report_fetch=True, template=False):
    return {
        "adapter_id": "host.lark",
        "capabilities": {
            "identity.current": {"available": True},
            "candidate.list": {
                "available": True,
                "metadata_only": metadata_only,
                "domains": ["docs", "im"],
            },
            "candidate.list_many": {"available": False},
            "candidate.fetch": {"available": True},
            "candidate.fetch_many": {"available": False},
            "report.create": {"available": True},
            "report.fetch": {"available": report_fetch},
            "template.fetch": {"available": template},
            "template.upsert": {"available": template},
            "template.delete": {"available": template},
        },
    }


class ValidateAdapterTests(unittest.TestCase):
    def test_two_phase_adapter_enables_broad_collection(self):
        result = run_validator(manifest())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["collection_mode"], "broad")
        self.assertEqual(payload["delivery_mode"], "document")
        self.assertTrue(payload["identity_available"])
        self.assertEqual(payload["metadata_strategy"], "serial")
        self.assertEqual(payload["fetch_strategy"], "serial")
        self.assertEqual(payload["template_mode"], "none")

    def test_template_storage_requires_verified_read_write_set(self):
        result = run_validator(manifest(template=True))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["template_mode"], "read_write")
        self.assertEqual(payload["template_fetch_operation"], "template.fetch")

    def test_template_write_without_delete_fails(self):
        payload = manifest()
        payload["capabilities"]["template.fetch"] = {"available": True}
        payload["capabilities"]["template.upsert"] = {"available": True}
        payload["capabilities"]["template.delete"] = {"available": False}
        result = run_validator(payload)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "template.upsert and template.delete must be available together",
            json.loads(result.stdout)["errors"],
        )

    def test_template_fetch_only_is_read_only(self):
        payload = manifest()
        payload["capabilities"]["template.fetch"] = {"available": True}
        payload["capabilities"]["template.upsert"] = {"available": False}
        payload["capabilities"]["template.delete"] = {"available": False}
        result = run_validator(payload)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["template_mode"], "read_only")

    def test_merged_read_adapter_is_explicit_only(self):
        result = run_validator(manifest(metadata_only=False))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["collection_mode"], "explicit_only")

    def test_create_without_fetch_fails(self):
        result = run_validator(manifest(report_fetch=False))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "report.create requires report.fetch for verified delivery",
            json.loads(result.stdout)["errors"],
        )

    def test_batch_capabilities_select_batch_operations(self):
        payload = manifest()
        payload["capabilities"]["candidate.list_many"] = {
            "available": True,
            "max_domains": 6,
        }
        payload["capabilities"]["candidate.fetch_many"] = {
            "available": True,
            "max_batch_size": 20,
        }
        result = run_validator(payload)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        validated = json.loads(result.stdout)
        self.assertEqual(validated["metadata_strategy"], "batch")
        self.assertEqual(validated["list_operation"], "candidate.list_many")
        self.assertEqual(validated["list_batch_size"], 6)
        self.assertEqual(validated["fetch_strategy"], "batch")
        self.assertEqual(validated["fetch_batch_size"], 20)

    def test_parallel_listing_declares_bounded_parallelism(self):
        payload = manifest()
        payload["capabilities"]["candidate.list"].update(
            {"parallel_safe": True, "max_parallelism": 4}
        )
        result = run_validator(payload)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        validated = json.loads(result.stdout)
        self.assertEqual(validated["metadata_strategy"], "parallel")
        self.assertEqual(validated["metadata_parallelism"], 4)

    def test_parallelism_without_parallel_safe_fails(self):
        payload = manifest()
        payload["capabilities"]["candidate.list"]["max_parallelism"] = 4
        result = run_validator(payload)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "candidate.list.max_parallelism requires parallel_safe=true",
            json.loads(result.stdout)["errors"],
        )

    def test_live_access_without_identity_fails(self):
        payload = manifest()
        payload["capabilities"]["identity.current"]["available"] = False
        result = run_validator(payload)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "live candidate access requires identity.current",
            json.loads(result.stdout)["errors"],
        )


if __name__ == "__main__":
    unittest.main()
