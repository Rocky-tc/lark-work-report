import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare-fetch-queue.py"


def run_filter(candidates):
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "candidates.json"
        source.write_text(
            json.dumps({"candidates": candidates}, ensure_ascii=False),
            encoding="utf-8",
        )
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--file", str(source)],
            capture_output=True,
            text=True,
            check=False,
        )


class PrepareFetchQueueTests(unittest.TestCase):
    def test_fetch_queue_contains_only_work_and_uncertain(self):
        result = run_filter(
            [
                {
                    "id": "A",
                    "title": "家庭体检安排",
                    "source_type": "calendar",
                    "source_ref": "cal_A",
                    "prefetch_relevance": "private",
                    "classification_reason": "明确标记为私人日历",
                },
                {
                    "id": "B",
                    "title": "午饭约哪",
                    "source_type": "message",
                    "source_ref": "msg_B",
                    "prefetch_relevance": "chatter",
                    "classification_reason": "无工作行动或结果",
                },
                {
                    "id": "C",
                    "title": "项目 A 复盘",
                    "source_type": "document",
                    "source_ref": "doc_C",
                    "prefetch_relevance": "work",
                    "classification_reason": "关联明确工作流",
                },
                {
                    "id": "D",
                    "title": "新方案探索",
                    "source_type": "document",
                    "source_ref": "doc_D",
                    "prefetch_relevance": "uncertain",
                    "classification_reason": "工作归属尚不明确",
                },
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual([item["id"] for item in payload["fetch_queue"]], ["C", "D"])
        self.assertEqual(payload["included_counts"], {"work": 1, "uncertain": 1})
        self.assertEqual(payload["skipped_counts"], {"private": 1, "chatter": 1})

        serialized_queue = json.dumps(payload["fetch_queue"], ensure_ascii=False)
        self.assertNotIn("家庭体检安排", serialized_queue)
        self.assertNotIn("午饭约哪", serialized_queue)
        self.assertNotIn("cal_A", serialized_queue)
        self.assertNotIn("msg_B", serialized_queue)

    def test_unknown_relevance_is_rejected(self):
        result = run_filter(
            [
                {
                    "id": "X",
                    "source_type": "document",
                    "source_ref": "doc_X",
                    "prefetch_relevance": "maybe",
                    "classification_reason": "未知",
                }
            ]
        )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertIn("invalid prefetch_relevance", payload["error"])

    def test_missing_classification_reason_is_rejected(self):
        result = run_filter(
            [
                {
                    "id": "X",
                    "source_type": "document",
                    "source_ref": "doc_X",
                    "prefetch_relevance": "uncertain",
                }
            ]
        )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertIn("classification_reason", payload["error"])


if __name__ == "__main__":
    unittest.main()
