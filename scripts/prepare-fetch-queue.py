#!/usr/bin/env python3
"""Build a sanitized body-fetch queue from metadata-only candidates."""

import argparse
import json
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from contracts import SOURCE_TYPES
from runtime_utils import read_json, write_json_atomic
from source_refs import is_valid_source_ref


ALLOWED_RELEVANCE = {"work", "uncertain", "private", "chatter"}
FETCHABLE_RELEVANCE = {"work", "uncertain"}
REQUIRED_FIELDS = (
    "id",
    "adapter_id",
    "source_type",
    "occurred_at",
    "source_ref",
    "prefetch_relevance",
    "classification_reason",
)
OPTIONAL_FIELDS = (
    "title",
    "container",
    "participants",
    "workstream_hint",
    "priority",
    "cursor",
)
BODY_FIELD_NAMES = {
    "body",
    "content",
    "raw_content",
    "transcript",
    "message_text",
    "full_text",
}
MAX_CANDIDATES = 5000
MAX_INPUT_BYTES = 5 * 1024 * 1024


def require_nonempty_string(candidate, field, index):
    value = candidate.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"candidate {index} requires non-empty {field}")


def validate_timestamp(value, index):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"candidate {index} occurred_at must be timezone-aware ISO 8601"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(
            f"candidate {index} occurred_at must include a timezone offset"
        )


