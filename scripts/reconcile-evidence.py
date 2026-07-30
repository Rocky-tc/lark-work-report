#!/usr/bin/env python3
"""Normalize, conservatively merge, rank, and split verified evidence records."""

import argparse
import hashlib
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

from contracts import (
    ACTION_KINDS,
    ASSIGNEE_RELATIONS,
    EVIDENCE_CONFIDENCE,
    SIGNAL_KINDS,
    SOURCE_RANK,
    SOURCE_TYPES,
    STATUS_BASES,
    STATUS_LABELS,
)
from runtime_utils import emit_json, read_json, write_json_atomic
from source_refs import is_valid_source_ref
from value_contracts import (
    optional_text,
    parse_aware_datetime as parse_time,
    require_text,
)


RECORD_FIELDS = {
    "record_id",
    "source_type",
    "source_ref",
    "occurred_at",
    "title",
    "actor",
    "workstream",
    "activity",
    "status",
    "status_basis",
    "output",
    "impact",
    "decision",
    "risk",
    "next_action",
    "signal_kind",
    "action_kind",
    "starts_at",
    "ends_at",
    "due_at",
    "requires_response",
    "assignee_relation",
    "participants",
    "confidence",
    "work_relevance",
    "sensitivity",
    "classification_reason",
    "retention_mode",
}
REQUIRED_TEXT_FIELDS = (
    "record_id",
    "source_type",
    "source_ref",
    "occurred_at",
    "actor",
    "workstream",
    "activity",
    "status",
    "status_basis",
    "signal_kind",
    "confidence",
    "work_relevance",
    "sensitivity",
    "classification_reason",
    "retention_mode",
)
OPTIONAL_TEXT_FIELDS = (
    "title",
    "output",
    "impact",
    "decision",
    "risk",
    "next_action",
    "action_kind",
    "starts_at",
    "ends_at",
    "due_at",
    "assignee_relation",
)
TIME_FIELDS = ("occurred_at", "starts_at", "ends_at", "due_at")
ALLOWED_STATUSES = set(STATUS_LABELS) | {"unconfirmed"}
ALLOWED_RELEVANCE = {"work", "uncertain"}
ALLOWED_SENSITIVITY = {"normal", "internal_sensitive"}
ALLOWED_RETENTION = {"ephemeral", "persistent"}
MAX_RECORDS = 2000
MAX_INPUT_BYTES = 10 * 1024 * 1024
BASIS_RANK = {"explicit": 3, "stated_plan": 2, "inferred": 1}
CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}
TEXT_TOKEN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)


def normalize_text(value):
    return "".join(
        character.lower()
        for character in value
        if character.isalnum() or "\u4e00" <= character <= "\u9fff"
    )


def text_tokens(value):
    tokens = set()
    for segment in TEXT_TOKEN.findall(value.lower()):
        tokens.add(segment)
        if any("\u4e00" <= character <= "\u9fff" for character in segment):
            tokens.update(
                segment[index : index + 2]
                for index in range(max(0, len(segment) - 1))
            )
    return tokens


def semantic_text(record):
    if "_semantic_text" in record:
        return record["_semantic_text"]
    return " ".join(
        record[field]
        for field in (
            "title",
            "activity",
            "output",
            "decision",
            "risk",
            "next_action",
        )
        if record.get(field)
    )


def similarity(left, right):
    left_text = left.get("_semantic_normalized") or normalize_text(
        semantic_text(left)
    )
    right_text = right.get("_semantic_normalized") or normalize_text(
        semantic_text(right)
    )
    if not left_text or not right_text:
        return 0.0
    sequence = SequenceMatcher(None, left_text, right_text).ratio()
    left_tokens = left.get("_semantic_tokens") or text_tokens(semantic_text(left))
    right_tokens = right.get("_semantic_tokens") or text_tokens(semantic_text(right))
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    return max(sequence, jaccard)


def participant_overlap(left, right):
    left_participants = set(left.get("participants", ()))
    right_participants = set(right.get("participants", ()))
    if not left_participants or not right_participants:
        return False
    return bool(left_participants & right_participants)


