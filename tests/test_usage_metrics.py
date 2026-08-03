import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
COMPARE = ROOT / "scripts" / "compare-runs.py"
sys.path.insert(0, str(ROOT / "scripts"))

import execution_graph  # noqa: E402
import empty_synthesis  # noqa: E402
import report_hydration  # noqa: E402
import usage_metrics  # noqa: E402


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def prepare_run(path, total_scale=1, model="same-model"):
    plan = {
        "schema_version": 1,
        "period": {
            "routed_profile": "weekly",
            "start": "2026-07-20T00:00:00+08:00",
            "end": "2026-07-27T00:00:00+08:00",
            "snapshot": "2026-07-27T00:00:00+08:00",
            "title_period": "2026-07-20 至 2026-07-26",
        },
        "requested_domains": ["docs"],
        "collected_domains": ["docs"],
        "identity_call": None,
        "template_call": None,
        "metadata_waves": [],
        "adapters": [],
    }
    descriptor = execution_graph.write(path, execution_graph.build(plan))
    plan["execution_graph"] = descriptor
    write_json(
        path / "run-plan.json",
        plan,
    )
    write_json(
        path / "request-context.json",
        {
            "schema_version": 1,
            "request": "整理本周周报",
            "scope": [],
            "exclusions": [],
            "emphasis": [],
        },
    )
    write_json(path / "identity.json", {"display_name": "测试用户"})
    write_json(
        path / "fetch-queue.json",
        {
            "schema_version": 2,
            "candidate_index": {
                "a": {
                    "title": "同一事项",
                    "source_ref": "https://example.com/a",
                }
            },
            "fetch_batches": [],
            "fetch_waves": [],
        },
    )
    write_json(
        path / "semantic-bodies" / "a.json",
        {"schema_version": 1, "items": [{"content": "完整相同正文"}]},
    )
    write_json(
        path / "ledger.json",
        {
            "work": [
                {
                    "cluster_id": "c1",
                    "source_refs": ["https://example.com/a"],
                }
            ],
            "uncertain": [],
        },
    )
    write_json(
        path / "extraction-audit.json",
        {"outcome_counts": {"work": 1, "uncertain": 0}},
    )
    (path / "report.md").write_text("# 已完成\n", encoding="utf-8")
    entries = []
    for index, node_id in enumerate(("classify", "extract", "synthesize"), 1):
        input_tokens = 80 * total_scale
        output_tokens = 20 * total_scale
        entries.append(
            {
                "node_id": node_id,
                "invocation_id": f"{node_id}-{index}",
                "elapsed_ms": 100 * total_scale,
                "usage_source": "host_file",
                "usage": {
                    "schema_version": 1,
                    "provider": "test-provider",
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                },
            }
        )
    write_json(
        path / "stage-metrics.json",
        {"schema_version": 1, "entries": entries},
    )
    aggregate_input = 240 * total_scale
    aggregate_output = 60 * total_scale
    write_json(
        path / "run-usage.json",
        {
            "schema_version": 1,
            "graph_id": "lark-work-report-v1",
            "graph_digest": descriptor["digest"],
            "elapsed_ms": 300 * total_scale,
            "usage_source": "host_file",
            "usage": {
                "schema_version": 1,
                "provider": "test-provider",
                "model": model,
                "input_tokens": aggregate_input,
                "output_tokens": aggregate_output,
                "total_tokens": aggregate_input + aggregate_output,
            },
        },
    )


def write_obligation_output(path, *, include_next_action):
    write_json(
        path / "ledger.json",
        {
            "schema_version": 2,
            "snapshot": "2026-07-27T00:00:00+08:00",
            "work": [
                {
                    "cluster_id": "c1",
                    "source_ref": "https://example.com/a",
                    "source_refs": ["https://example.com/a"],
                    "status": "completed",
                    "output": "完成同一事项",
                    "next_action": "观察真实运行效果",
                }
            ],
            "uncertain": [],
        },
    )
    write_json(
        path / "report-model.json",
        {
            "schema_version": 3,
            "summary": [
                {
                    "result": "完成同一事项",
                    "evidence_ids": ["c1"],
                }
            ],
            "workstreams": [
                {
                    "name": "同一事项",
                    "status": "completed",
                    "result": "完成同一事项",
                    "evidence_ids": ["c1"],
                }
            ],
            "risks": [],
            "next_actions": (
                [
                    {
                        "action": "观察真实运行效果",
                        "evidence_ids": ["c1"],
                    }
                ]
                if include_next_action
                else []
            ),
            "uncertain": [],
        },
    )


