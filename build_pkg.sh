#!/usr/bin/env bash
#
# build_pkg.sh — Build a macOS .pkg installer for Taurino
#
# Creates:  build/Taurino-1.0.0.pkg
#
# The .pkg installs:
#   /usr/local/lib/taurino/        — self-contained Python virtualenv
#   /usr/local/bin/taurino          — CLI wrapper
#   ~/Library/LaunchAgents/com.taurino.bridge.plist (optional, loaded on login)
#
# Usage:
#   chmod +x build_pkg.sh
#   ./build_pkg.sh
#
set -euo pipefail

VERSION="1.0.0"
IDENTIFIER="com.taurino.driver"
PKG_NAME="Taurino-${VERSION}.pkg"
CODE_SIGN_IDENTITY="${TAURINO_CODESIGN_IDENTITY:-}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
PAYLOAD="${BUILD_DIR}/payload"
SCRIPTS="${BUILD_DIR}/scripts"
ENTITLEMENTS="${BUILD_DIR}/taurino-hid.entitlements"

echo "==> Cleaning previous build..."
rm -rf "${BUILD_DIR}"
mkdir -p "${PAYLOAD}" "${SCRIPTS}"

cat > "${ENTITLEMENTS}" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>com.apple.developer.hid.virtual.device</key>
        <true/>
</dict>
</plist>
PLIST

# --------------------------------------------------------------------------
#  1) Create a self-contained virtualenv inside the payload
# --------------------------------------------------------------------------
echo "==> Creating virtualenv at payload/usr/local/lib/taurino..."
VENV_ROOT="${PAYLOAD}/usr/local/lib/taurino"
python3 -m venv --copies "${VENV_ROOT}"

# Install the taurino package + dependencies into the venv
"${VENV_ROOT}/bin/pip" install --quiet --upgrade pip
"${VENV_ROOT}/bin/pip" install --quiet "${SCRIPT_DIR}"

if [ -n "${CODE_SIGN_IDENTITY}" ]; then
    echo "==> Signing embedded Python for virtual HID entitlement..."
    codesign \
        --force \
        --sign "${CODE_SIGN_IDENTITY}" \
        --entitlements "${ENTITLEMENTS}" \
        --timestamp \
        --options runtime \
        "${VENV_ROOT}/bin/python3"
    codesign \
        --force \
        --sign "${CODE_SIGN_IDENTITY}" \
        --entitlements "${ENTITLEMENTS}" \
        --timestamp \
        --options runtime \
        "${VENV_ROOT}/bin/python"
else
    echo "==> No TAURINO_CODESIGN_IDENTITY set; system-wide virtual HID will"
    echo "    continue to fall back to UDP-only mode on macOS 13+."
fi

echo "==> Virtualenv ready ($(du -sh "${VENV_ROOT}" | cut -f1))"

# --------------------------------------------------------------------------
#  2) CLI wrapper at /usr/local/bin/taurino
# --------------------------------------------------------------------------
echo "==> Writing CLI wrapper..."
mkdir -p "${PAYLOAD}/usr/local/bin"
cat > "${PAYLOAD}/usr/local/bin/taurino" << 'WRAPPER'
#!/usr/bin/env bash
# Taurino — PDP Xbox Controller Driver for macOS
exec /usr/local/lib/taurino/bin/python -m taurino "$@"
WRAPPER
chmod 755 "${PAYLOAD}/usr/local/bin/taurino"

# --------------------------------------------------------------------------
#  3) LaunchAgent plist (bridge auto-start on login)
# --------------------------------------------------------------------------
echo "==> Writing LaunchAgent plist..."
AGENT_DIR="${PAYLOAD}/Library/LaunchAgents"
mkdir -p "${AGENT_DIR}"
cat > "${AGENT_DIR}/com.taurino.bridge.plist" << 'PLIST'
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

    <!-- Only start when a USB device from PDP (0x0E6F) is attached -->
    <key>LaunchEvents</key>
    <dict>
        <key>com.apple.iokit.matching</key>
        <dict>
            <key>com.taurino.pdp</key>
            <dict>
                <key>idVendor</key>
                <integer>3695</integer>
                <key>IOProviderClass</key>
                <string>IOUSBDevice</string>
            </dict>
        </dict>
    </dict>

    <key>RunAtLoad</key>
    <false/>

    <key>KeepAlive</key>
    <false/>

    <key>StandardOutPath</key>
    <string>/tmp/taurino-bridge.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/taurino-bridge.log</string>
</dict>
</plist>
PLIST

# --------------------------------------------------------------------------
#  4) Post-install script — load the LaunchAgent for the current user
# --------------------------------------------------------------------------
cat > "${SCRIPTS}/postinstall" << 'POST'
#!/usr/bin/env bash
set -e

AGENT="/Library/LaunchAgents/com.taurino.bridge.plist"

