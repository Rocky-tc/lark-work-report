"""Hydrate deterministic evidence facts into compact report models v4/v5."""

import hashlib
import json
import re

from contracts import PROFILE_PERIOD_LABELS, SOURCE_RANK, STATUS_LABELS
from value_contracts import require_text


SECTION_KINDS = {
    "summary": "work",
    "workstreams": "work",
    "risks": "work",
    "next_actions": "work",
    "uncertain": "uncertain",
}
V4_FIELDS = {
    "summary": {"result", "impact", "decision", "evidence_ids"},
    "workstreams": {
        "name",
        "status",
        "result",
        "impact",
        "decision",
        "progress",
        "evidence_ids",
    },
    "risks": {"risk", "impact", "assistance", "evidence_ids"},
    "next_actions": {"action", "purpose", "evidence_ids"},
    "uncertain": {"description", "reason", "evidence_ids"},
}
ACTION_FACTS = (
    "action_kind",
    "starts_at",
    "ends_at",
    "due_at",
    "assignee_relation",
)
V5_MODEL_FIELDS = {
    "schema_version",
    "ledger_fingerprint",
    *SECTION_KINDS,
}
V5_FIELDS = {
    section: (fields - {"evidence_ids"}) | {"evidence_refs"}
    for section, fields in V4_FIELDS.items()
}
REQUIRED_NARRATIVE_FIELDS = {
    "summary": ("result",),
    "workstreams": ("name", "status", "result"),
    "risks": ("risk",),
    "next_actions": ("action",),
    "uncertain": ("description", "reason"),
}
EVIDENCE_REF = re.compile(r"^([wu])([0-9]+)$")
SOURCE_LABELS = {
    "report_cache": "历史报告",
    "tasks": "任务",
    "calendar": "日历",
    "vc": "视频会议",
    "minutes": "妙记",
    "mentions": "@提及",
    "comments": "文档评论",
    "docs": "文档",
    "wiki": "知识库",
    "base": "多维表格",
    "im": "消息",
    "mail": "邮件",
    "okr": "OKR",
    "approval": "审批",
    "code_activity": "代码活动",
    "ai_sessions": "AI会话",
}


def has_text(item, field):
    value = item.get(field)
    return isinstance(value, str) and bool(value.strip())


def direct_sections(item, kind):
    """Return report sections that can safely reuse one ledger item verbatim."""
    if not isinstance(item, dict) or item.get("status_conflict") is not False:
        return []
    record_ids = item.get("record_ids")
    source_refs = item.get("source_refs")
    if (
        not isinstance(record_ids, list)
        or len(record_ids) != 1
        or not isinstance(source_refs, list)
        or len(source_refs) != 1
    ):
        return []
    if kind == "uncertain":
        if (
            (has_text(item, "title") or has_text(item, "activity"))
            and has_text(item, "classification_reason")
        ):
            return ["uncertain"]
        return []
    if kind != "work":
        raise ValueError("direct fill kind must be work or uncertain")
    result = []
    if has_text(item, "output"):
        result.append("summary")
        if (
            has_text(item, "workstream")
            and item.get("status") in STATUS_LABELS
        ):
            result.append("workstreams")
    if has_text(item, "risk"):
        result.append("risks")
    if has_text(item, "next_action"):
        result.append("next_actions")
    return result


def direct_item(section, record):
    if section == "summary":
        result = {"result": record["output"]}
        for field in ("impact", "decision"):
            if has_text(record, field):
                result[field] = record[field]
        return result
    if section == "workstreams":
        result = {
            "name": record["workstream"],
            "status": record["status"],
            "result": record["output"],
        }
        for field in ("impact", "decision"):
            if has_text(record, field):
                result[field] = record[field]
        if (
            has_text(record, "activity")
            and record["activity"] != record["output"]
        ):
            result["progress"] = record["activity"]
        return result
    if section == "risks":
        result = {"risk": record["risk"]}
        if has_text(record, "impact"):
            result["impact"] = record["impact"]
        return result
    if section == "next_actions":
        return {"action": record["next_action"]}
    if section == "uncertain":
        return {
            "description": (
                record["title"]
                if has_text(record, "title")
                else record["activity"]
            ),
            "reason": record["classification_reason"],
        }
    raise ValueError(f"unsupported direct fill section: {section}")


def canonical_digest(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def ledger_fingerprint(ledger):
    if not isinstance(ledger, dict) or ledger.get("schema_version") != 2:
        raise ValueError("evidence ledger schema_version must be 2")
    return canonical_digest(ledger)


def ledger_index(ledger):
    if not isinstance(ledger, dict) or ledger.get("schema_version") != 2:
        raise ValueError("report model v4 requires evidence ledger schema_version=2")
    result = {"work": {}, "uncertain": {}}
    all_ids = set()
    for kind in result:
        items = ledger.get(kind)
        if not isinstance(items, list):
            raise ValueError(f"evidence ledger requires a {kind} array")
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"evidence ledger {kind}[{index}] must be an object")
            cluster_id = require_text(
                item.get("cluster_id"),
                f"evidence ledger {kind}[{index}].cluster_id",
            )
            if cluster_id in all_ids:
                raise ValueError(f"evidence ledger duplicates cluster_id: {cluster_id}")
            all_ids.add(cluster_id)
            result[kind][cluster_id] = item
    return result


