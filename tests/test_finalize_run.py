import json
import hashlib
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
MANAGER = ROOT / "scripts" / "manage-run.py"
FINALIZER = ROOT / "scripts" / "finalize-run.py"


def run(command, *args):
    return subprocess.run(
        [sys.executable, str(command), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def report_model():
    return {
        "schema_version": 1,
        "profile": "weekly",
        "title": "个人工作周报｜2026-07-20 至 2026-07-26",
        "summary": [
            {
                "result": "完成报告优化",
                "source_ref": "https://example.com/work/1",
            }
        ],
        "workstreams": [],
        "risks": [],
        "next_actions": [],
        "uncertain": [],
        "coverage": {
            "start": "2026-07-20T00:00:00+08:00",
            "end": "2026-07-27T00:00:00+08:00",
            "snapshot": "2026-07-27T00:00:00+08:00",
            "domains": ["文档"],
            "access_gaps": [],
            "work_count": 1,
            "uncertain_count": 0,
        },
    }


def template_profile():
    names = {
        "summary": ("今日看点", "本周看点", "月度看点"),
        "progress": ("今日成果", "本周成果", "月度成果"),
        "risks": ("风险提醒", "风险提醒", "风险提醒"),
        "next": ("明日计划", "下周计划", "下月计划"),
        "uncertain": ("请我确认", "请我确认", "请我确认"),
        "coverage": ("依据与范围", "依据与范围", "依据与范围"),
    }
    return {
        "schema_version": 1,
        "template_id": "default",
        "source_fingerprint": "sha256:" + hashlib.sha256(b"sample").hexdigest(),
        "title_pattern": "{subject}{period_label}｜{period_range}",
        "sections": {
            slot: {
                "labels": dict(zip(("daily", "weekly", "monthly"), labels)),
                "item_style": "bullet",
            }
            for slot, labels in names.items()
        },
        "workstream_layout": "inline",
        "field_labels": {
            "impact": "影响",
            "decision": "决策",
            "progress": "进展",
            "assistance": "需协助",
            "purpose": "目标",
            "reason": "原因",
        },
        "tone": {
            "register": "concise",
            "voice": "neutral",
            "density": "compact",
        },
    }


class FinalizeRunTests(unittest.TestCase):
    def setUp(self):
        created = run(MANAGER, "create", "--profile", "weekly")
        self.assertEqual(created.returncode, 0, created.stderr)
        self.run_dir = Path(json.loads(created.stdout)["run_dir"])
        (self.run_dir / "run-plan.json").write_text(
            json.dumps({"period": {"routed_profile": "weekly"}}),
            encoding="utf-8",
        )
        (self.run_dir / "report-model.json").write_text(
            json.dumps(report_model(), ensure_ascii=False),
            encoding="utf-8",
        )
        (self.run_dir / "ledger.json").write_text(
            json.dumps(
                {
                    "work": [{"source_ref": "https://example.com/work/1"}],
                    "uncertain": [],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        if self.run_dir.exists():
            run(MANAGER, "cleanup", "--run-dir", str(self.run_dir))

    def test_render_and_validation_are_one_atomic_step(self):
        result = run(FINALIZER, "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        report = Path(payload["report_file"])
        self.assertTrue(report.is_file())
        self.assertIn("## 本周摘要", report.read_text(encoding="utf-8"))
        self.assertNotIn("# 个人工作周报", result.stdout)

    def test_invalid_evidence_does_not_write_report(self):
        (self.run_dir / "ledger.json").write_text(
            json.dumps({"work": [], "uncertain": []}),
            encoding="utf-8",
        )
        result = run(FINALIZER, "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse((self.run_dir / "report.md").exists())
        self.assertIn("不在 work 账本", result.stdout)

    def test_v2_multi_source_action_report_is_finalized(self):
        model = report_model()
        model["schema_version"] = 2
        model["summary"][0] = {
            "result": "完成报告优化",
            "source_refs": [
                "https://example.com/work/1",
                "https://example.com/comment/1",
            ],
        }
        model["next_actions"] = [
            {
                "action": "回复评审意见",
                "action_kind": "reply",
                "due_at": "2026-07-28T12:00:00+08:00",
                "requires_response": True,
                "assignee_relation": "self",
                "source_ref": "https://example.com/comment/1",
            }
        ]
        (self.run_dir / "report-model.json").write_text(
            json.dumps(model, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.run_dir / "ledger.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "work": [
                        {
                            "source_ref": "https://example.com/work/1",
                            "source_refs": [
                                "https://example.com/work/1",
                                "https://example.com/comment/1",
                            ],
                        }
                    ],
                    "uncertain": [],
                }
            ),
            encoding="utf-8",
        )
        result = run(FINALIZER, "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        markdown = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("行动类型：回复", markdown)
        self.assertIn("需要回复：是", markdown)
        self.assertIn("[W1][W1]", markdown)
        self.assertIn("[W1]: https://example.com/comment/1", markdown)

    def test_v3_requires_and_accepts_complete_ledger_coverage(self):
        model = report_model()
        model["schema_version"] = 3
        model["summary"][0]["evidence_ids"] = ["cluster-work-1"]
        ledger = {
            "schema_version": 2,
            "work": [
                {
                    "cluster_id": "cluster-work-1",
                    "source_ref": "https://example.com/work/1",
                }
            ],
            "uncertain": [],
        }
        (self.run_dir / "report-model.json").write_text(
            json.dumps(model, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.run_dir / "ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False),
            encoding="utf-8",
        )
        result = run(FINALIZER, "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_v4_hydrates_sources_priority_and_action_facts_from_ledger(self):
        model = report_model()
        model["schema_version"] = 4
        model["summary"][0] = {
            "result": "完成报告优化",
            "evidence_ids": ["cluster-work-1"],
        }
        model["next_actions"] = [
            {
                "action": "回复评审意见",
                "evidence_ids": ["cluster-work-1"],
            }
        ]
        ledger = {
            "schema_version": 2,
            "work": [
                {
                    "cluster_id": "cluster-work-1",
                    "source_ref": "https://example.com/work/1",
                    "source_refs": [
                        "https://example.com/work/1",
                        "https://example.com/comment/1",
                    ],
                    "priority": 7,
                    "priority_basis": ["当前主体责任明确", "两天内到期"],
                    "action_kind": "reply",
                    "due_at": "2026-07-28T12:00:00+08:00",
                    "requires_response": True,
                    "assignee_relation": "self",
                }
            ],
            "uncertain": [],
        }
        (self.run_dir / "report-model.json").write_text(
            json.dumps(model, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.run_dir / "ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False),
            encoding="utf-8",
        )
        result = run(FINALIZER, "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        markdown = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("[W2][W2]", markdown)
        self.assertIn("[W2]: https://example.com/comment/1", markdown)
        self.assertIn("行动类型：回复", markdown)
        self.assertIn("截止时间：2026-07-28T12:00:00+08:00", markdown)
        self.assertIn("需要回复：是", markdown)
        self.assertIn("排序依据：当前主体责任明确、两天内到期", markdown)

    def test_v4_omits_conflicting_action_fact_instead_of_guessing(self):
        model = report_model()
        model["schema_version"] = 4
        model["summary"] = []
        model["next_actions"] = [
            {
                "action": "确认最终截止时间",
                "evidence_ids": ["cluster-work-1", "cluster-work-2"],
            }
        ]
        model["coverage"]["work_count"] = 2
        ledger = {
            "schema_version": 2,
            "work": [
                {
                    "cluster_id": "cluster-work-1",
                    "source_ref": "https://example.com/work/1",
                    "source_refs": ["https://example.com/work/1"],
                    "priority": 10,
                    "due_at": "2026-07-28T12:00:00+08:00",
                },
                {
                    "cluster_id": "cluster-work-2",
                    "source_ref": "https://example.com/work/2",
                    "source_refs": ["https://example.com/work/2"],
                    "priority": 20,
                    "due_at": "2026-07-29T12:00:00+08:00",
                },
            ],
            "uncertain": [],
        }
        (self.run_dir / "report-model.json").write_text(
            json.dumps(model, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.run_dir / "ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False),
            encoding="utf-8",
        )
        result = run(FINALIZER, "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        markdown = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertNotIn("截止时间：", markdown)

    def test_v3_fails_when_any_ledger_cluster_is_omitted(self):
        model = report_model()
        model["schema_version"] = 3
        model["summary"][0]["evidence_ids"] = ["cluster-work-1"]
        model["coverage"]["work_count"] = 2
        ledger = {
            "schema_version": 2,
            "work": [
                {
                    "cluster_id": "cluster-work-1",
                    "source_ref": "https://example.com/work/1",
                },
                {
                    "cluster_id": "cluster-work-2",
                    "source_ref": "https://example.com/work/2",
                },
            ],
            "uncertain": [],
        }
        (self.run_dir / "report-model.json").write_text(
            json.dumps(model, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.run_dir / "ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False),
            encoding="utf-8",
        )
        result = run(FINALIZER, "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("cluster-work-2", result.stdout)
        self.assertFalse((self.run_dir / "report.md").exists())

    def test_v3_rejects_cross_ledger_evidence_reference(self):
        model = report_model()
        model["schema_version"] = 3
        model["summary"][0]["evidence_ids"] = ["cluster-uncertain-1"]
        ledger = {
            "schema_version": 2,
            "work": [
                {
                    "cluster_id": "cluster-work-1",
                    "source_ref": "https://example.com/work/1",
                }
            ],
            "uncertain": [
                {
                    "cluster_id": "cluster-uncertain-1",
                    "source_ref": "https://example.com/uncertain/1",
                }
            ],
        }
        model["coverage"]["uncertain_count"] = 1
        model["uncertain"] = [
            {
                "description": "事项归属待确认",
                "reason": "上下文不足",
                "source_ref": "https://example.com/uncertain/1",
                "evidence_ids": ["cluster-uncertain-1"],
            }
        ]
        (self.run_dir / "report-model.json").write_text(
            json.dumps(model, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.run_dir / "ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False),
            encoding="utf-8",
        )
        result = run(FINALIZER, "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("错误账本类型", result.stdout)

    def test_output_outside_run_is_rejected(self):
        result = run(
            FINALIZER,
            "--run-dir",
            str(self.run_dir),
            "--output",
            str(self.run_dir.parent / "escaped-report.md"),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("must remain inside", result.stderr)

    def test_default_template_is_applied_with_identity_and_period_context(self):
        (self.run_dir / "run-plan.json").write_text(
            json.dumps(
                {
                    "period": {
                        "routed_profile": "weekly",
                        "title_period": "2026-07-20 至 2026-07-26",
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.run_dir / "identity.json").write_text(
            json.dumps({"display_name": "张三"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.run_dir / "template-profile.json").write_text(
            json.dumps(template_profile(), ensure_ascii=False),
            encoding="utf-8",
        )
        result = run(FINALIZER, "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["template_id"], "default")
        markdown = Path(payload["report_file"]).read_text(encoding="utf-8")
        self.assertIn("# 张三个人周报｜2026-07-20 至 2026-07-26", markdown)
        self.assertIn("## 本周看点", markdown)

    def test_template_without_identity_context_fails_closed(self):
        (self.run_dir / "template-profile.json").write_text(
            json.dumps(template_profile(), ensure_ascii=False),
            encoding="utf-8",
        )
        result = run(FINALIZER, "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires identity", result.stderr)
        self.assertFalse((self.run_dir / "report.md").exists())


if __name__ == "__main__":
    unittest.main()
