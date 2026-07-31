#!/usr/bin/env python3
"""Expose the two-entry stage-io interface: next and commit."""

import argparse
import sys
from pathlib import Path

from runtime_utils import emit_json, load_script


SCRIPT_DIR = Path(__file__).resolve().parent
STAGE_IO = load_script(SCRIPT_DIR, "stage_io.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    next_parser = commands.add_parser("next")
    next_parser.add_argument("--run-dir", required=True)
    commit_parser = commands.add_parser("commit")
    commit_parser.add_argument("--run-dir", required=True)
    commit_parser.add_argument("--result-file", required=True)
    args = parser.parse_args()
    try:
        if args.command == "next":
            result = STAGE_IO.next_stage(args.run_dir)
        else:
            result = STAGE_IO.commit_stage(args.run_dir, args.result_file)
    except (OSError, ValueError) as exc:
        emit_json({"error": str(exc)}, sys.stderr)
        return 2
    emit_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