def remove_node_metric(path, node_id):
    metrics_path = path / "stage-metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["entries"] = [
        entry
        for entry in metrics["entries"]
        if entry.get("node_id") != node_id
    ]
    write_json(metrics_path, metrics)


def sync_aggregate_usage(path):
    metrics = json.loads(
        (path / "stage-metrics.json").read_text(encoding="utf-8")
    )
    entries = metrics["entries"]
    aggregate = json.loads(
        (path / "run-usage.json").read_text(encoding="utf-8")
    )
    aggregate["elapsed_ms"] = sum(entry["elapsed_ms"] for entry in entries)
    aggregate["usage"]["input_tokens"] = sum(
        entry["usage"]["input_tokens"] for entry in entries
    )
    aggregate["usage"]["output_tokens"] = sum(
        entry["usage"]["output_tokens"] for entry in entries
    )
    aggregate["usage"]["total_tokens"] = (
        aggregate["usage"]["input_tokens"]
        + aggregate["usage"]["output_tokens"]
    )
    write_json(path / "run-usage.json", aggregate)


def configure_empty_run(
    path,
    *,
    keep_synthesis_usage,
    write_bypass_receipt,
    report="# 空报告\n",
):
    write_json(
        path / "semantic-bodies" / "a.json",
        {
            "schema_version": 1,
            "items": [],
            "access_gaps": [
                {
                    "global_id": "a",
                    "outcome": "access_gap",
                    "reason": "forbidden",
                }
            ],
        },
    )
    ledger = {
        "schema_version": 2,
        "snapshot": "2026-07-27T00:00:00+08:00",
        "work": [],
        "uncertain": [],
    }
    write_json(path / "ledger.json", ledger)
    write_json(
        path / "extraction-audit.json",
        {
            "schema_version": 1,
            "total_count": 1,
            "processed_count": 1,
            "outcome_counts": {"access_gap": 1},
            "access_gaps": [{"reason": "forbidden"}],
        },
    )
    write_json(path / "report-model.json", empty_synthesis.canonical_model(ledger))
    (path / "report.md").write_text(report, encoding="utf-8")
    remove_node_metric(path, "extract")
    if not keep_synthesis_usage:
        remove_node_metric(path, "synthesize")
    sync_aggregate_usage(path)
    if write_bypass_receipt:
        plan = json.loads((path / "run-plan.json").read_text(encoding="utf-8"))
        graph, graph_state = execution_graph.load_for_run(path, plan)
        empty_synthesis.write_receipt(path, graph, graph_state)


