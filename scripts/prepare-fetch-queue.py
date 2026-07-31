#!/usr/bin/env python3
"""Build a sanitized body-fetch queue from metadata-only candidates."""

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path

from contracts import (
    ACTION_KINDS,
    ASSIGNEE_RELATIONS,
    SOURCE_RANK,
    SOURCE_TYPES,
)
from runtime_utils import (
    emit_json,
    read_json,
    write_json_compact_atomic,
)
from source_refs import is_valid_source_ref
from value_contracts import parse_aware_datetime, require_text


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
    "starts_at",
    "ends_at",
    "due_at",
    "requires_response",
    "action_kind",
    "assignee_relation",
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
    try:
        require_text(candidate.get(field), f"candidate {index} {field}")
    except ValueError as exc:
        raise ValueError(
            f"candidate {index} requires non-empty {field}"
        ) from exc


def validate_timestamp(value, index, field="occurred_at"):
    parse_aware_datetime(value, f"candidate {index} {field}")


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
            "parallelism": adapter.get("fetch_parallelism", 1),
            "file_output": adapter.get("fetch_file_output", False),
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
            parallelism = 1
            file_output = False
        else:
            selected = settings[adapter_id]
            operation = selected["operation"]
            batch_size = selected["batch_size"]
            parallelism = selected["parallelism"]
            file_output = selected["file_output"]
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
            if (
                isinstance(parallelism, bool)
                or not isinstance(parallelism, int)
                or parallelism < 1
            ):
                raise ValueError(
                    f"adapter has invalid fetch parallelism: {adapter_id}"
                )
            if not isinstance(file_output, bool):
                raise ValueError(
                    f"adapter has invalid fetch file_output flag: {adapter_id}"
                )
        for index in range(0, len(items), batch_size):
            group = items[index : index + batch_size]
            batch_id = f"fetch-{len(batches) + 1:04d}"
            batches.append(
                {
                    "batch_id": batch_id,
                    "adapter_id": adapter_id,
                    "operation": operation,
                    "parallelism": parallelism,
                    "file_output": file_output,
                    "body_file": f"fetch-results/{batch_id}.json",
                    "semantic_file": f"semantic-bodies/{batch_id}.json",
                    "evidence_file": f"evidence-parts/{batch_id}.json",
                    "request_file": f"fetch-requests/{batch_id}.json",
                    "global_ids": [item["global_id"] for item in group],
                }
            )
    return batches


def build_fetch_waves(fetch_batches):
    by_adapter = OrderedDict()
    limits = {}
    for batch in fetch_batches:
        adapter_id = batch["adapter_id"]
        by_adapter.setdefault(adapter_id, []).append(batch["batch_id"])
        limits[adapter_id] = batch["parallelism"]

    offsets = {adapter_id: 0 for adapter_id in by_adapter}
    waves = []
    while any(offsets[key] < len(by_adapter[key]) for key in by_adapter):
        batch_ids = []
        for adapter_id, ids in by_adapter.items():
            start = offsets[adapter_id]
            end = min(len(ids), start + limits[adapter_id])
            batch_ids.extend(ids[start:end])
            offsets[adapter_id] = end
        waves.append(
            {
                "wave": len(waves) + 1,
                "parallel": len(batch_ids) > 1,
                "batch_ids": batch_ids,
            }
        )
    return waves


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
        for field in ("starts_at", "ends_at", "due_at"):
            if field in candidate:
                require_nonempty_string(candidate, field, index)
                validate_timestamp(candidate[field], index, field)
        if "requires_response" in candidate and not isinstance(
            candidate["requires_response"], bool
        ):
            raise ValueError(
                f"candidate {index} requires_response must be boolean"
            )
        if (
            "action_kind" in candidate
            and candidate["action_kind"] not in ACTION_KINDS
        ):
            raise ValueError(f"candidate {index} has invalid action_kind")
        if (
            "assignee_relation" in candidate
            and candidate["assignee_relation"] not in ASSIGNEE_RELATIONS
        ):
            raise ValueError(f"candidate {index} has invalid assignee_relation")
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

    fetch_queue.sort(
        key=lambda item: (
            item.get("priority", 100),
            SOURCE_RANK[item["source_type"]],
            item["global_id"],
        )
    )
    fetch_batches = build_fetch_batches(fetch_queue, settings)
    fetch_waves = build_fetch_waves(fetch_batches)

    candidate_index = {}
    for item in fetch_queue:
        candidate_index[item["global_id"]] = {
            key: value for key, value in item.items() if key != "global_id"
        }
    return {
        "schema_version": 2,
        "candidate_index": candidate_index,
        "fetch_batches": fetch_batches,
        "fetch_waves": fetch_waves,
        "included_counts": included_counts,
        "deduplicated_count": deduplicated_count,
    }


def write_batch_requests(output_path, result):
    output_root = output_path.parent
    index = result["candidate_index"]
    for batch in result["fetch_batches"]:
        request_path = output_root / batch["request_file"]
        items = [
            {"global_id": global_id, **index[global_id]}
            for global_id in batch["global_ids"]
        ]
        write_json_compact_atomic(
            request_path,
            {
                "schema_version": 1,
                "batch_id": batch["batch_id"],
                "adapter_id": batch["adapter_id"],
                "operation": batch["operation"],
                "items": items,
            },
        )


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
        write_batch_requests(output_path, result)
        write_json_compact_atomic(output_path, result)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    summary = {
        "queue_file": str(output_path),
        "queued": len(result["candidate_index"]),
        "included_counts": result["included_counts"],
        "deduplicated_count": result["deduplicated_count"],
        "fetch_batch_count": len(result["fetch_batches"]),
        "fetch_wave_count": len(result["fetch_waves"]),
    }
    emit_json(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
