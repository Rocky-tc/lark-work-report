#!/usr/bin/env python3
"""Validate report structure, evidence anchors, coverage, and required ledgers."""

import argparse
import json
import re
import sys
from pathlib import Path

from contracts import PROFILE_SECTIONS, STATUS_LABELS
from source_refs import SOURCE_TARGET, is_valid_source_ref
from template_profiles import field_labels, load as load_template, section_settings
from value_contracts import parse_aware_datetime as parse_time_contract


WORK_SOURCE_LINK = re.compile(rf"\[工作来源\]\(({SOURCE_TARGET})\)")
UNCERTAIN_SOURCE_LINK = re.compile(rf"\[待复核来源\]\(({SOURCE_TARGET})\)")
SOURCE_DEFINITION = re.compile(
    rf"^\[((?:W|U)\d+)\]:\s*({SOURCE_TARGET})\s*$",
    re.MULTILINE,
)
SOURCE_MARKER = re.compile(r"\[((?:W|U)\d+)\]\[\1\]")
EMPTY_ITEM = re.compile(r"^(?:-\s*)?(无|暂无|未发现|证据不足)(?:[。；;.]|$)")
COVERAGE_NOTICE = re.compile(
    r"^>\s*覆盖说明[：:]\s*(.+?)\s*$",
    re.MULTILINE,
)
FORBIDDEN_CLASS = re.compile(
    r"\b(?:private|chatter)\b|私人材料|私人标题|私人摘要|闲聊材料|闲聊标题|闲聊摘要",
    re.IGNORECASE,
)
MODEL_SECTION_KINDS = {
    "summary": "work",
    "workstreams": "work",
    "risks": "work",
    "next_actions": "work",
    "uncertain": "uncertain",
}
FIELD_OBLIGATIONS = (
    ("output", (("summary", "result"), ("workstreams", "result"))),
    ("status", (("workstreams", "status"),)),
    ("decision", (("summary", "decision"), ("workstreams", "decision"))),
    (
        "impact",
        (
            ("summary", "impact"),
            ("workstreams", "impact"),
            ("risks", "impact"),
        ),
    ),
    ("risk", (("risks", "risk"),)),
    ("next_action", (("next_actions", "action"),)),
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
        elif COVERAGE_NOTICE.match(raw_line) or SOURCE_DEFINITION.match(raw_line):
            current = None
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


def has_text(value):
    return isinstance(value, str) and bool(value.strip())


def model_section_items(model):
    result = {section: [] for section in MODEL_SECTION_KINDS}
    if not isinstance(model, dict):
        return result
    for section in result:
        values = model.get(section)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            evidence_ids = item.get("evidence_ids")
            normalized = (
                [value.strip() for value in evidence_ids if has_text(value)]
                if isinstance(evidence_ids, list)
                else []
            )
            result[section].append((item, normalized))
    return result


def obligation_required(field, value):
    if field == "status":
        return value in STATUS_LABELS
    return has_text(value)


def obligation_satisfied(cluster_id, field, value, paths, section_items):
    for section, model_field in paths:
        for item, evidence_ids in section_items[section]:
            if cluster_id not in evidence_ids:
                continue
            if field == "status":
                if item.get(model_field) == value:
                    return True
            elif has_text(item.get(model_field)):
                return True
    return False


def obligation_token(cluster_id, field, value, paths):
    subject = (
        f"{cluster_id}.{field}={value}"
        if field == "status"
        else f"{cluster_id}.{field}"
    )
    targets = "|".join(f"{section}.{model_field}" for section, model_field in paths)
    return f"{subject}->{targets}"


def field_obligation_evaluation(model, ledger):
    """Return deterministic field/section coverage without inspecting prose wording."""
    section_items = model_section_items(model)
    required = []
    satisfied = []
    missing = []
    failures = []
    work_items = ledger.get("work") if isinstance(ledger, dict) else None
    if isinstance(work_items, list):
        for item in work_items:
            if not isinstance(item, dict) or not has_text(item.get("cluster_id")):
                continue
            cluster_id = item["cluster_id"].strip()
            for field, paths in FIELD_OBLIGATIONS:
                value = item.get(field)
                if not obligation_required(field, value):
                    continue
                token = obligation_token(cluster_id, field, value, paths)
                required.append(token)
                if obligation_satisfied(
                    cluster_id,
                    field,
                    value,
                    paths,
                    section_items,
                ):
                    satisfied.append(token)
                else:
                    missing.append(token)
                    failures.append(
                        {
                            "cluster_id": cluster_id,
                            "field": field,
                            "value": value,
                            "paths": paths,
                        }
                    )
    return {
        "required": sorted(required),
        "satisfied": sorted(satisfied),
        "missing": sorted(missing),
        "section_evidence": {
            section: [evidence_ids for _, evidence_ids in items]
            for section, items in section_items.items()
        },
        "failures": failures,
    }


def field_obligation_signature(model, ledger):
    evaluation = field_obligation_evaluation(model, ledger)
    return {
        key: evaluation[key]
        for key in ("required", "satisfied", "missing", "section_evidence")
    }


def obligation_error(failure):
    cluster_id = failure["cluster_id"]
    field = failure["field"]
    value = failure["value"]
    if field == "output":
        target = "summary.result 或 workstreams.result"
    elif field == "status":
        target = "workstreams.status"
    elif field == "decision":
        target = "summary.decision 或 workstreams.decision"
    elif field == "impact":
        target = "summary.impact、workstreams.impact 或 risks.impact"
    elif field == "risk":
        target = "risks.risk"
    else:
        target = "next_actions.action"
    subject = (
        f"{cluster_id}.{field}={value}"
        if field == "status"
        else f"{cluster_id}.{field}"
    )
    return f"报告模型字段未覆盖：{subject} 必须出现在 {target}"


def parse_source_registry(markdown, errors):
    definitions = {}
    targets = {}
    for label, source_ref in SOURCE_DEFINITION.findall(markdown):
        if label in definitions:
            errors.append(f"来源短引用重复定义：{label}")
            continue
        if source_ref in targets:
            errors.append(
                f"同一来源不能使用多个短引用：{targets[source_ref]}、{label}"
            )
            continue
        definitions[label] = source_ref
        targets[source_ref] = label
    markers = set(SOURCE_MARKER.findall(markdown))
    for label in sorted(markers - set(definitions)):
        errors.append(f"未定义的来源短引用：{label}")
    for label in sorted(set(definitions) - markers):
        errors.append(f"来源短引用未被使用：{label}")
    return definitions, markers


def line_has_typed_source(line, kind, definitions):
    direct = (
        WORK_SOURCE_LINK.search(line)
        if kind == "work"
        else UNCERTAIN_SOURCE_LINK.search(line)
    )
    prefix = "W" if kind == "work" else "U"
    referenced = any(
        label.startswith(prefix) and label in definitions
        for label in SOURCE_MARKER.findall(line)
    )
    return bool(direct or referenced)


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
    if not isinstance(model, dict):
        return {"ok": True, "errors": []}
    coverage = model.get("coverage")
    if not isinstance(coverage, dict):
        errors.append("报告模型缺少 coverage 对象")
    else:
        start = parse_aware_datetime(
            coverage.get("start"),
            "时间窗起点",
            errors,
        )
        end = parse_aware_datetime(
            coverage.get("end"),
            "时间窗终点",
            errors,
        )
        snapshot = parse_aware_datetime(
            coverage.get("snapshot"),
            "快照时间",
            errors,
        )
        if start and end and start >= end:
            errors.append("时间窗起点必须早于终点")
        if end and snapshot and end > snapshot:
            errors.append("时间窗终点不得晚于快照时间")
        for field in ("domains", "access_gaps"):
            values = coverage.get(field)
            if not isinstance(values, list) or not all(
                isinstance(value, str) and value.strip()
                for value in values
            ):
                errors.append(f"coverage.{field} 必须是字符串数组")
        for name, field in (
            ("work", "work_count"),
            ("uncertain", "uncertain_count"),
        ):
            count = coverage.get(field)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                errors.append(f"coverage.{field} 必须是非负整数")
                continue
            items = ledger.get(name) if isinstance(ledger, dict) else None
            if isinstance(items, list) and len(items) != count:
                errors.append(
                    f"分类计数与证据账本不一致：{name}={count}，"
                    f"ledger={len(items)}"
                )

    if model.get("schema_version") not in {3, 4}:
        return {"ok": not errors, "errors": errors}
    if not isinstance(ledger, dict) or ledger.get("schema_version") != 2:
        errors.append("报告模型 v3/v4 要求证据账本 schema_version=2")
        return {"ok": False, "errors": errors}

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
    for section, kind in MODEL_SECTION_KINDS.items():
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
    obligation_evaluation = field_obligation_evaluation(model, ledger)
    errors.extend(
        obligation_error(failure)
        for failure in obligation_evaluation["failures"]
    )
    return {"ok": not errors, "errors": errors}


def validate(markdown, profile, ledger, template=None, model=None):
    errors = []
    warnings = []
    sections, headings = split_sections(markdown)
    settings = section_settings(template, profile)
    required = tuple(setting["label"] for setting in settings)
    evidence_sections = set(required[:3])
    reason_label = field_labels(template)["reason"]
    source_definitions, source_markers = parse_source_registry(markdown, errors)

    if not re.search(r"^#\s+\S+", markdown, re.MULTILINE):
        errors.append("缺少一级标题")
    if "来源与覆盖" in headings:
        errors.append("不得输出独立“来源与覆盖”章节")

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
            if not line_has_typed_source(line, "work", source_definitions):
                errors.append(f"章节“{name}”的事实条目缺少工作来源锚：{line}")

    uncertain_name = required[4]
    for raw_line in sections.get(uncertain_name, []):
        line = raw_line.strip()
        if not line or line.startswith(("###", "```", "|")) or EMPTY_ITEM.match(line):
            continue
        if reason_label not in line:
            errors.append(f"章节“{uncertain_name}”的候选缺少待复核原因：{line}")
        if not line_has_typed_source(line, "uncertain", source_definitions):
            errors.append(f"章节“{uncertain_name}”的候选缺少待复核来源锚：{line}")

    coverage_counts = {
        name: len(ledger.get(name, []))
        if isinstance(ledger, dict) and isinstance(ledger.get(name), list)
        else 0
        for name in ("work", "uncertain")
    }
    notices = COVERAGE_NOTICE.findall(markdown)
    if len(notices) > 1:
        errors.append("覆盖说明最多只能出现一次")
    if isinstance(model, dict) and isinstance(model.get("coverage"), dict):
        model_coverage = model["coverage"]
        domains = model_coverage.get("domains")
        gaps = model_coverage.get("access_gaps")
        requires_notice = (
            isinstance(gaps, list)
            and bool(gaps)
        ) or (
            isinstance(domains, list)
            and not domains
        )
        if requires_notice and not notices:
            errors.append("存在覆盖缺口时必须显示覆盖说明")
        if not requires_notice and notices:
            errors.append("没有覆盖缺口时不应显示覆盖说明")
        if notices and isinstance(gaps, list):
            for gap in gaps:
                if isinstance(gap, str) and gap not in notices[0]:
                    errors.append(f"覆盖说明遗漏访问缺口：{gap}")
        if notices and isinstance(domains, list):
            for domain in domains:
                if isinstance(domain, str) and domain not in notices[0]:
                    errors.append(f"覆盖说明遗漏已覆盖数据源：{domain}")
    work_sources = set(WORK_SOURCE_LINK.findall(markdown))
    uncertain_sources = set(UNCERTAIN_SOURCE_LINK.findall(markdown))
    work_sources.update(
        source_definitions[label]
        for label in source_markers
        if label.startswith("W") and label in source_definitions
    )
    uncertain_sources.update(
        source_definitions[label]
        for label in source_markers
        if label.startswith("U") and label in source_definitions
    )
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
    visible_text = SOURCE_DEFINITION.sub("", visible_text)
    if FORBIDDEN_CLASS.search(visible_text):
        errors.append("报告只能出现 work 和 uncertain 两类内容")

    if not work_sources:
        warnings.append("报告没有任何可核验工作来源锚")

    ledger_refs = validate_ledger(ledger, None, errors)
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
    result = validate(markdown, args.profile, ledger, template, model)
    if model is not None:
        model_result = validate_model_coverage(model, ledger)
        result["errors"].extend(model_result["errors"])
        result["ok"] = not result["errors"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
