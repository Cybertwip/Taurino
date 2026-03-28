"""
PDP Xbox Controller USB Driver for macOS

This controller (VID 0x0E6F, PID 0x02F1) uses the GIP (Game Input Protocol)
used by Xbox One-class controllers -- NOT the older Xbox 360 Xinput protocol.
GIP requires an initialization handshake before the controller will send
actual input reports.

Protocol reference: Linux xpad / xone kernel drivers.
"""

import queue
import struct
import threading
import time
from dataclasses import dataclass, field, replace
from enum import IntFlag

import usb.core
import usb.util

# -- Vendor / Product IDs ----------------------------------------------------

PDP_VENDOR_ID = 0x0E6F

PDP_PRODUCT_IDS = {
    0x0139: "PDP Afterglow Prismatic (Xbox One)",
    0x0146: "PDP Xbox One Controller",
    0x0213: "PDP Xbox One Controller",
    0x02F1: "PDP Wired Controller for Xbox One",
    0x02F2: "PDP Wired Controller for Xbox One (Alt)",
    0x02A1: "PDP Afterglow Prismatic (v2)",
    0x0346: "PDP RC Xbox One",
    0x0446: "PDP Xbox One (v2)",
}

EXTRA_CONTROLLERS = {
    (0x045E, 0x02D1): "Microsoft Xbox One Controller",
    (0x045E, 0x02DD): "Microsoft Xbox One Controller (FW 2015)",
    (0x045E, 0x02E3): "Microsoft Xbox One Elite Controller",
    (0x045E, 0x0B00): "Microsoft Xbox One Elite 2 Controller",
    (0x045E, 0x0B12): "Microsoft Xbox Series X|S Controller",
    (0x24C6, 0x541A): "PowerA Xbox One Controller",
    (0x24C6, 0x542A): "PowerA Xbox One Spectra",
}

# -- GIP (Game Input Protocol) ------------------------------------------------

GIP_INTERFACE = 0
GIP_READ_BUFFER = 64

# GIP command types (byte 0 of each packet)
GIP_CMD_ACK        = 0x01
GIP_CMD_ANNOUNCE   = 0x02
GIP_CMD_STATUS     = 0x03
GIP_CMD_IDENTIFY   = 0x04
GIP_CMD_POWER      = 0x05
GIP_CMD_AUTHENTICATE = 0x06
GIP_CMD_GUIDE      = 0x07
GIP_CMD_AUDIO_CFG  = 0x08
GIP_CMD_RUMBLE     = 0x09
GIP_CMD_LED        = 0x0A
GIP_CMD_INPUT      = 0x20

# GIP initialization packets -- order matters.
# These are derived from the Linux xpad/xone kernel drivers.
GIP_INIT_POWER     = bytes([0x05, 0x20, 0x00, 0x01, 0x00])
GIP_PDP_INIT1      = bytes([0x0A, 0x20, 0x00, 0x03, 0x00, 0x01, 0x14])
GIP_PDP_INIT2      = bytes([0x06, 0x20, 0x00, 0x02, 0x01, 0x00])
GIP_PDP_INIT3      = bytes([0x06, 0x20, 0x00, 0x02, 0x02, 0x00])
# Alternate broader init used by newer firmware
GIP_INIT_LONG      = bytes([0x05, 0x20, 0x00, 0x0F, 0x06, 0x00, 0x00, 0x00,
                             0x00, 0x00, 0x00, 0x55, 0x53])


class Button(IntFlag):
    """GIP input report button bitmask (bytes 4-5 of a 0x20 report)."""
    SYNC         = 0x0001  # byte4 bit0
    # bit1 unused
    MENU         = 0x0004  # byte4 bit2 (Start)
    VIEW         = 0x0008  # byte4 bit3 (Back/Select)
    A            = 0x0010  # byte4 bit4
    B            = 0x0020  # byte4 bit5
    X            = 0x0040  # byte4 bit6
    Y            = 0x0080  # byte4 bit7
    DPAD_UP      = 0x0100  # byte5 bit0
    DPAD_DOWN    = 0x0200  # byte5 bit1
    DPAD_LEFT    = 0x0400  # byte5 bit2
    DPAD_RIGHT   = 0x0800  # byte5 bit3
    LEFT_BUMPER  = 0x1000  # byte5 bit4
    RIGHT_BUMPER = 0x2000  # byte5 bit5
    LEFT_STICK   = 0x4000  # byte5 bit6
    RIGHT_STICK  = 0x8000  # byte5 bit7


