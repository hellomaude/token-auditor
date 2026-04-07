#!/usr/bin/env python3
"""Gmail OAuth receipt extractor — bulk-scan your inbox for receipts.

Unlike the MCP-based approach (which burns tokens reading each email body through
Claude), this script uses Google's Gmail API directly via OAuth. Once authorized,
it can read thousands of emails for free, extract amounts via regex, and populate
receipts.jsonl via the existing receipts.py helper.

First-time setup:
1. Create a Google Cloud project: https://console.cloud.google.com/
2. Enable Gmail API: https://console.cloud.google.com/apis/library/gmail.googleapis.com
3. Create OAuth 2.0 credentials (Desktop app): https://console.cloud.google.com/apis/credentials
4. Download credentials.json to this directory
5. pip install google-auth google-auth-oauthlib google-api-python-client
6. python3 gmail_oauth_extract.py
   (first run opens a browser for authorization, saves token.json)

Usage:
    python3 gmail_oauth_extract.py                   # scan last 4 months (default)
    python3 gmail_oauth_extract.py --days 120        # custom lookback
    python3 gmail_oauth_extract.py --after 2025-12-06
    python3 gmail_oauth_extract.py --vendor anthropic  # just one vendor
    python3 gmail_oauth_extract.py --dry-run         # preview, don't save
"""
import argparse
import base64
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    print("ERROR: Missing dependencies. Install with:", file=sys.stderr)
    print("  pip install google-auth google-auth-oauthlib google-api-python-client", file=sys.stderr)
    sys.exit(1)

import receipts

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
AUDITOR_DIR = Path(__file__).parent
CREDS_FILE = AUDITOR_DIR / "credentials.json"
TOKEN_FILE = AUDITOR_DIR / "token.json"

# Query per vendor. Each entry: (search query, parser function name).
VENDOR_QUERIES = [
    ("anthropic", "from:invoice+statements@mail.anthropic.com"),
    ("openai_api", "from:billing@tm1.openai.com"),
    ("openai_plus", "from:noreply@tm.openai.com subject:plan"),
    ("cloudflare_invoice", 'from:noreply@notify.cloudflare.com "Invoice Amount"'),
    ("cloudflare_domain", 'from:noreply@notify.cloudflare.com "Domain registered"'),
    ("cursor", "from:cursor.com OR from:cursor.sh subject:receipt"),
    ("vercel", "from:vercel.com subject:invoice OR subject:receipt"),
    ("railway", "from:railway.app subject:invoice OR subject:receipt"),
    ("supabase", "from:supabase.com subject:invoice OR subject:receipt"),
    ("github", "from:billing@github.com OR from:noreply@github.com subject:receipt"),
    ("stripe_generic", "from:stripe.com subject:receipt"),
    ("apify", "from:billing@apify.com subject:payment"),
    ("replicate", "from:replicate.com subject:invoice"),
    ("elevenlabs", "from:elevenlabs.io subject:receipt"),
    ("perplexity", "from:perplexity.ai subject:receipt"),
    ("figma", "from:figma.com subject:receipt"),
    ("linear", "from:linear.app subject:receipt"),
    ("notion", "from:notion.so subject:receipt"),
    ("obsidian", "from:obsidian.md subject:receipt"),
]

# Regex patterns for extracting amounts from bodies
AMOUNT_PATTERNS = [
    # Stripe-style: "Receipt from VENDOR $X.XX Paid Month DD, YYYY"
    re.compile(r"Receipt from [^$]+\$(\d+(?:\.\d{2})?)\s+Paid\s+([A-Z][a-z]+\s+\d{1,2},\s*\d{4})"),
    # OpenAI API funding: "We charged $X.XX to your credit card"
    re.compile(r"We charged \$(\d+(?:\.\d{2})?)"),
    # Cloudflare invoice: "Invoice Amount: $X.XX"
    re.compile(r"Invoice Amount:?\s*\$(\d+(?:\.\d{2})?)"),
    # Generic "Total $X.XX" / "Amount paid $X.XX"
    re.compile(r"(?:Total|Amount paid|Amount):?\s*\$(\d+(?:\.\d{2})?)"),
    # Fallback: first $X.XX in the body
    re.compile(r"\$(\d+(?:\.\d{2})?)"),
]

# Vendor name extraction from sender/subject
VENDOR_MAP = {
    "mail.anthropic.com": "Anthropic",
    "anthropic.com": "Anthropic",
    "tm1.openai.com": "OpenAI",
    "tm.openai.com": "OpenAI",
    "openai.com": "OpenAI",
    "notify.cloudflare.com": "Cloudflare",
    "cloudflare.com": "Cloudflare",
    "cursor.com": "Cursor",
    "cursor.sh": "Cursor",
    "vercel.com": "Vercel",
    "railway.app": "Railway",
    "supabase.com": "Supabase",
    "github.com": "GitHub",
    "apify.com": "Apify",
    "replicate.com": "Replicate",
    "elevenlabs.io": "ElevenLabs",
    "perplexity.ai": "Perplexity",
    "figma.com": "Figma",
    "linear.app": "Linear",
    "notion.so": "Notion",
    "obsidian.md": "Obsidian",
    "hume.ai": "Hume AI",
    "mobbin.com": "Mobbin",
}


