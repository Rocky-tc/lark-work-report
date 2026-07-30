import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "prepare-run.py"
MANAGER = ROOT / "scripts" / "manage-run.py"


def adapter_manifest(*, parallel=False, batch=False, template=False):
    listed = {
        "available": True,
        "metadata_only": True,
        "domains": ["docs", "im", "tasks"],
    }
    if parallel:
        listed.update({"parallel_safe": True, "max_parallelism": 2})
    return {
        "adapter_id": "test.adapter",
        "capabilities": {
            "identity.current": {"available": True},
            "candidate.list": listed,
            "candidate.list_many": {
                "available": batch,
                **({"max_domains": 2} if batch else {}),
            },
            "candidate.fetch": {"available": True},
            "candidate.fetch_many": {
                "available": True,
                "max_batch_size": 10,
            },
            "report.create": {"available": False},
            "report.fetch": {"available": False},
            "template.fetch": {"available": template},
            "template.upsert": {"available": template},
            "template.delete": {"available": template},
        },
    }


def offline_manifest():
    return {
        "adapter_id": "offline.export",
        "capabilities": {
            "identity.current": {"available": False},
            "candidate.list": {"available": False},
            "candidate.fetch": {"available": False},
            "report.create": {"available": False},
            "report.fetch": {"available": False},
        },
    }


def valid_template():
    names = {
        "summary": ("今日摘要", "本周摘要", "月度摘要"),
        "progress": ("工作进展与结果", "工作流进展与结果", "目标与工作流进展"),
        "risks": ("风险与需协助事项", "风险与需协助事项", "风险与依赖"),
        "next": ("明日重点", "下周重点", "下月重点"),
        "uncertain": ("待复核", "待复核", "待复核"),
        "coverage": ("来源与覆盖", "来源与覆盖", "来源与覆盖"),
    }
    return {
        "schema_version": 1,
        "template_id": "default",
        "source_fingerprint": "sha256:" + hashlib.sha256(b"sample").hexdigest(),
        "title_pattern": "{subject}{period_label}｜{period_range}",
        "sections": {
            slot: {
                "labels": dict(zip(("daily", "weekly", "monthly"), labels)),
                "item_style": "bullet",
            }
            for slot, labels in names.items()
        },
        "workstream_layout": "inline",
        "field_labels": {
            "impact": "影响",
            "decision": "决策",
            "progress": "进展",
            "assistance": "需协助",
            "purpose": "目标",
            "reason": "原因",
        },
        "tone": {
            "register": "concise",
            "voice": "neutral",
            "density": "compact",
        },
    }


