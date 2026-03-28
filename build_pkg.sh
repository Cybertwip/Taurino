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
#   ./build_pkg.sh --no-signature   # skip all code signing
#
set -euo pipefail

NO_SIGNATURE=false
for arg in "$@"; do
    case "$arg" in
        --no-signature) NO_SIGNATURE=true ;;
    esac
done

VERSION="1.0.0"
IDENTIFIER="com.taurino.driver"
PKG_NAME="Taurino-${VERSION}.pkg"

if [ "${NO_SIGNATURE}" = true ]; then
    CODE_SIGN_IDENTITY="NONE"
else
    CODE_SIGN_IDENTITY="${TAURINO_CODESIGN_IDENTITY:--}"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
PAYLOAD="${BUILD_DIR}/payload"
SCRIPTS="${BUILD_DIR}/scripts"
ENTITLEMENTS="${SCRIPT_DIR}/packaging/taurino-hid.entitlements"

resolve_sign_identity() {
    local requested="$1"
    local identities matches count requested_team

    if [ "${requested}" = "-" ]; then
        printf '%s\n' "-"
        return 0
    fi

    identities="$(security find-identity -v -p codesigning 2>/dev/null | sed -n 's/.*"\(.*\)"$/\1/p')"
    if [ -z "${identities}" ]; then
        echo "No macOS code-signing identities found in keychain." >&2
        return 1
    fi

    if printf '%s\n' "${identities}" | grep -Fx -- "${requested}" >/dev/null; then
        printf '%s\n' "${requested}"
        return 0
    fi

    matches="$(printf '%s\n' "${identities}" | grep -i -- "${requested}" || true)"
    count="$(printf '%s\n' "${matches}" | sed '/^$/d' | wc -l | tr -d ' ')"

    if [ "${count}" = "1" ]; then
        printf '%s\n' "${matches}" | sed -n '1p'
        return 0
    fi

    requested_team="$(printf '%s\n' "${requested}" | sed -n 's/.*(\([^)]*\)).*/\1/p')"
    if [ -n "${requested_team}" ]; then
        matches="$(printf '%s\n' "${identities}" | grep -F "(${requested_team})" || true)"
        count="$(printf '%s\n' "${matches}" | sed '/^$/d' | wc -l | tr -d ' ')"
        if [ "${count}" = "1" ]; then
            printf '%s\n' "${matches}" | sed -n '1p'
            return 0
        fi
    fi

    if [ "${count}" = "0" ]; then
        echo "Requested signing identity not found: ${requested}" >&2
    else
        echo "Requested signing identity is ambiguous: ${requested}" >&2
        printf '%s\n' "${matches}" >&2
    fi
    echo "Available identities:" >&2
    printf '%s\n' "${identities}" >&2
    return 1
}

if [ "${CODE_SIGN_IDENTITY}" = "NONE" ]; then
    echo "==> Signing disabled (--no-signature)"
else
    CODE_SIGN_IDENTITY="$(resolve_sign_identity "${CODE_SIGN_IDENTITY}")"
    if [ "${CODE_SIGN_IDENTITY}" != "-" ]; then
        echo "==> Using signing identity: ${CODE_SIGN_IDENTITY}"
    fi
fi

sign_target() {
    local target="$1"
    if [ ! -f "${target}" ]; then
        return 0
    fi
    if [ "${CODE_SIGN_IDENTITY}" = "NONE" ]; then
        echo "    Skipping signature for $(basename "${target}")"
        return 0
    fi
    if [ "${CODE_SIGN_IDENTITY}" = "-" ]; then
        codesign --force --sign - --entitlements "${ENTITLEMENTS}" "${target}"
    else
        codesign \
            --force \
            --sign "${CODE_SIGN_IDENTITY}" \
            --entitlements "${ENTITLEMENTS}" \
            "${target}"
    fi
}

echo "==> Cleaning previous build..."
rm -rf "${BUILD_DIR}"
mkdir -p "${PAYLOAD}" "${SCRIPTS}"

# --------------------------------------------------------------------------
#  1) Create a self-contained virtualenv inside the payload
# --------------------------------------------------------------------------
echo "==> Creating virtualenv at payload/usr/local/lib/taurino..."
VENV_ROOT="${PAYLOAD}/usr/local/lib/taurino"
python3 -m venv --copies "${VENV_ROOT}"

