import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
RENDERER = ROOT / "scripts" / "render-report.py"
VALIDATOR = ROOT / "scripts" / "validate-report.py"


def model(profile="weekly"):
    return {
        "schema_version": 1,
        "profile": profile,
        "title": "张三个人工作报告｜2026-07-20 至 2026-07-26",
        "summary": [
            {
                "priority": 2,
                "result": "完成次要结果",
                "source_ref": "https://example.com/secondary",
            },
            {
                "priority": 1,
                "result": "完成关键结果",
                "impact": "统一了输出结构",
                "source_ref": "https://example.com/primary",
            },
        ],
        "workstreams": [
            {
                "priority": 1,
                "name": "报告 Skill",
                "status": "completed",
                "result": "形成五段式报告",
                "impact": "减少重复章节",
                "decision": "使用结构化模型统一渲染",
                "progress": "渲染器已开发完成",
                "source_ref": "source://host.lark/docs/doc-1",
            }
        ],
        "risks": [],
        "next_actions": [
            {
                "priority": 1,
                "action": "执行真实周报验证",
                "purpose": "验证阅读体验",
            }
        ],
        "uncertain": [],
        "coverage": {
            "start": "2026-07-20T00:00:00+08:00",
            "end": "2026-07-26T18:00:00+08:00",
            "snapshot": "2026-07-26T18:00:00+08:00",
            "domains": ["日历", "消息", "文档"],
            "access_gaps": [],
            "work_count": 3,
            "uncertain_count": 0,
        },
    }


def model_v2(profile="weekly"):
    payload = model(profile)
    payload["schema_version"] = 2
    payload["summary"][0].pop("source_ref")
    payload["summary"][0]["source_refs"] = [
        "https://example.com/secondary",
        "https://example.com/secondary-comment",
    ]
    payload["summary"][0]["priority_basis"] = "有明确影响、多来源印证"
    payload["workstreams"][0]["source_refs"] = [
        "source://host.lark/docs/doc-1",
        "source://host.lark/comments/comment-1",
    ]
    payload["next_actions"][0].update(
        {
            "action_kind": "reply",
            "due_at": "2026-07-27T12:00:00+08:00",
            "requires_response": True,
            "assignee_relation": "self",
            "priority_basis": "需要回复、两天内到期",
            "source_refs": ["source://host.lark/comments/comment-1"],
        }
    )
    return payload


def model_v3(profile="weekly"):
    payload = model_v2(profile)
    payload["schema_version"] = 3
    for section in (
        "summary",
        "workstreams",
        "risks",
        "next_actions",
        "uncertain",
    ):
        for index, item in enumerate(payload[section]):
            item["evidence_ids"] = [f"sha256:{section}-{index}"]
    return payload


def run_renderer(payload, output=False):
    tmp = tempfile.TemporaryDirectory()
    source = Path(tmp.name) / "model.json"
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    command = [sys.executable, str(RENDERER), "--file", str(source)]
    report = Path(tmp.name) / "report.md"
    if output:
        command.extend(["--output", str(report)])
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    return tmp, result, report


