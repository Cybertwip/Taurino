"""Virtual HID gamepad bridge for macOS.

Attempts to create a system-level virtual HID gamepad via IOKit's
IOHIDUserDevice API so that *all* macOS applications (including games)
see the PDP controller as a standard gamepad.

Fallback: a localhost UDP broadcast that any custom app can consume.

NOTE — On macOS 13 (Ventura) and later, IOHIDUserDeviceCreate requires
the calling binary to be signed with the
    com.apple.developer.hid.virtual.device
entitlement.  If creation fails the bridge falls back to UDP only and
prints instructions.
"""

import ctypes
import ctypes.util
import socket
import struct
import threading
import time

from .protocol import GAMEPAD_HID_DESCRIPTOR, GAMEPAD_REPORT_SIZE
from .state import ControllerState


# ---------------------------------------------------------------------------
#  IOKit Virtual HID Device
# ---------------------------------------------------------------------------

class IOKitHIDError(Exception):
    pass


class VirtualHIDGamepad:
    """Creates a virtual HID gamepad visible to the entire OS."""

    def __init__(self):
        self._device = None
        self._cf = None
        self._iokit = None
        self._rl_thread: threading.Thread | None = None
        self._rl_ref = None
        self._load()

    # -- Framework loading ----------------------------------------------------

    def _load(self):
        try:
            self._cf = ctypes.CDLL(
                "/System/Library/Frameworks/"
                "CoreFoundation.framework/CoreFoundation")
            self._iokit = ctypes.CDLL(
                "/System/Library/Frameworks/IOKit.framework/IOKit")
        except OSError as e:
            raise IOKitHIDError(f"Cannot load macOS frameworks: {e}")

        cf = self._cf
        iokit = self._iokit

        # ---- CFDictionary callback structs ----------------------------------

        class _KeyCB(ctypes.Structure):
            _fields_ = [("version", ctypes.c_long),
                        ("retain", ctypes.c_void_p),
                        ("release", ctypes.c_void_p),
                        ("copyDescription", ctypes.c_void_p),
                        ("equal", ctypes.c_void_p),
                        ("hash", ctypes.c_void_p)]

        class _ValCB(ctypes.Structure):
            _fields_ = [("version", ctypes.c_long),
                        ("retain", ctypes.c_void_p),
                        ("release", ctypes.c_void_p),
                        ("copyDescription", ctypes.c_void_p),
                        ("equal", ctypes.c_void_p)]

        self._key_cbs = _KeyCB.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
        self._val_cbs = _ValCB.in_dll(cf, "kCFTypeDictionaryValueCallBacks")

        # ---- Function signatures --------------------------------------------

        cf.CFDictionaryCreateMutable.restype = ctypes.c_void_p
        cf.CFDictionaryCreateMutable.argtypes = [
            ctypes.c_void_p, ctypes.c_long,
            ctypes.POINTER(_KeyCB), ctypes.POINTER(_ValCB)]
        cf.CFDictionarySetValue.restype = None
        cf.CFDictionarySetValue.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFDataCreate.restype = ctypes.c_void_p
        cf.CFDataCreate.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        cf.CFNumberCreate.restype = ctypes.c_void_p
        cf.CFNumberCreate.argtypes = [
            ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p]
        cf.CFRelease.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]

        # RunLoop
        cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
        cf.CFRunLoopGetCurrent.argtypes = []
        cf.CFRunLoopRun.restype = None
        cf.CFRunLoopRun.argtypes = []
        cf.CFRunLoopStop.restype = None
        cf.CFRunLoopStop.argtypes = [ctypes.c_void_p]

        self._kCFRunLoopDefaultMode = ctypes.c_void_p.in_dll(
            cf, "kCFRunLoopDefaultMode")

        # IOKit HID
        iokit.IOHIDUserDeviceCreate.restype = ctypes.c_void_p
        iokit.IOHIDUserDeviceCreate.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p]
        iokit.IOHIDUserDeviceHandleReport.restype = ctypes.c_int32
        iokit.IOHIDUserDeviceHandleReport.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8), ctypes.c_long]
        iokit.IOHIDUserDeviceScheduleWithRunLoop.restype = None
        iokit.IOHIDUserDeviceScheduleWithRunLoop.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]

    # -- CF helpers -----------------------------------------------------------

    def _cfstr(self, s: str):
        return self._cf.CFStringCreateWithCString(
            None, s.encode("utf-8"), 0x08000100)  # kCFStringEncodingUTF8

    def _cfnum(self, n: int):
        v = ctypes.c_int32(n)
        return self._cf.CFNumberCreate(None, 3, ctypes.byref(v))  # SInt32

    def _cfdata(self, b: bytes):
        buf = (ctypes.c_uint8 * len(b))(*b)
        return self._cf.CFDataCreate(None, buf, len(b))

    # -- Lifecycle ------------------------------------------------------------

    def open(self):
        cf = self._cf
        iokit = self._iokit

        props = cf.CFDictionaryCreateMutable(
            None, 0,
            ctypes.byref(self._key_cbs),
            ctypes.byref(self._val_cbs))

        cf.CFDictionarySetValue(props, self._cfstr("VendorID"),
                                self._cfnum(0x0E6F))
        cf.CFDictionarySetValue(props, self._cfstr("ProductID"),
                                self._cfnum(0xCAFE))
        cf.CFDictionarySetValue(props, self._cfstr("Product"),
                                self._cfstr("Taurino Virtual Gamepad"))
        cf.CFDictionarySetValue(props, self._cfstr("Manufacturer"),
                                self._cfstr("Taurino"))
        cf.CFDictionarySetValue(props, self._cfstr("Transport"),
                                self._cfstr("Virtual"))
        cf.CFDictionarySetValue(props, self._cfstr("ReportDescriptor"),
                                self._cfdata(GAMEPAD_HID_DESCRIPTOR))

        self._device = iokit.IOHIDUserDeviceCreate(None, props)
        cf.CFRelease(props)

        if not self._device:
            raise IOKitHIDError(
                "IOHIDUserDeviceCreate returned NULL.\n"
                "On macOS 13+ this requires the "
                "com.apple.developer.hid.virtual.device entitlement.\n"
                "The bridge will fall back to UDP broadcast.")

        # Schedule on a dedicated RunLoop thread so the OS sees the device.
        self._rl_thread = threading.Thread(
            target=self._runloop, daemon=True, name="taurino-hid-rl")
        self._rl_thread.start()
        time.sleep(0.1)

        print("[taurino] Virtual HID gamepad created — "
              "apps should see 'Taurino Virtual Gamepad'")

    def _runloop(self):
        loop = self._cf.CFRunLoopGetCurrent()
        self._rl_ref = loop
        self._iokit.IOHIDUserDeviceScheduleWithRunLoop(
            self._device, loop, self._kCFRunLoopDefaultMode)
        self._cf.CFRunLoopRun()

    def send_report(self, state: ControllerState) -> bool:
        if not self._device:
            return False
        report = state.pack_hid_report()
        buf = (ctypes.c_uint8 * len(report))(*report)
        ret = self._iokit.IOHIDUserDeviceHandleReport(
            self._device, buf, len(report))
        return ret == 0  # kIOReturnSuccess

    def close(self):
        if self._rl_ref:
            self._cf.CFRunLoopStop(self._rl_ref)
            self._rl_ref = None
        if self._rl_thread:
            self._rl_thread.join(timeout=1)
            self._rl_thread = None
        if self._device:
            self._cf.CFRelease(self._device)
            self._device = None


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
            except IOKitHIDError as e:
                print(f"[taurino] HID bridge unavailable: {e}")
                if self._use_udp:
                    print("[taurino] Continuing in UDP-only mode. "
                          "Use a signed/entitled build for system-wide HID.")
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