@dataclass
class ControllerState:
    """Parsed state of the controller at a point in time."""

    # Buttons (digital)
    dpad_up: bool = False
    dpad_down: bool = False
    dpad_left: bool = False
    dpad_right: bool = False
    menu: bool = False       # Start
    view: bool = False       # Back / Select
    left_stick_press: bool = False
    right_stick_press: bool = False
    left_bumper: bool = False
    right_bumper: bool = False
    sync: bool = False
    a: bool = False
    b: bool = False
    x: bool = False
    y: bool = False

    # Triggers (analog 0-1023)
    left_trigger: int = 0
    right_trigger: int = 0

    # Sticks (signed -32768 to 32767)
    left_stick_x: int = 0
    left_stick_y: int = 0
    right_stick_x: int = 0
    right_stick_y: int = 0

    # Raw data
    raw: bytes = field(default_factory=bytes, repr=False)

    @property
    def buttons(self) -> list[str]:
        """Return list of currently pressed button names."""
        pressed = []
        for name in [
            "dpad_up", "dpad_down", "dpad_left", "dpad_right",
            "menu", "view", "left_stick_press", "right_stick_press",
            "left_bumper", "right_bumper", "sync", "a", "b", "x", "y",
        ]:
            if getattr(self, name):
                pressed.append(name)
        return pressed

    def __str__(self) -> str:
        parts = []
        if self.buttons:
            parts.append(f"Buttons: {', '.join(self.buttons)}")
        if self.left_trigger:
            parts.append(f"LT: {self.left_trigger}")
        if self.right_trigger:
            parts.append(f"RT: {self.right_trigger}")
        if self.left_stick_x or self.left_stick_y:
            parts.append(f"LS: ({self.left_stick_x}, {self.left_stick_y})")
        if self.right_stick_x or self.right_stick_y:
            parts.append(f"RS: ({self.right_stick_x}, {self.right_stick_y})")
        return " | ".join(parts) if parts else "(idle)"


# -- Driver -------------------------------------------------------------------

