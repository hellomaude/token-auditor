#!/usr/bin/env python3
"""Cursor analyzer — reads ~/Library/Application Support/Cursor/User/globalStorage/state.vscdb
and emits cursor_report.json.

Caveat: Cursor stores essentially no token data locally. Every bubble's tokenCount
fields are zero. The only real spend data lives behind the dashboard API
(cursor.com/api/dashboard/get-current-period-usage), which is undocumented and flaky.

This adapter does Tier B (local proxy metrics): composer counts, message counts,
models used, attached file/folder counts, git diff counts. It can't tell you cost,
but it can tell you which projects you're spending Cursor time in.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path.home() / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
OUT_PATH = Path(__file__).parent / "cursor_report.json"


def safe_json(blob):
    if blob is None:
        return None
    try:
        return json.loads(blob)
    except (json.JSONDecodeError, TypeError):
        return None


def collect():
    if not DB_PATH.exists():
        return None
    # Open read-only so we never lock the live Cursor DB
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = conn.cursor()

    # 1. Pull all bubbles, index by id, count types per composer later
    bubbles = {}
    cur.execute("SELECT key, value FROM cursorDiskKV WHERE key LIKE 'bubbleId:%' AND value IS NOT NULL")
    for key, val in cur.fetchall():
        o = safe_json(val)
        if not o:
            continue
        # key is bubbleId:<composerId>:<bubbleId>
        parts = key.split(":")
        composer_id = parts[1] if len(parts) >= 3 else None
        bid = o.get("bubbleId") or (parts[2] if len(parts) >= 3 else None)
        if not bid:
            continue
        bubbles[bid] = {
            "composer_id": composer_id,
            "type": o.get("type"),
            "lints": len(o.get("lints") or []),
            "attached_chunks": len(o.get("attachedCodeChunks") or []),
            "git_diffs": len(o.get("gitDiffs") or []),
            "interpreter_results": len(o.get("interpreterResults") or []),
            "images": len(o.get("images") or []),
        }

    # 2. Walk composers, attach bubble metrics
    cur.execute("SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%' AND value IS NOT NULL")
    sessions = []
    for key, val in cur.fetchall():
        o = safe_json(val)
        if not o:
            continue
        composer_id = o.get("composerId") or key.split(":", 1)[-1]
        headers = o.get("fullConversationHeadersOnly") or []
        if not headers and not bubbles:
            # Empty composer
            continue

        user_msgs = 0
        asst_msgs = 0
        lints = 0
        attached = 0
        diffs = 0
        for h in headers:
            bid = h.get("bubbleId")
            if not bid:
                # Some headers carry just type
                t = h.get("type")
                if t == 1:
                    user_msgs += 1
                elif t == 2:
                    asst_msgs += 1
                continue
            b = bubbles.get(bid)
            if b:
                if b["type"] == 1:
                    user_msgs += 1
                elif b["type"] == 2:
                    asst_msgs += 1
                lints += b["lints"]
                attached += b["attached_chunks"]
                diffs += b["git_diffs"]
            else:
                t = h.get("type")
                if t == 1:
                    user_msgs += 1
                elif t == 2:
                    asst_msgs += 1

        created_ms = o.get("createdAt")
        created_iso = (
            datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).isoformat()
            if isinstance(created_ms, (int, float)) else None
        )
        model_cfg = o.get("modelConfig") or {}
        sessions.append({
            "composer_id": composer_id,
            "name": o.get("name"),
            "created_at": created_iso,
            "model": model_cfg.get("modelName") or "default",
            "max_mode": bool(model_cfg.get("maxMode")),
            "user_messages": user_msgs,
            "assistant_messages": asst_msgs,
            "total_messages": user_msgs + asst_msgs,
            "lint_warnings": lints,
            "attached_chunks": attached,
            "git_diffs": diffs,
            "newly_created_files": len(o.get("newlyCreatedFiles") or []),
            "newly_created_folders": len(o.get("newlyCreatedFolders") or []),
        })

    conn.close()
    return sessions


def aggregate(sessions):
    by_day = {}
    by_model = {}
    total_user = total_asst = total_lints = total_attached = total_diffs = 0

    for s in sessions:
        total_user += s["user_messages"]
        total_asst += s["assistant_messages"]
        total_lints += s["lint_warnings"]
        total_attached += s["attached_chunks"]
        total_diffs += s["git_diffs"]

        if s["created_at"]:
            day = s["created_at"][:10]
            d = by_day.setdefault(day, {"composers": 0, "messages": 0})
            d["composers"] += 1
            d["messages"] += s["total_messages"]

        m = s["model"] or "default"
        bm = by_model.setdefault(m, {"composers": 0, "messages": 0, "max_mode": 0})
        bm["composers"] += 1
        bm["messages"] += s["total_messages"]
        if s["max_mode"]:
            bm["max_mode"] += 1

    sessions.sort(key=lambda s: s.get("created_at") or "", reverse=True)

    return {
        "tool": "cursor",
        "tier": "B-local-metrics",
        "note": "Cursor's local SQLite contains no token data — all tokenCount fields are zero. "
                "Real spend lives behind the dashboard API. These are activity proxies only.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_composers": len(sessions),
            "total_user_messages": total_user,
            "total_assistant_messages": total_asst,
            "total_messages": total_user + total_asst,
            "total_lint_warnings": total_lints,
            "total_attached_chunks": total_attached,
            "total_git_diffs": total_diffs,
        },
        "by_day": dict(sorted(by_day.items())),
        "by_model": by_model,
        "sessions": sessions[:100],
    }


def run(out_path: Path = OUT_PATH):
    if not DB_PATH.exists():
        report = {
            "tool": "cursor",
            "error": f"Cursor DB not found at {DB_PATH}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    else:
        sessions = collect() or []
        report = aggregate(sessions)
    out_path.write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    r = run()
    if "error" in r:
        print(f"Cursor: {r['error']}")
    else:
        s = r["summary"]
        print(f"Cursor: {s['total_composers']} composers, "
              f"{s['total_messages']:,} messages "
              f"({s['total_user_messages']} user / {s['total_assistant_messages']} assistant)")
        print(f"  → {OUT_PATH}")
        print("  ⚠ Local data has no token counts. Use Cursor's billing dashboard for real spend.")
