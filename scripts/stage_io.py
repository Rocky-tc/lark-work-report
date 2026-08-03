"""Deep stage interface for compact Agent packets and deterministic hydration."""

import hashlib
import json
import re
import time
from pathlib import Path

import execution_graph
import empty_synthesis
import request_context
import usage_metrics
from runtime_utils import (
    load_script,
    read_json,
    write_json_compact_atomic,
)
from value_contracts import require_text


SCRIPT_DIR = Path(__file__).resolve().parent
MANAGE_RUN = load_script(SCRIPT_DIR, "manage-run.py")
COMPILE = load_script(SCRIPT_DIR, "compile-evidence.py")
NORMALIZE_BODY = load_script(SCRIPT_DIR, "normalize-fetch-body.py")
RECONCILE = load_script(SCRIPT_DIR, "reconcile-evidence.py")
REPORT_HYDRATION = load_script(SCRIPT_DIR, "report_hydration.py")
RENDER_REPORT = load_script(SCRIPT_DIR, "render-report.py")
VALIDATE_REPORT = load_script(SCRIPT_DIR, "validate-report.py")
FINALIZE = load_script(SCRIPT_DIR, "finalize-run.py")


PACKET_SCHEMA_VERSION = 1
CONTRACT_VERSION = 1
CONTRACT_FILES = {
    "fetch": "fetch-contract.md",
    "extract": "extraction-contract.md",
    "synthesize": "synthesis-contract.md",
}
EVIDENCE_OUTCOMES = {"work", "uncertain"}
DISCARD_OUTCOMES = {"discarded_private", "discarded_chatter"}
ALL_OUTCOMES = EVIDENCE_OUTCOMES | DISCARD_OUTCOMES | {"access_gap"}
PACKAGE_ID = re.compile(r"^(fetch|extract|synthesize)-[0-9a-f]{16}$")
IDENTITY_FIELDS = {
    "record_id",
    "source_type",
    "source_ref",
    "occurred_at",
    "work_relevance",
    "retention_mode",
}
CANDIDATE_FACT_FIELDS = (
    "starts_at",
    "ends_at",
    "due_at",
    "requires_response",
    "action_kind",
    "assignee_relation",
)
USAGE_FIELDS = {"input_tokens", "output_tokens", "total_tokens"}
EXTRACT_MAX_ITEMS = 100
EXTRACT_MAX_BYTES = 4 * 1024 * 1024
DOWNSTREAM_FILES = (
    "evidence-records.json",
    "ledger.json",
    "synthesis-view.json",
    "extraction-audit.json",
    "report-model.json",
    "report.md",
    empty_synthesis.RECEIPT_FILE,
    "repair-queue.json",
)
REPAIR_SECTIONS = tuple(REPORT_HYDRATION.SECTION_KINDS)