def should_merge(left, right):
    if left["work_relevance"] != right["work_relevance"]:
        return False
    if left["source_ref"] == right["source_ref"]:
        return True
    same_workstream = normalize_text(left["workstream"]) == normalize_text(
        right["workstream"]
    )
    score = similarity(left, right)
    if same_workstream and score >= 0.60:
        return True
    same_due = left.get("due_at") and left.get("due_at") == right.get("due_at")
    if same_workstream and same_due and score >= 0.45:
        return True
    return participant_overlap(left, right) and score >= 0.82


def validate_record(value, index):
    if not isinstance(value, dict):
        raise ValueError(f"records[{index}] must be an object")
    unknown = sorted(set(value) - RECORD_FIELDS)
    if unknown:
        raise ValueError(
            f"records[{index}] contains unknown fields: {', '.join(unknown)}"
        )
    record = {}
    for field in REQUIRED_TEXT_FIELDS:
        record[field] = require_text(value.get(field), f"records[{index}].{field}")
    for field in OPTIONAL_TEXT_FIELDS:
        normalized = optional_text(value.get(field), f"records[{index}].{field}")
        if normalized is not None:
            record[field] = normalized

    if record["source_type"] not in SOURCE_TYPES:
        raise ValueError(
            f"records[{index}].source_type is not a supported source type"
        )
    if not is_valid_source_ref(record["source_ref"]):
        raise ValueError(
            f"records[{index}].source_ref must use http(s):// or source://"
        )
    if record["status"] not in ALLOWED_STATUSES:
        raise ValueError(f"records[{index}].status is invalid")
    if record["status_basis"] not in STATUS_BASES:
        raise ValueError(f"records[{index}].status_basis is invalid")
    if record["signal_kind"] not in SIGNAL_KINDS:
        raise ValueError(f"records[{index}].signal_kind is invalid")
    if record.get("action_kind") not in {None, *ACTION_KINDS}:
        raise ValueError(f"records[{index}].action_kind is invalid")
    if record.get("assignee_relation") not in {None, *ASSIGNEE_RELATIONS}:
        raise ValueError(f"records[{index}].assignee_relation is invalid")
    if record["confidence"] not in EVIDENCE_CONFIDENCE:
        raise ValueError(f"records[{index}].confidence is invalid")
    if record["work_relevance"] not in ALLOWED_RELEVANCE:
        raise ValueError(f"records[{index}].work_relevance is invalid")
    if record["sensitivity"] not in ALLOWED_SENSITIVITY:
        raise ValueError(
            f"records[{index}].sensitivity must exclude private evidence"
        )
    if record["retention_mode"] not in ALLOWED_RETENTION:
        raise ValueError(f"records[{index}].retention_mode is invalid")

    parsed_times = {}
    for field in TIME_FIELDS:
        if field in record:
            record[field], parsed_times[field] = parse_time(
                record[field],
                f"records[{index}].{field}",
            )
    if (
        "starts_at" in parsed_times
        and "ends_at" in parsed_times
        and parsed_times["starts_at"] >= parsed_times["ends_at"]
    ):
        raise ValueError(f"records[{index}].starts_at must be before ends_at")

    requires_response = value.get("requires_response", False)
    if not isinstance(requires_response, bool):
        raise ValueError(f"records[{index}].requires_response must be boolean")
    record["requires_response"] = requires_response

    participants = value.get("participants", [])
    if (
        not isinstance(participants, list)
        or not all(isinstance(item, str) and item.strip() for item in participants)
        or len(set(participants)) != len(participants)
    ):
        raise ValueError(
            f"records[{index}].participants must be a unique string array"
        )
    record["participants"] = [" ".join(item.split()) for item in participants]
    record["_semantic_text"] = " ".join(
        record[field]
        for field in (
            "title",
            "activity",
            "output",
            "decision",
            "risk",
            "next_action",
        )
        if record.get(field)
    )
    record["_semantic_normalized"] = normalize_text(record["_semantic_text"])
    record["_semantic_tokens"] = text_tokens(record["_semantic_text"])
    record["_parsed_times"] = parsed_times
    return record


def record_time(record, field):
    parsed_times = record.get("_parsed_times")
    if parsed_times is not None:
        return parsed_times[field]
    return parse_time(record[field], field)[1]


