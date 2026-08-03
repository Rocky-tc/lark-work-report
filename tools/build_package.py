#!/usr/bin/env python3
"""Build a deterministic runtime-only Skill ZIP."""

import argparse
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOTS = (
    ROOT / "SKILL.md",
    ROOT / "agents",
    ROOT / "references",
    ROOT / "scripts",
)
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def runtime_files():
    files = []
    for root in RUNTIME_ROOTS:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(
                path
                for path in root.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            )
    return sorted(files, key=lambda path: path.relative_to(ROOT).as_posix())


def build(output):
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in runtime_files():
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(
                f"lark-work-report/{relative}",
                date_time=FIXED_TIME,
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dist/lark-work-report.zip")
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    print(build(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