def canonical_digest(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def file_digest(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def model_packet(stage, payload):
    return {
        "schema_version": PACKET_SCHEMA_VERSION,
        "stage": stage,
        "contract_version": CONTRACT_VERSION,
        "payload": payload,
    }


def compact_json_bytes(value):
    return len(
        (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
    )


def checked_relative(run_dir, value, label):
    value = require_text(value, label)
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"{label} must be relative to the run directory")
    resolved = (run_dir / path).resolve()
    try:
        resolved.relative_to(run_dir)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the run directory") from exc
    return resolved


def strict_object(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unknown fields: {', '.join(unknown)}")
    return value


def load_queue(run_dir):
    payload = read_json(run_dir / "fetch-queue.json", "fetch queue")
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise ValueError("stage-io requires fetch queue schema_version=2")
    raw_index = payload.get("candidate_index")
    raw_batches = payload.get("fetch_batches")
    waves = payload.get("fetch_waves")
    if not isinstance(raw_index, dict):
        raise ValueError("fetch queue requires candidate_index")
    if not isinstance(raw_batches, list) or not isinstance(waves, list):
        raise ValueError("fetch queue requires fetch_batches and fetch_waves")
    candidates = {}
    for global_id, item in raw_index.items():
        if not isinstance(global_id, str) or not global_id:
            raise ValueError("candidate_index contains an invalid global_id")
        if not isinstance(item, dict) or "global_id" in item:
            raise ValueError(f"candidate_index[{global_id}] is invalid")
        candidates[global_id] = {"global_id": global_id, **item}
    batches = {}
    for index, batch in enumerate(raw_batches):
        label = f"fetch_batches[{index}]"
        if not isinstance(batch, dict):
            raise ValueError(f"{label} must be an object")
        batch_id = require_text(batch.get("batch_id"), f"{label}.batch_id")
        if batch_id in batches:
            raise ValueError(f"fetch queue duplicates batch_id: {batch_id}")
        global_ids = batch.get("global_ids")
        if (
            not isinstance(global_ids, list)
            or not global_ids
            or not all(value in candidates for value in global_ids)
            or len(global_ids) != len(set(global_ids))
        ):
            raise ValueError(f"{label}.global_ids is invalid")
        batches[batch_id] = {
            **batch,
            "global_ids": global_ids,
            "request_path": checked_relative(
                run_dir,
                batch.get("request_file"),
                f"{label}.request_file",
            ),
            "body_path": checked_relative(
                run_dir,
                batch.get("body_file"),
                f"{label}.body_file",
            ),
            "semantic_path": checked_relative(
                run_dir,
                batch.get("semantic_file"),
                f"{label}.semantic_file",
            ),
            "evidence_path": checked_relative(
                run_dir,
                batch.get("evidence_file"),
                f"{label}.evidence_file",
            ),
        }
    ordered_waves = []
    covered = set()
    for index, wave in enumerate(waves):
        label = f"fetch_waves[{index}]"
        if not isinstance(wave, dict):
            raise ValueError(f"{label} must be an object")
        batch_ids = wave.get("batch_ids")
        if (
            not isinstance(batch_ids, list)
            or not all(value in batches for value in batch_ids)
        ):
            raise ValueError(f"{label}.batch_ids is invalid")
        if covered & set(batch_ids):
            raise ValueError("fetch waves duplicate batch_id")
        covered.update(batch_ids)
        ordered_waves.append(
            {
                "wave": wave.get("wave", index + 1),
                "parallel": bool(wave.get("parallel")),
                "batch_ids": batch_ids,
            }
        )
    if covered != set(batches):
        raise ValueError("fetch waves do not cover all batches")
    return payload, candidates, batches, ordered_waves


def normalize_existing_bodies(batches):
    for batch in batches.values():
        body = batch["body_path"]
        semantic = batch["semantic_path"]
        if not body.exists():
            continue
        if body.is_symlink() or not body.is_file() or body.stat().st_size == 0:
            raise ValueError(f"fetch body is not a complete regular file: {body.name}")
        normalized = NORMALIZE_BODY.normalize(
            read_json(batch["request_path"], "fetch request"),
            read_json(body, "fetch body"),
        )
        if semantic.exists():
            if semantic.is_symlink() or not semantic.is_file():
                raise ValueError(
                    f"normalized fetch body is not a regular file: {semantic.name}"
                )
            if read_json(semantic, "normalized fetch body") != normalized:
                raise ValueError(
                    f"normalized fetch body does not match raw body: {semantic.name}"
                )
        else:
            write_json_compact_atomic(semantic, normalized)


def packet_paths(run_dir, package_id):
    return (
        run_dir / "stage-packets" / f"{package_id}.json",
        run_dir / "stage-receipts" / f"{package_id}.json",
    )


def object_schema(properties, required):
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def reference_schema(value):
    if isinstance(value, dict):
        return value
    return {"enum": value} if isinstance(value, list) else {"const": value}


def fetch_item_schema(item_ref):
    ref = reference_schema(item_ref)
    return {
        "oneOf": [
            object_schema(
                {
                    "item_ref": ref,
                    "content_type": {"enum": ["text", "xml", "json"]},
                    "content": {},
                    "context": {"type": "object"},
                },
                ["item_ref", "content_type", "content", "context"],
            ),
            object_schema(
                {
                    "item_ref": ref,
                    "outcome": {"const": "access_gap"},
                    "reason": {"type": "string", "minLength": 1},
                },
                ["item_ref", "outcome", "reason"],
            ),
        ]
    }


def extraction_record_schema():
    fields = sorted(RECONCILE.RECORD_FIELDS - IDENTITY_FIELDS)
    properties = {}
    for field in fields:
        if field == "participants":
            properties[field] = {
                "type": "array",
                "items": {"type": "string"},
            }
        elif field == "requires_response":
            properties[field] = {"type": "boolean"}
        else:
            properties[field] = {"type": "string"}
    required = [
        field
        for field in RECONCILE.REQUIRED_TEXT_FIELDS
        if field not in IDENTITY_FIELDS
    ]
    return object_schema(properties, required)


def extract_item_schema(item_ref, record_schema=None):
    ref = reference_schema(item_ref)
    record_schema = record_schema or extraction_record_schema()
    evidence = object_schema(
        {
            "item_ref": ref,
            "outcome": {"enum": sorted(EVIDENCE_OUTCOMES)},
            "record": record_schema,
        },
        ["item_ref", "outcome", "record"],
    )
    discard = object_schema(
        {
            "item_ref": ref,
            "outcome": {"enum": sorted(DISCARD_OUTCOMES)},
        },
        ["item_ref", "outcome"],
    )
    gap = object_schema(
        {
            "item_ref": ref,
            "outcome": {"const": "access_gap"},
            "reason": {"type": "string", "minLength": 1},
        },
        ["item_ref", "outcome", "reason"],
    )
    return {"oneOf": [evidence, discard, gap]}


def synthesis_item_schema(section):
    properties = {}
    for field in sorted(REPORT_HYDRATION.V5_FIELDS[section]):
        if field == "evidence_refs":
            properties[field] = {
                "type": "array",
                "items": {"type": "string", "pattern": "^[wu][0-9]+$"},
                "minItems": 1,
                "uniqueItems": True,
            }
        else:
            properties[field] = {"type": "string"}
    return object_schema(properties, ["evidence_refs"])


def stage_result_schema(stage, payload):
    if stage == "fetch":
        batches = []
        definitions = {}
        for batch_index, batch in enumerate(payload["batches"]):
            properties = {"batch_ref": {"const": batch["batch_ref"]}}
            required = ["batch_ref"]
            if batch.get("output", {}).get("mode") != "managed_file":
                compact_items = len(batch["items"]) > 1
                if compact_items:
                    definition = f"fetch_batch_{batch_index}_item"
                    definitions[definition] = fetch_item_schema(
                        [item["item_ref"] for item in batch["items"]]
                    )
                    prefix = [
                        {"$ref": f"#/$defs/{definition}"}
                        for _ in batch["items"]
                    ]
                else:
                    prefix = [
                        fetch_item_schema(item["item_ref"])
                        for item in batch["items"]
                    ]
                properties["results"] = {
                    "type": "array",
                    "prefixItems": prefix,
                    "items": False,
                    "minItems": len(prefix),
                    "maxItems": len(prefix),
                }
                required.append("results")
            batches.append(object_schema(properties, required))
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **object_schema(
                {
                    "batches": {
                        "type": "array",
                        "prefixItems": batches,
                        "items": False,
                        "minItems": len(batches),
                        "maxItems": len(batches),
                    },
                },
                ["batches"],
            ),
        }
        if definitions:
            schema = {
                "$schema": schema.pop("$schema"),
                "$defs": definitions,
                **schema,
            }
        return schema
    if stage == "extract":
        refs = [item["item_ref"] for item in payload["items"]]
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": {
                "item_ref": {"enum": refs},
                "record": extraction_record_schema(),
            },
            **object_schema(
                {
                    "results": {
                        "type": "array",
                        "items": extract_item_schema(
                            {"$ref": "#/$defs/item_ref"},
                            {"$ref": "#/$defs/record"},
                        ),
                        "minItems": len(refs),
                        "maxItems": len(refs),
                    },
                },
                ["results"],
            ),
        }
        return schema
    properties = {
        section: {
            "type": "array",
            "items": synthesis_item_schema(section),
        }
        for section in REPORT_HYDRATION.SECTION_KINDS
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **object_schema(
            properties,
            list(REPORT_HYDRATION.SECTION_KINDS),
        ),
    }


