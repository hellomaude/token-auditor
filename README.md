<div align="center">
  <img src="token-logo-light.png#gh-light-mode-only" alt="Token Tracker" width="440">
  <img src="token-logo-dark.png#gh-dark-mode-only" alt="Token Tracker" width="440">
</div>

<div align="center">
  <h3>A dashboard that shows you where your Claude Code tokens (and dollars) actually go.</h3>
</div>

<br>

Token Tracker reads your local Claude Code session logs and tells you:

- **How many tokens you're burning** per day, per project, per model
- **An efficiency score** (0–100) reflecting how much waste is in your sessions
- **Where the waste is** — idle gap cache expirations, files read 3+ times, wrong model choice, runaway sessions
- **What to fix** — actionable insights with one-click auto-fix buttons
- **A live session monitor** — watches your currently-running session and warns before a 5-min idle gap
- **Real spend from email receipts** — optional Gmail scanner for Anthropic / OpenAI / Cursor / Vercel / Cloudflare receipts, categorized and tracked
- **Tax reports** — CSV + Markdown exports grouped by category, month, quarter, or year

All local. No cloud. The dashboard reads your `~/.claude/projects/*/` JSONL files directly.

---

## Quick start

```bash
git clone https://github.com/YOUR-USERNAME/token-tracker.git
cd token-tracker
./start.sh
open http://127.0.0.1:8787/dashboard.html
```

That's it. Three processes start: the HTTP server, a live-session monitor loop, and an analyzer that refreshes data every 5 minutes.

Stop everything with `./start.sh stop`.

## What you'll see

**Dashboard** (`dashboard.html`) — the main view:

- Daily Briefing card with efficiency score, delta vs yesterday, streak counter
- Overview stats: total tokens, sessions, turns, idle gaps, redundant reads
- Waste Analysis cards with animated ring gauges
- Token-Saving Insights with impact bars and one-click "Fix it" buttons
- Action Checklist — prioritized items you can mark done (persists locally)
- Activity charts: daily usage, tokens by project, tokens by model, top tools
- Efficiency charts: session duration vs tokens, turns per session histogram
- Details tables: most re-read files, highest token sessions

**Costs** (`costs.html`) — separate page for tax tracking:

- Total business software spend, monthly average, biggest category, top vendor
- By-category breakdown (AI APIs, AI tools, dev infra, dev tools, data, other)
- Spend by month + category distribution charts
- Full vendor table, filterable receipts table

Both pages have a **dark mode** toggle (bottom right 🌙 / ☀️) that persists in localStorage.

## How it works

```
~/.claude/projects/
  ├── <project-1>/
  │   ├── session-abc.jsonl  ← raw Claude Code session log
  │   └── session-def.jsonl
  └── ...
        │
        ▼  analyze.py
     report.json     ← aggregated stats, used by dashboard.html
        │
        ▼  snapshot.py (daily)
     history.jsonl   ← one row per day, used for deltas + streak

~/.claude/projects/<active-session>.jsonl
        │
        ▼  live_monitor.py (every 10s)
     live.json       ← current session state, used by sidebar
```

None of this data leaves your machine. There's no telemetry, no cloud service, no API calls home. The dashboard is static HTML + a tiny Python server.

## The efficiency score

Three penalties compose the score (0–100, higher is better):

| Metric | What it catches | Max penalty |
|---|---|---|
| **Redundant file reads** | Same file read 3+ times in a session | −30 |
| **Idle gap percentage** | Turns after 5+ min idle (cache expires, rebuild costs ~10x) | −25 |
| **Avg tokens per session** | Unfocused sessions that blow through context | −20 |

From `snapshot.py`:

```python
def calculate_score(report):
    redundant = report["token_waste"].get("redundant_file_reads", 0)
    idle_pct = report["token_waste"].get("idle_gap_pct_of_turns", 0)
    avg = report["summary"].get("avg_tokens_per_session", 0)

    read_penalty = min(redundant / 50, 30)
    idle_penalty = min(idle_pct * 3, 25)
    token_penalty = min(max(avg - 15000, 0) / 2000, 20)
    return max(0, round(100 - read_penalty - idle_penalty - token_penalty))
```

Tune these weights to your taste.

## Requirements

- **macOS** (tested) or Linux (should work, menu bar plugin is macOS-only)
- **Python 3.9+** (core dashboard uses only the standard library)
- **An active Claude Code install** writing JSONL logs to `~/.claude/projects/`

Optional:

- **SwiftBar** (macOS) — for the menu bar widget
- **Google Cloud OAuth credentials** — for bulk receipt scanning

