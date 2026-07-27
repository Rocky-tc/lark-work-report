import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare-fetch-queue.py"


def candidate(identifier, relevance, **overrides):
    result = {
        "id": identifier,
        "adapter_id": "test.adapter",
        "source_type": "docs",
        "occurred_at": "2026-07-26T10:00:00+08:00",
        "source_ref": f"source://docs/{identifier}",
        "prefetch_relevance": relevance,
        "classification_reason": "test classification",
    }
    result.update(overrides)
    return result


def run_filter(candidates, plan=None):
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "candidates.json"
        output = Path(tmp) / "queue.json"
        source.write_text(
            json.dumps({"candidates": candidates}, ensure_ascii=False),
            encoding="utf-8",
        )
        command = [
            sys.executable,
            str(SCRIPT),
            "--file",
            str(source),
            "--output",
            str(output),
        ]
        if plan is not None:
            plan_file = Path(tmp) / "run-plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            command.extend(["--run-plan", str(plan_file)])
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        queue = (
            json.loads(output.read_text(encoding="utf-8"))
            if output.exists()
            else None
        )
        return result, queue


def run_plan(*adapters):
    return {"adapters": list(adapters)}


def adapter(adapter_id, batch_size=1):
    return {
        "adapter_id": adapter_id,
        "fetch_operation": (
            "candidate.fetch_many" if batch_size > 1 else "candidate.fetch"
        ),
        "fetch_batch_size": batch_size,
    }


class PrepareFetchQueueTests(unittest.TestCase):
    def test_queue_contains_only_sanitized_work_and_uncertain(self):
        result, queue = run_filter(
            [
                candidate("A", "private", title="家庭体检安排"),
                candidate("B", "chatter", title="午饭约哪"),
                candidate("C", "work", title="项目 A 复盘", unknown="drop me"),
                candidate("D", "uncertain", title="新方案探索"),
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["queued"], 2)
        self.assertNotIn("fetch_queue", summary)
        self.assertEqual([item["id"] for item in queue["fetch_queue"]], ["C", "D"])
        self.assertEqual(queue["included_counts"], {"work": 1, "uncertain": 1})
        self.assertNotIn("skipped_counts", queue)
        self.assertNotIn("skipped_counts", summary)
        self.assertNotIn("unknown", queue["fetch_queue"][0])
        self.assertEqual(
            queue["fetch_queue"][0]["global_id"],
            "test.adapter:docs:C",
        )
        serialized = json.dumps(queue["fetch_queue"], ensure_ascii=False)
        self.assertNotIn("家庭体检安排", serialized)
        self.assertNotIn("午饭约哪", serialized)
        self.assertNotIn("项目 A 复盘", result.stdout)
        self.assertNotIn("新方案探索", result.stdout)
        self.assertNotIn('"private":', result.stdout)
        self.assertNotIn('"chatter":', result.stdout)

    def test_body_field_is_rejected_before_filtering(self):
        result, _ = run_filter(
            [candidate("X", "work", nested={"body": "私人正文意外混入"})]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("forbidden body field", json.loads(result.stderr)["error"])

    def test_unknown_relevance_is_rejected(self):
        result, _ = run_filter([candidate("X", "maybe")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid prefetch_relevance", json.loads(result.stderr)["error"])

    def test_missing_timezone_is_rejected(self):
        result, _ = run_filter(
            [candidate("X", "work", occurred_at="2026-07-26T10:00:00")]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timezone offset", json.loads(result.stderr)["error"])

    def test_duplicate_composite_identity_is_rejected(self):
        result, _ = run_filter(
            [candidate("X", "work"), candidate("X", "uncertain")]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicates stable identity", json.loads(result.stderr)["error"])

    def test_source_type_vocabulary_is_enforced(self):
        result, _ = run_filter(
            [candidate("X", "work", source_type="document")]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid source_type", json.loads(result.stderr)["error"])

    def test_source_ref_vocabulary_is_enforced(self):
        result, _ = run_filter([candidate("X", "work", source_ref="doc_X")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source_ref must use", json.loads(result.stderr)["error"])

    def test_markdown_breaking_source_ref_is_rejected(self):
        result, _ = run_filter(
            [candidate("X", "work", source_ref="https://example.com/a)b")]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source_ref must use", json.loads(result.stderr)["error"])

    def test_http_source_requires_a_host(self):
        result, _ = run_filter(
            [candidate("X", "work", source_ref="https://")]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source_ref must use", json.loads(result.stderr)["error"])

    def test_cross_domain_dedup_happens_before_fetch(self):
        shared = "https://example.com/docs/one"
        candidates = [
            candidate("doc-1", "uncertain", source_ref=shared, priority=2),
            candidate(
                "msg-1",
                "work",
                adapter_id="batch.adapter",
                source_type="im",
                source_ref=shared,
                priority=10,
            ),
        ]
        result, queue = run_filter(
            candidates,
            run_plan(
                adapter("test.adapter", 1),
                adapter("batch.adapter", 20),
            ),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["deduplicated_count"], 1)
        self.assertEqual(summary["queued"], 1)
        self.assertEqual(queue["fetch_queue"][0]["adapter_id"], "batch.adapter")
        self.assertEqual(queue["fetch_queue"][0]["prefetch_relevance"], "work")
        self.assertEqual(len(queue["fetch_queue"][0]["alternate_ids"]), 1)

    def test_private_and_work_conflict_on_same_source_is_rejected(self):
        shared = "source://host/docs/shared"
        result, queue = run_filter(
            [
                candidate("A", "work", source_ref=shared),
                candidate(
                    "B",
                    "private",
                    source_type="im",
                    source_ref=shared,
                ),
            ]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(queue)
        self.assertIn("conflicting fetch policy", json.loads(result.stderr)["error"])

    def test_fetch_queue_is_chunked_by_adapter_batch_size(self):
        result, queue = run_filter(
            [candidate(str(index), "work") for index in range(5)],
            run_plan(adapter("test.adapter", 2)),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["fetch_batch_count"], 3)
        self.assertEqual(
            [len(batch["items"]) for batch in queue["fetch_batches"]],
            [2, 2, 1],
        )

    def test_excluded_duplicates_do_not_affect_reported_dedup_count(self):
        shared = "source://host/docs/private"
        result, queue = run_filter(
            [
                candidate("A", "private", source_ref=shared),
                candidate("B", "private", source_type="im", source_ref=shared),
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["deduplicated_count"], 0)
        self.assertEqual(queue["deduplicated_count"], 0)
        self.assertEqual(queue["fetch_queue"], [])


if __name__ == "__main__":
    unittest.main()