class RenderReportTests(unittest.TestCase):
    def test_weekly_report_has_five_sections_and_passes_validator(self):
        tmp, result, report = run_renderer(model(), output=True)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            markdown = report.read_text(encoding="utf-8")
            headings = [line for line in markdown.splitlines() if line.startswith("## ")]
            self.assertEqual(
                headings,
                [
                    "## 本周摘要",
                    "## 工作流进展与结果",
                    "## 风险与需协助事项",
                    "## 下周重点",
                    "## 待复核",
                ],
            )
            ledger_file = Path(tmp.name) / "ledger.json"
            ledger_file.write_text(
                json.dumps(
                    {
                        "work": [
                            {"source_ref": "https://example.com/primary"},
                            {"source_ref": "https://example.com/secondary"},
                            {"source_ref": "source://host.lark/docs/doc-1"},
                        ],
                        "uncertain": [],
                    }
                ),
                encoding="utf-8",
            )
            validation = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATOR),
                    "--profile",
                    "weekly",
                    "--file",
                    str(report),
                    "--ledger-file",
                    str(ledger_file),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                validation.returncode,
                0,
                validation.stdout + validation.stderr,
            )
        finally:
            tmp.cleanup()

    def test_result_precedes_impact_decision_and_progress(self):
        tmp, result, _ = run_renderer(model())
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            line = next(
                item for item in result.stdout.splitlines() if "报告 Skill" in item
            )
            self.assertLess(line.index("形成五段式报告"), line.index("影响："))
            self.assertLess(line.index("影响："), line.index("决策："))
            self.assertLess(line.index("决策："), line.index("进展："))
        finally:
            tmp.cleanup()

    def test_priority_controls_item_order(self):
        tmp, result, _ = run_renderer(model())
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLess(
                result.stdout.index("完成关键结果"),
                result.stdout.index("完成次要结果"),
            )
        finally:
            tmp.cleanup()

    def test_v2_renders_action_context_and_multiple_sources(self):
        tmp, result, report = run_renderer(model_v2(), output=True)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            markdown = report.read_text(encoding="utf-8")
            self.assertIn("行动类型：回复", markdown)
            self.assertIn("截止时间：2026-07-27T12:00:00+08:00", markdown)
            self.assertIn("需要回复：是", markdown)
            self.assertIn("责任关系：当前主体", markdown)
            self.assertIn("排序依据：需要回复、两天内到期", markdown)
            self.assertIn(
                "[工作来源](https://example.com/secondary-comment)",
                markdown,
            )
            ledger_file = Path(tmp.name) / "ledger.json"
            ledger_file.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "work": [
                            {"source_ref": "https://example.com/primary"},
                            {
                                "source_ref": "https://example.com/secondary",
                                "source_refs": [
                                    "https://example.com/secondary",
                                    "https://example.com/secondary-comment",
                                ],
                            },
                            {
                                "source_ref": "source://host.lark/docs/doc-1",
                                "source_refs": [
                                    "source://host.lark/docs/doc-1",
                                    "source://host.lark/comments/comment-1",
                                ],
                            },
                        ],
                        "uncertain": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            validation = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATOR),
                    "--profile",
                    "weekly",
                    "--file",
                    str(report),
                    "--ledger-file",
                    str(ledger_file),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                validation.returncode,
                0,
                validation.stdout + validation.stderr,
            )
        finally:
            tmp.cleanup()

    def test_v2_factual_action_requires_a_source(self):
        payload = model_v2()
        payload["next_actions"][0].pop("source_refs")
        tmp, result, _ = run_renderer(payload)
        try:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "requires source_ref or source_refs",
                result.stderr,
            )
        finally:
            tmp.cleanup()

    def test_v3_evidence_ids_do_not_change_rendered_report(self):
        v2_tmp, v2_result, _ = run_renderer(model_v2())
        v3_tmp, v3_result, _ = run_renderer(model_v3())
        try:
            self.assertEqual(v2_result.returncode, 0, v2_result.stderr)
            self.assertEqual(v3_result.returncode, 0, v3_result.stderr)
            self.assertEqual(v3_result.stdout, v2_result.stdout)
        finally:
            v2_tmp.cleanup()
            v3_tmp.cleanup()

    def test_v3_requires_evidence_ids_for_every_report_item(self):
        payload = model_v3()
        payload["summary"][0].pop("evidence_ids")
        tmp, result, _ = run_renderer(payload)
        try:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "summary[0].evidence_ids must be a non-empty string array",
                result.stderr,
            )
        finally:
            tmp.cleanup()

    def test_v1_next_action_output_remains_unchanged(self):
        tmp, result, _ = run_renderer(model())
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("- 执行真实周报验证；目标：验证阅读体验。", result.stdout)
            self.assertNotIn("行动类型", result.stdout)
            self.assertNotIn("截止时间", result.stdout)
        finally:
            tmp.cleanup()

    def test_v1_cannot_smuggle_v2_fields_through_normalization(self):
        payload = model()
        payload["summary"][0]["source_refs"] = [
            "https://example.com/secondary-comment"
        ]
        tmp, result, _ = run_renderer(payload)
        try:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unknown fields: source_refs", result.stderr)
        finally:
            tmp.cleanup()

    def test_empty_sections_are_one_concise_line(self):
        payload = model()
        for field in (
            "summary",
            "workstreams",
            "risks",
            "next_actions",
            "uncertain",
        ):
            payload[field] = []
        tmp, result, _ = run_renderer(payload)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count("\n- 无\n"), 5)
        finally:
            tmp.cleanup()

    def test_all_profiles_use_five_sections(self):
        for profile in ("daily", "weekly", "monthly"):
            with self.subTest(profile=profile):
                tmp, result, _ = run_renderer(model(profile))
                try:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        sum(
                            line.startswith("## ")
                            for line in result.stdout.splitlines()
                        ),
                        5,
                    )
                finally:
                    tmp.cleanup()

    def test_invalid_source_ref_is_rejected(self):
        payload = model()
        payload["summary"][0]["source_ref"] = "doc-raw-id"
        tmp, result, _ = run_renderer(payload)
        try:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must use http(s):// or source://", result.stderr)
        finally:
            tmp.cleanup()

    def test_unknown_field_is_rejected_instead_of_ignored(self):
        payload = model()
        payload["workstreams"][0]["reslut"] = "拼写错误不应被忽略"
        tmp, result, _ = run_renderer(payload)
        try:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unknown fields: reslut", result.stderr)
        finally:
            tmp.cleanup()

    def test_schema_version_is_required(self):
        payload = model()
        del payload["schema_version"]
        tmp, result, _ = run_renderer(payload)
        try:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("schema_version must be 1", result.stderr)
        finally:
            tmp.cleanup()

    def test_markdown_breaking_parenthesis_in_url_is_rejected(self):
        payload = model()
        payload["summary"][0]["source_ref"] = "https://example.com/a)b"
        tmp, result, _ = run_renderer(payload)
        try:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must use http(s):// or source://", result.stderr)
        finally:
            tmp.cleanup()

    def test_output_cannot_overwrite_report_model(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            source = Path(tmp.name) / "model.json"
            source.write_text(json.dumps(model()), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--file",
                    str(source),
                    "--output",
                    str(source),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("output path must differ", result.stderr)
            self.assertEqual(json.loads(source.read_text()), model())
        finally:
            tmp.cleanup()

    def test_coverage_cannot_end_after_snapshot(self):
        payload = model()
        payload["coverage"]["end"] = "2026-07-27T00:00:00+08:00"
        tmp, result, _ = run_renderer(payload)
        try:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cannot be later", result.stderr)
        finally:
            tmp.cleanup()

    def test_repeated_sources_use_one_short_reference_definition(self):
        payload = model_v2()
        repeated = "source://host.lark/docs/doc-1"
        payload["summary"][1]["source_ref"] = repeated
        payload["coverage"]["work_count"] = 2
        tmp, result, report = run_renderer(payload, output=True)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            markdown = report.read_text(encoding="utf-8")
            self.assertEqual(markdown.count(repeated), 1)
            self.assertGreaterEqual(markdown.count("[W1][W1]"), 2)
            self.assertIn(f"[W1]: {repeated}", markdown)

            ledger_file = Path(tmp.name) / "ledger.json"
            ledger_file.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "work": [
                            {
                                "source_ref": repeated,
                                "source_refs": [
                                    repeated,
                                    "source://host.lark/comments/comment-1",
                                ],
                            },
                            {
                                "source_ref": "https://example.com/secondary",
                                "source_refs": [
                                    "https://example.com/secondary",
                                    "https://example.com/secondary-comment",
                                ],
                            },
                        ],
                        "uncertain": [],
                    }
                ),
                encoding="utf-8",
            )
            validation = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATOR),
                    "--profile",
                    "weekly",
                    "--file",
                    str(report),
                    "--ledger-file",
                    str(ledger_file),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                validation.returncode,
                0,
                validation.stdout + validation.stderr,
            )
        finally:
            tmp.cleanup()

    def test_unique_sources_remain_inline_without_reference_definition(self):
        tmp, result, _ = run_renderer(model())
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "[工作来源](https://example.com/primary)",
                result.stdout,
            )
            self.assertNotIn("\n[W1]:", result.stdout)
        finally:
            tmp.cleanup()

    def test_complete_coverage_is_not_rendered(self):
        tmp, result, _ = run_renderer(model())
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("## 来源与覆盖", result.stdout)
            self.assertNotIn("覆盖说明", result.stdout)
            self.assertNotIn("work=3", result.stdout)
        finally:
            tmp.cleanup()

    def test_access_gap_is_rendered_without_an_independent_section(self):
        payload = model()
        payload["coverage"]["access_gaps"] = ["群消息无权限"]
        tmp, result, _ = run_renderer(payload)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("## 来源与覆盖", result.stdout)
            self.assertIn(
                "> 覆盖说明：已覆盖日历、消息、文档；未能访问群消息无权限。",
                result.stdout,
            )
            self.assertNotIn("work=3", result.stdout)
        finally:
            tmp.cleanup()

    def test_no_available_domain_is_disclosed(self):
        payload = model()
        payload["coverage"]["domains"] = []
        tmp, result, _ = run_renderer(payload)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("> 覆盖说明：未发现可用数据源。", result.stdout)
        finally:
            tmp.cleanup()

    def test_optional_summary_fact_repeated_in_detail_is_rendered_once(self):
        payload = model()
        payload["summary"][1]["impact"] = payload["workstreams"][0]["impact"]
        tmp, result, _ = run_renderer(payload)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count("减少重复章节"), 1)
            self.assertIn("完成关键结果", result.stdout)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