class UsageMetricsTests(unittest.TestCase):
    def test_access_gap_only_semantic_bodies_do_not_require_extract_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            prepare_run(run_dir)
            write_json(
                run_dir / "semantic-bodies" / "a.json",
                {
                    "schema_version": 1,
                    "items": [],
                    "access_gaps": [
                        {
                            "global_id": "a",
                            "outcome": "access_gap",
                            "reason": "forbidden",
                        }
                    ],
                },
            )
            remove_node_metric(run_dir, "extract")

            summary = usage_metrics.run_summary(run_dir)

            self.assertNotIn("extract", summary["missing_real_usage"])

    def test_nonempty_semantic_body_requires_extract_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            prepare_run(run_dir)
            remove_node_metric(run_dir, "extract")

            summary = usage_metrics.run_summary(run_dir)

            self.assertIn("extract", summary["missing_real_usage"])

    def test_valid_empty_synthesis_receipt_exempts_and_reports_bypass(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            prepare_run(run_dir)
            configure_empty_run(
                run_dir,
                keep_synthesis_usage=False,
                write_bypass_receipt=True,
            )

            summary = usage_metrics.run_summary(run_dir)

            self.assertEqual(summary["missing_real_usage"], [])
            self.assertEqual(
                summary["bypassed_nodes"]["synthesize"]["reason"],
                "empty_ledger",
            )
            self.assertTrue(
                summary["bypassed_nodes"]["synthesize"][
                    "receipt_digest"
                ].startswith("sha256:")
            )
            self.assertNotIn("synthesize", summary["node_totals"])

    def test_stale_empty_synthesis_receipt_does_not_exempt_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            prepare_run(run_dir)
            configure_empty_run(
                run_dir,
                keep_synthesis_usage=False,
                write_bypass_receipt=True,
            )
            (run_dir / "report.md").write_text("# 被改写\n", encoding="utf-8")

            summary = usage_metrics.run_summary(run_dir)

            self.assertEqual(summary["bypassed_nodes"], {})
            self.assertIn("synthesize", summary["missing_real_usage"])

    def test_empty_synthesis_receipt_conflicts_with_synthesis_metric(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            prepare_run(run_dir)
            configure_empty_run(
                run_dir,
                keep_synthesis_usage=True,
                write_bypass_receipt=True,
            )

            summary = usage_metrics.run_summary(run_dir)

            self.assertEqual(
                summary["usage_conflicts"],
                ["synthesize usage conflicts with empty-ledger bypass"],
            )

    def test_compare_requires_same_data_model_and_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            candidate = root / "candidate"
            prepare_run(baseline, total_scale=2)
            prepare_run(candidate, total_scale=1)
            result = subprocess.run(
                [
                    sys.executable,
                    str(COMPARE),
                    "--baseline-run",
                    str(baseline),
                    "--candidate-run",
                    str(candidate),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            comparison = json.loads(result.stdout)
            self.assertTrue(comparison["comparable"])
            self.assertEqual(comparison["total_tokens"]["baseline"], 600)
            self.assertEqual(comparison["total_tokens"]["candidate"], 300)
            self.assertEqual(comparison["total_tokens"]["delta_percent"], -50.0)

    def test_compare_refuses_different_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            candidate = root / "candidate"
            prepare_run(baseline, model="model-a")
            prepare_run(candidate, model="model-b")
            result = subprocess.run(
                [
                    sys.executable,
                    str(COMPARE),
                    "--baseline-run",
                    str(baseline),
                    "--candidate-run",
                    str(candidate),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 3)
            comparison = json.loads(result.stdout)
            self.assertFalse(comparison["comparable"])
            self.assertIn(
                "semantic node provider/model sets differ",
                comparison["errors"],
            )

    def test_compare_allows_valid_asymmetric_synthesis_bypass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            candidate = root / "candidate"
            prepare_run(baseline)
            prepare_run(candidate)
            configure_empty_run(
                baseline,
                keep_synthesis_usage=True,
                write_bypass_receipt=False,
            )
            configure_empty_run(
                candidate,
                keep_synthesis_usage=False,
                write_bypass_receipt=True,
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(COMPARE),
                    "--baseline-run",
                    str(baseline),
                    "--candidate-run",
                    str(candidate),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            comparison = json.loads(result.stdout)
            self.assertTrue(comparison["comparable"])
            self.assertEqual(
                comparison["candidate"]["bypassed_nodes"]["synthesize"][
                    "reason"
                ],
                "empty_ledger",
            )
            self.assertEqual(
                comparison["by_node"]["synthesize"]["invocations"],
                {
                    "baseline": 1,
                    "candidate": 0,
                    "delta": -1,
                    "delta_percent": -100.0,
                },
            )

    def test_compare_rejects_asymmetric_bypass_when_reports_differ(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            candidate = root / "candidate"
            prepare_run(baseline)
            prepare_run(candidate)
            configure_empty_run(
                baseline,
                keep_synthesis_usage=True,
                write_bypass_receipt=False,
                report="# 基线报告\n",
            )
            configure_empty_run(
                candidate,
                keep_synthesis_usage=False,
                write_bypass_receipt=True,
                report="# 候选报告\n",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(COMPARE),
                    "--baseline-run",
                    str(baseline),
                    "--candidate-run",
                    str(candidate),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 3, result.stderr)
            comparison = json.loads(result.stdout)
            self.assertFalse(comparison["comparable"])
            self.assertIn(
                "reports differ across asymmetric synthesis bypass",
                comparison["errors"],
            )

    def test_compare_rejects_bypass_receipt_with_synthesis_metric(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            candidate = root / "candidate"
            prepare_run(baseline)
            prepare_run(candidate)
            configure_empty_run(
                baseline,
                keep_synthesis_usage=True,
                write_bypass_receipt=False,
                report="# 基线报告\n",
            )
            configure_empty_run(
                candidate,
                keep_synthesis_usage=True,
                write_bypass_receipt=True,
                report="# 候选报告\n",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(COMPARE),
                    "--baseline-run",
                    str(baseline),
                    "--candidate-run",
                    str(candidate),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 3, result.stderr)
            comparison = json.loads(result.stdout)
            self.assertFalse(comparison["comparable"])
            self.assertIn(
                "candidate has invalid usage state: synthesize usage conflicts "
                "with empty-ledger bypass",
                comparison["errors"],
            )
            self.assertIn(
                "reports differ across asymmetric synthesis bypass",
                comparison["errors"],
            )

    def test_compare_rejects_missing_synthesis_without_valid_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            candidate = root / "candidate"
            prepare_run(baseline)
            prepare_run(candidate)
            configure_empty_run(
                baseline,
                keep_synthesis_usage=True,
                write_bypass_receipt=False,
            )
            configure_empty_run(
                candidate,
                keep_synthesis_usage=False,
                write_bypass_receipt=False,
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(COMPARE),
                    "--baseline-run",
                    str(baseline),
                    "--candidate-run",
                    str(candidate),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 3, result.stderr)
            comparison = json.loads(result.stdout)
            self.assertFalse(comparison["comparable"])
            self.assertIn(
                "candidate lacks host-reported usage for: synthesize",
                comparison["errors"],
            )

    def test_compare_rejects_other_missing_node_despite_synthesis_bypass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            candidate = root / "candidate"
            prepare_run(baseline)
            prepare_run(candidate)
            configure_empty_run(
                baseline,
                keep_synthesis_usage=True,
                write_bypass_receipt=False,
            )
            configure_empty_run(
                candidate,
                keep_synthesis_usage=False,
                write_bypass_receipt=True,
            )
            remove_node_metric(candidate, "classify")
            sync_aggregate_usage(candidate)

            result = subprocess.run(
                [
                    sys.executable,
                    str(COMPARE),
                    "--baseline-run",
                    str(baseline),
                    "--candidate-run",
                    str(candidate),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 3, result.stderr)
            comparison = json.loads(result.stdout)
            self.assertFalse(comparison["comparable"])
            self.assertIn(
                "candidate lacks host-reported usage for: classify",
                comparison["errors"],
            )

    def test_compare_refuses_candidate_with_omitted_next_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            candidate = root / "candidate"
            prepare_run(baseline)
            prepare_run(candidate)
            write_obligation_output(baseline, include_next_action=True)
            write_obligation_output(candidate, include_next_action=False)

            result = subprocess.run(
                [
                    sys.executable,
                    str(COMPARE),
                    "--baseline-run",
                    str(baseline),
                    "--candidate-run",
                    str(candidate),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 3, result.stderr)
            comparison = json.loads(result.stdout)
            self.assertFalse(comparison["quality_equal"])
            self.assertIn("quality signatures differ", comparison["errors"])
            self.assertEqual(
                comparison["candidate"]["quality"]["field_obligations"][
                    "missing"
                ],
                ["c1.next_action->next_actions.action"],
            )

    def test_quality_signature_hydrates_current_v5_short_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            source = {
                "schema_version": 2,
                "snapshot": "2026-07-27T00:00:00+08:00",
                "work": [
                    {
                        "cluster_id": "c1",
                        "record_ids": ["record-1"],
                        "source_ref": "https://example.com/a",
                        "source_refs": ["https://example.com/a"],
                        "source_types": ["docs"],
                        "status_conflict": False,
                        "workstream": "同一事项",
                        "status": "completed",
                        "output": "完成同一事项",
                        "next_action": "观察真实运行效果",
                        "priority": 10,
                    }
                ],
                "uncertain": [],
            }
            write_json(run_dir / "ledger.json", source)
            write_json(
                run_dir / "run-plan.json",
                {
                    "period": {
                        "routed_profile": "weekly",
                        "start": "2026-07-20T00:00:00+08:00",
                        "end": "2026-07-27T00:00:00+08:00",
                        "snapshot": "2026-07-27T00:00:00+08:00",
                        "title_period": "2026-07-20 至 2026-07-26",
                    },
                    "collected_domains": ["docs"],
                },
            )
            write_json(run_dir / "identity.json", {"display_name": "测试用户"})
            write_json(
                run_dir / "report-model.json",
                {
                    "schema_version": 5,
                    "ledger_fingerprint": report_hydration.ledger_fingerprint(
                        source
                    ),
                    "summary": [{"evidence_refs": ["w0"]}],
                    "workstreams": [{"evidence_refs": ["w0"]}],
                    "risks": [],
                    "next_actions": [{"evidence_refs": ["w0"]}],
                    "uncertain": [],
                },
            )
            (run_dir / "report.md").write_text("# 已完成\n", encoding="utf-8")

            signature = usage_metrics.quality_signature(run_dir)

            obligations = signature["field_obligations"]
            self.assertEqual(obligations["missing"], [])
            self.assertEqual(obligations["section_evidence"]["summary"], [["c1"]])
            self.assertEqual(
                obligations["section_evidence"]["next_actions"],
                [["c1"]],
            )


if __name__ == "__main__":
    unittest.main()