def record_sort_key(record):
    return (
        -BASIS_RANK[record["status_basis"]],
        -CONFIDENCE_RANK[record["confidence"]],
        SOURCE_RANK[record["source_type"]],
        -record_time(record, "occurred_at").timestamp(),
        record["record_id"],
    )


def choose_text(records, field):
    for record in sorted(records, key=record_sort_key):
        if record.get(field):
            return record[field]
    return None


def resolve_status(records):
    strongest = max(BASIS_RANK[record["status_basis"]] for record in records)
    candidates = [
        record
        for record in records
        if BASIS_RANK[record["status_basis"]] == strongest
    ]
    newest_time = max(
        record_time(record, "occurred_at") for record in candidates
    )
    newest = [
        record
        for record in candidates
        if record_time(record, "occurred_at") == newest_time
    ]
    statuses = {record["status"] for record in newest}
    if len(statuses) != 1:
        return "unconfirmed", True
    return next(iter(statuses)), False


def ordered_sources(records):
    ordered = sorted(
        records,
        key=lambda record: (
            SOURCE_RANK[record["source_type"]],
            -record_time(record, "occurred_at").timestamp(),
            record["source_ref"],
        ),
    )
    sources = []
    seen = set()
    for record in ordered:
        if record["source_ref"] not in seen:
            sources.append(record["source_ref"])
            seen.add(record["source_ref"])
    return sources


def priority_score(record, source_count, snapshot):
    score = 0
    basis = []

    def add(points, reason):
        nonlocal score
        score += points
        basis.append(reason)

    if record.get("impact"):
        add(18, "有明确影响")
    if record.get("output"):
        add(12, "形成可核验产出")
    if record.get("decision"):
        add(12, "包含关键决策")
    if record.get("risk"):
        add(12, "存在风险或依赖")
    if record["status"] == "completed":
        add(10, "已形成完成结果")
    elif record["status"] == "blocked":
        add(10, "处于阻塞状态")
    if record.get("next_action"):
        add(6, "存在明确下一步")
    if record.get("assignee_relation") == "self":
        add(8, "当前主体责任明确")
    if record.get("requires_response"):
        add(8, "需要当前主体回复")
    if record.get("due_at"):
        due = record_time(record, "due_at")
        seconds = (due - snapshot).total_seconds()
        if seconds < 0:
            add(18, "截止时间已逾期")
        elif seconds <= 2 * 86400:
            add(15, "两天内到期")
        elif seconds <= 7 * 86400:
            add(8, "七天内到期")
        else:
            add(2, "存在明确截止时间")
    confidence_points = {"high": 12, "medium": 7, "low": 3}
    add(confidence_points[record["confidence"]], "证据置信度")
    if source_count > 1:
        add(min(8, (source_count - 1) * 2), "多来源相互印证")
    return min(score, 100), basis


def merge_cluster(records, snapshot):
    status, status_conflict = resolve_status(records)
    canonical = sorted(records, key=record_sort_key)[0]
    source_refs = ordered_sources(records)
    source_types = sorted(
        {record["source_type"] for record in records},
        key=SOURCE_RANK.__getitem__,
    )
    merged = {
        "cluster_id": "sha256:"
        + hashlib.sha256(
            "\n".join(
                sorted(
                    [record["record_id"] for record in records] + source_refs
                )
            ).encode("utf-8")
        ).hexdigest(),
        "record_ids": sorted(record["record_id"] for record in records),
        "source_type": canonical["source_type"],
        "source_types": source_types,
        "source_ref": source_refs[0],
        "source_refs": source_refs,
        "occurred_at": max(
            records,
            key=lambda record: record_time(record, "occurred_at"),
        )["occurred_at"],
        "actor": choose_text(records, "actor"),
        "workstream": choose_text(records, "workstream"),
        "activity": choose_text(records, "activity"),
        "status": status,
        "status_basis": choose_text(records, "status_basis"),
        "signal_kind": choose_text(records, "signal_kind"),
        "requires_response": any(
            record.get("requires_response", False) for record in records
        ),
        "assignee_relation": (
            "self"
            if any(record.get("assignee_relation") == "self" for record in records)
            else (
                "shared"
                if any(
                    record.get("assignee_relation") == "shared"
                    for record in records
                )
                else "unknown"
            )
        ),
        "participants": sorted(
            {
                participant
                for record in records
                for participant in record.get("participants", [])
            }
        ),
        "confidence": max(
            (record["confidence"] for record in records),
            key=CONFIDENCE_RANK.__getitem__,
        ),
        "work_relevance": (
            "uncertain" if status_conflict else canonical["work_relevance"]
        ),
        "sensitivity": (
            "internal_sensitive"
            if any(
                record["sensitivity"] == "internal_sensitive" for record in records
            )
            else "normal"
        ),
        "classification_reason": (
            "状态证据同强度且同时间冲突，需人工复核"
            if status_conflict
            else choose_text(records, "classification_reason")
        ),
        "retention_mode": (
            "persistent"
            if all(record["retention_mode"] == "persistent" for record in records)
            else "ephemeral"
        ),
        "status_conflict": status_conflict,
    }
    for field in (
        "title",
        "output",
        "impact",
        "decision",
        "risk",
        "next_action",
        "action_kind",
        "starts_at",
        "ends_at",
        "due_at",
    ):
        selected = choose_text(records, field)
        if selected:
            merged[field] = selected
    score, score_basis = priority_score(merged, len(source_refs), snapshot)
    merged["priority_score"] = score
    merged["priority"] = 100 - score
    merged["priority_basis"] = score_basis
    return merged


