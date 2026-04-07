"""Tests for auto-fix scripts."""
import tempfile
import unittest
from pathlib import Path

from fixes import add_to_claude_md


class TestAddToClaudeMd(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.project = self.tmpdir / "project"
        self.project.mkdir()

    def test_creates_claude_md_if_missing(self):
        target = self.project / "src" / "index.ts"
        target.parent.mkdir(parents=True)
        target.write_text("export function foo() {}\nexport class Bar {}\n")

        add_to_claude_md.run(file_path=target, project_root=self.project)

        claude_md = self.project / "CLAUDE.md"
        self.assertTrue(claude_md.exists())
        content = claude_md.read_text()
        self.assertIn("Quick Reference: src/index.ts", content)
        self.assertIn("foo", content)
        self.assertIn("Bar", content)

    def test_appends_to_existing_claude_md(self):
        target = self.project / "src" / "app.py"
        target.parent.mkdir(parents=True)
        target.write_text("def hello():\n    pass\nclass World:\n    pass\n")

        claude_md = self.project / "CLAUDE.md"
        claude_md.write_text("# Existing Content\n\nDo not delete.\n")

        add_to_claude_md.run(file_path=target, project_root=self.project)

        content = claude_md.read_text()
        self.assertIn("# Existing Content", content)
        self.assertIn("Do not delete", content)
        self.assertIn("Quick Reference: src/app.py", content)
        self.assertIn("hello", content)
        self.assertIn("World", content)

    def test_does_not_duplicate_if_already_present(self):
        target = self.project / "src" / "lib.js"
        target.parent.mkdir(parents=True)
        target.write_text("function bar() {}\n")

        add_to_claude_md.run(file_path=target, project_root=self.project)
        first = (self.project / "CLAUDE.md").read_text()

        add_to_claude_md.run(file_path=target, project_root=self.project)
        second = (self.project / "CLAUDE.md").read_text()

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