class PDP360Controller:
    """
    USB driver for PDP Xbox One (GIP) controllers on macOS.

    Usage:
        controller = PDP360Controller.find()
        with controller:
            while True:
                state = controller.read()
                if state:
                    print(state)
    """

    def __init__(self, device: usb.core.Device):
        self._dev = device
        self._claimed = False
        self._dead_zone = 4000
        self._ep_in = None
        self._ep_out = None
        self._seq = 0          # outgoing GIP sequence counter

    # -- Discovery ------------------------------------------------------------

    @classmethod
    def list_devices(cls) -> list[dict]:
        found = []
        for dev in usb.core.find(find_all=True, idVendor=PDP_VENDOR_ID):
            name = PDP_PRODUCT_IDS.get(dev.idProduct,
                                       f"PDP Unknown (0x{dev.idProduct:04X})")
            found.append({
                "device": dev, "vendor_id": dev.idVendor,
                "product_id": dev.idProduct, "name": name,
                "bus": dev.bus, "address": dev.address,
            })
        for (vid, pid), name in EXTRA_CONTROLLERS.items():
            for dev in usb.core.find(find_all=True, idVendor=vid, idProduct=pid):
                found.append({
                    "device": dev, "vendor_id": dev.idVendor,
                    "product_id": dev.idProduct, "name": name,
                    "bus": dev.bus, "address": dev.address,
                })
        return found

    @classmethod
    def find(cls, vendor_id: int | None = None,
             product_id: int | None = None) -> "PDP360Controller":
        if vendor_id and product_id:
            dev = usb.core.find(idVendor=vendor_id, idProduct=product_id)
            if dev is None:
                raise DeviceNotFoundError(
                    f"No device with VID=0x{vendor_id:04X} PID=0x{product_id:04X}")
            return cls(dev)
        devices = cls.list_devices()
        if not devices:
            raise DeviceNotFoundError(
                "No PDP / Xbox controller found. Is it plugged in?")
        info = devices[0]
        print(f"[pdp360] Found: {info['name']} "
              f"(VID=0x{info['vendor_id']:04X} PID=0x{info['product_id']:04X}) "
              f"on bus {info['bus']} address {info['address']}")
        return cls(info["device"])

    # -- Lifecycle ------------------------------------------------------------

    def open(self) -> None:
        dev = self._dev
        try:
            if dev.is_kernel_driver_active(GIP_INTERFACE):
                dev.detach_kernel_driver(GIP_INTERFACE)
                print("[pdp360] Detached kernel driver")
        except (usb.core.USBError, NotImplementedError):
            pass

        dev.set_configuration()
        usb.util.claim_interface(dev, GIP_INTERFACE)
        self._claimed = True

        cfg = dev.get_active_configuration()
        intf = cfg[(GIP_INTERFACE, 0)]
        self._ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e:
                usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
        self._ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e:
                usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)

        if self._ep_in:
            print(f"[pdp360] EP IN=0x{self._ep_in.bEndpointAddress:02X}  "
                  f"maxpkt={self._ep_in.wMaxPacketSize}")
        else:
            print("[pdp360] WARNING: no IN endpoint found!")
        if self._ep_out:
            print(f"[pdp360] EP OUT=0x{self._ep_out.bEndpointAddress:02X}")

        # Send GIP initialization sequence
        self._gip_init()

    def _gip_send(self, data: bytes) -> None:
        if not self._ep_out:
            return
        try:
            self._dev.write(self._ep_out.bEndpointAddress, data, timeout=200)
        except usb.core.USBError as e:
            print(f"[pdp360] Warning: send failed ({e})")

    def _gip_drain(self, timeout_ms: int = 100) -> None:
        """Read and discard any pending packets."""
        if not self._ep_in:
            return
        buf = max(GIP_READ_BUFFER, self._ep_in.wMaxPacketSize)
        while True:
            try:
                self._dev.read(self._ep_in.bEndpointAddress, buf,
                               timeout=timeout_ms)
            except (usb.core.USBTimeoutError, usb.core.USBError):
                break

    def _gip_init(self) -> None:
        """Send the GIP initialization handshake so the controller starts
        reporting inputs.  Tries PDP-specific packets first, then the
        generic Xbox One power-on packet."""
        print("[pdp360] Sending GIP init sequence...")

        # Drain any stale data from the device
        self._gip_drain(50)

        # 1) Power on (works for most Xbox One controllers)
        self._gip_send(GIP_INIT_POWER)
        time.sleep(0.05)

        # 2) Try the longer / newer init
        self._gip_send(GIP_INIT_LONG)
        time.sleep(0.05)

        # 3) PDP-specific init packets
        self._gip_send(GIP_PDP_INIT1)
        time.sleep(0.05)
        self._gip_send(GIP_PDP_INIT2)
        time.sleep(0.05)
        self._gip_send(GIP_PDP_INIT3)
        time.sleep(0.05)

        # Drain any ack/status replies from the init
        self._gip_drain(200)

        print("[pdp360] Init complete — controller should now send inputs")

    def close(self) -> None:
        if self._claimed:
            try:
                usb.util.release_interface(self._dev, GIP_INTERFACE)
            except usb.core.USBError:
                pass
            self._claimed = False
            print("[pdp360] Interface released")

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    # -- Configuration --------------------------------------------------------

    @property
    def dead_zone(self) -> int:
        return self._dead_zone

    @dead_zone.setter
    def dead_zone(self, value: int) -> None:
        self._dead_zone = max(0, value)

    # -- Reading input --------------------------------------------------------

    def read_raw(self, timeout_ms: int = 100) -> bytes | None:
        if not self._ep_in:
            raise RuntimeError("No IN endpoint – is the controller open?")
        try:
            buf = max(GIP_READ_BUFFER, self._ep_in.wMaxPacketSize)
            data = self._dev.read(self._ep_in.bEndpointAddress, buf,
                                  timeout=timeout_ms)
            return bytes(data)
        except usb.core.USBTimeoutError:
            return None
        except usb.core.USBError as e:
            if e.errno == 110:
                return None
            raise

    def read(self, timeout_ms: int = 100) -> ControllerState | None:
        """Read and parse one GIP input report (command 0x20).

        Non-input packets (acks, status, guide button) are silently skipped.
        Returns None on timeout.
        """
        if timeout_ms <= 0:
            return self._parse_packet(self.read_raw(0))

        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while True:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            data = self.read_raw(remaining_ms)
            if data is None:
                return None
            state = self._parse_packet(data)
            if state is not None:
                return state
            if time.monotonic() >= deadline:
                return None

    def poll(self, current: ControllerState, timeout_ms: int = 0,
             max_packets: int = 64) -> ControllerState:
        """Drain queued USB packets and return the freshest merged state.

        This keeps the GUI responsive under stick motion by dropping stale
        intermediate reports instead of rendering them frame by frame.
        """
        state = current
        for packet_index in range(max_packets):
            packet_timeout = timeout_ms if packet_index == 0 else 0
            raw = self.read_raw(packet_timeout)
            if raw is None:
                break
            incoming = self._parse_packet(raw)
            if incoming is not None:
                state = _merge_state(state, incoming)
        return state

    def _parse_packet(self, data: bytes | None) -> ControllerState | None:
        if data is None or len(data) < 4:
            return None

        cmd = data[0]

        if cmd == GIP_CMD_INPUT and len(data) >= 18:
            return self._parse_input(data)

        if cmd == GIP_CMD_GUIDE and len(data) >= 5:
            guide_pressed = bool(data[4] & 0x01)
            return ControllerState(sync=guide_pressed, raw=data)

        return None

    def _parse_input(self, data: bytes) -> ControllerState:
        """Parse a GIP 0x20 input report.

        Layout (after the 4-byte GIP header):
          byte 4:   buttons low  (sync, ?, menu, view, A, B, X, Y)
          byte 5:   buttons high (dU, dD, dL, dR, LB, RB, LS, RS)
          bytes 6-7:   left trigger  (uint16 LE, 0–1023)
          bytes 8-9:   right trigger (uint16 LE, 0–1023)
          bytes 10-11: left stick X  (int16 LE)
          bytes 12-13: left stick Y  (int16 LE)
          bytes 14-15: right stick X (int16 LE)
          bytes 16-17: right stick Y (int16 LE)
        """
        btns = struct.unpack_from("<H", data, 4)[0]
        lt, rt = struct.unpack_from("<HH", data, 6)
        lx, ly, rx, ry = struct.unpack_from("<hhhh", data, 10)

        dz = self._dead_zone
        if abs(lx) < dz:
            lx = 0
        if abs(ly) < dz:
            ly = 0
        if abs(rx) < dz:
            rx = 0
        if abs(ry) < dz:
            ry = 0

        return ControllerState(
            dpad_up=bool(btns & Button.DPAD_UP),
            dpad_down=bool(btns & Button.DPAD_DOWN),
            dpad_left=bool(btns & Button.DPAD_LEFT),
            dpad_right=bool(btns & Button.DPAD_RIGHT),
            menu=bool(btns & Button.MENU),
            view=bool(btns & Button.VIEW),
            left_stick_press=bool(btns & Button.LEFT_STICK),
            right_stick_press=bool(btns & Button.RIGHT_STICK),
            left_bumper=bool(btns & Button.LEFT_BUMPER),
            right_bumper=bool(btns & Button.RIGHT_BUMPER),
            sync=bool(btns & Button.SYNC),
            a=bool(btns & Button.A),
            b=bool(btns & Button.B),
            x=bool(btns & Button.X),
            y=bool(btns & Button.Y),
            left_trigger=lt,
            right_trigger=rt,
            left_stick_x=lx,
            left_stick_y=ly,
            right_stick_x=rx,
            right_stick_y=ry,
            raw=data,
        )

    # -- Writing output -------------------------------------------------------

    def set_rumble(self, left_motor: int = 0, right_motor: int = 0,
                   left_trigger_rumble: int = 0,
                   right_trigger_rumble: int = 0) -> None:
        """Set vibration motors (0-255 each).

        GIP rumble command 0x09:
          09 00 00 09 00 0F LL RR LT RT FF 00 00
        """
        lm = max(0, min(255, left_motor))
        rm = max(0, min(255, right_motor))
        ltr = max(0, min(255, left_trigger_rumble))
        rtr = max(0, min(255, right_trigger_rumble))
        msg = bytes([0x09, 0x00, 0x00, 0x09, 0x00, 0x0F,
                     ltr, rtr, lm, rm, 0xFF, 0x00, 0x00])
        self._gip_send(msg)

    def set_led(self, brightness: int = 20) -> None:
        """Set the guide button LED brightness (0-50 typical)."""
        b = max(0, min(50, brightness))
        # GIP LED command: 0A 20 00 03 00 01 14 (or with brightness byte)
        msg = bytes([0x0A, 0x20, 0x00, 0x03, 0x00, 0x01, b])
        self._gip_send(msg)