# Install the taurino package + dependencies into the venv
"${VENV_ROOT}/bin/pip" install --quiet --upgrade pip
"${VENV_ROOT}/bin/pip" install --quiet "${SCRIPT_DIR}"

echo "==> Building and signing native HID helper..."
HELPER_SRC="${SCRIPT_DIR}/helper"
HELPER_BIN="${VENV_ROOT}/bin/taurino-hid-helper"
clang -Wall -Wextra -O2 \
    -framework CoreFoundation -framework IOKit \
    -o "${HELPER_BIN}" "${HELPER_SRC}/taurino_hid_helper.c"
sign_target "${HELPER_BIN}"

if [ "${CODE_SIGN_IDENTITY}" = "NONE" ]; then
    echo "==> Skipping signature verification (--no-signature)"
    echo "    NOTE: HID virtual device creation will fail without a signed helper."
    echo "    You can sign later with: codesign --force --sign <identity> --entitlements packaging/taurino-hid.entitlements ${HELPER_BIN}"
else
    # Verify the signature and entitlement were applied.
    echo "==> Verifying helper binary..."
    if codesign --verify --verbose "${HELPER_BIN}" 2>/dev/null; then
        echo "    Code signature: valid"
    else
        echo "    Code signature: INVALID — HID device creation will fail at runtime."
    fi
    if codesign -d --entitlements - "${HELPER_BIN}" 2>&1 | grep -q "com.apple.developer.hid.virtual.device"; then
        echo "    HID entitlement: present"
    else
        echo "    HID entitlement: MISSING — virtual gamepad will not be created."
        echo "    Set TAURINO_CODESIGN_IDENTITY to a valid Developer ID."
    fi

    if [ "${CODE_SIGN_IDENTITY}" = "-" ]; then
        echo "==> Using ad-hoc signing for the HID helper. If macOS rejects the"
        echo "    virtual HID device, rebuild with TAURINO_CODESIGN_IDENTITY set"
        echo "    to an Apple signing identity."
    else
        echo "==> HID helper binary signed with developer identity."
        echo "    Only the helper needs the HID entitlement — not Python."
    fi
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
echo "  Doctor:  taurino doctor"
echo "  Scan:    taurino scan"
echo ""
echo "  The native HID helper binary handles"
echo "  virtual gamepad creation (no Python"
echo "  code-signing required)."
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
  • A native HID helper binary (codesigned for virtual gamepad)
  • A LaunchAgent that auto-starts the controller bridge
    when a PDP gamepad is connected

After installation, run:
  taurino gui        — visual controller tester
  taurino bridge     — forward to macOS apps
  taurino scan       — list connected controllers
  taurino doctor     — verify helper binary status
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
if [ "${CODE_SIGN_IDENTITY}" != "NONE" ] && [ "${CODE_SIGN_IDENTITY}" != "-" ]; then
    # Derive the "Developer ID Installer" identity from the application one.
    INSTALLER_IDENTITY="$(echo "${CODE_SIGN_IDENTITY}" | sed 's/Developer ID Application/Developer ID Installer/')"
    # If the derived name exists in the keychain, sign; otherwise skip.
    if security find-identity -v -p basic 2>/dev/null | grep -qF "${INSTALLER_IDENTITY}"; then
        echo "    Signing .pkg with: ${INSTALLER_IDENTITY}"
        productbuild \
            --distribution "${DIST_XML}" \
            --package-path "${BUILD_DIR}" \
            --sign "${INSTALLER_IDENTITY}" \
            "${BUILD_DIR}/${PKG_NAME}"
    else
        echo "    Installer identity '${INSTALLER_IDENTITY}' not found, building unsigned .pkg"
        productbuild \
            --distribution "${DIST_XML}" \
            --package-path "${BUILD_DIR}" \
            "${BUILD_DIR}/${PKG_NAME}"
    fi
else
    productbuild \
        --distribution "${DIST_XML}" \
        --package-path "${BUILD_DIR}" \
        "${BUILD_DIR}/${PKG_NAME}"
fi

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
