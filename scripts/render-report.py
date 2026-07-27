#!/usr/bin/env python3
"""Render a compact six-section work report from a structured JSON model."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from contracts import PROFILE_SECTIONS, STATUS_LABELS
from source_refs import is_valid_source_ref


MODEL_FIELDS = {
    "schema_version",
    "profile",
    "title",
    "summary",
    "workstreams",
    "risks",
    "next_actions",
    "uncertain",
    "coverage",
}
SUMMARY_FIELDS = {"priority", "result", "impact", "decision", "source_ref"}
WORKSTREAM_FIELDS = {
    "priority",
    "name",
    "status",
    "result",
    "impact",
    "decision",
    "progress",
    "source_ref",
}
RISK_FIELDS = {"priority", "risk", "impact", "assistance", "source_ref"}
NEXT_ACTION_FIELDS = {"priority", "action", "purpose"}
UNCERTAIN_FIELDS = {"priority", "description", "reason", "source_ref"}
COVERAGE_FIELDS = {
    "start",
    "end",
    "snapshot",
    "domains",
    "access_gaps",
    "work_count",
    "uncertain_count",
}


def require_object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def require_array(value, label):
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def reject_unknown_fields(value, allowed, label):
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unknown fields: {', '.join(unknown)}")


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return " ".join(value.split())


def optional_text(item, field, label):
    value = item.get(field)
    if value is None:
        return None
    return require_text(value, f"{label}.{field}")


def markdown_text(value, label):
    text = require_text(value, label)
    for token in ("\\", "*", "_", "`", "[", "]", "<", ">"):
        text = text.replace(token, f"\\{token}")
    return text


def source_link(value, label, link_label):
    source_ref = require_text(value, label)
    if not is_valid_source_ref(source_ref):
        raise ValueError(f"{label} must use http(s):// or source://")
    return f"[{link_label}]({source_ref})"


def priority(item, label):
    value = item.get("priority", 100)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label}.priority must be a non-negative integer")
    return value


def ordered_items(value, label):
    items = require_array(value, label)
    prepared = []
    for index, item in enumerate(items):
        item = require_object(item, f"{label}[{index}]")
        prepared.append((priority(item, f"{label}[{index}]"), index, item))
    return [item for _, _, item in sorted(prepared)]


def sentence(parts):
    cleaned = [part.rstrip("。；; ") for part in parts if part]
    return "；".join(cleaned) + "。"


def render_summary(items):
    lines = []
    for index, item in enumerate(ordered_items(items, "summary")):
        reject_unknown_fields(item, SUMMARY_FIELDS, f"summary[{index}]")
        result = markdown_text(item.get("result"), f"summary[{index}].result")
        impact = optional_text(item, "impact", f"summary[{index}]")
        decision = optional_text(item, "decision", f"summary[{index}]")
        parts = [result]
        if impact:
            parts.append(f"影响：{markdown_text(impact, 'summary impact')}")
        if decision:
            parts.append(f"决策：{markdown_text(decision, 'summary decision')}")
        link = source_link(
            item.get("source_ref"),
            f"summary[{index}].source_ref",
            "工作来源",
        )
        lines.append(f"- {sentence(parts)}{link}")
    return lines or ["- 无"]


def render_workstreams(items):
    lines = []
    for index, item in enumerate(ordered_items(items, "workstreams")):
        reject_unknown_fields(
            item,
            WORKSTREAM_FIELDS,
            f"workstreams[{index}]",
        )
        name = markdown_text(item.get("name"), f"workstreams[{index}].name")
        status = item.get("status")
        if status not in STATUS_LABELS:
            raise ValueError(
                f"workstreams[{index}].status must be one of "
                f"{', '.join(STATUS_LABELS)}"
            )
        result = markdown_text(item.get("result"), f"workstreams[{index}].result")
        impact = optional_text(item, "impact", f"workstreams[{index}]")
        decision = optional_text(item, "decision", f"workstreams[{index}]")
        progress = optional_text(item, "progress", f"workstreams[{index}]")
        parts = [result]
        if impact:
            parts.append(f"影响：{markdown_text(impact, 'workstream impact')}")
        if decision:
            parts.append(f"决策：{markdown_text(decision, 'workstream decision')}")
        if progress:
            parts.append(f"进展：{markdown_text(progress, 'workstream progress')}")
        link = source_link(
            item.get("source_ref"),
            f"workstreams[{index}].source_ref",
            "工作来源",
        )
        lines.append(
            f"- **{name}｜{STATUS_LABELS[status]}**：{sentence(parts)}{link}"
        )
    return lines or ["- 无"]


def render_risks(items):
    lines = []
    for index, item in enumerate(ordered_items(items, "risks")):
        reject_unknown_fields(item, RISK_FIELDS, f"risks[{index}]")
        risk = markdown_text(item.get("risk"), f"risks[{index}].risk")
        impact = optional_text(item, "impact", f"risks[{index}]")
        assistance = optional_text(item, "assistance", f"risks[{index}]")
        parts = [risk]
        if impact:
            parts.append(f"影响：{markdown_text(impact, 'risk impact')}")
        if assistance:
            parts.append(f"需协助：{markdown_text(assistance, 'risk assistance')}")
        link = source_link(
            item.get("source_ref"),
            f"risks[{index}].source_ref",
            "工作来源",
        )
        lines.append(f"- {sentence(parts)}{link}")
    return lines or ["- 无"]


def render_next_actions(items):
    lines = []
    for index, item in enumerate(ordered_items(items, "next_actions")):
        reject_unknown_fields(
            item,
            NEXT_ACTION_FIELDS,
            f"next_actions[{index}]",
        )
        action = markdown_text(item.get("action"), f"next_actions[{index}].action")
        purpose = optional_text(item, "purpose", f"next_actions[{index}]")
        parts = [action]
        if purpose:
            parts.append(f"目标：{markdown_text(purpose, 'next action purpose')}")
        lines.append(f"- {sentence(parts)}")
    return lines or ["- 无"]


def render_uncertain(items):
    lines = []
    for index, item in enumerate(ordered_items(items, "uncertain")):
        reject_unknown_fields(item, UNCERTAIN_FIELDS, f"uncertain[{index}]")
        description = markdown_text(
            item.get("description"),
            f"uncertain[{index}].description",
        )
        reason = markdown_text(item.get("reason"), f"uncertain[{index}].reason")
        link = source_link(
            item.get("source_ref"),
            f"uncertain[{index}].source_ref",
            "待复核来源",
        )
        lines.append(f"- {description}；原因：{reason}。{link}")
    return lines or ["- 无"]


def parse_time(value, label):
    text = require_text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be timezone-aware ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset")
    return text, parsed


def render_coverage(value):
    coverage = require_object(value, "coverage")
    reject_unknown_fields(coverage, COVERAGE_FIELDS, "coverage")
    start_text, start = parse_time(coverage.get("start"), "coverage.start")
    end_text, end = parse_time(coverage.get("end"), "coverage.end")
    snapshot_text, snapshot = parse_time(
        coverage.get("snapshot"),
        "coverage.snapshot",
    )
    if start >= end:
        raise ValueError("coverage.start must be earlier than coverage.end")
    if end > snapshot:
        raise ValueError("coverage.end cannot be later than coverage.snapshot")

    domains = [
        require_text(item, f"coverage.domains[{index}]")
        for index, item in enumerate(
            require_array(coverage.get("domains"), "coverage.domains")
        )
    ]
    gaps = [
        require_text(item, f"coverage.access_gaps[{index}]")
        for index, item in enumerate(
            require_array(coverage.get("access_gaps"), "coverage.access_gaps")
        )
    ]
    work_count = coverage.get("work_count")
    uncertain_count = coverage.get("uncertain_count")
    for value, label in (
        (work_count, "coverage.work_count"),
        (uncertain_count, "coverage.uncertain_count"),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{label} must be a non-negative integer")

    return [
        f"- 时间窗：{start_text} 至 {end_text}",
        f"- 快照时间：{snapshot_text}",
        f"- 覆盖域：{'、'.join(domains) if domains else '无'}",
        f"- 权限缺口：{'；'.join(gaps) if gaps else '无'}",
        f"- 分类计数：work={work_count}，uncertain={uncertain_count}",
    ]


def render(payload):
    payload = require_object(payload, "report model")
    reject_unknown_fields(payload, MODEL_FIELDS, "report model")
    if payload.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    profile = payload.get("profile")
    if profile not in PROFILE_SECTIONS:
        raise ValueError(f"profile must be one of {', '.join(PROFILE_SECTIONS)}")
    title = markdown_text(payload.get("title"), "title")
    sections = PROFILE_SECTIONS[profile]
    bodies = (
        render_summary(payload.get("summary")),
        render_workstreams(payload.get("workstreams")),
        render_risks(payload.get("risks")),
        render_next_actions(payload.get("next_actions")),
        render_uncertain(payload.get("uncertain")),
        render_coverage(payload.get("coverage")),
    )
    blocks = [f"# {title}"]
    for heading, lines in zip(sections, bodies):
        blocks.append(f"## {heading}\n" + "\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="Structured report model JSON")
    parser.add_argument("--output", help="Write Markdown to this path instead of stdout")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        source_path = Path(args.file)
        output_path = Path(args.output) if args.output else None
        if output_path and source_path.resolve() == output_path.resolve():
            raise ValueError("output path must differ from report model path")
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        markdown = render(payload)
        if output_path:
            output_path.write_text(markdown, encoding="utf-8")
        else:
            print(markdown, end="")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