def authenticate():
    """Return an authenticated Gmail service client."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDS_FILE.exists():
                print(f"ERROR: {CREDS_FILE} not found.", file=sys.stderr)
                print("Download OAuth credentials from Google Cloud Console and save as credentials.json", file=sys.stderr)
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def get_header(msg, name):
    """Get a header value from a Gmail message."""
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def get_body(msg):
    """Extract the plain text body from a Gmail message."""
    def walk(part):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        for sub in part.get("parts", []):
            text = walk(sub)
            if text:
                return text
        return ""
    return walk(msg.get("payload", {}))


def extract_vendor(sender: str, subject: str) -> str:
    """Best-effort vendor name."""
    sender_lower = sender.lower()
    for domain, name in VENDOR_MAP.items():
        if domain in sender_lower:
            return name
    # Stripe-routed receipts — extract vendor from subject: "Your receipt from VENDOR #..."
    m = re.search(r"receipt from ([\w\s&.,'-]+?)\s*#", subject, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return "Unknown"


def extract_amount(body: str) -> float | None:
    """Try each regex pattern to find a dollar amount."""
    for pattern in AMOUNT_PATTERNS:
        match = pattern.search(body)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, IndexError):
                continue
    return None


def extract_date(body: str, fallback_iso: str) -> str:
    """Parse a date from body, fall back to email date (YYYY-MM-DD)."""
    m = re.search(r"Paid\s+([A-Z][a-z]+\s+\d{1,2},\s*\d{4})", body)
    if m:
        try:
            return datetime.strptime(m.group(1), "%B %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return fallback_iso[:10] if fallback_iso else datetime.now().strftime("%Y-%m-%d")


def scan_query(service, query: str, label: str, dry_run: bool = False) -> tuple[int, float]:
    """Scan a single Gmail query, extract all receipts, save to store."""
    print(f"\n→ {label}: {query}")
    added = 0
    total = 0.0
    page_token = None
    pages_scanned = 0

    while True:
        resp = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=100,
            pageToken=page_token,
        ).execute()

        msgs = resp.get("messages", [])
        if not msgs:
            print(f"  (no messages)")
            break

        for meta in msgs:
            msg = service.users().messages().get(userId="me", id=meta["id"], format="full").execute()
            subject = get_header(msg, "Subject")
            sender = get_header(msg, "From")
            date_header = get_header(msg, "Date")

            body = get_body(msg)
            amount = extract_amount(body)
            if amount is None or amount == 0:
                continue  # skip unparseable or $0 receipts

            vendor = extract_vendor(sender, subject)
            internal_date = msg.get("internalDate", "")
            fallback_iso = datetime.fromtimestamp(int(internal_date) / 1000).isoformat() if internal_date else ""
            date_str = extract_date(body, fallback_iso)

            receipt = {
                "date": date_str,
                "vendor": vendor,
                "amount": amount,
                "currency": "USD",
                "description": subject[:100],
                "email_subject": subject,
                "email_from": sender,
                "gmail_id": meta["id"],
                "source": "gmail_oauth",
            }

            if dry_run:
                print(f"  [dry] {date_str} {vendor} ${amount:.2f}")
                added += 1
                total += amount
            else:
                if receipts.add(receipt):
                    added += 1
                    total += amount

        page_token = resp.get("nextPageToken")
        pages_scanned += 1
        if not page_token or pages_scanned >= 10:  # cap at 1000 messages per query
            break

    print(f"  {added} receipts, ${total:,.2f}")
    return added, total


def main():
    parser = argparse.ArgumentParser(description="Bulk Gmail receipt extractor via OAuth")
    parser.add_argument("--days", type=int, default=120, help="Days to look back (default: 120 = 4 months)")
    parser.add_argument("--after", help="Explicit after date (YYYY-MM-DD), overrides --days")
    parser.add_argument("--vendor", help="Only scan a specific vendor key from VENDOR_QUERIES")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be saved, don't write")
    args = parser.parse_args()

    if args.after:
        after = args.after
    else:
        after = (datetime.now() - timedelta(days=args.days)).strftime("%Y/%m/%d")

    print(f"Scanning Gmail for receipts after {after}")
    print(f"Output: {'(dry run)' if args.dry_run else receipts.DEFAULT_RECEIPTS}")

    service = authenticate()
    print("Authenticated.")

    queries = VENDOR_QUERIES
    if args.vendor:
        queries = [q for q in VENDOR_QUERIES if q[0] == args.vendor]
        if not queries:
            print(f"Unknown vendor '{args.vendor}'. Known: {[q[0] for q in VENDOR_QUERIES]}", file=sys.stderr)
            sys.exit(1)

    grand_added = 0
    grand_total = 0.0
    for label, base_query in queries:
        query = f"{base_query} after:{after.replace('-', '/')}"
        added, total = scan_query(service, query, label, dry_run=args.dry_run)
        grand_added += added
        grand_total += total

    print(f"\n{'=' * 50}")
    print(f"Total added: {grand_added}")
    print(f"Total amount: ${grand_total:,.2f}")
    print(f"{'=' * 50}")

    if not args.dry_run and grand_added > 0:
        s = receipts.summarize(receipts.load())
        print(f"\nReceipt store now has {s['count']} receipts totaling ${s['total']:,.2f}")


if __name__ == "__main__":
    main()
