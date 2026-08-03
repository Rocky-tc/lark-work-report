import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import report_hydration  # noqa: E402


def work_item(cluster_id, **fields):
    return {
        "cluster_id": cluster_id,
        "record_ids": [f"record-{cluster_id}"],
        "source_ref": f"source://test/docs/{cluster_id}",
        "source_refs": [f"source://test/docs/{cluster_id}"],
        "priority": 10,
        "priority_basis": ["形成可核验产出"],
        "workstream": "报告生成",
        "activity": "优化生成流程",
        "status": "completed",
        "status_conflict": False,
        "output": "完成生成流程优化",
        "impact": "减少重复叙述",
        "decision": "沿用兼容模型",
        "next_action": "观察实际效果",
        "classification_reason": "正文明确记录工作结果",
        **fields,
    }


def ledger(*items):
    return {
        "schema_version": 2,
        "snapshot": "2026-07-27T12:00:00+08:00",
        "work": list(items),
        "uncertain": [],
    }


def plan():
    return {
        "period": {
            "routed_profile": "weekly",
            "start": "2026-07-20T00:00:00+08:00",
            "end": "2026-07-27T00:00:00+08:00",
            "snapshot": "2026-07-27T12:00:00+08:00",
            "title_period": "2026-07-20 至 2026-07-26",
        },
        "collected_domains": ["docs"],
    }


def model_for(source, section, item):
    model = {
        "schema_version": 5,
        "ledger_fingerprint": report_hydration.ledger_fingerprint(source),
        "summary": [],
        "workstreams": [],
        "risks": [],
        "next_actions": [],
        "uncertain": [],
    }
    model[section] = [item]
    return model


class ReportHydrationTests(unittest.TestCase):
    def test_direct_summary_uses_single_unambiguous_ledger_item(self):
        source = ledger(work_item("a"))
        hydrated = report_hydration.hydrate(
            model_for(source, "summary", {"evidence_refs": ["w0"]}),
            source,
            plan(),
        )
        self.assertEqual(hydrated["summary"][0]["result"], "完成生成流程优化")
        self.assertEqual(hydrated["summary"][0]["impact"], "减少重复叙述")
        self.assertEqual(hydrated["summary"][0]["decision"], "沿用兼容模型")

    def test_direct_workstream_hydrates_required_and_optional_fields(self):
        source = ledger(work_item("a"))
        hydrated = report_hydration.hydrate(
            model_for(source, "workstreams", {"evidence_refs": ["w0"]}),
            source,
            plan(),
        )
        item = hydrated["workstreams"][0]
        self.assertEqual(item["name"], "报告生成")
        self.assertEqual(item["status"], "completed")
        self.assertEqual(item["result"], "完成生成流程优化")
        self.assertEqual(item["progress"], "优化生成流程")

    def test_partial_model_only_needs_optional_narrative(self):
        source = ledger(work_item("a"))
        hydrated = report_hydration.hydrate(
            model_for(
                source,
                "summary",
                {
                    "impact": "模型补充的影响说明",
                    "evidence_refs": ["w0"],
                },
            ),
            source,
            plan(),
        )
        self.assertEqual(hydrated["summary"][0]["result"], "完成生成流程优化")
        self.assertEqual(
            hydrated["summary"][0]["impact"],
            "模型补充的影响说明",
        )

    def test_direct_fill_rejects_one_cluster_with_multiple_sources(self):
        source = ledger(
            work_item(
                "a",
                record_ids=["record-a", "record-b"],
                source_refs=["source://test/docs/a", "source://test/tasks/a"],
            )
        )
        with self.assertRaisesRegex(ValueError, "requires model narrative"):
            report_hydration.hydrate(
                model_for(source, "summary", {"evidence_refs": ["w0"]}),
                source,
                plan(),
            )

    def test_direct_fill_rejects_multiple_references(self):
        source = ledger(work_item("a"), work_item("b"))
        with self.assertRaisesRegex(ValueError, "requires model narrative"):
            report_hydration.hydrate(
                model_for(
                    source,
                    "summary",
                    {"evidence_refs": ["w0", "w1"]},
                ),
                source,
                plan(),
            )

    def test_direct_fill_rejects_status_conflict(self):
        source = ledger(work_item("a", status_conflict=True))
        with self.assertRaisesRegex(ValueError, "requires model narrative"):
            report_hydration.hydrate(
                model_for(source, "summary", {"evidence_refs": ["w0"]}),
                source,
                plan(),
            )

    def test_direct_fill_can_be_disabled_for_personal_template(self):
        source = ledger(work_item("a"))
        with self.assertRaisesRegex(ValueError, "requires model narrative"):
            report_hydration.hydrate(
                model_for(source, "summary", {"evidence_refs": ["w0"]}),
                source,
                plan(),
                allow_direct_fill=False,
            )


if __name__ == "__main__":
    unittest.main()
