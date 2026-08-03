import hashlib
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

    def commit_private(self, payload, name, state=None):
        state = state or self.current_state
        result_file = self.run_dir / name
        write_json(result_file, payload)
        result = run(
            STAGE_IO,
            "commit",
            "--run-dir",
            str(self.run_dir),
            "--result-file",
            str(result_file),
            "--package-id",
            state["package_id"],
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def stage_result(self, packet, **fields):
        return {
            "schema_version": 1,
            "package_id": self.current_state["package_id"],
            "input_digest": self.current_state["input_digest"],
            **fields,
        }

    def load_packet(self, state):
        self.current_state = state
        return json.loads(Path(state["packet_file"]).read_text(encoding="utf-8"))

    def add_candidate_metadata(self, **fields):
        queue_path = self.run_dir / "fetch-queue.json"
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        queue["candidate_index"][self.global_id].update(fields)
        write_json(queue_path, queue)

        request_path = self.run_dir / "fetch-requests" / "fetch-0001.json"
        request = json.loads(request_path.read_text(encoding="utf-8"))
        request["items"][0].update(fields)
        write_json(request_path, request)

    def enable_static_graph(self, *, empty_ledger_synthesis_bypass=True):
        scripts = ROOT / "scripts"
        sys.path.insert(0, str(scripts))
        try:
            import execution_graph

            plan_path = self.run_dir / "run-plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            descriptor = execution_graph.write(
                self.run_dir,
                execution_graph.build(
                    plan,
                    empty_ledger_synthesis_bypass=(
                        empty_ledger_synthesis_bypass
                    ),
                ),
            )
            plan["execution_graph"] = descriptor
            write_json(plan_path, plan)
        finally:
            sys.path.remove(str(scripts))
        return descriptor

    def template_profile(self):
        names = {
            "summary": ("今日看点", "本周看点", "月度看点"),
            "progress": ("今日成果", "本周成果", "月度成果"),
            "risks": ("风险提醒", "风险提醒", "风险提醒"),
            "next": ("明日计划", "下周计划", "下月计划"),
            "uncertain": ("请我确认", "请我确认", "请我确认"),
            "coverage": ("依据与范围", "依据与范围", "依据与范围"),
        }
        return {
            "schema_version": 1,
            "template_id": "default",
            "source_fingerprint": "sha256:"
            + hashlib.sha256(b"sample").hexdigest(),
            "title_pattern": "{subject}{period_label}｜{period_range}",
            "sections": {
                slot: {
                    "labels": dict(
                        zip(("daily", "weekly", "monthly"), labels)
                    ),
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

    def semantic_record(self, **fields):
        return {
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
            "participants": ["当前用户"],
            "confidence": "high",
            "sensitivity": "normal",
            "classification_reason": "正文明确描述工作产出",
            **fields,
        }

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
                results=[
                    {
                        "item_ref": "i0",
                        "outcome": "work",
                        "record": self.semantic_record(
                            requires_response=False,
                            assignee_relation="self",
                        ),
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
                summary=[
                    {
                        "result": "完成工作报告 Skill 结构优化",
                        "impact": "减少重复上下文",
                        "evidence_refs": ["w0"],
                    }
                ],
                workstreams=[{"evidence_refs": ["w0"]}],
                risks=[],
                next_actions=[{"evidence_refs": ["w0"]}],
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
        self.assertIn("[W1][W1]", report)
        self.assertIn(
            "[W1]: https://example.com/docs/long-document-reference",
            report,
        )
        self.assertNotIn("## 来源与覆盖", report)
        self.assertNotIn("覆盖说明", report)

        metrics = json.loads(
            (self.run_dir / "stage-metrics.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [entry["stage"] for entry in metrics["entries"]],
            ["fetch", "extract", "synthesize"],
        )
        for entry in metrics["entries"]:
            self.assertGreater(entry["packet_bytes"], 0)
            self.assertGreater(entry["result_bytes"], 0)
            self.assertGreaterEqual(entry["elapsed_ms"], 0)

    def test_file_output_and_access_gap_bypass_model_body(self):
        state = self.next()
        packet = self.load_packet(state)
        batch = packet["payload"]["batches"][0]
        self.assertEqual(batch["output"]["mode"], "managed_file")
        self.assertTrue(Path(state["contract_file"]).name == "fetch-contract.md")
        self.assertTrue(Path(state["result_schema_file"]).is_file())
        staging = self.run_dir / batch["output"]["result_file"]
        write_json(
            staging,
            {
                "schema_version": 1,
                "batch_ref": "b0",
                "results": [
                    {
                        "item_ref": "i0",
                        "outcome": "access_gap",
                        "reason": "当前身份没有访问权限",
                    }
                ],
            },
        )
        committed = self.commit_private(
            {"batches": [{"batch_ref": "b0"}]},
            "file-fetch-result.json",
            state,
        )
        self.assertEqual(committed["next"]["stage"], "synthesize")
        audit = json.loads(
            (self.run_dir / "extraction-audit.json").read_text(encoding="utf-8")
        )
        self.assertEqual(audit["outcome_counts"]["access_gap"], 1)
        self.assertFalse(staging.exists())

    def test_new_static_graph_bypasses_synthesis_for_empty_access_gap_ledger(self):
        descriptor = self.enable_static_graph()
        state = self.next()
        packet = self.load_packet(state)
        committed = self.commit_private(
            {
                "batches": [
                    {
                        "batch_ref": "b0",
                        "results": [
                            {
                                "item_ref": "i0",
                                "outcome": "access_gap",
                                "reason": "当前身份没有访问权限",
                            }
                        ],
                    }
                ]
            },
            "static-gap-fetch-result.json",
            state,
        )

        self.assertEqual(committed["next"]["stage"], "complete")
        model = json.loads(
            (self.run_dir / "report-model.json").read_text(encoding="utf-8")
        )
        self.assertEqual(model["schema_version"], 5)
        for section in (
            "summary",
            "workstreams",
            "risks",
            "next_actions",
            "uncertain",
        ):
            self.assertEqual(model[section], [])
        report = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("## 本周摘要\n- 无", report)
        self.assertIn("覆盖说明", report)
        self.assertEqual(
            hashlib.sha256(
                (self.run_dir / "report-model.json").read_bytes()
            ).hexdigest(),
            "ae5606802942b2f4317e07e6c0136203bb94dec49ab163b6ecfebf6ff5c5983e",
        )
        self.assertEqual(
            hashlib.sha256(report.encode("utf-8")).hexdigest(),
            "315a64289f476e1f3b19d8f325bbf59074a600a56e44314740fb9a935d658e1f",
        )
        receipt_path = (
            self.run_dir / "stage-receipts" / "synthesize-empty-ledger.json"
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["graph_digest"], descriptor["digest"])
        self.assertEqual(receipt["ledger_fingerprint"], model["ledger_fingerprint"])
        self.assertTrue(receipt["report_model_digest"].startswith("sha256:"))
        self.assertTrue(receipt["report_digest"].startswith("sha256:"))
        metrics = json.loads(
            (self.run_dir / "stage-metrics.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [entry["stage"] for entry in metrics["entries"]],
            ["fetch"],
        )

        receipt_path.unlink()
        (self.run_dir / "report.md").unlink()
        resumed = self.next()
        self.assertEqual(resumed["stage"], "complete")
        self.assertTrue(receipt_path.is_file())
        self.assertTrue((self.run_dir / "report.md").is_file())

        ledger_path = self.run_dir / "ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["work"] = [{"cluster_id": "changed-after-receipt"}]
        write_json(ledger_path, ledger)
        rejected = run(STAGE_IO, "next", "--run-dir", str(self.run_dir))
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("receipt is not eligible", rejected.stderr)

    def test_empty_ledger_bypass_still_applies_template_in_finalizer(self):
        write_json(
            self.run_dir / "template-profile.json",
            self.template_profile(),
        )
        self.enable_static_graph()
        state = self.next()
        self.load_packet(state)
        committed = self.commit_private(
            {
                "batches": [
                    {
                        "batch_ref": "b0",
                        "results": [
                            {
                                "item_ref": "i0",
                                "outcome": "access_gap",
                                "reason": "当前身份没有访问权限",
                            }
                        ],
                    }
                ]
            },
            "templated-gap-fetch-result.json",
            state,
        )

        self.assertEqual(committed["next"]["stage"], "complete")
        report = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("# 张三个人周报｜2026-07-20 至 2026-07-26", report)
        for heading in (
            "本周看点",
            "本周成果",
            "风险提醒",
            "下周计划",
            "请我确认",
        ):
            self.assertIn(f"## {heading}\n- 无", report)
        self.assertIn("覆盖说明", report)
        self.assertEqual(
            hashlib.sha256(report.encode("utf-8")).hexdigest(),
            "8292853cfe9ee7d2f4dda06bca899416355afa8033097650b5d394b58ade2824",
        )

    def test_empty_ledger_bypass_after_private_content_matches_snapshot(self):
        self.enable_static_graph()
        state = self.next()
        self.load_packet(state)
        state = self.commit_private(
            {
                "batches": [
                    {
                        "batch_ref": "b0",
                        "results": [
                            {
                                "item_ref": "i0",
                                "content_type": "text",
                                "content": "今晚聚餐，家里临时有事。",
                                "context": {},
                            }
                        ],
                    }
                ]
            },
            "private-fetch-result.json",
            state,
        )["next"]
        self.load_packet(state)
        committed = self.commit_private(
            {
                "results": [
                    {"item_ref": "i0", "outcome": "discarded_private"}
                ]
            },
            "private-extract-result.json",
            state,
        )

        self.assertEqual(committed["next"]["stage"], "complete")
        report = (self.run_dir / "report.md").read_bytes()
        self.assertEqual(
            hashlib.sha256(report).hexdigest(),
            "142773732d9cfec24ead1b973f87ac1101cda43638433777a062652af70f79ba",
        )
        self.assertNotIn("聚餐".encode("utf-8"), report)

    def test_new_static_graph_keeps_synthesis_for_nonempty_ledger(self):
        self.enable_static_graph()

        committed = self.complete_extract()

        self.assertEqual(committed["next"]["stage"], "synthesize")
        self.assertFalse(
            (
                self.run_dir
                / "stage-receipts"
                / "synthesize-empty-ledger.json"
            ).exists()
        )

    def test_legacy_static_graph_still_synthesizes_an_empty_ledger(self):
        self.enable_static_graph(empty_ledger_synthesis_bypass=False)
        state = self.next()
        self.load_packet(state)

        committed = self.commit_private(
            {
                "batches": [
                    {
                        "batch_ref": "b0",
                        "results": [
                            {
                                "item_ref": "i0",
                                "outcome": "access_gap",
                                "reason": "当前身份没有访问权限",
                            }
                        ],
                    }
                ]
            },
            "legacy-static-gap-fetch-result.json",
            state,
        )

        self.assertEqual(committed["next"]["stage"], "synthesize")
        self.assertEqual(committed["next"]["graph_mode"], "static")

    def test_static_execution_graph_drives_stage_state(self):
        self.enable_static_graph()
        state = self.next()
        self.assertEqual(state["graph_mode"], "static")
        self.assertEqual(state["node_id"], "fetch")
        self.assertNotIn("graph_digest", state)

    def test_model_packet_and_private_result_omit_machine_envelope(self):
        state = self.next()
        packet = self.load_packet(state)
        self.assertNotIn("package_id", packet)
        self.assertNotIn("input_digest", packet)
        result_schema = json.loads(
            Path(state["result_schema_file"]).read_text(encoding="utf-8")
        )
        self.assertNotIn("usage", result_schema["properties"])
        batch = packet["payload"]["batches"][0]
        committed = self.commit_private(
            {
                "batches": [
                    {
                        "batch_ref": "b0",
                        "results": [
                            {
                                "item_ref": "i0",
                                "content_type": "text",
                                "content": "完整正文",
                                "context": {},
                            }
                        ],
                    }
                ]
            },
            "private-fetch-result.json",
            state,
        )
        self.assertEqual(committed["next"]["stage"], "extract")

    def test_host_usage_file_records_real_model_outside_model_schema(self):
        state = self.next()
        packet = self.load_packet(state)
        result_file = self.run_dir / "host-metered-fetch.json"
        write_json(
            result_file,
            {
                "batches": [
                    {
                        "batch_ref": "b0",
                        "results": [
                            {
                                "item_ref": "i0",
                                "content_type": "text",
                                "content": "完整正文",
                                "context": {},
                            }
                        ],
                    }
                ]
            },
        )
        usage_file = self.run_dir / "host-usage.json"
        write_json(
            usage_file,
            {
                "schema_version": 1,
                "provider": "test-provider",
                "model": "same-model",
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
            },
        )
        result = run(
            STAGE_IO,
            "commit",
            "--run-dir",
            str(self.run_dir),
            "--result-file",
            str(result_file),
            "--package-id",
            state["package_id"],
            "--usage-file",
            str(usage_file),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        metrics = json.loads(
            (self.run_dir / "stage-metrics.json").read_text(encoding="utf-8")
        )
        entry = metrics["entries"][0]
        self.assertEqual(entry["usage_source"], "host_file")
        self.assertEqual(entry["usage"]["model"], "same-model")
        self.assertNotIn("usage", json.loads(result_file.read_text(encoding="utf-8")))

    def test_explicit_request_context_makes_semantic_stage_self_contained(self):
        plan_path = self.run_dir / "run-plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["request_context"] = {
            "file": "request-context.json",
            "explicit": True,
            "isolated_semantic_stages": True,
        }
        write_json(plan_path, plan)
        request = {
            "schema_version": 1,
            "request": "只整理 A 项目，重点说明结果。",
            "scope": ["A 项目"],
            "exclusions": [],
            "emphasis": ["结果"],
        }
        write_json(self.run_dir / "request-context.json", request)
        committed = self.complete_fetch()
        state = committed["next"]
        self.assertEqual(state["context_mode"], "fresh")
        packet = self.load_packet(state)
        self.assertEqual(packet["payload"]["context"]["request"], request)
        self.assertEqual(packet["payload"]["context"]["subject"], "张三")
        self.assertEqual(
            packet["payload"]["context"]["period"]["routed_profile"],
            "weekly",
        )

    def test_extraction_repackages_batches_and_exactly_deduplicates_content(self):
        duplicate_content = "x" * 2_200_000
        queue_path = self.run_dir / "fetch-queue.json"
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        second_id = "adapter-a:docs:second-document"
        second = {
            **queue["candidate_index"][self.global_id],
            "id": "second-document",
            "source_ref": "https://example.com/docs/second-document",
            "title": "第二份工作报告优化",
        }
        queue["candidate_index"][second_id] = second
        queue["fetch_batches"].append(
            {
                "batch_id": "fetch-0002",
                "adapter_id": "adapter-a",
                "operation": "candidate.fetch",
                "parallelism": 1,
                "file_output": False,
                "body_file": "fetch-results/fetch-0002.json",
                "semantic_file": "semantic-bodies/fetch-0002.json",
                "evidence_file": "evidence-parts/fetch-0002.json",
                "request_file": "fetch-requests/fetch-0002.json",
                "global_ids": [second_id],
            }
        )
        queue["fetch_waves"].append(
            {"wave": 2, "parallel": False, "batch_ids": ["fetch-0002"]}
        )
        queue["included_counts"]["work"] = 2
        write_json(queue_path, queue)
        write_json(
            self.run_dir / "fetch-requests" / "fetch-0002.json",
            {
                "schema_version": 1,
                "batch_id": "fetch-0002",
                "adapter_id": "adapter-a",
                "operation": "candidate.fetch",
                "items": [{"global_id": second_id, **second}],
            },
        )

        state = self.next()
        for index in range(2):
            packet = self.load_packet(state)
            batch = packet["payload"]["batches"][0]
            state = self.commit_private(
                {
                    "batches": [
                        {
                            "batch_ref": batch["batch_ref"],
                            "results": [
                                {
                                    "item_ref": "i0",
                                    "content_type": "text",
                                    "content": duplicate_content,
                                    "context": {"section": "结果"},
                                }
                            ],
                        }
                    ]
                },
                f"dedup-fetch-{index}.json",
                state,
            )["next"]
        self.assertEqual(state["stage"], "extract")
        packet = self.load_packet(state)
        payload = packet["payload"]
        semantic_bytes = sum(
            (self.run_dir / "semantic-bodies" / f"fetch-000{index}.json").stat().st_size
            for index in (1, 2)
        )
        self.assertGreater(semantic_bytes, 4 * 1024 * 1024)
        self.assertLessEqual(Path(state["packet_file"]).stat().st_size, 4 * 1024 * 1024)
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(len(payload["content_blobs"]), 1)
        self.assertEqual(len(payload["context_blobs"]), 1)
        self.assertEqual(
            [item["metadata"]["title"] for item in payload["items"]],
            ["工作报告优化", "第二份工作报告优化"],
        )
        self.assertNotIn("batches", payload)

    def test_first_indivisible_extraction_batch_is_emitted_when_oversized(self):
        state = self.next()
        packet = self.load_packet(state)
        committed = self.commit_private(
            {
                "batches": [
                    {
                        "batch_ref": "b0",
                        "results": [
                            {
                                "item_ref": "i0",
                                "content_type": "text",
                                "content": "x" * (4 * 1024 * 1024),
                                "context": {},
                            }
                        ],
                    }
                ]
            },
            "oversized-fetch-result.json",
            state,
        )
        state = committed["next"]
        self.assertEqual(state["stage"], "extract")
        self.assertGreater(Path(state["packet_file"]).stat().st_size, 4 * 1024 * 1024)
        payload = self.load_packet(state)["payload"]
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(len(payload["content_blobs"]), 1)

    def test_extraction_batching_keeps_the_item_limit_between_batches(self):
        queue_path = self.run_dir / "fetch-queue.json"
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        base = queue["candidate_index"][self.global_id]
        second_ids = []
        second_candidates = []
        for index in range(100):
            global_id = f"adapter-a:docs:item-{index:03d}"
            candidate = {
                **base,
                "id": f"item-{index:03d}",
                "source_ref": f"https://example.com/docs/item-{index:03d}",
                "title": f"工作项 {index:03d}",
            }
            queue["candidate_index"][global_id] = candidate
            second_ids.append(global_id)
            second_candidates.append({"global_id": global_id, **candidate})
        queue["fetch_batches"].append(
            {
                "batch_id": "fetch-0002",
                "adapter_id": "adapter-a",
                "operation": "candidate.fetch",
                "parallelism": 1,
                "file_output": False,
                "body_file": "fetch-results/fetch-0002.json",
                "semantic_file": "semantic-bodies/fetch-0002.json",
                "evidence_file": "evidence-parts/fetch-0002.json",
                "request_file": "fetch-requests/fetch-0002.json",
                "global_ids": second_ids,
            }
        )
        queue["fetch_waves"].append(
            {"wave": 2, "parallel": False, "batch_ids": ["fetch-0002"]}
        )
        queue["included_counts"]["work"] = 101
        write_json(queue_path, queue)
        write_json(
            self.run_dir / "fetch-requests" / "fetch-0002.json",
            {
                "schema_version": 1,
                "batch_id": "fetch-0002",
                "adapter_id": "adapter-a",
                "operation": "candidate.fetch",
                "items": second_candidates,
            },
        )

        state = self.next()
        packet = self.load_packet(state)
        state = self.commit_private(
            {
                "batches": [
                    {
                        "batch_ref": "b0",
                        "results": [
                            {
                                "item_ref": "i0",
                                "content_type": "text",
                                "content": "第一批正文",
                                "context": {},
                            }
                        ],
                    }
                ]
            },
            "limit-fetch-1.json",
            state,
        )["next"]
        packet = self.load_packet(state)
        self.assertEqual(len(packet["payload"]["batches"][0]["items"]), 100)
        state = self.commit_private(
            {
                "batches": [
                    {
                        "batch_ref": "b0",
                        "results": [
                            {
                                "item_ref": item["item_ref"],
                                "content_type": "text",
                                "content": "共享正文",
                                "context": {},
                            }
                            for item in packet["payload"]["batches"][0]["items"]
                        ],
                    }
                ]
            },
            "limit-fetch-2.json",
            state,
        )["next"]
        self.assertEqual(state["stage"], "extract")
        payload = self.load_packet(state)["payload"]
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["metadata"]["title"], "工作报告优化")

    def test_synthesis_repair_replaces_only_invalid_item(self):
        committed = self.complete_extract()
        state = committed["next"]
        packet = self.load_packet(state)
        result_file = self.run_dir / "repairable-synthesis-result.json"
        write_json(
            result_file,
            {
                "summary": [
                    {
                        "result": "错误引用",
                        "evidence_refs": ["w9"],
                    }
                ],
                "workstreams": [{"evidence_refs": ["w0"]}],
                "risks": [],
                "next_actions": [{"evidence_refs": ["w0"]}],
                "uncertain": [],
            },
        )
        failed = run(
            STAGE_IO,
            "commit",
            "--run-dir",
            str(self.run_dir),
            "--result-file",
            str(result_file),
            "--package-id",
            state["package_id"],
        )
        self.assertEqual(failed.returncode, 2)
        error = json.loads(failed.stderr)["error"]
        marker = "repair_packet="
        repair_file = Path(
            error.split(marker, 1)[1].split(";", 1)[0]
        )
        repair = json.loads(repair_file.read_text(encoding="utf-8"))
        self.assertEqual(repair["targets"][0]["path"], "/summary/0")
        committed = self.commit_private(
            {
                "patches": [
                    {
                        "path": "/summary/0",
                        "value": {"evidence_refs": ["w0"]},
                    }
                ]
            },
            "synthesis-repair.json",
            state,
        )
        self.assertEqual(committed["next"]["stage"], "complete")
        self.assertIn(
            "完成实现并通过测试",
            (self.run_dir / "report.md").read_text(encoding="utf-8"),
        )

    def test_extraction_hydrates_candidate_facts_and_accepts_matching_legacy_value(self):
        self.add_candidate_metadata(
            starts_at="2026-07-25T09:00:00+08:00",
            ends_at="2026-07-25T10:00:00+08:00",
            due_at="2026-07-28T18:00:00+08:00",
            requires_response=True,
            action_kind="review",
            assignee_relation="self",
        )
        committed = self.complete_fetch()
        packet = self.load_packet(committed["next"])
        committed = self.commit(
            self.stage_result(
                packet,
                results=[
                    {
                        "item_ref": "i0",
                        "outcome": "work",
                        "record": self.semantic_record(
                            starts_at="2026-07-25T09:00:00+08:00",
                        ),
                    }
                ],
            ),
            "metadata-extract-result.json",
        )
        self.assertEqual(committed["next"]["stage"], "synthesize")
        records = json.loads(
            (self.run_dir / "evidence-records.json").read_text(encoding="utf-8")
        )["records"]
        self.assertEqual(records[0]["starts_at"], "2026-07-25T09:00:00+08:00")
        self.assertEqual(records[0]["ends_at"], "2026-07-25T10:00:00+08:00")
        self.assertEqual(records[0]["due_at"], "2026-07-28T18:00:00+08:00")
        self.assertTrue(records[0]["requires_response"])
        self.assertEqual(records[0]["action_kind"], "review")
        self.assertEqual(records[0]["assignee_relation"], "self")

    def test_extraction_rejects_candidate_fact_conflict(self):
        self.add_candidate_metadata(
            due_at="2026-07-28T18:00:00+08:00",
        )
        committed = self.complete_fetch()
        packet = self.load_packet(committed["next"])
        result_file = self.run_dir / "conflicting-metadata-extract-result.json"
        write_json(
            result_file,
            self.stage_result(
                packet,
                results=[
                    {
                        "item_ref": "i0",
                        "outcome": "work",
                        "record": self.semantic_record(
                            due_at="2026-07-29T18:00:00+08:00",
                        ),
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
        self.assertIn("conflicts with candidate metadata: due_at", result.stderr)

    def test_synthesis_can_direct_fill_a_simple_single_reference(self):
        committed = self.complete_extract()
        packet = self.load_packet(committed["next"])
        self.assertIn("summary", packet["payload"]["work"][0]["direct_sections"])
        committed = self.commit(
            self.stage_result(
                packet,
                summary=[{"evidence_refs": ["w0"]}],
                workstreams=[{"evidence_refs": ["w0"]}],
                risks=[],
                next_actions=[{"evidence_refs": ["w0"]}],
                uncertain=[],
            ),
            "direct-synthesis-result.json",
        )
        self.assertEqual(committed["next"]["stage"], "complete")
        report = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("完成实现并通过测试", report)

    def test_model_synthesis_rejects_a_residual_empty_bypass_receipt(self):
        committed = self.complete_extract()
        state = committed["next"]
        packet = self.load_packet(state)
        write_json(
            self.run_dir
            / "stage-receipts"
            / "synthesize-empty-ledger.json",
            {"schema_version": 1},
        )
        result_file = self.run_dir / "synthesis-with-bypass-receipt.json"
        write_json(
            result_file,
            self.stage_result(
                packet,
                summary=[{"evidence_refs": ["w0"]}],
                workstreams=[{"evidence_refs": ["w0"]}],
                risks=[],
                next_actions=[{"evidence_refs": ["w0"]}],
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
        self.assertIn("cannot coexist", result.stderr)
        self.assertFalse((self.run_dir / "report-model.json").exists())

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
                results=[
                    {
                        "item_ref": "i0",
                        "outcome": "work",
                        "record": {
                            "record_id": "invented",
                        },
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
