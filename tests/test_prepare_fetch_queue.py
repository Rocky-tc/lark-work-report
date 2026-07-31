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


def indexed(queue):
    return [
        {"global_id": global_id, **item}
        for global_id, item in queue["candidate_index"].items()
    ]


def adapter(
    adapter_id,
    batch_size=1,
    *,
    parallelism=1,
    file_output=False,
):
    return {
        "adapter_id": adapter_id,
        "fetch_operation": (
            "candidate.fetch_many" if batch_size > 1 else "candidate.fetch"
        ),
        "fetch_batch_size": batch_size,
        "fetch_parallelism": parallelism,
        "fetch_file_output": file_output,
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
        self.assertEqual([item["id"] for item in indexed(queue)], ["C", "D"])
        self.assertEqual(queue["included_counts"], {"work": 1, "uncertain": 1})
        self.assertNotIn("skipped_counts", queue)
        self.assertNotIn("skipped_counts", summary)
        self.assertNotIn("unknown", indexed(queue)[0])
        self.assertEqual(
            indexed(queue)[0]["global_id"],
            "test.adapter:docs:C",
        )
        serialized = json.dumps(queue["candidate_index"], ensure_ascii=False)
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

    def test_new_work_signal_source_types_are_supported(self):
        result, queue = run_filter(
            [
                candidate("mention", "work", source_type="mentions"),
                candidate("comment", "work", source_type="comments"),
                candidate("code", "work", source_type="code_activity"),
                candidate("ai", "work", source_type="ai_sessions"),
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            {item["source_type"] for item in indexed(queue)},
            {"mentions", "comments", "code_activity", "ai_sessions"},
        )

    def test_action_metadata_is_preserved_without_body_content(self):
        result, queue = run_filter(
            [
                candidate(
                    "reply",
                    "work",
                    source_type="mentions",
                    due_at="2026-07-27T12:00:00+08:00",
                    requires_response=True,
                    action_kind="reply",
                    assignee_relation="self",
                )
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        item = indexed(queue)[0]
        self.assertTrue(item["requires_response"])
        self.assertEqual(item["action_kind"], "reply")
        self.assertEqual(item["assignee_relation"], "self")

    def test_invalid_action_metadata_is_rejected(self):
        result, _ = run_filter(
            [
                candidate(
                    "reply",
                    "work",
                    due_at="2026-07-27T12:00:00",
                    action_kind="respond_somehow",
                )
            ]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timezone offset", json.loads(result.stderr)["error"])

    def test_queue_uses_source_priority_when_candidate_priority_is_equal(self):
        result, queue = run_filter(
            [
                candidate("ai", "work", source_type="ai_sessions"),
                candidate("task", "work", source_type="tasks"),
                candidate("comment", "work", source_type="comments"),
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [item["source_type"] for item in indexed(queue)],
            ["tasks", "comments", "ai_sessions"],
        )

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
        self.assertEqual(indexed(queue)[0]["adapter_id"], "batch.adapter")
        self.assertEqual(indexed(queue)[0]["prefetch_relevance"], "work")
        self.assertEqual(len(indexed(queue)[0]["alternate_ids"]), 1)

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
            [len(batch["global_ids"]) for batch in queue["fetch_batches"]],
            [2, 2, 1],
        )

    def test_fetch_batches_use_managed_files_and_stable_global_ids(self):
        result, queue = run_filter(
            [candidate("A", "work"), candidate("B", "work")],
            run_plan(adapter("test.adapter", 2, file_output=True)),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        batch = queue["fetch_batches"][0]
        self.assertEqual(batch["batch_id"], "fetch-0001")
        self.assertEqual(
            batch["body_file"],
            "fetch-results/fetch-0001.json",
        )
        self.assertEqual(
            batch["evidence_file"],
            "evidence-parts/fetch-0001.json",
        )
        self.assertEqual(
            batch["semantic_file"],
            "semantic-bodies/fetch-0001.json",
        )
        self.assertTrue(batch["file_output"])
        self.assertEqual(
            batch["global_ids"],
            ["test.adapter:docs:A", "test.adapter:docs:B"],
        )
        self.assertEqual(
            batch["request_file"],
            "fetch-requests/fetch-0001.json",
        )

    def test_batch_request_materializes_indexed_candidates_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "candidates.json"
            output = root / "fetch-queue.json"
            plan_file = root / "run-plan.json"
            source.write_text(
                json.dumps(
                    {"candidates": [candidate("A", "work"), candidate("B", "work")]}
                ),
                encoding="utf-8",
            )
            plan_file.write_text(
                json.dumps(run_plan(adapter("test.adapter", 2))),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--file",
                    str(source),
                    "--output",
                    str(output),
                    "--run-plan",
                    str(plan_file),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            queue = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(queue["schema_version"], 2)
            self.assertNotIn("fetch_queue", queue)
            self.assertNotIn("items", queue["fetch_batches"][0])
            request = json.loads(
                (root / queue["fetch_batches"][0]["request_file"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                [item["global_id"] for item in request["items"]],
                queue["fetch_batches"][0]["global_ids"],
            )
            self.assertEqual(request["items"][0]["source_ref"], "source://docs/A")

    def test_fetch_waves_respect_adapter_parallelism(self):
        result, queue = run_filter(
            [candidate(str(index), "work") for index in range(5)],
            run_plan(adapter("test.adapter", 1, parallelism=2)),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [len(wave["batch_ids"]) for wave in queue["fetch_waves"]],
            [2, 2, 1],
        )
        self.assertTrue(queue["fetch_waves"][0]["parallel"])
        self.assertFalse(queue["fetch_waves"][-1]["parallel"])

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
        self.assertEqual(queue["candidate_index"], {})


if __name__ == "__main__":
    unittest.main()
