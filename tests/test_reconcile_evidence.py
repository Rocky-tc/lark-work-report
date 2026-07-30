import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "reconcile-evidence.py"


def record(record_id, source_type="tasks", **overrides):
    value = {
        "record_id": record_id,
        "source_type": source_type,
        "source_ref": f"source://host/{source_type}/{record_id}",
        "occurred_at": "2026-07-30T10:00:00+08:00",
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
        "work_relevance": "work",
        "sensitivity": "normal",
        "classification_reason": "当前用户明确产出",
        "retention_mode": "ephemeral",
    }
    value.update(overrides)
    return value


def run_reconciler(records, snapshot="2026-07-30T12:00:00+08:00"):
    tmp = tempfile.TemporaryDirectory()
    source = Path(tmp.name) / "records.json"
    output = Path(tmp.name) / "ledger.json"
    source.write_text(
        json.dumps(
            {"schema_version": 1, "snapshot": snapshot, "records": records},
            ensure_ascii=False,
        ),
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
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    ledger = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
    return tmp, result, ledger


class ReconcileEvidenceTests(unittest.TestCase):
    def test_semantic_duplicates_merge_and_preserve_all_sources(self):
        records = [
            record("task-1"),
            record(
                "comment-1",
                "comments",
                source_ref="https://example.com/comment/1",
                title="个人工作总结方案评审完成",
                activity="个人工作总结方案已完成评审",
                output="产出评审结论",
            ),
        ]
        tmp, result, ledger = run_reconciler(records)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(ledger["stats"]["deduplicated_count"], 1)
            self.assertEqual(len(ledger["work"]), 1)
            self.assertEqual(
                set(ledger["work"][0]["source_refs"]),
                {
                    "source://host/tasks/task-1",
                    "https://example.com/comment/1",
                },
            )
            self.assertEqual(
                ledger["work"][0]["source_type"],
                "tasks",
            )
        finally:
            tmp.cleanup()

    def test_newer_explicit_status_wins(self):
        records = [
            record(
                "old",
                status="in_progress",
                occurred_at="2026-07-29T10:00:00+08:00",
            ),
            record(
                "new",
                "mentions",
                status="completed",
                occurred_at="2026-07-30T10:00:00+08:00",
            ),
        ]
        tmp, result, ledger = run_reconciler(records)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(ledger["work"][0]["status"], "completed")
            self.assertFalse(ledger["work"][0]["status_conflict"])
        finally:
            tmp.cleanup()

    def test_explicit_completion_beats_newer_inference(self):
        records = [
            record(
                "explicit",
                status="completed",
                status_basis="explicit",
                occurred_at="2026-07-29T10:00:00+08:00",
            ),
            record(
                "inferred",
                "ai_sessions",
                status="in_progress",
                status_basis="inferred",
                occurred_at="2026-07-30T10:00:00+08:00",
            ),
        ]
        tmp, result, ledger = run_reconciler(records)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(ledger["work"][0]["status"], "completed")
        finally:
            tmp.cleanup()

    def test_equal_strength_same_time_conflict_moves_to_uncertain(self):
        records = [
            record("done", status="completed"),
            record("blocked", "comments", status="blocked"),
        ]
        tmp, result, ledger = run_reconciler(records)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(ledger["work"], [])
            self.assertEqual(len(ledger["uncertain"]), 1)
            self.assertTrue(ledger["uncertain"][0]["status_conflict"])
            self.assertEqual(ledger["stats"]["status_conflict_count"], 1)
        finally:
            tmp.cleanup()

    def test_work_and_uncertain_never_merge(self):
        records = [
            record("work"),
            record(
                "uncertain",
                "comments",
                work_relevance="uncertain",
                classification_reason="归属需要复核",
            ),
        ]
        tmp, result, ledger = run_reconciler(records)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(ledger["work"]), 1)
            self.assertEqual(len(ledger["uncertain"]), 1)
            self.assertEqual(ledger["stats"]["deduplicated_count"], 0)
        finally:
            tmp.cleanup()

    def test_same_source_cannot_cross_work_and_uncertain(self):
        source_ref = "https://example.com/shared"
        tmp, result, ledger = run_reconciler(
            [
                record("work", source_ref=source_ref),
                record(
                    "uncertain",
                    "comments",
                    source_ref=source_ref,
                    work_relevance="uncertain",
                    classification_reason="归属需要复核",
                ),
            ]
        )
        try:
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(ledger)
            self.assertIn(
                "cannot belong to both work and uncertain",
                result.stderr,
            )
        finally:
            tmp.cleanup()

    def test_due_response_and_self_responsibility_raise_priority(self):
        records = [
            record(
                "urgent",
                source_type="mentions",
                due_at="2026-07-31T10:00:00+08:00",
                requires_response=True,
                action_kind="reply",
                next_action="回复方案评审意见",
            ),
            record(
                "normal",
                source_type="code_activity",
                title="整理代码注释",
                activity="整理代码注释",
                output=None,
                impact=None,
                assignee_relation="unknown",
                confidence="medium",
            ),
        ]
        tmp, result, ledger = run_reconciler(records)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(ledger["work"][0]["record_ids"], ["urgent"])
            self.assertIn("需要当前主体回复", ledger["work"][0]["priority_basis"])
            self.assertIn("两天内到期", ledger["work"][0]["priority_basis"])
        finally:
            tmp.cleanup()

    def test_private_evidence_is_rejected_instead_of_persisted(self):
        tmp, result, ledger = run_reconciler(
            [record("private", sensitivity="private")]
        )
        try:
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(ledger)
            self.assertIn("must exclude private evidence", result.stderr)
        finally:
            tmp.cleanup()

    def test_invalid_future_event_window_is_rejected(self):
        tmp, result, ledger = run_reconciler(
            [
                record(
                    "meeting",
                    source_type="calendar",
                    signal_kind="event",
                    starts_at="2026-07-30T11:00:00+08:00",
                    ends_at="2026-07-30T10:00:00+08:00",
                )
            ]
        )
        try:
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(ledger)
            self.assertIn("starts_at must be before ends_at", result.stderr)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
