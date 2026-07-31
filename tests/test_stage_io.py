import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
MANAGER = ROOT / "scripts" / "manage-run.py"
STAGE_IO = ROOT / "scripts" / "stage-io.py"


def run(command, *args):
    return subprocess.run(
        [sys.executable, str(command), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


class StageIoTests(unittest.TestCase):
    def setUp(self):
        created = run(MANAGER, "create", "--profile", "weekly")
        self.assertEqual(created.returncode, 0, created.stderr)
        self.run_dir = Path(json.loads(created.stdout)["run_dir"])
        self.global_id = "adapter-a:docs:document-with-a-very-long-stable-id"
        write_json(
            self.run_dir / "run-plan.json",
            {
                "schema_version": 1,
                "period": {
                    "routed_profile": "weekly",
                    "start": "2026-07-20T00:00:00+08:00",
                    "end": "2026-07-27T00:00:00+08:00",
                    "snapshot": "2026-07-27T12:00:00+08:00",
                    "title_period": "2026-07-20 至 2026-07-26",
                },
                "requested_domains": ["docs"],
                "unassigned_domains": [],
            },
        )
        write_json(
            self.run_dir / "identity.json",
            {"display_name": "张三"},
        )
        candidate = {
            "id": "document-with-a-very-long-stable-id",
            "adapter_id": "adapter-a",
            "source_type": "docs",
            "occurred_at": "2026-07-25T10:00:00+08:00",
            "source_ref": "https://example.com/docs/long-document-reference",
            "prefetch_relevance": "work",
            "classification_reason": "标题与工作项目明确相关",
            "title": "工作报告优化",
        }
        batch = {
            "batch_id": "fetch-0001",
            "adapter_id": "adapter-a",
            "operation": "candidate.fetch",
            "parallelism": 2,
            "file_output": True,
            "body_file": "fetch-results/fetch-0001.json",
            "semantic_file": "semantic-bodies/fetch-0001.json",
            "evidence_file": "evidence-parts/fetch-0001.json",
            "request_file": "fetch-requests/fetch-0001.json",
            "global_ids": [self.global_id],
        }
        write_json(
            self.run_dir / "fetch-queue.json",
            {
                "schema_version": 2,
                "candidate_index": {self.global_id: candidate},
                "fetch_batches": [batch],
                "fetch_waves": [
                    {"wave": 1, "parallel": False, "batch_ids": ["fetch-0001"]}
                ],
                "included_counts": {"work": 1, "uncertain": 0},
                "deduplicated_count": 0,
            },
        )
        write_json(
            self.run_dir / "fetch-requests" / "fetch-0001.json",
            {
                "schema_version": 1,
                "batch_id": "fetch-0001",
                "adapter_id": "adapter-a",
                "operation": "candidate.fetch",
                "items": [{"global_id": self.global_id, **candidate}],
            },
        )

    def tearDown(self):
        if self.run_dir.exists():
            run(MANAGER, "cleanup", "--run-dir", str(self.run_dir))

    def next(self):
        result = run(STAGE_IO, "next", "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def commit(self, payload, name):
        result_file = self.run_dir / name
        write_json(result_file, payload)
        result = run(
            STAGE_IO,
            "commit",
            "--run-dir",
            str(self.run_dir),
            "--result-file",
            str(result_file),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def stage_result(self, packet, **fields):
        return {
            "schema_version": 1,
            "package_id": packet["package_id"],
            "input_digest": packet["input_digest"],
            **fields,
        }

    def load_packet(self, state):
        return json.loads(Path(state["packet_file"]).read_text(encoding="utf-8"))

    def complete_fetch(self):
        state = self.next()
        self.assertEqual(state["stage"], "fetch")
        packet = self.load_packet(state)
        serialized = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn(self.global_id, serialized)
        self.assertNotIn("source_ref", serialized)
        return self.commit(
            self.stage_result(
                packet,
                batches=[
                    {
                        "batch_ref": "b0",
                        "results": [
                            {
                                "item_ref": "i0",
                                "content_type": "text",
                                "content": "完成报告结构优化并通过测试。",
                                "context": {"section": "结果"},
                            }
                        ],
                    }
                ],
            ),
            "fetch-result.json",
        )

    def complete_extract(self):
        committed = self.complete_fetch()
        state = committed["next"]
        self.assertEqual(state["stage"], "extract")
        packet = self.load_packet(state)
        serialized = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn(self.global_id, serialized)
        self.assertNotIn("source_ref", serialized)
        self.assertNotIn("content_fingerprint", serialized)
        return self.commit(
            self.stage_result(
                packet,
                batches=[
                    {
                        "batch_ref": "b0",
                        "results": [
                            {
                                "item_ref": "i0",
                                "outcome": "work",
                                "record": {
                                    "title": "报告结构优化",
                                    "actor": "当前用户",
                                    "workstream": "工作报告 Skill",
                                    "activity": "优化报告结构",
                                    "status": "completed",
                                    "status_basis": "explicit",
                                    "output": "完成实现并通过测试",
                                    "impact": "降低综合阶段上下文体积",
                                    "next_action": "观察真实运行效果",
                                    "signal_kind": "outcome",
                                    "requires_response": False,
                                    "assignee_relation": "self",
                                    "participants": ["当前用户"],
                                    "confidence": "high",
                                    "sensitivity": "normal",
                                    "classification_reason": "正文明确描述工作产出",
                                },
                            }
                        ],
                    }
                ],
            ),
            "extract-result.json",
        )

    def test_end_to_end_short_refs_and_deterministic_hydration(self):
        committed = self.complete_extract()
        state = committed["next"]
        self.assertEqual(state["stage"], "synthesize")
        packet = self.load_packet(state)
        serialized = json.dumps(packet, ensure_ascii=False)
        self.assertIn('"evidence_ref": "w0"', serialized)
        self.assertNotIn("cluster_id", serialized)
        self.assertNotIn("source_ref", serialized)
        self.assertNotIn("priority_basis", serialized)

        committed = self.commit(
            self.stage_result(
                packet,
                ledger_fingerprint=packet["payload"]["ledger_fingerprint"],
                summary=[
                    {
                        "result": "完成工作报告 Skill 结构优化",
                        "impact": "减少重复上下文",
                        "evidence_refs": ["w0"],
                    }
                ],
                workstreams=[],
                risks=[],
                next_actions=[],
                uncertain=[],
            ),
            "synthesis-result.json",
        )
        self.assertEqual(committed["next"]["stage"], "complete")
        model = json.loads(
            (self.run_dir / "report-model.json").read_text(encoding="utf-8")
        )
        self.assertEqual(model["schema_version"], 5)
        self.assertNotIn("title", model)
        self.assertNotIn("profile", model)
        self.assertNotIn("coverage", model)
        report = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("# 张三个人周报｜2026-07-20 至 2026-07-26", report)
        self.assertIn(
            "[工作来源](https://example.com/docs/long-document-reference)",
            report,
        )
        self.assertIn("计数：work=1，uncertain=0", report)

    def test_stale_packet_is_rejected(self):
        state = self.next()
        packet = self.load_packet(state)
        (self.run_dir / "fetch-queue.json").write_text(
            (self.run_dir / "fetch-queue.json").read_text(encoding="utf-8") + " ",
            encoding="utf-8",
        )
        result_file = self.run_dir / "stale-result.json"
        write_json(
            result_file,
            self.stage_result(packet, batches=[]),
        )
        result = run(
            STAGE_IO,
            "commit",
            "--run-dir",
            str(self.run_dir),
            "--result-file",
            str(result_file),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("changed after packaging", result.stderr)

    def test_extraction_rejects_repeated_deterministic_fields(self):
        committed = self.complete_fetch()
        state = committed["next"]
        packet = self.load_packet(state)
        result_file = self.run_dir / "bad-extract-result.json"
        write_json(
            result_file,
            self.stage_result(
                packet,
                batches=[
                    {
                        "batch_ref": "b0",
                        "results": [
                            {
                                "item_ref": "i0",
                                "outcome": "work",
                                "record": {
                                    "record_id": "invented",
                                },
                            }
                        ],
                    }
                ],
            ),
        )
        result = run(
            STAGE_IO,
            "commit",
            "--run-dir",
            str(self.run_dir),
            "--result-file",
            str(result_file),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("repeats deterministic fields", result.stderr)

    def test_synthesis_rejects_unknown_short_reference(self):
        committed = self.complete_extract()
        state = committed["next"]
        packet = self.load_packet(state)
        result_file = self.run_dir / "bad-synthesis-result.json"
        write_json(
            result_file,
            self.stage_result(
                packet,
                ledger_fingerprint=packet["payload"]["ledger_fingerprint"],
                summary=[
                    {
                        "result": "错误引用",
                        "evidence_refs": ["w9"],
                    }
                ],
                workstreams=[],
                risks=[],
                next_actions=[],
                uncertain=[],
            ),
        )
        result = run(
            STAGE_IO,
            "commit",
            "--run-dir",
            str(self.run_dir),
            "--result-file",
            str(result_file),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown evidence", result.stderr)
        self.assertFalse((self.run_dir / "report.md").exists())


if __name__ == "__main__":
    unittest.main()
