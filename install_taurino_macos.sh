#!/usr/bin/env bash

set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This installer only supports macOS." >&2
    exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run with sudo: sudo ./install_taurino_macos.sh" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_ROOT="/usr/local/lib/taurino"
BIN_DIR="/usr/local/bin"
LAUNCH_AGENT="/Library/LaunchAgents/com.taurino.bridge.plist"
ENTITLEMENTS="${SCRIPT_DIR}/packaging/taurino-hid.entitlements"
CODE_SIGN_IDENTITY="${TAURINO_CODESIGN_IDENTITY:--}"
CONSOLE_USER="${SUDO_USER:-$(stat -f '%Su' /dev/console)}"

sign_target() {
    local target="$1"
    if [[ ! -f "${target}" ]]; then
        return 0
    fi

    if [[ "${CODE_SIGN_IDENTITY}" == "-" ]]; then
        codesign --force --sign - --entitlements "${ENTITLEMENTS}" "${target}"
    else
        codesign \
            --force \
            --sign "${CODE_SIGN_IDENTITY}" \
            --entitlements "${ENTITLEMENTS}" \
            --timestamp \
            --options runtime \
            "${target}"
    fi
}

echo "==> Installing Taurino into ${INSTALL_ROOT}"
rm -rf "${INSTALL_ROOT}"
python3 -m venv --copies "${INSTALL_ROOT}"
"${INSTALL_ROOT}/bin/pip" install --quiet --upgrade pip
"${INSTALL_ROOT}/bin/pip" install --quiet "${SCRIPT_DIR}"

echo "==> Signing Taurino runtime"
sign_target "${INSTALL_ROOT}/bin/python"
if [[ -f "${INSTALL_ROOT}/bin/python3" ]]; then
    sign_target "${INSTALL_ROOT}/bin/python3"
fi

echo "==> Installing launcher"
mkdir -p "${BIN_DIR}"
cat > "${BIN_DIR}/taurino" << 'WRAPPER'
#!/usr/bin/env bash
exec /usr/local/lib/taurino/bin/python -m taurino "$@"
WRAPPER
chmod 755 "${BIN_DIR}/taurino"

echo "==> Installing LaunchAgent"
mkdir -p "/Library/LaunchAgents"
cat > "${LAUNCH_AGENT}" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.taurino.bridge</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/lib/taurino/bin/python</string>
        <string>-m</string>
        <string>taurino</string>
        <string>bridge</string>
    </array>
    <key>RunAtLoad</key>
    <false/>
    <key>KeepAlive</key>
    <false/>
    <key>LaunchEvents</key>
    <dict>
        <key>com.apple.iokit.matching</key>
        <dict>
            <key>com.taurino.pdp</key>
            <dict>
                <key>IOProviderClass</key>
                <string>IOUSBDevice</string>
                <key>idVendor</key>
                <integer>3695</integer>
            </dict>
        </dict>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/taurino-bridge.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/taurino-bridge.log</string>
</dict>
</plist>
PLIST
chmod 644 "${LAUNCH_AGENT}"

echo "==> Verifying HID entitlement on installed runtime"
if ! codesign -d --entitlements :- "${INSTALL_ROOT}/bin/python" 2>&1 | grep -q "com.apple.developer.hid.virtual.device"; then
    echo "Installed runtime does not expose com.apple.developer.hid.virtual.device." >&2
    echo "If ad-hoc signing is insufficient on this machine, rerun with TAURINO_CODESIGN_IDENTITY set to an Apple signing identity." >&2
fi

echo "==> Bootstrapping LaunchAgent for ${CONSOLE_USER}"
launchctl bootout "gui/$(id -u "${CONSOLE_USER}")" "${LAUNCH_AGENT}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u "${CONSOLE_USER}")" "${LAUNCH_AGENT}" 2>/dev/null || true

echo
echo "Taurino installed."
echo "Use /usr/local/bin/taurino doctor to verify the runtime entitlement."
echo "Use /usr/local/bin/taurino bridge to run the system-wide HID bridge."