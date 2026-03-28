"""Virtual HID gamepad bridge for macOS.

Architecture:

    Python (taurino bridge)
        ──[Unix socket]──▶  taurino-hid-helper  (native C binary, codesigned)
                               ──[IOKit]──▶  macOS HID subsystem

The ``taurino-hid-helper`` binary handles all IOKit/HID calls and must be
codesigned with the ``com.apple.developer.hid.virtual.device`` entitlement.
Only the small helper needs to be signed — not the entire Python runtime.

Fallback: a localhost UDP broadcast that any custom consumer can receive.
"""

import os
import shutil
import socket
import struct
import subprocess
import time

from .protocol import GAMEPAD_REPORT_SIZE
from .state import ControllerState


# ---------------------------------------------------------------------------
#  Paths
# ---------------------------------------------------------------------------

HELPER_BINARY_NAME = "taurino-hid-helper"
HELPER_SOCKET_PATH = "/tmp/taurino-hid.sock"
INSTALL_ROOT = "/usr/local/lib/taurino"
INSTALL_HELPER = f"{INSTALL_ROOT}/bin/{HELPER_BINARY_NAME}"
INSTALL_LAUNCHER = "/usr/local/bin/taurino"
LAUNCH_AGENT_PATH = "/Library/LaunchAgents/com.taurino.bridge.plist"

HID_VIRTUAL_DEVICE_ENTITLEMENT = "com.apple.developer.hid.virtual.device"


# ---------------------------------------------------------------------------
#  Helper discovery & diagnostics
# ---------------------------------------------------------------------------

def _find_helper() -> str | None:
    """Locate the taurino-hid-helper binary."""
    candidates = [
        INSTALL_HELPER,
        os.path.join(os.path.dirname(__file__), "..", "helper", HELPER_BINARY_NAME),
    ]
    for path in candidates:
        resolved = os.path.realpath(path)
        if os.path.isfile(resolved) and os.access(resolved, os.X_OK):
            return resolved
    return shutil.which(HELPER_BINARY_NAME)


