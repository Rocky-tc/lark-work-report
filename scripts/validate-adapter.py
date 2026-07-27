#!/usr/bin/env python3
"""Validate a host adapter manifest and select a safe collection mode."""

import argparse
import json
import re
import sys
from pathlib import Path

from contracts import SOURCE_TYPES


ADAPTER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def capability(capabilities, name):
    value = capabilities.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"capability {name} must be an object")
    available = value.get("available", False)
    if not isinstance(available, bool):
        raise ValueError(f"capability {name}.available must be boolean")
    return value


def bounded_integer(value, label, minimum, maximum, errors, *, required=False):
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{label} must be an integer")
        return None
    if not minimum <= value <= maximum:
        errors.append(f"{label} must be between {minimum} and {maximum}")
        return None
    return value


def validate(payload):
    errors = []
    warnings = []
    if not isinstance(payload, dict):
        raise ValueError("adapter manifest must be an object")

    adapter_id = payload.get("adapter_id")
    if not isinstance(adapter_id, str) or not ADAPTER_ID.fullmatch(adapter_id):
        errors.append("adapter_id must match ^[a-z0-9][a-z0-9._-]*$")

    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, dict):
        raise ValueError("adapter manifest requires a capabilities object")

    identity = capability(capabilities, "identity.current")
    listed = capability(capabilities, "candidate.list")
    listed_many = capability(capabilities, "candidate.list_many")
    fetched = capability(capabilities, "candidate.fetch")
    fetched_many = capability(capabilities, "candidate.fetch_many")
    created = capability(capabilities, "report.create")
    report_fetched = capability(capabilities, "report.fetch")

    domains = []
    list_parallelism = 1
    if listed.get("available"):
        metadata_only = listed.get("metadata_only")
        if not isinstance(metadata_only, bool):
            errors.append("candidate.list.metadata_only must be boolean when available")
        declared_domains = listed.get("domains")
        if not isinstance(declared_domains, list) or not declared_domains or not all(
            isinstance(item, str) and item for item in declared_domains
        ):
            errors.append("candidate.list.domains must be a non-empty string array")
        elif not set(declared_domains).issubset(SOURCE_TYPES):
            errors.append("candidate.list.domains contains an unknown source type")
        elif len(set(declared_domains)) != len(declared_domains):
            errors.append("candidate.list.domains must not contain duplicates")
        else:
            domains = declared_domains

        parallel_safe = listed.get("parallel_safe", False)
        if not isinstance(parallel_safe, bool):
            errors.append("candidate.list.parallel_safe must be boolean")
            parallel_safe = False
        if parallel_safe:
            list_parallelism = (
                bounded_integer(
                    listed.get("max_parallelism"),
                    "candidate.list.max_parallelism",
                    2,
                    16,
                    errors,
                    required=True,
                )
                or 1
            )
        elif "max_parallelism" in listed:
            errors.append(
                "candidate.list.max_parallelism requires parallel_safe=true"
            )

    list_batch_size = 1
    if listed_many.get("available"):
        if not listed.get("available"):
            errors.append("candidate.list_many requires candidate.list")
        if listed.get("metadata_only") is not True:
            errors.append(
                "candidate.list_many requires candidate.list.metadata_only=true"
            )
        list_batch_size = (
            bounded_integer(
                listed_many.get("max_domains"),
                "candidate.list_many.max_domains",
                2,
                len(SOURCE_TYPES),
                errors,
                required=True,
            )
            or 1
        )

    fetch_batch_size = 1
    if fetched_many.get("available"):
        if not fetched.get("available"):
            errors.append("candidate.fetch_many requires candidate.fetch")
        fetch_batch_size = (
            bounded_integer(
                fetched_many.get("max_batch_size"),
                "candidate.fetch_many.max_batch_size",
                2,
                100,
                errors,
                required=True,
            )
            or 1
        )

    if (
        listed.get("available") or fetched.get("available")
    ) and not identity.get("available"):
        errors.append("live candidate access requires identity.current")

    if created.get("available") and not report_fetched.get("available"):
        errors.append("report.create requires report.fetch for verified delivery")

    if (
        listed.get("available")
        and listed.get("metadata_only") is True
        and fetched.get("available")
    ):
        collection_mode = "broad"
    elif fetched.get("available"):
        collection_mode = "explicit_only"
        warnings.append(
            "metadata-only listing is unavailable; do not run broad discovery"
        )
    else:
        collection_mode = "offline_only"
        warnings.append("candidate.fetch is unavailable; use provided exports only")

    if collection_mode == "broad" and listed_many.get("available"):
        metadata_strategy = "batch"
        list_operation = "candidate.list_many"
    elif collection_mode == "broad" and listed.get("parallel_safe") is True:
        metadata_strategy = "parallel"
        list_operation = "candidate.list"
    elif collection_mode == "broad":
        metadata_strategy = "serial"
        list_operation = "candidate.list"
    else:
        metadata_strategy = "none"
        list_operation = None

    if fetched_many.get("available"):
        fetch_strategy = "batch"
        fetch_operation = "candidate.fetch_many"
    elif fetched.get("available"):
        fetch_strategy = "serial"
        fetch_operation = "candidate.fetch"
    else:
        fetch_strategy = "none"
        fetch_operation = None

    delivery_mode = (
        "document"
        if created.get("available") and report_fetched.get("available")
        else "markdown"
    )
    return {
        "ok": not errors,
        "adapter_id": adapter_id,
        "identity_available": identity.get("available", False),
        "collection_mode": collection_mode,
        "delivery_mode": delivery_mode,
        "domains": domains,
        "metadata_strategy": metadata_strategy,
        "list_operation": list_operation,
        "list_batch_size": list_batch_size,
        "metadata_parallelism": list_parallelism,
        "fetch_strategy": fetch_strategy,
        "fetch_operation": fetch_operation,
        "fetch_batch_size": fetch_batch_size,
        "errors": errors,
        "warnings": warnings,
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True)
    return parser


def main():
    args = build_parser().parse_args()
    try:
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
        result = validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
