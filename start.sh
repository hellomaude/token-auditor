#!/bin/bash
# Token Tracker — manual startup script
#
# Starts background processes (server + live monitor loop + analyze refresh loop).
# Use this instead of launchd on macOS if your install lives under ~/Documents/
# or ~/Downloads/ — those paths are blocked for launchd by TCC sandbox.
#
# Usage:
#   ./start.sh         — start all services
#   ./start.sh stop    — stop all services
#   ./start.sh status  — show what's running

set -e

# Resolve AUDITOR_DIR from the script location (portable)
AUDITOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="$AUDITOR_DIR/.pids"
LOG_DIR="$AUDITOR_DIR/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

stop_all() {
  for name in server live refresh menubar; do
    pidfile="$PID_DIR/$name.pid"
    if [ -f "$pidfile" ]; then
      pid=$(cat "$pidfile")
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null
        echo "  stopped: $name (pid $pid)"
      fi
      rm -f "$pidfile"
    fi
  done
  # Belt and suspenders — kill anything on 8787
  kill $(lsof -ti :8787) 2>/dev/null || true
}

status() {
  echo "Token Auditor status"
  echo "===================="
  for name in server live refresh menubar; do
    pidfile="$PID_DIR/$name.pid"
    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
      echo "  $name: running (pid $(cat "$pidfile"))"
    else
      echo "  $name: stopped"
    fi
  done
  echo ""
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8787/dashboard.html 2>/dev/null || echo "000")
  echo "  dashboard HTTP: $STATUS"
}

case "${1:-start}" in
  stop)
    echo "Stopping Token Auditor..."
    stop_all
    ;;
  status)
    status
    ;;
  start|"")
    echo "Starting Token Auditor..."
    stop_all

    cd "$AUDITOR_DIR"

    # 1. HTTP server (persistent)
    nohup python3 server.py > "$LOG_DIR/server.log" 2>&1 &
    echo $! > "$PID_DIR/server.pid"
    echo "  started: server (pid $!)"

    # 2. Live monitor loop (every 10 seconds)
    nohup bash -c "while true; do python3 live_monitor.py >> '$LOG_DIR/live.log' 2>&1; sleep 10; done" > /dev/null 2>&1 &
    echo $! > "$PID_DIR/live.pid"
    echo "  started: live_monitor (pid $!)"

    # 3. Analyze refresh loop (every 5 minutes)
    nohup bash -c "while true; do python3 analyze.py >> '$LOG_DIR/refresh.log' 2>&1; sleep 300; done" > /dev/null 2>&1 &
    echo $! > "$PID_DIR/refresh.pid"
    echo "  started: analyze refresh (pid $!)"

    # 4. Native menu bar app (uses /usr/bin/swift, no install needed)
    if [ -x /usr/bin/swift ] && [ -f "$AUDITOR_DIR/menubar.swift" ]; then
      nohup /usr/bin/swift "$AUDITOR_DIR/menubar.swift" > "$LOG_DIR/menubar.log" 2>&1 &
      echo $! > "$PID_DIR/menubar.pid"
      echo "  started: menubar (pid $!) — look for 🟢 in your menu bar"
    else
      echo "  skipped: menubar (need /usr/bin/swift — install Xcode Command Line Tools)"
    fi

    sleep 1
    echo ""
    status
    echo ""
    echo "Dashboard: http://127.0.0.1:8787/dashboard.html"
    echo "Stop with: $0 stop"
    ;;
  *)
    echo "Usage: $0 {start|stop|status}"
    exit 1
    ;;
esac
