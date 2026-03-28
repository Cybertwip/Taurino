"""USB driver for PDP Xbox One (GIP) controllers on macOS."""

import struct
import time
from dataclasses import replace

import usb.core
import usb.util

from .protocol import (
    PDP_VENDOR_ID, PDP_PRODUCTS, EXTRA_CONTROLLERS,
    GIP_INTERFACE, GIP_READ_BUFFER,
    GIP_CMD_ANNOUNCE, GIP_CMD_INPUT, GIP_CMD_GUIDE,
    GIP_OPT_ACK,
    GIP_INIT_POWER, GIP_INIT_LONG,
    GIP_PDP_LED, GIP_PDP_AUTH1, GIP_PDP_AUTH2,
    make_ack,
)
from .state import Button, ControllerState


class DeviceNotFoundError(Exception):
    pass


class PDP360Controller:
    """Low-level USB driver for PDP / Xbox One GIP controllers.

    Usage::

        ctrl = PDP360Controller.find()
        with ctrl:
            while True:
                state = ctrl.poll()
                if state:
                    print(state)
    """

    def __init__(self, device: usb.core.Device):
        self._dev = device
        self._claimed = False
        self._dead_zone = 4000
        self._ep_in = None
        self._ep_out = None
        self._seq = 0
        self._last_state = ControllerState()

    # -- Discovery ------------------------------------------------------------

    @classmethod
    def list_devices(cls) -> list[dict]:
        found = []
        for dev in usb.core.find(find_all=True, idVendor=PDP_VENDOR_ID):
            name = PDP_PRODUCTS.get(
                dev.idProduct, f"PDP Unknown (0x{dev.idProduct:04X})")
            found.append({
                "device": dev, "vendor_id": dev.idVendor,
                "product_id": dev.idProduct, "name": name,
                "bus": dev.bus, "address": dev.address,
            })
        for (vid, pid), name in EXTRA_CONTROLLERS.items():
            for dev in usb.core.find(find_all=True, idVendor=vid,
                                     idProduct=pid):
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
                    f"No device VID=0x{vendor_id:04X} PID=0x{product_id:04X}")
            return cls(dev)
        devices = cls.list_devices()
        if not devices:
            raise DeviceNotFoundError("No PDP/Xbox controller found")
        info = devices[0]
        print(f"[taurino] Found: {info['name']} "
              f"(VID=0x{info['vendor_id']:04X} PID=0x{info['product_id']:04X})")
        return cls(info["device"])

    # -- Lifecycle ------------------------------------------------------------

    def open(self) -> None:
        dev = self._dev
        try:
            if dev.is_kernel_driver_active(GIP_INTERFACE):
                dev.detach_kernel_driver(GIP_INTERFACE)
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
                usb.util.endpoint_direction(e.bEndpointAddress)
                == usb.util.ENDPOINT_IN)
        self._ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e:
                usb.util.endpoint_direction(e.bEndpointAddress)
                == usb.util.ENDPOINT_OUT)

        if not self._ep_in:
            print("[taurino] WARNING: no IN endpoint")
        if not self._ep_out:
            print("[taurino] WARNING: no OUT endpoint")

        self._gip_init()

    def close(self) -> None:
        if self._claimed:
            try:
                usb.util.release_interface(self._dev, GIP_INTERFACE)
            except usb.core.USBError:
                pass
            self._claimed = False

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    # -- GIP low-level --------------------------------------------------------

    def _send(self, data: bytes) -> None:
        if not self._ep_out:
            return
        try:
            self._dev.write(self._ep_out.bEndpointAddress, data, timeout=200)
        except usb.core.USBError:
            pass

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFF
        return self._seq

    def _ack_if_needed(self, data: bytes) -> None:
        """Send an ACK if the packet's header flags request one."""
        if len(data) >= 4 and (data[1] & GIP_OPT_ACK):
            ack = make_ack(self._next_seq(), data[0], data[1], data[2], data[3])
            self._send(ack)

    def _read_usb(self, timeout_ms: int = 100) -> bytes | None:
        """Read one raw USB packet (no ACK)."""
        if not self._ep_in:
            return None
        try:
            buf = max(GIP_READ_BUFFER, self._ep_in.wMaxPacketSize)
            data = self._dev.read(self._ep_in.bEndpointAddress, buf,
                                  timeout=timeout_ms)
            return bytes(data)
        except usb.core.USBTimeoutError:
            return None
        except usb.core.USBError as e:
            if e.errno == 110:  # timeout on some platforms
                return None
            raise

    def _gip_init(self) -> None:
        """Full GIP handshake: wait for ANNOUNCE, ACK it, power on,
        read/ACK responses, then send PDP-specific init packets."""
        print("[taurino] GIP init...")

        # 1) Wait for the ANNOUNCE packet and ACK it
        got_announce = False
        for _ in range(50):
            data = self._read_usb(100)
            if data and len(data) >= 4:
                self._ack_if_needed(data)
                if data[0] == GIP_CMD_ANNOUNCE:
                    got_announce = True
                    break
        if not got_announce:
            print("[taurino] Warning: no ANNOUNCE, trying init anyway")

        time.sleep(0.02)

        # 2) Power on
        self._send(GIP_INIT_POWER)
        time.sleep(0.05)

        # Read and ACK any response packets
        for _ in range(30):
            data = self._read_usb(80)
            if data is None:
                break
            self._ack_if_needed(data)

        # 3) Extended init (newer firmware)
        self._send(GIP_INIT_LONG)
        time.sleep(0.05)

        for _ in range(30):
            data = self._read_usb(80)
            if data is None:
                break
            self._ack_if_needed(data)

        # 4) PDP-specific setup
        self._send(GIP_PDP_LED)
        time.sleep(0.03)
        self._send(GIP_PDP_AUTH1)
        time.sleep(0.03)
        self._send(GIP_PDP_AUTH2)
        time.sleep(0.03)

        # 5) Drain remaining setup replies
        for _ in range(30):
            data = self._read_usb(50)
            if data is None:
                break
            self._ack_if_needed(data)

        print("[taurino] Init complete")

    # -- Properties -----------------------------------------------------------

    @property
    def dead_zone(self) -> int:
        return self._dead_zone

    @dead_zone.setter
    def dead_zone(self, value: int) -> None:
        self._dead_zone = max(0, value)

    # -- Reading input --------------------------------------------------------

    def read_raw(self, timeout_ms: int = 100) -> bytes | None:
        """Read one raw USB packet, ACKing if the device requests it."""
        data = self._read_usb(timeout_ms)
        if data:
            self._ack_if_needed(data)
        return data

    def read(self, timeout_ms: int = 100) -> ControllerState | None:
        """Read a single parsed input report.  Skips non-input packets."""
        if timeout_ms <= 0:
            return self._parse(self.read_raw(0))
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            remaining = max(0, int((deadline - time.monotonic()) * 1000))
            data = self.read_raw(remaining)
            if data is None:
                return None
            state = self._parse(data)
            if state is not None:
                return state
            if time.monotonic() >= deadline:
                return None

    def poll(self, timeout_ms: int = 8, max_packets: int = 32) -> ControllerState | None:
        """Drain queued USB packets, return the freshest input state or None."""
        latest = None
        for i in range(max_packets):
            t = timeout_ms if i == 0 else 0
            data = self.read_raw(t)
            if data is None:
                break
            state = self._parse(data)
            if state is not None:
                latest = state
        return latest

    def _parse(self, data: bytes | None) -> ControllerState | None:
        if data is None or len(data) < 4:
            return None
        cmd = data[0]
        if cmd == GIP_CMD_INPUT and len(data) >= 18:
            state = self._parse_input(data)
            # Preserve guide state from the most recent guide packet
            state = replace(state, guide=self._last_state.guide)
            self._last_state = state
            return state
        if cmd == GIP_CMD_GUIDE and len(data) >= 5:
            guide = bool(data[4] & 0x01)
            self._last_state = replace(self._last_state, guide=guide)
            return self._last_state
        return None

    def _parse_input(self, data: bytes) -> ControllerState:
        btns = struct.unpack_from("<H", data, 4)[0]
        # PDP Xbox One-class pads expose an XInput-like payload inside the
        # GIP packet: buttons at 4-5, triggers as bytes at 6-7, sticks at 8-15.
        lt_raw, rt_raw = struct.unpack_from("<BB", data, 6)
        lx, ly, rx, ry = struct.unpack_from("<hhhh", data, 8)

        # Normalize 8-bit trigger values into the 0-1023 range used elsewhere.
        lt = (lt_raw * 1023) // 255
        rt = (rt_raw * 1023) // 255

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
            a=bool(btns & Button.A),
            b=bool(btns & Button.B),
            x=bool(btns & Button.X),
            y=bool(btns & Button.Y),
            dpad_up=bool(btns & Button.DPAD_UP),
            dpad_down=bool(btns & Button.DPAD_DOWN),
            dpad_left=bool(btns & Button.DPAD_LEFT),
            dpad_right=bool(btns & Button.DPAD_RIGHT),
            menu=bool(btns & Button.MENU),
            view=bool(btns & Button.VIEW),
            left_bumper=bool(btns & Button.LEFT_BUMPER),
            right_bumper=bool(btns & Button.RIGHT_BUMPER),
            left_stick_press=bool(btns & Button.LEFT_STICK),
            right_stick_press=bool(btns & Button.RIGHT_STICK),
            sync=bool(btns & Button.SYNC),
            left_trigger=lt,
            right_trigger=rt,
            left_stick_x=lx,
            left_stick_y=ly,
            right_stick_x=rx,
            right_stick_y=ry,
        )

    # -- Writing output -------------------------------------------------------

    def set_rumble(self, left: int = 0, right: int = 0,
                   left_trigger: int = 0, right_trigger: int = 0) -> None:
        msg = bytes([0x09, 0x00, 0x00, 0x09, 0x00, 0x0F,
                     min(255, max(0, left_trigger)),
                     min(255, max(0, right_trigger)),
                     min(255, max(0, left)),
                     min(255, max(0, right)),
                     0xFF, 0x00, 0x00])
        self._send(msg)

    def set_led(self, brightness: int = 20) -> None:
        b = max(0, min(50, brightness))
        self._send(bytes([0x0A, 0x20, 0x00, 0x03, 0x00, 0x01, b]))