def clusters(records):
    parents = list(range(len(records)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left, right):
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for left in range(len(records)):
        for right in range(left + 1, len(records)):
            if should_merge(records[left], records[right]):
                union(left, right)

    grouped = {}
    for index, record in enumerate(records):
        grouped.setdefault(find(index), []).append(record)
    return list(grouped.values())


def reconcile(payload):
    if not isinstance(payload, dict):
        raise ValueError("input must be an object")
    if set(payload) - {"schema_version", "snapshot", "records"}:
        raise ValueError("input contains unknown top-level fields")
    if payload.get("schema_version") != 1:
        raise ValueError("input schema_version must be 1")
    snapshot_text, snapshot = parse_time(payload.get("snapshot"), "snapshot")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("input must contain a records array")
    if len(records) > MAX_RECORDS:
        raise ValueError(f"record count exceeds limit: {MAX_RECORDS}")
    normalized = [validate_record(record, index) for index, record in enumerate(records)]
    record_ids = [record["record_id"] for record in normalized]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("record_id values must be unique")
    relevance_by_source = {}
    for record in normalized:
        relevance_by_source.setdefault(record["source_ref"], set()).add(
            record["work_relevance"]
        )
    conflicts = [
        source_ref
        for source_ref, relevance in relevance_by_source.items()
        if len(relevance) > 1
    ]
    if conflicts:
        raise ValueError(
            "source_ref cannot belong to both work and uncertain: "
            + ", ".join(sorted(conflicts))
        )

    merged = [merge_cluster(group, snapshot) for group in clusters(normalized)]
    work = sorted(
        (record for record in merged if record["work_relevance"] == "work"),
        key=lambda record: (record["priority"], record["cluster_id"]),
    )
    uncertain = sorted(
        (record for record in merged if record["work_relevance"] == "uncertain"),
        key=lambda record: (record["priority"], record["cluster_id"]),
    )
    return {
        "schema_version": 2,
        "snapshot": snapshot_text,
        "work": work,
        "uncertain": uncertain,
        "stats": {
            "input_count": len(normalized),
            "cluster_count": len(merged),
            "deduplicated_count": len(normalized) - len(merged),
            "status_conflict_count": sum(
                record["status_conflict"] for record in merged
            ),
        },
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="Verified evidence records JSON")
    parser.add_argument("--output", required=True, help="Destination evidence ledger")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        input_path = Path(args.file).resolve()
        output_path = Path(args.output).resolve()
        if input_path == output_path:
            raise ValueError("input and output paths must differ")
        if input_path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"input file exceeds {MAX_INPUT_BYTES} bytes")
        result = reconcile(read_json(input_path, "evidence record file"))
        write_json_atomic(output_path, result)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    emit_json(
        {
            "ledger_file": str(output_path),
            "work_count": len(result["work"]),
            "uncertain_count": len(result["uncertain"]),
            **result["stats"],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
