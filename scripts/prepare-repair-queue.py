#!/usr/bin/env python3
"""Inspect a managed run and write a compact incremental repair queue."""

import argparse
import sys
from pathlib import Path

from runtime_utils import emit_json, load_script, write_json_compact_atomic


SCRIPT_DIR = Path(__file__).resolve().parent
MANAGE_RUN = load_script(SCRIPT_DIR, "manage-run.py")
REPAIR = load_script(SCRIPT_DIR, "repair_queue.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    try:
        run_dir = MANAGE_RUN.checked_run_dir(args.run_dir)
        result = REPAIR.build(run_dir)
        output = run_dir / "repair-queue.json"
        write_json_compact_atomic(output, result)
    except (OSError, ValueError) as exc:
        emit_json({"error": str(exc)}, sys.stderr)
        return 2
    emit_json(
        {
            "repair_file": str(output),
            "repair_count": len(result["candidate_index"]),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
