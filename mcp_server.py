#!/usr/bin/env python3
"""Token Tracker MCP server — stdlib only.

Speaks the MCP stdio protocol (JSON-RPC 2.0 over stdin/stdout) directly so
this works with any Python 3.9+ install — no `pip install mcp` needed.

Wire into Claude Code (~/.claude/settings.json):

    {
      "mcpServers": {
        "token-tracker": {
          "command": "python3",
          "args": ["/absolute/path/to/token-tracker/mcp_server.py"]
        }
      }
    }

Then in any Claude Code session, ask "what's my efficiency score today?"
or "where am I wasting tokens?" and Claude will call the appropriate tool.

Tools exposed:
    get_today_score      — today's score, tokens, cost, delta vs yesterday
    get_summary          — high-level summary across Claude Code + Codex + Cursor
    get_top_waste        — biggest current sources of waste
    get_redundant_reads  — files re-read 3+ times (so you can add to CLAUDE.md)
    get_live_sessions    — anything currently burning tokens
    refresh_report       — kick analyze.py to regenerate report.json
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
REPORT = ROOT / "report.json"
HISTORY = ROOT / "history.jsonl"
LIVE = ROOT / "live.json"

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "token-tracker"
SERVER_VERSION = "0.1.0"


# ---------- data helpers ----------

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, IOError):
        return {}


def _read_history() -> list:
    if not HISTORY.exists():
        return []
    out = []
    for line in HISTORY.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ---------- tool implementations ----------

def tool_get_today_score(_args):
    history = _read_history()
    if not history:
        return {"error": "No history yet. Run snapshot.py first."}
    today = history[-1]
    yesterday = history[-2] if len(history) >= 2 else None
    delta = None
    if yesterday and "score" in yesterday and "score" in today:
        delta = today["score"] - yesterday["score"]
    return {
        "date": today.get("date"),
        "score": today.get("score"),
        "tokens": today.get("tokens"),
        "cost": today.get("cost"),
        "score_delta_vs_yesterday": delta,
    }


def tool_get_summary(_args):
    r = _read_json(REPORT)
    if not r:
        return {"error": "No report.json. Run analyze.py first."}
    out = {
        "claude_code": r.get("summary"),
        "sources": r.get("sources", {}),
    }
    if "codex" in r:
        out["codex"] = r["codex"].get("summary")
    if "cursor" in r:
        out["cursor"] = r["cursor"].get("summary")
    return out


def tool_get_top_waste(args):
    limit = int(args.get("limit", 5))
    r = _read_json(REPORT)
    if not r:
        return {"error": "No report.json. Run analyze.py first."}
    waste = r.get("token_waste", {}) or {}
    sessions = sorted(
        r.get("sessions", []),
        key=lambda s: s.get("total_tokens") or 0,
        reverse=True,
    )[:limit]
    return {
        "redundant_file_reads": waste.get("redundant_file_reads"),
        "redundant_read_files": (waste.get("most_reread_files") or [])[:limit],
        "idle_gap_count": waste.get("idle_gap_count"),
        "idle_gap_pct_of_turns": waste.get("idle_gap_pct_of_turns"),
        "runaway_sessions": [
            {
                "session_id": s.get("session_id"),
                "project": s.get("project"),
                "tokens": s.get("total_tokens"),
            }
            for s in sessions
        ],
    }


def tool_get_redundant_reads(args):
    limit = int(args.get("limit", 10))
    r = _read_json(REPORT)
    files = (r.get("token_waste", {}) or {}).get("most_reread_files") or []
    return files[:limit]


def tool_get_live_sessions(_args):
    live = _read_json(LIVE)
    if not live:
        return {"active_sessions": [], "note": "live.json not found — live monitor not running."}
    return live


def tool_refresh_report(_args):
    result = subprocess.run(
        ["python3", str(ROOT / "analyze.py")],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    return {
        "ok": result.returncode == 0,
        "stdout_tail": (result.stdout or "").splitlines()[-5:],
        "stderr_tail": (result.stderr or "").splitlines()[-5:],
    }


TOOLS = [
    {
        "name": "get_today_score",
        "description": "Return today's efficiency score (0-100), token total, cost, and delta vs yesterday. Use when the user asks how their session is going or what their score is.",
        "inputSchema": {"type": "object", "properties": {}},
        "_fn": tool_get_today_score,
    },
    {
        "name": "get_summary",
        "description": "High-level summary across all sources (Claude Code + Codex CLI + Cursor). Use when the user wants an overview or cumulative spend across tools.",
        "inputSchema": {"type": "object", "properties": {}},
        "_fn": tool_get_summary,
    },
    {
        "name": "get_top_waste",
        "description": "Top sources of token waste in the current report: redundant reads, idle gaps, runaway sessions. Use when the user asks where they're wasting tokens or what to fix first.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "Max items per category", "default": 5}},
        },
        "_fn": tool_get_top_waste,
    },
    {
        "name": "get_redundant_reads",
        "description": "Files that have been read 3+ times across sessions, with re-read counts. Use when the user wants to know which files to add to CLAUDE.md so Claude stops re-reading them.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 10}},
        },
        "_fn": tool_get_redundant_reads,
    },
    {
        "name": "get_live_sessions",
        "description": "Claude Code sessions currently active (last activity within ~90s), with idle seconds, burn rate, and warnings. Use when the user asks what's running right now.",
        "inputSchema": {"type": "object", "properties": {}},
        "_fn": tool_get_live_sessions,
    },
    {
        "name": "refresh_report",
        "description": "Re-run analyze.py to regenerate report.json with the latest session data. Use after the user has finished work and wants fresh stats.",
        "inputSchema": {"type": "object", "properties": {}},
        "_fn": tool_refresh_report,
    },
]
TOOL_BY_NAME = {t["name"]: t for t in TOOLS}


# ---------- JSON-RPC plumbing ----------

def _public_tools():
    return [{k: v for k, v in t.items() if not k.startswith("_")} for t in TOOLS]


def _result(req_id, payload):
    return {"jsonrpc": "2.0", "id": req_id, "result": payload}


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle(msg):
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "notifications/initialized":
        return None  # notification, no reply

    if method == "tools/list":
        return _result(req_id, {"tools": _public_tools()})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = TOOL_BY_NAME.get(name)
        if not tool:
            return _error(req_id, -32601, f"Unknown tool: {name}")
        try:
            result = tool["_fn"](args)
        except Exception as e:
            return _error(req_id, -32000, f"Tool error: {e}")
        return _result(req_id, {
            "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}],
        })

    if method and method.startswith("notifications/"):
        return None

    if req_id is not None:
        return _error(req_id, -32601, f"Method not found: {method}")
    return None


def main():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        reply = handle(msg)
        if reply is None:
            continue
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
