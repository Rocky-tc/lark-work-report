#!/usr/bin/env python3
"""Render a compact six-section work report from a structured JSON model."""

import argparse
import json
import sys
from pathlib import Path

from contracts import (
    ACTION_KINDS,
    ASSIGNEE_RELATIONS,
    PROFILE_SECTIONS,
    STATUS_LABELS,
)
from source_refs import is_valid_source_ref
from template_profiles import (
    field_labels as resolved_field_labels,
    load as load_template,
    render_title,
    section_settings,
    validate as validate_template,
    workstream_layout as resolved_workstream_layout,
)
from value_contracts import (
    optional_text as optional_text_value,
    parse_aware_datetime as parse_time,
    require_text,
)


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
V2_EVIDENCE_FIELDS = {"source_refs", "priority_basis"}
V2_NEXT_ACTION_FIELDS = {
    "source_ref",
    "source_refs",
    "priority_basis",
    "due_at",
    "starts_at",
    "ends_at",
    "requires_response",
    "action_kind",
    "assignee_relation",
}
V3_EVIDENCE_FIELDS = {"evidence_ids"}
SECTION_FIELDS = {
    "summary": (SUMMARY_FIELDS, V2_EVIDENCE_FIELDS),
    "workstreams": (WORKSTREAM_FIELDS, V2_EVIDENCE_FIELDS),
    "risks": (RISK_FIELDS, V2_EVIDENCE_FIELDS),
    "next_actions": (NEXT_ACTION_FIELDS, V2_NEXT_ACTION_FIELDS),
    "uncertain": (UNCERTAIN_FIELDS, V2_EVIDENCE_FIELDS),
}
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


def optional_text(item, field, label):
    return optional_text_value(item.get(field), f"{label}.{field}")


def normalize_report_model(payload):
    payload = require_object(payload, "report model")
    reject_unknown_fields(payload, MODEL_FIELDS, "report model")
    schema_version = payload.get("schema_version")
    if schema_version not in {1, 2, 3}:
        raise ValueError("schema_version must be 1, 2, or 3")

    normalized = dict(payload)
    normalized["schema_version"] = 3 if schema_version == 3 else 2
    for section, (legacy_fields, v2_fields) in SECTION_FIELDS.items():
        items = require_array(payload.get(section), section)
        normalized_items = []
        allowed = (
            legacy_fields
            | (v2_fields if schema_version >= 2 else set())
            | (V3_EVIDENCE_FIELDS if schema_version == 3 else set())
        )
        for index, item in enumerate(items):
            label = f"{section}[{index}]"
            item = require_object(item, label)
            reject_unknown_fields(item, allowed, label)
            item = dict(item)
            if (
                schema_version == 1
                and section != "next_actions"
                and "source_ref" in item
            ):
                item["source_refs"] = [item.pop("source_ref")]
            if schema_version == 3:
                evidence_ids = item.get("evidence_ids")
                if (
                    not isinstance(evidence_ids, list)
                    or not evidence_ids
                    or not all(
                        isinstance(value, str) and value.strip()
                        for value in evidence_ids
                    )
                ):
                    raise ValueError(
                        f"{label}.evidence_ids must be a non-empty string array"
                    )
                normalized_ids = [" ".join(value.split()) for value in evidence_ids]
                if len(normalized_ids) != len(set(normalized_ids)):
                    raise ValueError(f"{label}.evidence_ids must not contain duplicates")
                item["evidence_ids"] = normalized_ids
            normalized_items.append(item)
        normalized[section] = normalized_items
    return normalized


def markdown_text(value, label):
    text = require_text(value, label)
    for token in ("\\", "*", "_", "`", "[", "]", "<", ">"):
        text = text.replace(token, f"\\{token}")
    return text


def source_links(item, label, link_label, *, required=True):
    values = []
    source_ref = item.get("source_ref")
    if source_ref is not None:
        values.append(require_text(source_ref, f"{label}.source_ref"))
    source_refs = item.get("source_refs", [])
    if not isinstance(source_refs, list):
        raise ValueError(f"{label}.source_refs must be an array")
    for index, value in enumerate(source_refs):
        values.append(require_text(value, f"{label}.source_refs[{index}]"))
    values = list(dict.fromkeys(values))
    if required and not values:
        raise ValueError(f"{label} requires source_ref or source_refs")
    for value in values:
        if not is_valid_source_ref(value):
            raise ValueError(
                f"{label}.source_refs must use http(s):// or source://"
            )
    return "".join(f"[{link_label}]({value})" for value in values)


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