def write_packet(run_dir, stage, payload, receipt):
    contract_path = SCRIPT_DIR.parent / "references" / CONTRACT_FILES[stage]
    if not contract_path.is_file():
        raise ValueError(f"stage contract is missing: {CONTRACT_FILES[stage]}")
    plan = read_json(run_dir / "run-plan.json", "run plan")
    _, graph_state = execution_graph.load_for_run(run_dir, plan)
    receipt = dict(receipt)
    receipt["guards"] = [
        *receipt.get("guards", []),
        {"path": str(contract_path), "digest": file_digest(contract_path)},
    ]
    if graph_state["file"]:
        receipt["guards"].append(
            {
                "path": graph_state["file"],
                "digest": file_digest(graph_state["file"]),
            }
        )
    unsigned = model_packet(stage, payload)
    digest = canonical_digest(unsigned)
    package_id = f"{stage}-{digest.removeprefix('sha256:')[:16]}"
    schema_path = run_dir / "stage-schemas" / f"{package_id}.json"
    write_json_compact_atomic(
        schema_path,
        stage_result_schema(stage, payload),
    )
    receipt["guards"] = [
        *receipt["guards"],
        {"path": str(schema_path), "digest": file_digest(schema_path)},
    ]
    packet = unsigned
    packet_path, receipt_path = packet_paths(run_dir, package_id)
    created_at_ns = time.time_ns()
    if receipt_path.is_file() and not receipt_path.is_symlink():
        existing = read_json(receipt_path, "stage receipt")
        if (
            existing.get("package_id") == package_id
            and existing.get("input_digest") == digest
            and isinstance(existing.get("created_at_ns"), int)
            and not isinstance(existing.get("created_at_ns"), bool)
        ):
            created_at_ns = existing["created_at_ns"]
    private = {
        "schema_version": PACKET_SCHEMA_VERSION,
        "stage": stage,
        "node_id": execution_graph.STAGE_NODE[stage],
        "graph_id": graph_state["graph_id"],
        "graph_digest": graph_state["digest"],
        "package_id": package_id,
        "input_digest": digest,
        "created_at_ns": created_at_ns,
        **receipt,
    }
    write_json_compact_atomic(packet_path, packet)
    write_json_compact_atomic(receipt_path, private)
    if "batches" in payload:
        item_count = sum(
            len(batch.get("items", []))
            for batch in payload.get("batches", [])
        )
    else:
        item_count = len(payload.get("work", [])) + len(
            payload.get("uncertain", [])
        )
    state = {
        "stage": stage,
        "node_id": execution_graph.STAGE_NODE[stage],
        "graph_mode": graph_state["mode"],
        "packet_file": str(packet_path),
        "package_id": package_id,
        "input_digest": digest,
        "item_count": item_count,
        "parallel": bool(payload.get("parallel")),
        "contract_file": str(contract_path),
        "contract_digest": file_digest(contract_path),
        "result_schema_file": str(schema_path),
    }
    if stage in {"extract", "synthesize"}:
        context_path = run_dir / "request-context.json"
        configured = plan.get("request_context")
        explicit = (
            isinstance(configured, dict)
            and configured.get("explicit") is True
            and context_path.is_file()
            and not context_path.is_symlink()
        )
        state["context_mode"] = "fresh" if explicit else "current"
        if context_path.is_file() and not context_path.is_symlink():
            state["request_context_file"] = str(context_path)
    return state


def guarded_files(paths):
    return [
        {"path": str(path), "digest": file_digest(path)}
        for path in paths
    ]


def fetch_locator(candidate):
    result = {
        "id": candidate["id"],
        "source_type": candidate["source_type"],
    }
    if "cursor" in candidate:
        result["cursor"] = candidate["cursor"]
    return result


def fetch_packet(run_dir, candidates, batches, wave):
    payload_batches = []
    receipt_batches = []
    guards = [run_dir / "fetch-queue.json"]
    for batch_index, batch_id in enumerate(wave["batch_ids"]):
        batch = batches[batch_id]
        if batch["body_path"].is_file() and batch["semantic_path"].is_file():
            continue
        batch_ref = f"b{batch_index}"
        items = []
        item_map = {}
        for item_index, global_id in enumerate(batch["global_ids"]):
            item_ref = f"i{item_index}"
            items.append(
                {
                    "item_ref": item_ref,
                    "locator": fetch_locator(candidates[global_id]),
                }
            )
            item_map[item_ref] = global_id
        file_output = batch.get("file_output") is True
        staging_path = checked_relative(
            run_dir,
            f"fetch-staging/{batch_id}.json",
            f"fetch batch {batch_id}.staging_file",
        )
        if file_output:
            staging_path.parent.mkdir(parents=True, exist_ok=True)
        public_batch = {
            "batch_ref": batch_ref,
            "adapter_id": batch["adapter_id"],
            "operation": batch["operation"],
            "items": items,
        }
        if file_output:
            public_batch["output"] = {
                "mode": "managed_file",
                "result_file": str(staging_path.relative_to(run_dir)),
            }
        payload_batches.append(
            public_batch
        )
        receipt_batches.append(
            {
                "batch_ref": batch_ref,
                "batch_id": batch_id,
                "item_map": item_map,
                "body_file": str(batch["body_path"]),
                "semantic_file": str(batch["semantic_path"]),
                "evidence_file": str(batch["evidence_path"]),
                "request_file": str(batch["request_path"]),
                "file_output": file_output,
                "staging_file": str(staging_path),
            }
        )
        guards.append(batch["request_path"])
    return write_packet(
        run_dir,
        "fetch",
        {
            "wave": wave["wave"],
            "parallel": len(payload_batches) > 1,
            "batches": payload_batches,
        },
        {
            "guards": guarded_files(guards),
            "batches": receipt_batches,
        },
    )


def model_metadata(candidate):
    hidden = {
        "global_id",
        "id",
        "adapter_id",
        "source_ref",
        "alternate_ids",
        "cursor",
    }
    return {
        key: value
        for key, value in candidate.items()
        if key not in hidden
    }


def build_extraction_material(run_dir, candidates, batches, batch_ids, packet_number):
    content_refs = {}
    context_refs = {}
    content_blobs = {}
    context_blobs = {}
    items = []
    receipt_batches = []
    guards = [run_dir / "fetch-queue.json", *semantic_context_guards(run_dir)]
    global_item_map = {}
    for batch_id in batch_ids:
        batch = batches[batch_id]
        if batch["evidence_path"].is_file():
            continue
        semantic = read_json(batch["semantic_path"], "normalized fetch body")
        if semantic.get("batch_id") != batch_id:
            raise ValueError("normalized fetch body batch_id is invalid")
        raw_items = semantic.get("items")
        raw_blobs = semantic.get("content_blobs")
        raw_gaps = semantic.get("access_gaps", [])
        if (
            not isinstance(raw_items, list)
            or not isinstance(raw_blobs, dict)
            or not isinstance(raw_gaps, list)
        ):
            raise ValueError("normalized fetch body is invalid")
        batch_item_refs = []
        for raw in raw_items:
            fingerprint = require_text(
                raw.get("content_fingerprint"),
                "semantic item.content_fingerprint",
            )
            if fingerprint not in raw_blobs:
                raise ValueError("semantic item references unknown content")
            if fingerprint not in content_refs:
                content_ref = f"c{len(content_refs)}"
                content_refs[fingerprint] = content_ref
                content_blobs[content_ref] = raw_blobs[fingerprint]
            context = raw.get("context")
            context_digest = canonical_digest(context)
            if context_digest not in context_refs:
                context_ref = f"x{len(context_refs)}"
                context_refs[context_digest] = context_ref
                context_blobs[context_ref] = context
            global_id = require_text(raw.get("global_id"), "semantic item.global_id")
            if global_id not in candidates:
                raise ValueError("semantic item references unknown candidate")
            item_ref = f"i{len(items)}"
            items.append(
                {
                    "item_ref": item_ref,
                    "content_ref": content_refs[fingerprint],
                    "context_ref": context_refs[context_digest],
                    "metadata": model_metadata(candidates[global_id]),
                }
            )
            batch_item_refs.append(item_ref)
            global_item_map[item_ref] = global_id
        receipt_batches.append(
            {
                "batch_id": batch_id,
                "item_refs": batch_item_refs,
                "evidence_file": str(batch["evidence_path"]),
                "prefetched_gaps": raw_gaps,
            }
        )
        guards.append(batch["semantic_path"])
    return {
        "payload": {
            "packet": packet_number,
            "parallel": False,
            "context": semantic_run_context(run_dir),
            "content_blobs": content_blobs,
            "context_blobs": context_blobs,
            "items": items,
        },
        "receipt": {
            "guards": guarded_files(guards),
            "layout": "flat",
            "item_map": global_item_map,
            "batches": receipt_batches,
        },
    }