def run_prepare(manifest, *extra):
    tmp = tempfile.TemporaryDirectory()
    adapters = Path(tmp.name) / "adapters.json"
    adapters.write_text(
        json.dumps({"adapters": [manifest]}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--adapters-file",
            str(adapters),
            "--period",
            "weekly",
            "--reference",
            "2026-07-27",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return tmp, result


def cleanup(run_dir):
    subprocess.run(
        [sys.executable, str(MANAGER), "cleanup", "--run-dir", run_dir],
        capture_output=True,
        text=True,
        check=False,
    )


class PrepareRunTests(unittest.TestCase):
    def test_batch_listing_builds_domain_batches(self):
        tmp, result = run_prepare(adapter_manifest(batch=True))
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            run_dir = summary["run_dir"]
            plan = json.loads(Path(summary["plan_file"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["identity_call_count"], 1)
            self.assertEqual(
                plan["identity_call"],
                {
                    "adapter_id": "test.adapter",
                    "operation": "identity.current",
                },
            )
            self.assertEqual(summary["metadata_call_count"], 2)
            self.assertEqual(summary["metadata_wave_count"], 2)
            self.assertEqual(
                [call["domains"] for wave in plan["metadata_waves"] for call in wave["calls"]],
                [["tasks", "docs"], ["im"]],
            )
            self.assertTrue(
                all(
                    call["operation"] == "candidate.list_many"
                    for wave in plan["metadata_waves"]
                    for call in wave["calls"]
                )
            )
        finally:
            if "run_dir" in locals():
                cleanup(run_dir)
            tmp.cleanup()

    def test_parallel_listing_builds_bounded_parallel_waves(self):
        tmp, result = run_prepare(adapter_manifest(parallel=True))
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            run_dir = summary["run_dir"]
            plan = json.loads(Path(summary["plan_file"]).read_text(encoding="utf-8"))
            self.assertEqual([len(wave["calls"]) for wave in plan["metadata_waves"]], [2, 1])
            self.assertTrue(plan["metadata_waves"][0]["parallel"])
            self.assertFalse(plan["metadata_waves"][1]["parallel"])
        finally:
            if "run_dir" in locals():
                cleanup(run_dir)
            tmp.cleanup()

    def test_default_domains_follow_evidence_yield_priority(self):
        payload = adapter_manifest(batch=True)
        payload["capabilities"]["candidate.list"]["domains"] = [
            "ai_sessions",
            "docs",
            "mentions",
            "calendar",
            "tasks",
        ]
        tmp, result = run_prepare(payload)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            run_dir = summary["run_dir"]
            plan = json.loads(Path(summary["plan_file"]).read_text(encoding="utf-8"))
            self.assertEqual(
                plan["requested_domains"],
                ["tasks", "calendar", "mentions", "docs", "ai_sessions"],
            )
            self.assertEqual(
                [
                    domain
                    for wave in plan["metadata_waves"]
                    for call in wave["calls"]
                    for domain in call["domains"]
                ],
                ["tasks", "calendar", "mentions", "docs", "ai_sessions"],
            )
        finally:
            if "run_dir" in locals():
                cleanup(run_dir)
            tmp.cleanup()

    def test_domain_coverage_avoids_redundant_metadata_queries(self):
        payload = adapter_manifest(batch=True)
        payload["capabilities"]["candidate.list"].update(
            {
                "domains": ["im", "docs"],
                "domain_coverage": {
                    "im": ["im", "mentions"],
                    "docs": ["docs", "comments"],
                },
            }
        )
        tmp, result = run_prepare(payload)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            run_dir = summary["run_dir"]
            plan = json.loads(Path(summary["plan_file"]).read_text(encoding="utf-8"))
            self.assertEqual(
                plan["requested_domains"],
                ["mentions", "comments", "docs", "im"],
            )
            self.assertEqual(summary["metadata_call_count"], 1)
            call = plan["metadata_waves"][0]["calls"][0]
            self.assertEqual(call["domains"], ["im", "docs"])
            self.assertEqual(
                call["covered_domains"],
                ["mentions", "comments", "docs", "im"],
            )
            self.assertEqual(plan["unassigned_domains"], [])
        finally:
            if "run_dir" in locals():
                cleanup(run_dir)
            tmp.cleanup()

    def test_explicit_related_domain_can_use_covering_query(self):
        payload = adapter_manifest()
        payload["capabilities"]["candidate.list"].update(
            {
                "domains": ["im"],
                "domain_coverage": {"im": ["im", "mentions"]},
            }
        )
        tmp, result = run_prepare(payload, "--domain", "mentions")
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            run_dir = summary["run_dir"]
            plan = json.loads(Path(summary["plan_file"]).read_text(encoding="utf-8"))
            call = plan["metadata_waves"][0]["calls"][0]
            self.assertEqual(call["domains"], ["im"])
            self.assertEqual(call["covered_domains"], ["mentions"])
        finally:
            if "run_dir" in locals():
                cleanup(run_dir)
            tmp.cleanup()

    def test_auto_template_fetch_is_planned_and_budgeted(self):
        tmp, result = run_prepare(adapter_manifest(template=True))
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            run_dir = summary["run_dir"]
            plan = json.loads(Path(summary["plan_file"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["template_call_count"], 1)
            self.assertEqual(summary["template_mode"], "auto")
            self.assertEqual(
                plan["template_call"],
                {
                    "adapter_id": "test.adapter",
                    "operation": "template.fetch",
                    "template_id": "default",
                },
            )
            state = json.loads(
                (Path(run_dir) / ".run-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["base_hard_limit"], 32)
            self.assertEqual(state["hard_limit"], 33)
        finally:
            if "run_dir" in locals():
                cleanup(run_dir)
            tmp.cleanup()

    def test_template_mode_none_skips_fetch(self):
        tmp, result = run_prepare(
            adapter_manifest(template=True),
            "--template-mode",
            "none",
        )
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            run_dir = summary["run_dir"]
            plan = json.loads(Path(summary["plan_file"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["template_call_count"], 0)
            self.assertEqual(plan["template_mode"], "none")
            self.assertIsNone(plan["template_call"])
        finally:
            if "run_dir" in locals():
                cleanup(run_dir)
            tmp.cleanup()

    def test_one_off_template_is_copied_without_persistent_fetch(self):
        with tempfile.TemporaryDirectory() as template_tmp:
            template_file = Path(template_tmp) / "template.json"
            template_file.write_text(
                json.dumps(valid_template(), ensure_ascii=False),
                encoding="utf-8",
            )
            tmp, result = run_prepare(
                adapter_manifest(template=True),
                "--template-file",
                str(template_file),
            )
            try:
                self.assertEqual(result.returncode, 0, result.stderr)
                summary = json.loads(result.stdout)
                run_dir = summary["run_dir"]
                plan = json.loads(
                    Path(summary["plan_file"]).read_text(encoding="utf-8")
                )
                self.assertEqual(summary["template_mode"], "one_off")
                self.assertEqual(summary["template_call_count"], 0)
                self.assertIsNone(plan["template_call"])
                copied = Path(run_dir) / "template-profile.json"
                self.assertEqual(json.loads(copied.read_text()), valid_template())
            finally:
                if "run_dir" in locals():
                    cleanup(run_dir)
                tmp.cleanup()

    def test_unassigned_domains_are_reported_without_extra_details(self):
        tmp, result = run_prepare(
            adapter_manifest(),
            "--domain",
            "docs,calendar",
        )
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            run_dir = summary["run_dir"]
            self.assertEqual(summary["unassigned_domains"], ["calendar"])
            self.assertNotIn("metadata_waves", summary)
        finally:
            if "run_dir" in locals():
                cleanup(run_dir)
            tmp.cleanup()

    def test_offline_run_uses_user_provided_identity(self):
        tmp, result = run_prepare(offline_manifest())
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            run_dir = summary["run_dir"]
            plan = json.loads(Path(summary["plan_file"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["identity_call_count"], 0)
            self.assertIsNone(plan["identity_call"])
            self.assertEqual(plan["metadata_waves"], [])
        finally:
            if "run_dir" in locals():
                cleanup(run_dir)
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
