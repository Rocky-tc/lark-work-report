import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "normalize-fetch-body.py"


def run_normalizer(request, body):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    request_file = root / "request.json"
    body_file = root / "body.json"
    output = root / "semantic.json"
    request_file.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    body_file.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--request-file",
            str(request_file),
            "--body-file",
            str(body_file),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
    return tmp, result, payload


def request(*ids):
    return {
        "schema_version": 1,
        "batch_id": "fetch-0001",
        "adapter_id": "test.adapter",
        "operation": "candidate.fetch_many",
        "items": [{"global_id": value} for value in ids],
    }


def body(*results):
    return {
        "schema_version": 1,
        "batch_id": "fetch-0001",
        "results": list(results),
    }


class NormalizeFetchBodyTests(unittest.TestCase):
    def test_text_and_context_are_preserved_exactly_and_identical_content_deduped(self):
        content = "第一行\r\n第二行\n\n完整正文"
        tmp, result, payload = run_normalizer(
            request("one", "two"),
            body(
                {
                    "global_id": "one",
                    "content_type": "text",
                    "content": content,
                    "context": {"page": 1, "nested": {"cursor": "x"}},
                },
                {
                    "global_id": "two",
                    "content_type": "text",
                    "content": content,
                    "context": {"page": 2},
                },
            ),
        )
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(payload["content_blobs"]), 1)
            fingerprint = payload["items"][0]["content_fingerprint"]
            self.assertEqual(payload["items"][1]["content_fingerprint"], fingerprint)
            self.assertEqual(payload["content_blobs"][fingerprint]["content"], content)
            self.assertEqual(
                payload["items"][0]["context"],
                {"page": 1, "nested": {"cursor": "x"}},
            )
        finally:
            tmp.cleanup()

    def test_json_key_order_dedupes_without_changing_values(self):
        tmp, result, payload = run_normalizer(
            request("one", "two"),
            body(
                {
                    "global_id": "one",
                    "content_type": "json",
                    "content": {"a": 1, "b": [2, 3]},
                    "context": {},
                },
                {
                    "global_id": "two",
                    "content_type": "json",
                    "content": {"b": [2, 3], "a": 1},
                    "context": {},
                },
            ),
        )
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(payload["content_blobs"]), 1)
            stored = next(iter(payload["content_blobs"].values()))["content"]
            self.assertEqual(stored, {"a": 1, "b": [2, 3]})
        finally:
            tmp.cleanup()

    def test_nonidentical_content_is_not_deduped(self):
        tmp, result, payload = run_normalizer(
            request("one", "two"),
            body(
                {
                    "global_id": "one",
                    "content_type": "text",
                    "content": "内容 A",
                    "context": {},
                },
                {
                    "global_id": "two",
                    "content_type": "text",
                    "content": "内容 A ",
                    "context": {},
                },
            ),
        )
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(payload["content_blobs"]), 2)
        finally:
            tmp.cleanup()

    def test_missing_or_extra_identity_is_rejected(self):
        tmp, result, payload = run_normalizer(
            request("one", "two"),
            body(
                {
                    "global_id": "one",
                    "content_type": "text",
                    "content": "内容",
                    "context": {},
                },
                {
                    "global_id": "extra",
                    "content_type": "text",
                    "content": "其他",
                    "context": {},
                },
            ),
        )
        try:
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(payload)
            self.assertIn("missing=1, extra=1", result.stderr)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