def _helper_has_entitlement(path: str) -> bool | None:
    """Check whether the helper binary has the HID virtual-device entitlement."""
    try:
        result = subprocess.run(
            ["codesign", "-d", "--entitlements", "-", path],
            capture_output=True, check=False, text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return False
    text = (result.stdout or "") + (result.stderr or "")
    return HID_VIRTUAL_DEVICE_ENTITLEMENT in text


def get_bridge_runtime_status() -> dict[str, object]:
    helper_path = _find_helper()
    helper_exists = helper_path is not None
    return {
        "helper_path": helper_path or INSTALL_HELPER,
        "helper_exists": helper_exists,
        "helper_codesigned": (
            _helper_has_entitlement(helper_path) if helper_exists else None
        ),
        "helper_socket_exists": os.path.exists(HELPER_SOCKET_PATH),
        "installed_launcher_exists": os.path.exists(INSTALL_LAUNCHER),
        "launch_agent_exists": os.path.exists(LAUNCH_AGENT_PATH),
    }


def format_bridge_doctor_report() -> str:
    status = get_bridge_runtime_status()

    def fmt(val: bool | None) -> str:
        if val is True:
            return "present"
        if val is False:
            return "missing"
        return "unknown"

    lines = [
        "Taurino bridge doctor",
        "",
        f"Helper binary: {fmt(status['helper_exists'])} ({status['helper_path']})",
        f"Helper HID entitlement: {fmt(status['helper_codesigned'])}",
        f"Helper socket: {'active' if status['helper_socket_exists'] else 'inactive'} ({HELPER_SOCKET_PATH})",
        f"Launcher: {fmt(status['installed_launcher_exists'])} ({INSTALL_LAUNCHER})",
        f"LaunchAgent: {fmt(status['launch_agent_exists'])} ({LAUNCH_AGENT_PATH})",
        "",
    ]

    if not status["helper_exists"]:
        lines.append(
            "Helper binary not found. Build it with: cd helper && make && make sign"
        )
    elif status["helper_codesigned"] is not True:
        lines.append(
            "Helper binary found but not codesigned with the HID entitlement. "
            "Sign it with: cd helper && make sign IDENTITY=\"Developer ID Application: ...\""
        )
    elif status["helper_socket_exists"]:
        lines.append("Helper is running and ready for connections.")
    else:
        lines.append(
            "Helper is properly signed. Start the bridge with: taurino bridge"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
#  Virtual HID Gamepad — talks to the native helper via Unix socket
# ---------------------------------------------------------------------------

class HIDHelperError(Exception):
    pass


class VirtualHIDGamepad:
    """Creates a virtual HID gamepad by delegating to the codesigned
    ``taurino-hid-helper`` process via a Unix domain socket.

    The helper handles IOKit calls, so the Python runtime does not need
    any special entitlements or code-signing.
    """

    def __init__(self):
        self._sock: socket.socket | None = None
        self._helper_proc: subprocess.Popen | None = None

    def open(self):
        if not self._try_connect():
            self._start_helper()
            if not self._try_connect():
                raise HIDHelperError(
                    "Cannot connect to taurino-hid-helper. "
                    "Build it with: cd helper && make && make sign")

        # The helper sends a 1-byte status after the device is created.
        data = self._sock.recv(1)
        if not data or data[0] != 0x01:
            self._sock.close()
            self._sock = None
            raise HIDHelperError(
                "taurino-hid-helper failed to create virtual HID device. "
                "Ensure the binary is codesigned with the "
                "com.apple.developer.hid.virtual.device entitlement.")

        print("[taurino] Virtual HID gamepad created via native helper — "
              "apps should see 'Taurino Virtual Gamepad'")

    def _try_connect(self) -> bool:
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(HELPER_SOCKET_PATH)
            self._sock = sock
            return True
        except (OSError, ConnectionRefusedError):
            return False

    def _start_helper(self):
        helper_path = _find_helper()
        if not helper_path:
            return
        try:
            self._helper_proc = subprocess.Popen(
                [helper_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            # Give the helper time to create the socket.
            time.sleep(0.3)
        except OSError:
            self._helper_proc = None

    def send_report(self, state: ControllerState) -> bool:
        if not self._sock:
            return False
        report = state.pack_hid_report()
        try:
            self._sock.sendall(report)
            return True
        except OSError:
            return False

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._helper_proc:
            try:
                self._helper_proc.terminate()
                self._helper_proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
            self._helper_proc = None


# ---------------------------------------------------------------------------
#  UDP localhost broadcast (always works, no entitlements needed)
# ---------------------------------------------------------------------------

BROADCAST_PORT = 54360
MAGIC = b"TAUR"


class UDPBroadcaster:
    """Sends controller state as UDP packets on localhost."""

    def __init__(self, port: int = BROADCAST_PORT):
        self._port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, state: ControllerState):
        payload = MAGIC + state.pack_hid_report()
        try:
            self._sock.sendto(payload, ("127.0.0.1", self._port))
        except OSError:
            pass

    def close(self):
        self._sock.close()


class UDPReceiver:
    """Receives controller state from a UDPBroadcaster."""

    def __init__(self, port: int = BROADCAST_PORT):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", port))
        self._sock.settimeout(0.1)

    def receive(self) -> ControllerState | None:
        try:
            data, _ = self._sock.recvfrom(256)
        except (socket.timeout, OSError):
            return None
        if len(data) < len(MAGIC) + GAMEPAD_REPORT_SIZE:
            return None
        if data[:len(MAGIC)] != MAGIC:
            return None
        return _unpack_report(data[len(MAGIC):])

    def close(self):
        self._sock.close()


def _unpack_report(report: bytes) -> ControllerState:
    buttons, lt, rt, lx, ly, rx, ry = struct.unpack_from("<HHHhhhh", report)
    return ControllerState(
        a=bool(buttons & 0x0001),
        b=bool(buttons & 0x0002),
        x=bool(buttons & 0x0004),
        y=bool(buttons & 0x0008),
        left_bumper=bool(buttons & 0x0010),
        right_bumper=bool(buttons & 0x0020),
        view=bool(buttons & 0x0040),
        menu=bool(buttons & 0x0080),
        left_stick_press=bool(buttons & 0x0100),
        right_stick_press=bool(buttons & 0x0200),
        dpad_up=bool(buttons & 0x0400),
        dpad_down=bool(buttons & 0x0800),
        dpad_left=bool(buttons & 0x1000),
        dpad_right=bool(buttons & 0x2000),
        sync=bool(buttons & 0x4000),
        guide=bool(buttons & 0x8000),
        left_trigger=lt,
        right_trigger=rt,
        left_stick_x=lx,
        left_stick_y=ly,
        right_stick_x=rx,
        right_stick_y=ry,
    )


# ---------------------------------------------------------------------------
#  Combined bridge: physical controller → virtual outputs
# ---------------------------------------------------------------------------

class ControllerBridge:
    """Reads from the physical USB controller and forwards state to
    a virtual HID device and/or UDP broadcast."""

    def __init__(self, controller, *, use_hid: bool = True,
                 use_udp: bool = True):
        self._ctrl = controller
        self._use_hid = use_hid
        self._use_udp = use_udp
        self._hid: VirtualHIDGamepad | None = None
        self._udp: UDPBroadcaster | None = None
        self._running = False

    def start(self):
        """Blocking — runs until Ctrl-C."""
        if self._use_hid:
            try:
                self._hid = VirtualHIDGamepad()
                self._hid.open()
            except HIDHelperError as e:
                print(f"[taurino] HID bridge unavailable: {e}")
                if self._use_udp:
                    print("[taurino] Continuing in UDP-only mode. "
                          "Build and codesign the helper for system-wide HID.")
                self._hid = None

        if self._use_udp:
            self._udp = UDPBroadcaster()
            print(f"[taurino] UDP broadcast on localhost:{BROADCAST_PORT}")

        if not self._hid and not self._udp:
            raise RuntimeError("No output available (HID failed, UDP disabled)")

        self._running = True
        print("[taurino] Bridge running — Ctrl-C to stop\n")
        prev: tuple | None = None

        try:
            while self._running:
                state = self._ctrl.poll(timeout_ms=8)
                if state is None:
                    continue
                snap = state.as_tuple()
                if snap == prev:
                    continue
                prev = snap
                if self._hid:
                    self._hid.send_report(state)
                if self._udp:
                    self._udp.send(state)
        except KeyboardInterrupt:
            print("\n[taurino] Bridge stopped")
        finally:
            self.stop()

    def stop(self):
        self._running = False
        if self._hid:
            self._hid.close()
            self._hid = None
        if self._udp:
            self._udp.close()
            self._udp = None
