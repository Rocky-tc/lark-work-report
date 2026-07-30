import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
MANAGER = ROOT / "scripts" / "manage-run.py"
COMPILER = ROOT / "scripts" / "compile-evidence.py"


def run(command, *args):
    return subprocess.run(
        [sys.executable, str(command), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def queue_item(identifier, source_type="tasks"):
    return {
        "id": identifier,
        "adapter_id": "test.adapter",
        "source_type": source_type,
        "occurred_at": "2026-07-30T10:00:00+08:00",
        "source_ref": f"source://host/{source_type}/{identifier}",
        "prefetch_relevance": "work",
        "classification_reason": "元数据表明与当前工作相关",
        "global_id": f"test.adapter:{source_type}:{identifier}",
    }


def record(item, relevance="work"):
    return {
        "record_id": item["global_id"],
        "source_type": item["source_type"],
        "source_ref": item["source_ref"],
        "occurred_at": item["occurred_at"],
        "title": "完成个人工作总结方案",
        "actor": "当前用户",
        "workstream": "工作总结 Skill",
        "activity": "完成个人工作总结方案评审",
        "status": "completed",
        "status_basis": "explicit",
        "output": "形成评审结论",
        "impact": "统一后续实现方向",
        "signal_kind": "outcome",
        "requires_response": False,
        "assignee_relation": "self",
        "participants": ["当前用户"],
        "confidence": "high",
        "work_relevance": relevance,
        "sensitivity": "normal",
        "classification_reason": "当前用户明确产出",
        "retention_mode": "ephemeral",
    }


class CompileEvidenceTests(unittest.TestCase):
    def setUp(self):
        created = run(MANAGER, "create", "--profile", "weekly")
        self.assertEqual(created.returncode, 0, created.stderr)
        self.run_dir = Path(json.loads(created.stdout)["run_dir"])
        (self.run_dir / "run-plan.json").write_text(
            json.dumps(
                {
                    "period": {
                        "snapshot": "2026-07-30T12:00:00+08:00",
                        "routed_profile": "weekly",
                    }
                }
            ),
            encoding="utf-8",
        )
        self.work = queue_item("work")
        self.other = queue_item("other", "comments")
        (self.run_dir / "fetch-queue.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "fetch_queue": [self.work, self.other],
                }
            ),
            encoding="utf-8",
        )
        (self.run_dir / "evidence-parts").mkdir()

    def tearDown(self):
        if self.run_dir.exists():
            run(MANAGER, "cleanup", "--run-dir", str(self.run_dir))

    def write_part(self, results, name="fetch-0001.json"):
        (self.run_dir / "evidence-parts" / name).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "batch_id": name.removesuffix(".json"),
                    "results": results,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_complete_parts_compile_records_ledger_and_private_safe_audit(self):
        self.write_part(
            [
                {
                    "global_id": self.work["global_id"],
                    "outcome": "work",
                    "record": record(self.work),
                },
                {
                    "global_id": self.other["global_id"],
                    "outcome": "discarded_private",
                },
            ]
        )
        result = run(COMPILER, "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = json.loads(
            (self.run_dir / "evidence-records.json").read_text(encoding="utf-8")
        )
        ledger = json.loads(
            (self.run_dir / "ledger.json").read_text(encoding="utf-8")
        )
        audit = json.loads(
            (self.run_dir / "extraction-audit.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(records["records"]), 1)
        self.assertEqual(len(ledger["work"]), 1)
        self.assertEqual(audit["processed_count"], 2)
        self.assertEqual(
            audit["outcome_counts"],
            {
                "work": 1,
                "uncertain": 0,
                "discarded_private": 1,
                "discarded_chatter": 0,
                "access_gap": 0,
            },
        )
        self.assertNotIn("title", json.dumps(audit, ensure_ascii=False))

    def test_missing_candidate_result_fails_closed_without_outputs(self):
        self.write_part(
            [
                {
                    "global_id": self.work["global_id"],
                    "outcome": "work",
                    "record": record(self.work),
                }
            ]
        )
        result = run(COMPILER, "--run-dir", str(self.run_dir))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing extraction results", result.stderr)
        self.assertFalse((self.run_dir / "evidence-records.json").exists())
        self.assertFalse((self.run_dir / "ledger.json").exists())

    def test_duplicate_candidate_result_is_rejected(self):
        duplicated = {
            "global_id": self.work["global_id"],
            "outcome": "work",
            "record": record(self.work),
        }
        self.write_part([duplicated])
        self.write_part(
            [
                duplicated,
                {
                    "global_id": self.other["global_id"],
                    "outcome": "discarded_chatter",
                },
            ],
            "fetch-0002.json",
        )
        result = run(COMPILER, "--run-dir", str(self.run_dir))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate extraction result", result.stderr)

    def test_record_identity_must_match_fetch_queue(self):
        mismatched = record(self.work)
        mismatched["source_ref"] = "source://host/tasks/different"
        self.write_part(
            [
                {
                    "global_id": self.work["global_id"],
                    "outcome": "work",
                    "record": mismatched,
                },
                {
                    "global_id": self.other["global_id"],
                    "outcome": "access_gap",
                    "reason": "正文接口无权限",
                },
            ]
        )
        result = run(COMPILER, "--run-dir", str(self.run_dir))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match fetch queue", result.stderr)

    def test_access_gap_is_preserved_without_inventing_evidence(self):
        self.write_part(
            [
                {
                    "global_id": self.work["global_id"],
                    "outcome": "access_gap",
                    "reason": "正文接口无权限",
                },
                {
                    "global_id": self.other["global_id"],
                    "outcome": "uncertain",
                    "record": record(self.other, "uncertain"),
                },
            ]
        )
        result = run(COMPILER, "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        audit = json.loads(
            (self.run_dir / "extraction-audit.json").read_text(encoding="utf-8")
        )
        self.assertEqual(audit["outcome_counts"]["access_gap"], 1)
        self.assertEqual(
            audit["access_gaps"],
            [
                {
                    "global_id": self.work["global_id"],
                    "source_ref": self.work["source_ref"],
                    "reason": "正文接口无权限",
                }
            ],
        )

    def test_managed_batches_require_complete_body_files(self):
        queue = json.loads(
            (self.run_dir / "fetch-queue.json").read_text(encoding="utf-8")
        )
        queue["fetch_batches"] = [
            {
                "batch_id": "fetch-0001",
                "body_file": "fetch-results/fetch-0001.json",
                "evidence_file": "evidence-parts/fetch-0001.json",
                "items": [
                    {"global_id": self.work["global_id"]},
                    {"global_id": self.other["global_id"]},
                ],
            }
        ]
        (self.run_dir / "fetch-queue.json").write_text(
            json.dumps(queue),
            encoding="utf-8",
        )
        self.write_part(
            [
                {
                    "global_id": self.work["global_id"],
                    "outcome": "work",
                    "record": record(self.work),
                },
                {
                    "global_id": self.other["global_id"],
                    "outcome": "discarded_chatter",
                },
            ]
        )
        result = run(COMPILER, "--run-dir", str(self.run_dir))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing complete fetch body file", result.stderr)

        (self.run_dir / "fetch-results").mkdir()
        (self.run_dir / "fetch-results" / "fetch-0001.json").write_text(
            json.dumps({"items": ["complete body retained in managed file"]}),
            encoding="utf-8",
        )
        result = run(COMPILER, "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