def format_items(items, style):
    if not items:
        return ["- 无"]
    if style == "bullet":
        return [f"- {item}" for item in items]
    if style == "numbered":
        return [f"{index}. {item}" for index, item in enumerate(items, start=1)]
    if style == "paragraph":
        lines = []
        for index, item in enumerate(items):
            if index:
                lines.append("")
            lines.append(item)
        return lines
    raise ValueError(f"unsupported item style: {style}")


def render_summary(items, style="bullet", labels=None):
    contents = []
    labels = labels or resolved_field_labels(None)
    for index, item in enumerate(ordered_items(items, "summary")):
        result = markdown_text(item.get("result"), f"summary[{index}].result")
        impact = optional_text(item, "impact", f"summary[{index}]")
        decision = optional_text(item, "decision", f"summary[{index}]")
        parts = [result]
        if impact:
            parts.append(
                f"{labels['impact']}：{markdown_text(impact, 'summary impact')}"
            )
        if decision:
            parts.append(
                f"{labels['decision']}：{markdown_text(decision, 'summary decision')}"
            )
        priority_basis = optional_text(item, "priority_basis", f"summary[{index}]")
        if priority_basis:
            parts.append(
                f"排序依据：{markdown_text(priority_basis, 'summary priority basis')}"
            )
        link = source_links(
            item,
            f"summary[{index}]",
            "工作来源",
        )
        contents.append(f"{sentence(parts)}{link}")
    return format_items(contents, style)


def render_workstreams(
    items,
    style="bullet",
    labels=None,
    layout="inline",
):
    contents = []
    labels = labels or resolved_field_labels(None)
    for index, item in enumerate(ordered_items(items, "workstreams")):
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
            parts.append(
                f"{labels['impact']}：{markdown_text(impact, 'workstream impact')}"
            )
        if decision:
            parts.append(
                f"{labels['decision']}："
                f"{markdown_text(decision, 'workstream decision')}"
            )
        if progress:
            parts.append(
                f"{labels['progress']}："
                f"{markdown_text(progress, 'workstream progress')}"
            )
        priority_basis = optional_text(
            item,
            "priority_basis",
            f"workstreams[{index}]",
        )
        if priority_basis:
            parts.append(
                "排序依据："
                f"{markdown_text(priority_basis, 'workstream priority basis')}"
            )
        link = source_links(
            item,
            f"workstreams[{index}]",
            "工作来源",
        )
        heading = f"{name}｜{STATUS_LABELS[status]}"
        if layout == "inline":
            contents.append(f"**{heading}**：{sentence(parts)}{link}")
        else:
            body = format_items([f"{sentence(parts)}{link}"], style)
            if contents:
                contents.append("")
            contents.append(f"### {heading}")
            contents.extend(body)
    if layout == "subsection":
        return contents or ["- 无"]
    return format_items(contents, style)


def render_risks(items, style="bullet", labels=None):
    contents = []
    labels = labels or resolved_field_labels(None)
    for index, item in enumerate(ordered_items(items, "risks")):
        risk = markdown_text(item.get("risk"), f"risks[{index}].risk")
        impact = optional_text(item, "impact", f"risks[{index}]")
        assistance = optional_text(item, "assistance", f"risks[{index}]")
        parts = [risk]
        if impact:
            parts.append(
                f"{labels['impact']}：{markdown_text(impact, 'risk impact')}"
            )
        if assistance:
            parts.append(
                f"{labels['assistance']}："
                f"{markdown_text(assistance, 'risk assistance')}"
            )
        priority_basis = optional_text(item, "priority_basis", f"risks[{index}]")
        if priority_basis:
            parts.append(
                f"排序依据：{markdown_text(priority_basis, 'risk priority basis')}"
            )
        link = source_links(
            item,
            f"risks[{index}]",
            "工作来源",
        )
        contents.append(f"{sentence(parts)}{link}")
    return format_items(contents, style)


