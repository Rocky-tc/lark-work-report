import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "source_refs.py"
SPEC = importlib.util.spec_from_file_location("source_refs", MODULE_PATH)
SOURCE_REFS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOURCE_REFS)


class SourceReferenceTests(unittest.TestCase):
    def test_supported_references(self):
        for value in (
            "https://example.com/doc/1?tab=a#section",
            "http://example.com",
            "source://host.lark/docs/doc-1",
            "source://docs/doc-x@2026-07-26T10:00:00+08:00",
        ):
            with self.subTest(value=value):
                self.assertTrue(SOURCE_REFS.is_valid_source_ref(value))

    def test_malformed_references(self):
        for value in (
            "https://",
            "https:///missing-host",
            "https://example.com/a)b",
            "source://",
            "source://host",
            "source:///docs/doc-1",
            "doc-1",
        ):
            with self.subTest(value=value):
                self.assertFalse(SOURCE_REFS.is_valid_source_ref(value))


if __name__ == "__main__":
    unittest.main()