def extraction_packet(
    run_dir,
    candidates,
    batches,
    batch_ids,
    packet_number,
    material=None,
):
    material = material or build_extraction_material(
        run_dir,
        candidates,
        batches,
        batch_ids,
        packet_number,
    )
    return write_packet(
        run_dir,
        "extract",
        material["payload"],
        material["receipt"],
    )


def load_optional(run_dir, name, label):
    path = run_dir / name
    if path.is_file() and not path.is_symlink():
        return read_json(path, label)
    return None


def semantic_run_context(run_dir):
    plan = read_json(run_dir / "run-plan.json", "run plan")
    period = plan.get("period")
    if not isinstance(period, dict):
        raise ValueError("run plan requires period context")
    context_path = run_dir / "request-context.json"
    context = (
        request_context.normalize(read_json(context_path, "request context"))
        if context_path.is_file() and not context_path.is_symlink()
        else request_context.fallback(plan.get("requested_domains", []))
    )
    identity = load_optional(run_dir, "identity.json", "identity")
    subject = None
    if isinstance(identity, dict):
        for field in ("display_name", "name"):
            value = identity.get(field)
            if isinstance(value, str) and value.strip():
                subject = value.strip()
                break
    return {
        "request": context,
        "subject": subject,
        "period": {
            key: period[key]
            for key in (
                "routed_profile",
                "start",
                "end",
                "snapshot",
                "title_period",
            )
            if key in period
        },
    }


def semantic_context_guards(run_dir):
    paths = [run_dir / "run-plan.json"]
    for name in ("request-context.json", "identity.json"):
        path = run_dir / name
        if path.is_file() and not path.is_symlink():
            paths.append(path)
    return paths


def synthesis_item(item, evidence_ref, kind, allow_direct_fill):
    hidden = {
        "cluster_id",
        "record_ids",
        "source_type",
        "source_ref",
        "source_refs",
        "work_relevance",
        "retention_mode",
        "priority_score",
        "priority_basis",
    }
    result = {
        "evidence_ref": evidence_ref,
        **{
            key: value
            for key, value in item.items()
            if key not in hidden
        },
    }
    if allow_direct_fill:
        direct = REPORT_HYDRATION.direct_sections(item, kind)
        if direct:
            result["direct_sections"] = direct
    return result


def synthesis_packet(run_dir, ledger):
    template = load_optional(run_dir, "template-profile.json", "template profile")
    allow_direct_fill = template is None
    fingerprint = REPORT_HYDRATION.ledger_fingerprint(ledger)
    writing_context = semantic_run_context(run_dir)
    writing_context["template_tone"] = (
        template.get("tone") if isinstance(template, dict) else None
    )
    payload = {
        "parallel": False,
        "context": writing_context,
        "work": [
            synthesis_item(item, f"w{index}", "work", allow_direct_fill)
            for index, item in enumerate(ledger["work"])
        ],
        "uncertain": [
            synthesis_item(item, f"u{index}", "uncertain", allow_direct_fill)
            for index, item in enumerate(ledger["uncertain"])
        ],
    }
    guards = [
        run_dir / "ledger.json",
        run_dir / "run-plan.json",
    ]
    for name in (
        "identity.json",
        "extraction-audit.json",
        "template-profile.json",
        "request-context.json",
    ):
        path = run_dir / name
        if path.is_file():
            guards.append(path)
    return write_packet(
        run_dir,
        "synthesize",
        payload,
        {
            "guards": guarded_files(guards),
            "ledger_fingerprint": fingerprint,
        },
    )


def materialize_gap_only_batches(run_dir, batches):
    written = False
    for batch in batches.values():
        evidence_path = batch["evidence_path"]
        if evidence_path.is_file():
            continue
        semantic = read_json(batch["semantic_path"], "normalized fetch body")
        items = semantic.get("items")
        gaps = semantic.get("access_gaps", [])
        if not isinstance(items, list) or not isinstance(gaps, list):
            raise ValueError("normalized fetch body is invalid")
        if items:
            continue
        if len(gaps) != len(batch["global_ids"]):
            raise ValueError("gap-only fetch batch does not cover all candidates")
        payload = {
            "schema_version": 1,
            "batch_id": batch["batch_id"],
            "results": gaps,
        }
        written = bool(same_or_write(evidence_path, payload)) or written
    if written:
        invalidate(run_dir)


def next_extraction_group(run_dir, candidates, batches, packet_number):
    selected = []
    selected_material = None
    for batch_id, batch in batches.items():
        if batch["evidence_path"].is_file():
            continue
        semantic = read_json(batch["semantic_path"], "normalized fetch body")
        items = semantic.get("items")
        if not isinstance(items, list):
            raise ValueError("normalized fetch body is invalid")
        if not items:
            continue
        material = build_extraction_material(
            run_dir,
            candidates,
            batches,
            [*selected, batch_id],
            packet_number,
        )
        item_count = len(material["payload"]["items"])
        packet_bytes = compact_json_bytes(
            model_packet("extract", material["payload"])
        )
        if selected and (
            item_count > EXTRACT_MAX_ITEMS
            or packet_bytes > EXTRACT_MAX_BYTES
        ):
            break
        selected.append(batch_id)
        selected_material = material
    return selected, selected_material