def render_next_actions(items, style="bullet", labels=None):
    contents = []
    labels = labels or resolved_field_labels(None)
    for index, item in enumerate(ordered_items(items, "next_actions")):
        action = markdown_text(item.get("action"), f"next_actions[{index}].action")
        purpose = optional_text(item, "purpose", f"next_actions[{index}]")
        parts = [action]
        if purpose:
            parts.append(
                f"{labels['purpose']}："
                f"{markdown_text(purpose, 'next action purpose')}"
            )
        action_kind = item.get("action_kind")
        if action_kind is not None:
            if action_kind not in ACTION_KINDS:
                raise ValueError(f"next_actions[{index}].action_kind is invalid")
            action_labels = {
                "reply": "回复",
                "prepare": "准备",
                "review": "评审",
                "deliver": "交付",
                "follow_up": "跟进",
                "attend": "参会",
            }
            parts.append(f"行动类型：{action_labels[action_kind]}")
        due_at = item.get("due_at")
        if due_at is not None:
            due_text, _ = parse_time(due_at, f"next_actions[{index}].due_at")
            parts.append(f"截止时间：{due_text}")
        starts_at = item.get("starts_at")
        ends_at = item.get("ends_at")
        start = end = None
        if starts_at is not None:
            start_text, start = parse_time(
                starts_at,
                f"next_actions[{index}].starts_at",
            )
            parts.append(f"开始时间：{start_text}")
        if ends_at is not None:
            end_text, end = parse_time(
                ends_at,
                f"next_actions[{index}].ends_at",
            )
            parts.append(f"结束时间：{end_text}")
        if start and end and start >= end:
            raise ValueError(
                f"next_actions[{index}].starts_at must be before ends_at"
            )
        requires_response = item.get("requires_response")
        if requires_response is not None:
            if not isinstance(requires_response, bool):
                raise ValueError(
                    f"next_actions[{index}].requires_response must be boolean"
                )
            if requires_response:
                parts.append("需要回复：是")
        assignee_relation = item.get("assignee_relation")
        if assignee_relation is not None:
            if assignee_relation not in ASSIGNEE_RELATIONS:
                raise ValueError(
                    f"next_actions[{index}].assignee_relation is invalid"
                )
            assignee_labels = {
                "self": "当前主体",
                "shared": "共同负责",
                "unknown": "待确认",
            }
            parts.append(f"责任关系：{assignee_labels[assignee_relation]}")
        priority_basis = optional_text(
            item,
            "priority_basis",
            f"next_actions[{index}]",
        )
        if priority_basis:
            parts.append(
                "排序依据："
                f"{markdown_text(priority_basis, 'next action priority basis')}"
            )
        factual = any(
            field in item
            for field in (
                "action_kind",
                "due_at",
                "starts_at",
                "ends_at",
                "requires_response",
                "assignee_relation",
            )
        )
        link = source_links(
            item,
            f"next_actions[{index}]",
            "工作来源",
            required=factual,
        )
        contents.append(f"{sentence(parts)}{link}")
    return format_items(contents, style)


def render_uncertain(items, style="bullet", labels=None):
    contents = []
    labels = labels or resolved_field_labels(None)
    for index, item in enumerate(ordered_items(items, "uncertain")):
        description = markdown_text(
            item.get("description"),
            f"uncertain[{index}].description",
        )
        reason = markdown_text(item.get("reason"), f"uncertain[{index}].reason")
        link = source_links(
            item,
            f"uncertain[{index}]",
            "待复核来源",
        )
        priority_basis = optional_text(
            item,
            "priority_basis",
            f"uncertain[{index}]",
        )
        parts = [description, f"{labels['reason']}：{reason}"]
        if priority_basis:
            parts.append(
                "排序依据："
                f"{markdown_text(priority_basis, 'uncertain priority basis')}"
            )
        contents.append(f"{sentence(parts)}{link}")
    return format_items(contents, style)


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


def render(payload, template=None, context=None):
    payload = normalize_report_model(payload)
    profile = payload.get("profile")
    if profile not in PROFILE_SECTIONS:
        raise ValueError(f"profile must be one of {', '.join(PROFILE_SECTIONS)}")
    if template is not None:
        template = validate_template(template)
    fallback_title = markdown_text(payload.get("title"), "title")
    title = markdown_text(
        render_title(template, fallback_title, context),
        "rendered title",
    )
    settings = section_settings(template, profile)
    sections = [setting["label"] for setting in settings]
    styles = [setting["item_style"] for setting in settings]
    labels = resolved_field_labels(template)
    layout = resolved_workstream_layout(template)
    bodies = (
        render_summary(
            payload.get("summary"),
            styles[0],
            labels,
        ),
        render_workstreams(
            payload.get("workstreams"),
            styles[1],
            labels,
            layout,
        ),
        render_risks(payload.get("risks"), styles[2], labels),
        render_next_actions(
            payload.get("next_actions"),
            styles[3],
            labels,
        ),
        render_uncertain(
            payload.get("uncertain"),
            styles[4],
            labels,
        ),
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
    parser.add_argument("--template-file")
    parser.add_argument("--context-file")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        source_path = Path(args.file)
        output_path = Path(args.output) if args.output else None
        if output_path and source_path.resolve() == output_path.resolve():
            raise ValueError("output path must differ from report model path")
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        template = load_template(args.template_file) if args.template_file else None
        context = (
            json.loads(Path(args.context_file).read_text(encoding="utf-8"))
            if args.context_file
            else None
        )
        markdown = render(payload, template, context)
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
