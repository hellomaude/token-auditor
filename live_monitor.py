#!/usr/bin/env python3
"""Live session monitor — polls active Claude Code sessions and writes live.json."""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_LIVE_PATH = Path(__file__).parent / "live.json"
FRESH_WINDOW_SECONDS = 90
IDLE_WARNING_SECONDS = 240

PRICING = {
    "claude-opus-4-6": (15.00, 75.00),
    "claude-opus-4-20250514": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5-20250514": (3.00, 15.00),
    "claude-sonnet-4-20250514": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (0.80, 4.00),
}


def parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def analyze_session_file(path: Path):
    """Read a session JSONL and extract live metrics. Returns dict or None."""
    try:
        lines = path.read_text().strip().split("\n")
    except (IOError, PermissionError):
        return None

    session_id = None
    project = None
    started_at = None
    last_ts = None
    total_input = 0
    total_output = 0
    model = None

    for line in lines:
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not session_id and entry.get("sessionId"):
            session_id = entry["sessionId"]
        if not project and entry.get("cwd"):
            # Project name = basename of the session's working directory
            project = os.path.basename(entry["cwd"]) or "unknown"

        ts = parse_iso(entry.get("timestamp"))
        if ts:
            if not started_at or ts < started_at:
                started_at = ts
            if not last_ts or ts > last_ts:
                last_ts = ts

        if entry.get("type") == "assistant":
            msg = entry.get("message", {})
            usage = msg.get("usage", {})
            total_input += usage.get("input_tokens", 0)
            total_output += usage.get("output_tokens", 0)
            if not model:
                model = msg.get("model")

    if not session_id or not last_ts:
        return None

    now = datetime.now(timezone.utc)
    idle_seconds = round((now - last_ts).total_seconds())
    duration_seconds = round((last_ts - started_at).total_seconds()) if started_at else 0
    duration_min = duration_seconds / 60

    burn_rate_per_min = round((total_input + total_output) / max(duration_min, 0.1)) if duration_min > 0 else 0

    cost = 0.0
    if model and model in PRICING:
        in_price, out_price = PRICING[model]
        cost = round((total_input / 1_000_000) * in_price + (total_output / 1_000_000) * out_price, 4)

    warning = None
    if idle_seconds >= IDLE_WARNING_SECONDS:
        warning = f"Idle for {idle_seconds // 60}m {idle_seconds % 60}s — cache will expire soon"

    return {
        "session_id": session_id,
        "project": project or "unknown",
        "started_at": started_at.isoformat() if started_at else None,
        "duration_minutes": round(duration_min, 1),
        "tokens": total_input + total_output,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "model": model,
        "burn_rate_per_min": burn_rate_per_min,
        "cost": cost,
        "idle_seconds": idle_seconds,
        "warning": warning,
    }


def scan(projects_dir: Path = DEFAULT_PROJECTS_DIR, fresh_window_seconds: int = FRESH_WINDOW_SECONDS) -> dict:
    """Find all active sessions across projects."""
    active = []
    projects_dir = Path(projects_dir)
    if not projects_dir.exists():
        return {"active_sessions": [], "updated_at": datetime.now(timezone.utc).isoformat()}

    cutoff = time.time() - fresh_window_seconds
    for path in projects_dir.glob("**/*.jsonl"):
        if "/subagents/" in str(path):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue

        analysis = analyze_session_file(path)
        if analysis:
            active.append(analysis)

    active.sort(key=lambda s: s["idle_seconds"])

    return {
        "active_sessions": active,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def run(projects_dir: Path = DEFAULT_PROJECTS_DIR, live_path: Path = DEFAULT_LIVE_PATH):
    """Scan and write live.json."""
    result = scan(projects_dir=projects_dir)
    Path(live_path).write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    result = run()
    n = len(result["active_sessions"])
    print(f"Live monitor: {n} active session(s) at {result['updated_at']}")
    for s in result["active_sessions"]:
        print(f"  {s['project']}/{s['session_id'][:8]}: {s['tokens']:,} tokens, idle {s['idle_seconds']}s")