def next_stage(run_dir_value):
    run_dir = MANAGE_RUN.checked_run_dir(run_dir_value)
    plan = read_json(run_dir / "run-plan.json", "run plan")
    graph, graph_state = execution_graph.load_for_run(run_dir, plan)
    _, candidates, batches, waves = load_queue(run_dir)
    normalize_existing_bodies(batches)
    ledger = None
    empty_synthesis_active = False
    for node_id in graph["stage_runtime_order"]:
        if node_id == "fetch":
            for wave in waves:
                pending = [
                    batch_id
                    for batch_id in wave["batch_ids"]
                    if not (
                        batches[batch_id]["body_path"].is_file()
                        and batches[batch_id]["semantic_path"].is_file()
                    )
                ]
                if pending:
                    return fetch_packet(
                        run_dir,
                        candidates,
                        batches,
                        {**wave, "batch_ids": pending},
                    )
        elif node_id == "extract":
            materialize_gap_only_batches(run_dir, batches)
            completed = sum(
                batch["evidence_path"].is_file() for batch in batches.values()
            )
            packet_number = completed + 1
            pending, material = next_extraction_group(
                run_dir,
                candidates,
                batches,
                packet_number,
            )
            if pending:
                return extraction_packet(
                    run_dir,
                    candidates,
                    batches,
                    pending,
                    packet_number,
                    material,
                )
        elif node_id == "compile":
            ledger_file = run_dir / "ledger.json"
            if not ledger_file.is_file():
                COMPILE.compile_run(run_dir)
            ledger = read_json(ledger_file, "evidence ledger")
        elif node_id == "synthesize":
            ledger = ledger or read_json(run_dir / "ledger.json", "evidence ledger")
            empty_synthesis_active = empty_synthesis.is_eligible(graph, ledger)
            if empty_synthesis_active:
                empty_synthesis.ensure_model(run_dir, graph, ledger)
            elif not (run_dir / "report-model.json").is_file():
                return synthesis_packet(run_dir, ledger)
        elif node_id == "finalize":
            receipt_path = run_dir / empty_synthesis.RECEIPT_FILE
            bypass_receipt = (
                empty_synthesis.verify_receipt(run_dir, graph, graph_state)
                if (
                    empty_synthesis_active
                    or receipt_path.is_file()
                    or receipt_path.is_symlink()
                )
                else None
            )
            if bypass_receipt is None and (
                empty_synthesis_active
                or not (run_dir / "report.md").is_file()
            ):
                finalized = FINALIZE.finalize(run_dir)
                if not finalized["ok"]:
                    raise ValueError("report finalization failed")
                if empty_synthesis_active:
                    empty_synthesis.write_receipt(
                        run_dir,
                        graph,
                        graph_state,
                    )
        elif node_id == "complete":
            ledger = ledger or read_json(run_dir / "ledger.json", "evidence ledger")
            return {
                "stage": "complete",
                "node_id": "complete",
                "graph_mode": graph_state["mode"],
                "report_file": str(run_dir / "report.md"),
                "item_count": len(ledger["work"]) + len(ledger["uncertain"]),
                "parallel": False,
            }
        else:
            raise ValueError(f"execution graph contains unsupported runtime node: {node_id}")
    raise ValueError("execution graph did not reach complete")


def verify_receipt(run_dir, result, private_package_id=None):
    strict_object(
        result,
        {
            "schema_version",
            "package_id",
            "input_digest",
            "batches",
            "results",
            "ledger_fingerprint",
            "summary",
            "workstreams",
            "risks",
            "next_actions",
            "uncertain",
            "usage",
            "patches",
        },
        "stage result",
    )
    if (
        result.get("schema_version") is not None
        and result.get("schema_version") != PACKET_SCHEMA_VERSION
    ):
        raise ValueError("stage result schema_version must be 1 when supplied")
    result_package_id = result.get("package_id")
    if result_package_id is not None:
        result_package_id = require_text(
            result_package_id,
            "stage result.package_id",
        )
    if (
        private_package_id is not None
        and result_package_id is not None
        and private_package_id != result_package_id
    ):
        raise ValueError("stage result package_id conflicts with commit package")
    package_id = private_package_id or result_package_id
    package_id = require_text(package_id, "commit package_id")
    if not PACKAGE_ID.fullmatch(package_id):
        raise ValueError("stage result.package_id is invalid")
    _, receipt_path = packet_paths(run_dir, package_id)
    receipt = read_json(receipt_path, "stage receipt")
    if receipt.get("package_id") != package_id:
        raise ValueError("stage receipt package_id is invalid")
    if (
        result.get("input_digest") is not None
        and result.get("input_digest") != receipt.get("input_digest")
    ):
        raise ValueError("stage result input_digest is stale or invalid")
    created_at_ns = receipt.get("created_at_ns")
    if created_at_ns is not None and (
        not isinstance(created_at_ns, int)
        or isinstance(created_at_ns, bool)
        or created_at_ns <= 0
    ):
        raise ValueError("stage receipt created_at_ns is invalid")
    validate_usage(result.get("usage"))
    for guard in receipt.get("guards", []):
        path = Path(guard["path"])
        if not path.is_file() or file_digest(path) != guard["digest"]:
            raise ValueError("stage result source artifacts changed after packaging")
    return receipt


def repair_targets(stage, error):
    targets = []
    if stage == "extract":
        for item_ref in re.findall(r"\bextract result (i[0-9]+)\b", error):
            targets.append(f"/results/{item_ref}")
    elif stage == "synthesize":
        for section, index in re.findall(
            r"\b(summary|workstreams|risks|next_actions|uncertain)\[([0-9]+)\]",
            error,
        ):
            targets.append(f"/{section}/{index}")
        if not targets:
            targets.extend(f"/{section}" for section in REPAIR_SECTIONS)
    return list(dict.fromkeys(targets))


def value_at_target(result, target):
    parts = target.removeprefix("/").split("/")
    if parts[0] == "results":
        item_ref = parts[1]
        for item in result.get("results", []):
            if isinstance(item, dict) and item.get("item_ref") == item_ref:
                return item
        return None
    section = parts[0]
    values = result.get(section)
    if len(parts) == 1:
        return values
    if isinstance(values, list):
        index = int(parts[1])
        if index < len(values):
            return values[index]
    return None