class DeviceNotFoundError(Exception):
    """Raised when no matching controller is found on USB."""


# -- CLI demo -----------------------------------------------------------------

def scan() -> None:
    print("Scanning USB for Xbox controllers...\n")
    devices = PDP360Controller.list_devices()
    if not devices:
        print("  (none found)")
        return
    for i, info in enumerate(devices):
        print(f"  [{i}] {info['name']}")
        print(f"      VID=0x{info['vendor_id']:04X}  PID=0x{info['product_id']:04X}  "
              f"bus={info['bus']}  addr={info['address']}")
    print()


def dump_raw() -> None:
    """Dump raw USB reports with GIP init — press buttons to verify."""
    controller = PDP360Controller.find()
    with controller:
        print("[pdp360] Dumping raw GIP packets — press Ctrl-C to stop")
        print("[pdp360] Press buttons / move sticks to see data change\n")
        try:
            count = 0
            while True:
                data = controller.read_raw(timeout_ms=50)
                if data is not None:
                    count += 1
                    cmd = data[0] if data else 0
                    hex_str = " ".join(f"{b:02X}" for b in data)
                    label = {
                        0x01: "ACK   ",
                        0x02: "ANNC  ",
                        0x03: "STATUS",
                        0x04: "IDENT ",
                        0x05: "POWER ",
                        0x06: "AUTH  ",
                        0x07: "GUIDE ",
                        0x20: "INPUT ",
                    }.get(cmd, f"0x{cmd:02X}  ")
                    print(f"[{count:4d}] {label} len={len(data):2d}  {hex_str}")
        except KeyboardInterrupt:
            print(f"\n[pdp360] Stopped after {count} packets.")


