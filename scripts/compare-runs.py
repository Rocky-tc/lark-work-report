#!/usr/bin/env python3
"""Compare two completed runs using real, same-model node usage."""

import argparse
import json
import sys
from pathlib import Path

import usage_metrics
from runtime_utils import emit_json, write_json_atomic


def percent(delta, baseline):
    if baseline == 0:
        return None
    return round(delta * 100 / baseline, 2)


def compare(baseline_dir, candidate_dir):
    baseline = usage_metrics.run_summary(baseline_dir)
    candidate = usage_metrics.run_summary(candidate_dir)
    errors = []
    if baseline["dataset_fingerprint"] != candidate["dataset_fingerprint"]:
        errors.append("dataset fingerprints differ")
    if baseline["models"] != candidate["models"]:
        errors.append("semantic node provider/model sets differ")
    if baseline["missing_real_usage"]:
        errors.append(
            "baseline lacks host-reported usage for: "
            + ", ".join(baseline["missing_real_usage"])
        )
    if candidate["missing_real_usage"]:
        errors.append(
            "candidate lacks host-reported usage for: "
            + ", ".join(candidate["missing_real_usage"])
        )
    if baseline["run_usage"] is None:
        errors.append("baseline lacks host-reported aggregate run usage")
    if candidate["run_usage"] is None:
        errors.append("candidate lacks host-reported aggregate run usage")
    baseline_run_model = (
        {
            "provider": baseline["run_usage"]["usage"]["provider"],
            "model": baseline["run_usage"]["usage"]["model"],
        }
        if baseline["run_usage"] is not None
        else None
    )
    candidate_run_model = (
        {
            "provider": candidate["run_usage"]["usage"]["provider"],
            "model": candidate["run_usage"]["usage"]["model"],
        }
        if candidate["run_usage"] is not None
        else None
    )
    if baseline_run_model != candidate_run_model:
        errors.append("aggregate run provider/model differs")
    quality_equal = baseline["quality"] == candidate["quality"]
    if not quality_equal:
        errors.append("quality signatures differ")
    nodes = sorted(
        set(baseline["node_totals"]) | set(candidate["node_totals"])
    )
    by_node = {}
    for node_id in nodes:
        before = baseline["node_totals"].get(node_id, {})
        after = candidate["node_totals"].get(node_id, {})
        node = {}
        for field in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "elapsed_ms",
            "invocations",
        ):
            old = before.get(field, 0)
            new = after.get(field, 0)
            delta = new - old
            node[field] = {
                "baseline": old,
                "candidate": new,
                "delta": delta,
                "delta_percent": percent(delta, old),
            }
        by_node[node_id] = node
    total_before = (
        baseline["run_usage"]["usage"]["total_tokens"]
        if baseline["run_usage"] is not None
        else 0
    )
    total_after = (
        candidate["run_usage"]["usage"]["total_tokens"]
        if candidate["run_usage"] is not None
        else 0
    )
    elapsed_before = (
        baseline["run_usage"]["elapsed_ms"]
        if baseline["run_usage"] is not None
        else 0
    )
    elapsed_after = (
        candidate["run_usage"]["elapsed_ms"]
        if candidate["run_usage"] is not None
        else 0
    )
    comparable = not errors
    return {
        "schema_version": 1,
        "comparable": comparable,
        "decision": (
            "candidate_measured"
            if comparable
            else "inconclusive"
        ),
        "errors": errors,
        "dataset_fingerprint": (
            baseline["dataset_fingerprint"]
            if baseline["dataset_fingerprint"]
            == candidate["dataset_fingerprint"]
            else None
        ),
        "models": baseline["models"] if baseline["models"] == candidate["models"] else None,
        "aggregate_model": (
            baseline_run_model
            if baseline_run_model == candidate_run_model
            else None
        ),
        "quality_equal": quality_equal,
        "total_tokens": {
            "baseline": total_before,
            "candidate": total_after,
            "delta": total_after - total_before,
            "delta_percent": percent(total_after - total_before, total_before),
        },
        "elapsed_ms": {
            "baseline": elapsed_before,
            "candidate": elapsed_after,
            "delta": elapsed_after - elapsed_before,
            "delta_percent": percent(elapsed_after - elapsed_before, elapsed_before),
        },
        "by_node": by_node,
        "baseline": baseline,
        "candidate": candidate,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-run", required=True)
    parser.add_argument("--candidate-run", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = compare(args.baseline_run, args.candidate_run)
        if args.output:
            write_json_atomic(Path(args.output), result)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        emit_json({"error": str(exc)}, sys.stderr)
        return 2
    emit_json(result)
    return 0 if result["comparable"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
