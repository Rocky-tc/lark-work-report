import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "validate-report.py"


VALID_WEEKLY = """\
# 张三个人周报｜2026-07-20 至 2026-07-26

## 本周概览
- 完成核心方案评审。[工作来源](https://example.com/overview)

## 各工作流进展与结果
- 报告 Skill 完成设计并进入开发。[工作来源](https://example.com/workstream)

## 关键产出和里程碑
- 形成可执行规格。[工作来源](https://example.com/output)

## 关键决策
- 采用一套引擎、三套报告策略。[工作来源](https://example.com/decision)

## 风险、阻塞和需协助事项
- 无

## 下周计划
- 完成真实数据 shadow run。

## 待复核
- 新方案探索可能属于本周工作；原因：工作流归属尚不明确。[待复核来源](https://example.com/uncertain)

## 证据索引与覆盖说明
- 时间窗：2026-07-20T00:00:00+08:00 至 2026-07-27T00:00:00+08:00
- 快照时间：2026-07-26T18:00:00+08:00
- 覆盖域：日历、消息、文档、任务、会议
- 权限缺口：话题群接口未覆盖
- 分类计数：work=12，uncertain=2
"""


def run_validator(markdown, profile="weekly"):
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.md"
        report.write_text(textwrap.dedent(markdown), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--profile", profile, "--file", str(report)],
            capture_output=True,
            text=True,
            check=False,
        )


class ValidateReportTests(unittest.TestCase):
    def test_complete_weekly_report_passes(self):
        result = run_validator(VALID_WEEKLY)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["errors"], [])

    def test_missing_required_section_fails(self):
        report = VALID_WEEKLY.replace("## 关键决策\n", "## 决策\n")
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("缺少必需章节：关键决策", json.loads(result.stdout)["errors"])

    def test_key_claim_without_source_anchor_fails(self):
        report = VALID_WEEKLY.replace(
            "- 形成可执行规格。[工作来源](https://example.com/output)",
            "- 形成可执行规格。",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        errors = json.loads(result.stdout)["errors"]
        self.assertTrue(any("关键产出和里程碑" in error and "来源锚" in error for error in errors))

    def test_key_claim_paragraph_without_source_anchor_fails(self):
        report = VALID_WEEKLY.replace(
            "- 报告 Skill 完成设计并进入开发。[工作来源](https://example.com/workstream)",
            "报告 Skill 完成设计并进入开发。",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        errors = json.loads(result.stdout)["errors"]
        self.assertTrue(any("各工作流进展与结果" in error and "来源锚" in error for error in errors))

    def test_placeholders_fail(self):
        report = VALID_WEEKLY.replace("- 完成真实数据 shadow run。", "- TODO：补充计划。")
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("报告包含 TODO/TBD 占位符", json.loads(result.stdout)["errors"])

    def test_missing_pending_review_section_fails(self):
        report = VALID_WEEKLY.replace(
            "## 待复核\n"
            "- 新方案探索可能属于本周工作；原因：工作流归属尚不明确。"
            "[待复核来源](https://example.com/uncertain)\n\n",
            "",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("缺少必需章节：待复核", json.loads(result.stdout)["errors"])

    def test_pending_review_item_requires_reason_and_typed_source(self):
        report = VALID_WEEKLY.replace(
            "- 新方案探索可能属于本周工作；原因：工作流归属尚不明确。"
            "[待复核来源](https://example.com/uncertain)",
            "- 新方案探索。[工作来源](https://example.com/uncertain)",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        errors = json.loads(result.stdout)["errors"]
        self.assertTrue(any("待复核" in error and "原因" in error for error in errors))
        self.assertTrue(any("待复核" in error and "待复核来源" in error for error in errors))

    def test_pending_review_source_cannot_support_work_claim(self):
        report = VALID_WEEKLY.replace(
            "[工作来源](https://example.com/output)",
            "[待复核来源](https://example.com/output)",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        errors = json.loads(result.stdout)["errors"]
        self.assertTrue(any("关键产出和里程碑" in error and "工作来源" in error for error in errors))

    def test_four_class_counts_fail(self):
        report = VALID_WEEKLY.replace(
            "- 分类计数：work=12，uncertain=2",
            "- 分类计数：work=12，uncertain=2，private=1，chatter=8",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("报告只能出现 work 和 uncertain 两类内容", json.loads(result.stdout)["errors"])

    def test_private_or_chatter_content_anywhere_in_report_fails(self):
        report = VALID_WEEKLY.replace(
            "## 下周计划\n",
            "## 私人材料\n- 家庭体检安排\n\n## 闲聊材料\n- 午饭约哪\n\n## 下周计划\n",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("报告只能出现 work 和 uncertain 两类内容", json.loads(result.stdout)["errors"])

    def test_aggregate_classification_counts_are_allowed(self):
        result = run_validator(VALID_WEEKLY)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["errors"], [])


if __name__ == "__main__":
    unittest.main()
