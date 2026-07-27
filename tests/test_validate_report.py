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

## 本周摘要
- 完成核心方案评审并形成统一输出方向。[工作来源](https://example.com/overview)

## 工作流进展与结果
- **报告 Skill｜已完成**：形成可执行规格；决策：采用一套引擎、三种报告格式。[工作来源](https://example.com/workstream)

## 风险与需协助事项
- 无

## 下周重点
- 完成真实数据 shadow run。

## 待复核
- 新方案探索可能属于本周工作；原因：工作流归属尚不明确。[待复核来源](https://example.com/uncertain)

## 来源与覆盖
- 时间窗：2026-07-20T00:00:00+08:00 至 2026-07-26T18:00:00+08:00
- 快照时间：2026-07-26T18:00:00+08:00
- 覆盖域：日历、消息、文档、任务、会议
- 权限缺口：话题群接口未覆盖
- 分类计数：work=2，uncertain=1
"""

DEFAULT_LEDGER = {
    "work": [
        {"source_ref": "https://example.com/overview"},
        {"source_ref": "https://example.com/workstream"},
    ],
    "uncertain": [
        {"source_ref": "https://example.com/uncertain"},
    ],
}


def run_validator(markdown, profile="weekly", ledger=DEFAULT_LEDGER):
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.md"
        report.write_text(textwrap.dedent(markdown), encoding="utf-8")
        command = [
            sys.executable,
            str(SCRIPT),
            "--profile",
            profile,
            "--file",
            str(report),
        ]
        ledger_file = Path(tmp) / "ledger.json"
        ledger_file.write_text(json.dumps(ledger), encoding="utf-8")
        command.extend(["--ledger-file", str(ledger_file)])
        return subprocess.run(
            command,
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
        report = VALID_WEEKLY.replace(
            "## 风险与需协助事项\n",
            "## 风险\n",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "缺少必需章节：风险与需协助事项",
            json.loads(result.stdout)["errors"],
        )

    def test_key_claim_without_source_anchor_fails(self):
        report = VALID_WEEKLY.replace(
            "[工作来源](https://example.com/workstream)",
            "",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        errors = json.loads(result.stdout)["errors"]
        self.assertTrue(
            any("工作流进展与结果" in error and "来源锚" in error for error in errors)
        )

    def test_key_claim_paragraph_without_source_anchor_fails(self):
        report = VALID_WEEKLY.replace(
            "- **报告 Skill｜已完成**：形成可执行规格；决策：采用一套引擎、三种报告格式。"
            "[工作来源](https://example.com/workstream)",
            "报告 Skill 已完成可执行规格。",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        errors = json.loads(result.stdout)["errors"]
        self.assertTrue(
            any("工作流进展与结果" in error and "来源锚" in error for error in errors)
        )

    def test_placeholders_fail(self):
        report = VALID_WEEKLY.replace(
            "- 完成真实数据 shadow run。",
            "- TODO：补充计划。",
        )
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
        self.assertTrue(
            any("待复核" in error and "待复核来源" in error for error in errors)
        )

    def test_pending_review_source_cannot_support_work_claim(self):
        report = VALID_WEEKLY.replace(
            "[工作来源](https://example.com/workstream)",
            "[待复核来源](https://example.com/workstream)",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        errors = json.loads(result.stdout)["errors"]
        self.assertTrue(
            any("工作流进展与结果" in error and "工作来源" in error for error in errors)
        )

    def test_four_class_counts_fail(self):
        report = VALID_WEEKLY.replace(
            "- 分类计数：work=2，uncertain=1",
            "- 分类计数：work=2，uncertain=1，private=1，chatter=8",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "报告只能出现 work 和 uncertain 两类内容",
            json.loads(result.stdout)["errors"],
        )

    def test_private_or_chatter_content_anywhere_in_report_fails(self):
        report = VALID_WEEKLY.replace(
            "## 下周重点\n",
            "## 私人材料\n- 家庭体检安排\n\n## 闲聊材料\n- 午饭约哪\n\n"
            "## 下周重点\n",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "报告只能出现 work 和 uncertain 两类内容",
            json.loads(result.stdout)["errors"],
        )

    def test_empty_sections_are_allowed(self):
        report = VALID_WEEKLY.replace(
            "- 新方案探索可能属于本周工作；原因：工作流归属尚不明确。"
            "[待复核来源](https://example.com/uncertain)",
            "- 无",
        ).replace(
            "- 分类计数：work=2，uncertain=1",
            "- 分类计数：work=2，uncertain=0",
        )
        result = run_validator(
            report,
            ledger={
                "work": DEFAULT_LEDGER["work"],
                "uncertain": [],
            },
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_duplicate_required_heading_fails(self):
        report = VALID_WEEKLY.replace(
            "## 工作流进展与结果",
            "## 本周摘要\n- 重复摘要。[工作来源](source://docs/duplicate)\n\n"
            "## 工作流进展与结果",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("必需章节重复：本周摘要", json.loads(result.stdout)["errors"])

    def test_required_heading_order_is_enforced(self):
        first = (
            "## 本周摘要\n"
            "- 完成核心方案评审并形成统一输出方向。"
            "[工作来源](https://example.com/overview)\n\n"
        )
        second = (
            "## 工作流进展与结果\n"
            "- **报告 Skill｜已完成**：形成可执行规格；决策：采用一套引擎、"
            "三种报告格式。[工作来源](https://example.com/workstream)\n\n"
        )
        report = VALID_WEEKLY.replace(first + second, second + first)
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "必需章节顺序不符合报告策略",
            json.loads(result.stdout)["errors"],
        )

    def test_stable_source_uri_is_accepted(self):
        stable_source = "source://docs/doc-x@2026-07-26T10:00:00+08:00"
        report = VALID_WEEKLY.replace(
            "https://example.com/workstream",
            stable_source,
        )
        ledger = {
            "work": [
                {"source_ref": "https://example.com/overview"},
                {"source_ref": stable_source},
            ],
            "uncertain": DEFAULT_LEDGER["uncertain"],
        }
        result = run_validator(
            report,
            ledger=ledger,
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_invalid_coverage_times_fail(self):
        report = VALID_WEEKLY.replace(
            "2026-07-20T00:00:00+08:00 至 2026-07-26T18:00:00+08:00",
            "x 至 y",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        errors = json.loads(result.stdout)["errors"]
        self.assertTrue(any("时间窗起点不是有效" in item for item in errors))
        self.assertTrue(any("时间窗终点不是有效" in item for item in errors))

    def test_time_window_cannot_end_after_snapshot(self):
        report = VALID_WEEKLY.replace(
            "2026-07-26T18:00:00+08:00\n- 快照时间",
            "2026-07-27T00:00:00+08:00\n- 快照时间",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "时间窗终点不得晚于快照时间",
            json.loads(result.stdout)["errors"],
        )

    def test_private_word_inside_source_url_does_not_false_positive(self):
        private_path = "https://example.com/private/workstream"
        report = VALID_WEEKLY.replace(
            "https://example.com/workstream",
            private_path,
        )
        ledger = {
            "work": [
                {"source_ref": "https://example.com/overview"},
                {"source_ref": private_path},
            ],
            "uncertain": DEFAULT_LEDGER["uncertain"],
        }
        result = run_validator(
            report,
            ledger=ledger,
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_ledger_counts_must_match_coverage(self):
        ledger = {
            "work": [{"source_ref": "source://docs/one"}],
            "uncertain": [{"source_ref": "source://docs/two"}],
        }
        result = run_validator(VALID_WEEKLY, ledger=ledger)
        self.assertNotEqual(result.returncode, 0)
        errors = json.loads(result.stdout)["errors"]
        self.assertTrue(
            any("分类计数与证据账本不一致：work" in item for item in errors)
        )

    def test_report_sources_must_exist_in_matching_ledgers(self):
        ledger = {
            "work": [
                {"source_ref": "https://example.com/overview"},
                {"source_ref": "https://example.com/different"},
            ],
            "uncertain": [
                {"source_ref": "https://example.com/uncertain"},
            ],
        }
        result = run_validator(VALID_WEEKLY, ledger=ledger)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "工作来源锚不在 work 账本中：https://example.com/workstream",
            json.loads(result.stdout)["errors"],
        )

    def test_source_cannot_exist_in_both_ledgers(self):
        ledger = {
            "work": [
                {"source_ref": "https://example.com/overview"},
                {"source_ref": "https://example.com/workstream"},
            ],
            "uncertain": [
                {"source_ref": "https://example.com/workstream"},
            ],
        }
        result = run_validator(VALID_WEEKLY, ledger=ledger)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "同一 source_ref 不能同时属于 work 和 uncertain："
            "https://example.com/workstream",
            json.loads(result.stdout)["errors"],
        )

    def test_positive_count_requires_a_matching_anchor(self):
        report = VALID_WEEKLY.replace(
            "- 新方案探索可能属于本周工作；原因：工作流归属尚不明确。"
            "[待复核来源](https://example.com/uncertain)",
            "- 无",
        )
        result = run_validator(report)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "分类计数包含 uncertain，但待复核章节没有来源锚",
            json.loads(result.stdout)["errors"],
        )

    def test_cli_requires_ledger_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.md"
            report.write_text(VALID_WEEKLY, encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--profile",
                    "weekly",
                    "--file",
                    str(report),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--ledger-file", result.stderr)


if __name__ == "__main__":
    unittest.main()
