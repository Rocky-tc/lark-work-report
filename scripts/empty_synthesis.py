"""Deterministic empty-ledger synthesis model and integrity receipt."""

import hashlib
from pathlib import Path

import execution_graph
import report_hydration
from runtime_utils import read_json, write_json_compact_atomic


RECEIPT_FILE = "stage-receipts/synthesize-empty-ledger.json"
RECEIPT_SCHEMA_VERSION = 1
MANDATORY_GUARDS = (
    "ledger.json",
    "run-plan.json",
    "extraction-audit.json",
)
OPTIONAL_GUARDS = (
    "identity.json",
    "template-profile.json",
    "request-context.json",
)
RECEIPT_FIELDS = {
    "schema_version",
    "node_id",
    "outcome",
    "reason",
    "graph_id",
    "graph_digest",
    "ledger_fingerprint",
    "guards",
    "report_model_digest",
    "report_digest",
}


def file_digest(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked_file(run_dir, value, label, *, must_exist):
    run_dir = Path(run_dir).resolve()
    path = Path(value)
    if not path.is_absolute():
        path = run_dir / path
    if path.is_symlink():
        raise ValueError(f"empty synthesis {label} cannot be a symlink")
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as exc:
        raise ValueError(f"empty synthesis {label} is missing") from exc
    try:
        resolved.relative_to(run_dir)
    except ValueError as exc:
        raise ValueError(
            f"empty synthesis {label} must remain inside the managed run"
        ) from exc
    if resolved.exists() and not resolved.is_file():
        raise ValueError(f"empty synthesis {label} must be a regular file")
    if must_exist and not resolved.is_file():
        raise ValueError(f"empty synthesis {label} is missing")
    return resolved


def strict_empty_ledger(ledger):
    report_hydration.ledger_index(ledger)
    return ledger.get("work") == [] and ledger.get("uncertain") == []


def is_eligible(graph, ledger):
    """Return true only for an explicit graph opt-in and a strict empty ledger."""
    if not execution_graph.supports_empty_synthesis_bypass(graph):
        return False
    return strict_empty_ledger(ledger)


def canonical_model(ledger):
    """Build the only report model allowed on the deterministic bypass."""
    if not strict_empty_ledger(ledger):
        raise ValueError("deterministic synthesis requires an empty work/uncertain ledger")
    return {
        "summary": [],
        "workstreams": [],
        "risks": [],
        "next_actions": [],
        "uncertain": [],
        # Keep the same insertion order as commit_synthesize() so the private
        # model bytes remain stable across the legacy and bypass paths.
        "schema_version": 5,
        "ledger_fingerprint": report_hydration.ledger_fingerprint(ledger),
    }


def ensure_model(run_dir, graph, ledger):
    """Atomically create, or exactly verify, the canonical empty model."""
    if not is_eligible(graph, ledger):
        raise ValueError("execution graph does not allow empty synthesis bypass")
    run_dir = Path(run_dir).resolve()
    path = checked_file(
        run_dir,
        "report-model.json",
        "report model",
        must_exist=False,
    )
    expected = canonical_model(ledger)
    if path.exists():
        if not path.is_file() or read_json(path, "report model") != expected:
            raise ValueError(
                "existing report model does not match deterministic empty synthesis"
            )
    else:
        write_json_compact_atomic(path, expected)
    return expected


def validate_graph_state(graph, graph_state):
    if not isinstance(graph_state, dict):
        raise ValueError("empty synthesis graph state is invalid")
    expected_digest = execution_graph.canonical_digest(graph)
    if (
        graph_state.get("mode") != "static"
        or not graph_state.get("file")
        or graph_state.get("graph_id") != graph.get("graph_id")
        or graph_state.get("digest") != expected_digest
    ):
        raise ValueError("empty synthesis graph state is stale or invalid")


def guard_paths(run_dir, graph_state):
    run_dir = Path(run_dir).resolve()
    values = [(name, True) for name in MANDATORY_GUARDS]
    values.extend((name, False) for name in OPTIONAL_GUARDS)
    graph_file = graph_state.get("file")
    if graph_file is not None:
        graph_path = checked_file(
            run_dir,
            graph_file,
            "execution graph",
            must_exist=True,
        )
        values.append((graph_path.relative_to(run_dir).as_posix(), True))
    return values


def guard_entries(run_dir, graph_state):
    result = []
    for relative, required in guard_paths(run_dir, graph_state):
        path = checked_file(
            run_dir,
            relative,
            f"guard {relative}",
            must_exist=required,
        )
        exists = path.is_file()
        if required and not exists:
            raise ValueError(f"empty synthesis guard is missing: {relative}")
        result.append(
            {
                "path": relative,
                "digest": file_digest(path) if exists else None,
            }
        )
    return result


def expected_receipt(run_dir, graph, graph_state):
    run_dir = Path(run_dir).resolve()
    execution_graph.validate(graph)
    validate_graph_state(graph, graph_state)
    ledger_path = checked_file(
        run_dir,
        "ledger.json",
        "ledger",
        must_exist=True,
    )
    ledger = read_json(ledger_path, "evidence ledger")
    if not is_eligible(graph, ledger):
        raise ValueError("empty synthesis receipt is not eligible")
    model_path = checked_file(
        run_dir,
        "report-model.json",
        "report model",
        must_exist=True,
    )
    model = read_json(model_path, "report model")
    if model != canonical_model(ledger):
        raise ValueError("empty synthesis receipt report model is not canonical")
    report_path = checked_file(
        run_dir,
        "report.md",
        "report",
        must_exist=True,
    )
    if report_path.stat().st_size == 0:
        raise ValueError("empty synthesis receipt report is empty")
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "node_id": "synthesize",
        "outcome": "bypassed",
        "reason": "empty_ledger",
        "graph_id": graph_state["graph_id"],
        "graph_digest": graph_state["digest"],
        "ledger_fingerprint": report_hydration.ledger_fingerprint(ledger),
        "guards": guard_entries(run_dir, graph_state),
        "report_model_digest": file_digest(model_path),
        "report_digest": file_digest(report_path),
    }


