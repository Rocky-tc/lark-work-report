#!/usr/bin/env python3
"""Validate and optionally normalize a personal report template profile."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from runtime_utils import write_json_atomic
from template_profiles import load


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--file")
    source_group.add_argument("--fingerprint-source")
    parser.add_argument("--output")
    parser.add_argument("--verify-file")
    args = parser.parse_args()
    try:
        if args.fingerprint_source:
            if args.output or args.verify_file:
                raise ValueError("--output and --verify-file require --file")
            visible = Path(args.fingerprint_source).read_text(encoding="utf-8")
            normalized = " ".join(visible.split())
            fingerprint = "sha256:" + hashlib.sha256(
                normalized.encode("utf-8")
            ).hexdigest()
            print(
                json.dumps(
                    {"ok": True, "source_fingerprint": fingerprint},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        profile = load(args.file)
        if args.verify_file:
            fetched = load(args.verify_file)
            if fetched != profile:
                raise ValueError(
                    "fetched template profile does not match the normalized source"
                )
        if args.output:
            output = Path(args.output)
            if output.resolve() == Path(args.file).resolve():
                raise ValueError("output path must differ from input template path")
            write_json_atomic(output, profile)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    result = {
        "ok": True,
        "template_id": profile["template_id"],
        "source_fingerprint": profile["source_fingerprint"],
        "profiles": ["daily", "weekly", "monthly"],
        "output": args.output,
        "verified": bool(args.verify_file),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
