#!/usr/bin/env python3
"""Validate report structure, evidence anchors, coverage, and required ledgers."""

import argparse
import json
import re
import sys
from pathlib import Path

from contracts import PROFILE_SECTIONS
from source_refs import SOURCE_TARGET, is_valid_source_ref
from template_profiles import field_labels, load as load_template, section_settings
from value_contracts import parse_aware_datetime as parse_time_contract


WORK_SOURCE_LINK = re.compile(rf"\[工作来源\]\(({SOURCE_TARGET})\)")
UNCERTAIN_SOURCE_LINK = re.compile(rf"\[待复核来源\]\(({SOURCE_TARGET})\)")
EMPTY_ITEM = re.compile(r"^(?:-\s*)?(无|暂无|未发现|证据不足)(?:[。；;.]|$)")
CLASSIFICATION_COUNTS = re.compile(
    r"^-\s*分类计数[：:]\s*work=(\d+)[，,\s]+uncertain=(\d+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
TIME_WINDOW = re.compile(r"^-\s*时间窗[：:]\s*(\S+)\s+至\s+(\S+)\s*$", re.MULTILINE)
SNAPSHOT_TIME = re.compile(r"^-\s*快照时间[：:]\s*(\S+)\s*$", re.MULTILINE)
FORBIDDEN_CLASS = re.compile(
    r"\b(?:private|chatter)\b|私人材料|私人标题|私人摘要|闲聊材料|闲聊标题|闲聊摘要",
    re.IGNORECASE,
)


def split_sections(markdown):
    sections = {}
    headings = []
    current = None
    for raw_line in markdown.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", raw_line)
        if heading:
            current = heading.group(1)
            headings.append(current)
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(raw_line)
    return sections, headings


def parse_aware_datetime(value, label, errors):
    try:
        _, parsed = parse_time_contract(value, label)
    except ValueError:
        errors.append(f"{label}不是有效的 ISO 8601 时间：{value}")
        return None
    return parsed


def validate_coverage(coverage, errors):
    for label in ("覆盖域", "权限缺口"):
        if not re.search(rf"^-\s*{label}[：:]\s*\S+", coverage, re.MULTILINE):
            errors.append(f"覆盖说明缺少：{label}")

    window = TIME_WINDOW.search(coverage)
    snapshot = SNAPSHOT_TIME.search(coverage)
    if not window:
        errors.append("覆盖说明缺少有效时间窗")
    if not snapshot:
        errors.append("覆盖说明缺少有效快照时间")

    start = end = snapshot_time = None
    if window:
        start = parse_aware_datetime(window.group(1), "时间窗起点", errors)
        end = parse_aware_datetime(window.group(2), "时间窗终点", errors)
    if snapshot:
        snapshot_time = parse_aware_datetime(snapshot.group(1), "快照时间", errors)
    if start and end and start >= end:
        errors.append("时间窗起点必须早于终点")
    if end and snapshot_time and end > snapshot_time:
        errors.append("时间窗终点不得晚于快照时间")

    counts = CLASSIFICATION_COUNTS.search(coverage)
    if not counts:
        errors.append("覆盖说明缺少 work/uncertain 两类聚合计数")
        return None
    return {"work": int(counts.group(1)), "uncertain": int(counts.group(2))}


def validate_ledger(ledger, coverage_counts, errors):
    refs = {"work": set(), "uncertain": set()}
    if not isinstance(ledger, dict):
        errors.append("证据账本必须是对象")
        return refs
    for name in ("work", "uncertain"):
        items = ledger.get(name)
        if not isinstance(items, list):
            errors.append(f"证据账本缺少数组：{name}")
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"证据账本 {name}[{index}] 必须是对象")
                continue
            item_refs = []
            source_ref = item.get("source_ref")
            if source_ref is not None:
                item_refs.append(source_ref)
            source_refs = item.get("source_refs", [])
            if not isinstance(source_refs, list):
                errors.append(f"证据账本 {name}[{index}].source_refs 必须是数组")
                source_refs = []
            item_refs.extend(source_refs)
            item_refs = list(dict.fromkeys(item_refs))
            if not item_refs:
                errors.append(
                    f"证据账本 {name}[{index}] 缺少 source_ref/source_refs"
                )
            for source in item_refs:
                if not isinstance(source, str) or not source.strip():
                    errors.append(f"证据账本 {name}[{index}] 包含空来源")
                elif not is_valid_source_ref(source):
                    errors.append(f"证据账本 {name}[{index}] 的来源无效")
                elif source in refs[name]:
                    errors.append(f"证据账本 {name} 存在重复 source_ref：{source}")
                else:
                    refs[name].add(source)
    if coverage_counts:
        for name in ("work", "uncertain"):
            items = ledger.get(name)
            if isinstance(items, list) and len(items) != coverage_counts[name]:
                errors.append(
                    f"分类计数与证据账本不一致：{name}="
                    f"{coverage_counts[name]}，ledger={len(items)}"
                )
    overlap = refs["work"] & refs["uncertain"]
    for source_ref in sorted(overlap):
        errors.append(f"同一 source_ref 不能同时属于 work 和 uncertain：{source_ref}")
    return refs


