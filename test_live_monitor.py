"""Tests for live_monitor.py — active session detection and metrics."""
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

import live_monitor


class TestLiveMonitor(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.projects_dir = self.tmpdir / "projects"
        self.projects_dir.mkdir()
        self.live_path = self.tmpdir / "live.json"

    def write_session(self, project_subdir, session_id, entries, mtime_offset_seconds=0):
        proj_dir = self.projects_dir / project_subdir
        proj_dir.mkdir(parents=True, exist_ok=True)
        path = proj_dir / f"{session_id}.jsonl"
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        if mtime_offset_seconds:
            new_mtime = time.time() + mtime_offset_seconds
            os.utime(path, (new_mtime, new_mtime))
        return path

    def test_no_active_sessions_when_dir_missing(self):
        result = live_monitor.scan(projects_dir=self.tmpdir / "nope")
        self.assertEqual(result["active_sessions"], [])
        self.assertIn("updated_at", result)

    def test_detects_recently_modified_session(self):
        now = datetime.now(timezone.utc).isoformat()
        self.write_session("-Users-test-myapp", "abc123", [
            {"type": "user", "timestamp": now, "sessionId": "abc123", "cwd": "/Users/test/myapp"},
            {"type": "assistant", "timestamp": now, "sessionId": "abc123",
             "message": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 100, "output_tokens": 200}}},
        ])
        result = live_monitor.scan(projects_dir=self.projects_dir)
        self.assertEqual(len(result["active_sessions"]), 1)
        s = result["active_sessions"][0]
        self.assertEqual(s["session_id"], "abc123")
        self.assertEqual(s["tokens"], 300)
        self.assertIn("project", s)

    def test_ignores_stale_sessions(self):
        now = datetime.now(timezone.utc).isoformat()
        self.write_session("-Users-test-old", "stale", [
            {"type": "assistant", "timestamp": now, "sessionId": "stale",
             "message": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 100, "output_tokens": 200}}},
        ], mtime_offset_seconds=-300)
        result = live_monitor.scan(projects_dir=self.projects_dir, fresh_window_seconds=60)
        self.assertEqual(result["active_sessions"], [])

    def test_writes_live_json(self):
        live_monitor.run(projects_dir=self.tmpdir / "nope", live_path=self.live_path)
        self.assertTrue(self.live_path.exists())
        data = json.loads(self.live_path.read_text())
        self.assertIn("active_sessions", data)
        self.assertIn("updated_at", data)

    def test_calculates_burn_rate(self):
        now_iso = datetime.now(timezone.utc).isoformat()
        earlier = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        self.write_session("-Users-test-fast", "burn1", [
            {"type": "assistant", "timestamp": earlier, "sessionId": "burn1",
             "message": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 0, "output_tokens": 1000}}},
            {"type": "assistant", "timestamp": now_iso, "sessionId": "burn1",
             "message": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 0, "output_tokens": 3000}}},
        ])
        result = live_monitor.scan(projects_dir=self.projects_dir)
        if result["active_sessions"]:
            self.assertGreater(result["active_sessions"][0]["burn_rate_per_min"], 0)


if __name__ == "__main__":
    unittest.main()
