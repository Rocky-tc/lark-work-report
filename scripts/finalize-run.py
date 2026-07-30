#!/usr/bin/env python3
"""Render and validate a report inside one managed run, then write it atomically."""

import argparse
import json
import sys
from pathlib import Path

from contracts import PROFILE_PERIOD_LABELS
from runtime_utils import load_script, read_json, write_text_atomic
from template_profiles import load as load_template


SCRIPT_DIR = Path(__file__).resolve().parent
MANAGE_RUN = load_script(SCRIPT_DIR, "manage-run.py")
RENDER_REPORT = load_script(SCRIPT_DIR, "render-report.py")
VALIDATE_REPORT = load_script(SCRIPT_DIR, "validate-report.py")


def checked_file(run_dir, value, default_name, *, must_exist):
    path = Path(value) if value else run_dir / default_name
    if path.is_symlink():
        raise ValueError(f"managed run file cannot be a symlink: {path.name}")
    resolved = path.resolve(strict=must_exist)
    try:
        resolved.relative_to(run_dir)
    except ValueError as exc:
        raise ValueError("report workflow files must remain inside the managed run") from exc
    return resolved


def template_context(run_dir, plan, profile):
    identity_file = run_dir / "identity.json"
    if not identity_file.is_file() or identity_file.is_symlink():
        return None
    identity = read_json(identity_file, "identity")
    if not isinstance(identity, dict):
        return None
    subject = next(
        (
            identity.get(field)
            for field in ("display_name", "name", "subject", "user_name")
            if isinstance(identity.get(field), str) and identity.get(field).strip()
        ),
        None,
    )
    period_range = (
        plan.get("period", {}).get("title_period")
        if isinstance(plan, dict)
        else None
    )
    if not subject or not isinstance(period_range, str) or not period_range.strip():
        return None
    return {
        "subject": subject,
        "period_label": PROFILE_PERIOD_LABELS[profile],
        "period_range": period_range,
    }


def finalize(
    run_dir_value,
    model_value=None,
    ledger_value=None,
    output_value=None,
    template_value=None,
):
    run_dir = MANAGE_RUN.checked_run_dir(run_dir_value)
    plan_file = checked_file(run_dir, None, "run-plan.json", must_exist=True)
    model_file = checked_file(
        run_dir, model_value, "report-model.json", must_exist=True
    )
    ledger_file = checked_file(run_dir, ledger_value, "ledger.json", must_exist=True)
    output_file = checked_file(run_dir, output_value, "report.md", must_exist=False)
    if output_file in {model_file, ledger_file, plan_file}:
        raise ValueError("report output must differ from workflow inputs")
    if template_value:
        template_file = checked_file(
            run_dir,
            template_value,
            "template-profile.json",
            must_exist=True,
        )
    else:
        default_template = run_dir / "template-profile.json"
        template_file = (
            checked_file(
                run_dir,
                None,
                "template-profile.json",
                must_exist=True,
            )
            if default_template.is_file() and not default_template.is_symlink()
            else None
        )
    if template_file in {model_file, ledger_file, plan_file, output_file}:
        raise ValueError("template profile must differ from workflow files")

    plan = read_json(plan_file, "run plan")
    model = read_json(model_file, "report model")
    ledger = read_json(ledger_file, "evidence ledger")
    profile = (
        plan.get("period", {}).get("routed_profile")
        if isinstance(plan, dict)
        else None
    )
    if profile not in VALIDATE_REPORT.PROFILE_SECTIONS:
        raise ValueError("run plan has an invalid routed profile")
    if model.get("profile") != profile:
        raise ValueError("report model profile does not match the run plan")

    template = load_template(template_file) if template_file else None
    context = template_context(run_dir, plan, profile)
    if template is not None and context is None:
        raise ValueError(
            "template rendering requires identity display name and period title context"
        )
    markdown = RENDER_REPORT.render(model, template, context)
    validation = VALIDATE_REPORT.validate(markdown, profile, ledger, template)
    if not validation["ok"]:
        return {
            "ok": False,
            "report_file": str(output_file),
            "profile": profile,
            "template_id": template["template_id"] if template else None,
            "errors": validation["errors"],
            "warnings": validation["warnings"],
        }
    write_text_atomic(output_file, markdown)
    return {
        "ok": True,
        "report_file": str(output_file),
        "profile": profile,
        "template_id": template["template_id"] if template else None,
        "warnings": validation["warnings"],
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--model-file")
    parser.add_argument("--ledger-file")
    parser.add_argument("--output")
    parser.add_argument("--template-file")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        result = finalize(
            args.run_dir,
            args.model_file,
            args.ledger_file,
            args.output,
            args.template_file,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
