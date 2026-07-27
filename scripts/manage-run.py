#!/usr/bin/env python3
"""Create, meter, inspect, and safely clean an ephemeral report run."""

import argparse
import json
import re
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


PREFIX = "lark-work-report-"
STATE_FILE = ".run-state.json"
HARD_LIMITS = {
    "daily": 18,
    "weekly": 32,
    "monthly_cached": 25,
    "monthly_uncached": 45,
    "deep": 50,
}
METER_LABEL = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def now():
    return datetime.now(timezone.utc).isoformat()


def write_state(run_dir, state):
    pending = run_dir / ".run-state.next"
    pending.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    pending.replace(run_dir / STATE_FILE)


def create(profile):
    run_dir = Path(tempfile.mkdtemp(prefix=PREFIX)).resolve()
    state = {
        "schema_version": 1,
        "run_id": str(uuid.uuid4()),
        "retention_mode": "ephemeral",
        "profile": profile,
        "hard_limit": HARD_LIMITS[profile],
        "call_count": 0,
        "calls": [],
        "created_at": now(),
    }
    write_state(run_dir, state)
    return {"run_dir": str(run_dir), **state}


def checked_run_dir(value):
    path = Path(value)
    if path.is_symlink():
        raise ValueError("run directory cannot be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"run directory does not exist: {value}") from exc
    temp_root = Path(tempfile.gettempdir()).resolve()
    if resolved.parent != temp_root or not resolved.name.startswith(PREFIX):
        raise ValueError("refusing path outside the managed temporary run root")
    state_path = resolved / STATE_FILE
    if state_path.is_symlink() or not state_path.is_file():
        raise ValueError("managed run marker is missing")
    return resolved


def read_state(run_dir):
    try:
        state = json.loads((run_dir / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("managed run state is unreadable") from exc
    if state.get("retention_mode") != "ephemeral":
        raise ValueError("managed run marker has an invalid retention mode")
    if state.get("schema_version") != 1:
        raise ValueError("managed run marker has an invalid schema version")
    profile = state.get("profile")
    if profile not in HARD_LIMITS:
        raise ValueError("managed run marker has an invalid profile")
    if state.get("hard_limit") != HARD_LIMITS[profile]:
        raise ValueError("managed run marker has an invalid hard limit")
    run_id = state.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("managed run marker has an invalid run id")
    call_count = state.get("call_count")
    calls = state.get("calls")
    if (
        not isinstance(call_count, int)
        or isinstance(call_count, bool)
        or call_count < 0
        or call_count > state["hard_limit"]
    ):
        raise ValueError("managed run marker has an invalid call count")
    if not isinstance(calls, list) or len(calls) != call_count:
        raise ValueError("managed run marker has an invalid call ledger")
    return state


def record_call(run_dir_value, domain, operation):
    return record_batch(run_dir_value, domain, operation, 1)


def record_batch(run_dir_value, domain, operation, count):
    if not METER_LABEL.fullmatch(domain):
        raise ValueError("domain must be a short machine label")
    if not METER_LABEL.fullmatch(operation):
        raise ValueError("operation must be a short machine label")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 50:
        raise ValueError("count must be an integer between 1 and 50")
    run_dir = checked_run_dir(run_dir_value)
    state = read_state(run_dir)
    if state["call_count"] + count > state["hard_limit"]:
        raise ValueError(
            f"external call hard limit would be exceeded: {state['hard_limit']}"
        )
    recorded_at = now()
    for _ in range(count):
        state["call_count"] += 1
        state["calls"].append(
            {
                "sequence": state["call_count"],
                "domain": domain,
                "operation": operation,
                "recorded_at": recorded_at,
            }
        )
    write_state(run_dir, state)
    return {
        "run_dir": str(run_dir),
        "recorded": count,
        "call_count": state["call_count"],
        "hard_limit": state["hard_limit"],
        "remaining": state["hard_limit"] - state["call_count"],
    }


def status(run_dir_value):
    run_dir = checked_run_dir(run_dir_value)
    state = read_state(run_dir)
    return {
        "run_dir": str(run_dir),
        **state,
        "remaining": state["hard_limit"] - state["call_count"],
    }


def cleanup(run_dir_value):
    run_dir = checked_run_dir(run_dir_value)
    state = read_state(run_dir)
    result = {
        "run_dir": str(run_dir),
        "run_id": state["run_id"],
        "call_count": state["call_count"],
        "cleaned": True,
    }
    shutil.rmtree(run_dir)
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--profile", required=True, choices=tuple(HARD_LIMITS))

    record_parser = subparsers.add_parser("record-call")
    record_parser.add_argument("--run-dir", required=True)
    record_parser.add_argument("--domain", required=True)
    record_parser.add_argument("--operation", required=True)

    batch_parser = subparsers.add_parser("record-batch")
    batch_parser.add_argument("--run-dir", required=True)
    batch_parser.add_argument("--domain", required=True)
    batch_parser.add_argument("--operation", required=True)
    batch_parser.add_argument("--count", required=True, type=int)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--run-dir", required=True)

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--run-dir", required=True)
    return parser


def main():
    args = build_parser().parse_args()
    try:
        if args.command == "create":
            result = create(args.profile)
        elif args.command == "record-call":
            result = record_call(args.run_dir, args.domain, args.operation)
        elif args.command == "record-batch":
            result = record_batch(
                args.run_dir, args.domain, args.operation, args.count
            )
        elif args.command == "status":
            result = status(args.run_dir)
        else:
            result = cleanup(args.run_dir)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
