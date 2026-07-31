"""Deep stage interface for compact Agent packets and deterministic hydration."""

import hashlib
import json
import re
from pathlib import Path

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
DOWNSTREAM_FILES = (
    "evidence-records.json",
    "ledger.json",
    "synthesis-view.json",
    "extraction-audit.json",
    "report-model.json",
    "report.md",
    "repair-queue.json",
)


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


def write_packet(run_dir, stage, payload, receipt):
    unsigned = {
        "schema_version": PACKET_SCHEMA_VERSION,
        "stage": stage,
        "contract_version": CONTRACT_VERSION,
        "payload": payload,
    }
    digest = canonical_digest(unsigned)
    package_id = f"{stage}-{digest.removeprefix('sha256:')[:16]}"
    packet = {
        **unsigned,
        "package_id": package_id,
        "input_digest": digest,
    }
    private = {
        "schema_version": PACKET_SCHEMA_VERSION,
        "stage": stage,
        "package_id": package_id,
        "input_digest": digest,
        **receipt,
    }
    packet_path, receipt_path = packet_paths(run_dir, package_id)
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
    return {
        "stage": stage,
        "packet_file": str(packet_path),
        "package_id": package_id,
        "input_digest": digest,
        "item_count": item_count,
        "parallel": bool(payload.get("parallel")),
    }


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
        payload_batches.append(
            {
                "batch_ref": batch_ref,
                "adapter_id": batch["adapter_id"],
                "operation": batch["operation"],
                "items": items,
            }
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


def extraction_packet(run_dir, candidates, batches, wave):
    payload_batches = []
    receipt_batches = []
    guards = [run_dir / "fetch-queue.json"]
    for batch_index, batch_id in enumerate(wave["batch_ids"]):
        batch = batches[batch_id]
        if batch["evidence_path"].is_file():
            continue
        semantic = read_json(batch["semantic_path"], "normalized fetch body")
        if semantic.get("batch_id") != batch_id:
            raise ValueError("normalized fetch body batch_id is invalid")
        raw_items = semantic.get("items")
        raw_blobs = semantic.get("content_blobs")
        if not isinstance(raw_items, list) or not isinstance(raw_blobs, dict):
            raise ValueError("normalized fetch body is invalid")
        batch_ref = f"b{batch_index}"
        content_map = {}
        compact_blobs = {}
        items = []
        item_map = {}
        for raw in raw_items:
            fingerprint = require_text(
                raw.get("content_fingerprint"),
                "semantic item.content_fingerprint",
            )
            if fingerprint not in raw_blobs:
                raise ValueError("semantic item references unknown content")
            if fingerprint not in content_map:
                content_ref = f"c{len(content_map)}"
                content_map[fingerprint] = content_ref
                compact_blobs[content_ref] = raw_blobs[fingerprint]
            global_id = require_text(raw.get("global_id"), "semantic item.global_id")
            if global_id not in candidates:
                raise ValueError("semantic item references unknown candidate")
            item_ref = f"i{len(items)}"
            items.append(
                {
                    "item_ref": item_ref,
                    "content_ref": content_map[fingerprint],
                    "context": raw.get("context"),
                    "metadata": model_metadata(candidates[global_id]),
                }
            )
            item_map[item_ref] = global_id
        payload_batches.append(
            {
                "batch_ref": batch_ref,
                "content_blobs": compact_blobs,
                "items": items,
            }
        )
        receipt_batches.append(
            {
                "batch_ref": batch_ref,
                "batch_id": batch_id,
                "item_map": item_map,
                "evidence_file": str(batch["evidence_path"]),
            }
        )
        guards.append(batch["semantic_path"])
    return write_packet(
        run_dir,
        "extract",
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


def load_optional(run_dir, name, label):
    path = run_dir / name
    if path.is_file() and not path.is_symlink():
        return read_json(path, label)
    return None


def synthesis_item(item, evidence_ref):
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
    return {
        "evidence_ref": evidence_ref,
        **{
            key: value
            for key, value in item.items()
            if key not in hidden
        },
    }


def synthesis_packet(run_dir, ledger):
    plan = read_json(run_dir / "run-plan.json", "run plan")
    identity = load_optional(run_dir, "identity.json", "identity")
    audit = load_optional(run_dir, "extraction-audit.json", "extraction audit")
    template = load_optional(run_dir, "template-profile.json", "template profile")
    top = REPORT_HYDRATION.deterministic_top_level(
        plan,
        identity,
        audit,
        ledger,
    )
    fingerprint = REPORT_HYDRATION.ledger_fingerprint(ledger)
    payload = {
        "parallel": False,
        "ledger_fingerprint": fingerprint,
        "context": {
            **top,
            "template_tone": (
                template.get("tone")
                if isinstance(template, dict)
                else None
            ),
        },
        "work": [
            synthesis_item(item, f"w{index}")
            for index, item in enumerate(ledger["work"])
        ],
        "uncertain": [
            synthesis_item(item, f"u{index}")
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


def next_stage(run_dir_value):
    run_dir = MANAGE_RUN.checked_run_dir(run_dir_value)
    _, candidates, batches, waves = load_queue(run_dir)
    normalize_existing_bodies(batches)

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

    for wave in waves:
        pending = [
            batch_id
            for batch_id in wave["batch_ids"]
            if not batches[batch_id]["evidence_path"].is_file()
        ]
        if pending:
            return extraction_packet(
                run_dir,
                candidates,
                batches,
                {**wave, "batch_ids": pending},
            )

    ledger_file = run_dir / "ledger.json"
    if not ledger_file.is_file():
        COMPILE.compile_run(run_dir)
    ledger = read_json(ledger_file, "evidence ledger")
    model_file = run_dir / "report-model.json"
    report_file = run_dir / "report.md"
    if not model_file.is_file():
        return synthesis_packet(run_dir, ledger)
    if not report_file.is_file():
        finalized = FINALIZE.finalize(run_dir)
        if not finalized["ok"]:
            raise ValueError("report finalization failed")
    return {
        "stage": "complete",
        "report_file": str(report_file),
        "item_count": len(ledger["work"]) + len(ledger["uncertain"]),
        "parallel": False,
    }


def verify_receipt(run_dir, result):
    strict_object(
        result,
        {
            "schema_version",
            "package_id",
            "input_digest",
            "batches",
            "ledger_fingerprint",
            "summary",
            "workstreams",
            "risks",
            "next_actions",
            "uncertain",
        },
        "stage result",
    )
    if result.get("schema_version") != PACKET_SCHEMA_VERSION:
        raise ValueError("stage result schema_version must be 1")
    package_id = require_text(result.get("package_id"), "stage result.package_id")
    if not PACKAGE_ID.fullmatch(package_id):
        raise ValueError("stage result.package_id is invalid")
    _, receipt_path = packet_paths(run_dir, package_id)
    receipt = read_json(receipt_path, "stage receipt")
    if receipt.get("package_id") != package_id:
        raise ValueError("stage receipt package_id is invalid")
    if result.get("input_digest") != receipt.get("input_digest"):
        raise ValueError("stage result input_digest is stale or invalid")
    for guard in receipt.get("guards", []):
        path = Path(guard["path"])
        if not path.is_file() or file_digest(path) != guard["digest"]:
            raise ValueError("stage result source artifacts changed after packaging")
    return receipt


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
        strict_object(returned, {"batch_ref", "results"}, "fetch batch result")
        results = exact_ref_map(
            returned.get("results"),
            "item_ref",
            batch["item_map"],
            f"fetch batch {batch['batch_ref']}.results",
        )
        expanded = []
        for item_ref, global_id in batch["item_map"].items():
            item = results[item_ref]
            strict_object(
                item,
                {"item_ref", "content_type", "content", "context"},
                f"fetch result {item_ref}",
            )
            expanded.append(
                {
                    "global_id": global_id,
                    "content_type": item.get("content_type"),
                    "content": item.get("content"),
                    "context": item.get("context"),
                }
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
    hydrated = {
        **record,
        "record_id": candidate["global_id"],
        "source_type": candidate["source_type"],
        "source_ref": candidate["source_ref"],
        "occurred_at": candidate["occurred_at"],
        "work_relevance": outcome,
        "retention_mode": retention_mode,
    }
    RECONCILE.validate_record(hydrated, 0)
    return hydrated


def commit_extract(run_dir, receipt, result):
    _, candidates, _, _ = load_queue(run_dir)
    retention_mode = MANAGE_RUN.read_state(run_dir)["retention_mode"]
    batches = exact_ref_map(
        result.get("batches"),
        "batch_ref",
        {batch["batch_ref"] for batch in receipt["batches"]},
        "stage result.batches",
    )
    prepared = []
    for batch in receipt["batches"]:
        returned = batches[batch["batch_ref"]]
        strict_object(returned, {"batch_ref", "results"}, "extract batch result")
        results = exact_ref_map(
            returned.get("results"),
            "item_ref",
            batch["item_map"],
            f"extract batch {batch['batch_ref']}.results",
        )
        expanded = []
        for item_ref, global_id in batch["item_map"].items():
            label = f"extract result {item_ref}"
            item = results[item_ref]
            if not isinstance(item, dict):
                raise ValueError(f"{label} must be an object")
            outcome = item.get("outcome")
            if outcome not in ALL_OUTCOMES:
                raise ValueError(f"{label}.outcome is invalid")
            if outcome in EVIDENCE_OUTCOMES:
                strict_object(item, {"item_ref", "outcome", "record"}, label)
                expanded.append(
                    {
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
                )
            elif outcome in DISCARD_OUTCOMES:
                strict_object(item, {"item_ref", "outcome"}, label)
                expanded.append({"global_id": global_id, "outcome": outcome})
            else:
                strict_object(item, {"item_ref", "outcome", "reason"}, label)
                expanded.append(
                    {
                        "global_id": global_id,
                        "outcome": outcome,
                        "reason": require_text(item.get("reason"), f"{label}.reason"),
                    }
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
    if result.get("batches") is not None:
        raise ValueError("synthesis result must not contain batches")
    model = {
        key: value
        for key, value in result.items()
        if key not in {"package_id", "input_digest"}
    }
    model["schema_version"] = 5
    ledger = read_json(run_dir / "ledger.json", "evidence ledger")
    plan = read_json(run_dir / "run-plan.json", "run plan")
    identity = load_optional(run_dir, "identity.json", "identity")
    audit = load_optional(run_dir, "extraction-audit.json", "extraction audit")
    hydrated = REPORT_HYDRATION.hydrate(
        model,
        ledger,
        plan,
        identity,
        audit,
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


def commit_stage(run_dir_value, result_file):
    run_dir = MANAGE_RUN.checked_run_dir(run_dir_value)
    result_path = Path(result_file)
    if result_path.is_symlink():
        raise ValueError("stage result cannot be a symlink")
    result_path = result_path.resolve(strict=True)
    try:
        result_path.relative_to(run_dir)
    except ValueError as exc:
        raise ValueError("stage result must remain inside the managed run") from exc
    result = read_json(result_path, "stage result")
    receipt = verify_receipt(run_dir, result)
    if receipt["stage"] == "fetch":
        committed = commit_fetch(run_dir, receipt, result)
    elif receipt["stage"] == "extract":
        committed = commit_extract(run_dir, receipt, result)
    elif receipt["stage"] == "synthesize":
        committed = commit_synthesize(run_dir, receipt, result)
    else:
        raise ValueError("stage receipt has an invalid stage")
    return {
        **committed,
        "package_id": receipt["package_id"],
        "next": next_stage(run_dir),
    }
