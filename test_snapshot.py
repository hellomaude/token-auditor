"""Tests for snapshot.py — daily history tracking."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import snapshot


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.report_path = Path(self.tmpdir) / "report.json"
        self.history_path = Path(self.tmpdir) / "history.jsonl"

        self.report = {
            "summary": {
                "total_tokens": 284000,
                "total_turns": 50,
                "total_tool_uses": 30,
                "avg_tokens_per_session": 23000,
            },
            "token_waste": {
                "idle_gap_count": 2,
                "redundant_file_reads": 15,
                "idle_gap_pct_of_turns": 4,
            },
            "by_model": {
                "claude-opus-4-6": {"tokens": 200000},
                "claude-sonnet-4-6": {"tokens": 84000},
            },
            "total_sessions": 12,
        }
        self.report_path.write_text(json.dumps(self.report))

    def test_appends_today_to_history(self):
        snapshot.run(report_path=self.report_path, history_path=self.history_path)
        lines = self.history_path.read_text().strip().split("\n")
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["date"], datetime.now().strftime("%Y-%m-%d"))
        self.assertEqual(entry["tokens"], 284000)
        self.assertEqual(entry["sessions"], 12)
        self.assertEqual(entry["idle_gaps"], 2)
        self.assertEqual(entry["redundant_reads"], 15)
        self.assertIn("score", entry)
        self.assertIn("cost", entry)

    def test_score_is_in_range(self):
        snapshot.run(report_path=self.report_path, history_path=self.history_path)
        entry = json.loads(self.history_path.read_text().strip())
        self.assertGreaterEqual(entry["score"], 0)
        self.assertLessEqual(entry["score"], 100)

    def test_cost_calculation_uses_model_pricing(self):
        snapshot.run(report_path=self.report_path, history_path=self.history_path)
        entry = json.loads(self.history_path.read_text().strip())
        self.assertGreater(entry["cost"], 0)
        self.assertLess(entry["cost"], 100)

    def test_replaces_existing_entry_for_same_date(self):
        snapshot.run(report_path=self.report_path, history_path=self.history_path)
        self.report["summary"]["total_tokens"] = 300000
        self.report_path.write_text(json.dumps(self.report))
        snapshot.run(report_path=self.report_path, history_path=self.history_path)

        lines = self.history_path.read_text().strip().split("\n")
        self.assertEqual(len(lines), 1, "Should replace, not append, for same date")
        entry = json.loads(lines[0])
        self.assertEqual(entry["tokens"], 300000)


if __name__ == "__main__":
    unittest.main()
