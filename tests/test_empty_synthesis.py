import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
import sys

sys.path.insert(0, str(ROOT / "scripts"))

import empty_synthesis  # noqa: E402
import execution_graph  # noqa: E402
import report_hydration  # noqa: E402


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


class EmptySynthesisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name)
        self.plan = {
            "period": {
                "routed_profile": "weekly",
                "start": "2026-07-20T00:00:00+08:00",
                "end": "2026-07-27T00:00:00+08:00",
                "snapshot": "2026-07-27T00:00:00+08:00",
                "title_period": "2026-07-20 至 2026-07-26",
            },
            "metadata_waves": [],
            "adapters": [],
        }
        self.graph = execution_graph.build(self.plan)
        descriptor = execution_graph.write(self.run_dir, self.graph)
        self.graph_state = {
            "graph_id": descriptor["graph_id"],
            "digest": descriptor["digest"],
            "mode": "static",
            "file": str(self.run_dir / descriptor["file"]),
        }
        self.ledger = {
            "schema_version": 2,
            "snapshot": "2026-07-27T00:00:00+08:00",
            "work": [],
            "uncertain": [],
        }
        write_json(self.run_dir / "run-plan.json", self.plan)
        write_json(self.run_dir / "ledger.json", self.ledger)
        write_json(
            self.run_dir / "extraction-audit.json",
            {
                "schema_version": 1,
                "total_count": 1,
                "processed_count": 1,
                "outcome_counts": {"access_gap": 1},
                "access_gaps": [{"reason": "无权限"}],
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_canonical_model_is_v5_with_five_empty_sections(self):
        model = empty_synthesis.canonical_model(self.ledger)

        self.assertEqual(
            model,
            {
                "schema_version": 5,
                "ledger_fingerprint": report_hydration.ledger_fingerprint(
                    self.ledger
                ),
                "summary": [],
                "workstreams": [],
                "risks": [],
                "next_actions": [],
                "uncertain": [],
            },
        )

    def test_existing_noncanonical_model_is_not_overwritten(self):
        write_json(
            self.run_dir / "report-model.json",
            {
                **empty_synthesis.canonical_model(self.ledger),
                "summary": [{"result": "不应存在"}],
            },
        )

        with self.assertRaisesRegex(ValueError, "does not match"):
            empty_synthesis.ensure_model(
                self.run_dir,
                self.graph,
                self.ledger,
            )
        stored = json.loads(
            (self.run_dir / "report-model.json").read_text(encoding="utf-8")
        )
        self.assertEqual(stored["summary"], [{"result": "不应存在"}])

    def test_receipt_verifies_graph_guards_model_and_report(self):
        model = empty_synthesis.ensure_model(
            self.run_dir,
            self.graph,
            self.ledger,
        )
        (self.run_dir / "report.md").write_text("# 空报告\n", encoding="utf-8")

        receipt = empty_synthesis.write_receipt(
            self.run_dir,
            self.graph,
            self.graph_state,
        )
        verified = empty_synthesis.verify_receipt(
            self.run_dir,
            self.graph,
            self.graph_state,
        )

        self.assertEqual(model["summary"], [])
        self.assertEqual(receipt["reason"], "empty_ledger")
        self.assertEqual(verified["node_id"], "synthesize")
        self.assertEqual(verified["reason"], "empty_ledger")
        self.assertTrue(verified["receipt_digest"].startswith("sha256:"))

        (self.run_dir / "report.md").write_text("# 被改写\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "receipt"):
            empty_synthesis.verify_receipt(
                self.run_dir,
                self.graph,
                self.graph_state,
            )

    def test_absent_receipt_returns_none_but_invalid_receipt_fails_closed(self):
        self.assertIsNone(
            empty_synthesis.verify_receipt(
                self.run_dir,
                self.graph,
                self.graph_state,
            )
        )
        write_json(
            self.run_dir / empty_synthesis.RECEIPT_FILE,
            {"schema_version": 1},
        )
        with self.assertRaisesRegex(ValueError, "receipt"):
            empty_synthesis.verify_receipt(
                self.run_dir,
                self.graph,
                self.graph_state,
            )

    def test_receipt_guards_the_absence_of_optional_context_files(self):
        empty_synthesis.ensure_model(
            self.run_dir,
            self.graph,
            self.ledger,
        )
        (self.run_dir / "report.md").write_text("# 空报告\n", encoding="utf-8")
        empty_synthesis.write_receipt(
            self.run_dir,
            self.graph,
            self.graph_state,
        )

        write_json(self.run_dir / "identity.json", {"display_name": "后来者"})

        with self.assertRaisesRegex(ValueError, "receipt"):
            empty_synthesis.verify_receipt(
                self.run_dir,
                self.graph,
                self.graph_state,
            )

    def test_old_graph_and_nonempty_ledger_are_not_eligible(self):
        legacy = execution_graph.build(
            self.plan,
            empty_ledger_synthesis_bypass=False,
        )
        self.assertFalse(empty_synthesis.is_eligible(legacy, self.ledger))
        nonempty = {
            **self.ledger,
            "work": [
                {
                    "cluster_id": "c1",
                    "source_refs": ["https://example.com/1"],
                }
            ],
        }
        self.assertFalse(empty_synthesis.is_eligible(self.graph, nonempty))
        with self.assertRaisesRegex(ValueError, "empty"):
            empty_synthesis.canonical_model(nonempty)


if __name__ == "__main__":
    unittest.main()