def find_body_field(value, path="candidate"):
    if isinstance(value, dict):
        for key, nested in value.items():
            nested_path = f"{path}.{key}"
            if key.lower() in BODY_FIELD_NAMES:
                return nested_path
            found = find_body_field(nested, nested_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = find_body_field(nested, f"{path}[{index}]")
            if found:
                return found
    return None


def sanitized_candidate(candidate):
    result = {field: candidate[field] for field in REQUIRED_FIELDS}
    for field in OPTIONAL_FIELDS:
        if field in candidate:
            result[field] = candidate[field]
    result["global_id"] = (
        f"{candidate['adapter_id']}:{candidate['source_type']}:{candidate['id']}"
    )
    return result


def adapter_settings(plan):
    if plan is None:
        return None
    adapters = plan.get("adapters") if isinstance(plan, dict) else None
    if not isinstance(adapters, list):
        raise ValueError("run plan requires an adapters array")
    settings = {}
    for adapter in adapters:
        if not isinstance(adapter, dict):
            raise ValueError("run plan adapter entries must be objects")
        adapter_id = adapter.get("adapter_id")
        if not isinstance(adapter_id, str) or not adapter_id:
            raise ValueError("run plan adapter requires adapter_id")
        if adapter_id in settings:
            raise ValueError(f"run plan duplicates adapter_id: {adapter_id}")
        settings[adapter_id] = {
            "operation": adapter.get("fetch_operation"),
            "batch_size": adapter.get("fetch_batch_size", 1),
        }
    return settings


def select_canonical(group, settings):
    def key(entry):
        _, candidate = entry
        adapter_id = candidate["adapter_id"]
        if settings is not None and adapter_id not in settings:
            raise ValueError(f"candidate adapter is missing from run plan: {adapter_id}")
        batch_size = (
            settings[adapter_id]["batch_size"] if settings is not None else 1
        )
        priority = candidate.get("priority", 100)
        return (-batch_size, priority, entry[0])

    return min(group, key=key)


def build_fetch_batches(fetch_queue, settings):
    by_adapter = OrderedDict()
    for item in fetch_queue:
        by_adapter.setdefault(item["adapter_id"], []).append(item)

    batches = []
    for adapter_id, items in by_adapter.items():
        if settings is None:
            operation = "candidate.fetch"
            batch_size = 1
        else:
            selected = settings[adapter_id]
            operation = selected["operation"]
            batch_size = selected["batch_size"]
            if not isinstance(operation, str) or not operation:
                raise ValueError(f"adapter cannot fetch candidates: {adapter_id}")
            if (
                isinstance(batch_size, bool)
                or not isinstance(batch_size, int)
                or batch_size < 1
            ):
                raise ValueError(
                    f"adapter has invalid fetch batch size: {adapter_id}"
                )
        for index in range(0, len(items), batch_size):
            group = items[index : index + batch_size]
            batches.append(
                {
                    "adapter_id": adapter_id,
                    "operation": operation,
                    "items": [
                        {
                            "id": item["id"],
                            "source_type": item["source_type"],
                            "source_ref": item["source_ref"],
                        }
                        for item in group
                    ],
                }
            )
    return batches


def prepare(payload, plan=None):
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list):
        raise ValueError("input must contain a candidates array")
    if len(candidates) > MAX_CANDIDATES:
        raise ValueError(f"candidate count exceeds limit: {MAX_CANDIDATES}")

    validated = []
    included_counts = {"work": 0, "uncertain": 0}
    seen = set()

    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError(f"candidate {index} must be an object")
        for field in REQUIRED_FIELDS:
            require_nonempty_string(candidate, field, index)

        source_type = candidate["source_type"]
        if source_type not in SOURCE_TYPES:
            raise ValueError(f"candidate {index} has invalid source_type: {source_type}")
        if not is_valid_source_ref(candidate["source_ref"]):
            raise ValueError(
                f"candidate {index} source_ref must use http(s):// or source://"
            )
        validate_timestamp(candidate["occurred_at"], index)

        identity = (candidate["adapter_id"], source_type, candidate["id"])
        if identity in seen:
            raise ValueError(f"candidate {index} duplicates stable identity {identity}")
        seen.add(identity)

        body_field = find_body_field(candidate)
        if body_field:
            raise ValueError(
                f"candidate {index} contains forbidden body field before filtering: "
                f"{body_field}"
            )

        relevance = candidate["prefetch_relevance"]
        if relevance not in ALLOWED_RELEVANCE:
            raise ValueError(
                f"candidate {index} has invalid prefetch_relevance: {relevance}"
            )

        if "priority" in candidate and (
            isinstance(candidate["priority"], bool)
            or not isinstance(candidate["priority"], int)
            or candidate["priority"] < 0
        ):
            raise ValueError(f"candidate {index} priority must be a non-negative integer")
        validated.append((index, candidate))

    grouped = OrderedDict()
    for entry in validated:
        grouped.setdefault(entry[1]["source_ref"], []).append(entry)

    settings = adapter_settings(plan)
    fetch_queue = []
    deduplicated_count = 0
    for source_ref, group in grouped.items():
        relevance = {candidate["prefetch_relevance"] for _, candidate in group}
        fetchable = relevance & FETCHABLE_RELEVANCE
        excluded = relevance - FETCHABLE_RELEVANCE
        if fetchable and excluded:
            raise ValueError(
                f"source_ref has conflicting fetch policy classifications: {source_ref}"
            )
        if excluded:
            continue

        deduplicated_count += len(group) - 1
        _, candidate = select_canonical(group, settings)
        item = sanitized_candidate(candidate)
        canonical_relevance = "work" if "work" in relevance else "uncertain"
        item["prefetch_relevance"] = canonical_relevance
        alternates = [
            {
                "adapter_id": other["adapter_id"],
                "source_type": other["source_type"],
                "id": other["id"],
            }
            for _, other in group
            if other is not candidate
        ]
        if alternates:
            item["alternate_ids"] = alternates
        fetch_queue.append(item)
        included_counts[canonical_relevance] += 1

    fetch_batches = build_fetch_batches(fetch_queue, settings)

    return {
        "schema_version": 1,
        "fetch_queue": fetch_queue,
        "fetch_batches": fetch_batches,
        "included_counts": included_counts,
        "deduplicated_count": deduplicated_count,
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="JSON file containing candidates")
    parser.add_argument("--output", required=True, help="Destination queue JSON file")
    parser.add_argument("--run-plan", help="Run plan produced by prepare-run.py")
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
        payload = read_json(input_path, "candidate file")
        plan = read_json(Path(args.run_plan), "run plan") if args.run_plan else None
        result = prepare(payload, plan)
        write_json_atomic(output_path, result)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    summary = {
        "queue_file": str(output_path),
        "queued": len(result["fetch_queue"]),
        "included_counts": result["included_counts"],
        "deduplicated_count": result["deduplicated_count"],
        "fetch_batch_count": len(result["fetch_batches"]),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