# Ensure libusb is available (Homebrew)
if ! [ -f /usr/local/lib/libusb-1.0.dylib ] && \
   ! [ -f /opt/homebrew/lib/libusb-1.0.dylib ]; then
    echo "[taurino] Installing libusb via Homebrew..."
    if command -v brew >/dev/null 2>&1; then
        brew install libusb 2>/dev/null || true
    else
        echo "[taurino] WARNING: libusb not found and Homebrew not available."
        echo "         Install libusb manually: brew install libusb"
    fi
fi

# Load the LaunchAgent for the installing user
CURRENT_USER="${USER:-$(stat -f '%Su' /dev/console)}"
if [ -f "${AGENT}" ]; then
    # Unload first if already present (upgrade scenario)
    launchctl bootout "gui/$(id -u "${CURRENT_USER}")" "${AGENT}" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u "${CURRENT_USER}")" "${AGENT}" 2>/dev/null || true
    echo "[taurino] LaunchAgent registered for ${CURRENT_USER}"
fi

echo ""
echo "======================================"
echo "  Taurino installed successfully!"
echo ""
echo "  CLI:     taurino --help"
echo "  GUI:     taurino gui"
echo "  Bridge:  taurino bridge"
echo "  Scan:    taurino scan"
echo ""
echo "  Note: system-wide virtual HID on macOS 13+ requires a signed build"
echo "  with the com.apple.developer.hid.virtual.device entitlement."
echo ""
echo "  The bridge auto-starts when a PDP"
echo "  controller is plugged in."
echo "======================================"
exit 0
POST
chmod 755 "${SCRIPTS}/postinstall"

# --------------------------------------------------------------------------
#  5) Pre-install script — unload old agent if upgrading
# --------------------------------------------------------------------------
cat > "${SCRIPTS}/preinstall" << 'PRE'
#!/usr/bin/env bash
set -e
AGENT="/Library/LaunchAgents/com.taurino.bridge.plist"
CURRENT_USER="${USER:-$(stat -f '%Su' /dev/console)}"
if [ -f "${AGENT}" ]; then
    launchctl bootout "gui/$(id -u "${CURRENT_USER}")" "${AGENT}" 2>/dev/null || true
fi
exit 0
PRE
chmod 755 "${SCRIPTS}/preinstall"

# --------------------------------------------------------------------------
#  6) Build the component .pkg
# --------------------------------------------------------------------------
echo "==> Building component package..."
COMPONENT="${BUILD_DIR}/taurino-component.pkg"
pkgbuild \
    --root "${PAYLOAD}" \
    --scripts "${SCRIPTS}" \
    --identifier "${IDENTIFIER}" \
    --version "${VERSION}" \
    --install-location "/" \
    "${COMPONENT}"

# --------------------------------------------------------------------------
#  7) Build the product .pkg (adds title, background, license to installer)
# --------------------------------------------------------------------------
echo "==> Writing distribution XML..."
DIST_XML="${BUILD_DIR}/distribution.xml"
cat > "${DIST_XML}" << DIST
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
    <title>Taurino — PDP Xbox Controller Driver</title>
    <organization>${IDENTIFIER}</organization>
    <domains enable_localSystem="true" enable_currentUserHome="true"/>
    <options customize="never" require-scripts="true"
             hostArchitectures="x86_64,arm64"/>

    <welcome mime-type="text/plain">
        <![CDATA[
Taurino ${VERSION}
PDP Xbox Controller Driver for macOS

This installer will set up:
  • The taurino CLI at /usr/local/bin/taurino
  • A Python virtualenv at /usr/local/lib/taurino/
  • A LaunchAgent that auto-starts the controller bridge
    when a PDP gamepad is connected

After installation, run:
  taurino gui        — visual controller tester
  taurino bridge     — forward to macOS apps
  taurino scan       — list connected controllers
        ]]>
    </welcome>

    <choices-outline>
        <line choice="default"/>
    </choices-outline>
    <choice id="default" title="Taurino Driver">
        <pkg-ref id="${IDENTIFIER}"/>
    </choice>
    <pkg-ref id="${IDENTIFIER}"
             version="${VERSION}"
             onConclusion="none">taurino-component.pkg</pkg-ref>
</installer-gui-script>
DIST

echo "==> Building product package..."
productbuild \
    --distribution "${DIST_XML}" \
    --package-path "${BUILD_DIR}" \
    "${BUILD_DIR}/${PKG_NAME}"

# --------------------------------------------------------------------------
#  Done
# --------------------------------------------------------------------------
echo ""
echo "================================================"
echo "  Built: build/${PKG_NAME}"
echo "  Size:  $(du -sh "${BUILD_DIR}/${PKG_NAME}" | cut -f1)"
echo ""
echo "  Install with:"
echo "    open build/${PKG_NAME}"
echo "  or:"
echo "    sudo installer -pkg build/${PKG_NAME} -target /"
echo "================================================"