def evidence_items(item, label, expected, other):
    values = item.get("evidence_ids")
    if (
        not isinstance(values, list)
        or not values
        or not all(isinstance(value, str) and value.strip() for value in values)
    ):
        raise ValueError(f"{label}.evidence_ids must be a non-empty string array")
    ids = [" ".join(value.split()) for value in values]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{label}.evidence_ids must not contain duplicates")
    selected = []
    for cluster_id in ids:
        if cluster_id not in expected:
            if cluster_id in other:
                raise ValueError(f"{label} references the wrong ledger partition")
            raise ValueError(f"{label} references unknown evidence: {cluster_id}")
        selected.append(expected[cluster_id])
    return ids, selected


def ordered_sources(records):
    values = []
    for record in records:
        source_ref = record.get("source_ref")
        if source_ref is not None:
            values.append(source_ref)
        source_refs = record.get("source_refs", [])
        if not isinstance(source_refs, list):
            raise ValueError("ledger source_refs must be an array")
        values.extend(source_refs)
    return list(dict.fromkeys(values))


def resolved_priority_basis(records):
    values = []
    for record in records:
        basis = record.get("priority_basis", [])
        if isinstance(basis, str):
            basis = [basis]
        if not isinstance(basis, list):
            raise ValueError("ledger priority_basis must be a string or array")
        values.extend(value for value in basis if isinstance(value, str) and value)
    values = list(dict.fromkeys(values))
    return "、".join(values) if values else None


def consensus(records, field):
    values = []
    for record in records:
        value = record.get(field)
        if value is not None and value not in values:
            values.append(value)
    return values[0] if len(values) == 1 else None


def identity_subject(identity):
    if not isinstance(identity, dict):
        return None
    return next(
        (
            " ".join(identity[field].split())
            for field in ("display_name", "name", "subject", "user_name")
            if isinstance(identity.get(field), str) and identity[field].strip()
        ),
        None,
    )


def deterministic_top_level(plan, identity, audit, ledger):
    if not isinstance(plan, dict):
        raise ValueError("report model v5 requires a run plan")
    period = plan.get("period")
    if not isinstance(period, dict):
        raise ValueError("report model v5 requires run plan period context")
    profile = period.get("routed_profile")
    if profile not in PROFILE_PERIOD_LABELS:
        raise ValueError("report model v5 run plan has an invalid routed profile")
    period_range = require_text(period.get("title_period"), "period.title_period")
    subject = identity_subject(identity)
    title = (
        f"{subject}{PROFILE_PERIOD_LABELS[profile]}｜{period_range}"
        if subject
        else f"{PROFILE_PERIOD_LABELS[profile]}｜{period_range}"
    )

    collected = plan.get("collected_domains")
    if isinstance(collected, list):
        source_types = list(
            dict.fromkeys(
                value
                for value in collected
                if value in SOURCE_RANK
            )
        )
    else:
        source_types = []
    if not source_types:
        source_types = sorted(
            {
                source_type
                for partition in ("work", "uncertain")
                for item in ledger.get(partition, [])
                for source_type in item.get("source_types", [])
                if source_type in SOURCE_RANK
            },
            key=SOURCE_RANK.__getitem__,
        )
    access_gaps = []
    if isinstance(audit, dict):
        for index, gap in enumerate(audit.get("access_gaps", [])):
            if not isinstance(gap, dict):
                raise ValueError(f"extraction audit access_gaps[{index}] is invalid")
            reason = require_text(
                gap.get("reason"),
                f"extraction audit access_gaps[{index}].reason",
            )
            source_ref = gap.get("source_ref")
            access_gaps.append(
                f"{reason}（{source_ref}）"
                if isinstance(source_ref, str) and source_ref
                else reason
            )
    snapshot = (
        period.get("snapshot")
        or period.get("snapshot_time")
        or period.get("end")
    )
    return {
        "profile": profile,
        "title": title,
        "coverage": {
            "start": require_text(period.get("start"), "period.start"),
            "end": require_text(period.get("end"), "period.end"),
            "snapshot": require_text(snapshot, "period.snapshot"),
            "domains": [SOURCE_LABELS[value] for value in source_types],
            "access_gaps": access_gaps,
            "work_count": len(ledger.get("work", [])),
            "uncertain_count": len(ledger.get("uncertain", [])),
        },
    }


