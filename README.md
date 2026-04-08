<div align="center">
  <img src="token-logo-light.png#gh-light-mode-only" alt="Token Auditor" width="440">
  <img src="token-logo-dark.png#gh-dark-mode-only" alt="Token Auditor" width="440">
</div>

# Token Auditor

A local dashboard for tracking what your AI coding tools actually cost you.

It reads Claude Code session logs, Codex CLI rollouts, Cursor exports, and (optionally) billing emails from Gmail. Then it shows you the damage in one place: how many tokens you burned today, which projects ate the most, where you're wasting cache, and what your monthly spend looks like across every AI vendor you use.

Everything runs on your machine. No API keys to Anthropic or OpenAI, no telemetry, no cloud sync. The only network call it makes is to Gmail's API (with your OAuth token) when you ask it to scan receipts — and even that uses regex parsing, not an LLM.

## What it tracks

| Source | What it reads | Where it lives |
|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` | session token usage, turn counts, cache stats |
| Codex CLI | `~/.codex/sessions/**/rollout-*.jsonl` | rate limits (5h + 7d windows), cost, sessions |
| Cursor | manual JSON export | composer message counts |
| Gmail | invoice emails via OAuth | receipts from Anthropic, OpenAI, Cursor, Cloudflare, Vercel, GitHub, Linear, Supabase, Apify, ElevenLabs, etc. |

## Screenshots

### Dashboard

The main page. Today's usage at the top, totals across all time, then waste analysis and concrete suggestions for what to fix.

![Dashboard](screenshots/dashboard.png)

### Cost breakdown

Receipts grouped by vendor and category. Hero numbers up top show monthly spend, average receipt size, and total receipts captured. Pulled from Gmail or added manually.

![Costs](screenshots/costs.png)

### Native menu bar widget

A frosted-glass macOS panel that lives in the menu bar. Shows your efficiency score, tokens today, cost today, Codex rate limits, and any active Claude Code sessions with live burn rate. Written in Swift with NSVisualEffectView, SF Rounded fonts, settings to hide sections you don't care about.

(Run `swift menubar.swift` to launch it.)

## Install

```bash
git clone https://github.com/hellomaude/token-auditor.git
cd token-auditor
pip install -r requirements.txt
./start.sh
```

That starts the analyzers and serves the dashboard at `http://localhost:8787/dashboard.html`.

To add receipt scanning:

1. Create a Google Cloud project, enable the Gmail API, download `credentials.json` to this directory
2. `pip install google-auth google-auth-oauthlib google-api-python-client`
3. `python3 gmail_oauth_extract.py` — first run opens a browser for OAuth, saves a token, then scans the last 4 months of invoices

To run the menu bar widget (macOS only):

```bash
swift menubar.swift
```

## What's in here

| File | Does what |
|---|---|
| `analyze.py` | Parses Claude Code JSONL session logs, builds `report.json` |
| `codex_analyze.py` | Parses Codex rollouts, extracts rate limits and costs |
| `cursor_analyze.py` | Reads Cursor composer exports |
| `live_monitor.py` | Background process that writes `live.json` with active sessions |
| `gmail_oauth_extract.py` | Direct Gmail API receipt scanner (no LLM, no token cost) |
| `receipts.py` | Receipt store and vendor → category mapper |
| `tax_report.py` | Generates a CSV grouped by category for tax filing |
| `snapshot.py` | Computes the efficiency score |
| `server.py` | Local HTTP server on `:8787` |
| `dashboard.html` | The main page |
| `costs.html` | Receipts breakdown page |
| `widget.html` | Web version of the menu bar widget |
| `menubar.swift` | Native macOS menu bar app + floating widget |
| `mcp_server.py` | Optional MCP bridge so you can ask Claude about your usage |

## How it costs you nothing

Token Auditor itself doesn't talk to any LLM. The whole pipeline is local file parsing plus a Gmail OAuth call. The cost figures it shows are reconstructed from the JSONL session logs your tools write to disk anyway — no API hits, no extra spend.

The only "AI" it touches is the optional MCP server, which exposes the local data to Claude when you want to ask questions like "what did I spend last week" without leaving your editor. That bit obviously costs whatever the conversation costs on your end.

## Privacy

The dashboard and the menu bar widget never leave your machine. `receipts.jsonl`, `report.json`, `live.json`, and the `receipts/` PDF folder are all in `.gitignore` and stay on local disk. Gmail credentials live in `credentials.json` and `token.json`, also gitignored.

## License

MIT.
