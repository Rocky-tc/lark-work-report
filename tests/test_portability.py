from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CORE_FILES = [ROOT / "SKILL.md", *sorted((ROOT / "references").glob("*.md"))]


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
            if ":" in line
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


if __name__ == "__main__":
    unittest.main()
