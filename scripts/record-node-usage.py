#!/usr/bin/env python3
"""Record host-reported usage for a semantic graph node."""

import argparse
import sys
from pathlib import Path

import usage_metrics
from runtime_utils import emit_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--invocation-id", required=True)
    parser.add_argument("--usage-file", required=True)
    parser.add_argument("--elapsed-ms", required=True, type=int)
    parser.add_argument("--input-file")
    parser.add_argument("--result-file")
    args = parser.parse_args()
    try:
        usage = usage_metrics.load_host_usage(args.run_dir, args.usage_file)
        result = usage_metrics.record_external_node(
            args.run_dir,
            args.node_id,
            args.invocation_id,
            usage,
            args.elapsed_ms,
            input_file=args.input_file,
            result_file=args.result_file,
        )
    except (OSError, ValueError) as exc:
        emit_json({"error": str(exc)}, sys.stderr)
        return 2
    emit_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
