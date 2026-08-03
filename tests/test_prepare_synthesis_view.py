import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_module():
    path = ROOT / "scripts" / "prepare-synthesis-view.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("prepare_synthesis_view", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def ledger_item(identifier):
    return {
        "cluster_id": identifier,
        "record_ids": ["record-1"],
        "source_type": "tasks",
        "source_types": ["tasks", "comments"],
        "source_ref": "source://host/tasks/1",
        "source_refs": [
            "source://host/tasks/1",
            "source://host/comments/1",
        ],
        "occurred_at": "2026-07-30T10:00:00+08:00",
        "actor": "当前用户",
        "workstream": "报告 Skill",
        "activity": "完成优化",
        "status": "completed",
        "status_basis": "explicit",
        "signal_kind": "outcome",
        "requires_response": False,
        "assignee_relation": "self",
        "participants": ["当前用户"],
        "confidence": "high",
        "work_relevance": "work",
        "sensitivity": "normal",
        "classification_reason": "明确工作产出",
        "retention_mode": "ephemeral",
        "status_conflict": False,
        "output": "完成实现",
        "priority_score": 90,
        "priority": 10,
        "priority_basis": ["有明确产出", "多来源相互印证"],
    }


class PrepareSynthesisViewTests(unittest.TestCase):
    def test_view_covers_every_cluster_without_source_urls(self):
        module = load_module()
        ledger = {
            "schema_version": 2,
            "snapshot": "2026-07-30T12:00:00+08:00",
            "work": [ledger_item("work-1")],
            "uncertain": [
                {
                    **ledger_item("uncertain-1"),
                    "work_relevance": "uncertain",
                }
            ],
        }
        view = module.prepare(ledger)
        self.assertEqual(
            {
                item["cluster_id"]
                for partition in ("work", "uncertain")
                for item in view[partition]
            },
            {"work-1", "uncertain-1"},
        )
        serialized = json.dumps(view, ensure_ascii=False)
        self.assertNotIn("source://", serialized)
        self.assertNotIn("source_ref", serialized)
        self.assertNotIn("record_ids", serialized)
        self.assertEqual(view["work"][0]["source_count"], 2)

    def test_compact_writer_does_not_pretty_print(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "view.json"
            module.write_json_compact_atomic(output, {"a": [1, 2]})
            self.assertEqual(output.read_text(encoding="utf-8"), '{"a":[1,2]}\n')


if __name__ == "__main__":
    unittest.main()
