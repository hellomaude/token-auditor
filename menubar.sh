#!/bin/bash
# Token Tracker — SwiftBar plugin
# When installed, symlink to ~/Library/Application Support/SwiftBar/Plugins/tokentracker.30s.sh
#
# Since SwiftBar symlinks this file, BASH_SOURCE won't resolve to your repo.
# Set TOKENTRACKER_DIR in your shell profile, OR edit the default path below.

AUDITOR_DIR="${TOKENTRACKER_DIR:-$HOME/token-tracker}"
HISTORY="$AUDITOR_DIR/history.jsonl"
LIVE="$AUDITOR_DIR/live.json"
DASHBOARD_URL="http://127.0.0.1:8787/dashboard.html"

# Read today's score from history.jsonl (last line)
if [ -f "$HISTORY" ]; then
  LAST_LINE=$(tail -n 1 "$HISTORY" 2>/dev/null)
  SCORE=$(echo "$LAST_LINE" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('score', 0))" 2>/dev/null || echo "0")
  TOKENS=$(echo "$LAST_LINE" | python3 -c "import sys,json; print(f\"{json.loads(sys.stdin.read()).get('tokens', 0):,}\")" 2>/dev/null || echo "0")
  COST=$(echo "$LAST_LINE" | python3 -c "import sys,json; print(f\"\${json.loads(sys.stdin.read()).get('cost', 0):.2f}\")" 2>/dev/null || echo "\$0.00")
else
  SCORE=0
  TOKENS="—"
  COST="\$0.00"
fi

# Pick emoji based on score
if [ "$SCORE" -ge 75 ]; then
  EMOJI="🟢"
elif [ "$SCORE" -ge 50 ]; then
  EMOJI="🟡"
else
  EMOJI="🔴"
fi

# Live session count
LIVE_COUNT=0
if [ -f "$LIVE" ]; then
  LIVE_COUNT=$(python3 -c "import json; print(len(json.load(open('$LIVE')).get('active_sessions', [])))" 2>/dev/null || echo "0")
fi

LIVE_INDICATOR=""
if [ "$LIVE_COUNT" -gt 0 ]; then
  LIVE_INDICATOR=" • ${LIVE_COUNT} live"
fi

# Top line — what shows in the menu bar
echo "${EMOJI} ${SCORE}${LIVE_INDICATOR}"

# Dropdown
echo "---"
echo "Token Auditor | size=14"
echo "Score: ${SCORE}/100 | size=12"
echo "Tokens today: ${TOKENS} | size=12"
echo "Cost today: ${COST} | size=12"

if [ "$LIVE_COUNT" -gt 0 ]; then
  echo "---"
  echo "🔴 ${LIVE_COUNT} active session(s) | size=12"
fi

echo "---"
echo "Open Dashboard | href=${DASHBOARD_URL}"
echo "Refresh Now | bash=python3 param1=${AUDITOR_DIR}/analyze.py terminal=false"
echo "Take Snapshot | bash=python3 param1=${AUDITOR_DIR}/snapshot.py terminal=false"
echo "---"
echo "Open Auditor folder | bash=open param1=${AUDITOR_DIR} terminal=false"