def validate_model_coverage(model, ledger):
    errors = []
    if not isinstance(model, dict) or model.get("schema_version") != 3:
        return {"ok": True, "errors": []}
    if not isinstance(ledger, dict) or ledger.get("schema_version") != 2:
        return {
            "ok": False,
            "errors": ["报告模型 v3 要求证据账本 schema_version=2"],
        }

    ledger_ids = {"work": set(), "uncertain": set()}
    for name in ("work", "uncertain"):
        items = ledger.get(name)
        if not isinstance(items, list):
            errors.append(f"证据账本缺少数组：{name}")
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"证据账本 {name}[{index}] 必须是对象")
                continue
            cluster_id = item.get("cluster_id")
            if not isinstance(cluster_id, str) or not cluster_id.strip():
                errors.append(f"证据账本 {name}[{index}] 缺少 cluster_id")
                continue
            cluster_id = cluster_id.strip()
            if cluster_id in ledger_ids["work"] | ledger_ids["uncertain"]:
                errors.append(f"证据账本存在重复 cluster_id：{cluster_id}")
            ledger_ids[name].add(cluster_id)

    report_ids = {"work": set(), "uncertain": set()}
    section_kinds = {
        "summary": "work",
        "workstreams": "work",
        "risks": "work",
        "next_actions": "work",
        "uncertain": "uncertain",
    }
    for section, kind in section_kinds.items():
        items = model.get(section)
        if not isinstance(items, list):
            errors.append(f"报告模型缺少数组：{section}")
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"报告模型 {section}[{index}] 必须是对象")
                continue
            evidence_ids = item.get("evidence_ids")
            if (
                not isinstance(evidence_ids, list)
                or not evidence_ids
                or not all(
                    isinstance(value, str) and value.strip()
                    for value in evidence_ids
                )
            ):
                errors.append(
                    f"报告模型 {section}[{index}] 缺少有效 evidence_ids"
                )
                continue
            normalized = [value.strip() for value in evidence_ids]
            if len(normalized) != len(set(normalized)):
                errors.append(
                    f"报告模型 {section}[{index}].evidence_ids 存在重复值"
                )
            for evidence_id in normalized:
                if evidence_id not in ledger_ids[kind]:
                    other = "uncertain" if kind == "work" else "work"
                    if evidence_id in ledger_ids[other]:
                        errors.append(
                            f"报告模型 {section}[{index}] 引用了错误账本类型："
                            f"{evidence_id}"
                        )
                    else:
                        errors.append(
                            f"报告模型 {section}[{index}] 引用了未知证据："
                            f"{evidence_id}"
                        )
                else:
                    report_ids[kind].add(evidence_id)

    for kind in ("work", "uncertain"):
        missing = sorted(ledger_ids[kind] - report_ids[kind])
        if missing:
            errors.append(
                f"报告模型未覆盖 {kind} 账本条目：" + "、".join(missing)
            )
    return {"ok": not errors, "errors": errors}