def expand_v5(
    model,
    ledger,
    plan,
    identity,
    audit,
    *,
    allow_direct_fill=True,
):
    unknown = sorted(set(model) - V5_MODEL_FIELDS)
    if unknown:
        raise ValueError(
            "report model v5 contains unknown fields: " + ", ".join(unknown)
        )
    expected_fingerprint = ledger_fingerprint(ledger)
    if model.get("ledger_fingerprint") != expected_fingerprint:
        raise ValueError("report model v5 ledger_fingerprint is stale or invalid")
    top = deterministic_top_level(plan, identity, audit, ledger)
    expanded = {
        "schema_version": 4,
        **top,
    }
    for section, kind in SECTION_KINDS.items():
        items = model.get(section)
        if not isinstance(items, list):
            raise ValueError(f"report model v5 requires a {section} array")
        prepared = []
        expected_prefix = "w" if kind == "work" else "u"
        partition = ledger.get(kind)
        if not isinstance(partition, list):
            raise ValueError(f"evidence ledger requires a {kind} array")
        for index, item in enumerate(items):
            label = f"{section}[{index}]"
            if not isinstance(item, dict):
                raise ValueError(f"{label} must be an object")
            unknown = sorted(set(item) - V5_FIELDS[section])
            if unknown:
                raise ValueError(
                    f"{label} contains unknown fields: {', '.join(unknown)}"
                )
            refs = item.get("evidence_refs")
            if not isinstance(refs, list) or not refs:
                raise ValueError(
                    f"{label}.evidence_refs must be a non-empty string array"
                )
            evidence_ids = []
            normalized_refs = []
            selected_records = []
            for ref_index, value in enumerate(refs):
                value = require_text(
                    value,
                    f"{label}.evidence_refs[{ref_index}]",
                )
                match = EVIDENCE_REF.fullmatch(value)
                if not match:
                    raise ValueError(f"{label} contains an invalid evidence_ref")
                if match.group(1) != expected_prefix:
                    raise ValueError(f"{label} references the wrong ledger partition")
                item_index = int(match.group(2))
                if item_index >= len(partition):
                    raise ValueError(f"{label} references unknown evidence: {value}")
                normalized_refs.append(value)
                selected_records.append(partition[item_index])
                evidence_ids.append(
                    require_text(
                        partition[item_index].get("cluster_id"),
                        f"ledger.{kind}[{item_index}].cluster_id",
                    )
                )
            if len(normalized_refs) != len(set(normalized_refs)):
                raise ValueError(f"{label}.evidence_refs must not contain duplicates")
            result = {
                key: value for key, value in item.items() if key != "evidence_refs"
            }
            direct_allowed = (
                allow_direct_fill
                and len(selected_records) == 1
                and section in direct_sections(selected_records[0], kind)
            )
            if not result:
                if not direct_allowed:
                    raise ValueError(
                        f"{label} requires model narrative for this evidence"
                    )
                result = direct_item(section, selected_records[0])
            elif direct_allowed:
                deterministic = direct_item(section, selected_records[0])
                for field in REQUIRED_NARRATIVE_FIELDS[section]:
                    if field not in result:
                        result[field] = deterministic[field]
            result["evidence_ids"] = evidence_ids
            prepared.append(result)
        expanded[section] = prepared
    return expanded


def hydrate(
    model,
    ledger,
    plan=None,
    identity=None,
    audit=None,
    *,
    allow_direct_fill=True,
):
    if isinstance(model, dict) and model.get("schema_version") == 5:
        model = expand_v5(
            model,
            ledger,
            plan,
            identity,
            audit,
            allow_direct_fill=allow_direct_fill,
        )
    if not isinstance(model, dict) or model.get("schema_version") != 4:
        return model
    index = ledger_index(ledger)
    hydrated = dict(model)
    hydrated["schema_version"] = 3
    for section, kind in SECTION_KINDS.items():
        items = model.get(section)
        if not isinstance(items, list):
            raise ValueError(f"report model requires a {section} array")
        prepared = []
        for item_index, item in enumerate(items):
            label = f"{section}[{item_index}]"
            if not isinstance(item, dict):
                raise ValueError(f"{label} must be an object")
            unknown = sorted(set(item) - V4_FIELDS[section])
            if unknown:
                raise ValueError(
                    f"{label} contains unknown fields: {', '.join(unknown)}"
                )
            ids, records = evidence_items(
                item,
                label,
                index[kind],
                index["uncertain" if kind == "work" else "work"],
            )
            result = dict(item)
            result["evidence_ids"] = ids
            result["priority"] = min(record.get("priority", 100) for record in records)
            sources = ordered_sources(records)
            if not sources:
                raise ValueError(f"{label} evidence has no source references")
            result["source_refs"] = sources
            basis = resolved_priority_basis(records)
            if basis:
                result["priority_basis"] = basis
            if section == "next_actions":
                for field in ACTION_FACTS:
                    value = consensus(records, field)
                    if value is not None:
                        result[field] = value
                if any(record.get("requires_response") is True for record in records):
                    result["requires_response"] = True
            prepared.append(result)
        hydrated[section] = prepared
    return hydrated
