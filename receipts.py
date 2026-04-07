#!/usr/bin/env python3
"""Receipt store — manages receipts.jsonl for business software / AI expenses.

A receipt is:
{
  "id": "gmail-msg-id-or-hash",
  "date": "2026-04-03",
  "vendor": "Anthropic",
  "amount": 200.00,
  "currency": "USD",
  "category": "ai-api",
  "description": "Claude Pro subscription",
  "email_subject": "Your Anthropic receipt",
  "email_from": "billing@anthropic.com",
  "pdf_path": "receipts/2026/04/2026-04-03-anthropic-200.00.pdf",
  "source": "gmail"
}

Categories:
- ai-api       : Anthropic, OpenAI, Replicate, ElevenLabs, Perplexity, etc.
- ai-tool      : Cursor, Claude Code, Copilot, Claude Pro, ChatGPT Plus
- dev-infra    : Cloudflare, Railway, Vercel, Fly.io, Supabase, Render
- dev-tools    : GitHub, Linear, Notion, Figma, Obsidian, 1Password
- data         : Apify, scraping APIs, datasets
- other        : Anything else tagged by the user
"""
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

DEFAULT_RECEIPTS = Path(__file__).parent / "receipts.jsonl"
DEFAULT_PDF_DIR = Path(__file__).parent / "receipts"

# Vendor → category mapping
VENDOR_CATEGORIES = {
    # AI APIs
    "anthropic": "ai-api",
    "openai": "ai-api",
    "replicate": "ai-api",
    "elevenlabs": "ai-api",
    "perplexity": "ai-api",
    "mistral": "ai-api",
    "cohere": "ai-api",
    "together": "ai-api",
    # AI tools / subscriptions
    "cursor": "ai-tool",
    "claude": "ai-tool",
    "chatgpt": "ai-tool",
    "copilot": "ai-tool",
    "github copilot": "ai-tool",
    # Dev infrastructure
    "cloudflare": "dev-infra",
    "railway": "dev-infra",
    "vercel": "dev-infra",
    "fly.io": "dev-infra",
    "fly": "dev-infra",
    "supabase": "dev-infra",
    "render": "dev-infra",
    "digitalocean": "dev-infra",
    "aws": "dev-infra",
    "amazon web services": "dev-infra",
    # Dev tools
    "github": "dev-tools",
    "linear": "dev-tools",
    "notion": "dev-tools",
    "figma": "dev-tools",
    "obsidian": "dev-tools",
    "1password": "dev-tools",
    "raycast": "dev-tools",
    "warp": "dev-tools",
    # Data / APIs
    "apify": "data",
    "scraperapi": "data",
    "brightdata": "data",
}


def categorize(vendor: str) -> str:
    """Return category for a vendor name. Defaults to 'other'."""
    v = (vendor or "").lower()
    for key, cat in VENDOR_CATEGORIES.items():
        if key in v:
            return cat
    return "other"


def compute_id(receipt: dict) -> str:
    """Stable ID from date + vendor + amount + gmail id (if present)."""
    if receipt.get("gmail_id"):
        return f"gmail-{receipt['gmail_id']}"
    key = f"{receipt.get('date','')}-{receipt.get('vendor','')}-{receipt.get('amount','')}"
    return "hash-" + hashlib.sha1(key.encode()).hexdigest()[:12]


def load(path: Path = DEFAULT_RECEIPTS) -> list[dict]:
    """Load all receipts from JSONL. Returns empty list if file missing."""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def save_all(receipts: list[dict], path: Path = DEFAULT_RECEIPTS) -> None:
    """Write the entire list to JSONL, replacing the file."""
    path = Path(path)
    path.write_text("\n".join(json.dumps(r) for r in receipts) + ("\n" if receipts else ""))


def add(receipt: dict, path: Path = DEFAULT_RECEIPTS) -> bool:
    """Add a receipt. Deduplicates by id. Returns True if new, False if duplicate."""
    if "id" not in receipt:
        receipt["id"] = compute_id(receipt)
    if "category" not in receipt:
        receipt["category"] = categorize(receipt.get("vendor", ""))
    if "added_at" not in receipt:
        receipt["added_at"] = datetime.now().isoformat()

    existing = load(path)
    if any(r.get("id") == receipt["id"] for r in existing):
        return False
    existing.append(receipt)
    save_all(existing, path)
    return True


def query(
    path: Path = DEFAULT_RECEIPTS,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category: Optional[str] = None,
    vendor: Optional[str] = None,
) -> list[dict]:
    """Filter receipts by date range / category / vendor."""
    rows = load(path)
    if start_date:
        rows = [r for r in rows if r.get("date", "") >= start_date]
    if end_date:
        rows = [r for r in rows if r.get("date", "") <= end_date]
    if category:
        rows = [r for r in rows if r.get("category") == category]
    if vendor:
        rows = [r for r in rows if vendor.lower() in (r.get("vendor") or "").lower()]
    return sorted(rows, key=lambda r: r.get("date", ""))


def summarize(rows: list[dict]) -> dict:
    """Roll up a list of receipts into totals by category and vendor."""
    total = 0.0
    by_category: dict[str, float] = {}
    by_vendor: dict[str, float] = {}
    for r in rows:
        amt = float(r.get("amount") or 0)
        total += amt
        cat = r.get("category") or "other"
        by_category[cat] = round(by_category.get(cat, 0) + amt, 2)
        vend = r.get("vendor") or "unknown"
        by_vendor[vend] = round(by_vendor.get(vend, 0) + amt, 2)
    return {
        "count": len(rows),
        "total": round(total, 2),
        "by_category": dict(sorted(by_category.items(), key=lambda x: -x[1])),
        "by_vendor": dict(sorted(by_vendor.items(), key=lambda x: -x[1])),
    }


if __name__ == "__main__":
    import sys
    rows = load()
    if not rows:
        print("No receipts yet. Run the Gmail extractor to populate.")
        sys.exit(0)
    s = summarize(rows)
    print(f"Receipts: {s['count']}")
    print(f"Total: ${s['total']:,.2f}")
    print("\nBy category:")
    for cat, amt in s["by_category"].items():
        print(f"  {cat:12s}  ${amt:>10,.2f}")
    print("\nTop vendors:")
    for vend, amt in list(s["by_vendor"].items())[:10]:
        print(f"  {vend:30s}  ${amt:>10,.2f}")
