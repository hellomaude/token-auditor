#!/usr/bin/env python3
"""
Token Auditor — Analyzes Claude Code session data for token usage patterns.
Reads JSONL session logs from ~/.claude/projects/ and generates a JSON report.

Usage: python3 analyze.py [--output report.json]
"""

import json
import os
import sys
import glob
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from pathlib import Path

CLAUDE_DIR = os.path.expanduser("~/.claude/projects")


def parse_timestamp(ts):
    """Parse ISO timestamp string to datetime."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def analyze_session(filepath):
    """Analyze a single session JSONL file."""
    session = {
        "file": str(filepath),
        "session_id": None,
        "project": None,
        "start_time": None,
        "end_time": None,
        "turns": 0,
        "assistant_turns": 0,
        "tool_uses": 0,
        "tool_types": Counter(),
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "total_tokens": 0,
        "idle_gaps": [],  # gaps > 5 min between turns
        "file_reads": Counter(),  # track file read paths
        "models_used": Counter(),
        "timestamps": [],
    }

    try:
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if not session["session_id"] and entry.get("sessionId"):
                    session["session_id"] = entry["sessionId"]

                if not session["project"] and entry.get("cwd"):
                    # Project name = basename of the session's working directory
                    session["project"] = os.path.basename(entry["cwd"]) or "unknown"

                ts = parse_timestamp(entry.get("timestamp"))
                if ts:
                    session["timestamps"].append(ts)

                msg_type = entry.get("type")

                if msg_type == "assistant":
                    session["assistant_turns"] += 1
                    msg = entry.get("message", {})
                    usage = msg.get("usage", {})

                    input_t = usage.get("input_tokens", 0)
                    output_t = usage.get("output_tokens", 0)
                    cache_create = usage.get("cache_creation_input_tokens", 0)
                    cache_read = usage.get("cache_read_input_tokens", 0)

                    session["input_tokens"] += input_t
                    session["output_tokens"] += output_t
                    session["cache_creation_tokens"] += cache_create
                    session["cache_read_tokens"] += cache_read
                    session["total_tokens"] += input_t + output_t

                    model = msg.get("model", "unknown")
                    session["models_used"][model] += 1

                    # Count tool uses
                    for content in msg.get("content", []):
                        if content.get("type") == "tool_use":
                            session["tool_uses"] += 1
                            session["tool_types"][content.get("name", "unknown")] += 1

                elif msg_type == "human" or msg_type == "user":
                    session["turns"] += 1
                    # Track file reads from tool results
                    msg = entry.get("message", {})
                    content = msg.get("content") if isinstance(msg, dict) else None
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                tool_input = block.get("tool_use_id", "")
                                # We'll catch reads from the tool_use side instead

                # Track Read tool calls specifically
                if msg_type == "assistant":
                    msg = entry.get("message", {})
                    for content in msg.get("content", []):
                        if content.get("type") == "tool_use":
                            name = content.get("name", "")
                            inp = content.get("input", {})
                            if name in ("Read", "read_file") and inp.get("file_path"):
                                session["file_reads"][inp["file_path"]] += 1

    except (IOError, PermissionError):
        return None

    # Calculate timing
    if session["timestamps"]:
        session["timestamps"].sort()
        session["start_time"] = session["timestamps"][0].isoformat()
        session["end_time"] = session["timestamps"][-1].isoformat()

        # Find idle gaps (> 5 minutes between consecutive timestamps)
        for i in range(1, len(session["timestamps"])):
            gap = (session["timestamps"][i] - session["timestamps"][i - 1]).total_seconds()
            if gap > 300:  # 5 minutes
                session["idle_gaps"].append({
                    "gap_seconds": round(gap),
                    "after_turn": i,
                    "at": session["timestamps"][i].isoformat(),
                })

        duration = (session["timestamps"][-1] - session["timestamps"][0]).total_seconds()
        session["duration_seconds"] = round(duration)
        session["duration_minutes"] = round(duration / 60, 1)
    else:
        session["duration_seconds"] = 0
        session["duration_minutes"] = 0

    # Find redundant reads (same file read 3+ times)
    session["redundant_reads"] = {
        path: count for path, count in session["file_reads"].items() if count >= 3
    }

    # Convert Counters to dicts for JSON serialization
    session["tool_types"] = dict(session["tool_types"])
    session["models_used"] = dict(session["models_used"])
    session["file_reads"] = dict(session["file_reads"])
    del session["timestamps"]

    return session


def generate_report(sessions):
    """Generate aggregate report from all session analyses."""
    report = {
        "generated_at": datetime.now().isoformat(),
        "total_sessions": len(sessions),
        "summary": {},
        "by_project": {},
        "by_model": defaultdict(lambda: {"tokens": 0, "turns": 0, "sessions": 0}),
        "token_waste": {},
        "top_tools": Counter(),
        "top_redundant_reads": [],
        "idle_gap_analysis": {},
        "daily_usage": defaultdict(lambda: {"tokens": 0, "sessions": 0, "turns": 0}),
        "sessions": [],
    }

    total_input = 0
    total_output = 0
    total_cache_create = 0
    total_cache_read = 0
    total_turns = 0
    total_tool_uses = 0
    total_idle_gaps = 0
    total_idle_gap_turns = 0
    total_redundant_reads = 0
    all_redundant = Counter()

    for s in sessions:
        total_input += s["input_tokens"]
        total_output += s["output_tokens"]
        total_cache_create += s["cache_creation_tokens"]
        total_cache_read += s["cache_read_tokens"]
        total_turns += s["assistant_turns"]
        total_tool_uses += s["tool_uses"]

        # Tool usage
        for tool, count in s["tool_types"].items():
            report["top_tools"][tool] += count

        # Model usage
        for model, count in s["models_used"].items():
            report["by_model"][model]["tokens"] += s["total_tokens"]
            report["by_model"][model]["turns"] += s["assistant_turns"]
        for model in s["models_used"]:
            report["by_model"][model]["sessions"] += 1

        # Project breakdown
        proj = s["project"] or "unknown"
        if proj not in report["by_project"]:
            report["by_project"][proj] = {
                "sessions": 0, "tokens": 0, "turns": 0,
                "input_tokens": 0, "output_tokens": 0,
                "cache_read": 0, "cache_create": 0,
            }
        report["by_project"][proj]["sessions"] += 1
        report["by_project"][proj]["tokens"] += s["total_tokens"]
        report["by_project"][proj]["turns"] += s["assistant_turns"]
        report["by_project"][proj]["input_tokens"] += s["input_tokens"]
        report["by_project"][proj]["output_tokens"] += s["output_tokens"]
        report["by_project"][proj]["cache_read"] += s["cache_read_tokens"]
        report["by_project"][proj]["cache_create"] += s["cache_creation_tokens"]

        # Idle gaps
        total_idle_gaps += len(s["idle_gaps"])
        total_idle_gap_turns += len(s["idle_gaps"])

        # Redundant reads
        for path, count in s["redundant_reads"].items():
            total_redundant_reads += count - 1  # excess reads
            all_redundant[path] += count

        # Daily usage
        if s["start_time"]:
            day = s["start_time"][:10]
            report["daily_usage"][day]["tokens"] += s["total_tokens"]
            report["daily_usage"][day]["sessions"] += 1
            report["daily_usage"][day]["turns"] += s["assistant_turns"]

        # Store condensed session info
        report["sessions"].append({
            "session_id": s["session_id"],
            "project": s["project"],
            "start_time": s["start_time"],
            "duration_minutes": s["duration_minutes"],
            "turns": s["assistant_turns"],
            "total_tokens": s["total_tokens"],
            "input_tokens": s["input_tokens"],
            "output_tokens": s["output_tokens"],
            "cache_read_tokens": s["cache_read_tokens"],
            "tool_uses": s["tool_uses"],
            "idle_gaps": len(s["idle_gaps"]),
            "redundant_reads": len(s["redundant_reads"]),
        })

    # Summary
    report["summary"] = {
        "total_tokens": total_input + total_output,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cache_creation_tokens": total_cache_create,
        "total_cache_read_tokens": total_cache_read,
        "cache_hit_rate": round(total_cache_read / max(total_input, 1) * 100, 1),
        "total_turns": total_turns,
        "total_tool_uses": total_tool_uses,
        "avg_tokens_per_turn": round(total_input / max(total_turns, 1)),
        "avg_tokens_per_session": round((total_input + total_output) / max(len(sessions), 1)),
    }

    # Token waste analysis
    report["token_waste"] = {
        "idle_gap_count": total_idle_gaps,
        "idle_gap_pct_of_turns": round(total_idle_gap_turns / max(total_turns, 1) * 100, 1),
        "redundant_file_reads": total_redundant_reads,
        "top_reread_files": [
            {"path": p, "total_reads": c}
            for p, c in all_redundant.most_common(20)
        ],
    }

    # Top tools
    report["top_tools"] = [
        {"tool": t, "count": c}
        for t, c in report["top_tools"].most_common(30)
    ]

    # Idle gap analysis
    all_gaps = []
    for s in sessions:
        all_gaps.extend(g["gap_seconds"] for g in s["idle_gaps"])
    if all_gaps:
        report["idle_gap_analysis"] = {
            "total_gaps": len(all_gaps),
            "avg_gap_seconds": round(sum(all_gaps) / len(all_gaps)),
            "max_gap_seconds": max(all_gaps),
            "total_idle_seconds": sum(all_gaps),
            "total_idle_minutes": round(sum(all_gaps) / 60, 1),
        }

    # Convert defaultdicts
    report["by_model"] = dict(report["by_model"])
    report["daily_usage"] = dict(sorted(report["daily_usage"].items()))

    # Sort sessions by start time
    report["sessions"].sort(key=lambda s: s["start_time"] or "")

    return report


def main():
    output_path = "report.json"
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output_path = sys.argv[idx + 1]

    print("Token Auditor — Scanning session data...")

    # Find all session JSONL files
    jsonl_files = glob.glob(os.path.join(CLAUDE_DIR, "**", "*.jsonl"), recursive=True)

    # Separate main sessions from subagents
    main_files = [f for f in jsonl_files if "/subagents/" not in f]
    subagent_files = [f for f in jsonl_files if "/subagents/" in f]

    print(f"Found {len(main_files)} main sessions, {len(subagent_files)} subagent sessions")

    # Analyze all main sessions
    sessions = []
    for i, filepath in enumerate(main_files):
        if (i + 1) % 50 == 0:
            print(f"  Analyzed {i + 1}/{len(main_files)}...")
        result = analyze_session(filepath)
        if result and result["assistant_turns"] > 0:
            sessions.append(result)

    print(f"Analyzed {len(sessions)} sessions with data")

    # Generate report
    report = generate_report(sessions)

    # Add subagent count
    report["subagent_sessions"] = len(subagent_files)

    # Multi-source: stitch in Codex CLI + Cursor reports if their adapters are present
    report["sources"] = {"claude-code": True}
    try:
        import codex_analyze
        codex_report = codex_analyze.run()
        report["codex"] = codex_report
        report["sources"]["codex-cli"] = True
        cs = codex_report["summary"]
        print(f"Codex: {cs['total_sessions']} sessions, {cs['total_tokens']:,} tokens, ${cs['total_cost']:.2f}")
    except Exception as e:
        print(f"Codex skipped: {e}")
    try:
        import cursor_analyze
        cursor_report = cursor_analyze.run()
        report["cursor"] = cursor_report
        if "error" not in cursor_report:
            report["sources"]["cursor"] = True
            cs = cursor_report["summary"]
            print(f"Cursor: {cs['total_composers']} composers, {cs['total_messages']:,} messages (no token data)")
    except Exception as e:
        print(f"Cursor skipped: {e}")

    # Write report
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\nReport saved to: {output_path}")
    print(f"\n=== Quick Summary ===")
    s = report["summary"]
    print(f"Sessions: {report['total_sessions']} (+{report['subagent_sessions']} subagents)")
    print(f"Total tokens: {s['total_tokens']:,}")
    print(f"Avg tokens/turn: {s['avg_tokens_per_turn']:,}")
    print(f"Cache hit rate: {s['cache_hit_rate']}%")
    w = report["token_waste"]
    print(f"Idle gaps (>5min): {w['idle_gap_count']} ({w['idle_gap_pct_of_turns']}% of turns)")
    print(f"Redundant file reads: {w['redundant_file_reads']}")


if __name__ == "__main__":
    main()