def repair_input(run_dir, receipt, result, targets):
    packet_path, _ = packet_paths(run_dir, receipt["package_id"])
    packet = read_json(packet_path, "stage packet")
    payload = packet.get("payload")
    if not isinstance(payload, dict):
        return None
    if receipt["stage"] == "extract":
        wanted = {
            target.split("/")[-1]
            for target in targets
            if target.startswith("/results/")
        }
        items = [
            item
            for item in payload.get("items", [])
            if isinstance(item, dict) and item.get("item_ref") in wanted
        ]
        content_refs = {item.get("content_ref") for item in items}
        context_refs = {item.get("context_ref") for item in items}
        return {
            "context": payload.get("context"),
            "content_blobs": {
                key: value
                for key, value in payload.get("content_blobs", {}).items()
                if key in content_refs
            },
            "context_blobs": {
                key: value
                for key, value in payload.get("context_blobs", {}).items()
                if key in context_refs
            },
            "items": items,
        }
    if receipt["stage"] == "synthesize":
        selected = {"work": {}, "uncertain": {}}
        for target in targets:
            current = value_at_target(result, target)
            section = target.removeprefix("/").split("/")[0]
            kind = REPORT_HYDRATION.SECTION_KINDS.get(section)
            if kind is None:
                continue
            prefix = "w" if kind == "work" else "u"
            available = {
                item.get("evidence_ref"): item
                for item in payload.get(kind, [])
                if isinstance(item, dict)
            }
            refs = (
                current.get("evidence_refs", [])
                if isinstance(current, dict)
                else []
            )
            valid = [ref for ref in refs if ref in available]
            chosen = valid or sorted(
                available,
                key=lambda value: int(value.removeprefix(prefix)),
            )
            for ref in chosen:
                selected[kind][ref] = available[ref]
        return {
            "context": payload.get("context"),
            "work": list(selected["work"].values()),
            "uncertain": list(selected["uncertain"].values()),
        }
    return None


def write_stage_repair(run_dir, receipt, result_path, result, error):
    targets = repair_targets(receipt["stage"], error)
    if not targets:
        return None
    repair_dir = run_dir / "stage-repairs"
    repair_packet = repair_dir / f"{receipt['package_id']}.json"
    repair_schema = repair_dir / f"{receipt['package_id']}.schema.json"
    packet = {
        "schema_version": 1,
        "stage": receipt["stage"],
        "error": " ".join(error.split())[:480],
        "targets": [
            {
                "path": target,
                "current": value_at_target(result, target),
            }
            for target in targets
        ],
        "input": repair_input(run_dir, receipt, result, targets),
    }
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **object_schema(
            {
                "patches": {
                    "type": "array",
                    "items": object_schema(
                        {
                            "path": {"enum": targets},
                            "value": {},
                        },
                        ["path", "value"],
                    ),
                    "minItems": 1,
                    "maxItems": len(targets),
                },
                "usage": object_schema(
                    {
                        field: {"type": "integer", "minimum": 0}
                        for field in sorted(USAGE_FIELDS)
                    },
                    [],
                ),
            },
            ["patches"],
        ),
    }
    write_json_compact_atomic(repair_packet, packet)
    write_json_compact_atomic(repair_schema, schema)
    manifest = {
        "schema_version": 1,
        "package_id": receipt["package_id"],
        "base_result_file": str(result_path),
        "base_result_digest": file_digest(result_path),
        "repair_packet": str(repair_packet),
        "repair_schema": str(repair_schema),
        "targets": targets,
    }
    write_json_compact_atomic(
        repair_dir / f"{receipt['package_id']}.manifest.json",
        manifest,
    )
    return manifest


def replace_target(result, target, value):
    parts = target.removeprefix("/").split("/")
    if parts[0] == "results":
        item_ref = parts[1]
        values = result.get("results")
        if not isinstance(values, list):
            raise ValueError("repair base result has no results array")
        for index, item in enumerate(values):
            if isinstance(item, dict) and item.get("item_ref") == item_ref:
                values[index] = value
                return
        raise ValueError(f"repair target is missing from base result: {target}")
    section = parts[0]
    if len(parts) == 1:
        result[section] = value
        return
    values = result.get(section)
    if not isinstance(values, list):
        raise ValueError(f"repair base result has no {section} array")
    index = int(parts[1])
    if index >= len(values):
        raise ValueError(f"repair target is missing from base result: {target}")
    values[index] = value


def apply_stage_repair(run_dir, package_id, submitted):
    strict_object(submitted, {"patches", "usage"}, "stage repair result")
    manifest_path = (
        run_dir / "stage-repairs" / f"{package_id}.manifest.json"
    )
    manifest = read_json(manifest_path, "stage repair manifest")
    if manifest.get("package_id") != package_id:
        raise ValueError("stage repair manifest package_id is invalid")
    base_path = Path(
        require_text(
            manifest.get("base_result_file"),
            "stage repair manifest.base_result_file",
        )
    )
    if (
        base_path.is_symlink()
        or not base_path.is_file()
        or file_digest(base_path) != manifest.get("base_result_digest")
    ):
        raise ValueError("stage repair base result changed after failure")
    allowed = set(manifest.get("targets", []))
    patches = submitted.get("patches")
    if not isinstance(patches, list) or not patches:
        raise ValueError("stage repair result requires patches")
    merged = read_json(base_path, "stage repair base result")
    seen = set()
    for index, patch in enumerate(patches):
        strict_object(patch, {"path", "value"}, f"stage repair patches[{index}]")
        target = require_text(
            patch.get("path"),
            f"stage repair patches[{index}].path",
        )
        if target not in allowed or target in seen:
            raise ValueError("stage repair result contains an invalid or duplicate path")
        seen.add(target)
        replace_target(merged, target, patch.get("value"))
    if "usage" in submitted:
        merged["usage"] = submitted["usage"]
    return merged


def clear_stage_repair(run_dir, package_id):
    repair_dir = run_dir / "stage-repairs"
    for suffix in (".json", ".schema.json", ".manifest.json", ".merged.json"):
        path = repair_dir / f"{package_id}{suffix}"
        if path.is_file() or path.is_symlink():
            path.unlink()


def validate_usage(value):
    if value is None:
        return None
    strict_object(value, USAGE_FIELDS, "stage result.usage")
    return usage_metrics.validate_tokens(
        value,
        "stage result.usage",
        require_identity=False,
    )


def record_stage_metric(
    run_dir,
    receipt,
    result_path,
    result,
    host_usage=None,
    status="complete",
):
    packet_path, _ = packet_paths(run_dir, receipt["package_id"])
    created_at_ns = receipt.get("created_at_ns")
    elapsed_ms = (
        max(0, (time.time_ns() - created_at_ns) // 1_000_000)
        if created_at_ns is not None
        else 0
    )
    entry = {
        "stage": receipt["stage"],
        "node_id": receipt.get("node_id", receipt["stage"]),
        "package_id": receipt["package_id"],
        "graph_id": receipt.get("graph_id"),
        "graph_digest": receipt.get("graph_digest"),
        "packet_bytes": packet_path.stat().st_size,
        "result_bytes": result_path.stat().st_size,
        "elapsed_ms": elapsed_ms,
        "status": status,
    }
    if status != "complete":
        entry["invocation_id"] = (
            f"{receipt['package_id']}:{status}:{time.time_ns()}"
        )
    usage = host_usage or validate_usage(result.get("usage"))
    if usage is not None:
        entry["usage"] = usage
        entry["usage_source"] = "host_file" if host_usage else "stage_result"
    usage_metrics.append_metric(run_dir, entry)


def exact_ref_map(items, field, expected, label):
    if not isinstance(items, list):
        raise ValueError(f"{label} must be an array")
    mapped = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{label}[{index}] must be an object")
        ref = require_text(item.get(field), f"{label}[{index}].{field}")
        if ref in mapped:
            raise ValueError(f"{label} duplicates {field}: {ref}")
        mapped[ref] = item
    if set(mapped) != set(expected):
        raise ValueError(f"{label} does not exactly cover its stage packet")
    return mapped


def same_or_write(path, payload):
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"stage output is not a regular file: {path.name}")
        if read_json(path, f"existing {path.name}") != payload:
            raise ValueError(f"stage output already exists with different content: {path.name}")
        return False
    write_json_compact_atomic(path, payload)
    return True


