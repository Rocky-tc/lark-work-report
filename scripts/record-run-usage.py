#!/usr/bin/env python3
"""Record host-reported aggregate usage for the complete workflow."""

import argparse
import sys

import usage_metrics
from runtime_utils import emit_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--usage-file", required=True)
    parser.add_argument("--elapsed-ms", required=True, type=int)
    args = parser.parse_args()
    try:
        usage = usage_metrics.load_host_usage(args.run_dir, args.usage_file)
        result = usage_metrics.record_run_total(
            args.run_dir,
            usage,
            args.elapsed_ms,
        )
    except (OSError, ValueError) as exc:
        emit_json({"error": str(exc)}, sys.stderr)
        return 2
    emit_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