## Receipt tracking + tax reports

The Costs page reads `receipts.jsonl`. Populate it three ways:

**1. Manual add:**

```bash
python3 -c "
import receipts
receipts.add({
    'date': '2026-04-06',
    'vendor': 'Cursor',
    'amount': 20.00,
    'description': 'Cursor Pro April',
})
"
```

**2. Bulk Gmail scan via OAuth:**

```bash
# One-time setup
pip install -r requirements.txt

# Go to https://console.cloud.google.com/
# - Create a project
# - Enable the Gmail API
# - Create OAuth 2.0 credentials (Desktop app type)
# - Download credentials.json → save to this directory

# Then scan
python3 gmail_oauth_extract.py --dry-run      # preview
python3 gmail_oauth_extract.py                # last 120 days
python3 gmail_oauth_extract.py --days 365     # full year
python3 gmail_oauth_extract.py --vendor anthropic  # one vendor
```

The scanner pattern-matches known AI/dev vendors. Add more by editing `VENDOR_QUERIES` and `VENDOR_MAP` in `gmail_oauth_extract.py`.

**3. Generate tax reports:**

```bash
python3 tax_report.py                      # current year YTD
python3 tax_report.py --year 2025          # full year
python3 tax_report.py --quarter 2026-Q1
python3 tax_report.py --month 2026-04
```

Outputs land in `reports/` as both CSV (for your accountant) and Markdown (for you).

## Categories

Receipts auto-categorize by vendor name:

- **ai-api** — Anthropic, OpenAI, Replicate, ElevenLabs, Perplexity, Mistral, Cohere, Together, Hume AI
- **ai-tool** — Cursor, ChatGPT Pro/Plus, Claude Pro/Max, GitHub Copilot
- **dev-infra** — Cloudflare, Railway, Vercel, Fly.io, Supabase, Render, AWS, DigitalOcean
- **dev-tools** — GitHub, Linear, Notion, Figma, Obsidian, 1Password, Raycast, Warp, Mobbin
- **data** — Apify, ScraperAPI, BrightData
- **other** — anything not matched

Add/change categories in `receipts.py` → `VENDOR_CATEGORIES`.

## Menu bar widget (macOS, optional)

```bash
brew install --cask swiftbar
# Symlink the plugin:
ln -sf "$PWD/menubar.sh" "$HOME/Library/Application Support/SwiftBar/Plugins/tokentracker.30s.sh"
# Tell menubar.sh where Token Tracker lives:
echo 'export TOKENTRACKER_DIR="'$PWD'"' >> ~/.zshrc
# Restart SwiftBar
```

You'll see `🟢 78` (or 🟡/🔴) in your menu bar. Click for a dropdown with today's score, tokens, cost, and quick actions.

## launchd daemons (optional, macOS)

`install.sh` installs four launchd agents (server + refresh + live monitor + nightly snapshot). **However**, macOS TCC blocks launchd from reading `~/Documents` and `~/Downloads`. If your clone lives in either, use `./start.sh` instead — it runs the same services from a shell context that has access.

If you clone elsewhere (`~/dev/`, `~/projects/`, `~/code/`), `install.sh` works:

```bash
./install.sh              # install + start
./install.sh --uninstall  # remove
```

## Local hostname (optional)

Want `http://dashboard.local:8787` instead of `127.0.0.1:8787`?

```bash
echo "127.0.0.1 dashboard.local" | sudo tee -a /etc/hosts
```

## Security and privacy

- **100% local.** No network calls except when you explicitly run `gmail_oauth_extract.py` (which talks directly to Google).
- The HTTP server only binds to `127.0.0.1`, not `0.0.0.0`. No remote access.
- `receipts.jsonl`, `history.jsonl`, `live.json`, `report.json`, and `reports/` are all **gitignored**. Nothing personal gets committed if you fork this.
- `credentials.json` and `token.json` (OAuth) are also gitignored. Don't commit them.

## Contributing

PRs welcome. The code is deliberately kept to Python stdlib + vanilla HTML/CSS/JS so anyone can read and modify it without installing a build system.

Rough roadmap:
- [ ] Configurable efficiency score weights
- [ ] Cross-machine sync (via git or a shared data dir)
- [ ] More auto-fix actions (not just "add to CLAUDE.md")
- [ ] Non-macOS menu bar widget (Windows tray, Linux tray)
- [ ] Team/multi-user support for shared token budgets

## License

MIT. See [LICENSE](LICENSE).

---

Built on a Sunday afternoon to answer "where is all my Claude Code money going?" Turns out: idle gaps and re-reading the same five files.
