"""Controller state representation."""

import struct
from dataclasses import dataclass
from enum import IntFlag


class Button(IntFlag):
    """GIP input report button bitmask (bytes 4-5 of a 0x20 report)."""
    DPAD_UP      = 0x0001
    DPAD_DOWN    = 0x0002
    DPAD_LEFT    = 0x0004
    DPAD_RIGHT   = 0x0008
    MENU         = 0x0010
    VIEW         = 0x0020
    LEFT_STICK   = 0x0040
    RIGHT_STICK  = 0x0080
    LEFT_BUMPER  = 0x0100
    RIGHT_BUMPER = 0x0200
    SYNC         = 0x0400
    A            = 0x1000
    B            = 0x2000
    X            = 0x4000
    Y            = 0x8000


@dataclass(slots=True)
class ControllerState:
    """Parsed controller state snapshot."""

    # Buttons
    a: bool = False
    b: bool = False
    x: bool = False
    y: bool = False
    dpad_up: bool = False
    dpad_down: bool = False
    dpad_left: bool = False
    dpad_right: bool = False
    menu: bool = False
    view: bool = False
    left_bumper: bool = False
    right_bumper: bool = False
    left_stick_press: bool = False
    right_stick_press: bool = False
    sync: bool = False
    guide: bool = False

    # Triggers (0-1023)
    left_trigger: int = 0
    right_trigger: int = 0

    # Sticks (-32768 to 32767)
    left_stick_x: int = 0
    left_stick_y: int = 0
    right_stick_x: int = 0
    right_stick_y: int = 0

    @property
    def buttons(self) -> list[str]:
        return [n for n in (
            "a", "b", "x", "y",
            "dpad_up", "dpad_down", "dpad_left", "dpad_right",
            "menu", "view", "left_bumper", "right_bumper",
            "left_stick_press", "right_stick_press", "sync", "guide",
        ) if getattr(self, n)]

    def as_tuple(self) -> tuple:
        """Value snapshot for fast equality checks (no object identity)."""
        return (
            self.a, self.b, self.x, self.y,
            self.dpad_up, self.dpad_down, self.dpad_left, self.dpad_right,
            self.menu, self.view, self.left_bumper, self.right_bumper,
            self.left_stick_press, self.right_stick_press,
            self.sync, self.guide,
            self.left_trigger, self.right_trigger,
            self.left_stick_x, self.left_stick_y,
            self.right_stick_x, self.right_stick_y,
        )

    def pack_hid_report(self) -> bytes:
        """Pack into a 14-byte HID gamepad report matching GAMEPAD_HID_DESCRIPTOR."""
        buttons = 0
        if self.a:                 buttons |= 0x0001
        if self.b:                 buttons |= 0x0002
        if self.x:                 buttons |= 0x0004
        if self.y:                 buttons |= 0x0008
        if self.left_bumper:       buttons |= 0x0010
        if self.right_bumper:      buttons |= 0x0020
        if self.view:              buttons |= 0x0040
        if self.menu:              buttons |= 0x0080
        if self.left_stick_press:  buttons |= 0x0100
        if self.right_stick_press: buttons |= 0x0200
        if self.dpad_up:           buttons |= 0x0400
        if self.dpad_down:         buttons |= 0x0800
        if self.dpad_left:         buttons |= 0x1000
        if self.dpad_right:        buttons |= 0x2000
        if self.sync:              buttons |= 0x4000
        if self.guide:             buttons |= 0x8000
        return struct.pack(
            "<HHHhhhh",
            buttons,
            min(1023, max(0, self.left_trigger)),
            min(1023, max(0, self.right_trigger)),
            max(-32768, min(32767, self.left_stick_x)),
            max(-32768, min(32767, self.left_stick_y)),
            max(-32768, min(32767, self.right_stick_x)),
            max(-32768, min(32767, self.right_stick_y)),
        )

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
