#!/usr/bin/env python3
"""Fail closed unless every queued body has one extraction outcome, then reconcile."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from runtime_utils import (
    emit_json,
    load_script,
    read_json,
    write_json_atomic,
)
from value_contracts import require_text


SCRIPT_DIR = Path(__file__).resolve().parent
MANAGE_RUN = load_script(SCRIPT_DIR, "manage-run.py")
RECONCILE = load_script(SCRIPT_DIR, "reconcile-evidence.py")
OUTCOMES = (
    "work",
    "uncertain",
    "discarded_private",
    "discarded_chatter",
    "access_gap",
)
EVIDENCE_OUTCOMES = {"work", "uncertain"}
DISCARDED_OUTCOMES = {"discarded_private", "discarded_chatter"}
MAX_PART_BYTES = 20 * 1024 * 1024
MAX_RESULTS = 5000


def strict_fields(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unknown fields: {', '.join(unknown)}")


def managed_relative_file(run_dir, value, label):
    value = require_text(value, label)
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"{label} must be relative to the run directory")
    resolved = (run_dir / path).resolve()
    try:
        resolved.relative_to(run_dir)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the run directory") from exc
    return resolved


def load_queue(run_dir):
    payload = read_json(run_dir / "fetch-queue.json", "fetch queue")
    strict_fields(
        payload,
        {
            "schema_version",
            "fetch_queue",
            "fetch_batches",
            "fetch_waves",
            "included_counts",
            "deduplicated_count",
        },
        "fetch queue",
    )
    if payload.get("schema_version") != 1:
        raise ValueError("fetch queue schema_version must be 1")
    items = payload.get("fetch_queue")
    if not isinstance(items, list):
        raise ValueError("fetch queue requires a fetch_queue array")
    queued = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"fetch_queue[{index}] must be an object")
        global_id = require_text(
            item.get("global_id"), f"fetch_queue[{index}].global_id"
        )
        if global_id in queued:
            raise ValueError(f"fetch queue duplicates global_id: {global_id}")
        for field in ("source_type", "source_ref", "occurred_at"):
            require_text(item.get(field), f"fetch_queue[{index}].{field}")
        relevance = item.get("prefetch_relevance")
        if relevance not in EVIDENCE_OUTCOMES:
            raise ValueError(
                f"fetch_queue[{index}].prefetch_relevance must be work or uncertain"
            )
        queued[global_id] = item
    raw_batches = payload.get("fetch_batches")
    if raw_batches is None:
        return queued, None
    if not isinstance(raw_batches, list):
        raise ValueError("fetch queue fetch_batches must be an array")
    batches = {}
    batched_ids = set()
    for index, batch in enumerate(raw_batches):
        label = f"fetch_batches[{index}]"
        if not isinstance(batch, dict):
            raise ValueError(f"{label} must be an object")
        batch_id = require_text(batch.get("batch_id"), f"{label}.batch_id")
        if batch_id in batches:
            raise ValueError(f"fetch queue duplicates batch_id: {batch_id}")
        items = batch.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError(f"{label}.items must be a non-empty array")
        ids = []
        for item_index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"{label}.items[{item_index}] must be an object")
            global_id = require_text(
                item.get("global_id"),
                f"{label}.items[{item_index}].global_id",
            )
            if global_id not in queued:
                raise ValueError(f"{label} contains unknown global_id: {global_id}")
            if global_id in batched_ids:
                raise ValueError(f"fetch batches duplicate global_id: {global_id}")
            batched_ids.add(global_id)
            ids.append(global_id)
        batches[batch_id] = {
            "global_ids": set(ids),
            "body_file": managed_relative_file(
                run_dir, batch.get("body_file"), f"{label}.body_file"
            ),
            "evidence_file": managed_relative_file(
                run_dir, batch.get("evidence_file"), f"{label}.evidence_file"
            ),
        }
    if batched_ids != set(queued):
        missing = sorted(set(queued) - batched_ids)
        raise ValueError("fetch batches do not cover queue: " + ", ".join(missing))
    return queued, batches


def parse_part(path, expected_batch_id=None, expected_ids=None):
    payload = read_json(path, f"evidence part {path.name}")
    strict_fields(
        payload,
        {"schema_version", "batch_id", "results"},
        f"evidence part {path.name}",
    )
    if payload.get("schema_version") != 1:
        raise ValueError(f"evidence part {path.name} schema_version must be 1")
    batch_id = require_text(
        payload.get("batch_id"), f"evidence part {path.name}.batch_id"
    )
    if expected_batch_id is not None and batch_id != expected_batch_id:
        raise ValueError(
            f"evidence part {path.name} batch_id does not match fetch queue"
        )
    part_results = payload.get("results")
    if not isinstance(part_results, list):
        raise ValueError(f"evidence part {path.name}.results must be an array")
    if expected_ids is not None:
        actual_ids = [
            result.get("global_id") for result in part_results
            if isinstance(result, dict)
        ]
        if len(actual_ids) != len(part_results) or set(actual_ids) != expected_ids:
            raise ValueError(
                f"evidence part {path.name} does not cover its fetch batch"
            )
    return part_results


def load_results(run_dir, batches):
    parts_dir = run_dir / "evidence-parts"
    if parts_dir.is_symlink():
        raise ValueError("evidence-parts cannot be a symlink")
    if not parts_dir.exists():
        return []
    if not parts_dir.is_dir():
        raise ValueError("evidence-parts must be a directory")
    if batches is not None:
        paths = []
        results = []
        total_bytes = 0
        expected_evidence_paths = set()
        for batch_id, batch in batches.items():
            body_path = batch["body_file"]
            if (
                body_path.is_symlink()
                or not body_path.is_file()
                or body_path.stat().st_size == 0
            ):
                raise ValueError(f"missing complete fetch body file for {batch_id}")
            path = batch["evidence_file"]
            expected_evidence_paths.add(path)
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"missing evidence part for {batch_id}")
            total_bytes += path.stat().st_size
            if total_bytes > MAX_PART_BYTES:
                raise ValueError(f"evidence parts exceed {MAX_PART_BYTES} bytes")
            part_results = parse_part(path, batch_id, batch["global_ids"])
            results.extend(part_results)
            if len(results) > MAX_RESULTS:
                raise ValueError(f"extraction result count exceeds {MAX_RESULTS}")
        actual_paths = {
            path.resolve()
            for path in parts_dir.glob("*.json")
            if path.is_file() and not path.is_symlink()
        }
        if actual_paths != expected_evidence_paths:
            raise ValueError("evidence-parts contains an unexpected or missing file")
        return results

    paths = sorted(parts_dir.glob("*.json"))
    total_bytes = 0
    results = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"evidence part must be a regular file: {path.name}")
        total_bytes += path.stat().st_size
        if total_bytes > MAX_PART_BYTES:
            raise ValueError(f"evidence parts exceed {MAX_PART_BYTES} bytes")
        part_results = parse_part(path)
        results.extend(part_results)
        if len(results) > MAX_RESULTS:
            raise ValueError(f"extraction result count exceeds {MAX_RESULTS}")
    return results


def validate_result(result, index, queued):
    label = f"extraction result {index}"
    if not isinstance(result, dict):
        raise ValueError(f"{label} must be an object")
    global_id = require_text(result.get("global_id"), f"{label}.global_id")
    if global_id not in queued:
        raise ValueError(f"{label} is not present in fetch queue: {global_id}")
    outcome = result.get("outcome")
    if outcome not in OUTCOMES:
        raise ValueError(f"{label}.outcome is invalid")
    queue_item = queued[global_id]

    if outcome in EVIDENCE_OUTCOMES:
        strict_fields(result, {"global_id", "outcome", "record"}, label)
        record = result.get("record")
        if not isinstance(record, dict):
            raise ValueError(f"{label}.record must be an object")
        expected = {
            "record_id": global_id,
            "source_type": queue_item["source_type"],
            "source_ref": queue_item["source_ref"],
            "occurred_at": queue_item["occurred_at"],
            "work_relevance": outcome,
        }
        if any(record.get(field) != value for field, value in expected.items()):
            raise ValueError(f"{label}.record identity does not match fetch queue")
        return global_id, outcome, record, None

    if outcome in DISCARDED_OUTCOMES:
        strict_fields(result, {"global_id", "outcome"}, label)
        return global_id, outcome, None, None

    strict_fields(result, {"global_id", "outcome", "reason"}, label)
    reason = require_text(result.get("reason"), f"{label}.reason")
    return global_id, outcome, None, reason


def compile_run(run_dir):
    queued, batches = load_queue(run_dir)
    results = load_results(run_dir, batches)
    seen = set()
    records = []
    counts = Counter({outcome: 0 for outcome in OUTCOMES})
    access_gaps = []
    for index, result in enumerate(results):
        global_id, outcome, record, reason = validate_result(result, index, queued)
        if global_id in seen:
            raise ValueError(f"duplicate extraction result: {global_id}")
        seen.add(global_id)
        counts[outcome] += 1
        if record is not None:
            records.append(record)
        elif outcome == "access_gap":
            access_gaps.append(
                {
                    "global_id": global_id,
                    "source_ref": queued[global_id]["source_ref"],
                    "reason": reason,
                }
            )

    missing = sorted(set(queued) - seen)
    if missing:
        raise ValueError("missing extraction results: " + ", ".join(missing))

    plan = read_json(run_dir / "run-plan.json", "run plan")
    period = plan.get("period") if isinstance(plan, dict) else None
    if not isinstance(period, dict):
        raise ValueError("run plan requires a period object")
    snapshot = require_text(period.get("snapshot"), "run plan period.snapshot")
    records_payload = {
        "schema_version": 1,
        "snapshot": snapshot,
        "records": records,
    }
    ledger = RECONCILE.reconcile(records_payload)
    audit = {
        "schema_version": 1,
        "total_count": len(queued),
        "processed_count": len(seen),
        "outcome_counts": {outcome: counts[outcome] for outcome in OUTCOMES},
        "access_gaps": access_gaps,
    }
    write_json_atomic(run_dir / "evidence-records.json", records_payload)
    write_json_atomic(run_dir / "ledger.json", ledger)
    write_json_atomic(run_dir / "extraction-audit.json", audit)
    return {
        "records_file": str(run_dir / "evidence-records.json"),
        "ledger_file": str(run_dir / "ledger.json"),
        "audit_file": str(run_dir / "extraction-audit.json"),
        "processed_count": len(seen),
        "work_count": len(ledger["work"]),
        "uncertain_count": len(ledger["uncertain"]),
        "access_gap_count": counts["access_gap"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    try:
        run_dir = MANAGE_RUN.checked_run_dir(args.run_dir)
        result = compile_run(run_dir)
    except (OSError, ValueError) as exc:
        emit_json({"error": str(exc)}, sys.stderr)
        return 2
    emit_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
