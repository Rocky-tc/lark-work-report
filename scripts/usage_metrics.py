"""Private host-reported usage and comparable run summaries."""

import hashlib
import json
from pathlib import Path

import execution_graph
from runtime_utils import load_script, read_json, write_json_compact_atomic
from value_contracts import require_text


TOKEN_FIELDS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
}
REQUIRED_TOKEN_FIELDS = {"input_tokens", "output_tokens", "total_tokens"}
USAGE_FILE_FIELDS = {
    "schema_version",
    "provider",
    "model",
    *TOKEN_FIELDS,
}
METRICS_FILE = "stage-metrics.json"
RUN_USAGE_FILE = "run-usage.json"
SCRIPT_DIR = Path(__file__).resolve().parent
REPORT_HYDRATION = load_script(SCRIPT_DIR, "report_hydration.py")
VALIDATE_REPORT = load_script(SCRIPT_DIR, "validate-report.py")


def strict_object(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(f"{label} contains unknown fields: {', '.join(unknown)}")
    return value


def validate_tokens(value, label="usage", require_identity=False):
    allowed = USAGE_FILE_FIELDS if require_identity else TOKEN_FIELDS
    strict_object(value, allowed, label)
    if require_identity:
        if value.get("schema_version") != 1:
            raise ValueError(f"{label}.schema_version must be 1")
        require_text(value.get("provider"), f"{label}.provider")
        require_text(value.get("model"), f"{label}.model")
        missing = sorted(REQUIRED_TOKEN_FIELDS - set(value))
        if missing:
            raise ValueError(f"{label} is missing: {', '.join(missing)}")
    normalized = dict(value)
    for field in TOKEN_FIELDS:
        if field not in value:
            continue
        amount = value[field]
        if (
            not isinstance(amount, int)
            or isinstance(amount, bool)
            or amount < 0
        ):
            raise ValueError(f"{label}.{field} must be non-negative")
    if (
        REQUIRED_TOKEN_FIELDS <= set(value)
        and value["total_tokens"] != value["input_tokens"] + value["output_tokens"]
    ):
        raise ValueError(f"{label}.total_tokens is inconsistent")
    if (
        "cached_input_tokens" in value
        and "input_tokens" in value
        and value["cached_input_tokens"] > value["input_tokens"]
    ):
        raise ValueError(f"{label}.cached_input_tokens exceeds input_tokens")
    return normalized


def load_host_usage(run_dir, usage_file):
    if usage_file is None:
        return None
    run_dir = Path(run_dir).resolve()
    path = Path(usage_file)
    if path.is_symlink():
        raise ValueError("usage file cannot be a symlink")
    path = path.resolve(strict=True)
    try:
        path.relative_to(run_dir)
    except ValueError as exc:
        raise ValueError("usage file must remain inside the managed run") from exc
    return validate_tokens(
        read_json(path, "host usage"),
        "host usage",
        require_identity=True,
    )


def append_metric(run_dir, entry):
    path = Path(run_dir) / METRICS_FILE
    if path.is_symlink():
        raise ValueError("stage metrics cannot be a symlink")
    if path.is_file():
        metrics = read_json(path, "stage metrics")
        if (
            not isinstance(metrics, dict)
            or metrics.get("schema_version") != 1
            or not isinstance(metrics.get("entries"), list)
        ):
            raise ValueError("stage metrics are invalid")
    else:
        metrics = {"schema_version": 1, "entries": []}
    invocation_id = entry.get("invocation_id") or entry.get("package_id")
    entries = [
        value
        for value in metrics["entries"]
        if not (
            isinstance(value, dict)
            and (value.get("invocation_id") or value.get("package_id"))
            == invocation_id
        )
    ]
    entries.append(entry)
    metrics["entries"] = entries
    write_json_compact_atomic(path, metrics)


def record_external_node(
    run_dir,
    node_id,
    invocation_id,
    usage,
    elapsed_ms,
    input_file=None,
    result_file=None,
):
    run_dir = Path(run_dir).resolve()
    plan = read_json(run_dir / "run-plan.json", "run plan")
    graph, graph_state = execution_graph.load_for_run(run_dir, plan)
    node = execution_graph.node_by_id(graph, node_id)
    if node["kind"] != "semantic":
        raise ValueError("node usage can only be recorded for semantic nodes")
    if not isinstance(elapsed_ms, int) or isinstance(elapsed_ms, bool) or elapsed_ms < 0:
        raise ValueError("elapsed_ms must be a non-negative integer")
    invocation_id = require_text(invocation_id, "invocation_id")
    entry = {
        "node_id": node_id,
        "invocation_id": invocation_id,
        "graph_id": graph_state["graph_id"],
        "graph_digest": graph_state["digest"],
        "elapsed_ms": elapsed_ms,
        "usage_source": "host_file",
        "usage": validate_tokens(usage, "host usage", require_identity=True),
    }
    for key, value in (("input_file", input_file), ("result_file", result_file)):
        if value is None:
            continue
        path = Path(value).resolve(strict=True)
        try:
            path.relative_to(run_dir)
        except ValueError as exc:
            raise ValueError(f"{key} must remain inside the managed run") from exc
        entry[f"{key}_digest"] = file_digest(path)
        entry[f"{key}_bytes"] = path.stat().st_size
    append_metric(run_dir, entry)
    return entry


def record_run_total(run_dir, usage, elapsed_ms):
    run_dir = Path(run_dir).resolve()
    if not isinstance(elapsed_ms, int) or isinstance(elapsed_ms, bool) or elapsed_ms < 0:
        raise ValueError("elapsed_ms must be a non-negative integer")
    plan = read_json(run_dir / "run-plan.json", "run plan")
    _, graph_state = execution_graph.load_for_run(run_dir, plan)
    report = run_dir / "report.md"
    if report.is_symlink() or not report.is_file() or report.stat().st_size == 0:
        raise ValueError("aggregate run usage requires a completed report")
    payload = {
        "schema_version": 1,
        "graph_id": graph_state["graph_id"],
        "graph_digest": graph_state["digest"],
        "elapsed_ms": elapsed_ms,
        "usage_source": "host_file",
        "usage": validate_tokens(
            usage,
            "host usage",
            require_identity=True,
        ),
    }
    write_json_compact_atomic(run_dir / RUN_USAGE_FILE, payload)
    return payload


def file_digest(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_digest(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def optional_json(run_dir, name):
    path = Path(run_dir) / name
    if path.is_file() and not path.is_symlink():
        return read_json(path, name)
    return None


def input_candidate_index(queue):
    if not isinstance(queue, dict) or not isinstance(
        queue.get("candidate_index"), dict
    ):
        return None
    semantic_fields = {
        "prefetch_relevance",
        "classification_reason",
        "retention_mode",
        "work_relevance",
    }
    return {
        key: {
            field: value
            for field, value in candidate.items()
            if field not in semantic_fields
        }
        for key, candidate in queue["candidate_index"].items()
        if isinstance(candidate, dict)
    }


def dataset_fingerprint(run_dir):
    run_dir = Path(run_dir)
    plan = read_json(run_dir / "run-plan.json", "run plan")
    queue = optional_json(run_dir, "fetch-queue.json")
    inputs = {
        "period": plan.get("period"),
        "requested_domains": plan.get("requested_domains"),
        "request": optional_json(run_dir, "request-context.json"),
        "identity": optional_json(run_dir, "identity.json"),
        "template": optional_json(run_dir, "template-profile.json"),
        "candidate_index": input_candidate_index(queue),
        "semantic_bodies": [
            read_json(path, path.name)
            for path in sorted((run_dir / "semantic-bodies").glob("*.json"))
            if path.is_file() and not path.is_symlink()
        ],
    }
    return canonical_digest(inputs)


def quality_signature(run_dir):
    run_dir = Path(run_dir)
    ledger = optional_json(run_dir, "ledger.json") or {}
    audit = optional_json(run_dir, "extraction-audit.json") or {}
    report = run_dir / "report.md"
    model = optional_json(run_dir, "report-model.json")
    if isinstance(model, dict) and model.get("schema_version") in {4, 5}:
        model = REPORT_HYDRATION.hydrate(
            model,
            ledger,
            optional_json(run_dir, "run-plan.json"),
            optional_json(run_dir, "identity.json"),
            audit,
            allow_direct_fill=optional_json(
                run_dir,
                "template-profile.json",
            )
            is None,
        )
    source_refs = []
    for group in ("work", "uncertain"):
        for item in ledger.get(group, []) if isinstance(ledger, dict) else []:
            sources = (
                item.get("source_refs", item.get("sources", []))
                if isinstance(item, dict)
                else []
            )
            for source in sources:
                if isinstance(source, str) and source:
                    source_refs.append(source)
                elif isinstance(source, dict) and source.get("source_ref"):
                    source_refs.append(source["source_ref"])
    return {
        "complete": report.is_file() and report.stat().st_size > 0,
        "work_count": len(ledger.get("work", [])) if isinstance(ledger, dict) else 0,
        "uncertain_count": (
            len(ledger.get("uncertain", [])) if isinstance(ledger, dict) else 0
        ),
        "outcome_counts": audit.get("outcome_counts"),
        "source_refs": sorted(set(source_refs)),
        "field_obligations": VALIDATE_REPORT.field_obligation_signature(
            model,
            ledger,
        ),
    }


def run_summary(run_dir):
    run_dir = Path(run_dir)
    plan = read_json(run_dir / "run-plan.json", "run plan")
    _, graph_state = execution_graph.load_for_run(run_dir, plan)
    metrics = read_json(run_dir / METRICS_FILE, "stage metrics")
    entries = metrics.get("entries")
    if not isinstance(entries, list):
        raise ValueError("stage metrics entries are invalid")
    node_totals = {}
    models = {}
    missing_real_usage = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("stage metrics entry is invalid")
        node_id = entry.get("node_id") or entry.get("stage")
        if not isinstance(node_id, str):
            raise ValueError("stage metrics entry lacks node_id")
        usage = entry.get("usage")
        if usage is None:
            if node_id in execution_graph.SEMANTIC_NODES:
                missing_real_usage.append(node_id)
            continue
        real = (
            entry.get("usage_source") == "host_file"
            and isinstance(usage.get("provider"), str)
            and isinstance(usage.get("model"), str)
        )
        if node_id in execution_graph.SEMANTIC_NODES and not real:
            missing_real_usage.append(node_id)
        validated = validate_tokens(
            usage,
            "stage metrics usage",
            require_identity=real,
        )
        totals = node_totals.setdefault(
            node_id,
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "elapsed_ms": 0,
                "invocations": 0,
            },
        )
        for field in REQUIRED_TOKEN_FIELDS:
            totals[field] += validated.get(field, 0)
        totals["elapsed_ms"] += entry.get("elapsed_ms", 0)
        totals["invocations"] += 1
        if real:
            models.setdefault(node_id, set()).add(
                (usage["provider"], usage["model"])
            )
    required_real_nodes = {"classify", "synthesize"}
    semantic_dir = run_dir / "semantic-bodies"
    if semantic_dir.is_dir() and any(semantic_dir.glob("*.json")):
        required_real_nodes.add("extract")
    missing_real_usage.extend(required_real_nodes - set(models))
    run_usage = optional_json(run_dir, RUN_USAGE_FILE)
    if run_usage is not None:
        strict_object(
            run_usage,
            {
                "schema_version",
                "graph_id",
                "graph_digest",
                "elapsed_ms",
                "usage_source",
                "usage",
            },
            "run usage",
        )
        if (
            run_usage.get("schema_version") != 1
            or run_usage.get("usage_source") != "host_file"
            or run_usage.get("graph_id") != graph_state["graph_id"]
            or run_usage.get("graph_digest") != graph_state["digest"]
        ):
            raise ValueError("run usage is invalid")
        run_usage["usage"] = validate_tokens(
            run_usage.get("usage"),
            "run usage.usage",
            require_identity=True,
        )
    return {
        "run_dir": str(run_dir.resolve()),
        "dataset_fingerprint": dataset_fingerprint(run_dir),
        "quality": quality_signature(run_dir),
        "node_totals": node_totals,
        "models": {
            key: sorted([{"provider": p, "model": m} for p, m in values],
                        key=lambda value: (value["provider"], value["model"]))
            for key, values in models.items()
        },
        "missing_real_usage": sorted(set(missing_real_usage)),
        "run_usage": run_usage,
    }
