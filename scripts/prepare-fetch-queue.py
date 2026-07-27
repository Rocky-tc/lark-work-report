#!/usr/bin/env python3
"""Build a body-fetch queue from metadata-classified report candidates."""

import argparse
import json
import sys
from pathlib import Path


ALLOWED_RELEVANCE = {"work", "uncertain", "private", "chatter"}
FETCHABLE_RELEVANCE = {"work", "uncertain"}
REQUIRED_FIELDS = (
    "id",
    "source_type",
    "source_ref",
    "prefetch_relevance",
    "classification_reason",
)


def require_nonempty_string(candidate, field, index):
    value = candidate.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"candidate {index} requires non-empty {field}")


def prepare(payload):
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list):
        raise ValueError("input must contain a candidates array")

    fetch_queue = []
    included_counts = {"work": 0, "uncertain": 0}
    skipped_counts = {"private": 0, "chatter": 0}

    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError(f"candidate {index} must be an object")
        for field in REQUIRED_FIELDS:
            require_nonempty_string(candidate, field, index)

        relevance = candidate["prefetch_relevance"]
        if relevance not in ALLOWED_RELEVANCE:
            raise ValueError(
                f"candidate {index} has invalid prefetch_relevance: {relevance}"
            )

        if relevance in FETCHABLE_RELEVANCE:
            fetch_queue.append(candidate)
            included_counts[relevance] += 1
        else:
            skipped_counts[relevance] += 1

    return {
        "fetch_queue": fetch_queue,
        "included_counts": included_counts,
        "skipped_counts": skipped_counts,
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="JSON file containing candidates")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
        result = prepare(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
