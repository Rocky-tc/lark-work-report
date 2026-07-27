#!/usr/bin/env python3
"""Validate the structural and evidence contract of a work-report draft."""

import argparse
import json
import re
from pathlib import Path


PROFILE_SECTIONS = {
    "daily": (
        "今日摘要",
        "今日完成",
        "关键产出与结果",
        "进行中事项",
        "阻塞与需协助事项",
        "明日计划",
        "待复核",
        "关键来源与覆盖说明",
    ),
    "weekly": (
        "本周概览",
        "各工作流进展与结果",
        "关键产出和里程碑",
        "关键决策",
        "风险、阻塞和需协助事项",
        "下周计划",
        "待复核",
        "证据索引与覆盖说明",
    ),
    "monthly": (
        "月度结果摘要",
        "目标、结果和可核验指标",
        "重点工作流演进",
        "关键里程碑和决策",
        "与上月或月初目标的对比",
        "风险、依赖和未决问题",
        "下月重点",
        "待复核",
        "证据索引与覆盖说明",
    ),
    "custom": (
        "周期概览",
        "各工作流进展与结果",
        "关键产出和里程碑",
        "风险、阻塞和需协助事项",
        "下一周期计划",
        "待复核",
        "证据索引与覆盖说明",
    ),
}

EVIDENCE_SECTIONS = {
    "daily": {"今日摘要", "今日完成", "关键产出与结果", "进行中事项", "阻塞与需协助事项"},
    "weekly": {"本周概览", "各工作流进展与结果", "关键产出和里程碑", "关键决策", "风险、阻塞和需协助事项"},
    "monthly": {
        "月度结果摘要",
        "目标、结果和可核验指标",
        "重点工作流演进",
        "关键里程碑和决策",
        "与上月或月初目标的对比",
        "风险、依赖和未决问题",
    },
    "custom": {"周期概览", "各工作流进展与结果", "关键产出和里程碑", "风险、阻塞和需协助事项"},
}

WORK_SOURCE_LINK = re.compile(r"\[工作来源\]\(https?://[^)]+\)")
UNCERTAIN_SOURCE_LINK = re.compile(r"\[待复核来源\]\(https?://[^)]+\)")
EMPTY_ITEM = re.compile(r"^(?:-\s*)?(无|暂无|未发现|证据不足)(?:[。；;.]|$)")
CLASSIFICATION_COUNTS = re.compile(
    r"^-\s*分类计数[：:]\s*work=\d+[，,\s]+uncertain=\d+\s*$",
    re.IGNORECASE | re.MULTILINE,
)
FORBIDDEN_CLASS = re.compile(
    r"\b(?:private|chatter)\b|私人材料|私人标题|私人摘要|闲聊材料|闲聊标题|闲聊摘要",
    re.IGNORECASE,
)


def split_sections(markdown):
    sections = {}
    current = None
    for raw_line in markdown.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", raw_line)
        if heading:
            current = heading.group(1)
            sections[current] = []
        elif current is not None:
            sections[current].append(raw_line)
    return sections


def validate(markdown, profile):
    errors = []
    warnings = []
    sections = split_sections(markdown)

    if not re.search(r"^#\s+\S+", markdown, re.MULTILINE):
        errors.append("缺少一级标题")

    for name in PROFILE_SECTIONS[profile]:
        if name not in sections:
            errors.append(f"缺少必需章节：{name}")

    if re.search(r"\b(?:TODO|TBD)\b", markdown, re.IGNORECASE):
        errors.append("报告包含 TODO/TBD 占位符")

    for name in EVIDENCE_SECTIONS[profile]:
        for raw_line in sections.get(name, []):
            line = raw_line.strip()
            if not line or line.startswith(("###", "```", "|")) or EMPTY_ITEM.match(line):
                continue
            if not WORK_SOURCE_LINK.search(line):
                errors.append(f"章节“{name}”的事实条目缺少工作来源锚：{line}")

    for raw_line in sections.get("待复核", []):
        line = raw_line.strip()
        if not line or line.startswith(("###", "```", "|")) or EMPTY_ITEM.match(line):
            continue
        if "原因" not in line:
            errors.append(f"章节“待复核”的候选缺少待复核原因：{line}")
        if not UNCERTAIN_SOURCE_LINK.search(line):
            errors.append(f"章节“待复核”的候选缺少待复核来源锚：{line}")

    coverage_name = PROFILE_SECTIONS[profile][-1]
    coverage = "\n".join(sections.get(coverage_name, []))
    for label in ("时间窗", "快照时间", "覆盖域", "权限缺口"):
        if not re.search(rf"{label}[：:]", coverage):
            errors.append(f"覆盖说明缺少：{label}")
    if not CLASSIFICATION_COUNTS.search(coverage):
        errors.append("覆盖说明缺少 work/uncertain 两类聚合计数")
    if FORBIDDEN_CLASS.search(markdown):
        errors.append("报告只能出现 work 和 uncertain 两类内容")

    if not WORK_SOURCE_LINK.search(markdown):
        warnings.append("报告没有任何可点击工作来源锚")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=tuple(PROFILE_SECTIONS))
    parser.add_argument("--file", required=True)
    return parser


def main():
    args = build_parser().parse_args()
    markdown = Path(args.file).read_text(encoding="utf-8")
    result = validate(markdown, args.profile)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
