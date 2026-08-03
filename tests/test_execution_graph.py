import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import execution_graph  # noqa: E402


class ExecutionGraphTests(unittest.TestCase):
    def plan(self):
        return {
            "schema_version": 1,
            "identity_call": {
                "adapter_id": "test",
                "operation": "identity.current",
            },
            "template_call": None,
            "metadata_waves": [
                {"parallel": True, "calls": [{}, {}]},
                {"parallel": False, "calls": [{}]},
            ],
            "adapters": [{"delivery_mode": "document"}],
        }

    def test_new_static_graph_allows_only_empty_ledger_synthesis_bypass(self):
        graph = execution_graph.build(self.plan())
        semantic = [
            node["id"] for node in graph["nodes"] if node["kind"] == "semantic"
        ]
        self.assertEqual(semantic, ["classify", "extract", "synthesize"])
        synthesize = execution_graph.node_by_id(graph, "synthesize")
        self.assertEqual(synthesize["max_invocations"], 1)
        self.assertEqual(synthesize["cardinality"], "optional")
        self.assertEqual(synthesize["bypass_when"], "ledger_empty")
        self.assertTrue(
            graph["invariants"]["empty_ledger_synthesis_bypass"]
        )
        extract = execution_graph.node_by_id(graph, "extract")
        self.assertEqual(extract["batching"], "bounded")
        self.assertEqual(
            graph["stage_runtime_order"],
            ["fetch", "extract", "compile", "synthesize", "finalize", "complete"],
        )

    def test_compat_graph_without_descriptor_still_requires_synthesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            plan = self.plan()
            (run_dir / "run-plan.json").write_text(
                json.dumps(plan),
                encoding="utf-8",
            )

            graph, state = execution_graph.load_for_run(run_dir)

            synthesize = execution_graph.node_by_id(graph, "synthesize")
            self.assertEqual(state["mode"], "compat")
            self.assertEqual(synthesize["cardinality"], "once")
            self.assertNotIn("bypass_when", synthesize)
            self.assertFalse(
                execution_graph.supports_empty_synthesis_bypass(graph)
            )

    def test_legacy_static_graph_remains_loadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            plan = self.plan()
            descriptor = execution_graph.write(
                run_dir,
                execution_graph.build(
                    plan,
                    empty_ledger_synthesis_bypass=False,
                ),
            )
            plan["execution_graph"] = descriptor
            (run_dir / "run-plan.json").write_text(
                json.dumps(plan),
                encoding="utf-8",
            )

            graph, state = execution_graph.load_for_run(run_dir)

            self.assertEqual(state["mode"], "static")
            self.assertFalse(
                execution_graph.supports_empty_synthesis_bypass(graph)
            )

    def test_bypass_graph_rejects_an_extra_legacy_synthesis_route(self):
        graph = execution_graph.build(self.plan())
        graph["edges"].append(
            {"from": "compile", "to": "synthesize", "when": "ledger_ready"}
        )

        with self.assertRaisesRegex(ValueError, "synthesis routing"):
            execution_graph.validate(graph)

    def test_written_graph_is_digest_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            plan = self.plan()
            descriptor = execution_graph.write(
                run_dir,
                execution_graph.build(plan),
            )
            plan["execution_graph"] = descriptor
            (run_dir / "run-plan.json").write_text(
                json.dumps(plan),
                encoding="utf-8",
            )
            graph, state = execution_graph.load_for_run(run_dir)
            self.assertEqual(state["mode"], "static")
            self.assertEqual(graph["graph_id"], execution_graph.GRAPH_ID)

            graph["nodes"][0]["handler"] = "changed"
            (run_dir / execution_graph.GRAPH_FILE).write_text(
                json.dumps(graph),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "digest"):
                execution_graph.load_for_run(run_dir)


if __name__ == "__main__":
    unittest.main()
