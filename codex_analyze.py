#!/usr/bin/env python3
"""Codex CLI analyzer — reads ~/.codex/sessions/**/rollout-*.jsonl and emits codex_report.json.

Codex CLI writes one JSONL file per session under ~/.codex/sessions/<YYYY>/<MM>/<DD>/.
Token usage lives in event_msg entries with payload.type == "token_count", inside
payload.info.total_token_usage. Cumulative — we diff against the previous turn to
get per-turn deltas.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

SESSIONS_DIRS = [
    Path.home() / ".codex" / "sessions",
    Path.home() / ".codex" / "archived_sessions",
]
OUT_PATH = Path(__file__).parent / "codex_report.json"

# Per-million-token pricing (input, output). Update as OpenAI ships new models.
# Longest keys are matched first, so put specific variants here even if a parent exists.
PRICING = {
    "gpt-5.4-pro":         (30.00, 180.00),
    "gpt-5.4-mini":         (0.25,   2.00),
    "gpt-5.4":              (2.50,  15.00),
    "gpt-5.3-codex":        (1.25,  10.00),
    "gpt-5.1-codex-mini":   (0.25,   2.00),
    "gpt-5-codex":          (1.25,  10.00),
    "gpt-5-mini":           (0.25,   2.00),
    "gpt-5":                (1.25,  10.00),
    "gpt-4.1-mini":         (0.40,   1.60),
    "gpt-4.1":              (2.00,   8.00),
    "gpt-4o-mini":          (0.15,   0.60),
    "gpt-4o":               (2.50,  10.00),
    "o4-mini":              (1.10,   4.40),
    "o3-mini":              (1.10,   4.40),
    "o3":                  (10.00,  40.00),
}
_PRICING_KEYS = sorted(PRICING.keys(), key=len, reverse=True)


def price(model, in_tok, out_tok):
    if not model:
        return 0.0
    key = None
    if model in PRICING:
        key = model
    else:
        for k in _PRICING_KEYS:
            if model.startswith(k):
                key = k
                break
    if not key:
        return 0.0
    pin, pout = PRICING[key]
    return round((in_tok / 1_000_000) * pin + (out_tok / 1_000_000) * pout, 4)


def parse_session(path: Path):
    """Return dict with meta + per-turn deltas + final totals, or None if unparseable."""
    meta = None
    last_total = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 0,
    }
    current_model = None
    turns = []
    started_at = None
    last_ts = None
    last_rate_limits = None
    last_rate_limits_ts = None

    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue

                t = o.get("type")
                ts_str = o.get("timestamp")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if not started_at or ts < started_at:
                            started_at = ts
                        if not last_ts or ts > last_ts:
                            last_ts = ts
                    except (ValueError, TypeError):
                        pass

                if t == "session_meta":
                    meta = o.get("payload", {})
                elif t == "turn_context":
                    m = o.get("payload", {}).get("model")
                    if m:
                        current_model = m
                elif t == "event_msg":
                    payload = o.get("payload", {})
                    if payload.get("type") != "token_count":
                        continue
                    # Rate limits are a sibling of "info" inside payload
                    rl = payload.get("rate_limits")
                    if rl:
                        last_rate_limits = rl
                        last_rate_limits_ts = ts_str
                    info = payload.get("info")
                    if not info:
                        continue
                    tot = info.get("total_token_usage") or {}
                    if not tot:
                        continue
                    delta = {k: tot.get(k, 0) - last_total.get(k, 0) for k in tot}
                    turns.append({
                        "ts": ts_str,
                        "model": current_model,
                        "delta": delta,
                    })
                    last_total = {k: tot.get(k, 0) for k in tot}
    except (IOError, PermissionError):
        return None

    if not turns and not meta:
        return None

    return {
        "session_id": (meta or {}).get("id") or path.stem,
        "cwd": (meta or {}).get("cwd"),
        "model": current_model,
        "started_at": started_at.isoformat() if started_at else None,
        "ended_at": last_ts.isoformat() if last_ts else None,
        "duration_minutes": round((last_ts - started_at).total_seconds() / 60, 1)
            if started_at and last_ts else 0,
        "turns": len(turns),
        "totals": last_total,
        "cost": price(current_model, last_total.get("input_tokens", 0), last_total.get("output_tokens", 0)),
        "rate_limits": last_rate_limits,
        "rate_limits_ts": last_rate_limits_ts,
    }


def find_rollouts():
    for base in SESSIONS_DIRS:
        if not base.exists():
            continue
        for path in base.glob("**/rollout-*.jsonl"):
            yield path


def aggregate():
    sessions = []
    by_day = {}
    by_model = {}
    by_project = {}
    total_in = total_out = total_reasoning = total_cached = 0
    total_cost = 0.0

    for path in find_rollouts():
        s = parse_session(path)
        if not s:
            continue
        sessions.append(s)
        tot = s["totals"]
        in_t = tot.get("input_tokens", 0)
        out_t = tot.get("output_tokens", 0)
        total_in += in_t
        total_out += out_t
        total_reasoning += tot.get("reasoning_output_tokens", 0)
        total_cached += tot.get("cached_input_tokens", 0)
        total_cost += s["cost"]

        if s["started_at"]:
            day = s["started_at"][:10]
            d = by_day.setdefault(day, {"tokens": 0, "cost": 0.0, "sessions": 0})
            d["tokens"] += in_t + out_t
            d["cost"] += s["cost"]
            d["sessions"] += 1

        m = s["model"] or "unknown"
        bm = by_model.setdefault(m, {"tokens": 0, "cost": 0.0, "sessions": 0})
        bm["tokens"] += in_t + out_t
        bm["cost"] += s["cost"]
        bm["sessions"] += 1

        proj = os.path.basename(s["cwd"]) if s.get("cwd") else "unknown"
        bp = by_project.setdefault(proj, {"tokens": 0, "cost": 0.0, "sessions": 0})
        bp["tokens"] += in_t + out_t
        bp["cost"] += s["cost"]
        bp["sessions"] += 1

    sessions.sort(key=lambda s: s.get("started_at") or "", reverse=True)

    # Find the newest rate_limits snapshot across all sessions — this is the
    # real subscription utilization, not an estimate from pricing tables.
    newest_rl = None
    newest_rl_ts = None
    for s in sessions:
        rl_ts = s.get("rate_limits_ts")
        rl = s.get("rate_limits")
        if rl and rl_ts and (newest_rl_ts is None or rl_ts > newest_rl_ts):
            newest_rl = rl
            newest_rl_ts = rl_ts

    return {
        "tool": "codex-cli",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rate_limits": newest_rl,
        "rate_limits_ts": newest_rl_ts,
        "summary": {
            "total_sessions": len(sessions),
            "total_input_tokens": total_in,
            "total_cached_input_tokens": total_cached,
            "total_output_tokens": total_out,
            "total_reasoning_tokens": total_reasoning,
            "total_tokens": total_in + total_out,
            "total_cost": round(total_cost, 2),
            "avg_tokens_per_session": round((total_in + total_out) / max(len(sessions), 1)),
        },
        "by_day": dict(sorted(by_day.items())),
        "by_model": by_model,
        "by_project": by_project,
        "sessions": sessions[:100],
    }


def run(out_path: Path = OUT_PATH):
    report = aggregate()
    out_path.write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    r = run()
    s = r["summary"]
    print(f"Codex: {s['total_sessions']} sessions, "
          f"{s['total_tokens']:,} tokens, ${s['total_cost']:.2f}")
    print(f"  → {OUT_PATH}")
