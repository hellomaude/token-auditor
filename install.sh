#!/bin/bash
# Token Auditor installer — sets up launchd daemons and SwiftBar plugin
set -e

# Resolve AUDITOR_DIR from the script location (works wherever you cloned the repo)
AUDITOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
SWIFTBAR_PLUGINS="$HOME/Library/Application Support/SwiftBar/Plugins"

# Handle uninstall flag first
if [ "$1" = "--uninstall" ]; then
  echo "Uninstalling Token Auditor..."
  for label in refresh live snapshot server; do
    plist="$LAUNCH_AGENTS/com.tokentracker.$label.plist"
    launchctl unload "$plist" 2>/dev/null || true
    rm -f "$plist"
    echo "  removed: $label"
  done
  rm -f "$SWIFTBAR_PLUGINS/tokentracker.30s.sh"
  echo "Uninstalled."
  exit 0
fi

echo "Token Auditor installer"
echo "======================="

# 1. Create logs directory
mkdir -p "$AUDITOR_DIR/logs"

# 2. Stop any existing instances
echo "→ Stopping any running daemons..."
for label in refresh live snapshot server; do
  launchctl unload "$LAUNCH_AGENTS/com.tokentracker.$label.plist" 2>/dev/null || true
done
kill $(lsof -ti :8787) 2>/dev/null || true

# 3. Render plists with the actual auditor directory and install
echo "→ Installing launchd daemons..."
mkdir -p "$LAUNCH_AGENTS"
for plist in "$AUDITOR_DIR"/launchd/*.plist; do
  name=$(basename "$plist")
  sed "s|__AUDITOR_DIR__|$AUDITOR_DIR|g" "$plist" > "$LAUNCH_AGENTS/$name"
  launchctl load "$LAUNCH_AGENTS/$name"
  echo "  loaded: $name"
done

# 4. Verify the server is responding
echo "→ Waiting for server..."
sleep 2
STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8787/dashboard.html 2>/dev/null || echo "000")
if [ "$STATUS" = "200" ]; then
  echo "  ✓ Server is up at http://127.0.0.1:8787/dashboard.html"
else
  echo "  ⚠ Server returned $STATUS — check $AUDITOR_DIR/logs/server.err"
fi

# 5. SwiftBar plugin
if [ -d "$SWIFTBAR_PLUGINS" ]; then
  echo "→ Installing SwiftBar plugin..."
  ln -sf "$AUDITOR_DIR/menubar.sh" "$SWIFTBAR_PLUGINS/tokentracker.30s.sh"
  echo "  ✓ Symlinked to $SWIFTBAR_PLUGINS/tokentracker.30s.sh"
  echo "  → Open or restart SwiftBar to load the plugin"
else
  echo "→ SwiftBar not installed at $SWIFTBAR_PLUGINS"
  echo "  Install with: brew install --cask swiftbar"
  echo "  Then re-run this script"
fi

echo ""
echo "Installation complete."
echo "Dashboard:  http://127.0.0.1:8787/dashboard.html"
echo "Logs:       $AUDITOR_DIR/logs/"
echo ""
echo "To uninstall: bash $AUDITOR_DIR/install.sh --uninstall"
