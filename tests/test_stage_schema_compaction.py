import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import stage_io  # noqa: E402


def compact_bytes(value):
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def json_equal(left, right):
    if type(left) is not type(right):
        return False
    return left == right


def resolve_ref(root, value):
    assert value.startswith("#/")
    current = root
    for token in value[2:].split("/"):
        current = current[token.replace("~1", "/").replace("~0", "~")]
    return current


def schema_accepts(schema, value, root=None):
    """Evaluate the JSON Schema subset emitted by stage_io."""

    root = root or schema
    if "$ref" in schema and not schema_accepts(
        resolve_ref(root, schema["$ref"]), value, root
    ):
        return False
    if "allOf" in schema and not all(
        schema_accepts(branch, value, root) for branch in schema["allOf"]
    ):
        return False
    if "oneOf" in schema and sum(
        schema_accepts(branch, value, root) for branch in schema["oneOf"]
    ) != 1:
        return False
    if "const" in schema and not json_equal(value, schema["const"]):
        return False
    if "enum" in schema and not any(
        json_equal(value, option) for option in schema["enum"]
    ):
        return False

    expected_type = schema.get("type")
    if expected_type == "object" and not isinstance(value, dict):
        return False
    if isinstance(value, dict) and any(
        keyword in schema
        for keyword in ("properties", "required", "additionalProperties")
    ):
        properties = schema.get("properties", {})
        if any(field not in value for field in schema.get("required", [])):
            return False
        if schema.get("additionalProperties") is False and (
            set(value) - set(properties)
        ):
            return False
        if any(
            not schema_accepts(field_schema, value[field], root)
            for field, field_schema in properties.items()
            if field in value
        ):
            return False
    elif expected_type == "array":
        if not isinstance(value, list):
            return False
        if len(value) < schema.get("minItems", 0):
            return False
        if len(value) > schema.get("maxItems", len(value)):
            return False
        prefix = schema.get("prefixItems", [])
        if any(
            not schema_accepts(item_schema, value[index], root)
            for index, item_schema in enumerate(prefix)
            if index < len(value)
        ):
            return False
        remaining = value[len(prefix) :]
        if remaining and schema.get("items") is False:
            return False
        if isinstance(schema.get("items"), dict) and any(
            not schema_accepts(schema["items"], item, root)
            for item in remaining if prefix
        ):
            return False
        if not prefix and isinstance(schema.get("items"), dict) and any(
            not schema_accepts(schema["items"], item, root) for item in value
        ):
            return False
        if schema.get("uniqueItems") and len(
            {json.dumps(item, sort_keys=True) for item in value}
        ) != len(value):
            return False
    elif expected_type == "string":
        if not isinstance(value, str):
            return False
        if len(value) < schema.get("minLength", 0):
            return False
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            return False
    elif expected_type == "boolean" and not isinstance(value, bool):
        return False
    return True


def fetch_payload(item_count, mode="inline"):
    return {
        "batches": [
            {
                "batch_ref": "b0",
                "items": [
                    {"item_ref": f"i{index}"} for index in range(item_count)
                ],
                "output": {"mode": mode},
            }
        ]
    }


def extract_payload(item_count):
    return {
        "items": [
            {"item_ref": f"i{index}"} for index in range(item_count)
        ]
    }


def extraction_record(**fields):
    return {
        "actor": "当前用户",
        "workstream": "工作报告 Skill",
        "activity": "压缩阶段 Schema",
        "status": "completed",
        "status_basis": "explicit",
        "signal_kind": "outcome",
        "confidence": "high",
        "sensitivity": "normal",
        "classification_reason": "正文明确描述工作产出",
        **fields,
    }


