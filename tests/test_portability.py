import importlib.util
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CORE_FILES = [ROOT / "SKILL.md", *sorted((ROOT / "references").glob("*.md"))]


def load_contracts():
    path = ROOT / "scripts" / "contracts.py"
    spec = importlib.util.spec_from_file_location("lark_work_report_contracts", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PortabilityTests(unittest.TestCase):
    def test_core_has_no_host_specific_install_dependency(self):
        forbidden = (
            "/" + "Users/",
            "." + "codex/",
            "." + "claude/",
            "CODEX" + "_HOME",
            "CLAUDE" + "_SKILL_DIR",
            "$lark-work-report",
            "/lark-work-report",
        )
        for path in CORE_FILES:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{path.name} depends on {token}")

    def test_frontmatter_uses_portable_required_fields_only(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
        self.assertIsNotNone(match)
        keys = {
            line.split(":", 1)[0].strip()
            for line in match.group(1).splitlines()
            if ":" in line and not line.startswith((" ", "\t"))
        }
        self.assertEqual({"name", "description"}, keys)

    def test_adapter_contract_defines_required_operations(self):
        text = (ROOT / "references" / "capability-adapters.md").read_text(
            encoding="utf-8"
        )
        for operation in (
            "identity.current",
            "candidate.list",
            "candidate.fetch",
            "report.create",
            "report.fetch",
        ):
            self.assertIn(operation, text)

    def test_adapter_schema_source_types_match_runtime_contract(self):
        schema = json.loads(
            (ROOT / "references" / "adapter-contract.schema.json").read_text(
                encoding="utf-8"
            )
        )
        declared = schema["properties"]["capabilities"]["properties"][
            "candidate.list"
        ]["properties"]["domains"]["items"]["enum"]
        self.assertEqual(set(declared), set(load_contracts().SOURCE_TYPES))

    def test_readme_uses_one_generic_install_flow(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        install = text.split("## 安装", 1)[1].split("## 数据适配", 1)[0]
        self.assertIn(
            "git clone https://github.com/Rocky-tc/lark-work-report.git", install
        )
        self.assertNotIn("\n### ", install)
        self.assertNotRegex(install, r"~/\.[^/]+/skills")

    def test_core_relative_markdown_links_resolve(self):
        pattern = re.compile(r"\[[^\]]+\]\(([^)]+\.md)\)")
        for path in CORE_FILES:
            text = path.read_text(encoding="utf-8")
            for target in pattern.findall(text):
                resolved = (path.parent / target).resolve()
                self.assertTrue(
                    resolved.is_file(),
                    f"{path.name} links to missing file {target}",
                )

    def test_skill_links_conditional_references_directly(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for name in (
            "capability-adapters.md",
            "classification-contract.md",
            "collection-policy.md",
            "extraction-contract.md",
            "relevance-and-retention.md",
            "evidence-model.md",
            "report-profiles.md",
            "report-rendering.md",
            "stage-io.md",
            "synthesis-contract.md",
            "output-contract.md",
            "template-profiles.md",
        ):
            self.assertIn(f"references/{name}", skill)
        self.assertFalse((ROOT / "references" / "runtime-contract.md").exists())

    def test_semantic_contracts_are_loaded_at_their_own_stages(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        classification = skill.index("references/classification-contract.md")
        extraction = skill.index("references/extraction-contract.md")
        synthesis = skill.index("references/synthesis-contract.md")
        self.assertLess(classification, extraction)
        self.assertLess(extraction, synthesis)
        self.assertIn("不能提前合并加载", skill)


if __name__ == "__main__":
    unittest.main()
