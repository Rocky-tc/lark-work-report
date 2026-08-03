"""Build a bounded repair queue containing only failed extraction candidates."""

from collections import Counter
from pathlib import Path

from runtime_utils import load_script, read_json


SCRIPT_DIR = Path(__file__).resolve().parent
NORMALIZE_BODY = load_script(SCRIPT_DIR, "normalize-fetch-body.py")
RECONCILE = load_script(SCRIPT_DIR, "reconcile-evidence.py")
VALID_OUTCOMES = {
    "work",
    "uncertain",
    "discarded_private",
    "discarded_chatter",
    "access_gap",
}


def queue_contract(run_dir):
    queue = read_json(run_dir / "fetch-queue.json", "fetch queue")
    version = queue.get("schema_version") if isinstance(queue, dict) else None
    if version == 1:
        items = queue.get("fetch_queue", [])
        index = {
            item["global_id"]: item
            for item in items
            if isinstance(item, dict) and isinstance(item.get("global_id"), str)
        }
    elif version == 2:
        raw_index = queue.get("candidate_index", {})
        if not isinstance(raw_index, dict):
            raise ValueError("candidate_index is invalid")
        index = {
            global_id: {"global_id": global_id, **item}
            for global_id, item in raw_index.items()
            if isinstance(global_id, str) and isinstance(item, dict)
        }
    else:
        raise ValueError("unsupported fetch queue")
    batches = []
    for raw in queue.get("fetch_batches", []) or []:
        if not isinstance(raw, dict):
            continue
        if version == 1:
            ids = [
                item.get("global_id")
                for item in raw.get("items", [])
                if isinstance(item, dict)
            ]
        else:
            ids = raw.get("global_ids", [])
        batches.append(
            {
                "batch_id": raw.get("batch_id"),
                "global_ids": [value for value in ids if value in index],
                "request_file": raw.get("request_file"),
                "body_file": raw.get("body_file"),
                "semantic_file": raw.get("semantic_file"),
                "evidence_file": raw.get("evidence_file"),
                "requires_semantic": version == 2,
            }
        )
    return version, index, batches


def path_for(run_dir, value):
    if not isinstance(value, str) or not value:
        return None
    path = (run_dir / value).resolve()
    try:
        path.relative_to(run_dir)
    except ValueError:
        return None
    return path


def build(run_dir, failure=None):
    run_dir = Path(run_dir).resolve()
    version, index, batches = queue_contract(run_dir)
    issues = []

    def add(global_ids, batch_id, reason_code):
        for global_id in global_ids:
            if global_id in index:
                issues.append(
                    {
                        "global_id": global_id,
                        "batch_id": batch_id,
                        "reason_code": reason_code,
                    }
                )

    def inspect_results(results, expected_ids, batch_id):
        if not isinstance(results, list):
            add(expected_ids, batch_id, "unreadable_part")
            return
        valid_ids = [
            result.get("global_id")
            for result in results
            if isinstance(result, dict)
            and isinstance(result.get("global_id"), str)
        ]
        counts = Counter(valid_ids)
        add(
            [global_id for global_id, count in counts.items() if count > 1],
            batch_id,
            "duplicate_result",
        )
        add(set(expected_ids) - set(valid_ids), batch_id, "missing_result")
        for result in results:
            if not isinstance(result, dict):
                add(expected_ids, batch_id, "invalid_result")
                continue
            global_id = result.get("global_id")
            if global_id not in expected_ids:
                add(expected_ids, batch_id, "invalid_result")
                continue
            outcome = result.get("outcome")
            if outcome not in VALID_OUTCOMES:
                add([global_id], batch_id, "invalid_result")
                continue
            if outcome in {"work", "uncertain"}:
                record = result.get("record")
                candidate = index[global_id]
                expected = {
                    "record_id": global_id,
                    "source_type": candidate.get("source_type"),
                    "source_ref": candidate.get("source_ref"),
                    "occurred_at": candidate.get("occurred_at"),
                    "work_relevance": outcome,
                }
                if not isinstance(record, dict) or any(
                    record.get(field) != value for field, value in expected.items()
                ):
                    add([global_id], batch_id, "identity_mismatch")
                    continue
                try:
                    RECONCILE.validate_record(record, 0)
                except ValueError:
                    add([global_id], batch_id, "invalid_result")
            elif outcome == "access_gap" and not isinstance(result.get("reason"), str):
                add([global_id], batch_id, "invalid_result")

    if batches:
        for batch in batches:
            batch_id = batch["batch_id"]
            ids = batch["global_ids"]
            body_path = path_for(run_dir, batch["body_file"])
            if body_path is None or not body_path.is_file() or body_path.stat().st_size == 0:
                add(ids, batch_id, "missing_body")
            if batch["requires_semantic"]:
                semantic_path = path_for(run_dir, batch["semantic_file"])
                if (
                    semantic_path is None
                    or not semantic_path.is_file()
                    or semantic_path.stat().st_size == 0
                ):
                    add(ids, batch_id, "missing_semantic_body")
                elif body_path is not None and body_path.is_file():
                    request_path = path_for(run_dir, batch["request_file"])
                    try:
                        expected = NORMALIZE_BODY.normalize(
                            read_json(request_path, "fetch request"),
                            read_json(body_path, "fetch body"),
                        )
                        if read_json(semantic_path, "semantic body") != expected:
                            add(ids, batch_id, "invalid_semantic_body")
                    except (OSError, ValueError, TypeError):
                        add(ids, batch_id, "invalid_semantic_body")
            evidence_path = path_for(run_dir, batch["evidence_file"])
            if evidence_path is None or not evidence_path.is_file():
                add(ids, batch_id, "missing_evidence_part")
                continue
            try:
                part = read_json(evidence_path, "evidence part")
                if (
                    not isinstance(part, dict)
                    or part.get("batch_id") != batch_id
                ):
                    raise ValueError("evidence batch mismatch")
                inspect_results(part.get("results"), ids, batch_id)
            except (OSError, ValueError):
                add(ids, batch_id, "unreadable_part")
    else:
        parts_dir = run_dir / "evidence-parts"
        results = []
        if parts_dir.is_dir():
            for path in sorted(parts_dir.glob("*.json")):
                try:
                    part = read_json(path, "evidence part")
                    if isinstance(part, dict):
                        results.extend(part.get("results", []))
                except ValueError:
                    add(index, path.stem, "unreadable_part")
        inspect_results(results, list(index), None)

    unique = {}
    for issue in issues:
        key = (
            issue["global_id"],
            issue["batch_id"],
            issue["reason_code"],
        )
        unique[key] = issue
    issues = sorted(
        unique.values(),
        key=lambda item: (
            item["batch_id"] or "",
            item["global_id"],
            item["reason_code"],
        ),
    )
    if not issues and failure:
        add(index, None, "compile_failure")
        issues = issues or [
            {
                "global_id": global_id,
                "batch_id": None,
                "reason_code": "compile_failure",
            }
            for global_id in index
        ]

    affected_ids = list(dict.fromkeys(issue["global_id"] for issue in issues))
    affected_batches = {
        issue["batch_id"] for issue in issues if issue["batch_id"] is not None
    }
    return {
        "schema_version": 1,
        "candidate_index": {
            global_id: {
                key: value
                for key, value in index[global_id].items()
                if key != "global_id"
            }
            for global_id in affected_ids
        },
        "repair_batches": [
            {
                key: value
                for key, value in batch.items()
                if key != "requires_semantic"
            }
            for batch in batches
            if batch["batch_id"] in affected_batches
        ],
        "issues": issues,
    }
