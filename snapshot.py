#!/usr/bin/env python3
"""Daily snapshot — appends today's stats to history.jsonl for trend tracking."""
import json
from datetime import datetime
from pathlib import Path

DEFAULT_REPORT = Path(__file__).parent / "report.json"
DEFAULT_HISTORY = Path(__file__).parent / "history.jsonl"

# Per-million pricing (input, output)
MODEL_PRICING = {
    "claude-opus-4-6": (15.00, 75.00),
    "claude-opus-4-20250514": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5-20250514": (3.00, 15.00),
    "claude-sonnet-4-20250514": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (0.80, 4.00),
}


def calculate_cost(by_model: dict) -> float:
    """Estimate cost in USD from per-model token usage."""
    total = 0.0
    for model, data in by_model.items():
        tokens = data.get("tokens", 0)
        if model in MODEL_PRICING:
            input_price, output_price = MODEL_PRICING[model]
            total += (tokens * 0.2 / 1_000_000) * input_price
            total += (tokens * 0.8 / 1_000_000) * output_price
    return round(total, 2)


def calculate_score(report: dict) -> int:
    """Compute efficiency score 0-100."""
    summary = report.get("summary", {})
    waste = report.get("token_waste", {})

    redundant = waste.get("redundant_file_reads", 0)
    idle_pct = waste.get("idle_gap_pct_of_turns", 0)
    avg_per_session = summary.get("avg_tokens_per_session", 0)

    read_penalty = min(redundant / 50, 30)
    idle_penalty = min(idle_pct * 3, 25)
    token_penalty = min(max(avg_per_session - 15000, 0) / 2000, 20)
    return max(0, round(100 - read_penalty - idle_penalty - token_penalty))


def run(report_path: Path = DEFAULT_REPORT, history_path: Path = DEFAULT_HISTORY) -> dict:
    """Generate today's snapshot and write to history.jsonl."""
    report = json.loads(Path(report_path).read_text())

    today = datetime.now().strftime("%Y-%m-%d")
    entry = {
        "date": today,
        "score": calculate_score(report),
        "tokens": report.get("summary", {}).get("total_tokens", 0),
        "sessions": report.get("total_sessions", 0),
        "turns": report.get("summary", {}).get("total_turns", 0),
        "tool_uses": report.get("summary", {}).get("total_tool_uses", 0),
        "idle_gaps": report.get("token_waste", {}).get("idle_gap_count", 0),
        "redundant_reads": report.get("token_waste", {}).get("redundant_file_reads", 0),
        "cost": calculate_cost(report.get("by_model", {})),
    }

    history_path = Path(history_path)
    existing = []
    if history_path.exists():
        for line in history_path.read_text().strip().split("\n"):
            if not line:
                continue
            try:
                row = json.loads(line)
                if row.get("date") != today:
                    existing.append(row)
            except json.JSONDecodeError:
                continue
    existing.append(entry)

    history_path.write_text("\n".join(json.dumps(r) for r in existing) + "\n")
    return entry


if __name__ == "__main__":
    entry = run()
    print(f"Snapshot for {entry['date']}: score={entry['score']}, "
          f"tokens={entry['tokens']:,}, cost=${entry['cost']:.2f}")