class StageSchemaCompactionTests(unittest.TestCase):
    def test_fetch_schema_preserves_result_acceptance(self):
        schema = stage_io.stage_result_schema("fetch", fetch_payload(3))
        valid = {
            "batches": [
                {
                    "batch_ref": "b0",
                    "results": [
                        {
                            "item_ref": "i0",
                            "content_type": "text",
                            "content": "完整正文",
                            "context": {},
                        },
                        {
                            "item_ref": "i1",
                            "outcome": "access_gap",
                            "reason": "当前身份无权访问",
                        },
                        {
                            "item_ref": "i2",
                            "content_type": "json",
                            "content": None,
                            "context": {"section": "结果"},
                        },
                    ],
                }
            ]
        }
        self.assertTrue(schema_accepts(schema, valid))

        invalid_results = [
            valid["batches"][0]["results"][:-1],
            [
                {**valid["batches"][0]["results"][0], "item_ref": "i9"},
                valid["batches"][0]["results"][1],
                valid["batches"][0]["results"][2],
            ],
            [
                {**valid["batches"][0]["results"][0], "extra": True},
                *valid["batches"][0]["results"][1:],
            ],
            [
                {
                    **valid["batches"][0]["results"][0],
                    "content_type": "markdown",
                },
                *valid["batches"][0]["results"][1:],
            ],
            [
                valid["batches"][0]["results"][0],
                {"item_ref": "i1", "outcome": "access_gap", "reason": ""},
                valid["batches"][0]["results"][2],
            ],
        ]
        for results in invalid_results:
            with self.subTest(results=results):
                self.assertFalse(
                    schema_accepts(
                        schema,
                        {"batches": [{"batch_ref": "b0", "results": results}]},
                    )
                )

        reordered = list(reversed(valid["batches"][0]["results"]))
        self.assertTrue(
            schema_accepts(
                schema,
                {"batches": [{"batch_ref": "b0", "results": reordered}]},
            )
        )
        self.assertEqual(
            set(
                stage_io.exact_ref_map(
                    reordered,
                    "item_ref",
                    {"i0", "i1", "i2"},
                    "results",
                )
            ),
            {"i0", "i1", "i2"},
        )
        duplicated = [
            valid["batches"][0]["results"][0],
            {**valid["batches"][0]["results"][1], "item_ref": "i0"},
            valid["batches"][0]["results"][2],
        ]
        with self.assertRaisesRegex(ValueError, "duplicates item_ref"):
            stage_io.exact_ref_map(
                duplicated,
                "item_ref",
                {"i0", "i1", "i2"},
                "results",
            )

    def test_extract_schema_preserves_result_acceptance(self):
        schema = stage_io.stage_result_schema("extract", extract_payload(2))
        valid_results = [
            {
                "item_ref": "i1",
                "outcome": "uncertain",
                "record": extraction_record(),
            },
            {"item_ref": "i0", "outcome": "discarded_private"},
        ]
        self.assertTrue(schema_accepts(schema, {"results": valid_results}))
        self.assertTrue(
            schema_accepts(
                schema,
                {
                    "results": [
                        {"item_ref": "i0", "outcome": "discarded_chatter"},
                        {
                            "item_ref": "i1",
                            "outcome": "access_gap",
                            "reason": "正文抓取不完整",
                        },
                    ]
                },
            )
        )

        invalid_results = [
            valid_results[:-1],
            [{**valid_results[0], "item_ref": "i9"}, valid_results[1]],
            [
                {"item_ref": "i1", "outcome": "work"},
                valid_results[1],
            ],
            [
                valid_results[0],
                {
                    "item_ref": "i0",
                    "outcome": "discarded_private",
                    "record": extraction_record(),
                },
            ],
            [
                {
                    **valid_results[0],
                    "record": extraction_record(unexpected="value"),
                },
                valid_results[1],
            ],
            [
                valid_results[0],
                {"item_ref": "i0", "outcome": "access_gap", "reason": ""},
            ],
        ]
        for results in invalid_results:
            with self.subTest(results=results):
                self.assertFalse(schema_accepts(schema, {"results": results}))

    def test_fetch_schema_compacts_only_when_inline_batch_benefits(self):
        singleton = stage_io.stage_result_schema("fetch", fetch_payload(1))
        self.assertEqual(compact_bytes(singleton), 882)
        self.assertNotIn("$defs", singleton)
        self.assertNotIn("allOf", json.dumps(singleton))

        ten_items = stage_io.stage_result_schema("fetch", fetch_payload(10))
        self.assertLessEqual(compact_bytes(ten_items), 1_400)
        self.assertIn("fetch_batch_0_item", ten_items["$defs"])
        self.assertNotIn("allOf", json.dumps(ten_items))

        hundred_items = stage_io.stage_result_schema("fetch", fetch_payload(100))
        self.assertLessEqual(compact_bytes(hundred_items), 5_900)

        managed = stage_io.stage_result_schema(
            "fetch", fetch_payload(10, mode="managed_file")
        )
        self.assertEqual(compact_bytes(managed), 338)
        self.assertNotIn("$defs", managed)

        mixed = stage_io.stage_result_schema(
            "fetch",
            {
                "batches": [
                    {
                        "batch_ref": "b0",
                        "items": [{"item_ref": "i0"}],
                        "output": {"mode": "inline"},
                    },
                    {
                        "batch_ref": "b1",
                        "items": [
                            {"item_ref": "i1"},
                            {"item_ref": "i2"},
                        ],
                        "output": {"mode": "inline"},
                    },
                    {
                        "batch_ref": "b2",
                        "items": [{"item_ref": "i3"}],
                        "output": {"mode": "managed_file"},
                    },
                ]
            },
        )
        batch_schemas = mixed["properties"]["batches"]["prefixItems"]
        singleton_item = batch_schemas[0]["properties"]["results"][
            "prefixItems"
        ][0]
        compact_item = batch_schemas[1]["properties"]["results"][
            "prefixItems"
        ][0]
        self.assertNotIn("allOf", singleton_item)
        self.assertEqual(
            compact_item,
            {"$ref": "#/$defs/fetch_batch_1_item"},
        )
        self.assertEqual(
            mixed["$defs"]["fetch_batch_1_item"]["oneOf"][0]
            ["properties"]["item_ref"],
            {"enum": ["i1", "i2"]},
        )
        self.assertNotIn("allOf", json.dumps(mixed))
        self.assertNotIn("results", batch_schemas[2]["properties"])

    def test_extract_schema_shares_refs_without_changing_array_result(self):
        ten_items = stage_io.stage_result_schema("extract", extract_payload(10))
        self.assertLessEqual(compact_bytes(ten_items), 1_850)
        self.assertEqual(
            ten_items["$defs"]["item_ref"],
            {"enum": [f"i{index}" for index in range(10)]},
        )
        results = ten_items["properties"]["results"]
        self.assertEqual(results["type"], "array")
        self.assertEqual(results["minItems"], 10)
        self.assertEqual(results["maxItems"], 10)

        hundred_items = stage_io.stage_result_schema(
            "extract", extract_payload(100)
        )
        self.assertLessEqual(compact_bytes(hundred_items), 2_400)


if __name__ == "__main__":
    unittest.main()
