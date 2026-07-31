#!/usr/bin/env python3
"""Normalize one complete fetch batch without summarizing or semantic deduplication."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from runtime_utils import emit_json, read_json, write_json_compact_atomic
from value_contracts import require_text


MAX_BODY_BYTES = 50 * 1024 * 1024
CONTENT_TYPES = {"text", "xml", "json"}


def strict_fields(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unknown fields: {', '.join(unknown)}")


def request_contract(request):
    strict_fields(
        request,
        {"schema_version", "batch_id", "adapter_id", "operation", "items"},
        "fetch request",
    )
    if request.get("schema_version") != 1:
        raise ValueError("fetch request schema_version must be 1")
    batch_id = require_text(request.get("batch_id"), "fetch request.batch_id")
    items = request.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("fetch request.items must be a non-empty array")
    ids = [
        require_text(item.get("global_id"), f"fetch request.items[{index}].global_id")
        if isinstance(item, dict)
        else require_text(None, f"fetch request.items[{index}].global_id")
        for index, item in enumerate(items)
    ]
    if len(ids) != len(set(ids)):
        raise ValueError("fetch request contains duplicate global_id")
    return batch_id, ids


def canonical_content(content_type, content, label):
    if content_type in {"text", "xml"}:
        if not isinstance(content, str):
            raise ValueError(f"{label}.content must be a string")
        encoded = content.encode("utf-8")
        stored = content
    else:
        try:
            canonical = json.dumps(
                content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}.content must be valid JSON") from exc
        encoded = canonical.encode("utf-8")
        stored = content
    digest = hashlib.sha256(
        content_type.encode("ascii") + b"\0" + encoded
    ).hexdigest()
    return f"sha256:{digest}", stored


def normalize(request, body):
    batch_id, expected_ids = request_contract(request)
    strict_fields(body, {"schema_version", "batch_id", "results"}, "fetch body")
    if body.get("schema_version") != 1:
        raise ValueError("fetch body schema_version must be 1")
    if require_text(body.get("batch_id"), "fetch body.batch_id") != batch_id:
        raise ValueError("fetch body batch_id does not match fetch request")
    results = body.get("results")
    if not isinstance(results, list):
        raise ValueError("fetch body.results must be an array")

    seen = set()
    blobs = {}
    items = []
    for index, result in enumerate(results):
        label = f"fetch body.results[{index}]"
        strict_fields(
            result,
            {"global_id", "content_type", "content", "context"},
            label,
        )
        global_id = require_text(result.get("global_id"), f"{label}.global_id")
        if global_id in seen:
            raise ValueError(f"fetch body duplicates global_id: {global_id}")
        seen.add(global_id)
        content_type = result.get("content_type")
        if content_type not in CONTENT_TYPES:
            raise ValueError(f"{label}.content_type is invalid")
        context = result.get("context")
        if not isinstance(context, dict):
            raise ValueError(f"{label}.context must be an object")
        fingerprint, content = canonical_content(
            content_type,
            result.get("content"),
            label,
        )
        blobs.setdefault(
            fingerprint,
            {"content_type": content_type, "content": content},
        )
        items.append(
            {
                "global_id": global_id,
                "content_fingerprint": fingerprint,
                "context": context,
            }
        )
    actual_ids = [item["global_id"] for item in items]
    if set(actual_ids) != set(expected_ids):
        missing = len(set(expected_ids) - set(actual_ids))
        extra = len(set(actual_ids) - set(expected_ids))
        raise ValueError(
            f"fetch body does not cover request: missing={missing}, extra={extra}"
        )
    order = {global_id: index for index, global_id in enumerate(expected_ids)}
    items.sort(key=lambda item: order[item["global_id"]])
    return {
        "schema_version": 1,
        "batch_id": batch_id,
        "content_blobs": blobs,
        "items": items,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        request_file = Path(args.request_file).resolve()
        body_file = Path(args.body_file).resolve()
        output = Path(args.output).resolve()
        if output in {request_file, body_file}:
            raise ValueError("output must differ from raw workflow inputs")
        if body_file.stat().st_size > MAX_BODY_BYTES:
            raise ValueError(f"fetch body exceeds {MAX_BODY_BYTES} bytes")
        result = normalize(
            read_json(request_file, "fetch request"),
            read_json(body_file, "fetch body"),
        )
        write_json_compact_atomic(output, result)
    except (OSError, ValueError) as exc:
        emit_json({"error": str(exc)}, sys.stderr)
        return 2
    emit_json(
        {
            "semantic_file": str(output),
            "item_count": len(result["items"]),
            "unique_content_count": len(result["content_blobs"]),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