def invalidate(run_dir, names=DOWNSTREAM_FILES):
    for name in names:
        path = run_dir / name
        if path.is_file() or path.is_symlink():
            path.unlink()


def fetch_batch_results(batch, returned):
    if batch.get("file_output") and "results" not in returned:
        strict_object(returned, {"batch_ref"}, "file-backed fetch batch result")
        staging_path = Path(batch["staging_file"])
        if (
            staging_path.is_symlink()
            or not staging_path.is_file()
            or staging_path.stat().st_size == 0
        ):
            raise ValueError(
                f"file-backed fetch batch is missing a complete result: "
                f"{batch['batch_ref']}"
            )
        if staging_path.stat().st_size > NORMALIZE_BODY.MAX_BODY_BYTES:
            raise ValueError(
                f"file-backed fetch result exceeds "
                f"{NORMALIZE_BODY.MAX_BODY_BYTES} bytes"
            )
        staged = read_json(staging_path, "file-backed fetch result")
        strict_object(
            staged,
            {"schema_version", "batch_ref", "results"},
            "file-backed fetch result",
        )
        if staged.get("schema_version") != 1:
            raise ValueError("file-backed fetch result schema_version must be 1")
        if staged.get("batch_ref") != batch["batch_ref"]:
            raise ValueError("file-backed fetch result batch_ref is invalid")
        return staged.get("results")
    strict_object(returned, {"batch_ref", "results"}, "fetch batch result")
    return returned.get("results")


def expanded_fetch_item(item_ref, global_id, item):
    label = f"fetch result {item_ref}"
    if item.get("outcome") == "access_gap":
        strict_object(item, {"item_ref", "outcome", "reason"}, label)
        return {
            "global_id": global_id,
            "outcome": "access_gap",
            "reason": require_text(item.get("reason"), f"{label}.reason"),
        }
    strict_object(
        item,
        {"item_ref", "content_type", "content", "context"},
        label,
    )
    return {
        "global_id": global_id,
        "content_type": item.get("content_type"),
        "content": item.get("content"),
        "context": item.get("context"),
    }


def commit_fetch(run_dir, receipt, result):
    batches = exact_ref_map(
        result.get("batches"),
        "batch_ref",
        {batch["batch_ref"] for batch in receipt["batches"]},
        "stage result.batches",
    )
    prepared = []
    for batch in receipt["batches"]:
        returned = batches[batch["batch_ref"]]
        results = exact_ref_map(
            fetch_batch_results(batch, returned),
            "item_ref",
            batch["item_map"],
            f"fetch batch {batch['batch_ref']}.results",
        )
        expanded = []
        for item_ref, global_id in batch["item_map"].items():
            expanded.append(
                expanded_fetch_item(item_ref, global_id, results[item_ref])
            )
        body = {
            "schema_version": 1,
            "batch_id": batch["batch_id"],
            "results": expanded,
        }
        request = read_json(Path(batch["request_file"]), "fetch request")
        semantic = NORMALIZE_BODY.normalize(request, body)
        prepared.append((batch, body, semantic))
    written = 0
    for batch, body, semantic in prepared:
        written += same_or_write(Path(batch["body_file"]), body)
        written += same_or_write(Path(batch["semantic_file"]), semantic)
        evidence = Path(batch["evidence_file"])
        if evidence.is_file() or evidence.is_symlink():
            evidence.unlink()
        staging = Path(batch["staging_file"])
        if staging.is_file() or staging.is_symlink():
            staging.unlink()
    invalidate(run_dir)
    return {"stage": "fetch", "written": written, "batch_count": len(prepared)}


def extraction_record(candidate, outcome, record, retention_mode, label):
    if not isinstance(record, dict):
        raise ValueError(f"{label}.record must be an object")
    forbidden = sorted(set(record) & IDENTITY_FIELDS)
    if forbidden:
        raise ValueError(
            f"{label}.record repeats deterministic fields: {', '.join(forbidden)}"
        )
    semantic = dict(record)
    for field in CANDIDATE_FACT_FIELDS:
        if field not in candidate:
            continue
        if field in semantic and semantic[field] != candidate[field]:
            raise ValueError(
                f"{label}.record conflicts with candidate metadata: {field}"
            )
        semantic[field] = candidate[field]
    hydrated = {
        **semantic,
        "record_id": candidate["global_id"],
        "source_type": candidate["source_type"],
        "source_ref": candidate["source_ref"],
        "occurred_at": candidate["occurred_at"],
        "work_relevance": outcome,
        "retention_mode": retention_mode,
    }
    RECONCILE.validate_record(hydrated, 0)
    return hydrated


def expanded_extraction_result(
    candidates,
    retention_mode,
    item_ref,
    global_id,
    item,
):
    label = f"extract result {item_ref}"
    if not isinstance(item, dict):
        raise ValueError(f"{label} must be an object")
    outcome = item.get("outcome")
    if outcome not in ALL_OUTCOMES:
        raise ValueError(f"{label}.outcome is invalid")
    if outcome in EVIDENCE_OUTCOMES:
        strict_object(item, {"item_ref", "outcome", "record"}, label)
        return {
            "global_id": global_id,
            "outcome": outcome,
            "record": extraction_record(
                candidates[global_id],
                outcome,
                item.get("record"),
                retention_mode,
                label,
            ),
        }
    if outcome in DISCARD_OUTCOMES:
        strict_object(item, {"item_ref", "outcome"}, label)
        return {"global_id": global_id, "outcome": outcome}
    strict_object(item, {"item_ref", "outcome", "reason"}, label)
    return {
        "global_id": global_id,
        "outcome": outcome,
        "reason": require_text(item.get("reason"), f"{label}.reason"),
    }


