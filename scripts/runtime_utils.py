"""Small shared helpers for workflow scripts."""

import importlib.util
import json
import sys
from pathlib import Path


def load_script(script_dir, name):
    path = Path(script_dir) / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load workflow module: {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path, label):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc


def write_json_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.next")
    pending.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    pending.replace(path)


def write_text_atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.next")
    pending.write_text(value, encoding="utf-8")
    pending.replace(path)


def emit_json(payload, stream=None):
    print(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        file=stream or sys.stdout,
    )