def validate(markdown, profile, ledger, template=None):
    errors = []
    warnings = []
    sections, headings = split_sections(markdown)
    settings = section_settings(template, profile)
    required = tuple(setting["label"] for setting in settings)
    evidence_sections = set(required[:3])
    reason_label = field_labels(template)["reason"]

    if not re.search(r"^#\s+\S+", markdown, re.MULTILINE):
        errors.append("缺少一级标题")

    for name in required:
        count = headings.count(name)
        if count == 0:
            errors.append(f"缺少必需章节：{name}")
        elif count > 1:
            errors.append(f"必需章节重复：{name}")

    positions = [headings.index(name) for name in required if name in headings]
    if len(positions) == len(required) and positions != sorted(positions):
        errors.append("必需章节顺序不符合报告策略")

    if re.search(r"\b(?:TODO|TBD)\b", markdown, re.IGNORECASE):
        errors.append("报告包含 TODO/TBD 占位符")

    for name in evidence_sections:
        for raw_line in sections.get(name, []):
            line = raw_line.strip()
            if not line or line.startswith(("###", "```", "|")) or EMPTY_ITEM.match(line):
                continue
            if not WORK_SOURCE_LINK.search(line):
                errors.append(f"章节“{name}”的事实条目缺少工作来源锚：{line}")

    uncertain_name = required[4]
    for raw_line in sections.get(uncertain_name, []):
        line = raw_line.strip()
        if not line or line.startswith(("###", "```", "|")) or EMPTY_ITEM.match(line):
            continue
        if reason_label not in line:
            errors.append(f"章节“{uncertain_name}”的候选缺少待复核原因：{line}")
        if not UNCERTAIN_SOURCE_LINK.search(line):
            errors.append(f"章节“{uncertain_name}”的候选缺少待复核来源锚：{line}")

    coverage_name = required[-1]
    coverage = "\n".join(sections.get(coverage_name, []))
    coverage_counts = validate_coverage(coverage, errors)
    work_sources = set(WORK_SOURCE_LINK.findall(markdown))
    uncertain_sources = set(UNCERTAIN_SOURCE_LINK.findall(markdown))
    for source_ref in sorted(work_sources | uncertain_sources):
        if not is_valid_source_ref(source_ref):
            errors.append(f"报告包含无效来源锚：{source_ref}")

    if coverage_counts:
        if coverage_counts["work"] > 0 and not work_sources:
            errors.append("分类计数包含 work，但报告没有工作来源锚")
        if coverage_counts["work"] == 0 and work_sources:
            errors.append("分类计数为 work=0，但报告包含工作来源锚")
        if coverage_counts["uncertain"] > 0 and not uncertain_sources:
            errors.append("分类计数包含 uncertain，但待复核章节没有来源锚")
        if coverage_counts["uncertain"] == 0 and uncertain_sources:
            errors.append("分类计数为 uncertain=0，但报告包含待复核来源锚")

    visible_text = re.sub(r"\]\([^)]+\)", "]", markdown)
    if FORBIDDEN_CLASS.search(visible_text):
        errors.append("报告只能出现 work 和 uncertain 两类内容")

    if not work_sources:
        warnings.append("报告没有任何可核验工作来源锚")

    ledger_refs = validate_ledger(ledger, coverage_counts, errors)
    for source_ref in sorted(work_sources - ledger_refs["work"]):
        errors.append(f"工作来源锚不在 work 账本中：{source_ref}")
    for source_ref in sorted(uncertain_sources - ledger_refs["uncertain"]):
        errors.append(f"待复核来源锚不在 uncertain 账本中：{source_ref}")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=tuple(PROFILE_SECTIONS))
    parser.add_argument("--file", required=True)
    parser.add_argument("--ledger-file", required=True)
    parser.add_argument("--template-file")
    parser.add_argument("--model-file")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        markdown = Path(args.file).read_text(encoding="utf-8")
        ledger = json.loads(Path(args.ledger_file).read_text(encoding="utf-8"))
        template = load_template(args.template_file) if args.template_file else None
        model = (
            json.loads(Path(args.model_file).read_text(encoding="utf-8"))
            if args.model_file
            else None
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    result = validate(markdown, args.profile, ledger, template)
    if model is not None:
        model_result = validate_model_coverage(model, ledger)
        result["errors"].extend(model_result["errors"])
        result["ok"] = not result["errors"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
