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
        },
        "requested_domains": ["docs"],
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


class UsageMetricsTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
