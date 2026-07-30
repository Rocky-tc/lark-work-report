#!/usr/bin/env python3
"""Resolve a period, validate adapters, and create one executable run plan."""

import argparse
import json
import sys
from pathlib import Path

from contracts import SOURCE_PRIORITY
from runtime_utils import emit_json, load_script, write_json_atomic
from template_profiles import load as load_template


SCRIPT_DIR = Path(__file__).resolve().parent
RESOLVE_PERIOD = load_script(SCRIPT_DIR, "resolve-period.py")
VALIDATE_ADAPTER = load_script(SCRIPT_DIR, "validate-adapter.py")
MANAGE_RUN = load_script(SCRIPT_DIR, "manage-run.py")


def chunks(values, size):
    return [values[index : index + size] for index in range(0, len(values), size)]


def load_adapters(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("adapter bundle is unreadable") from exc
    adapters = payload.get("adapters") if isinstance(payload, dict) else None
    if not isinstance(adapters, list) or not adapters:
        raise ValueError("adapter bundle must contain a non-empty adapters array")
    return adapters


def validate_adapters(adapters):
    validated = []
    seen = set()
    for manifest in adapters:
        result = VALIDATE_ADAPTER.validate(manifest)
        if not result["ok"]:
            raise ValueError(
                f"adapter {result.get('adapter_id') or '<unknown>'} is invalid: "
                + "; ".join(result["errors"])
            )
        adapter_id = result["adapter_id"]
        if adapter_id in seen:
            raise ValueError(f"duplicate adapter_id: {adapter_id}")
        seen.add(adapter_id)
        validated.append(result)
    return validated


def identity_call(adapters):
    adapter = next(
        (item for item in adapters if item["identity_available"]),
        None,
    )
    if adapter is None:
        if any(item["collection_mode"] != "offline_only" for item in adapters):
            raise ValueError("live collection requires an identity adapter")
        return None
    return {
        "adapter_id": adapter["adapter_id"],
        "operation": "identity.current",
    }


def compact_adapter(adapter):
    fields = (
        "adapter_id",
        "collection_mode",
        "delivery_mode",
        "domains",
        "domain_coverage",
        "list_operation",
        "list_batch_size",
        "metadata_parallelism",
        "fetch_operation",
        "fetch_batch_size",
        "fetch_file_output",
        "fetch_parallelism",
        "template_mode",
        "template_fetch_operation",
        "template_upsert_operation",
        "template_delete_operation",
    )
    return {field: adapter[field] for field in fields}


def requested_domains(values, adapters):
    if values:
        domains = []
        for value in values:
            domains.extend(item.strip() for item in value.split(",") if item.strip())
    else:
        available = {
            covered
            for adapter in adapters
            if adapter["collection_mode"] == "broad"
            for query_domain in adapter["domains"]
            for covered in adapter["domain_coverage"][query_domain]
        }
        domains = [domain for domain in SOURCE_PRIORITY if domain in available]
    unknown = set(domains) - VALIDATE_ADAPTER.SOURCE_TYPES
    if unknown:
        raise ValueError(f"unknown requested domains: {', '.join(sorted(unknown))}")
    return list(dict.fromkeys(domains))


def assign_domains(domains, adapters):
    assignments = {adapter["adapter_id"]: [] for adapter in adapters}
    coverage = {adapter["adapter_id"]: {} for adapter in adapters}
    remaining = set(domains)
    unassigned = []
    while remaining:
        target = next(domain for domain in domains if domain in remaining)
        options = []
        for adapter_index, adapter in enumerate(adapters):
            if adapter["collection_mode"] != "broad":
                continue
            for query_index, query_domain in enumerate(adapter["domains"]):
                covered = set(adapter["domain_coverage"][query_domain])
                if target not in covered:
                    continue
                gain = covered & remaining
                options.append(
                    (
                        -len(gain),
                        query_domain != target,
                        adapter_index,
                        query_index,
                        adapter,
                        query_domain,
                        gain,
                    )
                )
        if not options:
            unassigned.append(target)
            remaining.remove(target)
            continue
        _, _, _, _, adapter, query_domain, gain = min(
            options,
            key=lambda item: item[:4],
        )
        adapter_id = adapter["adapter_id"]
        assignments[adapter_id].append(query_domain)
        covered_in_order = [domain for domain in domains if domain in gain]
        coverage[adapter_id][query_domain] = covered_in_order
        remaining -= gain
    return assignments, coverage, unassigned


def metadata_call(adapter, domains, covered_domains, period):
    return {
        "adapter_id": adapter["adapter_id"],
        "operation": adapter["list_operation"],
        "domains": domains,
        "covered_domains": covered_domains,
        "start": period["start"],
        "end": period["end"],
    }


def build_metadata_waves(
    adapters,
    assignments,
    coverage,
    period,
    domain_order=None,
):
    ranked_waves = []
    domain_order = domain_order or [
        domain
        for adapter in adapters
        for domain in assignments[adapter["adapter_id"]]
    ]
    domain_rank = {
        domain: index for index, domain in enumerate(domain_order)
    }
    for adapter in adapters:
        domains = assignments[adapter["adapter_id"]]
        if not domains:
            continue
        adapter_coverage = coverage[adapter["adapter_id"]]
        strategy = adapter["metadata_strategy"]
        if strategy == "batch":
            groups = chunks(domains, adapter["list_batch_size"])
            ranked_waves.extend(
                (
                    min(
                        domain_rank[covered]
                        for domain in group
                        for covered in adapter_coverage[domain]
                    ),
                    [
                        metadata_call(
                            adapter,
                            group,
                            [
                                domain
                                for domain in domain_order
                                if any(
                                    domain in adapter_coverage[query_domain]
                                    for query_domain in group
                                )
                            ],
                            period,
                        )
                    ],
                )
                for group in groups
            )
        elif strategy == "parallel":
            calls = [
                metadata_call(
                    adapter,
                    [domain],
                    adapter_coverage[domain],
                    period,
                )
                for domain in domains
            ]
            for group in chunks(calls, adapter["metadata_parallelism"]):
                ranked_waves.append(
                    (
                        min(
                            domain_rank[covered]
                            for call in group
                            for covered in call["covered_domains"]
                        ),
                        group,
                    )
                )
        elif strategy == "serial":
            ranked_waves.extend(
                (
                    min(
                        domain_rank[covered]
                        for covered in adapter_coverage[domain]
                    ),
                    [
                        metadata_call(
                            adapter,
                            [domain],
                            adapter_coverage[domain],
                            period,
                        )
                    ],
                )
                for domain in domains
            )
    waves = [calls for _, calls in sorted(ranked_waves, key=lambda item: item[0])]
    return [
        {"wave": index, "parallel": len(calls) > 1, "calls": calls}
        for index, calls in enumerate(waves, start=1)
    ]


def run_profile(period, depth, monthly_cache):
    if depth == "deep":
        return "deep"
    if period["routed_profile"] != "monthly":
        return period["routed_profile"]
    return "monthly_cached" if monthly_cache == "present" else "monthly_uncached"


def template_call(adapters, mode, template_file):
    if template_file or mode == "none":
        return None
    adapter = next(
        (
            item
            for item in adapters
            if item["template_fetch_operation"] == "template.fetch"
        ),
        None,
    )
    if adapter is None:
        return None
    return {
        "adapter_id": adapter["adapter_id"],
        "operation": "template.fetch",
        "template_id": "default",
    }


def prepare(args):
    period = RESOLVE_PERIOD.resolve(
        period=args.period,
        reference=RESOLVE_PERIOD.parse_date(args.reference),
        start=RESOLVE_PERIOD.parse_date(args.start) if args.start else None,
        end=RESOLVE_PERIOD.parse_date(args.end) if args.end else None,
        timezone_name=args.timezone,
        relative=args.relative,
        snapshot=RESOLVE_PERIOD.parse_datetime(args.snapshot)
        if args.snapshot
        else None,
    )
    adapters = validate_adapters(load_adapters(args.adapters_file))
    domains = requested_domains(args.domain, adapters)
    assignments, coverage, unassigned = assign_domains(domains, adapters)
    waves = build_metadata_waves(
        adapters,
        assignments,
        coverage,
        period,
        domains,
    )
    planned_identity = identity_call(adapters)
    one_off_template = load_template(args.template_file) if args.template_file else None
    planned_template = template_call(
        adapters,
        args.template_mode,
        args.template_file,
    )
    profile = run_profile(period, args.depth, args.monthly_cache)

    created = MANAGE_RUN.create(profile, 1 if planned_template else 0)
    run_dir = Path(created["run_dir"])
    plan_file = run_dir / "run-plan.json"
    try:
        if one_off_template:
            write_json_atomic(run_dir / "template-profile.json", one_off_template)
        plan = {
            "schema_version": 1,
            "period": period,
            "profile": profile,
            "template_mode": (
                "one_off"
                if one_off_template
                else ("auto" if args.template_mode == "auto" else "none")
            ),
            "template_call": planned_template,
            "requested_domains": domains,
            "unassigned_domains": unassigned,
            "identity_call": planned_identity,
            "adapters": [compact_adapter(adapter) for adapter in adapters],
            "metadata_waves": waves,
        }
        write_json_atomic(plan_file, plan)
    except Exception:
        MANAGE_RUN.cleanup(str(run_dir))
        raise

    return {
        "run_dir": str(run_dir),
        "plan_file": str(plan_file),
        "routed_profile": period["routed_profile"],
        "run_profile": profile,
        "identity_call_count": 1 if planned_identity else 0,
        "template_call_count": 1 if planned_template else 0,
        "template_mode": plan["template_mode"],
        "template_file": (
            str(run_dir / "template-profile.json") if one_off_template else None
        ),
        "metadata_wave_count": len(waves),
        "metadata_call_count": sum(len(wave["calls"]) for wave in waves),
        "unassigned_domains": unassigned,
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapters-file", required=True)
    parser.add_argument(
        "--period",
        required=True,
        choices=("daily", "weekly", "monthly", "custom"),
    )
    parser.add_argument("--reference", required=True)
    parser.add_argument("--relative", choices=("current", "previous"), default="current")
    parser.add_argument("--snapshot")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument(
        "--domain",
        action="append",
        default=[],
        help="Requested source type; repeat or pass a comma-separated list",
    )
    parser.add_argument("--depth", choices=("quick", "standard", "deep"), default="standard")
    parser.add_argument(
        "--monthly-cache", choices=("present", "absent"), default="absent"
    )
    template_group = parser.add_mutually_exclusive_group()
    template_group.add_argument(
        "--template-mode",
        choices=("auto", "none"),
        default="auto",
    )
    template_group.add_argument("--template-file")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        result = prepare(args)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    emit_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
