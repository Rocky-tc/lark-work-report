"""Static, reviewable execution graph for one personal report run."""

import hashlib
import json
from pathlib import Path

from runtime_utils import read_json, write_json_compact_atomic


SCHEMA_VERSION = 1
GRAPH_ID = "lark-work-report-v1"
GRAPH_FILE = "execution-graph.json"
SEMANTIC_NODES = ("classify", "extract", "synthesize")
STAGE_ORDER = ("fetch", "extract", "compile", "synthesize", "finalize", "complete")
STAGE_NODE = {
    "fetch": "fetch",
    "extract": "extract",
    "synthesize": "synthesize",
}
KINDS = {"deterministic", "adapter", "semantic", "terminal"}
CARDINALITIES = {"once", "optional", "repeat"}


def canonical_digest(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def node(node_id, kind, cardinality, handler, **fields):
    return {
        "id": node_id,
        "kind": kind,
        "cardinality": cardinality,
        "handler": handler,
        **fields,
    }


def build(plan):
    """Build the fixed graph with only per-run activation metadata."""
    metadata_waves = plan.get("metadata_waves", [])
    template_active = plan.get("template_call") is not None
    identity_active = plan.get("identity_call") is not None
    graph = {
        "schema_version": SCHEMA_VERSION,
        "graph_id": GRAPH_ID,
        "nodes": [
            node("prepare", "deterministic", "once", "prepare-run"),
            node(
                "identity",
                "adapter",
                "optional",
                "identity.current",
                active=identity_active,
                max_invocations=1 if identity_active else 0,
            ),
            node(
                "template",
                "adapter",
                "optional",
                "template.fetch",
                active=template_active,
                max_invocations=1 if template_active else 0,
            ),
            node(
                "metadata",
                "adapter",
                "repeat",
                "candidate.list",
                active=bool(metadata_waves),
                planned_waves=len(metadata_waves),
                parallel_by_wave=[
                    bool(wave.get("parallel")) for wave in metadata_waves
                ],
            ),
            node(
                "classify",
                "semantic",
                "once",
                "prepare-fetch-queue",
                contract="classification-contract.md",
                context_mode="current",
                max_invocations=1,
            ),
            node(
                "fetch",
                "adapter",
                "repeat",
                "stage-io.fetch",
                contract="fetch-contract.md",
                batching="adapter",
            ),
            node(
                "extract",
                "semantic",
                "repeat",
                "stage-io.extract",
                contract="extraction-contract.md",
                context_mode="fresh_if_explicit",
                batching="bounded",
                max_items=100,
                max_bytes=4 * 1024 * 1024,
            ),
            node(
                "compile",
                "deterministic",
                "once",
                "compile-evidence",
            ),
            node(
                "synthesize",
                "semantic",
                "once",
                "stage-io.synthesize",
                contract="synthesis-contract.md",
                context_mode="fresh_if_explicit",
                max_invocations=1,
            ),
            node(
                "finalize",
                "deterministic",
                "once",
                "finalize-run",
            ),
            node(
                "deliver",
                "adapter",
                "optional",
                "report.create+report.fetch",
                active=any(
                    adapter.get("delivery_mode") == "document"
                    for adapter in plan.get("adapters", [])
                    if isinstance(adapter, dict)
                ),
                max_invocations=(
                    2
                    if any(
                        adapter.get("delivery_mode") == "document"
                        for adapter in plan.get("adapters", [])
                        if isinstance(adapter, dict)
                    )
                    else 0
                ),
            ),
            node("complete", "terminal", "once", "return-report"),
        ],
        "edges": [
            {"from": "prepare", "to": "identity", "when": "identity.active"},
            {"from": "prepare", "to": "template", "when": "not identity.active"},
            {"from": "identity", "to": "template", "when": "template.active"},
            {"from": "identity", "to": "metadata", "when": "not template.active"},
            {"from": "template", "to": "metadata", "when": "always"},
            {"from": "metadata", "to": "metadata", "when": "more_metadata_waves"},
            {"from": "metadata", "to": "classify", "when": "metadata_complete"},
            {"from": "classify", "to": "fetch", "when": "fetch_queue_ready"},
            {"from": "fetch", "to": "fetch", "when": "pending_fetch_wave"},
            {"from": "fetch", "to": "extract", "when": "fetch_complete"},
            {"from": "extract", "to": "extract", "when": "pending_extract_batch"},
            {"from": "extract", "to": "compile", "when": "extraction_complete"},
            {"from": "compile", "to": "synthesize", "when": "ledger_ready"},
            {"from": "synthesize", "to": "finalize", "when": "model_ready"},
            {"from": "finalize", "to": "deliver", "when": "deliver.active"},
            {"from": "finalize", "to": "complete", "when": "not deliver.active"},
            {"from": "deliver", "to": "complete", "when": "delivery_verified"},
        ],
        "stage_runtime_order": list(STAGE_ORDER),
        "invariants": {
            "semantic_nodes": list(SEMANTIC_NODES),
            "batch_extraction": True,
            "synthesis_max_invocations": 1,
            "managed_files": True,
            "short_references": True,
            "deterministic_hydration": True,
            "incremental_repair": True,
        },
    }
    validate(graph)
    return graph


def validate(graph):
    if not isinstance(graph, dict) or graph.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("execution graph schema_version must be 1")
    if graph.get("graph_id") != GRAPH_ID:
        raise ValueError("execution graph graph_id is invalid")
    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("execution graph requires nodes")
    node_map = {}
    for index, value in enumerate(nodes):
        if not isinstance(value, dict):
            raise ValueError(f"execution graph node {index} must be an object")
        node_id = value.get("id")
        if not isinstance(node_id, str) or not node_id or node_id in node_map:
            raise ValueError("execution graph contains an invalid or duplicate node")
        if value.get("kind") not in KINDS:
            raise ValueError(f"execution graph node kind is invalid: {node_id}")
        if value.get("cardinality") not in CARDINALITIES:
            raise ValueError(f"execution graph cardinality is invalid: {node_id}")
        if not isinstance(value.get("handler"), str) or not value["handler"]:
            raise ValueError(f"execution graph handler is invalid: {node_id}")
        node_map[node_id] = value
    semantic = tuple(
        value["id"] for value in nodes if value.get("kind") == "semantic"
    )
    if semantic != SEMANTIC_NODES:
        raise ValueError("execution graph changes the semantic node set")
    synthesize = node_map["synthesize"]
    if (
        synthesize.get("cardinality") != "once"
        or synthesize.get("max_invocations") != 1
    ):
        raise ValueError("execution graph must keep one synthesis invocation")
    extract = node_map["extract"]
    if extract.get("batching") != "bounded":
        raise ValueError("execution graph must keep bounded batch extraction")
    if tuple(graph.get("stage_runtime_order", [])) != STAGE_ORDER:
        raise ValueError("execution graph stage runtime order is invalid")
    edges = graph.get("edges")
    if not isinstance(edges, list):
        raise ValueError("execution graph requires edges")
    for edge in edges:
        if (
            not isinstance(edge, dict)
            or edge.get("from") not in node_map
            or edge.get("to") not in node_map
            or not isinstance(edge.get("when"), str)
            or not edge["when"]
        ):
            raise ValueError("execution graph contains an invalid edge")
    invariants = graph.get("invariants")
    if not isinstance(invariants, dict):
        raise ValueError("execution graph requires invariants")
    expected = {
        "semantic_nodes": list(SEMANTIC_NODES),
        "batch_extraction": True,
        "synthesis_max_invocations": 1,
        "managed_files": True,
        "short_references": True,
        "deterministic_hydration": True,
        "incremental_repair": True,
    }
    if invariants != expected:
        raise ValueError("execution graph invariants are invalid")
    return graph


def write(run_dir, graph):
    validate(graph)
    path = Path(run_dir) / GRAPH_FILE
    write_json_compact_atomic(path, graph)
    return {
        "file": GRAPH_FILE,
        "digest": canonical_digest(graph),
        "graph_id": GRAPH_ID,
    }


def load_for_run(run_dir, plan=None):
    """Load and verify a graph, with a compatibility graph for older runs."""
    run_dir = Path(run_dir).resolve()
    plan = plan or read_json(run_dir / "run-plan.json", "run plan")
    descriptor = plan.get("execution_graph")
    if descriptor is None:
        graph = build(plan)
        return graph, {
            "graph_id": GRAPH_ID,
            "digest": canonical_digest(graph),
            "mode": "compat",
            "file": None,
        }
    if not isinstance(descriptor, dict):
        raise ValueError("run plan execution_graph is invalid")
    if set(descriptor) != {"file", "digest", "graph_id"}:
        raise ValueError("run plan execution_graph fields are invalid")
    if descriptor.get("graph_id") != GRAPH_ID:
        raise ValueError("run plan execution_graph graph_id is invalid")
    relative = Path(descriptor.get("file", ""))
    if relative.is_absolute():
        raise ValueError("execution graph file must be relative")
    raw_path = run_dir / relative
    if raw_path.is_symlink():
        raise ValueError("execution graph file cannot be a symlink")
    path = raw_path.resolve()
    try:
        path.relative_to(run_dir)
    except ValueError as exc:
        raise ValueError("execution graph file escapes the run directory") from exc
    if not path.is_file():
        raise ValueError("execution graph file is missing")
    graph = validate(read_json(path, "execution graph"))
    digest = canonical_digest(graph)
    if digest != descriptor.get("digest"):
        raise ValueError("execution graph digest is stale or invalid")
    return graph, {
        "graph_id": GRAPH_ID,
        "digest": digest,
        "mode": "static",
        "file": str(path),
    }


def node_by_id(graph, node_id):
    for value in graph["nodes"]:
        if value["id"] == node_id:
            return value
    raise ValueError(f"execution graph node is missing: {node_id}")