def monitor() -> None:
    controller = PDP360Controller.find()
    prev = ""
    with controller:
        print("[pdp360] Reading inputs — press Ctrl-C to stop\n")
        try:
            while True:
                state = controller.read(timeout_ms=16)
                if state is not None:
                    text = str(state)
                    if text != prev:
                        print(f"\r{text:<80}", end="", flush=True)
                        prev = text
        except KeyboardInterrupt:
            print("\n\n[pdp360] Stopped.")
            controller.set_rumble(0, 0)


def _merge_state(current: ControllerState, incoming: ControllerState) -> ControllerState:
    if incoming.raw and incoming.raw[0] == GIP_CMD_GUIDE:
        return replace(current, sync=incoming.sync, raw=incoming.raw)
    return incoming


def gui(vendor_id: int | None = None, product_id: int | None = None) -> None:
    try:
        import pygame
    except ImportError as exc:
        raise SystemExit(
            "pygame is required for gui mode. Install it with: pip install pygame"
        ) from exc

    pygame.init()
    pygame.display.set_caption("PDP Xbox Controller Tester")

    desktop_sizes = getattr(pygame.display, "get_desktop_sizes", lambda: [])()
    if desktop_sizes:
        dw, dh = desktop_sizes[0]
    else:
        di = pygame.display.Info()
        dw, dh = max(1100, di.current_w), max(760, di.current_h)

    flags = pygame.RESIZABLE | getattr(pygame, "WINDOWMAXIMIZED", 0)
    screen = pygame.display.set_mode((dw, dh), flags)
    clock = pygame.time.Clock()

    # Design-space base size — all coordinates below are in this space
    BASE_W, BASE_H = 1100, 760

    colors = {
        "bg": (14, 18, 25),
        "panel": (28, 34, 46),
        "panel_alt": (36, 44, 60),
        "text": (233, 237, 243),
        "muted": (145, 156, 173),
        "active": (96, 216, 160),
        "accent": (87, 163, 255),
        "danger": (255, 113, 113),
        "trigger": (255, 194, 92),
    }

    # ---- Scaled layout state (recalculated on resize) ----
    _s = 1.0        # uniform scale factor
    _ox = 0.0       # x offset for centering
    _oy = 0.0       # y offset for centering
    _fonts: dict = {}

    def _update_layout() -> None:
        nonlocal _s, _ox, _oy, _fonts
        w, h = screen.get_size()
        _s = min(w / BASE_W, h / BASE_H)
        _ox = (w - BASE_W * _s) / 2
        _oy = (h - BASE_H * _s) / 2
        _fonts = {
            "title": pygame.font.SysFont("Menlo", max(8, int(28 * _s)), bold=True),
            "label": pygame.font.SysFont("Menlo", max(8, int(20 * _s))),
            "small": pygame.font.SysFont("Menlo", max(8, int(16 * _s))),
        }

    _update_layout()
    _prev_win_size = screen.get_size()

    # Helpers — design-space → screen-space
    def sc(x: float, y: float) -> tuple[int, int]:
        return (int(_ox + x * _s), int(_oy + y * _s))

    def sr(x: float, y: float, w: float, h: float) -> pygame.Rect:
        return pygame.Rect(int(_ox + x * _s), int(_oy + y * _s),
                           max(1, int(w * _s)), max(1, int(h * _s)))

    def si(v: float) -> int:
        return max(1, int(v * _s))

    # ---- Drawing helpers (draw directly to screen) ----
    def draw_text(text: str, font_key: str, color, pos: tuple[float, float]) -> None:
        surf = _fonts[font_key].render(text, True, color)
        screen.blit(surf, sc(*pos))

    def draw_button(label: str, rect_args: tuple, pressed: bool,
                    active_color=None) -> None:
        color = active_color or colors["active"]
        fill = color if pressed else colors["panel_alt"]
        border = color if pressed else colors["muted"]
        r = sr(*rect_args)
        br = max(1, si(14))
        pygame.draw.rect(screen, fill, r, border_radius=br)
        pygame.draw.rect(screen, border, r, width=max(1, si(2)), border_radius=br)
        text_color = colors["bg"] if pressed else colors["text"]
        label_surf = _fonts["label"].render(label, True, text_color)
        label_rect = label_surf.get_rect(center=r.center)
        screen.blit(label_surf, label_rect)

    def draw_trigger(label: str, value: int, rx: float, ry: float,
                     rw: float, rh: float) -> None:
        r = sr(rx, ry, rw, rh)
        br = max(1, si(12))
        pad = max(1, si(4))
        pygame.draw.rect(screen, colors["panel_alt"], r, border_radius=br)
        pygame.draw.rect(screen, colors["muted"], r, width=max(1, si(2)),
                         border_radius=br)
        height = int((max(0, min(1023, value)) / 1023) * (r.height - 2 * pad))
        if height:
            fill_rect = pygame.Rect(r.x + pad, r.bottom - pad - height,
                                    r.width - 2 * pad, height)
            pygame.draw.rect(screen, colors["trigger"], fill_rect,
                             border_radius=max(1, si(8)))
        draw_text(label, "label", colors["text"], (rx, ry - 28))
        draw_text(str(value), "small", colors["muted"], (rx, ry + rh + 8))

    def draw_stick(label: str, cx: float, cy: float, radius: float,
                   x_val: int, y_val: int) -> None:
        scx, scy = sc(cx, cy)
        sr_ = si(radius)
        lw = max(1, si(2))
        pygame.draw.circle(screen, colors["panel_alt"], (scx, scy), sr_)
        pygame.draw.circle(screen, colors["muted"], (scx, scy), sr_, width=lw)
        pygame.draw.line(screen, colors["muted"],
                         (scx - sr_, scy), (scx + sr_, scy), 1)
        pygame.draw.line(screen, colors["muted"],
                         (scx, scy - sr_), (scx, scy + sr_), 1)
        knob_range = sr_ - si(16)
        kx = scx + int((x_val / 32767) * knob_range) if x_val else scx
        ky = scy - int((y_val / 32767) * knob_range) if y_val else scy
        knob_r = si(18)
        pygame.draw.circle(screen, colors["accent"], (kx, ky), knob_r)
        pygame.draw.circle(screen, colors["text"], (kx, ky), knob_r, width=lw)
        draw_text(label, "label", colors["text"],
                  (cx - radius, cy + radius + 16))
        draw_text(f"x={x_val:6d}  y={y_val:6d}", "small", colors["muted"],
                  (cx - radius, cy + radius + 44))

    # ---- Controller worker thread ----
    shared = {
        "state": ControllerState(),
        "status": "Connecting to controller...",
        "error": None,
        "connected": False,
    }
    shared_lock = threading.Lock()
    stop_event = threading.Event()
    command_queue: queue.Queue[tuple[str, tuple[int, ...]]] = queue.Queue()

    def controller_worker(initial_led: int) -> None:
        controller = None
        current_state = ControllerState()
        try:
            with shared_lock:
                shared["status"] = "Searching for controller..."
            controller = PDP360Controller.find(vendor_id=vendor_id,
                                               product_id=product_id)
            with shared_lock:
                shared["status"] = "Initializing controller..."
            controller.open()
            controller.set_led(initial_led)
            with shared_lock:
                shared["connected"] = True
                shared["status"] = "Controller ready"

            while not stop_event.is_set():
                # Process pending commands (rumble / LED)
                while True:
                    try:
                        command, values = command_queue.get_nowait()
                    except queue.Empty:
                        break
                    if command == "rumble":
                        controller.set_rumble(*values)
                    elif command == "led":
                        controller.set_led(values[0])

                # Drain pending USB packets, keep freshest state.
                # timeout_ms=8 blocks briefly so we don't burn CPU when idle.
                current_state = controller.poll(current_state,
                                                timeout_ms=8,
                                                max_packets=32)
                with shared_lock:
                    shared["state"] = current_state
        except Exception as exc:
            with shared_lock:
                shared["connected"] = False
                shared["error"] = f"{type(exc).__name__}: {exc}"
                shared["status"] = "Controller initialization failed"
        finally:
            if controller is not None:
                try:
                    controller.set_rumble(0, 0, 0, 0)
                except usb.core.USBError:
                    pass
                controller.close()
            with shared_lock:
                shared["connected"] = False
                if shared["error"] is None:
                    shared["status"] = "Controller disconnected"

    rumble_on = False
    led_levels = [0, 10, 20, 35, 50]
    led_index = 2
    state = ControllerState()
    prev_state_id: int | None = None  # id() of last rendered state

    worker = threading.Thread(
        target=controller_worker,
        args=(led_levels[led_index],),
        name="pdp360-gui-worker",
        daemon=True,
    )
    worker.start()

    need_redraw = True
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type in (pygame.VIDEORESIZE,
                                getattr(pygame, "WINDOWRESIZED", 0x00)):
                cur = screen.get_size()
                if cur != _prev_win_size:
                    _update_layout()
                    _prev_win_size = cur
                    need_redraw = True
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_r:
                    rumble_on = not rumble_on
                    if rumble_on:
                        command_queue.put(("rumble", (180, 220, 0, 0)))
                    else:
                        command_queue.put(("rumble", (0, 0, 0, 0)))
                elif event.key == pygame.K_t:
                    rumble_on = not rumble_on
                    if rumble_on:
                        command_queue.put(("rumble", (0, 0, 255, 255)))
                    else:
                        command_queue.put(("rumble", (0, 0, 0, 0)))
                elif event.key == pygame.K_l:
                    led_index = (led_index + 1) % len(led_levels)
                    command_queue.put(("led", (led_levels[led_index],)))

        # Grab latest state from worker
        with shared_lock:
            state = shared["state"]
            status_text = shared["status"]
            error_text = shared["error"]
            connected = shared["connected"]

        # Only redraw when state object changed or window resized
        sid = id(state)
        if sid != prev_state_id:
            prev_state_id = sid
            need_redraw = True

        if not need_redraw:
            clock.tick(60)
            continue
        need_redraw = False

        # ---- Render frame directly to screen ----
        screen.fill(colors["bg"])

        draw_text("PDP Xbox Controller Tester", "title", colors["text"],
                  (40, 28))
        draw_text("Esc: quit   R: motor rumble   T: trigger rumble   "
                  "L: cycle LED", "small", colors["muted"], (40, 68))

        pygame.draw.rect(screen, colors["panel"], sr(32, 110, 1036, 610),
                         border_radius=max(1, si(24)))

        draw_stick("Left Stick", 220, 280, 110,
                   state.left_stick_x, state.left_stick_y)
        draw_stick("Right Stick", 860, 280, 110,
                   state.right_stick_x, state.right_stick_y)

        draw_trigger("LT", state.left_trigger, 90, 470, 72, 180)
        draw_trigger("RT", state.right_trigger, 938, 470, 72, 180)

        draw_button("U", (392, 252, 54, 54), state.dpad_up)
        draw_button("D", (392, 368, 54, 54), state.dpad_down)
        draw_button("L", (334, 310, 54, 54), state.dpad_left)
        draw_button("R", (450, 310, 54, 54), state.dpad_right)

        draw_button("VIEW", (470, 500, 100, 48), state.view)
        draw_button("MENU", (598, 500, 100, 48), state.menu)
        draw_button("SYNC", (536, 210, 96, 44), state.sync, colors["accent"])
        draw_button("LB", (180, 150, 90, 42), state.left_bumper)
        draw_button("RB", (830, 150, 90, 42), state.right_bumper)
        draw_button("LS", (178, 620, 84, 40), state.left_stick_press)
        draw_button("RS", (818, 620, 84, 40), state.right_stick_press)

        draw_button("Y", (744, 268, 58, 58), state.y, (246, 208, 84))
        draw_button("X", (686, 326, 58, 58), state.x, (93, 170, 255))
        draw_button("B", (802, 326, 58, 58), state.b, (255, 132, 96))
        draw_button("A", (744, 384, 58, 58), state.a, (96, 216, 160))

        status_lines = [
            f"Buttons: {', '.join(state.buttons) if state.buttons else '(none)'}",
            f"Controller: {'connected' if connected else 'offline'}",
            f"Status: {status_text}",
            f"Rumble: {'on' if rumble_on else 'off'}",
            f"LED brightness: {led_levels[led_index]}",
        ]
        for idx, line in enumerate(status_lines):
            draw_text(line, "small", colors["muted"],
                      (440, 566 + idx * 22))

        if not connected:
            win_w, win_h = screen.get_size()
            overlay = pygame.Surface((win_w, win_h), pygame.SRCALPHA)
            overlay.fill((8, 12, 16, 138))
            screen.blit(overlay, (0, 0))
            pygame.draw.rect(screen, colors["panel_alt"],
                             sr(280, 286, 540, 148),
                             border_radius=max(1, si(20)))
            pygame.draw.rect(screen, colors["accent"],
                             sr(280, 286, 540, 148),
                             width=max(1, si(2)),
                             border_radius=max(1, si(20)))
            draw_text("Controller status", "label", colors["text"],
                      (316, 318))
            draw_text(status_text, "label", colors["accent"], (316, 356))
            if error_text:
                draw_text(error_text[:54], "small", colors["danger"],
                          (316, 394))

        pygame.display.flip()
        clock.tick(60)

    stop_event.set()
    worker.join(timeout=1.5)
    pygame.quit()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="PDP Xbox Controller Driver (GIP)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("scan", help="List connected controllers")
    sub.add_parser("monitor", help="Read and display live controller input")
    sub.add_parser("dump", help="Dump raw GIP packets for debugging")
    gp = sub.add_parser("gui", help="Open a pygame controller test window")
    gp.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    gp.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    rp = sub.add_parser("read", help="Read N input reports and exit")
    rp.add_argument("-n", type=int, default=100)
    rp.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    rp.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    args = parser.parse_args()

    if args.command == "scan":
        scan()
    elif args.command == "monitor":
        monitor()
    elif args.command == "dump":
        dump_raw()
    elif args.command == "gui":
        gui(vendor_id=args.vid, product_id=args.pid)
    elif args.command == "read":
        controller = PDP360Controller.find(vendor_id=args.vid,
                                           product_id=args.pid)
        with controller:
            count = 0
            while count < args.n:
                state = controller.read(timeout_ms=50)
                if state:
                    count += 1
                    print(f"[{count:4d}] {state}")
        print(f"\nDone — read {count} reports.")
    else:
        parser.print_help()
        print("\nQuick start:")
        print("  python pdp_xbox360.py scan      # list controllers")
        print("  python pdp_xbox360.py dump      # raw packet debug")
        print("  python pdp_xbox360.py monitor   # live input display")
        print("  python pdp_xbox360.py gui       # pygame visual tester")
        print("  python pdp_xbox360.py read -n 50")


if __name__ == "__main__":
    main()
