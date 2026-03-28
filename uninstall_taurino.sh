#!/usr/bin/env bash
#
# uninstall_taurino.sh — Remove Taurino from macOS
#
set -euo pipefail

echo "Uninstalling Taurino..."

# Stop the bridge LaunchAgent
AGENT="/Library/LaunchAgents/com.taurino.bridge.plist"
if [ -f "${AGENT}" ]; then
    launchctl bootout "gui/$(id -u)" "${AGENT}" 2>/dev/null || true
    sudo rm -f "${AGENT}"
    echo "  Removed LaunchAgent"
fi

# Remove virtualenv
if [ -d /usr/local/lib/taurino ]; then
    sudo rm -rf /usr/local/lib/taurino
    echo "  Removed /usr/local/lib/taurino/"
fi

# Remove CLI wrapper
if [ -f /usr/local/bin/taurino ]; then
    sudo rm -f /usr/local/bin/taurino
    echo "  Removed /usr/local/bin/taurino"
fi

# Forget the package receipt
pkgutil --pkgs 2>/dev/null | grep -q "com.taurino.driver" && \
    sudo pkgutil --forget com.taurino.driver 2>/dev/null || true

echo ""
echo "Taurino has been uninstalled."