def prefetched_gap_results(batch, candidates):
    expanded = []
    for gap_index, gap in enumerate(batch.get("prefetched_gaps", [])):
        if not isinstance(gap, dict):
            raise ValueError(
                f"prefetched gap {batch.get('batch_id')}[{gap_index}] is invalid"
            )
        global_id = require_text(
            gap.get("global_id"),
            f"prefetched gap {batch.get('batch_id')}[{gap_index}].global_id",
        )
        if global_id not in candidates:
            raise ValueError("prefetched gap references unknown candidate")
        expanded.append(
            {
                "global_id": global_id,
                "outcome": "access_gap",
                "reason": require_text(
                    gap.get("reason"),
                    f"prefetched gap {batch.get('batch_id')}[{gap_index}].reason",
                ),
            }
        )
    return expanded


def commit_extract(run_dir, receipt, result):
    _, candidates, _, _ = load_queue(run_dir)
    retention_mode = MANAGE_RUN.read_state(run_dir)["retention_mode"]
    flat = receipt.get("layout") == "flat"
    if flat:
        global_item_map = receipt.get("item_map")
        if not isinstance(global_item_map, dict):
            raise ValueError("flat extraction receipt requires item_map")
        flat_results = exact_ref_map(
            result.get("results"),
            "item_ref",
            global_item_map,
            "stage result.results",
        )
        legacy_batches = None
    else:
        legacy_batches = exact_ref_map(
            result.get("batches"),
            "batch_ref",
            {batch["batch_ref"] for batch in receipt["batches"]},
            "stage result.batches",
        )
    prepared = []
    for batch in receipt["batches"]:
        expanded = prefetched_gap_results(batch, candidates)
        if flat:
            for item_ref in batch["item_refs"]:
                global_id = global_item_map[item_ref]
                expanded.append(
                    expanded_extraction_result(
                        candidates,
                        retention_mode,
                        item_ref,
                        global_id,
                        flat_results[item_ref],
                    )
                )
        else:
            returned = legacy_batches[batch["batch_ref"]]
            strict_object(returned, {"batch_ref", "results"}, "extract batch result")
            results = exact_ref_map(
                returned.get("results"),
                "item_ref",
                batch["item_map"],
                f"extract batch {batch['batch_ref']}.results",
            )
            for item_ref, global_id in batch["item_map"].items():
                expanded.append(
                    expanded_extraction_result(
                        candidates,
                        retention_mode,
                        item_ref,
                        global_id,
                        results[item_ref],
                    )
                )
        prepared.append(
            (
                Path(batch["evidence_file"]),
                {
                    "schema_version": 1,
                    "batch_id": batch["batch_id"],
                    "results": expanded,
                },
            )
        )
    written = 0
    for path, payload in prepared:
        written += same_or_write(path, payload)
    invalidate(run_dir)
    return {"stage": "extract", "written": written, "batch_count": len(prepared)}


def commit_synthesize(run_dir, receipt, result):
    bypass_receipt = run_dir / empty_synthesis.RECEIPT_FILE
    if bypass_receipt.is_file() or bypass_receipt.is_symlink():
        raise ValueError(
            "model synthesis cannot coexist with an empty-ledger bypass receipt"
        )
    if result.get("batches") is not None:
        raise ValueError("synthesis result must not contain batches")
    model = {
        key: value
        for key, value in result.items()
        if key
        not in {
            "schema_version",
            "package_id",
            "input_digest",
            "ledger_fingerprint",
            "usage",
        }
    }
    model["schema_version"] = 5
    model["ledger_fingerprint"] = receipt["ledger_fingerprint"]
    ledger = read_json(run_dir / "ledger.json", "evidence ledger")
    plan = read_json(run_dir / "run-plan.json", "run plan")
    identity = load_optional(run_dir, "identity.json", "identity")
    audit = load_optional(run_dir, "extraction-audit.json", "extraction audit")
    template = load_optional(run_dir, "template-profile.json", "template profile")
    hydrated = REPORT_HYDRATION.hydrate(
        model,
        ledger,
        plan,
        identity,
        audit,
        allow_direct_fill=template is None,
    )
    RENDER_REPORT.normalize_report_model(hydrated)
    coverage = VALIDATE_REPORT.validate_model_coverage(hydrated, ledger)
    if not coverage["ok"]:
        raise ValueError("; ".join(coverage["errors"]))
    write_json_compact_atomic(run_dir / "report-model.json", model)
    report = run_dir / "report.md"
    if report.is_file() or report.is_symlink():
        report.unlink()
    finalized = FINALIZE.finalize(run_dir)
    if not finalized["ok"]:
        raise ValueError("report finalization failed")
    return {
        "stage": "synthesize",
        "written": 2,
        "report_file": finalized["report_file"],
    }


def commit_stage(run_dir_value, result_file, package_id=None, usage_file=None):
    run_dir = MANAGE_RUN.checked_run_dir(run_dir_value)
    host_usage = usage_metrics.load_host_usage(run_dir, usage_file)
    result_path = Path(result_file)
    if result_path.is_symlink():
        raise ValueError("stage result cannot be a symlink")
    result_path = result_path.resolve(strict=True)
    try:
        result_path.relative_to(run_dir)
    except ValueError as exc:
        raise ValueError("stage result must remain inside the managed run") from exc
    submitted = read_json(result_path, "stage result")
    validation_path = result_path
    if isinstance(submitted, dict) and "patches" in submitted:
        package_id = require_text(package_id, "repair commit package_id")
        result = apply_stage_repair(run_dir, package_id, submitted)
        validation_path = (
            run_dir / "stage-repairs" / f"{package_id}.merged.json"
        )
        write_json_compact_atomic(validation_path, result)
    else:
        result = submitted
    if host_usage is not None and result.get("usage") is not None:
        raise ValueError("usage must be supplied by either result or usage file, not both")
    receipt = verify_receipt(run_dir, result, package_id)
    try:
        if receipt["stage"] == "fetch":
            committed = commit_fetch(run_dir, receipt, result)
        elif receipt["stage"] == "extract":
            committed = commit_extract(run_dir, receipt, result)
        elif receipt["stage"] == "synthesize":
            committed = commit_synthesize(run_dir, receipt, result)
        else:
            raise ValueError("stage receipt has an invalid stage")
    except ValueError as exc:
        record_stage_metric(
            run_dir,
            receipt,
            result_path,
            submitted,
            host_usage=host_usage,
            status="failed",
        )
        repair = write_stage_repair(
            run_dir,
            receipt,
            validation_path,
            result,
            str(exc),
        )
        if repair is not None:
            raise ValueError(
                f"{exc}; repair_packet={repair['repair_packet']}; "
                f"repair_schema={repair['repair_schema']}"
            ) from exc
        raise
    record_stage_metric(
        run_dir,
        receipt,
        result_path,
        submitted,
        host_usage=host_usage,
    )
    clear_stage_repair(run_dir, receipt["package_id"])
    return {
        **committed,
        "package_id": receipt["package_id"],
        "next": next_stage(run_dir),
    }
