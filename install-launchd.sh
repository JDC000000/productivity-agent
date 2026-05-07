#!/bin/bash
# Install (or reinstall) the launchd agent so the bot auto-starts at login
# and restarts on crash. Idempotent — safe to run multiple times.
#
# Usage:
#   chmod +x install-launchd.sh
#   ./install-launchd.sh
#
# To uninstall:
#   launchctl unload ~/Library/LaunchAgents/com.jon.productivity-agent.plist
#   rm ~/Library/LaunchAgents/com.jon.productivity-agent.plist
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$HERE/launchd/com.jon.productivity-agent.plist.example"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET="$TARGET_DIR/com.jon.productivity-agent.plist"

if [ ! -f "$TEMPLATE" ]; then
    echo "ERROR: template not found at $TEMPLATE"
    exit 1
fi

if [ ! -x "$HERE/run.sh" ]; then
    echo "Making run.sh executable..."
    chmod +x "$HERE/run.sh"
fi

mkdir -p "$TARGET_DIR"
mkdir -p "$HOME/.productivity-agent/logs"

# Substitute placeholders
sed \
    -e "s|PRODUCTIVITY_AGENT_DIR|$HERE|g" \
    -e "s|HOME_DIR|$HOME|g" \
    "$TEMPLATE" > "$TARGET"

echo "Wrote: $TARGET"

# Use modern launchctl bootstrap/bootout (works on macOS 11+).
# Old `launchctl load/unload` is deprecated and prints misleading errors.
UID_NUM="$(id -u)"
DOMAIN="gui/$UID_NUM"
SERVICE="$DOMAIN/com.jon.productivity-agent"

# Bootout existing service if present (idempotent — ignore failure)
if launchctl print "$SERVICE" >/dev/null 2>&1; then
    echo "Booting out existing agent..."
    launchctl bootout "$SERVICE" 2>/dev/null || true
fi

# Bootstrap the new service. macOS launchd sometimes returns EIO (exit 5) if
# bootstrap runs too soon after bootout — retry a couple of times with a small
# backoff before giving up.
echo "Bootstrapping agent..."
for attempt in 1 2 3; do
    if launchctl bootstrap "$DOMAIN" "$TARGET"; then
        break
    fi
    if [ "$attempt" -eq 3 ]; then
        echo "Bootstrap failed after 3 attempts."
        exit 1
    fi
    echo "Bootstrap attempt $attempt failed (likely transient post-bootout EIO); retrying in 2s..."
    sleep 2
done

echo
echo "[OK] launchd agent installed."
echo
echo "Verify it's running:"
echo "  launchctl list | grep productivity-agent"
echo
echo "Tail logs:"
echo "  tail -f $HOME/.productivity-agent/logs/launchd.stderr.log"
echo "  tail -f $HOME/.productivity-agent/logs/agent.log"
echo
echo "Stop the agent:"
echo "  launchctl unload $TARGET"
echo
echo "The bot will now auto-start every time you log in to your Mac."
