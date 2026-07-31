#!/usr/bin/env python3
"""Create a compact, lossless-for-synthesis view of a canonical evidence ledger."""

import argparse
import json
import sys
from pathlib import Path

from runtime_utils import emit_json, read_json, write_json_compact_atomic
from value_contracts import require_text


REQUIRED_FIELDS = (
    "cluster_id",
    "source_types",
    "occurred_at",
    "actor",
    "workstream",
    "activity",
    "status",
    "status_basis",
    "signal_kind",
    "requires_response",
    "assignee_relation",
    "participants",
    "confidence",
    "classification_reason",
    "status_conflict",
    "sensitivity",
    "priority",
    "priority_basis",
)
OPTIONAL_FIELDS = (
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
)


def compact_item(item, label):
    if not isinstance(item, dict):
        raise ValueError(f"{label} must be an object")
    for field in REQUIRED_FIELDS:
        if field not in item:
            raise ValueError(f"{label} requires {field}")
    cluster_id = require_text(item["cluster_id"], f"{label}.cluster_id")
    source_refs = item.get("source_refs")
    if not isinstance(source_refs, list) or not source_refs:
        raise ValueError(f"{label}.source_refs must be a non-empty array")
    result = {"cluster_id": cluster_id}
    for field in REQUIRED_FIELDS[1:]:
        result[field] = item[field]
    result["source_count"] = len(dict.fromkeys(source_refs))
    for field in OPTIONAL_FIELDS:
        if field in item:
            result[field] = item[field]
    return result


def prepare(payload):
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise ValueError("ledger schema_version must be 2")
    output = {
        "schema_version": 1,
        "ledger_schema_version": 2,
        "snapshot": require_text(payload.get("snapshot"), "ledger.snapshot"),
    }
    seen = set()
    for partition in ("work", "uncertain"):
        items = payload.get(partition)
        if not isinstance(items, list):
            raise ValueError(f"ledger requires a {partition} array")
        compacted = []
        for index, item in enumerate(items):
            result = compact_item(item, f"ledger.{partition}[{index}]")
            cluster_id = result["cluster_id"]
            if cluster_id in seen:
                raise ValueError(f"ledger duplicates cluster_id: {cluster_id}")
            seen.add(cluster_id)
            compacted.append(result)
        output[partition] = compacted
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="Canonical ledger JSON")
    parser.add_argument("--output", required=True, help="Compact synthesis view JSON")
    args = parser.parse_args()
    try:
        source = Path(args.file).resolve()
        output = Path(args.output).resolve()
        if source == output:
            raise ValueError("input and output paths must differ")
        result = prepare(read_json(source, "evidence ledger"))
        write_json_compact_atomic(output, result)
    except (OSError, ValueError) as exc:
        emit_json({"error": str(exc)}, sys.stderr)
        return 2
    emit_json(
        {
            "synthesis_file": str(output),
            "work_count": len(result["work"]),
            "uncertain_count": len(result["uncertain"]),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