def write_receipt(run_dir, graph, graph_state):
    """Write the private proof after the normal finalizer succeeds."""
    run_dir = Path(run_dir).resolve()
    expected = expected_receipt(run_dir, graph, graph_state)
    path = checked_file(
        run_dir,
        RECEIPT_FILE,
        "receipt",
        must_exist=False,
    )
    if path.exists():
        if not path.is_file() or read_json(path, "empty synthesis receipt") != expected:
            raise ValueError("existing empty synthesis receipt is stale or invalid")
    else:
        write_json_compact_atomic(path, expected)
    return expected


def verify_receipt(run_dir, graph, graph_state):
    """Return normalized proof metadata; absent is None and invalid fails closed."""
    run_dir = Path(run_dir).resolve()
    path = checked_file(
        run_dir,
        RECEIPT_FILE,
        "receipt",
        must_exist=False,
    )
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError("empty synthesis receipt is invalid")
    actual = read_json(path, "empty synthesis receipt")
    if not isinstance(actual, dict) or set(actual) != RECEIPT_FIELDS:
        raise ValueError("empty synthesis receipt fields are invalid")
    expected = expected_receipt(run_dir, graph, graph_state)
    if actual != expected:
        raise ValueError("empty synthesis receipt is stale or invalid")
    return {
        "node_id": actual["node_id"],
        "reason": actual["reason"],
        "graph_id": actual["graph_id"],
        "graph_digest": actual["graph_digest"],
        "ledger_fingerprint": actual["ledger_fingerprint"],
        "report_digest": actual["report_digest"],
        "receipt_digest": file_digest(path),
    }
